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
    Returns macro regime dict with 5 signals:
      - yield_curve  : T10Y2Y spread
      - fed_rate     : Fed Funds effective rate
      - cpi_yoy      : CPI YoY %
      - vix          : VIX fear index (VIXCLS)
      - hy_spread    : HY credit spread in bps (BAMLH0A0HYM2) — widens in risk-off
    Cached 24h in DB.
    """
    from database.db import get_cache, set_cache
    cached = get_cache("macro_regime")
    if cached:
        return cached

    # Fetch all 5 series (FRED is free, no rate limits worth worrying about)
    yield_obs  = _get_series("T10Y2Y",       limit=5)   # daily yield curve
    rate_obs   = _get_series("DFF",           limit=10)  # daily Fed Funds
    cpi_obs    = _get_series("CPIAUCSL",      limit=14)  # monthly CPI
    vix_obs    = _get_series("VIXCLS",        limit=5)   # daily VIX
    hy_obs     = _get_series("BAMLH0A0HYM2", limit=5)   # daily HY-IG spread (%)

    yield_curve = _latest_value(yield_obs)
    fed_rate    = _latest_value(rate_obs)
    vix         = _latest_value(vix_obs)
    hy_spread   = _latest_value(hy_obs)  # in %, multiply by 100 for bps

    # CPI YoY: FRED provides index level, compute manually from 13 months of data
    cpi_yoy = None
    if cpi_obs and len(cpi_obs) >= 13:
        curr_cpi     = _latest_value(cpi_obs[:1])
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
    if vix is not None and vix > 30:
        risk_off_count += 1
        reasons.append(f"VIX elevated ({vix:.0f}) — market fear state")
    if hy_spread is not None and hy_spread > 5.0:  # >500bps = stress
        risk_off_count += 1
        reasons.append(f"HY credit spread wide ({hy_spread:.1f}%) — risk assets under pressure")

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
    if vix is not None and vix < 18:
        risk_on_count += 1
        reasons.append(f"VIX low ({vix:.0f}) — complacent/bullish sentiment")
    if hy_spread is not None and hy_spread < 3.5:  # <350bps = tight spreads
        risk_on_count += 1
        reasons.append(f"HY credit spread tight ({hy_spread:.1f}%) — risk appetite healthy")

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
        "vix":         round(vix, 1) if vix is not None else None,
        "hy_spread":   round(hy_spread, 2) if hy_spread is not None else None,
        "explanation": explanation,
        "fetched_at":  datetime.now(timezone.utc).isoformat(),
    }

    set_cache("macro_regime", result, ttl_hours=24)
    return result


def macro_regime_label(regime_dict: dict) -> str:
    """Returns a single-line label for Claude prompts."""
    r    = regime_dict.get("regime", "NEUTRAL")
    yc   = regime_dict.get("yield_curve")
    rate = regime_dict.get("fed_rate")
    cpi  = regime_dict.get("cpi_yoy")
    vix  = regime_dict.get("vix")
    hy   = regime_dict.get("hy_spread")

    parts = []
    if yc is not None:
        parts.append(f"yield curve {yc:+.2f}%")
    if rate is not None:
        parts.append(f"Fed Funds {rate:.2f}%")
    if cpi is not None:
        parts.append(f"CPI {cpi:.1f}% YoY")
    if vix is not None:
        fear = " FEAR" if vix > 30 else " elevated" if vix > 20 else ""
        parts.append(f"VIX {vix:.0f}{fear}")
    if hy is not None:
        stress = " STRESSED" if hy > 5.0 else " wide" if hy > 4.0 else ""
        parts.append(f"HY spread {hy:.1f}%{stress}")

    detail = ", ".join(parts) if parts else "data unavailable"
    return f"{r} ({detail})"
