import telebot
from telebot.types import LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton
import os
from dotenv import load_dotenv
import logging
from logging.handlers import RotatingFileHandler
import time
import sys
import signal
import sqlite3
import requests
import json
import threading
import uvicorn
from webhook_server import app, set_bot_instance
from pyngrok import ngrok, conf

# Настройка логирования
def setup_logging():
    # Создаем директорию для логов, если она не существует
    log_dir = 'logs'
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    # Настройка основного логгера
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    
    # Форматтер для логов
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Обработчик для файла с ротацией (10 файлов по 5 МБ)
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, 'bot.log'), 
        maxBytes=5*1024*1024,  # 5 МБ
        backupCount=10
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    
    # Обработчик для файла с детальными логами
    debug_handler = RotatingFileHandler(
        os.path.join(log_dir, 'debug.log'), 
        maxBytes=5*1024*1024,  # 5 МБ
        backupCount=5
    )
    debug_handler.setLevel(logging.DEBUG)
    debug_handler.setFormatter(formatter)
    
    # Обработчик для консоли
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)
    
    # Добавляем обработчики к логгеру
    logger.addHandler(file_handler)
    logger.addHandler(debug_handler)
    logger.addHandler(console_handler)
    
    # Отдельный логгер для запросов к API
    api_logger = logging.getLogger('api')
    api_file_handler = RotatingFileHandler(
        os.path.join(log_dir, 'api.log'), 
        maxBytes=5*1024*1024,
        backupCount=5
    )
    api_file_handler.setFormatter(formatter)
    api_logger.addHandler(api_file_handler)
    api_logger.setLevel(logging.DEBUG)
    
    return logger, api_logger

# Инициализация логгеров
logger, api_logger = setup_logging()

# Загрузка переменных окружения из файла .env
load_dotenv()
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    logger.critical("No TELEGRAM_BOT_TOKEN provided")
    raise ValueError("No TELEGRAM_BOT_TOKEN provided")

PRIVATE_CHANNEL_ID = os.getenv('PRIVATE_CHANNEL_ID')
if not PRIVATE_CHANNEL_ID:
    logger.critical("No PRIVATE_CHANNEL_ID provided")
    raise ValueError("No PRIVATE_CHANNEL_ID provided")

LAVA_API_KEY = os.getenv('LAVA_API_KEY')
if not LAVA_API_KEY:
    logger.critical("No LAVA_API_KEY provided")
    raise ValueError("No LAVA_API_KEY provided")

LAVA_OFFER_ID = os.getenv('LAVA_OFFER_ID')
if not LAVA_OFFER_ID:
    logger.critical("No LAVA_OFFER_ID provided")
    raise ValueError("No LAVA_OFFER_ID provided")

logger.info("Environment variables loaded successfully")

# Инициализация бота
bot = telebot.TeleBot(TOKEN)
logger.info("Telegram bot initialized")

# Инициализация базы данных
def init_db():
    try:
        conn = sqlite3.connect('subscribers.db')
        cursor = conn.cursor()
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS subscribers (
            telegram_id TEXT PRIMARY KEY,
            payment_id TEXT,
            status TEXT,
            expiry_date TEXT
        )
        ''')
        conn.commit()
        conn.close()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        raise

# Функция для создания инвойса в Lava API
def create_lava_invoice(telegram_id):
    url = "https://gate.lava.top/api/v2/invoice"
    headers = {
        "X-Api-Key": LAVA_API_KEY,
        "Content-Type": "application/json"
    }
    
    # Используем точный формат из примера
    payload = {
        "email": f"{telegram_id}@t.me",  # Формат должен быть TELEGRAM_ID@t.me
        "offerId": LAVA_OFFER_ID,
        "periodicity": "MONTHLY",
        "currency": "RUB",  # Изменено на RUB вместо USD
        "buyerLanguage": "RU",
        "paymentMethod": "BANK131",
        "clientUtm": {}
    }
    
    api_logger.debug(f"Creating Lava invoice for user {telegram_id}")
    api_logger.debug(f"Request payload: {json.dumps(payload)}")
    api_logger.debug(f"Using X-Api-Key header for authentication")
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        api_logger.debug(f"Response status code: {response.status_code}")
        
        if response.status_code == 400:
            api_logger.error(f"Bad Request: {response.text}")
            # Попробуем получить детали ошибки
            try:
                error_details = response.json()
                api_logger.error(f"Error details: {json.dumps(error_details)}")
            except:
                pass
            return None
        elif response.status_code == 401:
            api_logger.error("Authentication failed: Invalid API key or unauthorized access")
            return None
            
        response.raise_for_status()
        api_logger.debug(f"Lava API response: {response.text}")
        api_logger.info(f"Successfully created invoice for user {telegram_id}")
        return response.json()
    except requests.exceptions.RequestException as e:
        api_logger.error(f"Error creating Lava invoice: {e}")
        if hasattr(e, 'response') and e.response:
            api_logger.error(f"Response status: {e.response.status_code}")
            api_logger.error(f"Response body: {e.response.text}")
        return None

# Обработчик команды /start
@bot.message_handler(commands=['start'])
def welcome(message):
    user_id = message.from_user.id
    username = message.from_user.username or "Unknown"
    logger.info(f"User {user_id} (@{username}) started the bot")
    
    if message.chat.type == 'private':
        keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        button_buy = telebot.types.KeyboardButton(text="✅ Оплатить доступ")
        keyboard.add(button_buy)
        bot.send_message(
            message.chat.id,
            'Приветствую! '
            'Я помогу Вам получить доступ в платный канал BuryatFilms.',
            reply_markup=keyboard
        )
        logger.debug(f"Sent welcome message to user {user_id}")

# Обработчик текстовых сообщений
@bot.message_handler(content_types=['text'])
def handle_text(message):
    user_id = message.from_user.id
    text = message.text
    logger.debug(f"Received text message from user {user_id}: {text}")
    
    if message.text == "✅ Оплатить доступ":
        logger.info(f"User {user_id} requested payment")
        process_payment(message)

# Функция обработки оплаты
def process_payment(message):
    telegram_id = message.from_user.id
    logger.info(f"Processing payment for user {telegram_id}")
    
    invoice_data = create_lava_invoice(telegram_id)
    
    if invoice_data and 'paymentUrl' in invoice_data:
        payment_url = invoice_data['paymentUrl']
        payment_id = invoice_data.get('id', 'unknown')
        logger.info(f"Created invoice {payment_id} for user {telegram_id}")
        
        # Сохраняем информацию о платеже в базу данных
        try:
            conn = sqlite3.connect('subscribers.db')
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO subscribers (telegram_id, payment_id, status) VALUES (?, ?, ?)",
                (str(telegram_id), payment_id, "pending")
            )
            conn.commit()
            conn.close()
            logger.debug(f"Saved payment info to database for user {telegram_id}")
        except Exception as e:
            logger.error(f"Error saving payment info to database: {e}")
        
        # Создаем инлайн-клавиатуру с кнопкой для оплаты
        keyboard = InlineKeyboardMarkup()
        payment_button = InlineKeyboardButton(text="Перейти к оплате", url=payment_url)
        keyboard.add(payment_button)
        
        bot.send_message(
            message.chat.id,
            "Для оплаты доступа к каналу, пожалуйста, нажмите на кнопку ниже:",
            reply_markup=keyboard
        )
        logger.debug(f"Sent payment button to user {telegram_id}")
    else:
        logger.error(f"Failed to create invoice for user {telegram_id}")
        bot.send_message(
            message.chat.id,
            "Извините, произошла ошибка при создании платежа. Пожалуйста, попробуйте позже."
        )

# Функция для проверки и удаления пользователей с истекшей подпиской
def check_expired_subscriptions():
    logger.info("Checking for expired subscriptions")
    try:
        conn = sqlite3.connect('subscribers.db')
        cursor = conn.cursor()
        
        # Находим пользователей с истекшей подпиской
        cursor.execute(
            "SELECT telegram_id FROM subscribers WHERE status = 'active' AND expiry_date < datetime('now')"
        )
        expired_users = cursor.fetchall()
        
        if expired_users:
            logger.info(f"Found {len(expired_users)} expired subscriptions")
        else:
            logger.info("No expired subscriptions found")
        
        # Обновляем статус
        cursor.execute(
            "UPDATE subscribers SET status = 'expired' WHERE status = 'active' AND expiry_date < datetime('now')"
        )
        conn.commit()
        conn.close()
        
        # Удаляем пользователей из канала
        for user in expired_users:
            telegram_id = user[0]
            logger.info(f"Processing expired subscription for user {telegram_id}")
            try:
                bot.kick_chat_member(PRIVATE_CHANNEL_ID, telegram_id)
                logger.debug(f"Kicked user {telegram_id} from channel")
                bot.unban_chat_member(PRIVATE_CHANNEL_ID, telegram_id)
                logger.debug(f"Unbanned user {telegram_id} from channel")
                bot.send_message(
                    telegram_id,
                    "Ваша подписка истекла. Для продления доступа, пожалуйста, оплатите подписку снова."
                )
                logger.debug(f"Sent expiration notification to user {telegram_id}")
            except Exception as e:
                logger.error(f"Error removing user {telegram_id} from channel: {e}")
    except Exception as e:
        logger.error(f"Error checking expired subscriptions: {e}")

# Прерывание скрипта пользователем
def signal_handler(sig, frame):
    logger.info("Program interrupted by user.")
    sys.exit(0)

# Функция для настройки ngrok туннеля
def setup_ngrok(port):
    # Проверяем, есть ли NGROK_AUTH_TOKEN в переменных окружения
    ngrok_auth_token = os.getenv('NGROK_AUTH_TOKEN')
    if ngrok_auth_token:
        logger.info("Setting up ngrok with auth token")
        conf.get_default().auth_token = ngrok_auth_token
    else:
        logger.warning("NGROK_AUTH_TOKEN not found. Using ngrok without authentication.")
    
    # Запускаем ngrok туннель
    try:
        # Закрываем все существующие туннели
        for tunnel in ngrok.get_tunnels():
            ngrok.disconnect(tunnel.public_url)
        
        # Создаем новый туннель
        public_url = ngrok.connect(port, "http")
        logger.info(f"Ngrok tunnel established: {public_url}")
        
        # Извлекаем URL для вебхука
        webhook_url = f"{public_url}/webhook/lava"
        logger.info(f"Webhook URL: {webhook_url}")
        
        return webhook_url
    except Exception as e:
        logger.error(f"Error setting up ngrok: {e}")
        return None

# Основное тело скрипта
def main():
    logger.info("Starting application")
    
    # Инициализация базы данных
    init_db()
    
    # Настройка планировщика для проверки истекших подписок
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_expired_subscriptions, 'interval', hours=24)
    scheduler.start()
    logger.info("Scheduler started")
    
    # Передаем экземпляр бота в FastAPI приложение
    set_bot_instance(bot, PRIVATE_CHANNEL_ID)
    logger.info("Bot instance set for webhook server")
    
    # Настройка ngrok для доступа к вебхуку
    webhook_url = setup_ngrok(8000)
    if webhook_url:
        logger.info(f"Please configure your Lava API webhook to: {webhook_url}")
        # Можно также отправить URL администратору бота
        admin_id = os.getenv('ADMIN_TELEGRAM_ID')
        if admin_id:
            try:
                bot.send_message(admin_id, f"Бот запущен. URL для вебхука Lava API: {webhook_url}")
            except Exception as e:
                logger.error(f"Error sending webhook URL to admin: {e}")
    else:
        logger.warning("Failed to set up ngrok tunnel. Webhook will not be accessible from the internet.")
    
    # Запуск FastAPI в отдельном потоке
    webhook_thread = threading.Thread(
        target=uvicorn.run,
        args=(app,),
        kwargs={"host": "0.0.0.0", "port": 8000},
        daemon=True
    )
    webhook_thread.start()
    logger.info("Webhook server started on port 8000")
    
    while True:
        try:
            logger.info("Starting bot polling...")
            bot.polling(none_stop=True, timeout=25)
        except Exception as e:
            logger.error(f"Bot stopped with an error: {e}", exc_info=True)
            # Ожидание перед перезапуском,
            # чтобы избежать частых перезапусков при постоянных ошибках
            logger.info("Waiting 5 seconds before restart")
            time.sleep(5)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)  # Обработка SIGINT
    main()
