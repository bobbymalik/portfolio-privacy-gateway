"""Local option greeks (Black-Scholes).

SnapTrade and most holdings aggregators return option *positions* (strike, expiry,
type, price) but NOT greeks. The engine needs greeks, so we compute them here:
back out implied volatility from the option's market price, then derive the
greeks analytically.

Conventions match what the de-id core expects (same as the tastytrade feed):
  delta  : per share
  gamma  : per share, per $1 underlying move
  theta  : per share, per CALENDAR DAY, in option-price units (negative = decay)
  vega   : per share, per 1 IMPLIED-VOL POINT (i.e. per 1%, not per 1.0)

Honest limit: these are model values from a Black-Scholes fit to the option's
mark. They will not exactly equal a broker's published greeks (different vol
surface, rate, dividend assumptions). Good enough for portfolio-level risk, not
for precise hedging.
"""

from __future__ import annotations

import math

_SQRT_2PI = math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def _d1_d2(S, K, T, r, sigma, q=0.0):
    vt = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vt
    return d1, d1 - vt


def bs_price(S, K, T, r, sigma, option_type, q=0.0) -> float:
    if T <= 0 or sigma <= 0:
        # intrinsic value at/after expiry
        return max(0.0, (S - K) if option_type == "CALL" else (K - S))
    d1, d2 = _d1_d2(S, K, T, r, sigma, q)
    if option_type == "CALL":
        return S * math.exp(-q * T) * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * math.exp(-q * T) * _norm_cdf(-d1)


def implied_vol(price, S, K, T, r, option_type, q=0.0) -> float | None:
    """Solve for implied vol via bisection. Returns None if no sane solution."""
    if T <= 0 or price <= 0:
        return None
    intrinsic = max(0.0, (S - K) if option_type == "CALL" else (K - S))
    if price < intrinsic - 1e-6:
        return None
    lo, hi = 1e-4, 5.0
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        diff = bs_price(S, K, T, r, mid, option_type, q) - price
        if abs(diff) < 1e-6:
            return mid
        if diff > 0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def greeks(S, K, T, r, sigma, option_type, q=0.0) -> dict:
    """Per-share greeks in the de-id/tastytrade convention."""
    if T <= 0 or sigma <= 0:
        delta = (1.0 if S > K else 0.0) if option_type == "CALL" else (-1.0 if S < K else 0.0)
        return {"delta": delta, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    d1, d2 = _d1_d2(S, K, T, r, sigma, q)
    pdf = _norm_pdf(d1)
    disc_q = math.exp(-q * T)
    gamma = disc_q * pdf / (S * sigma * math.sqrt(T))
    vega_per_pt = S * disc_q * pdf * math.sqrt(T) / 100.0
    if option_type == "CALL":
        delta = disc_q * _norm_cdf(d1)
        theta_yr = (-(S * disc_q * pdf * sigma) / (2 * math.sqrt(T))
                    - r * K * math.exp(-r * T) * _norm_cdf(d2)
                    + q * S * disc_q * _norm_cdf(d1))
    else:
        delta = disc_q * (_norm_cdf(d1) - 1.0)
        theta_yr = (-(S * disc_q * pdf * sigma) / (2 * math.sqrt(T))
                    + r * K * math.exp(-r * T) * _norm_cdf(-d2)
                    - q * S * disc_q * _norm_cdf(-d1))
    return {"delta": delta, "gamma": gamma, "theta": theta_yr / 365.0, "vega": vega_per_pt}


def greeks_from_price(market_price, S, K, T, r, option_type, q=0.0) -> dict | None:
    """Back out IV from the option mark, then return per-share greeks (+ the iv)."""
    iv = implied_vol(market_price, S, K, T, r, option_type, q)
    if iv is None:
        return None
    g = greeks(S, K, T, r, iv, option_type, q)
    g["iv"] = iv
    return g
