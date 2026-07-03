"""Client for the remote (proprietary) WFS risk engine.

The engine runs server-side in the owner's AWS account; this client calls it. It
sends ONLY the de-identified snapshot (re-checked against the egress allow-list
first), so nothing identifiable ever leaves the machine -- not even to the engine.

Auth matches the AWS Lambda authorizer: per-user HMAC. The client signs
    hex( HMAC-SHA256(secret, timestamp + "\n" + sha256_hex(body_bytes)) )
and sends X-Key-Id, X-Timestamp, X-Body-Hash, X-Signature. The signature is
lowercase hex (the server verifies with hmac.hexdigest()); the secret is used as
UTF-8 bytes of the key string (not hex-decoded). The server looks up the secret for
the key id, recomputes, and compares constant-time; stale timestamps are rejected
(replay protection). Same signing shape as the SnapTrade personal path.

Config (via the secret store / env):
    DEID_ENGINE_URL       the /assess endpoint (https://xxx.execute-api...amazonaws.com/assess)
    DEID_ENGINE_KEY_ID    the subscriber's WFS key id
    DEID_ENGINE_SECRET    the subscriber's WFS secret (used to sign; never sent)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request

from .deidentify import assert_allowlisted


class EngineNotConfigured(Exception):
    pass


class EngineError(Exception):
    pass


class RiskEngineClient:
    def __init__(self, url: str | None, key_id: str | None, secret: str | None,
                 timeout: float = 8.0) -> None:
        self._url = url
        self._key_id = key_id
        self._secret = secret
        self._timeout = timeout

    @property
    def configured(self) -> bool:
        # A subscriber must have all three: endpoint, key id, and signing secret.
        return bool(self._url and self._key_id and self._secret)

    def assess(self, sanitized_snapshot: dict) -> dict:
        if not self.configured:
            raise EngineNotConfigured("WFS engine not configured")
        # Defense in depth: only allow-listed, de-identified keys may leave.
        assert_allowlisted(sanitized_snapshot)

        body = json.dumps(sanitized_snapshot, separators=(",", ":")).encode()
        ts = str(int(time.time()))
        body_hash = hashlib.sha256(body).hexdigest()
        # Signature is lowercase hex (not base64) -- matches the server authorizer,
        # which verifies with hmac.hexdigest().
        sig = hmac.new(self._secret.encode(), f"{ts}\n{body_hash}".encode(),
                       hashlib.sha256).hexdigest()

        req = urllib.request.Request(self._url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-Key-Id", self._key_id)
        req.add_header("X-Timestamp", ts)
        req.add_header("X-Body-Hash", body_hash)   # authorizer requires this header
        req.add_header("X-Signature", sig)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            raise EngineError(f"engine returned HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise EngineError(f"could not reach engine: {e.reason}") from e


def free_stub(wfs_url: str) -> dict:
    """Free tier result: no deterministic engine, plus a subscribe invitation."""
    return {
        "tier": "free",
        "engine": None,
        "deterministic": False,
        "notice": (
            "This is an AI-generated assessment by Claude interpreting your "
            "de-identified metrics -- not a deterministic risk score. For a "
            "reproducible, validated risk score with scenario analysis and factor "
            f"exposure, subscribe to WFS at {wfs_url}."
        ),
    }


def resolve_assessment(snapshot, engine, local_available, local_assess, wfs_url):
    """Pure tier resolution (no MCP globals, so it's unit-testable).

    Priority: WFS subscriber (AWS) -> local deterministic binary -> free AI stub.
    On AWS failure, degrade gracefully to the local binary if present, else to the
    free stub (so a network blip never hard-errors the assessment).
    """
    if engine.configured:
        try:
            result = engine.assess(snapshot)
            result.setdefault("engine", "wfs-deterministic-v1")
            result["tier"] = "subscriber"
            result["deterministic"] = True
            return result
        except (EngineError, EngineNotConfigured) as e:
            if local_available:
                result = local_assess(snapshot)
                result.update(tier="subscriber", engine="local-v1",
                              deterministic=True,
                              notice="WFS engine unreachable; used local engine.")
                return result
            result = free_stub(wfs_url)
            result["tier"] = "subscriber_degraded"
            result["engine_error"] = str(e)   # e.g. "engine returned HTTP 403: ..."
            result["notice"] = (f"Your WFS engine returned an error ({e}); "
                                "showing an AI interpretation meanwhile.")
            return result
    if local_available:
        result = local_assess(snapshot)
        result.setdefault("engine", "local-v1")
        result["tier"] = "local"
        result["deterministic"] = True
        return result
    return free_stub(wfs_url)
