"""SnapTrade broker: mapping + local greeks computation."""

import datetime as dt

from deid_gateway.deidentify import deidentify_snapshot, enforce_egress
from deid_gateway.snaptrade_broker import SnapTradeBroker

SECRET = "unit-test-secret"
EXPIRY = (dt.date.today() + dt.timedelta(days=365)).isoformat()


class FakeAdapter:
    """Stands in for the SnapTrade SDK + quote source."""
    def list_accounts(self):
        return [{"id": "acc-1", "number": "Z12345", "name": "Brokerage"}]

    def cash(self, account_id):
        return 5000.0

    def equity_positions(self, account_id):
        return [{"ticker": "AAPL", "units": 100, "price": 200.0}]

    def option_positions(self, account_id):
        # long 2 AAPL calls, K=210, ~1y out, mark $12
        return [{"underlying": "AAPL", "occ": "AAPL  270101C00210000",
                 "option_type": "CALL", "strike": 210.0, "expiry": EXPIRY,
                 "units": 2, "price": 12.0}]

    def spot(self, ticker):
        return 200.0 if ticker == "AAPL" else None


def test_snaptrade_maps_and_computes_greeks():
    broker = SnapTradeBroker(FakeAdapter())
    raw = broker.fetch_snapshots()[0]

    # NAV = cash + equity mv + option mv = 5000 + 20000 + (2*12*100)=2400
    assert abs(raw["account"]["net_liquidating_value"] - 27400.0) < 1e-6
    assert broker.unpriced_options == 0

    eq = next(p for p in raw["positions"] if p["instrument_type"] == "Equity")
    opt = next(p for p in raw["positions"] if p["instrument_type"] == "Equity Option")
    assert eq["delta"] == 1.0
    # computed call greeks: 0<delta<1, gamma>0, theta<0 (decay), vega>0
    assert 0.0 < opt["delta"] < 1.0
    assert opt["gamma"] > 0 and opt["theta"] < 0 and opt["vega"] > 0
    assert opt["underlying_price"] == 200.0


def test_snaptrade_snapshot_passes_egress_with_greeks():
    broker = SnapTradeBroker(FakeAdapter())
    raw = broker.fetch_snapshots()[0]
    snap = deidentify_snapshot(raw, SECRET)
    enforce_egress(snap, raw)                      # no PII / only allow-listed keys
    opt = next(p for p in snap["positions"] if "210000" in p["symbol"])
    assert opt["delta_dollars_pct"] != 0.0         # greeks survived into the snapshot
    assert opt["beta"] and opt["sector"] == "Technology"   # AAPL enriched from refdata


def test_missing_spot_marks_unpriced():
    class NoSpot(FakeAdapter):
        def equity_positions(self, account_id):
            return []                               # AAPL not held as stock
        def spot(self, ticker):
            return None                             # and no quote source
    broker = SnapTradeBroker(NoSpot())
    raw = broker.fetch_snapshots()[0]
    assert broker.unpriced_options == 1
    opt = next(p for p in raw["positions"] if p["instrument_type"] == "Equity Option")
    assert opt["delta"] == 0.0                      # included for NAV, greeks zeroed


# ---- Personal API key auth (clientId + consumerKey, no userId/userSecret) ----

def test_personal_signature_matches_snaptrade_reference():
    """Our request signing must equal SnapTrade's published algorithm exactly."""
    import hmac, json
    from base64 import b64encode
    from hashlib import sha256
    from deid_gateway.snaptrade_broker import sign_request

    ck = "YOUR_CONSUMER_KEY"
    content = {"userId": "api@passiv.com", "userSecret": "CHRIS.P.BACON"}
    path = "/api/v1/snapTrade/mockSignature"
    query = "clientId=PASSIVTEST&timestamp=1635790389"
    ref = b64encode(hmac.new(ck.encode(),
        json.dumps({"content": content, "path": path, "query": query},
                   separators=(",", ":"), sort_keys=True).encode(), sha256).digest()).decode()
    assert sign_request(ck, path, query, content) == ref


def test_personal_adapter_maps_holdings_and_computes_greeks(monkeypatch):
    from deid_gateway import snaptrade_broker as sb

    canned = {
        "/accounts": [{"id": "acc1", "number": "X1", "name": "Brokerage"}],
        "/accounts/acc1/balances": [{"cash": 10000.0, "currency": {"code": "USD"}}],
        "/accounts/acc1/positions": [
            {"symbol": {"symbol": {"symbol": "MSFT"}}, "units": 100, "price": 400.0}],
        "/accounts/acc1/options": [
            {"symbol": {"option_symbol": {
                "ticker": "AAPL  271217C00200000", "option_type": "CALL",
                "strike_price": 200.0, "expiration_date": "2027-12-17",
                "underlying_symbol": {"symbol": "AAPL"}}},
             "units": -2, "price": 35.0}],
        "/accounts/acc1/quotes": [{"symbol": "AAPL", "last_trade_price": 205.0}],
    }
    monkeypatch.setattr(sb._PersonalSigner, "get",
                        lambda self, path, params=None: canned[path])

    broker = sb.from_sdk("CID", "CKEY")  # personal: no user/secret; spot from SnapTrade quotes
    snaps = broker.fetch_snapshots()
    assert len(snaps) == 1
    snap = snaps[0]
    # equity + option present; NAV = cash + signed market values
    kinds = [p["instrument_type"] for p in snap["positions"]]
    assert "Equity" in kinds and "Equity Option" in kinds
    opt = next(p for p in snap["positions"] if p["instrument_type"] == "Equity Option")
    assert opt["delta"] != 0.0 and opt["gamma"] != 0.0      # local Black-Scholes fired
    assert opt["underlying_price"] == 205.0                  # spot from SnapTrade /quotes


def test_data_as_of_surfaced_and_passes_egress(monkeypatch):
    from deid_gateway import snaptrade_broker as sb
    from deid_gateway.deidentify import deidentify_snapshot, enforce_egress
    canned = {
        "/accounts": [{"id": "acc1", "number": "X1", "name": "B",
                       "sync_status": {"holdings": {"last_successful_sync": "2026-06-29T13:42:00Z"}}}],
        "/accounts/acc1/balances": [{"cash": 1000.0}],
        "/accounts/acc1/positions": [{"symbol": {"symbol": {"symbol": "MSFT"}}, "units": 10, "price": 400.0}],
        "/accounts/acc1/options": [],
    }
    monkeypatch.setattr(sb._PersonalSigner, "get", lambda self, path, params=None: canned[path])
    raw = sb.from_sdk("CID", "CKEY").fetch_snapshots()[0]
    snap = deidentify_snapshot(raw, "secret")
    assert snap["data_as_of"] == "2026-06-29T13:42:00Z"   # freshness surfaced
    enforce_egress(snap, raw)                              # timestamp is not PII -> passes
