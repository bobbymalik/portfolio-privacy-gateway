"""Local market reference data: ticker -> (beta, sector).

This is what makes the engine work for ANY user's portfolio. The engine itself
holds no ticker knowledge; the gateway enriches each position here, locally,
before de-identification. The data is bundled (refdata.json) so lookups are
fully offline -- no ticker ever leaves the machine at runtime.

Betas and sectors are public market facts, not proprietary; the proprietary part
(thresholds, scenarios, scoring weights) stays in the compiled engine.

Coverage is honest: an unknown ticker returns beta 1.0 / sector "Unknown" and is
marked known=False so the engine can report how much of the book it actually
understands.
"""

from __future__ import annotations

import json
import os

# Leveraged / inverse ETFs: vendor "beta" fields are unreliable for these, so we
# encode their structural leverage directly. General coverage of common products,
# not portfolio-specific. (factor, sector)
_LEVERAGED = {
    "TQQQ": (3.3, "Technology"),  "SQQQ": (-3.3, "Technology"),
    "SOXL": (4.2, "Technology"),  "SOXS": (-4.2, "Technology"),
    "UPRO": (3.0, "Broad Equity"), "SPXL": (3.0, "Broad Equity"),
    "SPXU": (-3.0, "Broad Equity"), "SPXS": (-3.0, "Broad Equity"), "SH": (-1.0, "Broad Equity"),
    "TNA": (3.45, "Small Cap"),   "TZA": (-3.45, "Small Cap"),
    "TMF": (-0.6, "Bonds"),       "TMV": (0.6, "Bonds"),
    "FAS": (3.3, "Financial Services"),   "FAZ": (-3.3, "Financial Services"),
    "LABU": (3.0, "Healthcare"), "LABD": (-3.0, "Healthcare"),
    "NUGT": (3.0, "Basic Materials"),   "DUST": (-3.0, "Basic Materials"),
    "UVXY": (-3.0, "Volatility"), "SVXY": (1.5, "Volatility"), "VXX": (-2.5, "Volatility"),
}

# Broad-market ETFs, sector ETFs, cash-equivalents, and common ADRs that vendor
# data mislabels or misses. These take precedence over the bundled FMP dataset.
# Each entry may set "sector" and/or "beta"; an omitted "beta" keeps the measured
# FMP beta (or 1.0 if unknown). Public market facts, not portfolio-specific.
#
# Why this matters: FMP dumps broad index funds (VOO, QQQ, SCHD) and money-market
# funds (SPAXX) into "Financial Services", inventing a sector cluster that isn't
# real. Cash-equivalents are reclassified to "Cash" (excluded from sector
# concentration); index funds to "Broad Equity"; sector ETFs to their true sector.
# Correlated market beta across all of these is captured by net_beta_dollars, not
# by sector concentration.
_OVERRIDES: dict[str, dict] = {
    # --- broad US equity index funds ---
    "VOO": {"sector": "Broad Equity", "beta": 1.0},
    "SPY": {"sector": "Broad Equity", "beta": 1.0},
    "IVV": {"sector": "Broad Equity", "beta": 1.0},
    "SPLG": {"sector": "Broad Equity", "beta": 1.0},
    "VTI": {"sector": "Broad Equity", "beta": 1.0},
    "ITOT": {"sector": "Broad Equity", "beta": 1.0},
    "DIA": {"sector": "Broad Equity", "beta": 0.98},
    "RSP": {"sector": "Broad Equity", "beta": 1.0},
    "VUG": {"sector": "Broad Equity", "beta": 1.1},
    "VTV": {"sector": "Broad Equity", "beta": 0.9},
    "SCHD": {"sector": "Broad Equity"},   # keep FMP beta (~0.6-0.8, dividend tilt)
    "VYM": {"sector": "Broad Equity", "beta": 0.82},
    "DVY": {"sector": "Broad Equity", "beta": 0.8},
    "SCHG": {"sector": "Broad Equity", "beta": 1.1},
    "QQQ": {"sector": "Technology", "beta": 1.15},   # Nasdaq-100, tech-dominated proxy
    "QQQM": {"sector": "Technology", "beta": 1.15},
    "IWM": {"sector": "Small Cap", "beta": 1.18},
    "IJR": {"sector": "Small Cap", "beta": 1.15},
    "MDY": {"sector": "Small Cap", "beta": 1.1},
    # --- international ---
    "VEA": {"sector": "International Equity", "beta": 0.9},
    "VWO": {"sector": "International Equity", "beta": 0.9},
    "EFA": {"sector": "International Equity", "beta": 0.9},
    "EEM": {"sector": "International Equity", "beta": 0.95},
    "VXUS": {"sector": "International Equity", "beta": 0.9},
    # --- sector ETFs ---
    "IGV": {"sector": "Technology", "beta": 1.2},   # software
    "SMH": {"sector": "Technology", "beta": 1.4},   # semis
    "SOXX": {"sector": "Technology", "beta": 1.4},
    "XLK": {"sector": "Technology", "beta": 1.15},
    "VGT": {"sector": "Technology", "beta": 1.15},
    "ARKK": {"sector": "Technology", "beta": 1.5},
    "XLF": {"sector": "Financial Services", "beta": 1.1},
    "XLE": {"sector": "Energy", "beta": 1.1},
    "XLV": {"sector": "Healthcare", "beta": 0.8},
    "XBI": {"sector": "Healthcare", "beta": 1.2},
    "XLY": {"sector": "Consumer Cyclical", "beta": 1.1},
    "XLP": {"sector": "Consumer Defensive", "beta": 0.6},
    "XLI": {"sector": "Industrials", "beta": 1.05},
    "XLU": {"sector": "Utilities", "beta": 0.5},
    "XLB": {"sector": "Basic Materials", "beta": 1.05},
    "XLRE": {"sector": "Real Estate", "beta": 0.9},
    "XLC": {"sector": "Communication Services", "beta": 1.05},
    # --- bonds / commodities ---
    "TLT": {"sector": "Bonds", "beta": -0.3},
    "IEF": {"sector": "Bonds", "beta": -0.2},
    "AGG": {"sector": "Bonds", "beta": 0.0},
    "BND": {"sector": "Bonds", "beta": 0.0},
    "LQD": {"sector": "Bonds", "beta": 0.1},
    "HYG": {"sector": "Bonds", "beta": 0.4},
    "GLD": {"sector": "Commodity", "beta": 0.1},
    "IAU": {"sector": "Commodity", "beta": 0.1},
    "SLV": {"sector": "Commodity", "beta": 0.2},
    # --- cash equivalents (T-bill ETFs + money-market funds) -> excluded from
    #     sector concentration; beta ~0 ---
    "SGOV": {"sector": "Cash", "beta": 0.0},
    "BIL": {"sector": "Cash", "beta": 0.0},
    "SHV": {"sector": "Cash", "beta": 0.0},
    "SHY": {"sector": "Cash", "beta": 0.0},
    "USFR": {"sector": "Cash", "beta": 0.0},
    "SPAXX": {"sector": "Cash", "beta": 0.0},
    "FDRXX": {"sector": "Cash", "beta": 0.0},
    "FZFXX": {"sector": "Cash", "beta": 0.0},
    "SWVXX": {"sector": "Cash", "beta": 0.0},
    "VMFXX": {"sector": "Cash", "beta": 0.0},
    "VMRXX": {"sector": "Cash", "beta": 0.0},
    "SPRXX": {"sector": "Cash", "beta": 0.0},
    # --- common ADRs / OTC that fall to Unknown under a US-only screener ---
    "BABA": {"sector": "Consumer Cyclical", "beta": 0.9},
    "JD": {"sector": "Consumer Cyclical", "beta": 1.0},
    "PDD": {"sector": "Consumer Cyclical", "beta": 1.1},
    "TSM": {"sector": "Technology", "beta": 1.1},
    "NVO": {"sector": "Healthcare", "beta": 0.7},
    "SE": {"sector": "Consumer Cyclical", "beta": 1.3},
    "SHOP": {"sector": "Technology", "beta": 1.6},
    "FNMA": {"sector": "Financial Services", "beta": 1.2},
    "FMCC": {"sector": "Financial Services", "beta": 1.2},
    "AMRN": {"sector": "Healthcare", "beta": 1.1},
}

_DEFAULT_BETA = 1.0
_DEFAULT_SECTOR = "Unknown"


class ReferenceData:
    def __init__(self, path: str | None = None) -> None:
        path = path or os.path.join(os.path.dirname(__file__), "refdata.json")
        try:
            with open(path) as f:
                self._tickers = json.load(f).get("tickers", {})
        except Exception:
            self._tickers = {}

    def lookup(self, ticker: str) -> dict:
        """Return {beta, sector, known} for an underlying ticker.

        Precedence: leveraged/inverse table -> override table (ETF/ADR/cash) ->
        bundled FMP dataset -> Unknown. An override that sets only "sector" keeps
        the measured FMP beta.
        """
        t = (ticker or "").upper().strip()
        if t in _LEVERAGED:
            beta, sector = _LEVERAGED[t]
            return {"beta": beta, "sector": sector, "known": True}
        rec = self._tickers.get(t)
        if t in _OVERRIDES:
            ov = _OVERRIDES[t]
            fmp_beta = float(rec["beta"]) if rec else _DEFAULT_BETA
            return {"beta": float(ov.get("beta", fmp_beta)),
                    "sector": ov.get("sector", _DEFAULT_SECTOR), "known": True}
        if rec:
            return {"beta": float(rec["beta"]), "sector": rec["sector"], "known": True}
        return {"beta": _DEFAULT_BETA, "sector": _DEFAULT_SECTOR, "known": False}
