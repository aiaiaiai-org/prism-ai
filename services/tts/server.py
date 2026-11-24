# © 2026 aiaiaiai · aiaiaiai.org
# SPDX-License-Identifier: Apache-2.0

"""Bounded, product-authorized speech synthesis host adapter."""
import json
import os
import threading
from urllib.parse import urlsplit

import jwt

from engine import LocalSpeech, SpeechFailure

ISSUER = "https://auth.aiaiaiai.org"
AUDIENCE = "prism-ai"
SCOPE = "tts.synthesize"
MAX_BODY = 48_000
# At 14-17 characters of speech per second, 4,000 characters fit the 300 second
# audio cap. Callers (Whisper) must not send more: no truncation happens here.
MAX_TEXT = 4_000
MAX_AUDIO = 60 * 1024 * 1024  # bounded intermediate PCM WAV, not delivery size
MAX_SECONDS = 300
LEEWAY = 10  # seconds of clock skew tolerated between AIS and this host
RETRY_AFTER = "10"


class Rejected(Exception):
    def __init__(self, status, code, headers=()):
        self.status, self.code, self.headers = status, code, tuple(headers)


class ProductAuthorization:
    def __init__(self, jwks_url):
        parsed = urlsplit(jwks_url)
        if (parsed.scheme, parsed.netloc) != ("https", "auth.aiaiaiai.org"):
            raise ValueError("AIS JWKS must use the pinned HTTPS issuer")
        # Unknown key IDs may force at most one JWKS refresh per cooldown, so
        # unauthenticated requests cannot make this service hammer the issuer.
        # Stated explicitly so a dependency default change cannot remove it.
        self.keys = jwt.PyJWKClient(jwks_url, timeout=3, lifespan=300, cooldown_duration=30)

    def __call__(self, header):
        if not header.startswith("Bearer ") or len(header) > 8192:
            raise Rejected("401 Unauthorized", "invalid_token")
        token = header[7:]
        try:
            metadata = jwt.get_unverified_header(token)
            if metadata.get("typ") != "at+jwt" or metadata.get("alg") != "EdDSA":
                raise jwt.InvalidTokenError()
            key = self.keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(token, key.key, algorithms=["EdDSA"],
                                issuer=ISSUER, audience=AUDIENCE, leeway=LEEWAY,
                                options={"require": ["iss", "aud", "sub", "iat", "exp"]})
            if type(claims["iat"]) is not int or type(claims["exp"]) is not int:
                raise jwt.InvalidTokenError()
            if not 0 < claims["exp"] - claims["iat"] <= 300:
                raise jwt.InvalidTokenError()
        except jwt.PyJWKClientConnectionError:
            raise Rejected("503 Service Unavailable", "issuer_unavailable") from None
        except jwt.PyJWTError:
            raise Rejected("401 Unauthorized", "invalid_token") from None
        scope = claims.get("scope")
        if not isinstance(scope, str) or SCOPE not in scope.split():
            raise Rejected("403 Forbidden", "insufficient_scope")


def app_factory(authorize, synthesize):
    # Deployment must use one worker; threads share the bounded engine slot.
    slot = threading.BoundedSemaphore(1)

    def app(env, start):
        def reply(status, data, content_type="application/json", headers=()):
            start(status, [("Content-Type", content_type), ("Content-Length", str(len(data))),
                           ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                           *headers])
            return [data]

        try:
            path = env.get("PATH_INFO")
            method = env.get("REQUEST_METHOD")
            if path == "/healthz" and method == "GET":
                return reply("200 OK", b'{"status":"ok"}')
            if path != "/v1/speech":
                raise Rejected("404 Not Found", "not_found")
            if method != "POST":
                raise Rejected("405 Method Not Allowed", "method_not_allowed")
            authorize(env.get("HTTP_AUTHORIZATION", ""))
            if env.get("CONTENT_TYPE", "").split(";")[0].strip().lower() != "application/json":
                raise Rejected("415 Unsupported Media Type", "invalid_content_type")
            try:
                size = int(env.get("CONTENT_LENGTH", ""))
            except ValueError:
                raise Rejected("411 Length Required", "length_required") from None
            if size < 1 or size > MAX_BODY:
                raise Rejected("413 Payload Too Large", "invalid_size")
            try:
                raw = env["wsgi.input"].read(size)
                if len(raw) != size:
                    raise ValueError()
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict) or set(payload) != {"text", "voice"}:
                    raise ValueError()
                text, voice = payload["text"], payload["voice"]
                if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
                    raise ValueError()
                if not isinstance(voice, str) or not 1 <= len(voice) <= 64:
                    raise ValueError()
                text.encode("utf-8")
                if "\x00" in text or "[[" in text:
                    raise ValueError()
            except (ValueError, UnicodeError, RecursionError):
                raise Rejected("422 Unprocessable Entity", "invalid_request") from None
            if not slot.acquire(blocking=False):
                raise Rejected("429 Too Many Requests", "engine_busy",
                               [("Retry-After", RETRY_AFTER)])
            try:
                audio = synthesize(text, voice)
            finally:
                slot.release()
            return reply("200 OK", audio, "audio/wav")
        except SpeechFailure as exc:
            status = {
                "unknown_voice": "422 Unprocessable Entity",
                "audio_too_long": "422 Unprocessable Entity",
                "audio_too_large": "422 Unprocessable Entity",
                "synthesis_timeout": "504 Gateway Timeout",
                "synthesis_failed": "502 Bad Gateway",
            }.get(exc.code)
            if status is None:
                return reply("500 Internal Server Error", b'{"error":"internal_error"}')
            return reply(status, json.dumps({"error": exc.code}).encode())
        except Rejected as exc:
            return reply(exc.status, json.dumps({"error": exc.code}).encode(), headers=exc.headers)
        except Exception:
            # Do not log request payloads, subprocess output, or exception values.
            return reply("500 Internal Server Error", b'{"error":"internal_error"}')
    return app


def create_app():
    models = json.loads(os.environ["TTS_MODELS"])
    if not isinstance(models, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                               for k, v in models.items()):
        raise ValueError("invalid voice map")
    return app_factory(ProductAuthorization(os.environ["TTS_AIS_JWKS_URL"]),
                       LocalSpeech(models, max_seconds=MAX_SECONDS, max_audio=MAX_AUDIO))
