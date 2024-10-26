import asyncio
import voice
import sounddevice as sd
import vosk
import g4f
from fuzzywuzzy import fuzz
import random
import json
import queue
import webbrowser
from datetime import datetime, timedelta
import logging
import subprocess
import sys
import os

# Настройка логирования
logger = logging.getLogger('sonya_assistant')
logger.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

formatter = logging.Formatter('%(asctime)s %(levelname)s:%(message)s')
console_handler.setFormatter(formatter)

if logger.hasHandlers():
    logger.handlers.clear()

logger.addHandler(console_handler)

# Базовый контекст для ассистента
base_dialogue = [
    {
        "role": "system",
        "content": (
            "Ты интеллектуальный голосовой помощник Соня. Твоя задача помогать "
            "пользователю с решением разных задач, беседовать с ним на различные темы "
            "и управлять его делами. Важно, все цифры прописывай буквами, например не 8, а восемь. "
            "Помни, у тебя женский пол."
        )
    }
]

messages = base_dialogue.copy()

# Ограниченная очередь для аудио данных
q = queue.Queue(maxsize=20)  # Установите подходящий размер

# Инициализация модели распознавания речи
model = vosk.Model("model_small_ru")

# Получение устройства по умолчанию и частоты микрофона
device = sd.default.device
samplerate = int(sd.query_devices(device[0], "input")["default_samplerate"])

# Список будильников и напоминаний
alarms = []
reminders = []

# Функции для управления контекстом
def update_chat(messages, role, content):
    messages.append({"role": role, "content": content})
    return messages

def clear_context():
    global messages
    messages = base_dialogue.copy()
    return messages

# Функция обратного вызова для аудио данных
def audio_callback(indata, frames, time, status):
    try:
        q.put_nowait(bytes(indata))
    except queue.Full:
        logger.warning("Очередь аудио переполнена. Данные будут отброшены.")

# Функция для проверки ключевого слова
def is_wake_word(data):
    wake_words = ["соня", "сонька", "сонечка", "sonya"]
    for word in wake_words:
        ratio = fuzz.partial_ratio(data.lower(), word)
        if ratio > 80:
            return True
    return False

# Функция для управления яркостью с использованием brightnessctl
def adjust_brightness(direction):
    logger.info(f"Регулировка яркости: {direction}")
    if sys.platform.startswith("linux"):
        try:
            if direction == "up":
                subprocess.check_call(["brightnessctl", "set", "+10%"])
            else:
                subprocess.check_call(["brightnessctl", "set", "10%-"])
            response = "Яркость изменена."
            logger.info(f"Бот ответил: {response}")
            voice.bot_speak(response)
        except subprocess.CalledProcessError as e:
            error_msg = "Не удалось изменить яркость. Пожалуйста, проверьте настройки."
            logger.error(f"Ошибка при изменении яркости: {e}")
            logger.info(f"Бот ответил: {error_msg}")
            voice.bot_speak(error_msg)
    else:
        response = "Изменение яркости не поддерживается на этой системе."
        logger.info(f"Бот ответил: {response}")
        voice.bot_speak(response)

# Функция для регулировки громкости
def adjust_volume(direction):
    logger.info(f"Регулировка громкости: {direction}")
    if sys.platform.startswith("linux"):
        if direction == "up":
            os.system("amixer -D pulse sset Master 5%+")
        else:
            os.system("amixer -D pulse sset Master 5%-")
        response = "Громкость изменена."
        logger.info(f"Бот ответил: {response}")
        voice.bot_speak(response)
    else:
        response = "Изменение громкости не поддерживается на этой системе."
        logger.info(f"Бот ответил: {response}")
        voice.bot_speak(response)

# Функция для воспроизведения звукового сигнала
def play_sound():
    logger.info("Воспроизведение звукового сигнала")
    if sys.platform.startswith("linux"):
        sound_file = "/home/blacksnaker/SonyaPy/beep.mp3"
        subprocess.call(["paplay", sound_file])
    else:
        pass

# Функция для открытия приложений
def open_application(app_name):
    logger.info(f"Попытка открыть приложение: {app_name}")
    applications = {
        'калькулятор': 'gnome-calculator',
        'терминал': 'gnome-terminal',
        'блокнот': 'gedit',
        # Добавьте сюда другие приложения
    }
    app_command = applications.get(app_name.lower())
    if app_command:
        try:
            subprocess.Popen([app_command])
            response = f"Открываю {app_name}."
            logger.info(f"Бот ответил: {response}")
            voice.bot_speak(response)
        except Exception as e:
            error_msg = f"Не удалось открыть {app_name}."
            logger.error(f"Ошибка при открытии приложения: {e}")
            logger.info(f"Бот ответил: {error_msg}")
            voice.bot_speak(error_msg)
    else:
        response = f"Приложение {app_name} не найдено."
        logger.info(f"Бот ответил: {response}")
        voice.bot_speak(response)

# Новая функция для игры "Угадай число"
def guess_number():
    number = random.randint(1, 100)
    response = "Я загадала число от одного до ста. Попробуй угадать!"
    logger.info(f"Бот ответил: {response}")
    voice.bot_speak(response)

    while True:
        guess = int(input("Ваше предположение: "))  # Можно заменить на голосовой ввод
        if guess < number:
            voice.bot_speak("Моё число больше.")
        elif guess > number:
            voice.bot_speak("Моё число меньше.")
        else:
            voice.bot_speak("Поздравляю! Ты угадал число.")
            break

# Новая функция для добавления напоминаний
def add_reminder(reminder_text, minutes):
    reminder_time = datetime.now() + timedelta(minutes=minutes)
    reminders.append((reminder_time, reminder_text))
    response = f"Напоминание установлено на {minutes} минут."
    logger.info(f"Бот ответил: {response}")
    voice.bot_speak(response)

# Новая функция для приветствия пользователя
def greet_user():
    hour = datetime.now().hour
    if 5 <= hour < 12:
        greeting = "Доброе утро! Как я могу помочь тебе сегодня?"
    elif 12 <= hour < 18:
        greeting = "Добрый день! Чем могу помочь?"
    else:
        greeting = "Добрый вечер! Какие у тебя планы на вечер?"
    logger.info(f"Бот ответил: {greeting}")
    voice.bot_speak(greeting)

# Функция для обработки команд
async def process_command(command):
    command = command.lower()
    logger.info(f"Обработка команды: {command}")
    if "открой браузер" in command:
        response = "Открываю браузер"
        logger.info(f"Бот ответил: {response}")
        voice.bot_speak(response)
        webbrowser.open("https://www.google.com")
    elif "какой сегодня день" in command:
        days = ["понедельник", "вторник", "среда", "четверг",
                "пятница", "суббота", "воскресенье"]
        day_name = days[datetime.now().weekday()]
        response = f"Сегодня {day_name}"
        logger.info(f"Бот ответил: {response}")
        voice.bot_speak(response)
    elif "анекдот" in command:
        jokes = [
            "Почему программисты путают Хэллоуин и Рождество? Потому что 31 октября — это 25 декабря в шестнадцатеричной системе.",
            "Было бы смешно, если бы не было так грустно."
        ]
        joke = random.choice(jokes)
        logger.info(f"Бот ответил: {joke}")
        voice.bot_speak(joke)
    elif "включи музыку" in command:
        response = "Включаю музыку"
        logger.info(f"Бот ответил: {response}")
        voice.bot_speak(response)
        webbrowser.open("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    elif "погода" in command:
        await get_weather()
    elif "установи будильник" in command:
        await set_alarm(command)
    elif "напомни через" in command:
        reminder_text = command.split("напомни через")[-1].strip()
        minutes = int(''.join(filter(str.isdigit, reminder_text)))
        add_reminder("Напоминание", minutes)
    elif "игра угадай число" in command:
        guess_number()
    elif any(phrase in command for phrase in ["увеличь громкость", "прибавь громкость"]):
        adjust_volume("up")
    elif any(phrase in command for phrase in ["уменьши громкость", "убавь громкость"]):
        adjust_volume("down")
    elif any(phrase in command for phrase in ["увеличь яркость", "прибавь яркость"]):
        adjust_brightness("up")
    elif any(phrase in command for phrase in ["уменьши яркость", "убавь яркость"]):
        adjust_brightness("down")
    elif any(phrase in command for phrase in ["открой приложение", "запусти приложение", "открой"]):
        # Открытие приложения
        for phrase in ["открой приложение", "запусти приложение", "открой"]:
            if phrase in command:
                app_start = command.find(phrase) + len(phrase)
                app_name = command[app_start:].strip()
                break
        else:
            app_name = ""

        if app_name:
            open_application(app_name)
        else:
            response = "Пожалуйста, укажите название приложения."
            logger.info(f"Бот ответил: {response}")
            voice.bot_speak(response)
    else:
        # Используем чат-бот для ответа
        update_chat(messages, "user", command)
        try:
            logger.info("Отправка сообщения в g4f ChatCompletion")
            response = g4f.ChatCompletion.create(
                model="gpt-4o",
                messages=messages,
            )
            if isinstance(response, str):
                logger.info(f"Бот ответил: {response}")
                voice.bot_speak(response.lower())
                clear_context()  # Очищаем контекст после ответа
            else:
                error_msg = "Ошибка: непредвиденный формат ответа."
                logger.info(f"Бот ответил: {error_msg}")
                voice.bot_speak(error_msg)
                clear_context()
        except Exception as e:
            error_msg = "Произошла ошибка при получении ответа."
            logger.error(f"Ошибка при генерации ответа: {e}")
            logger.info(f"Бот ответил: {error_msg}")
            voice.bot_speak(error_msg)

# Функция для получения погоды (заглушка)
async def get_weather():
    logger.info("Запрос погоды")
    response = "Сегодня ясная погода с температурой двадцать градусов."
    logger.info(f"Бот ответил: {response}")
    voice.bot_speak(response)

# Функция для установки будильника
async def set_alarm(command):
    try:
        logger.info("Установка будильника")
        time_str = command.split("через")[1].strip()
        minutes = int(''.join(filter(str.isdigit, time_str)))
        alarm_time = datetime.now() + timedelta(minutes=minutes)
        alarms.append(alarm_time)
        response = f"Будильник установлен на {minutes} минут."
        logger.info(f"Бот ответил: {response}")
        voice.bot_speak(response)
        logger.info(f"Будильник установлен на {alarm_time}")
    except Exception as e:
        error_msg = "Не удалось установить будильник. Пожалуйста, повторите попытку."
        logger.error(f"Ошибка установки будильника: {e}")
        logger.info(f"Бот ответил: {error_msg}")
        voice.bot_speak(error_msg)

# Функция для проверки будильников и напоминаний
async def check_alarms():
    while True:
        now = datetime.now()
        for alarm_time in alarms.copy():
            if now >= alarm_time:
                response = "Сработал будильник."
                logger.info(f"Бот ответил: {response}")
                voice.bot_speak(response)
                alarms.remove(alarm_time)
                logger.info("Будильник сработал")
        for reminder_time, reminder_text in reminders.copy():
            if now >= reminder_time:
                logger.info(f"Бот ответил: {reminder_text}")
                voice.bot_speak(reminder_text)
                reminders.remove((reminder_time, reminder_text))
        await asyncio.sleep(30)

# Основная функция распознавания речи
async def recognize(data):
    logger.info(f"Пользователь сказал: {data}")
    if is_wake_word(data):
        # Воспроизводим звуковой сигнал
        play_sound()
        # Убираем ключевое слово из команды
        command = data.lower()
        for wake_word in ["соня", "сонька", "сонечка", "sonya"]:
            command = command.replace(wake_word, "")
        command = command.strip()
        if command:
            await process_command(command)
        else:
            response = "Я вас слушаю."
            logger.info(f"Бот ответил: {response}")
            voice.bot_speak(response)
    else:
        logger.info("Ключевое слово не обнаружено.")

# Функция обработки аудио данных
async def audio_loop():
    with sd.RawInputStream(
        samplerate=samplerate,
        blocksize=8000,  # Уменьшаем размер блока
        device=device[0],
        dtype="int16",
        channels=1,
        callback=audio_callback,
    ):
        rec = vosk.KaldiRecognizer(model, samplerate)
        while True:
            try:
                data = await asyncio.get_event_loop().run_in_executor(None, q.get)
                if rec.AcceptWaveform(data):
                    data_text = json.loads(rec.Result())["text"]
                    await recognize(data_text)
            except Exception as e:
                logger.error(f"Ошибка распознавания: {e}")

# Основная функция программы
async def main():
    greet_user()
    await asyncio.gather(
        audio_loop(),
        check_alarms(),
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Программа остановлена пользователем.")
