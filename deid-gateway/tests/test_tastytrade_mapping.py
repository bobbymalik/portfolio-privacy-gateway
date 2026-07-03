"""Offline validation of the tastytrade connector's mapping logic.

We can't reach the live tastytrade API from here, but the pure mapping functions
(_build_raw, _signed_quantity, _mid) are testable with fake SDK objects. The key
assertion: connector output flows cleanly through the de-id boundary.
"""

from decimal import Decimal
from types import SimpleNamespace

import asyncio

from deid_gateway.broker import _build_raw, _is_option, _mid, _run_async, _signed_quantity
from deid_gateway.deidentify import deidentify_snapshot, enforce_egress

SECRET = "test-secret-fixed"


def test_run_async_from_sync_context():
    async def coro():
        return 7
    assert _run_async(lambda: coro()) == 7


def test_run_async_from_running_loop():
    # Reproduces the FastMCP case: a sync call made from inside a running loop.
    async def coro():
        return 9

    async def driver():
        return _run_async(lambda: coro())   # would raise without the fix

    assert asyncio.run(driver()) == 9


def _pos(**kw):
    base = dict(close_price=Decimal("0"), multiplier=1)
    base.update(kw)
    return SimpleNamespace(**base)


def test_signed_quantity_and_mid():
    short = _pos(quantity=Decimal("2"), quantity_direction="Short")
    long = _pos(quantity=Decimal("120"), quantity_direction="Long")
    assert _signed_quantity(short) == -2
    assert _signed_quantity(long) == 120
    q = SimpleNamespace(bid_price=Decimal("558.0"), ask_price=Decimal("558.4"))
    assert _mid(q) == 558.2
    assert _mid(None) is None
    assert _is_option("Equity Option") and not _is_option("Equity")


def test_connector_output_passes_the_boundary():
    acct = SimpleNamespace(account_number="5WT00123", nickname="Roth IRA - active")
    bal = SimpleNamespace(
        net_liquidating_value=Decimal("184729.34"),
        cash_balance=Decimal("23110.22"),
        maintenance_requirement=Decimal("41850"),
    )
    positions = [
        _pos(symbol="SPY 250920P00540000", instrument_type="Equity Option",
             underlying_symbol="SPY", quantity=Decimal("2"), quantity_direction="Short",
             multiplier=100, mark_price=Decimal("6.70")),
        _pos(symbol="NVDA", instrument_type="Equity",
             underlying_symbol="NVDA", quantity=Decimal("120"), quantity_direction="Long",
             multiplier=1, mark_price=Decimal("134.10")),
    ]
    opt_stream = {"SPY 250920P00540000": ".SPY250920P540"}
    und_stream = {"SPY": "SPY", "NVDA": "NVDA"}
    greeks = {".SPY250920P540": SimpleNamespace(
        event_symbol=".SPY250920P540",
        delta=Decimal("-0.28"), gamma=Decimal("0.012"),
        theta=Decimal("9.4"), vega=Decimal("-11.2"))}
    quotes = {
        "SPY": SimpleNamespace(event_symbol="SPY", bid_price=Decimal("558.0"), ask_price=Decimal("558.4")),
        "NVDA": SimpleNamespace(event_symbol="NVDA", bid_price=Decimal("134.0"), ask_price=Decimal("134.2")),
    }

    raw = _build_raw(acct, bal, positions, opt_stream, und_stream, greeks, quotes)

    # Mapping is correct: short option is negative, market values are signed.
    opt = raw["positions"][0]
    assert opt["quantity"] == -2
    assert opt["market_value"] == -2 * 100 * 6.70
    assert opt["underlying_price"] == 558.2
    assert opt["delta"] == -0.28
    eq = raw["positions"][1]
    assert eq["quantity"] == 120 and eq["market_value"] == 120 * 134.10 and eq["delta"] == 1.0

    # The whole point: connector output is accepted by the de-id boundary,
    # and nothing identifiable survives.
    sanitized = deidentify_snapshot(raw, SECRET)
    enforce_egress(sanitized, raw)                       # raises on any leak
    import json
    blob = json.dumps(sanitized)
    assert "5WT00123" not in blob and "Roth IRA - active" not in blob
    assert "184729" not in blob
    assert sanitized["account_token"].startswith("acct_")
