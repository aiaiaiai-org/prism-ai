# © 2026 aiaiaiai · aiaiaiai.org
# SPDX-License-Identifier: Apache-2.0

import io
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cryptography.hazmat.primitives import serialization
import base64

from engine import SpeechFailure
from server import (app_factory, ProductAuthorization, Rejected, ISSUER, AUDIENCE, SCOPE,
                    MAX_TEXT, MAX_BODY, RETRY_AFTER)


class SpeechTests(unittest.TestCase):
    def setUp(self):
        self.engine = Mock(return_value=b"RIFFexample")
        self.key = Ed25519PrivateKey.generate()
        self.auth = ProductAuthorization(ISSUER + "/jwks")
        self.auth.keys = Mock()
        self.auth.keys.get_signing_key_from_jwt.return_value = SimpleNamespace(
            key=self.key.public_key())
        self.app = app_factory(self.auth, self.engine)

    def token(self, changes=None, typ="at+jwt", key=None):
        now = int(time.time())
        claims = dict(iss=ISSUER, aud=AUDIENCE, sub="product", scope=SCOPE,
                      iat=now, exp=now + 300)
        claims.update(changes or {})
        return jwt.encode(claims, key or self.key, algorithm="EdDSA",
                          headers={"typ": typ, "kid": "test"})

    def request(self, token=None, payload=None, path="/v1/speech", method="POST",
                content_type="application/json", length=None):
        raw = json.dumps(payload if payload is not None else
                         {"text": "Привіт", "voice": "uk-UA"}).encode()
        start = Mock()
        response = self.app(dict(PATH_INFO=path, REQUEST_METHOD=method,
                                 CONTENT_TYPE=content_type,
                                 CONTENT_LENGTH=str(len(raw)) if length is None else length,
                                 HTTP_AUTHORIZATION="Bearer " + token if token else "",
                                 **{"wsgi.input": io.BytesIO(raw)}), start)
        status, headers = start.call_args.args
        return status, dict(headers), b"".join(response)

    def test_authorized(self):
        status, headers, body = self.request(self.token())
        self.assertEqual(status, "200 OK")
        self.assertEqual(headers["Content-Type"], "audio/wav")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(body, b"RIFFexample")
        self.engine.assert_called_once_with("Привіт", "uk-UA")

    def test_missing_token(self):
        self.assertEqual(self.request()[0], "401 Unauthorized")
        self.engine.assert_not_called()

    def test_wrong_token(self):
        for token in (self.token({"aud": "another"}),
                      self.token({"iss": "https://other.example"}),
                      self.token(typ="id+jwt"),
                      self.token(key=Ed25519PrivateKey.generate()),
                      self.token({"iat": 1, "exp": 301}),
                      self.token({"exp": int(time.time()) + 600})):
            with self.subTest(token=token[:20]):
                self.assertEqual(self.request(token)[0], "401 Unauthorized")
        self.engine.assert_not_called()

    def test_missing_scope(self):
        self.assertEqual(self.request(self.token({"scope": "profile"}))[0], "403 Forbidden")
        self.engine.assert_not_called()

    def test_bounds_and_schema(self):
        for payload in ({"text": "x" * (MAX_TEXT + 1), "voice": "uk-UA"},
                        {"text": " ", "voice": "uk-UA"},
                        {"text": "secret", "voice": "uk-UA", "chat_id": 1},
                        {"text": "[[raw]]", "voice": "uk-UA"}):
            self.assertEqual(self.request(self.token(), payload)[0], "422 Unprocessable Entity")
        self.engine.assert_not_called()

    def test_text_at_the_limit_is_accepted(self):
        status = self.request(self.token(), {"text": "x" * MAX_TEXT, "voice": "uk-UA"})[0]
        self.assertEqual(status, "200 OK")

    def test_token_issued_within_clock_skew_is_accepted(self):
        now = int(time.time())
        token = self.token({"iat": now + 5, "exp": now + 305})
        self.assertEqual(self.request(token)[0], "200 OK")

    def test_token_issued_far_in_the_future_is_rejected(self):
        now = int(time.time())
        token = self.token({"iat": now + 120, "exp": now + 420})
        self.assertEqual(self.request(token)[0], "401 Unauthorized")
        self.engine.assert_not_called()

    def test_routing_and_framing_errors(self):
        token = self.token()
        cases = [
            (dict(path="/other"), "404 Not Found"),
            (dict(method="GET"), "405 Method Not Allowed"),
            (dict(content_type="text/plain"), "415 Unsupported Media Type"),
            (dict(length=""), "411 Length Required"),
            (dict(length="0"), "413 Payload Too Large"),
            (dict(length=str(MAX_BODY + 1)), "413 Payload Too Large"),
        ]
        for options, expected in cases:
            with self.subTest(options=options):
                self.assertEqual(self.request(token, **options)[0], expected)
        self.engine.assert_not_called()

    def test_busy_engine_answers_429_with_retry_after(self):
        inner = []

        def synthesize(text, voice):
            inner.append(self.request(self.token()))
            return b"RIFFexample"

        self.engine.side_effect = synthesize
        self.assertEqual(self.request(self.token())[0], "200 OK")
        status, headers, _ = inner[0]
        self.assertEqual(status, "429 Too Many Requests")
        self.assertEqual(headers["Retry-After"], RETRY_AFTER)
        # The slot is released afterwards.
        inner.clear()
        self.engine.side_effect = None
        self.engine.return_value = b"RIFFexample"
        self.assertEqual(self.request(self.token())[0], "200 OK")

    def test_errors_do_not_leak(self):
        self.engine.side_effect = RuntimeError("private text")
        self.assertEqual(self.request(self.token())[2], b'{"error":"internal_error"}')

    def test_health(self):
        self.assertEqual(self.request(path="/healthz", method="GET")[0], "200 OK")
        self.engine.assert_not_called()

    def test_engine_error(self):
        self.engine.side_effect = SpeechFailure("unknown_voice")
        self.assertEqual(self.request(self.token())[0], "422 Unprocessable Entity")


class JwksRefreshTests(unittest.TestCase):
    def test_unknown_key_ids_do_not_refetch_the_issuer_on_every_request(self):
        key = Ed25519PrivateKey.generate()
        raw = key.public_key().public_bytes(serialization.Encoding.Raw,
                                            serialization.PublicFormat.Raw)
        jwks = {"keys": [{"kty": "OKP", "crv": "Ed25519", "kid": "known", "use": "sig",
                          "x": base64.urlsafe_b64encode(raw).rstrip(b"=").decode()}]}
        auth = ProductAuthorization(ISSUER + "/jwks")
        opener = Mock()
        opener.open.side_effect = lambda *args, **kwargs: io.BytesIO(json.dumps(jwks).encode())
        fetch = opener.open
        now = int(time.time())
        with patch("jwt.jwks_client.urllib.request.build_opener", return_value=opener):
            self.exercise(auth, key, now)
        self.assertEqual(fetch.call_count, 1)

    def exercise(self, auth, key, now):
        for number in range(5):
            token = jwt.encode(dict(iss=ISSUER, aud=AUDIENCE, sub="product", scope=SCOPE,
                                    iat=now, exp=now + 300), key, algorithm="EdDSA",
                               headers={"typ": "at+jwt", "kid": f"unknown-{number}"})
            with self.assertRaises(Rejected) as caught:
                auth("Bearer " + token)
            self.assertEqual(caught.exception.status, "401 Unauthorized")

    def test_refresh_cooldown_is_configured_explicitly(self):
        self.assertEqual(ProductAuthorization(ISSUER + "/jwks").keys.cooldown_duration, 30)


if __name__ == "__main__":
    unittest.main()
