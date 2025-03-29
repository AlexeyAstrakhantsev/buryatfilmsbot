from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import logging
import sqlite3
import os
from dotenv import load_dotenv
import secrets

# Загрузка переменных окружения
load_dotenv()
WEBHOOK_USERNAME = os.getenv('WEBHOOK_USERNAME')
WEBHOOK_PASSWORD = os.getenv('WEBHOOK_PASSWORD')

app = FastAPI()
security = HTTPBasic()

# Глобальные переменные для хранения экземпляра бота и ID канала
bot_instance = None
channel_id = None

def set_bot_instance(bot, channel):
    """Устанавливает экземпляр бота и ID канала для использования в вебхуках"""
    global bot_instance, channel_id
    bot_instance = bot
    channel_id = channel


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    """Проверка Basic авторизации"""
    if not WEBHOOK_USERNAME or not WEBHOOK_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server authentication not configured"
        )
    
    correct_username = secrets.compare_digest(credentials.username, WEBHOOK_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, WEBHOOK_PASSWORD)
    
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return True


# Обработчик вебхуков от Lava API
@app.post("/webhook/lava")
async def lava_webhook(request: Request, authenticated: bool = Depends(verify_credentials)):
    global bot_instance, channel_id
    
    if not bot_instance or not channel_id:
        return {"status": "error", "message": "Bot instance not initialized"}
    
    data = await request.json()
    logging.info(f"Received webhook data: {data}")
    
    try:
        event_type = data.get('eventType')
        status = data.get('status')
        contract_id = data.get('contractId')
        buyer_email = data.get('buyer', {}).get('email', '')
        
        # Извлекаем telegram_id из email (который содержит @t.me/ID)
        telegram_id = None
        if '@t.me/' in buyer_email:
            telegram_id = buyer_email.split('@t.me/')[1]
        
        # Обработка успешного платежа или продления подписки
        if status == 'subscription-active' and (
            event_type == 'payment.success' or 
            event_type == 'subscription.recurring.payment.success'
        ):
            # Обновляем статус платежа в базе данных
            conn = sqlite3.connect('subscribers.db')
            cursor = conn.cursor()
            
            # Проверяем, существует ли пользователь в базе
            if telegram_id:
                cursor.execute(
                    "SELECT * FROM subscribers WHERE telegram_id = ?", 
                    (telegram_id,)
                )
                user_exists = cursor.fetchone()
                
                if user_exists:
                    # Обновляем существующего пользователя
                    cursor.execute(
                        "UPDATE subscribers SET payment_id = ?, status = 'active', "
                        "expiry_date = datetime('now', '+30 days') WHERE telegram_id = ?",
                        (contract_id, telegram_id)
                    )
                else:
                    # Создаем нового пользователя
                    cursor.execute(
                        "INSERT INTO subscribers (telegram_id, payment_id, status, expiry_date) "
                        "VALUES (?, ?, 'active', datetime('now', '+30 days'))",
                        (telegram_id, contract_id)
                    )
                
                conn.commit()
                
                # Отправляем пользователю ссылку на канал
                try:
                    invite_link = bot_instance.create_chat_invite_link(
                        channel_id, 
                        member_limit=1
                    ).invite_link
                    
                    message_text = "Спасибо за оплату! Вот ваша ссылка для доступа к каналу: " + invite_link
                    if event_type == 'subscription.recurring.payment.success':
                        message_text = "Ваша подписка успешно продлена! Вот ссылка для доступа к каналу: " + invite_link
                    
                    bot_instance.send_message(
                        telegram_id,
                        message_text
                    )
                except Exception as e:
                    logging.error(f"Error sending invite link to user {telegram_id}: {e}")
            
            conn.close()
        
        # Обработка неудачного платежа
        elif status == 'subscription-failed' and (
            event_type == 'payment.failed' or 
            event_type == 'subscription.recurring.payment.failed'
        ):
            error_message = data.get('errorMessage', 'Неизвестная ошибка')
            
            if telegram_id:
                # Если это ошибка продления, нужно обновить статус в базе
                if event_type == 'subscription.recurring.payment.failed':
                    conn = sqlite3.connect('subscribers.db')
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE subscribers SET status = 'expired' WHERE telegram_id = ?",
                        (telegram_id,)
                    )
                    conn.commit()
                    conn.close()
                    
                    # Удаляем пользователя из канала
                    try:
                        bot_instance.kick_chat_member(channel_id, telegram_id)
                        bot_instance.unban_chat_member(channel_id, telegram_id)
                    except Exception as e:
                        logging.error(f"Error removing user {telegram_id} from channel: {e}")
                
                # Отправляем сообщение о неудачном платеже
                try:
                    message_text = f"Произошла ошибка при оплате: {error_message}. Пожалуйста, попробуйте снова."
                    if event_type == 'subscription.recurring.payment.failed':
                        message_text = f"Не удалось продлить вашу подписку: {error_message}. Ваш доступ к каналу приостановлен. Пожалуйста, оплатите подписку снова."
                    
                    bot_instance.send_message(
                        telegram_id,
                        message_text
                    )
                except Exception as e:
                    logging.error(f"Error sending message to user {telegram_id}: {e}")
        
        return {"status": "success"}
    except Exception as e:
        logging.error(f"Error processing webhook: {e}")
        return {"status": "error", "message": str(e)}


# Простой эндпоинт для проверки работоспособности сервера
@app.get("/")
async def root():
    return {"status": "Webhook server is running"} 