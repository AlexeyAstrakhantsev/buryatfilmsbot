import telebot
from telebot.types import LabeledPrice
import os
from dotenv import load_dotenv
import logging
import time
import sys
import signal
import sqlite3

# Загрузка переменных окружения из файла .env
load_dotenv()
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    raise ValueError("No TELEGRAM_BOT_TOKEN provided")

PROVIDER_TOKEN = os.getenv('PROVIDER_TOKEN')
if not PROVIDER_TOKEN:
    raise ValueError("No PROVIDER_TOKEN provided")

PRIVATE_CHANNEL_ID = os.getenv('PRIVATE_CHANNEL_ID')
if not PRIVATE_CHANNEL_ID:
    raise ValueError("No PRIVATE_CHANNEL_ID provided")

CURRENCY = os.getenv('CURRENCY')
if not CURRENCY:
    CURRENCY = "RUB"
PRICE = os.getenv('PRICE')
if not PRICE:
    PRICE = 99000
TITLE = os.getenv('TITLE')
if not TITLE:
    TITLE = "Доступ к каналу BuryatFilms"
DESCRIPTION = os.getenv('DESCRIPTION')
if not DESCRIPTION:
    DESCRIPTION = "Предоставляется на 1 календарный месяц. "
DATABASE = os.getenv('DATABASE')
if not DATABASE:
    DATABASE = "database.db"


def init_db():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS "users" (
        "key"	INTEGER NOT NULL UNIQUE,
        "telegram_id"	INTEGER NOT NULL UNIQUE,
        "username"	TEXT,
        "premium"   BOOL,
        PRIMARY KEY("key" AUTOINCREMENT)
    );
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS "purchases" (
        "key"	INTEGER NOT NULL UNIQUE,
        "telegram_id"	INTEGER NOT NULL,
        "pay_date"	DATETIME,
        "pay_days"   INT,
        "pay_expire_date"	DATETIME,
        PRIMARY KEY("key" AUTOINCREMENT)
    );
    ''')
    conn.close()


init_db()
bot = telebot.TeleBot(TOKEN)


# Обработчик запросов на вступление
@bot.chat_join_request_handler()
def joinrequest(message: telebot.types.ChatJoinRequest):
    user_id = message.from_user.id
    bot.send_message(message.from_user.id, "Привет, ты подал заявку!")
    pay_message(message=message)
    logging.info(f"Request from: {message.from_user.username} (id:{user_id})")
    logging.info(message)


# Вывод счета на оплату
def pay_message(message):
    chat_id = message.from_user.id
    bot.send_message(
        chat_id,
        'Для оплаты ведите данные тестовой карты: '
        '1111 1111 1111 1026, 12/25, CVC 000.')
    logging.info('Pay request from '
                 f'{message.from_user.username} (id:{message.from_user.id})')
    logging.info(message)
    try:
        bot.send_invoice(
                    chat_id=chat_id,
                    title=TITLE,
                    description=DESCRIPTION,
                    invoice_payload='invoice',
                    provider_token=PROVIDER_TOKEN,
                    start_parameter='start',
                    currency='RUB',
                    prices=[LabeledPrice("30 дней", PRICE)])
    except Exception as e:
        bot.send_message(
            chat_id, 'Произошла ошибка при отправке запроса: '
            f'{str(e)}')


# Обработчик кнопки Pay
@bot.message_handler(func=lambda message: message.text == '✅ Оплатить доступ')
def handle_pay_massage(message):
    pay_message(message=message)


# Обработчик команды /pay
@bot.message_handler(commands=['pay'])
def handel_message_pay_answer(message):
    pay_message(message=message)


# Пречекаут (хз что это)
@bot.pre_checkout_query_handler(func=lambda query: True)
def checkout(pre_checkout_query):
    bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True,
                                  error_message="Pre_Сhekout.")


# Обработка успешного платежа
@bot.message_handler(content_types=['successful_payment'])
def got_payment(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    bot.send_message(message.chat.id,
                     'Ваш платеж на сумму `{}` принят! '
                     .format(
                         message.successful_payment.total_amount / 100),
                     parse_mode='Markdown')
    logging.info('Pay details from '
                 f'{message.from_user.username} (id:{message.from_user.id})')
    logging.info(message)
    try:
        bot.approve_chat_join_request(
            chat_id=PRIVATE_CHANNEL_ID,
            user_id=user_id)
        bot.send_message(chat_id, 'Вам предоставлен доступ к платному каналу!')
    except Exception as e:
        bot.send_message(
            chat_id, 'Произошла ошибка при отправке запроса: '
            f'{str(e)}')


# Обработчик команды /start
@bot.message_handler(commands=['start'])
def welcome(message):
    if message.chat.type == 'private':
        keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        chat_id = message.chat.id
        button_buy = telebot.types.InlineKeyboardButton(
            text="✅ Оплатить доступ",
            callback_data='pay'
            )
        keyboard.add(button_buy)
        bot.send_message(
            chat_id,
            'Приветствую! '
            'Я помогу Вам получить доступ в платный канал BuryatFilms.',
            reply_markup=keyboard)


# Прерывание скрипта пользователем
def signal_handler(sig, frame):
    logging.info("Program interrupted by user.")
    sys.exit(0)


# Основное тело скрипта
def main():
    # Настройка логирования
    logging.basicConfig(filename='bot.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
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
