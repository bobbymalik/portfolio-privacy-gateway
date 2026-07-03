"""Broker connectors.

The gateway only ever calls Broker.fetch_snapshots(), which returns a list of
RAW account snapshots (with PII). De-identification happens downstream, so
connectors stay dumb: they just pull and return broker-native data.

POC ships with MockBroker. Swapping in a real broker is a one-class change --
see TastytradeBroker below for where the real SDK calls go.
"""

from __future__ import annotations

import asyncio
from typing import Callable, Protocol


def _run_async(coro_factory: Callable):
    """Run an async coroutine to completion whether or not an event loop is
    already running in this thread.

    FastMCP calls sync tool functions from inside its own event loop, where
    asyncio.run() raises "cannot be called from a running event loop". In that
    case we run the coroutine in a dedicated worker thread with its own loop.
    """
    import concurrent.futures

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())  # no loop running: safe to run directly
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(coro_factory())).result()


class Broker(Protocol):
    def fetch_snapshots(self) -> list[dict]:
        ...


class MultiBroker:
    """Fans out across several brokers (e.g. tastytrade + SnapTrade) and returns
    all their accounts as one list. A failure in one source is skipped so the
    others still return -- partial data beats no data for a combined portfolio."""

    def __init__(self, brokers: list) -> None:
        self._brokers = [b for b in brokers if b is not None]
        self.errors: list[str] = []

    def fetch_snapshots(self) -> list[dict]:
        out: list[dict] = []
        self.errors = []
        for b in self._brokers:
            try:
                out.extend(b.fetch_snapshots())
            except Exception as e:  # one broker down shouldn't sink the rest
                self.errors.append(f"{type(b).__name__}: {e}")
        return out


# A realistic raw snapshot: an SPY iron condor plus two singles, with full PII.
# This is exactly the shape a broker would hand you -- names, account numbers,
# exact balances and all -- and exactly what must NOT reach the AI.
_FIXTURE = [{
    "account": {
        "account_number": "5WT00123",
        "account_holder": "Bobby Malik",
        "nickname": "Roth IRA - active",
        "net_liquidating_value": 184729.34,
        "cash_balance": 23110.22,
        "maintenance_requirement": 41850.00,
    },
    "positions": [
        {"symbol": "SPY 250920P00540000", "underlying": "SPY", "instrument_type": "Equity Option",
         "quantity": -2, "underlying_price": 558.20, "market_value": -1340.0,
         "delta": -0.28, "gamma": 0.012, "theta": 9.4, "vega": -11.2},
        {"symbol": "SPY 250920P00530000", "underlying": "SPY", "instrument_type": "Equity Option",
         "quantity": 2, "underlying_price": 558.20, "market_value": 720.0,
         "delta": 0.18, "gamma": 0.009, "theta": -6.1, "vega": 8.0},
        {"symbol": "SPY 250920C00575000", "underlying": "SPY", "instrument_type": "Equity Option",
         "quantity": -2, "underlying_price": 558.20, "market_value": -1180.0,
         "delta": 0.24, "gamma": 0.011, "theta": 8.7, "vega": -10.4},
        {"symbol": "SPY 250920C00585000", "underlying": "SPY", "instrument_type": "Equity Option",
         "quantity": 2, "underlying_price": 558.20, "market_value": 640.0,
         "delta": -0.15, "gamma": 0.008, "theta": -5.3, "vega": 7.1},
        {"symbol": "NVDA", "underlying": "NVDA", "instrument_type": "Equity",
         "quantity": 120, "underlying_price": 134.10, "market_value": 16092.0,
         "delta": 1.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0},
        {"symbol": "AAPL 251017C00230000", "underlying": "AAPL", "instrument_type": "Equity Option",
         "quantity": -3, "underlying_price": 226.40, "market_value": -2010.0,
         "delta": 0.41, "gamma": 0.014, "theta": 11.2, "vega": -14.8},
    ],
}]


class MockBroker:
    """Returns the fixture above. Use this to validate the whole pipeline."""

    def fetch_snapshots(self) -> list[dict]:
        return _FIXTURE


class TastytradeBroker:
    """Real read-only tastytrade connector (SDK v12+).

    Pulls accounts, balances, positions, and live greeks, then maps each account
    into the same raw dict shape MockBroker returns. Nothing downstream changes.

    Auth uses tastytrade's OAuth flow: set the environment variables
        TT_SECRET   -- your OAuth provider secret
        TT_REFRESH  -- your personal refresh token
    (or pass them explicitly). This connector only ever reads -- it never places
    an order. Generate a personal OAuth grant in your tastytrade account settings.

    Greeks aren't on the positions endpoint; they come off the DXLink stream, so
    we resolve each option's streamer symbol and pull one live Greeks event per
    contract, plus one Quote per underlying for the underlying price.
    """

    _GREEKS_TIMEOUT = 4.0
    _QUOTE_TIMEOUT = 4.0

    def __init__(self, provider_secret: str | None = None, refresh_token: str | None = None) -> None:
        self._secret = provider_secret      # falls back to $TT_SECRET inside Session
        self._refresh = refresh_token       # falls back to $TT_REFRESH inside Session

    def fetch_snapshots(self) -> list[dict]:
        # FastMCP invokes sync tools from inside its own running event loop, so a
        # bare asyncio.run() would raise. _run_async handles both cases.
        return _run_async(self._fetch_async)

    async def _fetch_async(self) -> list[dict]:
        from tastytrade import Account, DXLinkStreamer, Session
        from tastytrade.dxfeed import Greeks, Quote
        from tastytrade.instruments import Equity, Option

        async with Session(self._secret, self._refresh) as session:
            accounts = await Account.get(session)
            if not isinstance(accounts, list):
                accounts = [accounts]

            snapshots: list[dict] = []
            for i, acct in enumerate(accounts, 1):
                label = f"account {i}/{len(accounts)}"  # positional, never a real identifier
                try:
                    bal = await acct.get_balances(session)
                except Exception as e:
                    raise RuntimeError(f"get_balances failed ({label}): {e}") from e
                try:
                    positions = await acct.get_positions(session, include_marks=True)
                except Exception as e:
                    raise RuntimeError(f"get_positions failed ({label}): {e}") from e

                # Resolve streamer symbols: greeks for equity options, quotes for
                # underlyings. Best-effort per symbol -- a holding this connector
                # can't look up (index, futures, crypto) is skipped, not fatal,
                # and simply falls back to mark price / zero greeks.
                opt_stream: dict[str, str] = {}        # position.symbol -> option streamer symbol
                for p in positions:
                    if str(p.instrument_type) == "Equity Option":
                        try:
                            instr = await Option.get(session, p.symbol)
                            if instr.streamer_symbol:
                                opt_stream[p.symbol] = instr.streamer_symbol
                        except Exception:
                            pass

                underlyings = sorted({p.underlying_symbol for p in positions if p.underlying_symbol})
                und_stream: dict[str, str] = {}        # underlying symbol -> equity streamer symbol
                for sym in underlyings:
                    try:
                        eq = await Equity.get(session, [sym])
                        eq = eq[0] if isinstance(eq, list) else eq
                        if eq and eq.streamer_symbol:
                            und_stream[sym] = eq.streamer_symbol
                    except Exception:
                        pass

                # One live event per symbol off the stream (with a timeout so a
                # closed market or missing quote never hangs the gateway).
                greeks: dict = {}
                quotes: dict = {}
                if opt_stream or und_stream:
                    try:
                        async with DXLinkStreamer(session) as streamer:
                            if opt_stream:
                                await streamer.subscribe(Greeks, list(opt_stream.values()))
                            if und_stream:
                                await streamer.subscribe(Quote, list(und_stream.values()))
                            greeks = await _collect(streamer, Greeks, opt_stream.values(), self._GREEKS_TIMEOUT)
                            quotes = await _collect(streamer, Quote, und_stream.values(), self._QUOTE_TIMEOUT)
                    except Exception:
                        pass  # no live greeks/quotes -> fall back to marks below

                snapshots.append(_build_raw(acct, bal, positions, opt_stream, und_stream, greeks, quotes))
            return snapshots


def _is_option(instrument_type) -> bool:
    return "option" in str(instrument_type).lower()


def _signed_quantity(p) -> float:
    """Shorts must be negative so directional delta-dollars come out right."""
    qty = float(p.quantity)
    direction = str(p.quantity_direction).lower()
    if direction == "short":
        return -qty
    if direction == "zero":
        return 0.0
    return qty


def _mid(quote) -> float | None:
    if quote is None or quote.bid_price is None or quote.ask_price is None:
        return None
    return (float(quote.bid_price) + float(quote.ask_price)) / 2.0


async def _collect(streamer, event_class, symbols, timeout: float) -> dict:
    """Pull one event per symbol, or return whatever arrived before the timeout."""
    import asyncio as _asyncio

    wanted = set(symbols)
    got: dict = {}
    if not wanted:
        return got

    async def _inner() -> None:
        async for event in streamer.listen(event_class):
            got[event.event_symbol] = event
            if wanted.issubset(got):
                return

    try:
        await _asyncio.wait_for(_inner(), timeout)
    except (_asyncio.TimeoutError, TimeoutError):
        pass
    return got


def _build_raw(acct, bal, positions, opt_stream, und_stream, greeks, quotes) -> dict:
    """Map tastytrade objects into the raw dict shape the de-id core expects:
    signed quantities, per-contract greeks, signed market value, underlying price."""
    raw_positions = []
    for p in positions:
        mult = int(p.multiplier or (100 if _is_option(p.instrument_type) else 1))
        mark_price = float(p.mark_price) if p.mark_price is not None else float(p.close_price or 0.0)
        signed_qty = _signed_quantity(p)
        market_value = signed_qty * mult * mark_price

        underlying_price = _mid(quotes.get(und_stream.get(p.underlying_symbol))) or mark_price or 0.0

        itype = str(p.instrument_type)
        if itype == "Equity Option":
            g = greeks.get(opt_stream.get(p.symbol))
            delta = float(g.delta) if g else 0.0
            gamma = float(g.gamma) if g else 0.0
            theta = float(g.theta) if g else 0.0
            vega = float(g.vega) if g else 0.0
        elif itype == "Equity":
            delta, gamma, theta, vega = 1.0, 0.0, 0.0, 0.0
        else:
            # Futures, futures options, crypto: not modeled here -> neutral so
            # they don't misstate exposure. Still counted in weight / gross.
            delta, gamma, theta, vega = 0.0, 0.0, 0.0, 0.0

        raw_positions.append({
            "symbol": p.symbol,
            "underlying": p.underlying_symbol or p.symbol,
            "instrument_type": str(p.instrument_type),
            "quantity": signed_qty,
            "underlying_price": float(underlying_price),
            "market_value": float(market_value),
            "delta": delta, "gamma": gamma, "theta": theta, "vega": vega,
        })

    return {
        "account": {
            "account_number": acct.account_number,
            "account_holder": "",                                  # legal name is never fetched, so it can't leak
            "nickname": getattr(acct, "nickname", "") or "",       # scrubbed by the egress PII guard
            "net_liquidating_value": float(bal.net_liquidating_value),
            "cash_balance": float(bal.cash_balance),
            "maintenance_requirement": float(bal.maintenance_requirement),
        },
        "positions": raw_positions,
    }
