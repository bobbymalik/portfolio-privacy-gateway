"""Tests for the engine client and the encrypted audit log."""

import os

import pytest

from deid_gateway.audit import AuditLog
from deid_gateway.deidentify import EgressViolation
from deid_gateway.engine_client import EngineNotConfigured, RiskEngineClient

CLEAN_SNAPSHOT = {
    "schema_version": "deid-1",
    "account_token": "acct_test",
    "num_positions": 1,
    "net_delta_dollars_pct": 10.0,
    "gross_exposure_pct": 50.0,
    "theta_day_pct": 0.1,
    "vega_pct_per_vol_point": 0.2,
    "margin_utilization_pct": 20.0,
    "concentration_top1_pct": 30.0,
    "concentration_top3_pct": 45.0,
    "positions": [{
        "symbol": "AMZN", "underlying": "AMZN", "instrument_type": "Equity",
        "weight_pct": 30.0, "delta_dollars_pct": 30.0, "gamma_dollars_pct": 0.0,
        "theta_day_pct": 0.0, "vega_pct_per_vol_point": 0.0,
    }],
}


def test_engine_client_raises_when_unconfigured():
    client = RiskEngineClient(url=None, key_id=None, secret=None)
    assert client.configured is False
    with pytest.raises(EngineNotConfigured):
        client.assess(CLEAN_SNAPSHOT)


def test_engine_client_blocks_non_deidentified_payload():
    client = RiskEngineClient(url="https://example.invalid/assess", key_id="k", secret="s")
    leaky = dict(CLEAN_SNAPSHOT, cash_balance_usd=23110.22)   # must never leave
    with pytest.raises(EgressViolation):
        client.assess(leaky)


def test_audit_log_roundtrip_is_encrypted(tmp_path):
    path = str(tmp_path / "audit.log")
    log = AuditLog(secret="unit-test-secret", path=path)
    log.record(tool="get_risk_assessment", account_token="acct_test",
               payload=CLEAN_SNAPSHOT, risk_score=42)

    # On-disk bytes are ciphertext: no token, tool name, or hash in cleartext.
    raw = open(path, "rb").read()
    assert b"acct_test" not in raw
    assert b"get_risk_assessment" not in raw

    entries = log.read()
    assert len(entries) == 1
    assert entries[0]["tool"] == "get_risk_assessment"
    assert entries[0]["account_token"] == "acct_test"
    assert entries[0]["risk_score"] == 42
    assert "payload_sha256" in entries[0]
    assert "positions" not in entries[0]            # raw payload never stored, only its hash
