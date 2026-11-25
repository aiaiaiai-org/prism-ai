# syntax=docker/dockerfile:1
# © 2026 aiaiaiai · aiaiaiai.org
# SPDX-License-Identifier: Apache-2.0

# Fetches the external engine and voice, each pinned by checksum (see NOTICE for licenses).
FROM debian:bookworm-slim AS assets
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /assets
# Piper CLI 2023.11.14-2 (MIT). The archive also carries espeak-ng (GPL-3.0), see NOTICE.
ARG PIPER_URL=https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_x86_64.tar.gz
ARG PIPER_SHA256=a50cb45f355b7af1f6d758c1b360717877ba0a398cc8cbe6d2a7a3a26e225992
# Voice uk_UA-ukrainian_tts-medium (CC0), pinned to a rhasspy/piper-voices revision.
ARG VOICES=https://huggingface.co/rhasspy/piper-voices/resolve/5ec43b186ef915a195d3ddbcfd9916e76c503c3f/uk/uk_UA/ukrainian_tts/medium
ARG VOICE_ONNX_SHA256=7920419ac5f6fd8b6450520f24b52ed5a319cb53dd018fbcd71c9e079cbac84f
ARG VOICE_JSON_SHA256=4e96e72917ca9b94edc77d6ccfee03a73f450ba2fc1ca93c2e562bc014e5aa55
RUN set -eux; \
    curl -fsSL --retry 3 -o piper.tgz "$PIPER_URL"; \
    echo "$PIPER_SHA256  piper.tgz" | sha256sum -c -; \
    mkdir -p /out/piper /out/voices; \
    tar -xzf piper.tgz -C /out; \
    for f in uk_UA-ukrainian_tts-medium.onnx uk_UA-ukrainian_tts-medium.onnx.json; do \
        curl -fsSL --retry 3 -o "/out/voices/$f" "$VOICES/$f"; \
    done; \
    echo "$VOICE_ONNX_SHA256  /out/voices/uk_UA-ukrainian_tts-medium.onnx" | sha256sum -c -; \
    echo "$VOICE_JSON_SHA256  /out/voices/uk_UA-ukrainian_tts-medium.onnx.json" | sha256sum -c -

FROM python:3.12-slim
RUN useradd --system --create-home --uid 1927 tts
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=assets /out/piper /opt/piper
COPY --from=assets /out/voices /opt/voices
COPY engine.py server.py ./
# Configuration is the environment. No secret: the service only verifies AIS tokens against
# the issuer's public keys. TLS ends at the edge.
ENV PATH=/opt/piper:$PATH \
    TTS_MODELS='{"uk-UA":"/opt/voices/uk_UA-ukrainian_tts-medium.onnx"}' \
    TTS_AIS_JWKS_URL=https://auth.aiaiaiai.org/.well-known/jwks.json
USER tts
EXPOSE 1927
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:1927/healthz', timeout=3)"
# One worker: the engine admits one request at a time (a second gets 429).
CMD ["gunicorn", "--workers", "1", "--threads", "4", "--timeout", "120", "--bind", "0.0.0.0:1927", "server:create_app()"]
