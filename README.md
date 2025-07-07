# yookassa_spec

# Интеграция YooKassa с Telegram ботом: Техническая спецификация

## Содержание

1. [Обзор](#обзор)
2. [Требования](#требования)
3. [Архитектура системы](#архитектура-системы)
4. [Настройка сервера](#настройка-сервера)
   - [Установка необходимого ПО](#установка-необходимого-по)
   - [Создание SSL-сертификата](#создание-ssl-сертификата)
   - [Настройка Nginx](#настройка-nginx)
   - [Настройка Firewall](#настройка-firewall)
5. [Интеграция с YooKassa](#интеграция-с-yookassa)
   - [Получение доступа к API](#получение-доступа-к-api)
   - [Настройка webhook](#настройка-webhook)
   - [Требования к фискализации](#требования-к-фискализации)
6. [Разработка Telegram бота](#разработка-telegram-бота)
   - [Структура проекта](#структура-проекта)
   - [Реализация бота (bot.py)](#реализация-бота-botpy)
   - [Реализация webhook-сервера (webhook_server.py)](#реализация-webhook-сервера-webhook_serverpy)
   - [Хранение данных](#хранение-данных)
7. [Настройка systemd сервисов](#настройка-systemd-сервисов)
8. [Мониторинг и отладка](#мониторинг-и-отладка)
9. [Частые проблемы и решения](#частые-проблемы-и-решения)
10. [Полный код](#полный-код)

## Обзор

Данная техническая спецификация описывает процесс интеграции платежной системы YooKassa с Telegram ботом на Python. Интеграция позволяет пользователям Telegram бота выбирать и оплачивать подписки через YooKassa, а также автоматически обрабатывать уведомления о статусе платежей через webhook.

Основные компоненты системы:
- Telegram бот на основе библиотеки aiogram
- API YooKassa для создания платежей
- Webhook-сервер для обработки уведомлений от YooKassa
- Nginx как прокси для обеспечения HTTPS соединения
- Хранение данных о платежах и подписках

## Требования

### Технические требования

- Сервер с белым IP-адресом
- Linux (Ubuntu/Debian рекомендуется)
- Python 3.9+
- Nginx
- Доступ к API YooKassa (shop_id и secret_key)
- Токен Telegram Bot API

### Программные зависимости

```
aiogram>=3.0.0
yookassa>=2.4.0
fastapi>=0.100.0
uvicorn>=0.23.0
python-dotenv>=1.0.0
```

## Архитектура системы

Система состоит из следующих компонентов:

1. **Telegram бот** - интерфейс для взаимодействия с пользователем, отображает кнопки подписок и создаёт платежи через YooKassa.

2. **Webhook-сервер** - обрабатывает уведомления от YooKassa о статусе платежей и обновляет информацию о подписках пользователей.

3. **Nginx** - выступает в роли прокси для webhook-сервера, обеспечивая HTTPS соединение и перенаправление запросов.

4. **Хранилище данных** - хранит информацию о платежах и подписках пользователей (для упрощения используются файлы формата pickle).

Схема взаимодействия:
```
Пользователь <-> Telegram Bot <-> YooKassa API
                                     |
                                     v
                       YooKassa Webhook -> Nginx -> Webhook-сервер
```

## Настройка сервера

### Установка необходимого ПО

```bash
# Обновление системы
apt update
apt upgrade -y

# Установка необходимых пакетов
apt install -y python3 python3-pip python3-venv nginx certbot openssl

# Создание директории проекта
mkdir -p /opt/telegram-yookassa-bot
cd /opt/telegram-yookassa-bot

# Создание виртуального окружения
python3 -m venv venv
source venv/bin/activate

# Установка зависимостей
pip install aiogram yookassa fastapi uvicorn python-dotenv
```

### Создание SSL-сертификата

Для работы с YooKassa требуется HTTPS соединение. При отсутствии домена можно создать самоподписанный сертификат для IP-адреса:

```bash
# Создание директории для сертификатов
mkdir -p /etc/ssl/private

# Генерация приватного ключа
openssl genrsa -out /etc/ssl/private/webhook-selfsigned.key 2048

# Создание самоподписанного сертификата
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /etc/ssl/private/webhook-selfsigned.key \
  -out /etc/ssl/certs/webhook-selfsigned.crt \
  -subj "/CN=YOUR_SERVER_IP" \
  -addext "subjectAltName = IP:YOUR_SERVER_IP"
```

> **Важно:** Замените `YOUR_SERVER_IP` на реальный IP-адрес вашего сервера.

### Настройка Nginx

Создайте конфигурационный файл для Nginx:

```bash
cat > /etc/nginx/sites-available/yookassa-webhook << 'EOL'
server {
    listen 80;
    server_name YOUR_SERVER_IP;
    
    # Redirect HTTP to HTTPS
    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl;
    server_name YOUR_SERVER_IP;

    ssl_certificate /etc/ssl/certs/webhook-selfsigned.crt;
    ssl_certificate_key /etc/ssl/private/webhook-selfsigned.key;
    
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;
    ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512:ECDHE-RSA-AES256-GCM-SHA384:DHE-RSA-AES256-GCM-SHA384;
    
    # Webhook endpoint
    location /webhook {
        proxy_pass http://127.0.0.1:8000/webhook;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    # Health check endpoint
    location /health {
        proxy_pass http://127.0.0.1:8000/health;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
    
    # Debug endpoint
    location /debug/storage {
        proxy_pass http://127.0.0.1:8000/debug/storage;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
    
    # Test webhook endpoint
    location ~ ^/test-webhook/(.*)$ {
        proxy_pass http://127.0.0.1:8000/test-webhook/$1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOL
```

> **Важно:** Замените `YOUR_SERVER_IP` на реальный IP-адрес вашего сервера.

Активируйте конфигурацию:

```bash
# Создание символической ссылки
ln -s /etc/nginx/sites-available/yookassa-webhook /etc/nginx/sites-enabled/

# Удаление дефолтной конфигурации, если необходимо
rm -f /etc/nginx/sites-enabled/default

# Проверка конфигурации
nginx -t

# Перезапуск Nginx
systemctl restart nginx
```

### Настройка Firewall

Настройте файрвол для разрешения HTTP, HTTPS и SSH трафика:

```bash
# Установка ufw, если не установлен
apt install -y ufw

# Разрешение необходимых портов
ufw allow ssh
ufw allow http
ufw allow https

# Включение файрвола
ufw --force enable

# Проверка статуса
ufw status
```

## Интеграция с YooKassa

### Получение доступа к API

1. Зарегистрируйтесь в YooKassa (https://yookassa.ru/)
2. Создайте магазин в личном кабинете
3. Получите `shop_id` и `secret_key` в разделе API

### Настройка webhook

1. В личном кабинете YooKassa перейдите в раздел "Настройки" > "API" > "Уведомления"
2. Добавьте новый webhook с URL: `https://YOUR_SERVER_IP/webhook`
3. Выберите уведомления, которые хотите получать (минимум - "payment.succeeded")

> **Важно:** YooKassa может не принимать самоподписанные сертификаты. В этом случае рекомендуется использовать домен с валидным SSL-сертификатом.

### Требования к фискализации

Согласно законодательству РФ (54-ФЗ), при приеме платежей необходимо передавать информацию для формирования фискального чека. При создании платежа в YooKassa требуется передавать следующие данные:

```python
"receipt": {
    "customer": {
        "email": "customer@example.com"  # Email клиента
    },
    "items": [
        {
            "description": "Описание товара/услуги",
            "amount": {
                "value": "100.00",
                "currency": "RUB"
            },
            "vat_code": 1,  # Код ставки НДС (1 - без НДС)
            "quantity": 1,
            "payment_subject": "service",  # Предмет расчета
            "payment_mode": "full_payment"  # Признак способа расчета
        }
    ]
}
```

**Коды ставки НДС (vat_code):**
- 1 - без НДС
- 2 - НДС по ставке 0%
- 3 - НДС по ставке 10%
- 4 - НДС по ставке 20%
- 5 - НДС по расчетной ставке 10/110
- 6 - НДС по расчетной ставке 20/120

**Предмет расчета (payment_subject):**
- "commodity" - товар
- "excise" - подакцизный товар
- "job" - работа
- "service" - услуга
- и другие

**Признак способа расчета (payment_mode):**
- "full_prepayment" - предоплата 100%
- "partial_prepayment" - предоплата
- "advance" - аванс
- "full_payment" - полный расчет
- и другие

## Разработка Telegram бота

### Структура проекта

```
/opt/telegram-yookassa-bot/
├── venv/                  # Виртуальное окружение
├── bot.py                 # Основной файл бота
├── webhook_server.py      # Сервер для обработки webhook
├── .env                   # Файл с переменными окружения
├── requirements.txt       # Зависимости проекта
├── payment_storage.pkl    # Хранилище данных о платежах
└── user_subscriptions.pkl # Хранилище данных о подписках
```

Файл `.env` должен содержать:

```
TELEGRAM_TOKEN=your_telegram_token
YOOKASSA_SHOP_ID=your_shop_id
YOOKASSA_SECRET_KEY=your_secret_key
WEBHOOK_URL=https://your_server_ip/webhook
```

### Реализация бота (bot.py)

Основной файл Telegram бота:

```python
import logging
import os
from datetime import datetime, timedelta
import uuid
import json
import pickle
from typing import Dict, Any, Optional

# Environment variables
from dotenv import load_dotenv
load_dotenv()

# Aiogram libraries
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# YooKassa integration
from yookassa import Configuration, Payment

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    filename="/opt/telegram-yookassa-bot/bot.log"
)
logger = logging.getLogger(__name__)

# Environment variables
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
YOOKASSA_SHOP_ID = os.environ.get("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.environ.get("YOOKASSA_SECRET_KEY")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

# Configure YooKassa
Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY

# Data storage functions
def load_data():
    global payment_storage, user_subscriptions
    
    try:
        with open('/opt/telegram-yookassa-bot/payment_storage.pkl', 'rb') as f:
            payment_storage = pickle.load(f)
            logger.info(f"Event type: {event}")
        
        # Process payment.succeeded events
        if event == "payment.succeeded":
            logger.info("Processing payment.succeeded event")
            await process_successful_payment(data)
        elif event == "payment.waiting_for_capture":
            logger.info("Processing payment.waiting_for_capture event")
            # For testing, we can also process this event
            await process_successful_payment(data)
        else:
            logger.info(f"Received {event} event, not processing")
        
        # Always return 200 OK to acknowledge the webhook
        return {"status": "success"}
    
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON: {e}")
        return {"status": "success"}  # Still return 200 OK
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        # Still return 200 OK to prevent retries
        return {"status": "success"}

# Health check endpoint
@app.get("/health")
async def health_check():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

# Debug endpoint for viewing storage
@app.get("/debug/storage")
async def debug_storage():
    """Display storage contents for debugging."""
    load_data()
    return {
        "payment_storage": payment_storage,
        "user_subscriptions": user_subscriptions
    }

# Test endpoint for debugging
@app.get("/test-webhook/{payment_id}")
async def test_webhook(payment_id: str):
    """Test endpoint to simulate a webhook call."""
    # Reload data to get the latest updates
    load_data()
    
    if payment_id not in payment_storage:
        return {"status": "error", "message": f"Payment {payment_id} not found"}
    
    # Create a test webhook payload
    test_data = {
        "event": "payment.succeeded",
        "object": {
            "id": payment_id,
            "status": "succeeded",
            "metadata": payment_storage[payment_id].get("metadata", {})
        }
    }
    
    # Process the test webhook
    success = await process_successful_payment(test_data)
    
    if success:
        return {"status": "success", "message": f"Successfully processed test webhook for payment {payment_id}"}
    else:
        return {"status": "error", "message": f"Failed to process test webhook for payment {payment_id}"}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

### Хранение данных

В данной реализации используется простое хранение данных в файлах формата pickle:

1. **payment_storage.pkl** - хранит информацию о платежах:
   ```python
   {
       "payment_id": {
           "user_id": 123456789,
           "amount": "1.00",
           "subscription_days": 30,
           "subscription_id": "sub_30",
           "status": "pending",
           "created_at": "2025-07-07T10:30:00.000000",
           "processed": False
       },
       ...
   }
   ```

2. **user_subscriptions.pkl** - хранит информацию о подписках пользователей:
   ```python
   {
       123456789: {
           "end_date": "2025-08-07T10:30:00.000000"
       },
       ...
   }
   ```

> **Важно:** В производственной среде рекомендуется использовать более надежные системы хранения данных, такие как SQLite, PostgreSQL или Redis.

## Настройка systemd сервисов

Для автоматического запуска и перезапуска бота и webhook-сервера создайте systemd сервисы:

### Сервис для бота

```bash
cat > /etc/systemd/system/tg-yookassa-bot.service << 'EOL'
[Unit]
Description=Telegram YooKassa Bot
After=network.target

[Service]
User=root
WorkingDirectory=/opt/telegram-yookassa-bot
ExecStart=/opt/telegram-yookassa-bot/venv/bin/python /opt/telegram-yookassa-bot/bot.py
Restart=always
RestartSec=10
StandardOutput=append:/opt/telegram-yookassa-bot/bot.log
StandardError=append:/opt/telegram-yookassa-bot/bot_error.log

[Install]
WantedBy=multi-user.target
EOL
```

### Сервис для webhook-сервера

```bash
cat > /etc/systemd/system/tg-yookassa-webhook.service << 'EOL'
[Unit]
Description=Telegram YooKassa Webhook Server
After=network.target

[Service]
User=root
WorkingDirectory=/opt/telegram-yookassa-bot
ExecStart=/opt/telegram-yookassa-bot/venv/bin/python /opt/telegram-yookassa-bot/webhook_server.py
Restart=always
RestartSec=10
StandardOutput=append:/opt/telegram-yookassa-bot/webhook.log
StandardError=append:/opt/telegram-yookassa-bot/webhook_error.log

[Install]
WantedBy=multi-user.target
EOL
```

Активируйте и запустите сервисы:

```bash
# Перезагрузка systemd для обнаружения новых сервисов
systemctl daemon-reload

# Включение сервисов для автозапуска
systemctl enable tg-yookassa-bot.service
systemctl enable tg-yookassa-webhook.service

# Запуск сервисов
systemctl start tg-yookassa-bot.service
systemctl start tg-yookassa-webhook.service
```

## Мониторинг и отладка

### Просмотр логов

```bash
# Логи бота
tail -f /opt/telegram-yookassa-bot/bot.log
tail -f /opt/telegram-yookassa-bot/bot_error.log

# Логи webhook-сервера
tail -f /opt/telegram-yookassa-bot/webhook.log
tail -f /opt/telegram-yookassa-bot/webhook_error.log

# Логи Nginx
tail -f /var/log/nginx/access.log
tail -f /var/log/nginx/error.log
```

### Проверка статуса сервисов

```bash
systemctl status tg-yookassa-bot.service
systemctl status tg-yookassa-webhook.service
systemctl status nginx
```

### Отладочные эндпоинты

Для упрощения отладки webhook-сервер предоставляет следующие эндпоинты:

1. **Проверка работоспособности**:
   ```
   https://YOUR_SERVER_IP/health
   ```

2. **Просмотр данных в хранилище**:
   ```
   https://YOUR_SERVER_IP/debug/storage
   ```

## Частые проблемы и решения

### 1. YooKassa не принимает самоподписанный сертификат

**Проблема**: YooKassa может отклонять webhook с самоподписанным сертификатом.

**Решение**: 
- Приобретите домен и настройте валидный SSL-сертификат через Let's Encrypt
- Обратитесь в поддержку YooKassa для уточнения требований к SSL

### 2. Webhook не получает уведомления

**Проблема**: Webhook-сервер не получает уведомления от YooKassa.

**Решение**:
- Проверьте настройки webhook в личном кабинете YooKassa
- Убедитесь, что ваш сервер доступен из интернета
- Проверьте логи Nginx на наличие ошибок
- Проверьте, что webhook-сервер запущен и слушает порт 8000

### 3. Ошибка при создании платежа

**Проблема**: Возникает ошибка `Receipt is missing or illegal` при создании платежа.

**Решение**:
- Добавьте информацию о чеке при создании платежа (email клиента, информация о товаре/услуге, НДС и т.д.)
- Убедитесь, что формат данных для чека соответствует требованиям YooKassa

### 4. Платеж создается, но webhook не обрабатывает его

**Проблема**: Платеж успешно создается, но webhook не обрабатывает уведомление о платеже.

**Решение**:
- Проверьте, что webhook-сервер корректно загружает данные из хранилища
- Убедитесь, что ID платежа сохраняется в хранилище при создании
- Добавьте дополнительное логирование для отслеживания проблемы
- Проверьте, что webhook возвращает статус 200 OK даже в случае ошибки

## Полный код

Полный код проекта доступен в этой спецификации и может быть использован как основа для реализации интеграции YooKassa с Telegram ботом.

### Дополнительные рекомендации

1. **Безопасность**:
   - Не запускайте сервисы от имени root в производственной среде
   - Храните конфиденциальную информацию (токены, ключи) в безопасном месте
   - Регулярно обновляйте систему и зависимости

2. **Масштабируемость**:
   - Для производственной среды рекомендуется использовать базу данных вместо файлового хранилища
   - Разделите бизнес-логику и хранение данных для упрощения поддержки
   - Добавьте систему мониторинга для отслеживания состояния сервисов

3. **Обработка ошибок**:
   - Добавьте более детальную обработку ошибок и уведомления администратора
   - Реализуйте механизм повторных попыток для обработки платежей в случае ошибок
   - Добавьте валидацию входных данных для предотвращения ошибок

4. **Тестирование**:
   - Используйте тестовый режим YooKassa для тестирования интеграции
   - Создайте автоматизированные тесты для проверки основных функций
   - Проведите нагрузочное тестирование для оценки производительностиinfo(f"Loaded {len(payment_storage)} payments from storage")
    except FileNotFoundError:
        payment_storage = {}
        logger.info("Created new payment storage")
    
    try:
        with open('/opt/telegram-yookassa-bot/user_subscriptions.pkl', 'rb') as f:
            user_subscriptions = pickle.load(f)
            logger.info(f"Loaded {len(user_subscriptions)} subscriptions from storage")
    except FileNotFoundError:
        user_subscriptions = {}
        logger.info("Created new user subscriptions storage")

def save_data():
    with open('/opt/telegram-yookassa-bot/payment_storage.pkl', 'wb') as f:
        pickle.dump(payment_storage, f)
    
    with open('/opt/telegram-yookassa-bot/user_subscriptions.pkl', 'wb') as f:
        pickle.dump(user_subscriptions, f)
    
    logger.info("Data saved to files")

# Load data on startup
load_data()

# Define subscription options
SUBSCRIPTIONS = {
    "sub_30": {"days": 30, "price": "1.00", "description": "Подписка на 30 дней"},
    "sub_60": {"days": 60, "price": "2.00", "description": "Подписка на 60 дней"},
    "sub_90": {"days": 90, "price": "3.00", "description": "Подписка на 90 дней"},
    "sub_10": {"days": 10, "price": "4.00", "description": "Подписка на 10 дней"},
}

# Define FSM states
class SubscriptionStates(StatesGroup):
    choosing_subscription = State()

# Initialize bot and dispatcher
bot = Bot(token=TELEGRAM_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    """Send a message with subscription options."""
    keyboard = InlineKeyboardBuilder()
    keyboard.row(
        InlineKeyboardButton(text="30 дней - 1₽", callback_data="sub_30"),
        InlineKeyboardButton(text="60 дней - 2₽", callback_data="sub_60")
    )
    keyboard.row(
        InlineKeyboardButton(text="90 дней - 3₽", callback_data="sub_90"),
        InlineKeyboardButton(text="10 дней - 4₽", callback_data="sub_10")
    )
    
    await message.answer(
        "Выберите подписку:", 
        reply_markup=keyboard.as_markup()
    )
    await state.set_state(SubscriptionStates.choosing_subscription)
    logger.info(f"User {message.from_user.id} started the bot")

@dp.callback_query(F.data.startswith("sub_"), SubscriptionStates.choosing_subscription)
async def process_subscription_choice(callback: types.CallbackQuery, state: FSMContext):
    """Handle the subscription choice and create payment."""
    await callback.answer()
    
    user_id = callback.from_user.id
    subscription_id = callback.data
    
    if subscription_id not in SUBSCRIPTIONS:
        await callback.message.edit_text("Произошла ошибка. Попробуйте снова.")
        logger.error(f"Invalid subscription ID: {subscription_id}")
        await state.clear()
        return
    
    subscription = SUBSCRIPTIONS[subscription_id]
    
    # Генерируем email на основе ID пользователя
    user_email = f"{user_id}@telegram.org"
    
    # Create a unique idempotence key
    idempotence_key = str(uuid.uuid4())
    
    try:
        # Create a payment using YooKassa with receipt information
        payment = Payment.create({
            "amount": {
                "value": subscription["price"],
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": f"https://t.me/{(await bot.get_me()).username}"
            },
            "capture": True,
            "description": subscription["description"],
            "metadata": {
                "user_id": str(user_id),
                "subscription_id": subscription_id,
                "days": str(subscription["days"])
            },
            "receipt": {
                "customer": {
                    "email": user_email
                },
                "items": [
                    {
                        "description": subscription["description"],
                        "amount": {
                            "value": subscription["price"],
                            "currency": "RUB"
                        },
                        "vat_code": 1,
                        "quantity": 1,
                        "payment_subject": "service",
                        "payment_mode": "full_payment"
                    }
                ]
            }
        }, idempotence_key)
        
        logger.info(f"Created payment with ID: {payment.id}")
        
        # Store payment info in our storage
        payment_storage[payment.id] = {
            "user_id": user_id,
            "amount": subscription["price"],
            "subscription_days": subscription["days"],
            "subscription_id": subscription_id,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
        }
        
        # Save data to files
        save_data()
        
        logger.info(f"Created payment {payment.id} for user {user_id}")
        
        # Create inline keyboard with payment link
        keyboard = InlineKeyboardBuilder()
        keyboard.row(InlineKeyboardButton(
            text="Оплатить", 
            url=payment.confirmation.confirmation_url
        ))
        keyboard.row(InlineKeyboardButton(
            text="Отмена", 
            callback_data="cancel_payment"
        ))
        
        await callback.message.edit_text(
            f"Подписка: {subscription['description']}\n"
            f"Стоимость: {subscription['price']}₽\n\n"
            "Нажмите кнопку ниже для оплаты:",
            reply_markup=keyboard.as_markup(),
        )
        
    except Exception as e:
        logger.error(f"Error creating payment: {e}")
        await callback.message.edit_text(
            "Произошла ошибка при создании платежа. Попробуйте позже."
        )
        await state.clear()

@dp.callback_query(F.data == "cancel_payment")
async def cancel_payment(callback: types.CallbackQuery, state: FSMContext):
    """Cancel the payment process."""
    await callback.answer()
    await callback.message.edit_text("Платеж отменен. Чтобы начать снова, используйте /start")
    await state.clear()
    logger.info(f"User {callback.from_user.id} canceled payment")

@dp.message(Command("subscription"))
async def check_subscription(message: types.Message):
    """Check current subscription status."""
    user_id = message.from_user.id
    
    # Reload data to get the latest updates
    load_data()
    
    if user_id in user_subscriptions:
        end_date = datetime.fromisoformat(user_subscriptions[user_id]["end_date"])
        days_left = (end_date - datetime.now()).days
        
        if days_left > 0:
            await message.answer(
                f"У вас активная подписка до {end_date.strftime('%d.%m.%Y')}.\n"
                f"Осталось дней: {days_left}"
            )
            logger.info(f"User {user_id} checked subscription: active, {days_left} days left")
        else:
            await message.answer(
                "У вас нет активной подписки. Используйте /start чтобы оформить подписку."
            )
            logger.info(f"User {user_id} checked subscription: expired")
    else:
        await message.answer(
            "У вас нет активной подписки. Используйте /start чтобы оформить подписку."
        )
        logger.info(f"User {user_id} checked subscription: not found")

async def main():
    # Start the bot
    logger.info("Starting bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

### Реализация webhook-сервера (webhook_server.py)

Файл для обработки webhook-уведомлений от YooKassa:

```python
import logging
import os
import json
import pickle
from typing import Dict, Any
from datetime import datetime, timedelta
import asyncio

from fastapi import FastAPI, Request, HTTPException
import uvicorn
from dotenv import load_dotenv
from aiogram import Bot

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG,
    filename="/opt/telegram-yookassa-bot/webhook.log"
)
logger = logging.getLogger(__name__)

# Get environment variables
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
bot = Bot(token=TELEGRAM_TOKEN)

# Data storage functions
def load_data():
    global payment_storage, user_subscriptions
    
    try:
        with open('/opt/telegram-yookassa-bot/payment_storage.pkl', 'rb') as f:
            payment_storage = pickle.load(f)
            logger.info(f"Loaded {len(payment_storage)} payments from storage")
            logger.debug(f"Payment IDs in storage: {list(payment_storage.keys())}")
    except FileNotFoundError:
        payment_storage = {}
        logger.info("Created new payment storage")
    
    try:
        with open('/opt/telegram-yookassa-bot/user_subscriptions.pkl', 'rb') as f:
            user_subscriptions = pickle.load(f)
            logger.info(f"Loaded {len(user_subscriptions)} subscriptions from storage")
    except FileNotFoundError:
        user_subscriptions = {}
        logger.info("Created new user subscriptions storage")

def save_data():
    with open('/opt/telegram-yookassa-bot/payment_storage.pkl', 'wb') as f:
        pickle.dump(payment_storage, f)
    
    with open('/opt/telegram-yookassa-bot/user_subscriptions.pkl', 'wb') as f:
        pickle.dump(user_subscriptions, f)
    
    logger.info("Data saved to files")

# Load data on startup
load_data()

# Create FastAPI app
app = FastAPI()

async def process_successful_payment(payment_data: Dict[str, Any]) -> bool:
    """Process a successful payment from YooKassa webhook."""
    logger.debug(f"Processing payment data: {json.dumps(payment_data)}")
    
    payment_id = payment_data.get("object", {}).get("id")
    
    if not payment_id:
        logger.warning("No payment ID found in the webhook data")
        return False
    
    # Reload data to get the latest updates
    load_data()
    
    logger.debug(f"Payment ID from webhook: {payment_id}")
    logger.debug(f"Available payment IDs in storage: {list(payment_storage.keys())}")
    
    # If the payment is not in storage but contains valid metadata, we'll create it
    if payment_id not in payment_storage:
        # Try to get user data from metadata
        metadata = payment_data.get("object", {}).get("metadata", {})
        user_id = metadata.get("user_id")
        subscription_id = metadata.get("subscription_id")
        days = metadata.get("days")
        
        if user_id and subscription_id and days:
            logger.info(f"Payment {payment_id} not found in storage, but contains valid metadata. Creating entry.")
            payment_storage[payment_id] = {
                "user_id": int(user_id),
                "subscription_id": subscription_id,
                "subscription_days": int(days),
                "status": "pending",
                "created_at": datetime.now().isoformat(),
            }
            save_data()
        else:
            logger.warning(f"Unknown payment ID received: {payment_id} and no valid metadata found")
            return False
    
    # Get stored payment data
    stored_payment = payment_storage[payment_id]
    user_id = stored_payment["user_id"]
    days = stored_payment["subscription_days"]
    
    # Check if payment is successful
    status = payment_data.get("object", {}).get("status")
    logger.debug(f"Payment status: {status}")
    
    if status != "succeeded":
        logger.info(f"Payment {payment_id} status: {status}, not processing")
        stored_payment["status"] = status
        save_data()
        return True
    
    # If payment is already processed, don't process it again
    if stored_payment.get("status") == "succeeded" and stored_payment.get("processed", False):
        logger.info(f"Payment {payment_id} already processed, skipping")
        return True
    
    # Update payment status
    stored_payment["status"] = "succeeded"
    stored_payment["processed"] = True
    
    # Calculate subscription end date
    now = datetime.now()
    
    # If user already has a subscription, extend it
    if user_id in user_subscriptions:
        current_end = datetime.fromisoformat(user_subscriptions[user_id]["end_date"])
        if current_end > now:
            end_date = current_end + timedelta(days=days)
        else:
            end_date = now + timedelta(days=days)
    else:
        end_date = now + timedelta(days=days)
    
    # Update user subscription
    user_subscriptions[user_id] = {"end_date": end_date.isoformat()}
    
    # Save the updated data
    save_data()
    
    logger.info(f"Successfully processed payment {payment_id} for user {user_id}")
    logger.info(f"User {user_id} subscription extended until {end_date.isoformat()}")
    
    # Notify user about successful payment
    try:
        logger.debug(f"Attempting to send notification to user {user_id}")
        await bot.send_message(
            chat_id=int(user_id),
            text=f"Ваш платеж успешно обработан!\n"
                f"Подписка активна до {end_date.strftime('%d.%m.%Y')}"
        )
        logger.info(f"Sent payment confirmation to user {user_id}")
    except Exception as e:
        logger.error(f"Error sending payment confirmation to user {user_id}: {e}")
    
    return True

@app.post("/webhook")
async def yookassa_webhook(request: Request):
    """Handle YooKassa webhook notifications."""
    logger.info("Received webhook request")
    
    # According to YooKassa documentation, we need to return 200 OK
    # even if there's an error, to prevent retries
    
    # Log request headers for debugging
    headers = dict(request.headers.items())
    logger.debug(f"Request headers: {json.dumps(headers)}")
    
    try:
        # Get request body
        body = await request.body()
        logger.debug(f"Raw request body: {body.decode('utf-8')}")
        
        # Parse JSON
        data = await request.json()
        logger.info(f"Received webhook data: {json.dumps(data)}")
        
        # Verify the event type (payment.succeeded, payment.canceled, etc.)
        event = data.get("event")
        if not event:
            logger.warning("No event in webhook data")
            return {"status": "success"}  # Still return 200 OK
        
        logger.info(f"Event type: {event}")
        
        # Process payment.succeeded events
        if event == "payment.succeeded":
            logger.info("Processing payment.succeeded event")
            await process_successful_payment(data)
        elif event == "payment.waiting_for_capture":
            logger.info("Processing payment.waiting_for_capture event")
            # For testing, we can also process this event
            await process_successful_payment(data)
        else:
            logger.info(f"Received {event} event, not processing")
        
        # Always return 200 OK to acknowledge the webhook
        return {"status": "success"}
    
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON: {e}")
        return {"status": "success"}  # Still return 200 OK
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        # Still return 200 OK to prevent retries
        return {"status": "success"}
