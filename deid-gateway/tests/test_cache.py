"""Gateway TTL cache: auto-refresh of stale data + result caching."""

import time

from deid_gateway.service import Gateway


class CountingBroker:
    """A broker that reports how many times it was pulled."""
    def __init__(self):
        self.pulls = 0

    def fetch_snapshots(self):
        self.pulls += 1
        return [{
            "account": {"account_number": "5WT00001", "account_holder": "", "nickname": "",
                        "net_liquidating_value": 100000.0, "cash_balance": 100000.0,
                        "maintenance_requirement": 0.0},
            "positions": [],
        }]


def test_default_ttl_is_60_minutes():
    gw = Gateway(broker=CountingBroker(), secret="s")
    assert gw._cache_ttl == 3600.0


def test_reuses_cache_within_ttl_then_refreshes_when_stale():
    b = CountingBroker()
    gw = Gateway(broker=b, secret="s", cache_ttl=0.3)   # 300 ms window
    gw.list_accounts(); gw.list_accounts(); gw.list_accounts()
    assert b.pulls == 1                                  # served from cache
    time.sleep(0.35)
    gw.list_accounts()
    assert b.pulls == 2                                  # stale -> one re-pull


def test_result_cache_hits_within_ttl_and_expires_after():
    gw = Gateway(broker=CountingBroker(), secret="s", cache_ttl=0.3)
    gw.cache_put("k", {"risk_score": 77})
    assert gw.cache_get("k") == {"risk_score": 77}       # hit
    time.sleep(0.35)
    assert gw.cache_get("k") is None                     # expired


def test_snapshot_key_stable_for_identical_snapshots():
    gw = Gateway(broker=CountingBroker(), secret="s")
    snap = {"account_token": "portfolio_all", "net_delta_dollars_pct": 50.0, "positions": []}
    assert gw.snapshot_key("assessment", snap) == gw.snapshot_key("assessment", dict(snap))


def test_refresh_clears_result_cache():
    b = CountingBroker()
    gw = Gateway(broker=b, secret="s", cache_ttl=3600)
    gw.cache_put("k", {"x": 1})
    gw.refresh()                                          # new data invalidates results
    assert gw.cache_get("k") is None
