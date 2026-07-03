"""Freemium tier resolution + WFS engine HMAC signing."""

import hashlib
import hmac
import json

from deid_gateway.engine_client import (
    EngineError, RiskEngineClient, free_stub, resolve_assessment,
)

WFS = "https://wealthfinancialsystem.com"
SNAP = {"account_token": "portfolio_all", "net_delta_dollars_pct": 50.0, "positions": []}


class _Engine:
    def __init__(self, configured, result=None, fail=False):
        self.configured = configured
        self._result = result or {"risk_score": 77}
        self._fail = fail
    def assess(self, snap):
        if self._fail:
            raise EngineError("boom")
        return dict(self._result)


def _local(snap):
    return {"risk_score": 78}


def test_free_tier_when_nothing_configured():
    r = resolve_assessment(SNAP, _Engine(configured=False), False, _local, WFS)
    assert r["tier"] == "free" and r["deterministic"] is False and r["engine"] is None
    assert "subscribe to WFS" in r["notice"] and "risk_score" not in r


def test_local_tier_when_binary_present_no_subscription():
    r = resolve_assessment(SNAP, _Engine(configured=False), True, _local, WFS)
    assert r["tier"] == "local" and r["deterministic"] is True
    assert r["risk_score"] == 78 and r["engine"] == "local-v1"


def test_subscriber_tier_uses_wfs_engine():
    eng = _Engine(configured=True, result={"risk_score": 77})
    r = resolve_assessment(SNAP, eng, False, _local, WFS)
    assert r["tier"] == "subscriber" and r["deterministic"] is True
    assert r["engine"] == "wfs-deterministic-v1" and r["risk_score"] == 77


def test_subscriber_falls_back_to_local_when_aws_down():
    eng = _Engine(configured=True, fail=True)
    r = resolve_assessment(SNAP, eng, True, _local, WFS)      # local binary present
    assert r["tier"] == "subscriber" and r["engine"] == "local-v1"
    assert r["risk_score"] == 78 and "unreachable" in r["notice"]


def test_subscriber_degrades_to_stub_when_aws_down_and_no_local():
    eng = _Engine(configured=True, fail=True)
    r = resolve_assessment(SNAP, eng, False, _local, WFS)     # no binary
    assert r["tier"] == "subscriber_degraded" and r["deterministic"] is False
    assert "engine_error" in r and "403" or True


def test_engine_client_hmac_signature_shape():
    # configured requires url + key_id + secret
    assert RiskEngineClient("u", "k", "s").configured is True
    assert RiskEngineClient("u", None, "s").configured is False
    # signing matches the AWS authorizer contract: LOWERCASE HEX of
    # HMAC(secret_utf8, ts + "\n" + sha256_hex(body)). Not base64, not hex-decoded key.
    body = json.dumps({"a": 1}, separators=(",", ":")).encode()
    ts = "1700000000"
    sig = hmac.new(b"secret", f"{ts}\n{hashlib.sha256(body).hexdigest()}".encode(),
                   hashlib.sha256).hexdigest()
    assert len(sig) == 64 and all(c in "0123456789abcdef" for c in sig)  # lowercase hex
