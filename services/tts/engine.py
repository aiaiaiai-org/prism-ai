# © 2026 aiaiaiai · aiaiaiai.org
# SPDX-License-Identifier: Apache-2.0

"""Model execution adapter, independent of HTTP and identity."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import wave


class SpeechFailure(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class LocalSpeech:
    """External executable boundary; no provider network calls."""
    def __init__(self, models, *, max_seconds, max_audio, piper_timeout=75):
        self.max_seconds = max_seconds
        self.max_audio = max_audio
        self.piper_timeout = piper_timeout
        self.models = {name: Path(path).resolve() for name, path in models.items()}
        self.piper = shutil.which("piper")
        if not self.piper or not self.models:
            raise RuntimeError("speech engine or voices not configured")
        for path in self.models.values():
            if not path.is_file() or not Path(str(path) + ".json").is_file():
                raise RuntimeError("voice model or config missing")

    def __call__(self, text, voice):
        if voice not in self.models:
            raise SpeechFailure("unknown_voice")
        with tempfile.TemporaryDirectory(prefix="tts-") as directory:
            wav = Path(directory) / "speech.wav"
            try:
                subprocess.run([self.piper, "-m", str(self.models[voice]), "-f", str(wav)],
                               input=text.encode("utf-8"), stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=self.piper_timeout, check=True)
                if wav.stat().st_size > self.max_audio:
                    raise SpeechFailure("audio_too_large")
                with wave.open(str(wav), "rb") as audio:
                    rate = audio.getframerate()
                    frames = audio.getnframes()
                    channels = audio.getnchannels()
                    if (not 8000 <= rate <= 48000 or frames <= 0
                            or channels not in (1, 2) or audio.getsampwidth() != 2
                            or audio.getcomptype() != "NONE"):
                        raise RuntimeError("invalid PCM audio")
                    if frames / rate > self.max_seconds:
                        raise SpeechFailure("audio_too_long")
                    if len(audio.readframes(frames)) != frames * channels * 2:
                        raise RuntimeError("incomplete audio")
                result = wav.read_bytes()
                if (not result.startswith(b"RIFF") or result[8:12] != b"WAVE"
                        or int.from_bytes(result[4:8], "little") != len(result) - 8):
                    raise RuntimeError("invalid WAV container")
                return result
            except subprocess.TimeoutExpired:
                raise SpeechFailure("synthesis_timeout") from None
            except (OSError, subprocess.CalledProcessError, wave.Error, EOFError, RuntimeError):
                raise SpeechFailure("synthesis_failed") from None
