"""SnapTrade broker connector.

SnapTrade aggregates ~20+ brokerages through one API, but returns option
*positions* without greeks. This connector pulls holdings via a thin adapter,
computes per-option greeks locally (deid_gateway.greeks), and emits the same raw
snapshot shape every other broker uses, so the de-id core and engine are
unchanged.

The adapter boundary keeps this testable and SDK-version-agnostic: the broker
consumes already-normalized holdings, so the (uncertain, evolving) SnapTrade JSON
parsing lives in one small place (SnapTradeAdapter) that you confirm against your
live account.

Limits worth knowing:
  * Greeks are Black-Scholes model values fit to each option's mark -- close to,
    not identical to, a broker's published greeks.
  * Options need an underlying spot price; if the underlying isn't also held as
    stock and no quote source is configured, that option is included for
    weight/NAV but its greeks are zero (counted as "unpriced").
  * Margin maintenance is often not exposed by aggregators; margin utilization
    may read 0 for SnapTrade-connected accounts.
"""

from __future__ import annotations

import datetime as _dt
import os
from typing import Callable, Protocol

from . import greeks as _greeks

_RISK_FREE = float(os.environ.get("DEID_RISK_FREE", "0.045"))


class SnapTradeAdapter(Protocol):
    """Normalized data access. The real implementation wraps the SnapTrade SDK
    (+ a spot-quote source); a fake implements the same shape for tests."""
    def list_accounts(self) -> list[dict]: ...            # [{id, number, name}]
    def cash(self, account_id: str) -> float: ...
    def equity_positions(self, account_id: str) -> list[dict]: ...   # [{ticker, units, price}]
    def option_positions(self, account_id: str) -> list[dict]: ...   # [{underlying, occ, option_type, strike, expiry, units, price}]
    def quotes(self, account_id: str, tickers: list[str]) -> dict: ...  # {ticker: last_price}


class SnapTradeBroker:
    def __init__(self, adapter: SnapTradeAdapter, risk_free: float | None = None) -> None:
        self._a = adapter
        self._r = _RISK_FREE if risk_free is None else risk_free
        self.unpriced_options = 0

    def fetch_snapshots(self) -> list[dict]:
        return [self._snapshot(a) for a in self._a.list_accounts()]

    # ------------------------------------------------------------------
    def _snapshot(self, acct: dict) -> dict:
        aid = acct["id"]
        equities = self._a.equity_positions(aid)
        options = self._a.option_positions(aid)
        cash = float(self._a.cash(aid))

        spot_cache = {e["ticker"].upper(): float(e["price"]) for e in equities}

        # Underlyings we need a spot for but don't hold as stock: ask SnapTrade's
        # own quotes endpoint (same brokerage connection, no external data source).
        needed = sorted({o["underlying"].upper() for o in options
                         if o["underlying"] and o["underlying"].upper() not in spot_cache})
        if needed:
            spot_cache.update(self._quotes(aid, needed))

        positions: list[dict] = []
        gross = cash  # NAV = cash + signed market values

        for e in equities:
            units, price = float(e["units"]), float(e["price"])
            mv = units * price
            gross += mv
            positions.append({
                "symbol": e["ticker"], "underlying": e["ticker"].upper(),
                "instrument_type": "Equity", "quantity": units,
                "underlying_price": price, "market_value": mv,
                "delta": 1.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0,
            })

        today = _dt.date.today()
        for o in options:
            units, price = float(o["units"]), float(o["price"])
            mv = units * price * 100.0
            gross += mv
            under = o["underlying"].upper()
            spot = spot_cache.get(under)
            g = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
            if spot:
                T = max((_dt.date.fromisoformat(o["expiry"]) - today).days, 0) / 365.0
                computed = _greeks.greeks_from_price(
                    price, spot, float(o["strike"]), T, self._r, o["option_type"].upper())
                if computed:
                    g = computed
                else:
                    self.unpriced_options += 1
            else:
                self.unpriced_options += 1
            positions.append({
                "symbol": o["occ"], "underlying": under,
                "instrument_type": "Equity Option", "quantity": units,
                "underlying_price": float(spot or 0.0), "market_value": mv,
                "delta": g["delta"], "gamma": g["gamma"],
                "theta": g["theta"], "vega": g["vega"],
            })

        return {
            "account": {
                "account_number": str(acct.get("number") or acct["id"]),
                "account_holder": acct.get("name", ""),
                "nickname": acct.get("name", ""),
                "data_as_of": acct.get("as_of"),   # SnapTrade holdings sync time
                "net_liquidating_value": gross,
                "cash_balance": cash,
                # aggregators rarely expose maintenance margin; 0 => no margin flag
                "maintenance_requirement": float(acct.get("maintenance", 0.0)),
            },
            "positions": positions,
        }

    def _quotes(self, account_id: str, tickers: list[str]) -> dict:
        try:
            return {k.upper(): float(v) for k, v in
                    self._a.quotes(account_id, tickers).items() if v}
        except Exception:
            return {}


# ----------------------------------------------------------------------
# SnapTrade Personal API key auth.
# Verified against the official SDK (snaptrade-typescript-sdk v11 canary):
# personalApiKey signs requests EXACTLY like commercialApiKey -- consumerKey
# HMAC-SHA256, PartnerSignature + PartnerTimestamp -- the only difference is it
# sends just clientId (no userId/userSecret); the server resolves the single
# implicit user provisioned with the Personal key at signup. So no OAuth, no
# register: clientId + consumerKey is sufficient for read access.
class _PersonalSigner:
    BASE = "https://api.snaptrade.com/api/v1"

    def __init__(self, client_id: str, consumer_key: str) -> None:
        self._client_id = client_id
        self._consumer_key = consumer_key

    def get(self, path: str, params: dict | None = None) -> object:
        import json, time, urllib.parse, urllib.request
        q = {"clientId": self._client_id, "timestamp": str(int(time.time()))}
        if params:
            q.update(params)
        query = urllib.parse.urlencode(q)
        signature = sign_request(self._consumer_key, f"/api/v1{path}", query, None)
        url = f"{self.BASE}{path}?{query}"
        req = urllib.request.Request(
            url, headers={"Signature": signature, "Accept": "application/json",
                          "User-Agent": "deid-gateway/personal"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())


def sign_request(consumer_key: str, path: str, query: str, content: object = None) -> str:
    """SnapTrade request signature. Matches the official SDK and SnapTrade's
    published recipe exactly: HMAC-SHA256 over the compact, key-sorted JSON of
    {content, path, query}, keyed by encodeURI(consumerKey), base64-encoded.
    `path` includes the /api/v1 prefix; `query` is the querystring without '?'."""
    import base64, hashlib, hmac, json, urllib.parse
    key = urllib.parse.quote(consumer_key, safe="-_.!~*'();,/?:@&=+$#")  # encodeURI
    sig_content = json.dumps({"content": content, "path": path, "query": query},
                             separators=(",", ":"), sort_keys=True)
    return base64.b64encode(
        hmac.new(key.encode(), sig_content.encode(), hashlib.sha256).digest()).decode()


def _personal_adapter(client_id, consumer_key):
    sign = _PersonalSigner(client_id, consumer_key)

    class _PersonalAdapter:
        def list_accounts(self):
            out = []
            for a in sign.get("/accounts"):
                sync = (((a.get("sync_status") or {}).get("holdings") or {})
                        .get("last_successful_sync"))
                out.append({"id": a["id"], "number": a.get("number"),
                            "name": a.get("name", ""), "as_of": sync})
            return out

        def cash(self, account_id):
            bals = sign.get(f"/accounts/{account_id}/balances")
            return sum(float(b.get("cash") or 0.0) for b in bals)

        def equity_positions(self, account_id):
            out = []
            for p in sign.get(f"/accounts/{account_id}/positions"):
                sym = (((p.get("symbol") or {}).get("symbol") or {}).get("symbol")) or ""
                if sym:
                    out.append({"ticker": sym, "units": p.get("units") or 0.0,
                                "price": p.get("price") or 0.0})
            return out

        def option_positions(self, account_id):
            out = []
            for p in sign.get(f"/accounts/{account_id}/options"):
                opt = (p.get("symbol") or {}).get("option_symbol") or {}
                out.append({
                    "underlying": (opt.get("underlying_symbol") or {}).get("symbol", ""),
                    "occ": opt.get("ticker", ""),
                    "option_type": opt.get("option_type", "CALL"),
                    "strike": opt.get("strike_price") or 0.0,
                    "expiry": str(opt.get("expiration_date", ""))[:10],
                    "units": p.get("units") or 0.0,
                    "price": p.get("price") or 0.0,
                })
            return out

        def quotes(self, account_id, tickers):
            if not tickers:
                return {}
            rows = sign.get(f"/accounts/{account_id}/quotes",
                            {"symbols": ",".join(tickers), "use_ticker": "true"})
            return _parse_quotes(rows)

    return _PersonalAdapter()


def _parse_quotes(rows) -> dict:
    """Map SnapTrade /quotes rows -> {ticker: price}. Prefers last trade, falls
    back to bid/ask mid. Works whether the symbol field is a string or a nested
    UniversalSymbol."""
    out = {}
    for q in (rows or []):
        sym = q.get("symbol")
        if isinstance(sym, dict):
            sym = sym.get("symbol") or sym.get("raw_symbol")
        if not sym:
            continue
        price = q.get("last_trade_price")
        if not price:
            bid, ask = q.get("bid_price"), q.get("ask_price")
            if bid and ask:
                price = (float(bid) + float(ask)) / 2.0
        if price:
            out[str(sym).upper()] = float(price)
    return out


# ----------------------------------------------------------------------
def from_sdk(client_id: str, consumer_key: str, user_id: str | None = None,
             user_secret: str | None = None) -> SnapTradeBroker:
    """Build a SnapTrade-backed broker.

    Auth mode is chosen by what you have:
      * Personal key  (clientId + consumerKey, NO userId/userSecret) -> signed
        HTTP with the implicit user. This is the free Personal-key path.
      * Commercial key (clientId + consumerKey + userId + userSecret) -> official
        SDK with explicit user.
    Greeks are computed locally (SnapTrade returns none); the underlying spot they
    need comes from SnapTrade's own /quotes endpoint -- no external data source.
    """
    if not user_secret:    # Personal key: implicit user, signed HTTP
        return SnapTradeBroker(_personal_adapter(client_id, consumer_key))

    # Commercial key: explicit user via the official SDK
    from snaptrade_client import SnapTrade  # pip install snaptrade-python-sdk

    sdk = SnapTrade(consumer_key=consumer_key, client_id=client_id)

    def _accounts():
        resp = sdk.account_information.list_user_accounts(user_id=user_id, user_secret=user_secret)
        out = []
        for a in resp.body:
            sync = (((a.get("sync_status") or {}).get("holdings") or {})
                    .get("last_successful_sync"))
            out.append({"id": a["id"], "number": a.get("number"),
                        "name": a.get("name", ""), "as_of": sync})
        return out

    def _positions(account_id):
        resp = sdk.account_information.get_user_account_positions(
            user_id=user_id, user_secret=user_secret, account_id=account_id)
        return resp.body

    def _options(account_id):
        resp = sdk.options.list_option_holdings(
            user_id=user_id, user_secret=user_secret, account_id=account_id)
        return resp.body

    def _balances(account_id):
        resp = sdk.account_information.get_user_account_balance(
            user_id=user_id, user_secret=user_secret, account_id=account_id)
        return resp.body

    class _SDKAdapter:
        def list_accounts(self):
            return _accounts()

        def cash(self, account_id):
            return sum(float(b.get("cash") or 0.0) for b in _balances(account_id))

        def equity_positions(self, account_id):
            out = []
            for p in _positions(account_id):
                sym = (((p.get("symbol") or {}).get("symbol") or {}).get("symbol")) or ""
                if sym:
                    out.append({"ticker": sym, "units": p.get("units") or 0.0,
                                "price": p.get("price") or 0.0})
            return out

        def option_positions(self, account_id):
            out = []
            for p in _options(account_id):
                osym = p.get("symbol") or {}
                opt = osym.get("option_symbol") or {}
                out.append({
                    "underlying": (opt.get("underlying_symbol") or {}).get("symbol", ""),
                    "occ": opt.get("ticker", ""),
                    "option_type": opt.get("option_type", "CALL"),
                    "strike": opt.get("strike_price") or 0.0,
                    "expiry": opt.get("expiration_date", "")[:10],
                    "units": p.get("units") or 0.0,
                    "price": p.get("price") or 0.0,
                })
            return out

        def quotes(self, account_id, tickers):
            if not tickers:
                return {}
            resp = sdk.trading.get_user_account_quotes(
                user_id=user_id, user_secret=user_secret, account_id=account_id,
                symbols=",".join(tickers), use_ticker=True)
            return _parse_quotes(resp.body)

    return SnapTradeBroker(_SDKAdapter())
