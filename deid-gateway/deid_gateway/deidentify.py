"""De-identification core.

This is the trust boundary. Raw broker data goes in; only normalized,
tokenized, allow-listed data comes out. Everything that could re-identify a
person or account -- names, account numbers, exact dollar balances -- is either
dropped or converted to a dimensionless ratio before it can leave.

The two public entry points are:
    deidentify_snapshot(raw, secret) -> sanitized dict
    enforce_egress(sanitized, raw)   -> raises if anything unsafe slipped through
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re

from .refdata import ReferenceData

SCHEMA_VERSION = "deid-1"

_REFDATA = ReferenceData()   # bundled, offline ticker -> beta/sector

# Deny-by-default: only these keys may ever appear in data that leaves the gateway.
ALLOWED_PORTFOLIO_KEYS = {
    "schema_version",
    "account_token",
    "num_positions",
    "net_delta_dollars_pct",
    "gross_exposure_pct",
    "theta_day_pct",
    "vega_pct_per_vol_point",
    "margin_utilization_pct",
    "concentration_top1_pct",
    "concentration_top3_pct",
    "reference_coverage_pct",
    "data_as_of",
    "positions",
}
ALLOWED_POSITION_KEYS = {
    "symbol",
    "underlying",
    "instrument_type",
    "weight_pct",
    "delta_dollars_pct",
    "gamma_dollars_pct",
    "theta_day_pct",
    "vega_pct_per_vol_point",
    "beta",
    "sector",
}

# Fields on the raw record that must NEVER appear in output, in any form.
_PII_FIELDS = ("account_number", "account_holder", "nickname")


def tokenize_account(account_number: str, secret: str) -> str:
    """Stable, non-reversible pseudonym for an account.

    Same account always maps to the same token (so the model can reason about
    "account A" across calls), but the token reveals nothing about the original.
    """
    digest = hmac.new(secret.encode(), account_number.encode(), hashlib.sha256).hexdigest()
    return "acct_" + digest[:12]


def _multiplier(instrument_type: str) -> int:
    return 100 if "option" in instrument_type.lower() else 1


def deidentify_snapshot(raw: dict, secret: str) -> dict:
    """Convert one raw account snapshot into a sanitized, normalized snapshot.

    Money is expressed only as ratios against net liquidating value (NAV), which
    preserves the entire risk structure -- concentration, leverage, greeks
    balance -- while destroying the absolute dollar fingerprint.
    """
    acct = raw["account"]
    nav = float(acct["net_liquidating_value"])
    if nav <= 0:
        raise ValueError("NAV must be positive to normalize")

    pct = lambda x: round(100.0 * x / nav, 4)

    positions_out = []
    net_delta_dollars = 0.0
    gross_exposure = 0.0
    total_theta = 0.0
    total_vega = 0.0
    by_underlying: dict[str, float] = {}
    known_gross = 0.0

    for p in raw["positions"]:
        mult = _multiplier(p["instrument_type"])
        qty = float(p["quantity"])
        upx = float(p["underlying_price"])
        mv = float(p["market_value"])

        delta_dollars = float(p["delta"]) * qty * mult * upx
        # gamma-dollars: P&L curvature so the engine can do a gamma-adjusted shock.
        gamma_dollars = float(p.get("gamma", 0.0)) * qty * mult * upx * upx
        theta_day = float(p["theta"]) * qty * mult       # per-day decay in $
        vega = float(p["vega"]) * qty * mult             # $ per 1 vol point

        net_delta_dollars += delta_dollars
        gross_exposure += abs(mv)
        total_theta += theta_day
        total_vega += vega
        by_underlying[p["underlying"]] = by_underlying.get(p["underlying"], 0.0) + abs(mv)

        # Enrich locally with market reference data (beta/sector). Offline lookup;
        # tickers never leave the machine. Public facts, not proprietary.
        ref = _REFDATA.lookup(p["underlying"])
        if ref["known"]:
            known_gross += abs(mv)

        positions_out.append({
            "symbol": p["symbol"],
            "underlying": p["underlying"],
            "instrument_type": p["instrument_type"],
            "weight_pct": pct(mv),
            "delta_dollars_pct": pct(delta_dollars),
            "gamma_dollars_pct": pct(gamma_dollars),
            "theta_day_pct": pct(theta_day),
            "vega_pct_per_vol_point": pct(vega),
            "beta": ref["beta"],
            "sector": ref["sector"],
        })

    weights = sorted((100.0 * v / nav for v in by_underlying.values()), reverse=True)
    top1 = round(weights[0], 4) if weights else 0.0
    top3 = round(sum(weights[:3]), 4) if weights else 0.0

    sanitized = {
        "schema_version": SCHEMA_VERSION,
        "account_token": tokenize_account(acct["account_number"], secret),
        "num_positions": len(positions_out),
        "net_delta_dollars_pct": pct(net_delta_dollars),
        "gross_exposure_pct": pct(gross_exposure),
        "theta_day_pct": pct(total_theta),
        "vega_pct_per_vol_point": pct(total_vega),
        "margin_utilization_pct": pct(float(acct["maintenance_requirement"])),
        "concentration_top1_pct": top1,
        "concentration_top3_pct": top3,
        "reference_coverage_pct": round(100.0 * known_gross / gross_exposure, 2) if gross_exposure else 0.0,
        "data_as_of": acct.get("data_as_of"),   # holdings sync time; None => live
        "positions": positions_out,
    }
    return sanitized


def assert_allowlisted(sanitized: dict) -> dict:
    """Allowlist-only check (no raw comparison). Use before sending a snapshot to
    an external engine: guarantees only approved, de-identified keys leave."""
    _check_keys(sanitized)
    return sanitized


def enforce_egress(sanitized: dict, raw: dict) -> dict:
    """Backstop control. Run on every payload before it leaves the gateway.

    1. Allowlist conformance: deny-by-default on every key.
    2. PII scan: assert no raw identifier or exact dollar amount survived.
    Raises EgressViolation on any failure; returns the payload unchanged on pass.
    """
    _check_keys(sanitized)

    blob = json.dumps(sanitized)
    acct = raw["account"]

    for field in _PII_FIELDS:
        val = str(acct.get(field, "")).strip()
        if val and val in blob:
            raise EgressViolation(f"raw identifier '{field}' leaked into output")

    # Exact dollar amounts are quasi-identifiers; none should appear verbatim.
    # (Zero is not identifying and would false-match the many zeros in the
    # normalized output, e.g. accounts with no margin requirement.)
    for money in (acct["net_liquidating_value"], acct["cash_balance"],
                  acct["maintenance_requirement"]):
        amt = int(round(float(money)))
        if amt and re.search(rf"(?<!\d){amt}(?!\d)", blob):
            raise EgressViolation("an exact dollar amount leaked into output")

    return sanitized


def _check_keys(sanitized: dict) -> None:
    extra = set(sanitized) - ALLOWED_PORTFOLIO_KEYS
    if extra:
        raise EgressViolation(f"non-allowlisted portfolio keys: {sorted(extra)}")
    for pos in sanitized.get("positions", []):
        extra_p = set(pos) - ALLOWED_POSITION_KEYS
        if extra_p:
            raise EgressViolation(f"non-allowlisted position keys: {sorted(extra_p)}")


class EgressViolation(Exception):
    """Raised when a payload fails the egress allowlist or PII scan."""
