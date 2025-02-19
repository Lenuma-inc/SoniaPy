import sys
import asyncio
import voice  # Ваш модуль для TTS или звукового вывода
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
import os

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QMessageBox, QScrollArea, QSizePolicy,
    QSpacerItem, QFrame, QMenu, QMenuBar, QStatusBar, QPlainTextEdit
)
from PyQt6.QtGui import QFont, QColor, QPalette, QIcon, QPixmap, QAction
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QSize, QTimer,
    QPropertyAnimation, QEasingCurve
)

# --- Логирование ---
logger = logging.getLogger('sonya_assistant_gui')
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s %(levelname)s:%(message)s')
console_handler.setFormatter(formatter)
if logger.hasHandlers():
    logger.handlers.clear()
logger.addHandler(console_handler)

# --- Базовый контекст для GPT ---
base_dialogue = [
    {
        "role": "system",
        "content": (
            "Ты интеллектуальный голосовой помощник Соня. Твоя задача помогать "
            "пользователю с решением разных задач, беседовать с ним на различные темы "
            "и управлять его делами. Важно, все цифры прописывай буквами, например не 8, а восемь. "
            "Помни, у тебя женский пол. Обязательно запоминай контекст разговора"
        )
    }
]

# --- Списки будильников и напоминаний ---
alarms = []
reminders = []

# --- Очередь для аудиоданных (Vosk) ---
q = queue.Queue(maxsize=20)

# Инициализация модели Vosk (укажите путь к вашей модели)
model = vosk.Model("model_small_ru")  
device = sd.default.device
samplerate = int(sd.query_devices(device[0], "input")["default_samplerate"])


class AssistantThread(QThread):
    """
    Поток ассистента, который:
      - Постоянно слушает микрофон (audio_loop),
      - Проверяет будильники / напоминания (check_alarms),
      - Обрабатывает команды (process_command).
    """
    update_chat_signal = pyqtSignal(str, str)  # (sender, message)
    notify_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.tasks = []
        self.messages = base_dialogue.copy()
        self.mute_voice = False  # Если True, бот не озвучивает ответы

    def run(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self.main())
        except asyncio.CancelledError:
            pass
        finally:
            self.loop.close()

    async def main(self):
        self._greet_user()
        audio_task = asyncio.create_task(self._audio_loop())
        alarms_task = asyncio.create_task(self._check_alarms())
        self.tasks = [audio_task, alarms_task]
        try:
            await asyncio.gather(*self.tasks)
        except asyncio.CancelledError:
            for task in self.tasks:
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            raise

    def stop(self):
        for task in self.tasks:
            task.cancel()
        self.loop.call_soon_threadsafe(self.loop.stop)

    def send_command(self, command: str):
        """
        Вызывается из MainWindow, чтобы передать команду ассистенту.
        """
        self.update_chat_signal.emit("user", command)
        asyncio.run_coroutine_threadsafe(self._process_command(command), self.loop)

    # --- Основные корутины ---

    async def _audio_loop(self):
        with sd.RawInputStream(
            samplerate=samplerate,
            blocksize=8000,
            device=device[0],
            dtype="int16",
            channels=1,
            callback=self._audio_callback,
        ):
            rec = vosk.KaldiRecognizer(model, samplerate)
            while True:
                try:
                    data = await asyncio.get_event_loop().run_in_executor(None, q.get)
                    if rec.AcceptWaveform(data):
                        data_text = json.loads(rec.Result())["text"]
                        await self._recognize(data_text)
                except asyncio.CancelledError:
                    logger.info("audio_loop отменена")
                    break
                except Exception as e:
                    logger.error(f"Ошибка распознавания: {e}")

    async def _recognize(self, data: str):
        logger.info(f"Пользователь сказал: {data}")
        if self._is_wake_word(data):
            self._play_sound()
            command = data.lower()
            for wake_word in ["соня", "сонька", "сонечка", "sonya"]:
                command = command.replace(wake_word, "")
            command = command.strip()
            if command:
                await self._process_command(command)
                self.notify_signal.emit("Соня: Выполнил команду.")
            else:
                response = "Я вас слушаю."
                self._speak(response)
                self.update_chat_signal.emit("sonya", response)
                self.notify_signal.emit(response)

    def _is_wake_word(self, data: str) -> bool:
        # Проверяем, есть ли «соня» (и т.п.) в распознанном тексте
        wake_words = ["соня", "сонька", "сонечка", "sonya"]
        for word in wake_words:
            ratio = fuzz.partial_ratio(data.lower(), word)
            if ratio > 80:
                return True
        return False

    async def _check_alarms(self):
        try:
            while True:
                now = datetime.now()
                for alarm_time in alarms.copy():
                    if now >= alarm_time:
                        response = "Сработал будильник."
                        self._speak(response)
                        self.notify_signal.emit(response)
                        alarms.remove(alarm_time)
                for reminder_time, reminder_text in reminders.copy():
                    if now >= reminder_time:
                        self._speak(reminder_text)
                        self.notify_signal.emit(reminder_text)
                        reminders.remove((reminder_time, reminder_text))
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            logger.info("check_alarms отменена")

    async def _process_command(self, command: str):
        command = command.lower()
        logger.info(f"Обработка команды: {command}")

        if "открой браузер" in command:
            response = "Открываю браузер"
            self._speak(response)
            self.update_chat_signal.emit("sonya", response)
            webbrowser.open("https://www.google.com")

        # Добавляйте здесь свои «известные» команды...

        else:
            # Любой другой запрос — отправляем в ChatCompletion
            self._update_chat("user", command)
            try:
                response = g4f.ChatCompletion.create(
                    model="gpt-4o",
                    messages=self.messages,
                )
                if isinstance(response, str):
                    self._speak(response.lower())
                    self.update_chat_signal.emit("sonya", response)
                    self._clear_context()
                else:
                    error_msg = "Ошибка: непредвиденный формат ответа."
                    self._speak(error_msg)
                    self.update_chat_signal.emit("sonya", error_msg)
                    self._clear_context()
            except Exception as e:
                error_msg = "Произошла ошибка при получении ответа."
                logger.error(f"Ошибка при генерации ответа: {e}")
                self._speak(error_msg)
                self.update_chat_signal.emit("sonya", error_msg)

    # --- Вспомогательные методы ---
    def _audio_callback(self, indata, frames, time, status):
        try:
            q.put_nowait(bytes(indata))
        except queue.Full:
            logger.warning("Очередь аудио переполнена.")

    def _update_chat(self, role: str, content: str):
        self.messages.append({"role": role, "content": content})

    def _clear_context(self):
        self.messages = base_dialogue.copy()

    def _speak(self, text: str):
        if not self.mute_voice:
            voice.bot_speak(text)

    def _play_sound(self):
        if sys.platform.startswith("linux"):
            sound_file = "beep.mp3"
            if os.path.exists(sound_file):
                subprocess.call(["paplay", sound_file])

    def _greet_user(self):
        hour = datetime.now().hour
        if 5 <= hour < 12:
            greeting = "Доброе утро! Как я могу помочь тебе сегодня?"
        elif 12 <= hour < 18:
            greeting = "Добрый день! Чем могу помочь?"
        else:
            greeting = "Добрый вечер! Какие у тебя планы на вечер?"
        self._speak(greeting)
        self.update_chat_signal.emit("sonya", greeting)


class MessageBubble(QWidget):
    """
    Виджет-«пузырёк» для одного сообщения (от пользователя или бота).
    """
    def __init__(self, message, sender, avatar_path=None):
        super().__init__()
        self.sender = sender
        self.message = message
        self.avatar_path = avatar_path
        self.init_ui()
        self.animate_appearance()

    def init_ui(self):
        layout = QHBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        timestamp = QLabel(datetime.now().strftime("%H:%M"))
        timestamp.setFont(QFont("Arial", 8))
        timestamp.setStyleSheet("color: gray;")

        if self.sender == "user":
            # Пользовательский вариант (справа)
            if self.avatar_path and os.path.exists(self.avatar_path):
                avatar = QLabel()
                pixmap = QPixmap(self.avatar_path)
                pixmap = pixmap.scaled(
                    50, 50,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                avatar.setPixmap(pixmap)
                avatar.setFixedSize(50, 50)
                layout.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignRight)

            bubble = QLabel(self.message)
            bubble.setWordWrap(True)
            bubble.setStyleSheet("""
                QLabel {
                    background-color: #7289DA;
                    color: white;
                    padding: 10px;
                    border-radius: 15px;
                    font-size: 14px;
                    max-width: 300px;
                }
            """)

            bubble_layout = QVBoxLayout()
            bubble_layout.addWidget(bubble)
            bubble_layout.addWidget(timestamp, alignment=Qt.AlignmentFlag.AlignRight)

            container = QWidget()
            container.setLayout(bubble_layout)
            layout.addWidget(container, alignment=Qt.AlignmentFlag.AlignRight)

        else:
            # Бот (слева)
            if self.avatar_path and os.path.exists(self.avatar_path):
                avatar = QLabel()
                pixmap = QPixmap(self.avatar_path)
                pixmap = pixmap.scaled(
                    50, 50,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                avatar.setPixmap(pixmap)
                avatar.setFixedSize(50, 50)
                layout.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignLeft)

            bubble = QLabel(self.message)
            bubble.setWordWrap(True)
            bubble.setStyleSheet("""
                QLabel {
                    background-color: #99AAB5;
                    color: white;
                    padding: 10px;
                    border-radius: 15px;
                    font-size: 14px;
                    max-width: 300px;
                }
            """)

            bubble_layout = QVBoxLayout()
            bubble_layout.addWidget(bubble)
            bubble_layout.addWidget(timestamp, alignment=Qt.AlignmentFlag.AlignLeft)

            container = QWidget()
            container.setLayout(bubble_layout)
            layout.addWidget(container, alignment=Qt.AlignmentFlag.AlignLeft)

        self.setLayout(layout)

    def animate_appearance(self):
        self.setWindowOpacity(0)
        self.animation = QPropertyAnimation(self, b"windowOpacity")
        self.animation.setDuration(500)
        self.animation.setStartValue(0)
        self.animation.setEndValue(1)
        self.animation.setEasingCurve(QEasingCurve.Type.OutBounce)
        self.animation.start()


class SimplifiedWindow(QWidget):
    """
    Упрощённое окно, плавающее в правом нижнем углу.
    Перетаскивается мышью, содержит «миниконсоль» (QPlainTextEdit) и поле ввода команды.
    """
    send_command_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        # Безрамочное окно, поверх других
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # Размер (по вкусу)
        self.setFixedSize(300, 220)

        # Для «драга»
        self._dragging = False
        self._dragPos = None

        # Основной лейаут
        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(5, 5, 5, 5)
        self.main_layout.setSpacing(5)
        self.setLayout(self.main_layout)

        # История сообщений
        self.text_display = QPlainTextEdit()
        self.text_display.setReadOnly(True)
        self.text_display.setStyleSheet("""
            QPlainTextEdit {
                background-color: rgba(35, 35, 35, 150);
                color: #FFFFFF;
                font-size: 13px;
                border: 2px solid #2C2F33;
                border-radius: 5px;
            }
        """)
        self.text_display.setFixedHeight(140)
        self.main_layout.addWidget(self.text_display)

        # Поле ввода команды + кнопка отправки
        self.input_layout = QHBoxLayout()
        self.input_layout.setContentsMargins(0, 0, 0, 0)
        self.input_layout.setSpacing(5)

        self.command_input = QLineEdit()
        self.command_input.setPlaceholderText("Введите команду...")
        self.command_input.setStyleSheet("""
            QLineEdit {
                background-color: rgba(35, 35, 35, 150);
                color: #FFFFFF;
                font-size: 14px;
                padding: 6px;
                border: 2px solid #2C2F33;
                border-radius: 25px;
            }
            QLineEdit:focus {
                border: 2px solid #7289DA;
            }
        """)
        self.command_input.returnPressed.connect(self.send_command)
        self.input_layout.addWidget(self.command_input)

        self.send_button = QPushButton()
        self.send_button.setIcon(QIcon("icons/send.png"))
        self.send_button.setIconSize(QSize(20, 20))
        self.send_button.setFixedSize(40, 40)
        self.send_button.setStyleSheet(button_style())
        self.send_button.clicked.connect(self.send_command)
        self.input_layout.addWidget(self.send_button)

        self.main_layout.addLayout(self.input_layout)

    def send_command(self):
        text = self.command_input.text().strip()
        if text:
            self.send_command_signal.emit(text)
            # Отобразим введённое сообщение у себя
            self.add_message("user", text)
            self.command_input.clear()

    def add_message(self, sender: str, message: str):
        if sender == "user":
            prefix = "Вы: "
        else:
            prefix = "Соня: "
        self.text_display.appendPlainText(f"{prefix}{message}")
        self.text_display.verticalScrollBar().setValue(
            self.text_display.verticalScrollBar().maximum()
        )

    # --- Методы для перетаскивания окна ---
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._dragPos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and event.buttons() == Qt.MouseButton.LeftButton:
            newPos = event.globalPosition().toPoint() - self._dragPos
            self.move(newPos)
            event.accept()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
        super().mouseReleaseEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.is_simplified = False

        self.setWindowTitle("Соня - Голосовой Помощник")
        self.setGeometry(100, 100, 1000, 700)
        self.setWindowIcon(QIcon("app_icon.png"))

        self.setup_ui()
        self.show()

        self.assistant_thread = AssistantThread()
        self.assistant_thread.update_chat_signal.connect(self.update_chat)
        self.assistant_thread.notify_signal.connect(self.notify)
        self.assistant_thread.start()

        # Создаём упрощённое окно (скрыто, покажется при включении режима)
        self.simplified_window = SimplifiedWindow()
        self.simplified_window.send_command_signal.connect(self.assistant_thread.send_command)

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(10, 10, 10, 10)
        self.main_layout.setSpacing(10)

        # Верхняя панель
        self.top_bar = QFrame()
        self.top_bar.setFixedHeight(60)
        self.top_bar.setStyleSheet("""
            QFrame {
                background-color: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 #2C2F33, stop:1 #23272A
                );
                border-radius: 10px;
            }
        """)
        top_layout = QHBoxLayout()
        top_layout.setContentsMargins(25, 0, 25, 0)

        logo = QLabel()
        logo_pixmap = QPixmap("logo.png")
        if logo_pixmap.isNull():
            logo.setText("🔮")
            logo.setFont(QFont("Segoe UI", 28))
            logo.setStyleSheet("color: #7289DA;")
        else:
            logo_pixmap = logo_pixmap.scaled(60, 60, Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation)
            logo.setPixmap(logo_pixmap)
            logo.setFixedSize(60, 60)
        top_layout.addWidget(logo)

        title = QLabel("Соня - Голосовой Помощник")
        title.setFont(QFont("Segoe UI", 24, QFont.Weight.Bold))
        title.setStyleSheet("color: #FFFFFF;")
        top_layout.addWidget(title)

        spacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        top_layout.addItem(spacer)

        self.theme_toggle_btn = QPushButton()
        self.theme_toggle_btn.setIcon(QIcon("icons/light_mode.png"))
        self.theme_toggle_btn.setIconSize(QSize(24, 24))
        self.theme_toggle_btn.setFixedSize(50, 50)
        self.theme_toggle_btn.setStyleSheet(button_style())
        self.theme_toggle_btn.clicked.connect(self.toggle_theme)
        top_layout.addWidget(self.theme_toggle_btn)

        self.simplify_btn = QPushButton()
        self.simplify_btn.setIcon(QIcon("icons/simplify.png"))
        self.simplify_btn.setIconSize(QSize(24, 24))
        self.simplify_btn.setFixedSize(50, 50)
        self.simplify_btn.setStyleSheet(button_style())
        self.simplify_btn.clicked.connect(self.toggle_simplified_mode)
        top_layout.addWidget(self.simplify_btn)

        self.top_bar.setLayout(top_layout)
        self.main_layout.addWidget(self.top_bar)

        # Чат с прокруткой
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                background-color: #2C2F33;
                border: 2px solid #23272A;
                border-radius: 10px;
            }
        """)
        self.chat_widget = QWidget()
        self.chat_layout = QVBoxLayout()
        self.chat_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.chat_layout.setSpacing(10)
        self.chat_widget.setLayout(self.chat_layout)

        spacer_chat = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        self.chat_layout.addItem(spacer_chat)
        self.scroll_area.setWidget(self.chat_widget)
        self.main_layout.addWidget(self.scroll_area)

        # Поле ввода команды
        self.input_widget = QWidget()
        self.input_layout = QHBoxLayout()
        self.input_layout.setSpacing(10)

        self.command_input = QLineEdit()
        self.command_input.setPlaceholderText("Введите команду...")
        self.command_input.setFont(QFont("Segoe UI", 14))
        self.command_input.setStyleSheet("""
            QLineEdit {
                background-color: #23272A;
                color: #FFFFFF;
                font-size: 16px;
                padding: 12px 15px;
                border: 2px solid #2C2F33;
                border-radius: 25px;
            }
            QLineEdit:focus {
                border: 2px solid #7289DA;
            }
        """)
        self.command_input.returnPressed.connect(self.handle_command)
        self.input_layout.addWidget(self.command_input)

        self.send_button = QPushButton()
        self.send_button.setIcon(QIcon("icons/send.png"))
        self.send_button.setIconSize(QSize(24, 24))
        self.send_button.setFixedSize(50, 50)
        self.send_button.setStyleSheet(button_style())
        self.send_button.clicked.connect(self.handle_command)
        self.input_layout.addWidget(self.send_button)

        self.input_widget.setLayout(self.input_layout)
        self.main_layout.addWidget(self.input_widget)

        # Кнопки управления (пример)
        self.buttons_widget = QWidget()
        self.buttons_layout = QHBoxLayout()
        self.buttons_layout.setSpacing(10)

        self.brightness_up_btn = QPushButton()
        self.brightness_up_btn.setIcon(QIcon("icons/brightness_up.png"))
        self.brightness_up_btn.setIconSize(QSize(24, 24))
        self.brightness_up_btn.setFixedSize(50, 50)
        self.brightness_up_btn.setStyleSheet(button_style())
        self.brightness_up_btn.clicked.connect(lambda: self.assistant_thread.send_command("увеличь яркость"))
        self.buttons_layout.addWidget(self.brightness_up_btn)

        self.brightness_down_btn = QPushButton()
        self.brightness_down_btn.setIcon(QIcon("icons/brightness_down.png"))
        self.brightness_down_btn.setIconSize(QSize(24, 24))
        self.brightness_down_btn.setFixedSize(50, 50)
        self.brightness_down_btn.setStyleSheet(button_style())
        self.brightness_down_btn.clicked.connect(lambda: self.assistant_thread.send_command("уменьши яркость"))
        self.buttons_layout.addWidget(self.brightness_down_btn)

        self.volume_up_btn = QPushButton()
        self.volume_up_btn.setIcon(QIcon("icons/volume_up.png"))
        self.volume_up_btn.setIconSize(QSize(24, 24))
        self.volume_up_btn.setFixedSize(50, 50)
        self.volume_up_btn.setStyleSheet(button_style())
        self.volume_up_btn.clicked.connect(lambda: self.assistant_thread.send_command("увеличь громкость"))
        self.buttons_layout.addWidget(self.volume_up_btn)

        self.volume_down_btn = QPushButton()
        self.volume_down_btn.setIcon(QIcon("icons/volume_down.png"))
        self.volume_down_btn.setIconSize(QSize(24, 24))
        self.volume_down_btn.setFixedSize(50, 50)
        self.volume_down_btn.setStyleSheet(button_style())
        self.volume_down_btn.clicked.connect(lambda: self.assistant_thread.send_command("уменьши громкость"))
        self.buttons_layout.addWidget(self.volume_down_btn)

        self.buttons_widget.setLayout(self.buttons_layout)
        self.main_layout.addWidget(self.buttons_widget)

        central_widget.setLayout(self.main_layout)

        # Статус-бар
        self.status_bar = QStatusBar()
        self.status_bar.setStyleSheet("""
            QStatusBar {
                background-color: #23272A;
                color: #FFFFFF;
            }
        """)
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Готово")

        # Меню
        menubar = self.menuBar()
        menubar.setStyleSheet("""
            QMenuBar {
                background-color: #23272A;
                color: #FFFFFF;
                font-size: 14px;
            }
            QMenuBar::item {
                background: transparent;
                padding: 5px 15px;
            }
            QMenuBar::item:selected {
                background-color: #2C2F33;
            }
            QMenu {
                background-color: #23272A;
                color: #FFFFFF;
                font-size: 14px;
            }
            QMenu::item:selected {
                background-color: #2C2F33;
            }
        """)

        file_menu = menubar.addMenu("Файл")
        exit_action = QAction("Выход", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        help_menu = menubar.addMenu("Помощь")
        about_action = QAction("О программе", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

        # Тёмная тема по умолчанию
        self.current_theme = "dark"
        self.set_dark_theme()

    def handle_command(self):
        command = self.command_input.text().strip()
        if command:
            self.assistant_thread.send_command(command)
            self.command_input.clear()
        else:
            QMessageBox.warning(self, "Пустая команда", "Пожалуйста, введите команду.")

    def update_chat(self, sender: str, message: str):
        if sender == "user":
            avatar_path = "user_avatar.png"
        else:
            avatar_path = "bot_avatar.png"

        bubble = MessageBubble(message, sender, avatar_path=avatar_path)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble)
        QTimer.singleShot(100, lambda: self.scroll_area.verticalScrollBar().setValue(
            self.scroll_area.verticalScrollBar().maximum()))

        # Дублируем в упрощённое окно
        self.simplified_window.add_message(sender, message)

    def notify(self, message: str):
        sender = "sonya"
        avatar_path = "bot_avatar.png"
        bubble = MessageBubble(message, sender, avatar_path=avatar_path)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble)
        self.status_bar.showMessage(message)

        # Дублируем в упрощённое окно
        self.simplified_window.add_message(sender, message)

        QTimer.singleShot(100, lambda: self.scroll_area.verticalScrollBar().setValue(
            self.scroll_area.verticalScrollBar().maximum()))

    def show_about(self):
        QMessageBox.information(self, "О программе", "Соня - голосовой помощник\nВерсия 1.1")

    def closeEvent(self, event):
        self.assistant_thread.stop()
        self.assistant_thread.quit()
        self.assistant_thread.wait()
        event.accept()

    def toggle_theme(self):
        if self.current_theme == "dark":
            self.set_light_theme()
            self.current_theme = "light"
            self.theme_toggle_btn.setIcon(QIcon("icons/dark_mode.png"))
        else:
            self.set_dark_theme()
            self.current_theme = "dark"
            self.theme_toggle_btn.setIcon(QIcon("icons/light_mode.png"))

    def set_dark_theme(self):
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(35, 35, 35))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.Base, QColor(45, 45, 45))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(35, 35, 35))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.Text, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.Button, QColor(35, 35, 35))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
        palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
        self.setPalette(palette)

    def set_light_theme(self):
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(240, 240, 240))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(0, 0, 0))
        palette.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(225, 225, 225))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(0, 0, 0))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(0, 0, 0))
        palette.setColor(QPalette.ColorRole.Text, QColor(0, 0, 0))
        palette.setColor(QPalette.ColorRole.Button, QColor(240, 240, 240))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(0, 0, 0))
        palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
        palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
        self.setPalette(palette)

    def toggle_simplified_mode(self):
        if not self.is_simplified:
            self.enter_simplified_mode()
        else:
            self.exit_simplified_mode()

    def enter_simplified_mode(self):
        """
        При входе в упрощённый режим скрываем интерфейс и показываем окно в правом нижнем углу.
        """
        # Скрываем элементы основного окна
        self.top_bar.hide()
        self.scroll_area.hide()
        self.input_widget.hide()
        self.buttons_widget.hide()

        # Выключаем голос
        self.assistant_thread.mute_voice = True

        # 1. Сначала показываем упрощённое окно
        self.simplified_window.show()

        # 2. На «следующий тик» событий переносим его в правый нижний угол
        QTimer.singleShot(0, self._position_simplified_window)

        self.is_simplified = True
        self.simplify_btn.setIcon(QIcon("icons/exit_simplify.png"))

    def _position_simplified_window(self):
        """
        Дополнительный метод, вызываемый по таймеру после show(),
        чтобы гарантировать корректное позиционирование в правом нижнем углу.
        """
        screen_geometry = QApplication.primaryScreen().availableGeometry()
        x = screen_geometry.width() - self.simplified_window.width() - 20
        y = screen_geometry.height() - self.simplified_window.height() - 20
        self.simplified_window.move(x, y)

    def exit_simplified_mode(self):
        """
        Возвращаемся из упрощённого режима в обычный.
        """
        self.simplified_window.hide()

        self.top_bar.show()
        self.scroll_area.show()
        self.input_widget.show()
        self.buttons_widget.show()

        self.assistant_thread.mute_voice = False
        self.simplify_btn.setIcon(QIcon("icons/simplify.png"))
        self.is_simplified = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Если окно в упрощённом режиме, «приклеим» обратно
        if self.is_simplified:
            QTimer.singleShot(0, self._position_simplified_window)


def button_style():
    """
    Стиль для кнопок.
    """
    return """
        QPushButton {
            background-color: #7289DA;
            color: white;
            font-size: 14px;
            padding: 10px;
            border: none;
            border-radius: 10px;
            transition: background-color 0.3s, transform 0.2s;
        }
        QPushButton:hover {
            background-color: #5b6eae;
            transform: scale(1.05);
        }
        QPushButton:pressed {
            background-color: #4e5d8a;
            transform: scale(0.95);
        }
    """


def main():
    app = QApplication(sys.argv)

    # Тёмная палитра по умолчанию
    dark_palette = QPalette()
    dark_palette.setColor(QPalette.ColorRole.Window, QColor(35, 35, 35))
    dark_palette.setColor(QPalette.ColorRole.WindowText, QColor(255, 255, 255))
    dark_palette.setColor(QPalette.ColorRole.Base, QColor(45, 45, 45))
    dark_palette.setColor(QPalette.ColorRole.AlternateBase, QColor(35, 35, 35))
    dark_palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 255))
    dark_palette.setColor(QPalette.ColorRole.ToolTipText, QColor(255, 255, 255))
    dark_palette.setColor(QPalette.ColorRole.Text, QColor(255, 255, 255))
    dark_palette.setColor(QPalette.ColorRole.Button, QColor(35, 35, 35))
    dark_palette.setColor(QPalette.ColorRole.ButtonText, QColor(255, 255, 255))
    dark_palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
    dark_palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
    dark_palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    dark_palette.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
    app.setPalette(dark_palette)

    window = MainWindow()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
