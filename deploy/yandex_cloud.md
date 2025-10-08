# Развёртывание Telegram-бота в Yandex Cloud

Этот гид дополняет основные шаги из README и описывает весь путь от подготовки инфраструктуры до автоматического обновления контейнера.

## 1. Подготовка окружения

1. Установите Docker, `yc` CLI и выполните `yc init`.
2. Создайте отдельный каталог для данных SQLite (например, `/opt/wb-bot-data`).
3. Сохраните `.env` с переменными окружения (можно воспользоваться [Yandex Lockbox](https://cloud.yandex.ru/docs/lockbox/) для секретов).

## 2. Сборка и публикация образа

```bash
yc container registry create --name wildberries-registry
export REGISTRY_ID=$(yc container registry list --format json | jq -r '.[0].id')
docker build -t cr.yandex/$REGISTRY_ID/wildberries-bot:$(git rev-parse --short HEAD) .
docker push cr.yandex/$REGISTRY_ID/wildberries-bot:$(git rev-parse --short HEAD)
```

При необходимости отметьте образ тегом `latest`.

## 3. Развёртывание на Compute Cloud

1. Создайте сервисный аккаунт и назначьте роли:
   ```bash
   yc iam service-account create --name wb-bot-sa
   yc resource-manager folder add-access-binding $(yc config get folder-id) \
     --role container-registry.images.puller \
     --subject serviceAccount:$(yc iam service-account get --name wb-bot-sa --format json | jq -r '.id')
   yc resource-manager folder add-access-binding $(yc config get folder-id) \
     --role compute.admin \
     --subject serviceAccount:$(yc iam service-account get --name wb-bot-sa --format json | jq -r '.id')
   ```
2. Создайте ВМ (пример):
   ```bash
   yc compute instance create \
     --name wb-bot \
     --zone ru-central1-a \
     --cores 2 --memory 4 \
     --create-boot-disk image-folder-id=standard-images,image-family=ubuntu-2204-lts \
     --service-account-name wb-bot-sa \
     --network-interface subnet-name=default-ru-central1-a,nat-ip-version=ipv4
   ```
3. Подключитесь по SSH, установите Docker (`apt install docker.io`), авторизуйтесь в реестре `docker login cr.yandex`.
4. Создайте файл `/etc/systemd/system/wb-bot.service`:
   ```ini
   [Unit]
   Description=Wildberries Telegram Bot
   After=docker.service
   Requires=docker.service

   [Service]
   Restart=always
   ExecStart=/usr/bin/docker run --rm \
     --name wildberries-bot \
     --env-file /opt/wb-bot-data/.env \
     -v /opt/wb-bot-data:/data \
     cr.yandex/$REGISTRY_ID/wildberries-bot:latest
   ExecStop=/usr/bin/docker stop wildberries-bot

   [Install]
   WantedBy=multi-user.target
   ```
5. Выполните `systemctl daemon-reload && systemctl enable --now wb-bot`.

## 4. Развёртывание в Serverless Containers

1. Создайте контейнер:
   ```bash
   yc serverless container create --name wb-bot
   yc serverless container revision deploy \
     --container-name wb-bot \
     --image cr.yandex/$REGISTRY_ID/wildberries-bot:latest \
     --cores 1 --memory 1GB \
     --concurrency 1 \
     --execution-timeout 600s \
     --environment TELEGRAM_TOKEN=...,DATABASE_URL=sqlite:////tmp/bot.db,YOOKASSA_SHOP_ID=...
   ```
2. Включите минимум одну постоянно работающую инстанцию: `yc serverless container allow-unauthenticated-invoke wb-bot` и настройте автопилот `--service-account-id ...`.
3. Добавьте [инициализацию persistent volume](https://cloud.yandex.ru/docs/functions/operations/storage) или используйте управляемую БД (PostgreSQL) вместо SQLite.

## 5. Настройка обновлений

- Используйте GitHub Actions/GitLab CI с `yc container registry configure-docker` и `docker push` для автоматических релизов.
- После публикации образа выполните `yc compute instance update-container` или `yc serverless container revision deploy` для обновления рабочей среды.

## 6. Мониторинг и логирование

- Подключите `yc logging` для чтения логов контейнера.
- Настройте алерты в Cloud Monitoring по ключевым метрикам (доступность, использование CPU/RAM).

## 7. Настройка вебхуков YooKassa (опционально)

Если требуется получать уведомления о статусах платежей автоматически:

1. Создайте HTTP endpoint (например, на Yandex Cloud Functions) и пробросьте его в переменную `YOOKASSA_RETURN_URL` или `webhook`.
2. Обновите `PaymentService` для обработки входящих webhook-уведомлений и подтверждения платежей без участия пользователя.

Следуя этому руководству, вы получите полностью автоматизированный пайплайн развёртывания и сможете поддерживать бота в рабочем состоянии в Yandex Cloud.
