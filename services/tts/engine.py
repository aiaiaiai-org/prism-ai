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
    def __init__(self, models, *, max_seconds, max_audio, piper_timeout=75, ffmpeg_timeout=15):
        self.max_seconds = max_seconds
        self.max_audio = max_audio
        self.piper_timeout = piper_timeout
        self.ffmpeg_timeout = ffmpeg_timeout
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
                               stderr=subprocess.DEVNULL, timeout=self.piper_timeout, check=True)
                with wave.open(str(wav), "rb") as audio:
                    rate = audio.getframerate()
                    if rate <= 0:
                        raise RuntimeError("invalid audio")
                    if audio.getnframes() / rate > self.max_seconds:
                        raise SpeechFailure("audio_too_long")
                # One byte beyond the limit: oversize output is detected below
                # instead of being silently truncated at the limit.
                subprocess.run([self.ffmpeg, "-nostdin", "-v", "error", "-i", str(wav),
                                "-map_metadata", "-1", "-c:a", "libopus", "-b:a", "32k",
                                "-ac", "1", "-fs", str(self.max_audio + 1),
                                "-f", "ogg", str(ogg)], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=self.ffmpeg_timeout, check=True)
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
