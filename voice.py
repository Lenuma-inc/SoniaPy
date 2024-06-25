import os
import torch
import time
import sounddevice as sd

device = torch.device('cpu')
torch.set_num_threads(4)
local_file = "model.pt"

if not os.path.isfile(local_file):
    torch.hub.download_url_to_file("https://models.silero.ai/models/tts/ru/v4_ru.pt",
                                   local_file)

model = torch.package.PackageImporter(
    local_file).load_pickle("tts_models", "model")
model.to(device)

sample_rate = 48000
speaker = 'baya'


def bot_speak(text):
    audio = model.apply_tts(text=text,
                            speaker=speaker,
                            sample_rate=sample_rate)

    sd.play(audio, sample_rate * 1.05)
    time.sleep((len(audio) / sample_rate) + 0.5)
    sd.stop()
