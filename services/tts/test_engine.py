# © 2026 aiaiaiai · aiaiaiai.org
# SPDX-License-Identifier: Apache-2.0

"""Engine boundary tests with fake piper/ffmpeg executables; no real models."""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine import LocalSpeech, SpeechFailure

PIPER = """#!{python}
import os, sys, time, wave
args = sys.argv
out = args[args.index("-f") + 1]
text = sys.stdin.buffer.read()
log = os.environ.get("FAKE_STDIN_LOG")
if log:
    open(log, "wb").write(text)
mode = os.environ.get("FAKE_PIPER", "ok")
if mode == "fail":
    sys.exit(3)
if mode == "sleep":
    time.sleep(30)
seconds = 2 if mode == "long" else 0.5
with wave.open(out, "wb") as audio:
    audio.setnchannels(1)
    audio.setsampwidth(2)
    audio.setframerate(8000)
    audio.writeframes(b"\\0\\0" * int(8000 * seconds))
if mode == "zero_rate":
    data = bytearray(open(out, "rb").read())
    data[24:28] = b"\\0\\0\\0\\0"
    open(out, "wb").write(bytes(data))
"""

FFMPEG = """#!{python}
import os, sys
args = sys.argv
log = os.environ.get("FAKE_ARGS_LOG")
if log:
    open(log, "w").write("\\n".join(args[1:]))
mode = os.environ.get("FAKE_FFMPEG", "ok")
if mode == "fail":
    sys.exit(1)
header = b"OggS" + b"\\0" * 22 + b"OpusHead" + b"\\0" * 11
body = {{"ok": header, "big": header + b"x" * 4096, "bad": b"not an ogg file"}}[mode]
open(args[-1], "wb").write(body)
"""


class LocalSpeechTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="tts-test-"))
        self.addCleanup(self.cleanup)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.install("piper", PIPER)
        self.install("ffmpeg", FFMPEG)
        self.temp = self.root / "tmp"
        self.temp.mkdir()
        voice = self.root / "voice.onnx"
        voice.write_bytes(b"model")
        Path(str(voice) + ".json").write_text("{}")
        self.models = {"uk-UA": str(voice)}
        self.stdin_log = self.root / "stdin.bin"
        self.args_log = self.root / "ffmpeg-args.txt"
        env = {"PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
               "FAKE_STDIN_LOG": str(self.stdin_log), "FAKE_ARGS_LOG": str(self.args_log)}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        temp_patcher = patch.object(tempfile, "tempdir", str(self.temp))
        temp_patcher.start()
        self.addCleanup(temp_patcher.stop)

    def cleanup(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def install(self, name, template):
        path = self.bin / name
        path.write_text(template.format(python=sys.executable))
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def engine(self, **options):
        options.setdefault("max_seconds", 1)
        options.setdefault("max_audio", 1024 * 1024)
        return LocalSpeech(self.models, **options)

    def failure(self, engine, voice="uk-UA"):
        with self.assertRaises(SpeechFailure) as caught:
            engine("Привіт", voice)
        return caught.exception.code

    def test_synthesizes_ogg_opus_and_passes_text_only_through_stdin(self):
        audio = self.engine()("Привіт, світе", "uk-UA")
        self.assertTrue(audio.startswith(b"OggS"))
        self.assertIn(b"OpusHead", audio[:256])
        self.assertEqual(self.stdin_log.read_bytes(), "Привіт, світе".encode("utf-8"))
        self.assertEqual(os.listdir(self.temp), [])

    def test_strips_metadata_and_bounds_output_size_by_one_byte(self):
        self.engine(max_audio=2048)("текст", "uk-UA")
        arguments = self.args_log.read_text().split("\n")
        self.assertEqual(arguments[arguments.index("-map_metadata") + 1], "-1")
        self.assertEqual(arguments[arguments.index("-fs") + 1], "2049")

    def test_unknown_voice(self):
        self.assertEqual(self.failure(self.engine(), "other"), "unknown_voice")

    def test_audio_too_long(self):
        os.environ["FAKE_PIPER"] = "long"
        self.assertEqual(self.failure(self.engine()), "audio_too_long")
        self.assertEqual(os.listdir(self.temp), [])

    def test_audio_too_large(self):
        os.environ["FAKE_FFMPEG"] = "big"
        self.assertEqual(self.failure(self.engine(max_audio=1024)), "audio_too_large")
        self.assertEqual(os.listdir(self.temp), [])

    def test_invalid_audio_is_a_synthesis_failure(self):
        os.environ["FAKE_FFMPEG"] = "bad"
        self.assertEqual(self.failure(self.engine()), "synthesis_failed")

    def test_zero_sample_rate_is_a_synthesis_failure(self):
        os.environ["FAKE_PIPER"] = "zero_rate"
        self.assertEqual(self.failure(self.engine()), "synthesis_failed")

    def test_failed_executables_are_synthesis_failures(self):
        for name, variable in (("piper", "FAKE_PIPER"), ("ffmpeg", "FAKE_FFMPEG")):
            with self.subTest(name), patch.dict(os.environ, {variable: "fail"}):
                self.assertEqual(self.failure(self.engine()), "synthesis_failed")
        self.assertEqual(os.listdir(self.temp), [])

    def test_timeout_is_reported_and_cleans_up(self):
        os.environ["FAKE_PIPER"] = "sleep"
        self.assertEqual(self.failure(self.engine(piper_timeout=0.5)), "synthesis_timeout")
        self.assertEqual(os.listdir(self.temp), [])

    def test_missing_engine_or_voice_files_fail_at_startup(self):
        with patch.dict(os.environ, {"PATH": str(self.root)}):
            with self.assertRaises(RuntimeError):
                self.engine()
        os.remove(str(self.models["uk-UA"]) + ".json")
        with self.assertRaises(RuntimeError):
            self.engine()


if __name__ == "__main__":
    unittest.main()
