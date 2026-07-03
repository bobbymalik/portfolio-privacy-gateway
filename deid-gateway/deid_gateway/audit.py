"""Persistent, encrypted, append-only audit log.

Records one entry per sanitized payload that leaves the gateway (to the AI or to
the risk engine): timestamp, tool, account token, payload hash, and outcome. No
raw data is ever recorded. Each line is Fernet-encrypted at rest, so even the
hashes and tokens are protected on disk.

The encryption key is derived from DEID_GATEWAY_SECRET via PBKDF2, so there's no
extra key to manage -- if you can't unlock the gateway secret, you can't read
the log.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time

_KDF_SALT = b"deid-gateway/audit/v1"
_KDF_ROUNDS = 200_000


def _derive_key(secret: str) -> bytes:
    raw = hashlib.pbkdf2_hmac("sha256", secret.encode(), _KDF_SALT, _KDF_ROUNDS)
    return base64.urlsafe_b64encode(raw)


class AuditLog:
    def __init__(self, secret: str, path: str | None = None) -> None:
        from cryptography.fernet import Fernet  # lazy import

        self._fernet = Fernet(_derive_key(secret))
        self._path = path or os.path.expanduser("~/.deid_gateway/audit.log")
        os.makedirs(os.path.dirname(self._path), exist_ok=True)

    def record(self, *, tool: str, account_token: str | None,
               payload: dict | None = None, **extra) -> None:
        entry = {
            "ts": time.time(),
            "tool": tool,
            "account_token": account_token,
            **extra,
        }
        if payload is not None:
            entry["payload_sha256"] = hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode()).hexdigest()
        token = self._fernet.encrypt(json.dumps(entry).encode())
        with open(os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "wb") as f:
            f.write(token + b"\n")

    def read(self) -> list[dict]:
        """Decrypt and return all entries (for the local control UI / audits)."""
        if not os.path.exists(self._path):
            return []
        out = []
        with open(self._path, "rb") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(self._fernet.decrypt(line).decode()))
        return out
