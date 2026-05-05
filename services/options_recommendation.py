"""
Options contract recommendation for a stock prediction.

Given: ticker, direction (BULLISH/BEARISH), days_to_target, timeframe, stock entry/target prices.
Returns: best liquid contract to BUY, with estimated option target price.

CALL BUY OPTION — direction == BULLISH  (buy a call, profit if stock rises above strike)
PUT  BUY OPTION — direction == BEARISH  (buy a put,  profit if stock falls below strike)

Expiry strategy by timeframe:
  short/medium (≤35d prediction): target 35 DTE regardless of days_to_target.
    Rationale: buy time, hold only 2-10 days while the move plays out, sell before theta
    accelerates. At 30+ DTE, theta is ~$0.05-0.15/day vs $0.40-0.80/day at 5 DTE.
  long (>35d prediction): target days_to_target + 30d buffer (existing behaviour).

Liquidity thresholds:
  short/medium — must exit quickly:
    Grade A: OI≥150, vol≥15, spread≤15%
    Grade B: OI≥75,  vol≥5,  spread≤25%
  long — hold weeks-months:
    Grade A: OI≥300, vol≥30, spread≤10%
    Grade B: OI≥75,  vol≥10, spread≤20%

Strike band: ATM → 1-strike OTM only (both timeframes)
  Calls: [price×0.98, price×1.06]
  Puts:  [price×0.94, price×1.02]

Target price estimation (first-order delta approximation):
  delta ≈ 0.50 ATM (|strike-price|/price ≤ 2%)
         0.35 near-OTM (≤ 5%)
         0.22 OTM (> 5%, not recommended)
  option_target = entry_mid + stock_move × delta
  ⚠️ First-order only. Ignores gamma, theta decay, IV changes.

API calls: yfinance only, no API key. Called on-demand from UI only (never during scanner).
Cached 4h in Supabase — second view within 4h costs zero network calls.
"""
import yfinance as yf
from datetime import datetime, timedelta, timezone
from database.db import get_cache, set_cache


_CACHE_TTL_HOURS = 4          # options prices change intraday
_SHORT_TERM_DTE  = 35         # fixed DTE target for short/medium predictions

# Long-term liquidity thresholds (hold weeks-months, exit is less time-sensitive)
_LT_MIN_OI       = 75
_LT_MIN_OI_A     = 300
_LT_MIN_VOL_A    = 30
_LT_MAX_SPREAD_A = 0.10
_LT_MIN_VOL_B    = 10
_LT_MAX_SPREAD_B = 0.20

# Short/medium-term liquidity thresholds (must exit in days — tighter spread critical)
_ST_MIN_OI       = 75
_ST_MIN_OI_A     = 150
_ST_MIN_VOL_A    = 15
_ST_MAX_SPREAD_A = 0.15
_ST_MIN_VOL_B    = 5
_ST_MAX_SPREAD_B = 0.25


def _spread_pct(bid: float, ask: float) -> float | None:
    if not bid or not ask or bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2
    return (ask - bid) / mid if mid > 0 else None


def _grade(oi: int, vol: int, spread: float | None, short_term: bool = False) -> str | None:
    if short_term:
        min_oi, min_oi_a = _ST_MIN_OI, _ST_MIN_OI_A
        min_vol_a, max_spread_a = _ST_MIN_VOL_A, _ST_MAX_SPREAD_A
        min_vol_b, max_spread_b = _ST_MIN_VOL_B, _ST_MAX_SPREAD_B
    else:
        min_oi, min_oi_a = _LT_MIN_OI, _LT_MIN_OI_A
        min_vol_a, max_spread_a = _LT_MIN_VOL_A, _LT_MAX_SPREAD_A
        min_vol_b, max_spread_b = _LT_MIN_VOL_B, _LT_MAX_SPREAD_B

    if spread is None or spread > max_spread_b or oi < min_oi:
        return None
    if oi >= min_oi_a and vol >= min_vol_a and spread <= max_spread_a:
        return "A"
    if oi >= min_oi and vol >= min_vol_b and spread <= max_spread_b:
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


def _is_monthly_expiry(exp_date) -> bool:
    """
    Monthly options: 3rd Friday of the month (highest OI by far).
    yfinance returns the last *trading* day — typically the 3rd Friday,
    but some months it's Thursday the 18th when Friday falls on a holiday.
    Accept 3rd Friday ± 1 day to handle both cases.
    """
    from calendar import monthcalendar, FRIDAY
    mc = monthcalendar(exp_date.year, exp_date.month)
    fridays = [week[FRIDAY] for week in mc if week[FRIDAY] != 0]
    if len(fridays) < 3:
        return False
    third_fri = fridays[2]
    return abs(exp_date.day - third_fri) <= 1


def _best_expiry(all_expiries: list[str], days_to_target: int, short_term: bool = False) -> str | None:
    today = datetime.now(timezone.utc).date()

    if short_term:
        target_days = _SHORT_TERM_DTE
        min_days    = max(days_to_target + 3, 14)
    else:
        buffer = 30
        target_days = days_to_target + buffer
        min_days    = max(days_to_target, 5)

    target_date = today + timedelta(days=target_days)
    min_date    = today + timedelta(days=min_days)

    candidates = []
    for e in all_expiries:
        try:
            exp_date = datetime.strptime(e, "%Y-%m-%d").date()
            if exp_date < min_date:
                continue
            diff = abs((exp_date - target_date).days)
            candidates.append((diff, not _is_monthly_expiry(exp_date), e))
        except Exception:
            continue

    if not candidates:
        return None

    # Sort: prefer monthlies (is_monthly=False sorts before True), then closest to target DTE
    candidates.sort(key=lambda x: (x[1], x[0]))
    return candidates[0][2]


def _best_contract(chain_df, spot: float, option_type: str, short_term: bool = False) -> dict | None:
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

    min_oi = _ST_MIN_OI if short_term else _LT_MIN_OI

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

        if bid <= 0 or oi < min_oi:
            continue

        spread = _spread_pct(bid, ask)
        g = _grade(oi, vol, spread, short_term=short_term)
        if g is None:
            continue

        grade_rank = 0 if g == "A" else 1
        if grade_rank < best_grade_rank or (grade_rank == best_grade_rank and oi > best_oi):
            best_grade_rank = grade_rank
            best_oi = oi
            mid = (bid + ask) / 2
            delta = _delta_approx(strike, spot)
            best = {
                "strike":       round(strike, 2),
                "bid":          round(bid, 2),
                "ask":          round(ask, 2),
                "mid":          round(mid, 2),
                "oi":           oi,
                "volume":       vol,
                "iv":           round(iv * 100, 1) if iv else None,
                "last":         round(last, 2),
                "spread_pct":   round((spread or 0) * 100, 1),
                "grade":        g,
                "delta_approx": delta,
            }

    return best


def get_option_recommendation(
    ticker: str,
    direction: str,
    days_to_target: int,
    stock_entry: float,
    stock_target: float,
    timeframe: str = "long",
    has_earnings: bool = False,
) -> dict:
    """
    Returns the best options contract recommendation for the prediction.

    timeframe: "short" | "medium" | "long"
      short/medium → 35 DTE target, stricter liquidity (must be able to exit quickly)
      long          → days_to_target + 30d buffer, standard liquidity

    has_earnings: True if earnings fall within the hold window
      → surfaced as a warning in the result (IV crush risk on exit)

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
      is_short_term      — bool
      earnings_warning   — bool  (IV crush risk if earnings in window)
      available          — bool
      reason             — str
    """
    short_term = timeframe in ("short", "medium")
    cache_key = f"opt_rec_{ticker}_{direction}_{timeframe}_{days_to_target}"
    cached = get_cache(cache_key)
    if cached is not None:
        return cached

    unavailable = {
        "available": False, "option_type": None, "reason": "No liquid contract found",
        "grade": None, "entry_mid": None, "target_est": None, "gain_pct_est": None,
        "is_short_term": short_term, "earnings_warning": has_earnings,
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

        expiry = _best_expiry(all_expiries, days_to_target, short_term=short_term)
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

        contract = _best_contract(chain_df, spot, chain_key.rstrip("s"), short_term=short_term)
        if contract is None:
            liq_note = "(OI≥75, spread≤25% required)" if short_term else "(OI≥75, spread≤20% required)"
            result = {**unavailable, "option_type": option_type_label,
                      "reason": f"No liquid contract in ATM/near-OTM band {liq_note}"}
            set_cache(cache_key, result, ttl_hours=_CACHE_TTL_HOURS)
            return result

        # Estimated option target price
        stock_move  = abs(stock_target - stock_entry)
        option_move = stock_move * contract["delta_approx"]
        entry_mid   = contract["mid"]
        target_est  = round(entry_mid + option_move, 2)
        gain_pct    = round(option_move / entry_mid * 100, 1) if entry_mid > 0 else 0

        # Days to expiry
        try:
            exp_date    = datetime.strptime(expiry, "%Y-%m-%d").date()
            days_to_exp = (exp_date - datetime.now(timezone.utc).date()).days
        except Exception:
            days_to_exp = None

        expiry_label = ""
        try:
            expiry_label = datetime.strptime(expiry, "%Y-%m-%d").strftime("%b %d, %Y")
        except Exception:
            expiry_label = expiry

        result = {
            "available":        True,
            "option_type":      option_type_label,
            "strike":           contract["strike"],
            "expiry":           expiry,
            "expiry_label":     expiry_label,
            "entry_mid":        entry_mid,
            "target_est":       target_est,
            "gain_pct_est":     gain_pct,
            "oi":               contract["oi"],
            "volume":           contract["volume"],
            "spread_pct":       contract["spread_pct"],
            "iv_pct":           contract.get("iv"),
            "grade":            contract["grade"],
            "delta_approx":     contract["delta_approx"],
            "days_to_expiry":   days_to_exp,
            "is_short_term":    short_term,
            "earnings_warning": has_earnings,
            "reason":           "",
        }
        set_cache(cache_key, result, ttl_hours=_CACHE_TTL_HOURS)
        return result

    except Exception as e:
        result = {**unavailable, "option_type": option_type_label,
                  "reason": f"Fetch error: {str(e)[:80]}"}
        set_cache(cache_key, result, ttl_hours=_CACHE_TTL_HOURS)
        return result
