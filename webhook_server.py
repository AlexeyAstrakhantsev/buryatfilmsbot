from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import logging
from logging.handlers import RotatingFileHandler
import os
import sqlite3
from dotenv import load_dotenv
import secrets
import json
import traceback

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования для вебхуков
def setup_webhook_logging():
    # Создаем директорию для логов, если она не существует
    log_dir = 'logs'
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    # Настройка логгера для вебхуков
    webhook_logger = logging.getLogger('webhook')
    webhook_logger.setLevel(logging.DEBUG)
    
    # Форматтер для логов
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Обработчик для файла с ротацией
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, 'webhook.log'), 
        maxBytes=5*1024*1024,  # 5 МБ
        backupCount=5
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    
    # Обработчик для консоли
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    
    # Добавляем обработчики к логгеру
    webhook_logger.addHandler(file_handler)
    webhook_logger.addHandler(console_handler)
    
    return webhook_logger

webhook_logger = setup_webhook_logging()

# Получение учетных данных для аутентификации вебхука
WEBHOOK_USERNAME = os.getenv("WEBHOOK_USERNAME")
WEBHOOK_PASSWORD = os.getenv("WEBHOOK_PASSWORD")

# Путь к базе данных (для Amvera используем постоянное хранилище)
DB_PATH = os.path.join('data', os.getenv('DATABASE', 'subscribers.db'))
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# Создание FastAPI приложения
app = FastAPI()
security = HTTPBasic()

# Глобальные переменные для хранения экземпляра бота и ID канала
bot_instance = None
channel_id = None

# Функция для установки экземпляра бота
def set_bot_instance(bot, channel):
    global bot_instance, channel_id
    bot_instance = bot
    channel_id = channel
    webhook_logger.info("Bot instance and channel ID set for webhook server")

# Функция для проверки учетных данных
def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    if not WEBHOOK_USERNAME or not WEBHOOK_PASSWORD:
        webhook_logger.warning("Webhook authentication is not configured")
        return True
    
    correct_username = secrets.compare_digest(credentials.username, WEBHOOK_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, WEBHOOK_PASSWORD)
    
    if not (correct_username and correct_password):
        webhook_logger.warning(f"Authentication failed for user: {credentials.username}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    
    webhook_logger.debug(f"Authentication successful for user: {credentials.username}")
    return True

# Обработчик вебхука от Lava API
@app.post("/webhook/lava")
async def lava_webhook(request: Request, authenticated: bool = Depends(verify_credentials)):
    webhook_logger.info("Received webhook from Lava API")
    
    try:
        # Получаем данные из запроса
        data = await request.json()
        webhook_logger.debug(f"Webhook data: {json.dumps(data)}")
        
        # Проверяем наличие необходимых полей
        if 'id' not in data or 'status' not in data:
            webhook_logger.error("Invalid webhook data: missing required fields")
            return {"status": "error", "message": "Invalid data format"}
        
        payment_id = data['id']
        status = data['status']
        
        webhook_logger.info(f"Processing payment {payment_id} with status {status}")
        
        # Проверяем, что у нас есть экземпляр бота и ID канала
        if not bot_instance or not channel_id:
            webhook_logger.error("Bot instance or channel ID not set")
            return {"status": "error", "message": "Bot not initialized"}
        
        # Обрабатываем статус платежа
        if status == 'PAID':
            # Находим пользователя по ID платежа
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT telegram_id FROM subscribers WHERE payment_id = ?", (payment_id,))
            result = cursor.fetchone()
            
            if result:
                telegram_id = result[0]
                webhook_logger.info(f"Found user {telegram_id} for payment {payment_id}")
                
                # Обновляем статус подписки
                from datetime import datetime, timedelta
                expiry_date = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
                
                cursor.execute(
                    "UPDATE subscribers SET status = ?, expiry_date = ? WHERE payment_id = ?",
                    ("active", expiry_date, payment_id)
                )
                conn.commit()
                webhook_logger.debug(f"Updated subscription status for user {telegram_id}")
                
                # Добавляем пользователя в канал
                try:
                    # Создаем ссылку-приглашение
                    invite_link = bot_instance.create_chat_invite_link(
                        channel_id,
                        member_limit=1,
                        expire_date=int((datetime.now() + timedelta(days=1)).timestamp())
                    )
                    invite_url = invite_link.invite_link
                    webhook_logger.debug(f"Created invite link for user {telegram_id}")
                    
                    # Отправляем сообщение пользователю
                    bot_instance.send_message(
                        telegram_id,
                        f"Спасибо за оплату! Ваша подписка активирована до {expiry_date}.\n\n"
                        f"Для доступа к каналу используйте эту ссылку: {invite_url}\n\n"
                        f"Ссылка действительна в течение 24 часов."
                    )
                    webhook_logger.info(f"Sent invite link to user {telegram_id}")
                except Exception as e:
                    webhook_logger.error(f"Error adding user to channel: {e}")
                    webhook_logger.error(traceback.format_exc())
            else:
                webhook_logger.warning(f"User not found for payment {payment_id}")
            
            conn.close()
        elif status == 'CANCELED' or status == 'EXPIRED':
            # Обновляем статус платежа в базе данных
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("UPDATE subscribers SET status = ? WHERE payment_id = ?", (status.lower(), payment_id))
            conn.commit()
            
            # Находим пользователя по ID платежа
            cursor.execute("SELECT telegram_id FROM subscribers WHERE payment_id = ?", (payment_id,))
            result = cursor.fetchone()
            
            if result:
                telegram_id = result[0]
                webhook_logger.info(f"Payment {payment_id} for user {telegram_id} was {status.lower()}")
                
                # Отправляем сообщение пользователю
                try:
                    bot_instance.send_message(
                        telegram_id,
                        f"Ваш платеж был {status.lower()}. Для получения доступа к каналу, пожалуйста, оплатите подписку."
                    )
                    webhook_logger.debug(f"Sent payment {status.lower()} notification to user {telegram_id}")
                except Exception as e:
                    webhook_logger.error(f"Error sending notification to user: {e}")
            
            conn.close()
        
        return {"status": "success"}
    except Exception as e:
        webhook_logger.error(f"Error processing webhook: {e}")
        webhook_logger.error(traceback.format_exc())
        return {"status": "error", "message": str(e)}


# Простой эндпоинт для проверки работоспособности сервера
@app.get("/")
async def root():
    webhook_logger.debug("Health check endpoint called")
    return {"status": "Webhook server is running"} 