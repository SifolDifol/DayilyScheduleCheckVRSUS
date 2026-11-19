FROM python:3.10-slim
LABEL authors="SifolDifol"
# Устанавливаем часовой пояс (чтобы проверки с 05:00 до 18:00 работали верно)
ENV TZ=Europe/Moscow
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Рабочая директория внутри контейнера
WORKDIR /app

# Сначала копируем только requirements для кэширования
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем остальные файлы проекта
COPY main.py .
COPY templates/ ./templates/

# Открываем порт для веб-интерфейса
EXPOSE 5000

# Команда запуска
CMD ["python", "main.py"]