"""Multi-broker fan-out + whole-portfolio aggregate (NAV-weighted, not averaged)."""

from deid_gateway.broker import MultiBroker
from deid_gateway.service import AGGREGATE_TOKEN, Gateway

SECRET = "unit-test-secret"


def _raw(acct_no, nav, amzn_mv, upx=200.0):
    qty = amzn_mv / upx
    return {"account": {"account_number": acct_no, "account_holder": "", "nickname": "",
                        "net_liquidating_value": nav, "cash_balance": nav - amzn_mv,
                        "maintenance_requirement": 0.0},
            "positions": [{"symbol": "AMZN", "underlying": "AMZN", "instrument_type": "Equity",
                           "quantity": qty, "underlying_price": upx, "market_value": amzn_mv,
                           "delta": 1.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}]}


class FakeBroker:
    def __init__(self, snaps): self._s = snaps
    def fetch_snapshots(self): return self._s


def test_aggregate_is_nav_weighted_not_averaged():
    # A: AMZN is 60% of a $100k account.  B: AMZN is 10% of a $300k account.
    a = FakeBroker([_raw("5WT00001", 100_000, 60_000)])
    b = FakeBroker([_raw("5WT00002", 300_000, 30_000)])
    gw = Gateway(broker=MultiBroker([a, b]), secret=SECRET)

    combined = gw.portfolio_snapshot(AGGREGATE_TOKEN)
    assert combined["account_token"] == AGGREGATE_TOKEN
    # correct dollar-weighted concentration = (60k+30k)/400k = 22.5%
    # a naive average of 60% and 10% would give 35% -- which would be WRONG
    assert abs(combined["concentration_top1_pct"] - 22.5) < 0.3
    assert abs(combined["net_delta_dollars_pct"] - 22.5) < 0.3   # 90k delta-$/400k


def test_per_account_still_default_and_aggregate_listed():
    a = FakeBroker([_raw("5WT00001", 100_000, 60_000)])
    b = FakeBroker([_raw("5WT00002", 300_000, 30_000)])
    gw = Gateway(broker=MultiBroker([a, b]), secret=SECRET)

    accts = gw.list_accounts()
    assert any(x.get("aggregate") for x in accts)              # aggregate offered
    assert sum(1 for x in accts if not x.get("aggregate")) == 2  # both real accounts present

    # a single real account still reads as its own 60%, unchanged
    real = next(x["account_token"] for x in accts if not x.get("aggregate"))
    one = gw.portfolio_snapshot(real)
    assert abs(one["concentration_top1_pct"] - 60.0) < 0.3 or abs(one["concentration_top1_pct"] - 10.0) < 0.3


def test_one_source_down_does_not_sink_the_rest():
    class Broken:
        def fetch_snapshots(self): raise RuntimeError("broker offline")
    good = FakeBroker([_raw("5WT00001", 100_000, 60_000)])
    mb = MultiBroker([Broken(), good])
    snaps = mb.fetch_snapshots()
    assert len(snaps) == 1 and mb.errors      # good source survived, error recorded
