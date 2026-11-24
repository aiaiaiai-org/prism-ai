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
    def __init__(self, models, *, max_seconds, max_audio):
        self.max_seconds = max_seconds
        self.max_audio = max_audio
        self.models = {name: Path(path).resolve() for name, path in models.items()}
        self.piper = shutil.which("piper")
        self.ffmpeg = shutil.which("ffmpeg")
        if not self.piper or not self.ffmpeg or not self.models:
            raise RuntimeError("speech engine or voices not configured")
        for path in self.models.values():
            if not path.is_file() or not Path(str(path) + ".json").is_file():
                raise RuntimeError("voice model or config missing")

    def __call__(self, text, voice):
        if voice not in self.models:
            raise SpeechFailure("unknown_voice")
        with tempfile.TemporaryDirectory(prefix="tts-") as directory:
            wav = Path(directory) / "speech.wav"
            ogg = Path(directory) / "speech.ogg"
            try:
                subprocess.run([self.piper, "-m", str(self.models[voice]), "-f", str(wav)],
                               input=text.encode("utf-8"), stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=75, check=True)
                with wave.open(str(wav), "rb") as audio:
                    if audio.getnframes() / audio.getframerate() > self.max_seconds:
                        raise SpeechFailure("audio_too_long")
                subprocess.run([self.ffmpeg, "-nostdin", "-v", "error", "-i", str(wav),
                                "-c:a", "libopus", "-b:a", "32k", "-ac", "1",
                                "-f", "ogg", str(ogg)], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=15, check=True)
                if ogg.stat().st_size > self.max_audio:
                    raise SpeechFailure("audio_too_large")
                result = ogg.read_bytes()
                if not result.startswith(b"OggS") or b"OpusHead" not in result[:256]:
                    raise RuntimeError("invalid audio")
                return result
            except subprocess.TimeoutExpired:
                raise SpeechFailure("synthesis_timeout") from None
            except (OSError, subprocess.CalledProcessError, wave.Error, EOFError, RuntimeError):
                raise SpeechFailure("synthesis_failed") from None
