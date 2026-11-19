import requests
from bs4 import BeautifulSoup
from urllib.parse import quote
from datetime import datetime, timedelta
import json
import time
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from email.utils import formataddr
import threading
from flask import Flask, render_template, request

app = Flask(__name__)

# ==========================================
# ⚙️ УПРАВЛЕНИЕ НАСТРОЙКАМИ
# ==========================================
CONFIG_FILE = "config.json"
FILE_STATE = "last_schedule.json"

# Блокировка для предотвращения одновременного доступа к файлам из разных потоков
state_lock = threading.Lock()

DEFAULT_CONFIG = {
    "teacher_name": "Алексеева Наталья Дмитриевна",
    "period_days": 7,
    "smtp_server": "smtp.mail.ru",
    "smtp_port": 465,
    "email_sender": "",
    "email_password": "",
    "email_receiver": "",
    "web_host": "0.0.0.0",
    "web_port": 5000,
    "force_check": False
}


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            # Если файл битый или это папка, возвращаем дефолт
            return DEFAULT_CONFIG.copy()

    # Если файла нет — создаем его с настройками по умолчанию
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=4)
    return DEFAULT_CONFIG.copy()


APP_CONFIG = load_config()


def save_config():
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(APP_CONFIG, f, ensure_ascii=False, indent=4)


# ==========================================
# 🛠️ ЛОГИКА ПАРСИНГА И ПОЧТЫ
# ==========================================
def fetch_schedule(teacher_name, start_date_str, end_date_str):
    encoded_name = quote(teacher_name)
    url = f"https://lk.tolgas.ru/public-schedule/teacher?name={encoded_name}&date={start_date_str}&dateTo={end_date_str}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        schedule_container = soup.find('div', id='printable-schedule')
        if not schedule_container: return []

        schedule_data = []
        current_date = ""
        current_day = ""

        for element in schedule_container.find_all('div', recursive=False):
            classes = element.get('class', [])
            if 'date-bar' in classes:
                current_date = element.find('span').get_text(strip=True)
                current_day = element.find('h4', class_='week').get_text(strip=True)
            elif 'lesson-item' in classes:
                number_div = element.find('div', class_='lesson-number')
                lesson_number = number_div.contents[0].strip() if number_div and number_div.contents else "?"
                time_tag = number_div.find('div', class_='lesson-time') if number_div else None
                time_str = time_tag.get_text(strip=True) if time_tag else "?"
                subject_tag = element.find('div', class_='lesson-title')
                subject = subject_tag.get_text(strip=True) if subject_tag else "Не указан"
                room_tag = element.find('span', class_='lesson-auditorium')
                room = room_tag.get_text(strip=True) if room_tag else "Не указана"
                type_tag = element.find('div', class_='lesson-type')
                lesson_type = type_tag.get_text(strip=True) if type_tag else "Не указан"
                details_div = element.find('div', class_='lesson-details')
                groups = []
                if details_div:
                    for text in details_div.stripped_strings:
                        if text != room: groups.append(text)

                schedule_data.append({
                    "date": current_date, "day_of_week": current_day,
                    "lesson_number": lesson_number, "time": time_str,
                    "subject": subject, "room": room, "groups": groups, "lesson_type": lesson_type
                })
        return schedule_data
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M')}] Ошибка сети: {e}")
        return None


def generate_html_schedule(schedule):
    if not schedule: return "<p>В этот период пар нет.</p>"
    html = "<h2>Расписание изменилось!</h2>"
    last_date = ""
    for lesson in schedule:
        if lesson['date'] != last_date:
            if last_date != "": html += "</tbody></table><br>"
            last_date = lesson['date']
            html += f"<h3 style='color: #2c3e50;'>📅 {lesson['date']} ({lesson['day_of_week']})</h3>"
            html += "<table style='border-collapse: collapse; width: 100%; font-family: Arial;'><thead style='background: #f8f9fa;'><tr>"
            html += "<th style='border: 1px solid #ddd; padding: 8px;'>Пара</th><th style='border: 1px solid #ddd; padding: 8px;'>Время</th>"
            html += "<th style='border: 1px solid #ddd; padding: 8px;'>Предмет</th><th style='border: 1px solid #ddd; padding: 8px;'>Ауд.</th></tr></thead><tbody>"
        groups_str = ", ".join(lesson['groups']) if lesson['groups'] else "—"
        html += f"<tr><td style='border: 1px solid #ddd; padding: 8px;'>{lesson['lesson_number']}</td>"
        html += f"<td style='border: 1px solid #ddd; padding: 8px;'>{lesson['time']}</td>"
        html += f"<td style='border: 1px solid #ddd; padding: 8px;'><b>{lesson['subject']}</b><br><small>{lesson['lesson_type']} | {groups_str}</small></td>"
        html += f"<td style='border: 1px solid #ddd; padding: 8px;'>{lesson['room']}</td></tr>"
    if last_date != "": html += "</tbody></table>"
    return html


def send_email_notification(schedule_data):
    sender = APP_CONFIG.get('email_sender')
    password = APP_CONFIG.get('email_password')
    receiver = APP_CONFIG.get('email_receiver')
    smtp_server = APP_CONFIG.get('smtp_server')
    smtp_port = int(APP_CONFIG.get('smtp_port', 465))

    if not sender or not password or not receiver:
        print("   ⚠️ Ошибка: Не заполнены настройки почты!")
        return

    try:
        msg = MIMEMultipart()
        msg['From'] = formataddr((str(Header('Монитор расписания', 'utf-8')), sender))
        msg['To'] = formataddr((str(Header('Получатель', 'utf-8')), receiver))
        msg['Subject'] = Header(f"🔔 Изменилось расписание: {APP_CONFIG['teacher_name']}", 'utf-8')

        msg.attach(MIMEText(generate_html_schedule(schedule_data), 'html', 'utf-8'))

        with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
            server.login(sender, password)
            server.sendmail(sender, [receiver], msg.as_string())

        print(f"   📧 Письмо отправлено на {receiver}!")
    except Exception as e:
        print(f"   ❌ Ошибка отправки: {e}")


# ==========================================
# 🕒 ФОНОВАЯ ПРОВЕРКА
# ==========================================
def check_for_updates(force_email=False):
    teacher = APP_CONFIG['teacher_name']
    days = APP_CONFIG['period_days']

    print(f"\n[{datetime.now().strftime('%d.%m.%Y %H:%M')}] ⏳ Проверка: {teacher} ({days} дн.)...")

    today = datetime.now()
    start_date = today.strftime('%Y-%m-%d')
    end_date = (today + timedelta(days=days - 1)).strftime('%Y-%m-%d')

    new_schedule = fetch_schedule(teacher, start_date, end_date)
    if new_schedule is None: return

    new_schedule_str = json.dumps(new_schedule, ensure_ascii=False, sort_keys=True, indent=4)

    # Блокируем доступ к файлу на время проверки
    with state_lock:
        if os.path.exists(FILE_STATE):
            with open(FILE_STATE, "r", encoding="utf-8") as f:
                old_schedule_str = f.read()

            if old_schedule_str != new_schedule_str:
                print("\n🔔 РАСПИСАНИЕ ИЗМЕНИЛОСЬ! Отправляем email...")
                send_email_notification(new_schedule)
                with open(FILE_STATE, "w", encoding="utf-8") as f:
                    f.write(new_schedule_str)
            elif force_email:
                print("\n📧 Принудительная отправка (смена настроек)...")
                send_email_notification(new_schedule)
            else:
                print("   ✅ Изменений нет.")
        else:
            # Если файла нет (самый первый запуск за всю историю)
            print("\n🆕 Первый запуск. Сохраняем базу и отправляем на почту...")
            send_email_notification(new_schedule)
            with open(FILE_STATE, "w", encoding="utf-8") as f:
                f.write(new_schedule_str)


def background_monitor():
    print("🚀 Фоновый монитор запущен.")
    check_for_updates(force_email=True)

    last_checked_hour = datetime.now().hour

    while True:
        now = datetime.now()
        current_hour = now.hour

        # Проверяем, не нажал ли пользователь кнопку "Сохранить"
        if APP_CONFIG.get('force_check', False):
            with state_lock:  # Блокируем изменение флага
                APP_CONFIG['force_check'] = False
                save_config()

            check_for_updates(force_email=True)
            last_checked_hour = current_hour

        if 5 <= current_hour < 18:
            if current_hour != last_checked_hour:
                check_for_updates(force_email=False)
                last_checked_hour = current_hour
        else:
            last_checked_hour = current_hour

        time.sleep(10)


# ==========================================
# 🌐 ВЕБ-ИНТЕРФЕЙС
# ==========================================
@app.route('/')
def index():
    return render_template('index.html', config=APP_CONFIG)


@app.route('/update', methods=['POST'])
def update():
    APP_CONFIG['teacher_name'] = request.form['teacher']
    APP_CONFIG['period_days'] = int(request.form['period'])

    APP_CONFIG['smtp_server'] = request.form['smtp_server']
    APP_CONFIG['smtp_port'] = int(request.form['smtp_port'])
    APP_CONFIG['email_sender'] = request.form['email_sender']
    APP_CONFIG['email_password'] = request.form['email_password']
    APP_CONFIG['email_receiver'] = request.form['email_receiver']
    APP_CONFIG['web_host'] = request.form['web_host']
    APP_CONFIG['web_port'] = int(request.form['web_port'])

    APP_CONFIG['force_check'] = True
    save_config()

    message = "Настройки сохранены! Запущена внеплановая проверка. ВНИМАНИЕ: Если вы меняли порт, перезапустите скрипт/контейнер для применения."
    return render_template('index.html', config=APP_CONFIG, message=message)


if __name__ == '__main__':
    monitor_thread = threading.Thread(target=background_monitor, daemon=True)
    monitor_thread.start()

    # БЕРЕМ ХОСТ И ПОРТ ИЗ НАСТРОЕК
    host = APP_CONFIG.get('web_host', '0.0.0.0')
    port = int(APP_CONFIG.get('web_port', 5000))

    print(f"🌐 Веб-интерфейс доступен по адресу: http://127.0.0.1:{port}")
    app.run(debug=False, host=host, port=port)