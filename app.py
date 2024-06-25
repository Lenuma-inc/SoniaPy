import voice
import sounddevice as sd
import vosk
from fuzzywuzzy import fuzz
import random
import json
import queue
from subprocess import call
import webbrowser

voice.bot_speak("Соня вас внимательно слушает ...")

VA_CMD_LIST = {
    "help": ('список команд', 'команды', 'что ты умеешь', 'твои навыки', 'навыки'),
    "joke": ('расскажи анекдот', 'рассмеши', 'шутка', 'расскажи шутку', 'пошути', 'развесели'),
    "browser": ('браузер', 'запусти браузер'),
    "volume up": ('сделай громче','увеличь громкость'),
    "volume down": ('сделай тише','уменши громкость'),
    "volume full": ('верни звук','включи звук'),
    "volume mute": ('выключи звук','отключи звук'),
    "reboot": ('перезагрузи','перезагрузка'),
    "shutdown": ('выключи компьютер')
}

q = queue.Queue()

model = vosk.Model("model_small_ru")

device = sd.default.device  # <--- по умолчанию
samplerate = int(
    sd.query_devices(device[0], "input")["default_samplerate"]
)  # получаем частоту микрофона


def callback(indata, frames, time, status):
    q.put(bytes(indata))


def main():
    # постоянная прослушка микрофона
    with sd.RawInputStream(
        samplerate=samplerate,
        blocksize=16000,
        device=device[0],
        dtype="int16",
        channels=1,
        callback=callback,
    ):

        rec = vosk.KaldiRecognizer(model, samplerate)
        while True:
            data = q.get()
            if rec.AcceptWaveform(data):
                data = json.loads(rec.Result())["text"]
                recognize(data)
                filter_cmd(data)


# преобразование текста в речь
def recognize(data):
    print("Пользователь сказал: " + data)
    if data.startswith("соня"):
        # обращаются к ассистенту
        cmd = recognize_cmd(filter_cmd(data))
       # print("Сервер получил: " + cmd)
        if cmd['cmd'] not in VA_CMD_LIST.keys():
            voice.bot_speak("Что?")
        else:
            execute_cmd(cmd['cmd'])


def recognize_cmd(cmd: str):
    rc = {'cmd': '', 'percent': 0}
    for c, v in VA_CMD_LIST.items():

        for x in v:
            vrt = fuzz.ratio(cmd, x)
            if vrt > rc['percent']:
                rc['cmd'] = c
                rc['percent'] = vrt

    return rc

def execute_cmd(cmd: str):
    if cmd == 'help':
        # help
        text = "Я умею: ..."
        text += "рассказывать анекдоты ..."
        text += "управлять громкостью"
        text += "выключать и перезагружать компьютер,"
        text += "и открывать браузер"
        voice.bot_speak(text)
        pass

elif cmd == 'joke':
    jokes = [
        'Как смеются программисты? ... ехе ехе ехе',
        'ЭсКьюЭль запрос заходит в бар, подходит к двум столам и спрашивает .. «м+ожно присоединиться?»',
        'Программист это машина для преобразования кофе в код',
        'В чем разница между программистом и пользователем? Пользователь думает, что один килобайт - это тысяча байт, а программист знает, что один килобайт - это тысяча двадцать четыре байта.',
        'Почему программисты путают Рождество с Хэллоуином? Потому что окт тридцать один равно дек двадцать пять.',
        'Бинарные люди делятся на десять типов: тех, кто понимает двоичную систему, и тех, кто нет.',
        'Всё, что мы видим в мире - результат ошибки компиляции.',
        'Кодировка – это как температура в комнате. Если неправильно настроена, то дискомфорт обеспечен.',
        'Программист - это человек, который решил задачу, о которой вы даже не подозревали, способом, который вы не поймете.',
        'Почему программисты любят природу? Потому что там нет багов, только фичи.'
    ]

        voice.bot_speak(random.choice(jokes))

    elif cmd == 'browser':
      webbrowser.open("google.com")
      voice.bot_speak("открываю")

    elif cmd == 'volume up':
      call(["amixer", "-D", "pulse", "sset", "Master", "10%+"])
      voice.bot_speak("громкость увеличина на десять процентов")

    elif cmd == 'volume down':
      call(["amixer", "-D", "pulse", "sset", "Master", "10%-"])
      voice.bot_speak("громкость уменьшина на десять процентов")

    elif cmd == 'volume mute':
      call(["amixer", "-D", "pulse", "sset", "Master", "0%"])
      voice.bot_speak("громкость отключена")

    elif cmd == 'volume full':
      call(["amixer", "-D", "pulse", "sset", "Master", "100%"])
      voice.bot_speak("громкость включена")

    elif cmd == 'reboot':
      call(["reboot"])

    elif cmd == 'shutdown':
      call(["shutdown", "-f", "now"])

# очистка текста от имени бота
def filter_cmd(data):
    cmd = data
    cmd = cmd.replace('соня', '').strip()
    cmd = cmd.replace('  ', ' ').strip()
    return cmd

while True:
    main()
