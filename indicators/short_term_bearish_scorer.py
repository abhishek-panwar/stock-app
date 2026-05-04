"""
Short-term bearish scorer — Type 1: overbought reversal setups.

Target: stocks that have had a sustained run (8%+ in 5 days), are extended
above their mean, and are showing exhaustion / distribution signals.

Signal philosophy:
  - RSI > 70 = primary entry condition (not penalized here — it IS the setup)
  - Bearish RSI divergence = price making new highs but RSI declining = distribution
  - MACD bearish crossover or flattening histogram = momentum fading
  - OBV declining while price still high = smart money selling into strength
  - BB upper band rejection = price touched upper band and closed back inside
  - High ROC 5d = extended run confirmed (required for setup)
  - Volume on down days > up days = distribution pattern
"""

FORMULA_VERSION = "bearish_v1.0"


def compute_short_term_bearish_score(
    ind: dict,
    sentiment: dict,
    analyst: dict,
    earnings: dict,
    source: str = "hot_stock",
    earnings_calendar: dict = None,
) -> dict:
    """
    Returns score dict with breakdown and total (0–100).
    Higher score = stronger reversal setup = more likely to decline.
    Deliberately omits fundamentals/insider — these are short-term reversal signals only.
    """
    scores = {}
    price = ind.get("price", 0)

    # ── Group 1: RSI Exhaustion (30 pts) ──────────────────────────────────────
    # This is the core of the reversal thesis. RSI >70 is REQUIRED for the setup.
    rsi = ind.get("rsi", 50)
    if rsi >= 80:
        rsi_score = 20   # severely overbought
    elif rsi >= 75:
        rsi_score = 16
    elif rsi >= 70:
        rsi_score = 12
    elif rsi >= 65:
        rsi_score = 6   # approaching overbought — lower conviction
    else:
        rsi_score = 0   # not a reversal candidate

    # Bearish RSI divergence: price up but RSI declining = strongest reversal signal
    divergence_bonus = 10 if ind.get("rsi_bearish_divergence") else 0

    scores["rsi_exhaustion"] = round(min(rsi_score + divergence_bonus, 30), 1)

    # ── Group 2: Momentum Fading (25 pts) ─────────────────────────────────────
    macd_line   = ind.get("macd_line", 0)
    macd_signal = ind.get("macd_signal", 0)
    macd_hist   = ind.get("macd_hist", 0)
    macd_hist_prev = ind.get("macd_hist_prev", 0)

    if ind.get("macd_crossover_bearish"):
        macd_score = 15   # line just crossed below signal = confirmed reversal
    elif macd_line > macd_signal and macd_hist < macd_hist_prev and macd_hist_prev > 0:
        macd_score = 12   # histogram shrinking from positive = momentum fading, early warning
    elif macd_line < macd_signal:
        macd_score = 8    # already bearish
    else:
        macd_score = 2

    # ROC: high positive ROC = stock is extended = strengthens reversal thesis
    roc_5 = ind.get("roc_5", 0) or 0
    roc_10 = ind.get("roc_10", 0) or 0
    if roc_5 >= 10 or roc_10 >= 15:
        roc_score = 10   # very extended run — high reversion probability
    elif roc_5 >= 5 or roc_10 >= 8:
        roc_score = 6
    elif roc_5 >= 2:
        roc_score = 3
    else:
        roc_score = 0   # no run = not a reversal setup

    scores["momentum_fading"] = round(min(macd_score + roc_score, 25), 1)

    # ── Group 3: Distribution / Volume (20 pts) ───────────────────────────────
    obv = ind.get("obv_trend", "NEUTRAL")
    if obv == "DIVERGING_BEARISH":
        obv_score = 15   # price rising but OBV falling = smart money distributing
    elif obv == "DECLINING":
        obv_score = 10
    elif obv == "NEUTRAL":
        obv_score = 3
    else:
        obv_score = 0   # CONFIRMING or DIVERGING_BULLISH = still accumulating, not a short

    # Volume surge on a declining day = distribution signal
    vsr = ind.get("volume_surge_ratio", 1.0)
    if obv in ("DIVERGING_BEARISH", "DECLINING") and vsr >= 2.0:
        dist_bonus = 5   # high volume + OBV declining = strong distribution
    else:
        dist_bonus = 0

    scores["distribution"] = round(min(obv_score + dist_bonus, 20), 1)

    # ── Group 4: Price Extension / Structure (15 pts) ─────────────────────────
    ma20  = ind.get("ma20") or price
    ma50  = ind.get("ma50") or price

    # Extension above MA20 — how far above mean
    ext_pct = (price - ma20) / ma20 * 100 if ma20 > 0 else 0
    if ext_pct >= 12:
        ext_score = 10   # severely extended
    elif ext_pct >= 8:
        ext_score = 8
    elif ext_pct >= 4:
        ext_score = 5
    else:
        ext_score = 1

    # BB upper band rejection: touched/exceeded upper band = natural resistance
    if not ind.get("bb_breakout_up"):
        bb_score = 3   # price below upper band after run = rejected
    else:
        bb_score = 0   # still breaking out = no rejection yet

    # Price above VWAP but severely extended = sign of exhaustion
    vwap_score = 2 if ind.get("price_above_vwap") and ext_pct >= 8 else 0

    scores["extension"] = round(min(ext_score + bb_score + vwap_score, 15), 1)

    # ── Group 5: External Confirmation (10 pts) ────────────────────────────────
    # For bearish reversals, negative/neutral sentiment confirms, positive is a headwind
    news_s = sentiment.get("score", 0)
    if news_s < -0.3:
        news_score = 5   # negative news while price high = distribution accelerator
    elif news_s < 0.3:
        news_score = 3   # neutral = no news catalyst to sustain the run
    else:
        news_score = 0   # positive news = may sustain rally, reduces reversal conviction

    consensus = analyst.get("consensus", "HOLD")
    analyst_score = {"STRONG_BUY": 0, "BUY": 1, "HOLD": 3, "SELL": 5, "STRONG_SELL": 6}.get(consensus, 3)

    scores["external"] = round(min(news_score + analyst_score, 10), 1)

    # ── Hard penalties ─────────────────────────────────────────────────────────
    base = sum(scores.values())
    penalty = 0
    penalty_reasons = []

    # Earnings within 5 days = gap risk — avoid shorting into earnings
    if earnings_calendar and earnings_calendar.get("has_upcoming"):
        days_to_earn = earnings_calendar.get("days_to_earnings", 99)
        if days_to_earn <= 5:
            penalty += 25
            penalty_reasons.append(f"Earnings in {days_to_earn}d — gap risk (-25)")

    # Already in downtrend — not a fresh reversal candidate
    ma50 = ind.get("ma50") or price
    if price < ma50:
        penalty += 10
        penalty_reasons.append("Price below MA50 — already in downtrend, not a reversal setup (-10)")

    # OBV confirming during the run is expected — only penalise lightly.
    # A -15 here wiped out most candidates since any 8%+ run has buyers behind it.
    # Real red flag is OBV still accelerating UP on the day of scan (not yet reversing).
    if obv == "CONFIRMING" and ind.get("volume_surge_ratio", 1.0) >= 2.0:
        penalty += 8
        penalty_reasons.append("OBV confirming + high volume surge — buyers still in control (-8)")
    elif obv == "CONFIRMING":
        penalty += 3
        penalty_reasons.append("OBV still confirming — mild caution (-3)")

    total = max(0, min(round(base - penalty), 100))

    bonus_reasons = [r for r in [
        f"RSI bearish divergence (+10)" if ind.get("rsi_bearish_divergence") else None,
        f"MACD bearish crossover (+15)" if ind.get("macd_crossover_bearish") else None,
        f"OBV distribution" if obv == "DIVERGING_BEARISH" else None,
    ] if r]
    bonus_reasons += penalty_reasons

    # Conviction: need RSI exhaustion + at least 2 of fading/distribution/extension
    confirmations = 0
    if scores.get("rsi_exhaustion", 0) >= 12:    confirmations += 1
    if scores.get("momentum_fading", 0) >= 10:   confirmations += 1
    if scores.get("distribution", 0) >= 8:       confirmations += 1
    if scores.get("extension", 0) >= 8:          confirmations += 1
    conviction_pass = total >= 40 and confirmations >= 3

    return {
        "total": total,
        "base": round(base),
        "bonus": -penalty,
        "bonus_reasons": bonus_reasons,
        "breakdown": scores,
        "formula_version": FORMULA_VERSION,
        "analyst_upside_pct": None,
        "earnings_calendar": earnings_calendar,
        "insider_buying": None,
        "conviction_pass": conviction_pass,
    }
