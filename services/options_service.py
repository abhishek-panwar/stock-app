"""
Options flow service — yfinance only, no API key required.

Fetches 3 expiry windows for each ticker (Friday long-term scan only):
  1. Nearest weekly expiry     — current short-term sentiment
  2. ~30-day (1 month out)     — near-term institutional positioning
  3. ~60-day (2 months out)    — medium-term directional bias

Signals derived:
  put_call_ratio     — >1.2 = bearish pressure; <0.6 = bullish; between = neutral
  iv_skew            — % spread between OTM put IV and OTM call IV; positive = fear premium
  net_delta_bias     — weighted directional signal from call vs put open interest
  flow_signal        — "BULLISH" | "BEARISH" | "NEUTRAL" — composite of all 3 expiries

Call budget: 1 yfinance Ticker call per ticker (option chain bundled). Cached 4h.
"""
import yfinance as yf
from datetime import datetime, timezone
from database.db import get_cache, set_cache


_CACHE_TTL_HOURS = 4
_OTM_BAND = 0.05  # 5% OTM for skew calculation


def _nearest_expiries(all_expiries: list[str], n: int = 3) -> list[str]:
    """Return the n nearest upcoming expiries from a sorted list of 'YYYY-MM-DD' strings."""
    today = datetime.now(timezone.utc).date().isoformat()
    upcoming = sorted(e for e in all_expiries if e >= today)
    # Space them out: pick nearest, ~30d, ~60d rather than 3 consecutive weeklies
    if len(upcoming) < 1:
        return []
    result = [upcoming[0]]  # nearest weekly
    target_days = [30, 60]
    for target in target_days:
        best = None
        best_diff = float("inf")
        for e in upcoming[1:]:
            try:
                diff = abs((datetime.strptime(e, "%Y-%m-%d").date() -
                            datetime.now(timezone.utc).date()).days - target)
                if diff < best_diff:
                    best_diff = diff
                    best = e
            except Exception:
                continue
        if best and best not in result:
            result.append(best)
    return result[:n]


def _parse_chain(chain, price: float) -> dict:
    """
    Parse a single expiry's option chain into flow signals.
    Returns dict with put_call_ratio, iv_skew, call_oi, put_oi.
    """
    try:
        calls = chain.calls
        puts  = chain.puts

        if calls.empty or puts.empty:
            return {}

        # Total OI and volume
        call_oi  = int(calls["openInterest"].fillna(0).sum())
        put_oi   = int(puts["openInterest"].fillna(0).sum())
        call_vol = int(calls["volume"].fillna(0).sum())
        put_vol  = int(puts["volume"].fillna(0).sum())

        total_vol = call_vol + put_vol
        put_call_ratio = round(put_vol / call_vol, 2) if call_vol > 0 else None

        # IV skew: compare IV of OTM puts vs OTM calls (fear premium signal)
        otm_put_lo  = price * (1 - _OTM_BAND)
        otm_put_hi  = price * (1 - 0.01)
        otm_call_lo = price * (1 + 0.01)
        otm_call_hi = price * (1 + _OTM_BAND)

        iv_col = "impliedVolatility"
        if iv_col not in calls.columns or iv_col not in puts.columns:
            iv_skew = None
        else:
            otm_puts  = puts[(puts["strike"] >= otm_put_lo) & (puts["strike"] <= otm_put_hi)]
            otm_calls = calls[(calls["strike"] >= otm_call_lo) & (calls["strike"] <= otm_call_hi)]
            if otm_puts.empty or otm_calls.empty:
                iv_skew = None
            else:
                avg_put_iv  = float(otm_puts[iv_col].fillna(0).mean())
                avg_call_iv = float(otm_calls[iv_col].fillna(0).mean())
                iv_skew = round((avg_put_iv - avg_call_iv) * 100, 1) if avg_call_iv > 0 else None

        return {
            "call_oi":        call_oi,
            "put_oi":         put_oi,
            "call_vol":       call_vol,
            "put_vol":        put_vol,
            "total_vol":      total_vol,
            "put_call_ratio": put_call_ratio,
            "iv_skew":        iv_skew,
        }
    except Exception:
        return {}


def get_options_flow(ticker: str) -> dict:
    """
    Fetches options flow for 3 expiries and derives a composite flow_signal.

    Returns:
      {
        "put_call_ratio":    float | None   — weighted avg across 3 expiries
        "iv_skew":           float | None   — positive = fear premium (bearish)
        "net_oi_bias":       float | None   — (call_oi - put_oi) / total_oi
        "flow_signal":       "BULLISH" | "BEARISH" | "NEUTRAL" | None
        "expiries_analyzed": int
        "total_volume":      int
      }
    Cached 4h.
    """
    cache_key = f"options_flow_{ticker}"
    cached = get_cache(cache_key)
    if cached is not None:
        return cached

    empty = {
        "put_call_ratio": None, "iv_skew": None, "net_oi_bias": None,
        "flow_signal": None, "expiries_analyzed": 0, "total_volume": 0,
    }

    try:
        t = yf.Ticker(ticker)
        all_expiries = t.options
        if not all_expiries:
            set_cache(cache_key, empty, ttl_hours=_CACHE_TTL_HOURS)
            return empty

        current_price = None
        try:
            current_price = float(t.fast_info.last_price)
        except Exception:
            pass
        if not current_price or current_price <= 0:
            set_cache(cache_key, empty, ttl_hours=_CACHE_TTL_HOURS)
            return empty

        expiries = _nearest_expiries(list(all_expiries), n=3)
        if not expiries:
            set_cache(cache_key, empty, ttl_hours=_CACHE_TTL_HOURS)
            return empty

        all_pcr  = []
        all_skew = []
        total_call_oi = 0
        total_put_oi  = 0
        total_vol     = 0

        for exp in expiries:
            try:
                chain = t.option_chain(exp)
                parsed = _parse_chain(chain, current_price)
                if not parsed:
                    continue
                if parsed.get("put_call_ratio") is not None:
                    all_pcr.append(parsed["put_call_ratio"])
                if parsed.get("iv_skew") is not None:
                    all_skew.append(parsed["iv_skew"])
                total_call_oi += parsed.get("call_oi", 0)
                total_put_oi  += parsed.get("put_oi", 0)
                total_vol     += parsed.get("total_vol", 0)
            except Exception:
                continue

        if not all_pcr:
            set_cache(cache_key, empty, ttl_hours=_CACHE_TTL_HOURS)
            return empty

        avg_pcr   = round(sum(all_pcr) / len(all_pcr), 2)
        avg_skew  = round(sum(all_skew) / len(all_skew), 1) if all_skew else None
        total_oi  = total_call_oi + total_put_oi
        net_oi_bias = round((total_call_oi - total_put_oi) / total_oi, 3) if total_oi > 0 else None

        # Composite signal: 2 out of 3 signals must agree
        bullish_votes = 0
        bearish_votes = 0

        if avg_pcr <= 0.6:
            bullish_votes += 1
        elif avg_pcr >= 1.2:
            bearish_votes += 1

        if avg_skew is not None:
            if avg_skew < -5:     # call IV > put IV = bullish positioning
                bullish_votes += 1
            elif avg_skew > 10:   # put IV >> call IV = fear / hedging demand
                bearish_votes += 1

        if net_oi_bias is not None:
            if net_oi_bias >= 0.15:   # calls dominate OI
                bullish_votes += 1
            elif net_oi_bias <= -0.15:  # puts dominate OI
                bearish_votes += 1

        if bullish_votes >= 2:
            flow_signal = "BULLISH"
        elif bearish_votes >= 2:
            flow_signal = "BEARISH"
        else:
            flow_signal = "NEUTRAL"

        result = {
            "put_call_ratio":    avg_pcr,
            "iv_skew":           avg_skew,
            "net_oi_bias":       net_oi_bias,
            "flow_signal":       flow_signal,
            "expiries_analyzed": len(all_pcr),
            "total_volume":      total_vol,
        }
        set_cache(cache_key, result, ttl_hours=_CACHE_TTL_HOURS)
        return result

    except Exception:
        set_cache(cache_key, empty, ttl_hours=_CACHE_TTL_HOURS)
        return empty
