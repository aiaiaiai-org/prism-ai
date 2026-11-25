#!/usr/bin/env bash
# © 2026 aiaiaiai · aiaiaiai.org
# SPDX-License-Identifier: Apache-2.0
#
# Real synthesis through the built image: the engine, the voice and the container user must
# work together. Usage: smoke.sh IMAGE
set -Eeuo pipefail
image="${1:?usage: smoke.sh IMAGE}"
out="$(mktemp)"
trap 'rm -f "$out"' EXIT
printf 'Доброго дня, це перевірка озвучення.' \
  | docker run --rm -i --entrypoint sh "$image" -c \
      'piper -m /opt/voices/uk_UA-ukrainian_tts-medium.onnx -f /tmp/speech.wav >/dev/null 2>&1 && cat /tmp/speech.wav' > "$out"
python3 - "$out" <<'PY'
import sys, wave
with wave.open(sys.argv[1], "rb") as audio:
    seconds = audio.getnframes() / audio.getframerate()
    assert audio.getsampwidth() == 2 and audio.getcomptype() == "NONE", "not PCM16"
    assert 1 <= seconds <= 20, f"unexpected duration {seconds:.1f}s"
    print(f"ok: {audio.getframerate()} Hz, {seconds:.1f}s")
PY
