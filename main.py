import telebot
from telebot.types import LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton
import os
from dotenv import load_dotenv
import logging
import time
import sys
import signal
import sqlite3
import requests
import json
import threading
import uvicorn
from webhook_server import app, set_bot_instance

# Загрузка переменных окружения из файла .env
load_dotenv()
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    raise ValueError("No TELEGRAM_BOT_TOKEN provided")

PRIVATE_CHANNEL_ID = os.getenv('PRIVATE_CHANNEL_ID')
if not PRIVATE_CHANNEL_ID:
    raise ValueError("No PRIVATE_CHANNEL_ID provided")

LAVA_API_KEY = os.getenv('LAVA_API_KEY')
if not LAVA_API_KEY:
    raise ValueError("No LAVA_API_KEY provided")

LAVA_OFFER_ID = os.getenv('LAVA_OFFER_ID')
if not LAVA_OFFER_ID:
    raise ValueError("No LAVA_OFFER_ID provided")

# Инициализация бота
bot = telebot.TeleBot(TOKEN)

# Инициализация базы данных
def init_db():
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

# Функция для создания инвойса в Lava API
def create_lava_invoice(telegram_id):
    url = "https://gate.lava.top/api/v2/invoice"
    headers = {
        "Authorization": f"Bearer {LAVA_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "email": f"@t.me/{telegram_id}",
        "offerId": LAVA_OFFER_ID,
        "periodicity": "MONTHLY",
        "currency": "USD",
        "buyerLanguage": "RU",
        "paymentMethod": "BANK131",
        "clientUtm": {}
    }
    
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logging.error(f"Error creating Lava invoice: {e}")
        return None

# Обработчик команды /start
@bot.message_handler(commands=['start'])
def welcome(message):
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

# Обработчик текстовых сообщений
@bot.message_handler(content_types=['text'])
def handle_text(message):
    if message.text == "✅ Оплатить доступ":
        process_payment(message)

# Функция обработки оплаты
def process_payment(message):
    telegram_id = message.from_user.id
    invoice_data = create_lava_invoice(telegram_id)
    
    if invoice_data and 'paymentUrl' in invoice_data:
        payment_url = invoice_data['paymentUrl']
        payment_id = invoice_data.get('id', 'unknown')
        
        # Сохраняем информацию о платеже в базу данных
        conn = sqlite3.connect('subscribers.db')
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO subscribers (telegram_id, payment_id, status) VALUES (?, ?, ?)",
            (str(telegram_id), payment_id, "pending")
        )
        conn.commit()
        conn.close()
        
        # Создаем инлайн-клавиатуру с кнопкой для оплаты
        keyboard = InlineKeyboardMarkup()
        payment_button = InlineKeyboardButton(text="Перейти к оплате", url=payment_url)
        keyboard.add(payment_button)
        
        bot.send_message(
            message.chat.id,
            "Для оплаты доступа к каналу, пожалуйста, нажмите на кнопку ниже:",
            reply_markup=keyboard
        )
    else:
        bot.send_message(
            message.chat.id,
            "Извините, произошла ошибка при создании платежа. Пожалуйста, попробуйте позже."
        )

# Функция для проверки и удаления пользователей с истекшей подпиской
def check_expired_subscriptions():
    conn = sqlite3.connect('subscribers.db')
    cursor = conn.cursor()
    
    # Находим пользователей с истекшей подпиской
    cursor.execute(
        "SELECT telegram_id FROM subscribers WHERE status = 'active' AND expiry_date < datetime('now')"
    )
    expired_users = cursor.fetchall()
    
    # Обновляем статус
    cursor.execute(
        "UPDATE subscribers SET status = 'expired' WHERE status = 'active' AND expiry_date < datetime('now')"
    )
    conn.commit()
    conn.close()
    
    # Удаляем пользователей из канала
    for user in expired_users:
        telegram_id = user[0]
        try:
            bot.kick_chat_member(PRIVATE_CHANNEL_ID, telegram_id)
            bot.unban_chat_member(PRIVATE_CHANNEL_ID, telegram_id)  # Разбаниваем, чтобы пользователь мог вернуться после оплаты
            bot.send_message(
                telegram_id,
                "Ваша подписка истекла. Для продления доступа, пожалуйста, оплатите подписку снова."
            )
        except Exception as e:
            logging.error(f"Error removing user {telegram_id} from channel: {e}")

# Прерывание скрипта пользователем
def signal_handler(sig, frame):
    logging.info("Program interrupted by user.")
    sys.exit(0)

# Основное тело скрипта
def main():
    # Настройка логирования
    logging.basicConfig(filename='bot.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    
    # Инициализация базы данных
    init_db()
    
    # Настройка планировщика для проверки истекших подписок
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_expired_subscriptions, 'interval', hours=24)
    scheduler.start()
    
    # Передаем экземпляр бота в FastAPI приложение
    set_bot_instance(bot, PRIVATE_CHANNEL_ID)
    
    # Запуск FastAPI в отдельном потоке
    threading.Thread(
        target=uvicorn.run,
        args=(app,),
        kwargs={"host": "0.0.0.0", "port": 8000},
        daemon=True
    ).start()
    
    while True:
        try:
            logging.info("Starting bot...")
            bot.polling(none_stop=True, timeout=25)
        except Exception as e:
            logging.error("Bot stopped with an error: %s", e)
            # Ожидание перед перезапуском,
            # чтобы избежать частых перезапусков при постоянных ошибках
            time.sleep(5)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)  # Обработка SIGINT
    main()
