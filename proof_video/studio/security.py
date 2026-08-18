"""Localhost-only bootstrap and session security for Proof Studio."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import time
from urllib.parse import urlparse


COOKIE_NAME = "proof_studio_session"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


class StudioSecurity:
    def __init__(self, state_root: Path, *, token_ttl: int = 120) -> None:
        self.secret_path = state_root / "session-secret"
        self.secret_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.secret_path.exists():
            temporary = self.secret_path.with_suffix(".tmp")
            temporary.write_text(secrets.token_urlsafe(48), encoding="utf-8")
            temporary.replace(self.secret_path)
        self.secret = self.secret_path.read_text(encoding="utf-8").strip().encode()
        self.token_ttl = token_ttl
        self.sessions: set[str] = set()
        self.used_nonces: set[str] = set()

    def issue_bootstrap_token(self) -> str:
        payload = {
            "iat": int(time.time()),
            "nonce": secrets.token_urlsafe(18),
        }
        encoded = _b64(json.dumps(payload, separators=(",", ":")).encode())
        signature = _b64(hmac.new(self.secret, encoded.encode(), hashlib.sha256).digest())
        return f"{encoded}.{signature}"

    def exchange_bootstrap_token(self, token: str) -> str:
        try:
            encoded, signature = token.split(".", 1)
            expected = _b64(
                hmac.new(self.secret, encoded.encode(), hashlib.sha256).digest()
            )
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            padding = "=" * (-len(encoded) % 4)
            payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
            issued = int(payload["iat"])
            nonce = str(payload["nonce"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("invalid bootstrap token") from error
        if abs(int(time.time()) - issued) > self.token_ttl:
            raise ValueError("bootstrap token expired")
        if nonce in self.used_nonces:
            raise ValueError("bootstrap token already used")
        self.used_nonces.add(nonce)
        session = secrets.token_urlsafe(36)
        self.sessions.add(session)
        return session

    def valid_session(self, value: str | None) -> bool:
        return bool(value and value in self.sessions)


def valid_local_host(host: str) -> bool:
    hostname = host.rsplit(":", 1)[0].strip("[]").lower()
    return hostname in {"127.0.0.1", "localhost", "::1"}


def valid_same_origin(origin: str | None, host: str) -> bool:
    if not origin:
        return True
    parsed = urlparse(origin)
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == host.lower()
