# Telegram Wildberries Parser Bot

Телеграм-бот для поиска товаров на [wildberries.ru](https://www.wildberries.ru/) по названию, минимальной и максимальной цене.

## Возможности

- Поиск доступен без подписки — можно сразу тестировать функциональность.
- Подбор товара по названию с настройкой минимальной и максимальной цены через кнопку «🔍 Поиск товара».
- Детализированная карточка товара: ссылка, фото, цены (в том числе с кошельком WB), расчёт профита, рейтинг и отзывы, остатки у продавца.
- Профиль продавца в выдаче: название магазина, рейтинг, количество заказов и дата регистрации.
- Настраиваемый пользователем список запрещённых слов, исключающий ненужные варианты из выдачи (управление через меню «🚫 Запрещённые слова»).
- Инфраструктура для YooKassa и подписок сохранена в коде, но не активируется при запуске бота.

## Требования

- Python 3.11+
- Токен телеграм-бота (`TELEGRAM_TOKEN`)

## Установка

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Создайте файл `.env` и укажите в нём токен бота (для тестирования можно использовать `8269524086:AAGFIEvVXKi55hQODcNxwwrzscy5zYLSsB0`) и при необходимости параметры БД:

```env
TELEGRAM_TOKEN=ВАШ_ТОКЕН
DATABASE_URL=sqlite:///data/bot.db
REQUEST_TIMEOUT=10
MAX_RESULTS=10
# Параметры YooKassa и подписок можно оставить пустыми, если они не нужны.
YOOKASSA_SHOP_ID=
YOOKASSA_SECRET_KEY=
YOOKASSA_RETURN_URL=
```

## Запуск

```bash
python -m bot.main
```

## Использование

1. Наберите `/start`, чтобы получить приветствие и главное меню.
2. Для поиска товаров нажмите кнопку «🔍 Поиск товара»: бот спросит название, минимальную и (при необходимости) максимальную цену, а затем пришлёт карточки подходящих позиций.
3. Управляйте фильтром запрещённых слов через меню «🚫 Запрещённые слова»: добавляйте и удаляйте фразы кнопками «➕ Добавить слова» и «➖ Удалить слово».
4. При необходимости вернитесь к платёжной модели, включив команды подписки в `bot/main.py` (см. комментарии в коде).

## Подключение YooKassa

1. Зарегистрируйте магазин в [кабинете YooKassa](https://yookassa.ru/) и получите `shop_id` и секретный ключ API.
2. Укажите публичный URL, на который YooKassa будет возвращать клиента после оплаты, и внесите его в `YOOKASSA_RETURN_URL`.
3. Заполните переменные окружения `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY`, `YOOKASSA_RETURN_URL`. Без них платёжная интеграция будет отключена.
4. Настройте тарифы и стоимость в `DEFAULT_PLANS` файла `bot/services/subscription.py` при необходимости.
5. Проверьте оплату, выбрав платный тариф командой `/subscribe` и следуя инструкциям бота.

## Развёртывание в Yandex Cloud

1. Установите и авторизуйте [CLI Yandex Cloud](https://cloud.yandex.ru/docs/cli/quickstart).
2. Соберите образ контейнера:
   ```bash
   docker build -t cr.yandex/<registry-id>/wildberries-bot:latest .
   ```
3. Создайте реестр и загрузите образ:
   ```bash
   yc container registry create --name wildberries-registry
   docker push cr.yandex/<registry-id>/wildberries-bot:latest
   ```
4. Подготовьте сервисный аккаунт и задайте права `container-registry.images.puller` и `compute.admin` (для ВМ) или `serverless.containers.invoker` (для контейнеров).
5. **Вариант 1: Compute Cloud (ВМ)**
   - Создайте ВМ c Ubuntu.
   - Установите Docker и авторизуйтесь в реестре: `docker login cr.yandex`.
   - Запустите контейнер с переменными окружения и томом для БД:
     ```bash
     docker run -d --restart unless-stopped \
       --name wildberries-bot \
       -e TELEGRAM_TOKEN=... \
       -e DATABASE_URL=sqlite:////data/bot.db \
       -e YOOKASSA_SHOP_ID=... \
       -e YOOKASSA_SECRET_KEY=... \
       -e YOOKASSA_RETURN_URL=https://<домен>/success \
       -v /opt/wb-bot-data:/data \
       cr.yandex/<registry-id>/wildberries-bot:latest
     ```
   - Откройте доступ к интернету для исходящих запросов (для Telegram API и Wildberries).
6. **Вариант 2: Yandex Cloud Serverless Containers**
   - Создайте серверless-контейнер с образом из реестра.
   - Установите переменные окружения и включите минимум 1 vCPU и 512 MiB RAM.
   - Настройте автоматический запуск (минимум 1 реплика) и включите доступ в интернет.
7. Добавьте в `.env` все нужные переменные окружения и используйте секреты Yandex Cloud для хранения ключей.
8. Настройте мониторинг логов через Cloud Logging (`yc logging read ...`) и автоматический рестарт по сбоям.

Подробнее см. в `deploy/yandex_cloud.md`.

## Расширение функционала

- Вы можете добавить собственные тарифы, изменив словарь `DEFAULT_PLANS` в `bot/services/subscription.py`.
- При необходимости реализуйте дополнительные фильтры в `bot/services/wildberries.py`, используя параметры API Wildberries.
