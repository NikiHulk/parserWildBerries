# Telegram Wildberries Parser Bot

Телеграм-бот для поиска товаров на [wildberries.ru](https://www.wildberries.ru/) по названию, минимальной и максимальной цене.

## Возможности

- Поиск доступен без подписки — можно сразу тестировать функциональность.
- Главное меню построено на кнопках: «🔎 Поиск» запускает мастер, который последовательно спрашивает название товара, верхний порог цены и список исключающих слов.
- Детализированная карточка товара: ссылка, фото, цены (в том числе с кошельком WB), расчёт профита, рейтинг и отзывы, остатки у продавца.
- Профиль продавца в выдаче: название магазина, рейтинг, количество заказов и дата регистрации.
- Исключающие слова вводятся прямо в мастере, что позволяет отсеять «б/у», «восстановленный» и другие нежелательные варианты перед показом карточек.
- Инфраструктура для YooKassa и подписок сохранена в коде, но не активируется при запуске бота.

## Требования

- Python 3.11+
- Токен телеграм-бота (`TELEGRAM_TOKEN`)

## Установка

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install playwright
playwright install chromium
```

Создайте файл `.env` и укажите в нём токен бота (для тестирования можно использовать `8269524086:AAGFIEvVXKi55hQODcNxwwrzscy5zYLSsB0`) и при необходимости параметры БД:

```env
TELEGRAM_TOKEN=ВАШ_ТОКЕН
DATABASE_URL=sqlite:///data/bot.db
REQUEST_TIMEOUT=10
MAX_RESULTS=10
TG_RESULTS_PER_PAGE=8
TG_WELCOME_TEXT="Добро пожаловать! Нажмите «🔎 Поиск», чтобы найти товар."
WB_DEFAULT_MIN_PRICE=
WB_DEFAULT_MAX_PRICE=
WB_DEFAULT_LIMIT=8
PLAYWRIGHT_THROTTLE_MS=2000
PLAYWRIGHT_HEADLESS=true
PLAYWRIGHT_TZ=Europe/Moscow
PLAYWRIGHT_STATE_PATH=/app/data/wb_playwright_state.json
PLAYWRIGHT_USER_DATA_DIR=data
PLAYWRIGHT_PROXY=
PROXY_POOL=
HTTPX_PROXY=
WB_HTTP_PROXY=
HTTP_PROXY=
# при необходимости можно задать HTTPS_PROXY/ALL_PROXY
HTTPS_PROXY=
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

1. Наберите `/start`, чтобы получить приветствие и кнопочное меню.
2. Нажмите «🔎 Поиск» — бот спросит название товара, верхний порог цены и список исключающих слов (опционально).
3. После ввода данных бот отдаст карточки по 8 штук на страницу и предложит пролистывать их кнопками «◀️ / ▶️».
4. При необходимости вернитесь к платёжной модели, включив команды подписки в `bot/main.py` (см. комментарии в коде).

### HTML-фолбэк через Playwright и прокси

Чтобы обходить антибот-проверки Wildberries и получать HTML-выдачу даже при 403/498, бот использует Playwright (headless Chromium) и пул прокси. Основные переменные окружения:

- `WB_DEFAULT_MIN_PRICE` / `WB_DEFAULT_MAX_PRICE` / `WB_DEFAULT_LIMIT` — дефолтные параметры поиска для CLI/бота (если пользователь не указал иное).
- `PLAYWRIGHT_THROTTLE_MS` — базовая задержка между действиями браузера (по умолчанию 2000 мс). При агрессивных блокировках увеличьте значение.
- `PLAYWRIGHT_HEADLESS` — режим браузера (`true`/`false`).
- `PLAYWRIGHT_TZ` — временная зона браузера (по умолчанию `Europe/Moscow`).
- `PLAYWRIGHT_STATE_PATH` — файл `storage_state`, где сохраняются cookies/локальные данные (по умолчанию `/app/data/wb_playwright_state.json`).
- `PLAYWRIGHT_USER_DATA_DIR` — директория для вспомогательных данных Playwright (`data` по умолчанию).
- `PLAYWRIGHT_PROXY` — фиксированный прокси только для Playwright. Если не указан, клиент будет чередовать значения из `PROXY_POOL`.
- `PROXY_POOL` — список прокси через запятую (`http://user:pass@ip:port`). Прокси ротируются по кругу с липким окном 60–120 с; при 403/498/429 или таймаутах используется следующий адрес. Рекомендуются residential/ISP RU-прокси со sticky-сеансами 10–30 минут.
- `HTTPX_PROXY` / `WB_HTTP_PROXY` / `HTTP_PROXY` — прокси для HTTP-запросов (JSON API Wildberries, Telegram и т.д.). Клиент использует первое непустое значение в указанном порядке и логирует его в отчёте (лог без логина/пароля).

CLI `tools/check_wb.py` поддерживает опцию `--html-first`, чтобы сразу запускать Playwright-фолбэк (например, `python tools/check_wb.py --query "стакан" --max 40 --limit 8 --html-first --throttle 2000`). При включённом режиме выводит источник (`html_xhr`/`html_dom`), использованный прокси и факт ротации.

#### Быстрая проверка прокси/IP

Проверить, какой IP-адрес видит Wildberries через httpx:

```bash
python - <<'PY'
import os
import httpx

proxy = os.getenv('HTTPX_PROXY') or os.getenv('WB_HTTP_PROXY') or os.getenv('HTTP_PROXY')
transport = httpx.AsyncHTTPTransport(proxy=proxy) if proxy else None

async def main():
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await client.get('https://ifconfig.me/ip', timeout=5.0)
        print('HTTPX IP:', resp.text.strip())

import asyncio
asyncio.run(main())
PY
```

И проверить IP Playwright (используется тот же прокси и cookies, что и у бота):

```bash
python - <<'PY'
import asyncio
import os
from playwright.async_api import async_playwright

async def main():
    proxy = os.getenv('PLAYWRIGHT_PROXY')
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=os.getenv('PLAYWRIGHT_HEADLESS', 'true').lower() not in {'0','false','no'},
            proxy={'server': proxy} if proxy else None,
        )
        page = await browser.new_page()
        await page.goto('https://ifconfig.me/', wait_until='domcontentloaded', timeout=15000)
        ip = await page.text_content('body')
        print('Playwright IP:', (ip or '').strip())
        await browser.close()

asyncio.run(main())
PY
```

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
