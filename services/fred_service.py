"""
FRED (Federal Reserve Economic Data) macro regime service.

Derives a RISK_ON / NEUTRAL / RISK_OFF label from 3 macro signals:
  1. Yield curve: 10Y Treasury − 2Y Treasury spread (T10Y2Y)
     - Inverted (<0) = recession risk → bearish for risk assets
     - Steep (>0.5) = healthy expansion → supports risk-on
  2. Fed Funds effective rate (DFF)
     - Rising rapidly (>5% and trending up) = tight conditions → headwind
  3. CPI YoY inflation trend (CPIAUCSL, monthly)
     - Used to judge whether Fed is likely to cut or hold

Regime logic:
  RISK_OFF  — yield curve inverted AND (rate > 5% OR recent CPI > 4%)
  RISK_ON   — yield curve steep (>0.5) AND rate trending flat/down AND CPI < 3.5%
  NEUTRAL   — everything else

Cache TTL: 24h — macro regime doesn't change intraday.
Free API, no rate limits worth worrying about.
"""
import os
import time
import requests
from datetime import datetime, timezone

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"


def _get_series(series_id: str, limit: int = 10) -> list[dict]:
    """Fetch last N observations for a FRED series."""
    api_key = os.environ.get("FRED_API_KEY", "")
    if not api_key:
        return []
    try:
        r = requests.get(
            FRED_BASE,
            params={
                "series_id":      series_id,
                "api_key":        api_key,
                "file_type":      "json",
                "sort_order":     "desc",
                "limit":          limit,
                "observation_start": "2020-01-01",
            },
            timeout=15,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        return data.get("observations", [])
    except Exception as e:
        print(f"  FRED error {series_id}: {e}")
        return []


def _latest_value(observations: list[dict]) -> float | None:
    """Return the most recent non-missing value."""
    for obs in observations:
        v = obs.get("value", ".")
        if v != ".":
            try:
                return float(v)
            except Exception:
                continue
    return None


def _trend(observations: list[dict], n: int = 3) -> float | None:
    """Return average change over last n observations (most recent first)."""
    vals = []
    for obs in observations:
        v = obs.get("value", ".")
        if v != ".":
            try:
                vals.append(float(v))
            except Exception:
                continue
        if len(vals) >= n + 1:
            break
    if len(vals) < 2:
        return None
    changes = [vals[i] - vals[i+1] for i in range(min(n, len(vals)-1))]
    return sum(changes) / len(changes)


def get_macro_regime() -> dict:
    """
    Returns macro regime dict:
    {
        "regime":       "RISK_ON" | "NEUTRAL" | "RISK_OFF",
        "yield_curve":  float | None,   # T10Y2Y spread
        "fed_rate":     float | None,   # current Fed Funds rate
        "cpi_yoy":      float | None,   # latest CPI YoY %
        "explanation":  str,
        "fetched_at":   str,
    }
    Cached 24h in DB.
    """
    from database.db import get_cache, set_cache
    cached = get_cache("macro_regime")
    if cached:
        return cached

    # Fetch all 3 series in sequence (FRED is free, no parallelism needed)
    yield_obs = _get_series("T10Y2Y", limit=5)      # daily, last 5 trading days
    rate_obs  = _get_series("DFF", limit=10)         # daily Fed Funds effective
    cpi_obs   = _get_series("CPIAUCSL", limit=14)    # monthly CPI

    yield_curve = _latest_value(yield_obs)
    fed_rate    = _latest_value(rate_obs)
    rate_trend  = _trend(rate_obs, n=5)  # rising if positive

    # CPI YoY: FRED provides index level, compute YoY manually from 13 months of data
    cpi_yoy = None
    if cpi_obs and len(cpi_obs) >= 13:
        curr_cpi = _latest_value(cpi_obs[:1])
        year_ago_cpi = _latest_value(cpi_obs[12:13])
        if curr_cpi and year_ago_cpi and year_ago_cpi > 0:
            cpi_yoy = round((curr_cpi - year_ago_cpi) / year_ago_cpi * 100, 1)

    # ── Regime logic ──────────────────────────────────────────────────────────
    regime = "NEUTRAL"
    reasons = []

    # RISK_OFF conditions
    risk_off_count = 0
    if yield_curve is not None and yield_curve < 0:
        risk_off_count += 1
        reasons.append(f"Yield curve inverted ({yield_curve:+.2f}%)")
    if fed_rate is not None and fed_rate > 5.0:
        risk_off_count += 1
        reasons.append(f"Fed Funds rate elevated ({fed_rate:.2f}%)")
    if cpi_yoy is not None and cpi_yoy > 4.0:
        risk_off_count += 1
        reasons.append(f"CPI inflation high ({cpi_yoy:.1f}% YoY)")

    # RISK_ON conditions
    risk_on_count = 0
    if yield_curve is not None and yield_curve > 0.5:
        risk_on_count += 1
        reasons.append(f"Yield curve healthy ({yield_curve:+.2f}%)")
    if fed_rate is not None and fed_rate <= 4.0:
        risk_on_count += 1
        reasons.append(f"Fed Funds accommodative ({fed_rate:.2f}%)")
    if cpi_yoy is not None and cpi_yoy < 3.0:
        risk_on_count += 1
        reasons.append(f"CPI benign ({cpi_yoy:.1f}% YoY)")
    elif cpi_yoy is None:
        # Unknown CPI — don't penalise risk_on
        pass

    if risk_off_count >= 2:
        regime = "RISK_OFF"
    elif risk_on_count >= 2 and risk_off_count == 0:
        regime = "RISK_ON"

    explanation = f"{regime}: {'; '.join(reasons)}" if reasons else f"{regime}: insufficient data"

    result = {
        "regime":      regime,
        "yield_curve": round(yield_curve, 2) if yield_curve is not None else None,
        "fed_rate":    round(fed_rate, 2) if fed_rate is not None else None,
        "cpi_yoy":     cpi_yoy,
        "explanation": explanation,
        "fetched_at":  datetime.now(timezone.utc).isoformat(),
    }

    set_cache("macro_regime", result, ttl_hours=24)
    return result


def macro_regime_label(regime_dict: dict) -> str:
    """Returns a single-line label for Claude prompts."""
    r = regime_dict.get("regime", "NEUTRAL")
    exp = regime_dict.get("explanation", "")
    yc  = regime_dict.get("yield_curve")
    rate = regime_dict.get("fed_rate")
    cpi  = regime_dict.get("cpi_yoy")

    parts = []
    if yc is not None:
        parts.append(f"yield curve {yc:+.2f}%")
    if rate is not None:
        parts.append(f"Fed Funds {rate:.2f}%")
    if cpi is not None:
        parts.append(f"CPI {cpi:.1f}% YoY")

    detail = ", ".join(parts) if parts else "data unavailable"
    return f"{r} ({detail})"
