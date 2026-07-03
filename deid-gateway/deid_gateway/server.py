"""MCP server: the surface Claude Desktop / Claude Code connect to (stdio).

Every tool is read-only and returns only de-identified data. There is
deliberately no tool that returns a raw identifier and no write tool, so the
worst a prompt-injection can do is make the model read more sanitized data.

Run directly:   python -m deid_gateway.server
"""

from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP

from .audit import AuditLog
from .broker import MockBroker, MultiBroker, TastytradeBroker
from .engine_client import RiskEngineClient, resolve_assessment
from . import local_engine
from .secrets import SecretStore
from .service import Gateway

mcp = FastMCP("deid-portfolio-gateway")

# Resolve everything through the secret store: AWS Secrets Manager when
# DEID_AWS_SECRET_ID is set, otherwise environment variables. No secret ever
# needs to live in the Claude Desktop config -- only pointers (region, secret id).
_store = SecretStore()

def _make_tastytrade():
    return TastytradeBroker(provider_secret=_store.get("TT_SECRET"),
                            refresh_token=_store.get("TT_REFRESH"))


def _make_snaptrade():
    from .snaptrade_broker import from_sdk
    return from_sdk(
        client_id=_store.get("SNAPTRADE_CLIENT_ID"),
        consumer_key=_store.get("SNAPTRADE_CONSUMER_KEY"),
        user_id=_store.get("SNAPTRADE_USER_ID"),
        user_secret=_store.get("SNAPTRADE_USER_SECRET"),
    )


_broker_kind = (_store.get("DEID_BROKER", "mock") or "mock").lower()
if _broker_kind == "tastytrade":
    _broker = _make_tastytrade()
elif _broker_kind == "snaptrade":
    _broker = _make_snaptrade()
elif _broker_kind in ("multi", "all", "auto"):
    # include every source that has credentials configured
    _sources = []
    if _store.get("TT_SECRET") and _store.get("TT_REFRESH"):
        _sources.append(_make_tastytrade())
    if _store.get("SNAPTRADE_CLIENT_ID") and _store.get("SNAPTRADE_CONSUMER_KEY"):
        _sources.append(_make_snaptrade())   # personal (no secret) or commercial
    _broker = MultiBroker(_sources) if _sources else MockBroker()
else:
    _broker = MockBroker()

# DEID_GATEWAY_SECRET may come from the keychain/Secrets Manager; if absent,
# Gateway falls back to its local generated-secret file (fine for the mock POC).
_gateway_secret = _store.get("DEID_GATEWAY_SECRET")

# Persistent encrypted audit log (key derived from the gateway secret).
try:
    _audit = AuditLog(_gateway_secret) if _gateway_secret else None
except Exception:
    _audit = None
_gateway = Gateway(broker=_broker, secret=_gateway_secret, audit=_audit)

# Proprietary WFS risk engine runs in the owner's AWS account; the gateway only
# sends it the de-identified snapshot and shows the result.
_engine = RiskEngineClient(_store.get("DEID_ENGINE_URL"),
                           _store.get("DEID_ENGINE_KEY_ID"),
                           _store.get("DEID_ENGINE_SECRET"))

# Concise startup status (no secrets) -> Claude's MCP log, useful for support.
print(f"[deid-gateway] ready: broker={_broker_kind} engine={'configured' if _engine.configured else 'not-configured'}",
      file=sys.stderr, flush=True)

_WFS_URL = "https://wealthfinancialsystem.com"


@mcp.tool()
def list_accounts() -> list[dict]:
    """List the connected accounts as opaque tokens (no real identifiers)."""
    return _gateway.list_accounts()


@mcp.tool()
def get_portfolio_snapshot(account_token: str | None = None) -> dict:
    """Full de-identified snapshot: normalized portfolio metrics + per-position
    weights and greeks, all as percentages of net liquidating value."""
    return _gateway.portfolio_snapshot(account_token)


@mcp.tool()
def get_risk_metrics(account_token: str | None = None) -> dict:
    """Portfolio-level risk only (no positions): net delta-dollars %, gross
    exposure %, theta/day %, vega, margin utilization %, concentration."""
    return _gateway.risk_metrics(account_token)


@mcp.tool()
def get_positions(account_token: str | None = None) -> list[dict]:
    """Per-position de-identified detail: symbol, underlying, type, and
    weight / delta-dollars / theta / vega each as % of NAV."""
    return _gateway.positions(account_token)


@mcp.tool()
def get_risk_assessment(account_token: str | None = None) -> dict:
    """Assess portfolio risk on a de-identified snapshot.

    Tiers, resolved from configuration:
      * WFS subscriber (DEID_ENGINE_* set) -> deterministic engine on AWS.
      * Local binary present (self-host/dev) -> deterministic local engine.
      * Otherwise (free) -> an AI-interpreted result with a subscribe notice; the
        model reasons over the metrics but there is no deterministic score.
    Only de-identified data is ever sent to the remote engine."""
    snapshot = _gateway.portfolio_snapshot(account_token)
    # Deterministic engine on an unchanged snapshot returns an identical score, so
    # serve repeats from cache -- protects the paid engine and returns instantly.
    cache_key = _gateway.snapshot_key("assessment", snapshot)
    cached = _gateway.cache_get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}
    result = resolve_assessment(
        snapshot, _engine,
        getattr(local_engine, "AVAILABLE", False),
        local_engine.assess, _WFS_URL,
    )
    _gateway.cache_put(cache_key, result)
    if _audit is not None:
        _audit.record(tool="get_risk_assessment",
                      account_token=snapshot.get("account_token"),
                      payload=snapshot, risk_score=result.get("risk_score"))
    return result


@mcp.tool()
def refresh_data() -> dict:
    """Re-pull fresh data from the broker into the local cache."""
    n = _gateway.refresh()
    return {"accounts_loaded": n}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
