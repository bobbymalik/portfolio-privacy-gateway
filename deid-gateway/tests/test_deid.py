"""The POC's real deliverable: proof that nothing identifiable crosses the boundary."""

import json

import pytest

from deid_gateway.broker import MockBroker
from deid_gateway.deidentify import (
    EgressViolation,
    deidentify_snapshot,
    enforce_egress,
    tokenize_account,
)
from deid_gateway.service import Gateway

SECRET = "test-secret-fixed"
RAW = MockBroker().fetch_snapshots()[0]


def test_no_pii_in_output():
    out = deidentify_snapshot(RAW, SECRET)
    blob = json.dumps(out)
    assert "Bobby Malik" not in blob
    assert "5WT00123" not in blob
    assert "Roth IRA - active" not in blob


def test_no_exact_dollar_amounts():
    out = deidentify_snapshot(RAW, SECRET)
    blob = json.dumps(out)
    for money in (184729, 23110, 41850):       # nav, cash, maintenance (rounded)
        assert str(money) not in blob


def test_enforce_egress_passes_clean_payload():
    out = deidentify_snapshot(RAW, SECRET)
    assert enforce_egress(out, RAW) is out      # no exception


def test_enforce_egress_blocks_non_allowlisted_key():
    out = deidentify_snapshot(RAW, SECRET)
    out["cash_balance_usd"] = 23110.22          # something that must never leave
    with pytest.raises(EgressViolation):
        enforce_egress(out, RAW)


def test_enforce_egress_blocks_leaked_identifier():
    out = deidentify_snapshot(RAW, SECRET)
    out["positions"][0]["symbol"] = "Bobby Malik"   # smuggle a name into a field
    with pytest.raises(EgressViolation):
        enforce_egress(out, RAW)


def test_token_is_stable_and_opaque():
    t1 = tokenize_account("5WT00123", SECRET)
    t2 = tokenize_account("5WT00123", SECRET)
    assert t1 == t2                              # stable across calls
    assert "5WT00123" not in t1                  # does not reveal the source
    assert t1.startswith("acct_")


def test_normalization_is_dimensionless_and_sane():
    out = deidentify_snapshot(RAW, SECRET)
    assert out["num_positions"] == 6
    # NVDA single is the largest gross weight; top1 should be meaningful and <= top3
    assert 0 < out["concentration_top1_pct"] <= out["concentration_top3_pct"]
    # every position carries only normalized fields
    for p in out["positions"]:
        assert set(p) == {
            "symbol", "underlying", "instrument_type",
            "weight_pct", "delta_dollars_pct", "gamma_dollars_pct",
            "theta_day_pct", "vega_pct_per_vol_point",
            "beta", "sector",
        }


def test_gateway_tools_emit_sanitized_and_audit():
    gw = Gateway(broker=MockBroker(), secret=SECRET)
    snap = gw.portfolio_snapshot()
    assert snap["account_token"].startswith("acct_")
    metrics = gw.risk_metrics()
    assert "positions" not in metrics            # risk_metrics omits positions
    accounts = gw.list_accounts()
    assert accounts and "account_token" in accounts[0]
    # audit log recorded a hash for each emitted payload, never raw data
    log = gw.audit_log()
    assert len(log) >= 2
    assert all("payload_sha256" in e for e in log)
    assert all("account_number" not in json.dumps(e) for e in log)


def test_etf_adr_overrides():
    from deid_gateway.refdata import ReferenceData
    rd = ReferenceData()
    # broad index funds: NOT Financial Services
    assert rd.lookup("VOO")["sector"] == "Broad Equity"
    assert rd.lookup("QQQ")["sector"] == "Technology"
    # cash-equivalents reclassified to Cash (excluded from sector concentration)
    assert rd.lookup("SPAXX")["sector"] == "Cash"
    assert rd.lookup("SGOV")["sector"] == "Cash"
    # sector ETF + ADR
    assert rd.lookup("IGV")["sector"] == "Technology"
    assert rd.lookup("BABA")["sector"] == "Consumer Cyclical"
    # override sets only sector for SCHD -> keeps a non-default beta path, known=True
    assert rd.lookup("SCHD")["sector"] == "Broad Equity"
    assert rd.lookup("VOO")["known"] is True
