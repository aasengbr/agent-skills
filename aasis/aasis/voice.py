"""Voz 100% local: wake word (openWakeWord), STT (faster-whisper), TTS (SAPI do Windows)."""
import numpy as np

SR = 16000
CHUNK = 1280  # 80 ms, exigido pelo openWakeWord


class Voice:
    def __init__(self, cfg: dict):
        import pyttsx3
        import sounddevice as sd
        from faster_whisper import WhisperModel
        from openwakeword.model import Model

        self.v = cfg["voice"]
        self.sd = sd
        self.stt = WhisperModel(self.v["stt_model"], compute_type=self.v["stt_compute_type"])
        self.wake = Model(wakeword_models=[self.v["wake_word"]])
        self.tts = pyttsx3.init()
        self.tts.setProperty("rate", self.v["tts_rate"])
        for voice in self.tts.getProperty("voices"):  # prefere voz pt-BR instalada
            if "pt" in (voice.id + voice.name).lower() or "portug" in voice.name.lower():
                self.tts.setProperty("voice", voice.id)
                break

    def speak(self, text: str) -> None:
        self.tts.say(text)
        self.tts.runAndWait()

    def wait_for_wake_word(self) -> None:
        with self.sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=CHUNK) as s:
            while True:
                audio, _ = s.read(CHUNK)
                scores = self.wake.predict(audio[:, 0])
                if max(scores.values()) >= self.v["wake_threshold"]:
                    self.wake.reset()
                    return

    def listen(self) -> str:
        """Grava até silêncio prolongado e transcreve."""
        frames, quiet, heard = [], 0, False
        limit = int(self.v["max_record_seconds"] * SR / CHUNK)
        quiet_limit = int(self.v["silence_seconds"] * SR / CHUNK)
        with self.sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=CHUNK) as s:
            for _ in range(limit):
                audio, _ = s.read(CHUNK)
                frames.append(audio[:, 0].copy())
                loud = np.abs(audio).mean() > 300
                heard |= loud
                quiet = 0 if loud else quiet + 1
                if heard and quiet >= quiet_limit:
                    break
        pcm = np.concatenate(frames).astype(np.float32) / 32768.0
        segs, _ = self.stt.transcribe(pcm, language="pt", vad_filter=True)
        return " ".join(sg.text.strip() for sg in segs).strip()
