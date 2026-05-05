"""
Options contract recommendation for a stock prediction.

Given: ticker, direction (BULLISH/BEARISH), days_to_target, stock entry price, stock target price.
Returns: best liquid contract to BUY, with estimated option target price.

CALL BUY OPTION — direction == BULLISH  (buy a call, profit if stock rises above strike)
PUT  BUY OPTION — direction == BEARISH  (buy a put,  profit if stock falls below strike)

Contract selection:
  1. Expiry: nearest expiry >= (days_to_target + buffer) so theta doesn't eat the trade
     buffer = 7d for short-term (≤10d), 21d for medium (11-35d), 30d for long (>35d)
  2. Strike band: search ATM → 1-strike OTM only (higher OTM = too speculative)
     Calls: strikes in [price×0.98, price×1.06]
     Puts:  strikes in [price×0.94, price×1.02]
  3. Liquidity filter: OI ≥ 100, bid > 0, bid-ask spread ≤ 25% of mid
  4. Pick highest-OI contract in the band that passes liquidity
  5. Grade: A (OI≥500, vol≥50, spread≤10%), B (OI≥100, vol≥10, spread≤20%), skip if neither

Target price estimation (first-order delta approximation):
  delta ≈ 0.50 if |strike - price| / price ≤ 2%  (ATM)
         0.35 if |strike - price| / price ≤ 5%  (1-strike OTM)
         0.22 otherwise (deep OTM — not recommended)
  stock_move = abs(stock_target - stock_entry)
  option_target = option_entry + stock_move × delta
  ⚠️ First-order only. Ignores gamma, theta decay, IV changes.
"""
import yfinance as yf
from datetime import datetime, timedelta, timezone
from database.db import get_cache, set_cache


_CACHE_TTL_HOURS = 4     # options prices change intraday
_MIN_OI          = 100   # hard minimum open interest
_MIN_OI_A        = 500
_MIN_VOL_A       = 50
_MAX_SPREAD_A    = 0.10  # 10%
_MIN_VOL_B       = 10
_MAX_SPREAD_B    = 0.20  # 20%


def _spread_pct(bid: float, ask: float) -> float | None:
    if not bid or not ask or bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2
    return (ask - bid) / mid if mid > 0 else None


def _grade(oi: int, vol: int, spread: float | None) -> str | None:
    if spread is None or spread > _MAX_SPREAD_B or oi < _MIN_OI:
        return None  # skip — too illiquid
    if oi >= _MIN_OI_A and vol >= _MIN_VOL_A and spread <= _MAX_SPREAD_A:
        return "A"
    if oi >= _MIN_OI and vol >= _MIN_VOL_B and spread <= _MAX_SPREAD_B:
        return "B"
    return None


def _delta_approx(strike: float, spot: float) -> float:
    pct_otm = abs(strike - spot) / spot
    if pct_otm <= 0.02:
        return 0.50
    elif pct_otm <= 0.05:
        return 0.35
    else:
        return 0.22


def _best_expiry(all_expiries: list[str], days_to_target: int) -> str | None:
    today = datetime.now(timezone.utc).date()
    if days_to_target <= 10:
        buffer = 7
    elif days_to_target <= 35:
        buffer = 21
    else:
        buffer = 30

    # Must not expire before the thesis plays out
    min_days = max(days_to_target, 5)
    target_days = days_to_target + buffer
    target_date = today + timedelta(days=target_days)
    min_date    = today + timedelta(days=min_days)

    best = None
    best_diff = float("inf")
    for e in all_expiries:
        try:
            exp_date = datetime.strptime(e, "%Y-%m-%d").date()
            if exp_date < min_date:
                continue
            diff = abs((exp_date - target_date).days)
            if diff < best_diff:
                best_diff = diff
                best = e
        except Exception:
            continue
    return best


def _best_contract(chain_df, spot: float, option_type: str) -> dict | None:
    """
    Finds highest-OI liquid contract in the ATM/near-OTM band.
    option_type: 'call' or 'put'
    """
    if chain_df is None or chain_df.empty:
        return None

    if option_type == "call":
        lo, hi = spot * 0.98, spot * 1.06
    else:
        lo, hi = spot * 0.94, spot * 1.02

    band = chain_df[
        (chain_df["strike"] >= lo) &
        (chain_df["strike"] <= hi)
    ].copy()

    if band.empty:
        return None

    best = None
    best_grade_rank = 99
    best_oi = -1

    for _, row in band.iterrows():
        try:
            strike  = float(row["strike"])
            bid     = float(row.get("bid") or 0)
            ask     = float(row.get("ask") or 0)
            oi      = int(row.get("openInterest") or 0)
            vol     = int(row.get("volume") or 0)
            iv      = float(row.get("impliedVolatility") or 0)
            last    = float(row.get("lastPrice") or 0)
        except Exception:
            continue

        if bid <= 0 or oi < _MIN_OI:
            continue

        spread = _spread_pct(bid, ask)
        g = _grade(oi, vol, spread)
        if g is None:
            continue

        grade_rank = 0 if g == "A" else 1
        if grade_rank < best_grade_rank or (grade_rank == best_grade_rank and oi > best_oi):
            best_grade_rank = grade_rank
            best_oi = oi
            mid = (bid + ask) / 2
            delta = _delta_approx(strike, spot)
            best = {
                "strike":   round(strike, 2),
                "bid":      round(bid, 2),
                "ask":      round(ask, 2),
                "mid":      round(mid, 2),
                "oi":       oi,
                "volume":   vol,
                "iv":       round(iv * 100, 1) if iv else None,
                "last":     round(last, 2),
                "spread_pct": round((spread or 0) * 100, 1),
                "grade":    g,
                "delta_approx": delta,
            }

    return best


def get_option_recommendation(
    ticker: str,
    direction: str,
    days_to_target: int,
    stock_entry: float,
    stock_target: float,
) -> dict:
    """
    Returns the best options contract recommendation for the prediction.

    dict keys:
      option_type        — "CALL BUY OPTION" | "PUT BUY OPTION"
      strike             — float
      expiry             — str "YYYY-MM-DD"
      expiry_label       — str "Jun 20, 2025"
      entry_mid          — float (mid of bid/ask at time of recommendation)
      target_est         — float (estimated option price at stock target)
      gain_pct_est       — float (% gain on option if stock hits target)
      oi                 — int
      volume             — int
      spread_pct         — float
      iv_pct             — float | None
      grade              — "A" | "B"
      delta_approx       — float
      days_to_expiry     — int
      short_term_warning — bool (theta risk if days_to_target ≤ 10)
      available          — bool (False if no liquid contract found)
      reason             — str (why unavailable, if applicable)
    """
    cache_key = f"opt_rec_{ticker}_{direction}_{days_to_target}"
    cached = get_cache(cache_key)
    if cached is not None:
        return cached

    unavailable = {
        "available": False, "option_type": None, "reason": "No liquid contract found",
        "grade": None, "entry_mid": None, "target_est": None, "gain_pct_est": None,
    }

    if direction not in ("BULLISH", "BEARISH"):
        return {**unavailable, "reason": "NEUTRAL prediction — no directional option play"}

    if not stock_entry or stock_entry <= 0:
        return {**unavailable, "reason": "Missing stock entry price"}

    option_type_label = "CALL BUY OPTION" if direction == "BULLISH" else "PUT BUY OPTION"
    chain_key = "calls" if direction == "BULLISH" else "puts"

    try:
        t = yf.Ticker(ticker)
        all_expiries = list(t.options or [])
        if not all_expiries:
            result = {**unavailable, "option_type": option_type_label,
                      "reason": "No options chain available for this ticker"}
            set_cache(cache_key, result, ttl_hours=_CACHE_TTL_HOURS)
            return result

        expiry = _best_expiry(all_expiries, days_to_target)
        if not expiry:
            result = {**unavailable, "option_type": option_type_label,
                      "reason": "No expiry found beyond thesis horizon"}
            set_cache(cache_key, result, ttl_hours=_CACHE_TTL_HOURS)
            return result

        chain = t.option_chain(expiry)
        chain_df = chain.calls if chain_key == "calls" else chain.puts

        # Use live fast_info price if we have it, else fall back to stock_entry
        spot = stock_entry
        try:
            live = float(t.fast_info.last_price)
            if live and live > 0:
                spot = live
        except Exception:
            pass

        contract = _best_contract(chain_df, spot, chain_key.rstrip("s"))
        if contract is None:
            result = {**unavailable, "option_type": option_type_label,
                      "reason": "No liquid contract in ATM/near-OTM band (OI too low or spread too wide)"}
            set_cache(cache_key, result, ttl_hours=_CACHE_TTL_HOURS)
            return result

        # Estimated option target price
        stock_move = abs(stock_target - stock_entry)
        option_move = stock_move * contract["delta_approx"]
        entry_mid   = contract["mid"]
        target_est  = round(entry_mid + option_move, 2)
        gain_pct    = round(option_move / entry_mid * 100, 1) if entry_mid > 0 else 0

        # Days to expiry
        try:
            exp_date = datetime.strptime(expiry, "%Y-%m-%d").date()
            days_to_exp = (exp_date - datetime.now(timezone.utc).date()).days
        except Exception:
            days_to_exp = None

        expiry_label = ""
        try:
            expiry_label = datetime.strptime(expiry, "%Y-%m-%d").strftime("%b %d, %Y")
        except Exception:
            expiry_label = expiry

        result = {
            "available":          True,
            "option_type":        option_type_label,
            "strike":             contract["strike"],
            "expiry":             expiry,
            "expiry_label":       expiry_label,
            "entry_mid":          entry_mid,
            "target_est":         target_est,
            "gain_pct_est":       gain_pct,
            "oi":                 contract["oi"],
            "volume":             contract["volume"],
            "spread_pct":         contract["spread_pct"],
            "iv_pct":             contract.get("iv"),
            "grade":              contract["grade"],
            "delta_approx":       contract["delta_approx"],
            "days_to_expiry":     days_to_exp,
            "short_term_warning": days_to_target <= 10,
            "reason":             "",
        }
        set_cache(cache_key, result, ttl_hours=_CACHE_TTL_HOURS)
        return result

    except Exception as e:
        result = {**unavailable, "option_type": option_type_label,
                  "reason": f"Fetch error: {str(e)[:80]}"}
        set_cache(cache_key, result, ttl_hours=_CACHE_TTL_HOURS)
        return result
