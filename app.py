# Этот файл нужен для совместимости с Amvera
# Он импортирует FastAPI приложение из webhook_server.py

from webhook_server import app

# Amvera будет использовать этот файл для запуска веб-сервера
# Основной код бота запускается через main.py
