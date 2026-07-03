"""Gateway service: the part that holds raw data and serves sanitized data.

Design notes that matter:
- The RAW cache and the token->account re-identification map live here and NEVER
  leave this object. Only deidentify_snapshot() output is ever returned.
- MCP tool calls read from the in-memory cache (fast); the broker round-trip is
  decoupled into refresh(), so tool latency does not depend on the broker API.
- Every sanitized payload is passed through enforce_egress() and recorded in an
  append-only audit log (payload hash only) before it can be returned.
"""

from __future__ import annotations

import hashlib
import json
import os
import time

from .broker import Broker, MockBroker
from .deidentify import deidentify_snapshot, enforce_egress, tokenize_account

# Sentinel token for the whole-portfolio aggregate view (not a real account).
AGGREGATE_TOKEN = "portfolio_all"


def _load_secret() -> str:
    """POC secret handling. Production: OS keychain (.mcpb marks fields sensitive)."""
    secret = os.environ.get("DEID_GATEWAY_SECRET")
    if secret:
        return secret
    path = os.path.expanduser("~/.deid_gateway/secret")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        return open(path).read().strip()
    secret = hashlib.sha256(os.urandom(32)).hexdigest()
    with open(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600), "w") as f:
        f.write(secret)
    return secret


class Gateway:
    def __init__(self, broker: Broker | None = None, secret: str | None = None,
                 audit=None, cache_ttl: float | None = None) -> None:
        self._broker = broker or MockBroker()
        self._secret = secret or _load_secret()
        self._raw_by_account: dict[str, dict] = {}     # real account_number -> raw
        self._account_by_token: dict[str, str] = {}     # token -> real account_number
        self._refreshed_at: float | None = None
        self._audit: list[dict] = []
        self._audit_log = audit                         # optional persistent encrypted log
        # How long a broker pull (and any derived result) is reused before we
        # re-pull. Bounds both data staleness and cost (broker pulls + engine
        # calls). Configurable via DEID_CACHE_TTL (seconds); default 3600 (60 min).
        if cache_ttl is None:
            try:
                cache_ttl = float(os.environ.get("DEID_CACHE_TTL", "3600"))
            except ValueError:
                cache_ttl = 3600.0
        self._cache_ttl = cache_ttl
        self._result_cache: dict[str, tuple[float, dict]] = {}

    # ---- raw side (never exposed) ----
    def refresh(self) -> int:
        """Pull fresh raw snapshots from the broker into the cache."""
        self._raw_by_account.clear()
        self._account_by_token.clear()
        self._result_cache.clear()          # new data invalidates cached results
        for snap in self._broker.fetch_snapshots():
            acct_no = snap["account"]["account_number"]
            token = tokenize_account(acct_no, self._secret)
            self._raw_by_account[acct_no] = snap
            self._account_by_token[token] = acct_no
        self._refreshed_at = time.time()
        return len(self._raw_by_account)

    def _stale(self) -> bool:
        return (self._cache_ttl > 0 and self._refreshed_at is not None
                and (time.time() - self._refreshed_at) > self._cache_ttl)

    def _ensure_loaded(self) -> None:
        if not self._raw_by_account or self._stale():
            self.refresh()

    # ---- result cache (protects the paid engine + avoids recompute) ----
    def cache_get(self, key: str) -> dict | None:
        hit = self._result_cache.get(key)
        if hit is None:
            return None
        ts, value = hit
        if self._cache_ttl <= 0 or (time.time() - ts) > self._cache_ttl:
            return None
        return value

    def cache_put(self, key: str, value: dict) -> None:
        self._result_cache[key] = (time.time(), value)

    @staticmethod
    def snapshot_key(prefix: str, snapshot: dict) -> str:
        h = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
        return f"{prefix}:{h}"

    # ---- sanitized side (the only thing tools return) ----
    def _emit(self, sanitized: dict, raw: dict, tool: str) -> dict:
        enforce_egress(sanitized, raw)            # allowlist + PII scan
        entry = {
            "ts": time.time(),
            "tool": tool,
            "account_token": sanitized.get("account_token"),
            "payload_sha256": hashlib.sha256(json.dumps(sanitized, sort_keys=True).encode()).hexdigest(),
        }
        self._audit.append(entry)
        if self._audit_log is not None:
            self._audit_log.record(tool=tool, account_token=sanitized.get("account_token"),
                                   payload=sanitized)
        return sanitized

    def list_accounts(self) -> list[dict]:
        self._ensure_loaded()
        out = [{"account_token": tok} for tok in self._account_by_token]
        if len(out) >= 2:   # offer the combined view only when there's >1 account
            out.append({"account_token": AGGREGATE_TOKEN, "aggregate": True,
                        "covers_accounts": len(out)})
        return out

    def _combined_raw(self) -> dict:
        """Merge all cached accounts into one raw portfolio. Summing dollar values
        and concatenating positions, then normalizing against the TOTAL NAV
        downstream, is what makes the aggregate percentages correct -- you cannot
        average per-account percentages."""
        self._ensure_loaded()
        nav = cash = maint = 0.0
        positions: list[dict] = []
        as_ofs: list[str] = []
        for raw in self._raw_by_account.values():
            a = raw["account"]
            nav += float(a["net_liquidating_value"])
            cash += float(a["cash_balance"])
            maint += float(a["maintenance_requirement"])
            if a.get("data_as_of"):
                as_ofs.append(a["data_as_of"])
            positions.extend(raw["positions"])
        return {
            "account": {
                "account_number": "__PORTFOLIO_ALL__", "account_holder": "", "nickname": "",
                "data_as_of": min(as_ofs) if as_ofs else None,   # stalest source
                "net_liquidating_value": nav, "cash_balance": cash,
                "maintenance_requirement": maint,
            },
            "positions": positions,
        }

    def _resolve(self, account_token: str | None) -> dict:
        self._ensure_loaded()
        if account_token == AGGREGATE_TOKEN:
            return self._combined_raw()
        if account_token is None:
            account_token = next(iter(self._account_by_token))
        acct_no = self._account_by_token.get(account_token)
        if acct_no is None:
            raise KeyError(f"unknown account_token: {account_token}")
        return self._raw_by_account[acct_no]

    def _snapshot(self, account_token: str | None) -> dict:
        raw = self._resolve(account_token)
        snap = deidentify_snapshot(raw, self._secret)
        if account_token == AGGREGATE_TOKEN:
            snap["account_token"] = AGGREGATE_TOKEN     # readable label, not a real token
        return snap, raw

    def portfolio_snapshot(self, account_token: str | None = None) -> dict:
        snap, raw = self._snapshot(account_token)
        return self._emit(snap, raw, "portfolio_snapshot")

    def risk_metrics(self, account_token: str | None = None) -> dict:
        snap, raw = self._snapshot(account_token)
        metrics = {k: v for k, v in snap.items() if k != "positions"}
        # metrics is a strict subset of allow-listed keys, so egress still passes
        return self._emit(metrics, raw, "risk_metrics")

    def positions(self, account_token: str | None = None) -> list[dict]:
        snap, raw = self._snapshot(account_token)
        return self._emit(snap, raw, "positions")["positions"]

    def audit_log(self) -> list[dict]:
        return list(self._audit)
