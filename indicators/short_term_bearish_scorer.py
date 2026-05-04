"""
Short-term bearish scorer — Type 1: overbought reversal setups.

Target: stocks that have had a sustained run (8%+ in 5-10 days), are extended
above their mean, and are showing reversal confirmation signals.

Signal philosophy:
  - RSI > 70 = primary entry condition; bearish divergence over 10 bars = stronger
  - MACD histogram shrinking = earlier and more valuable than crossover
  - Distribution days (close down on high volume) = institutional selling
  - OBV declining while price still high = smart money distributing
  - BB upper band rejection = touched upper band then closed back inside
  - ATR-based extension = price > 2× ATR above MA20 = parabolic exhaustion
  - Candlestick triggers = bearish engulfing / shooting star / upper wick = NOW reversing
  - Short interest penalty = don't short heavily shorted stocks (squeeze risk)
  - MA50 slope rising = strong uptrend = mean reversion thesis is weaker
"""

FORMULA_VERSION = "bearish_v2.0"


def compute_short_term_bearish_score(
    ind: dict,
    sentiment: dict,
    analyst: dict,
    earnings: dict,
    source: str = "hot_stock",
    earnings_calendar: dict = None,
    rel_strength_vs_spy: float = None,
    sector_return_5d: float = None,
    short_interest_pct: float = None,
) -> dict:
    """
    Returns score dict with breakdown and total (0–100).
    Higher score = stronger reversal setup = more likely to decline.
    """
    scores = {}
    price = ind.get("price", 0)

    # ── Group 1: RSI Exhaustion (25 pts) ──────────────────────────────────────
    rsi = ind.get("rsi", 50)
    if rsi >= 80:
        rsi_score = 15
    elif rsi >= 75:
        rsi_score = 12
    elif rsi >= 70:
        rsi_score = 9
    elif rsi >= 65:
        rsi_score = 4
    else:
        rsi_score = 0

    # Bearish RSI divergence over 10 bars: price higher but RSI lower = momentum genuinely fading
    divergence_bonus = 10 if ind.get("rsi_bearish_divergence") else 0

    scores["rsi_exhaustion"] = round(min(rsi_score + divergence_bonus, 25), 1)

    # ── Group 2: Momentum Fading (25 pts) ─────────────────────────────────────
    macd_line      = ind.get("macd_line", 0)
    macd_signal    = ind.get("macd_signal", 0)
    macd_hist      = ind.get("macd_hist", 0)
    macd_hist_prev = ind.get("macd_hist_prev", 0)

    # Histogram shrinking is earlier and more valuable than crossover
    if ind.get("macd_crossover_bearish"):
        macd_score = 15   # confirmed reversal — late but definitive
    elif macd_line > macd_signal and macd_hist < macd_hist_prev and macd_hist_prev > 0:
        macd_score = 20   # histogram shrinking from positive = earliest MACD warning
    elif macd_line < macd_signal:
        macd_score = 10   # already bearish MACD
    else:
        macd_score = 2

    scores["momentum_fading"] = round(min(macd_score, 25), 1)

    # ── Group 3: Distribution / Volume (20 pts) ───────────────────────────────
    obv = ind.get("obv_trend", "NEUTRAL")
    if obv == "DIVERGING_BEARISH":
        obv_score = 10   # price rising but OBV falling = smart money distributing
    elif obv == "DECLINING":
        obv_score = 7
    elif obv == "NEUTRAL":
        obv_score = 2
    else:
        obv_score = 0   # CONFIRMING or DIVERGING_BULLISH = buyers still in control

    # Distribution days: closes down on above-avg volume = institutional selling pattern
    dist_days = ind.get("distribution_days", 0)
    if dist_days >= 4:
        dist_day_score = 10
    elif dist_days >= 3:
        dist_day_score = 7
    elif dist_days >= 2:
        dist_day_score = 4
    else:
        dist_day_score = 0

    scores["distribution"] = round(min(obv_score + dist_day_score, 20), 1)

    # ── Group 4: Price Extension / Structure (20 pts) ─────────────────────────
    ma20 = ind.get("ma20") or price
    atr  = ind.get("atr") or (price * 0.02)

    # % extension above MA20
    ext_pct = (price - ma20) / ma20 * 100 if ma20 > 0 else 0
    if ext_pct >= 12:
        ext_score = 8
    elif ext_pct >= 8:
        ext_score = 6
    elif ext_pct >= 4:
        ext_score = 4
    else:
        ext_score = 1

    # ATR-based extension: price > 2× ATR above MA20 = parabolic, high reversion probability
    atr_ext_score = 4 if atr > 0 and (price - ma20) >= 2 * atr else 0

    # BB upper band rejection: touched upper band recently then closed back inside
    if ind.get("bb_touched_upper") and not ind.get("bb_breakout_up"):
        bb_score = 5
    else:
        bb_score = 0

    # Price above VWAP when severely extended = exhaustion
    vwap_score = 3 if ind.get("price_above_vwap") and ext_pct >= 8 else 0

    scores["extension"] = round(min(ext_score + atr_ext_score + bb_score + vwap_score, 20), 1)

    # ── Group 5: Candlestick Triggers (10 pts) ────────────────────────────────
    # These are the "reversing NOW" signals — price action confirming the thesis
    candle_score = 0
    candle_reasons = []
    if ind.get("bearish_engulfing"):
        candle_score += 7
        candle_reasons.append("bearish engulfing")
    if ind.get("shooting_star"):
        candle_score += 6
        candle_reasons.append("shooting star")
    if ind.get("upper_wick_rejection"):
        candle_score += 4
        candle_reasons.append("upper wick rejection")

    scores["candle_trigger"] = round(min(candle_score, 10), 1)

    # ── Group 6: External Confirmation (5 pts) ────────────────────────────────
    # Reduced from 10 — analyst ratings lag, news is noisy
    news_s = sentiment.get("score", 0)
    if news_s < -0.3:
        news_score = 3
    elif news_s < 0.3:
        news_score = 2
    else:
        news_score = 0

    consensus = analyst.get("consensus", "HOLD")
    analyst_score = {"STRONG_BUY": 0, "BUY": 0, "HOLD": 2, "SELL": 3, "STRONG_SELL": 3}.get(consensus, 2)

    scores["external"] = round(min(news_score + analyst_score, 5), 1)

    # ── Market context adjustment ──────────────────────────────────────────────
    market_adj = 0
    market_reasons = []

    if sector_return_5d is not None:
        if sector_return_5d <= -2:
            market_adj += 5
            market_reasons.append(f"Sector down {sector_return_5d:.1f}% — confirms reversal (+5)")
        elif sector_return_5d >= 4:
            market_adj -= 4
            market_reasons.append(f"Sector up {sector_return_5d:.1f}% — sector tailwind fights reversal (-4)")

    if rel_strength_vs_spy is not None:
        if rel_strength_vs_spy >= 5:
            market_adj -= 4
            market_reasons.append(f"Outperforming SPY by {rel_strength_vs_spy:.1f}% — run has real momentum (-4)")
        elif rel_strength_vs_spy <= -2:
            market_adj += 3
            market_reasons.append(f"Underperforming SPY by {abs(rel_strength_vs_spy):.1f}% — stock-specific weakness (+3)")

    # ── Hard penalties ─────────────────────────────────────────────────────────
    base = sum(scores.values()) + market_adj
    penalty = 0
    penalty_reasons = market_reasons[:]

    # Earnings within 5 days = gap risk
    if earnings_calendar and earnings_calendar.get("has_upcoming"):
        days_to_earn = earnings_calendar.get("days_to_earnings", 99)
        if days_to_earn <= 5:
            penalty += 25
            penalty_reasons.append(f"Earnings in {days_to_earn}d — gap risk (-25)")

    # Already in downtrend — not a fresh reversal candidate
    ma50 = ind.get("ma50") or price
    if price < ma50:
        penalty += 10
        penalty_reasons.append("Price below MA50 — already in downtrend (-10)")

    # MA50 slope rising = strong uptrend = mean reversion thesis is weaker
    if ind.get("ma50_slope_rising"):
        penalty += 8
        penalty_reasons.append("MA50 slope rising — strong uptrend, mean reversion less likely (-8)")

    # High short interest = squeeze risk — don't short an already-crowded short
    if short_interest_pct is not None and short_interest_pct >= 15:
        penalty += 10
        penalty_reasons.append(f"Short interest {short_interest_pct:.0f}% — squeeze risk (-10)")
    elif short_interest_pct is not None and short_interest_pct >= 8:
        penalty += 5
        penalty_reasons.append(f"Short interest {short_interest_pct:.0f}% — elevated squeeze risk (-5)")

    # OBV confirming: buyers still in control
    if obv == "CONFIRMING" and ind.get("volume_surge_ratio", 1.0) >= 2.0:
        penalty += 8
        penalty_reasons.append("OBV confirming + high volume surge — buyers still in control (-8)")
    elif obv == "CONFIRMING":
        penalty += 3
        penalty_reasons.append("OBV still confirming — mild caution (-3)")

    total = max(0, min(round(base - penalty), 100))

    bonus_reasons = [r for r in [
        "RSI bearish divergence (+10)" if ind.get("rsi_bearish_divergence") else None,
        "MACD bearish crossover (+15)" if ind.get("macd_crossover_bearish") else None,
        "MACD histogram shrinking (+20)" if (macd_line > macd_signal and macd_hist < macd_hist_prev and macd_hist_prev > 0) else None,
        "OBV distribution" if obv == "DIVERGING_BEARISH" else None,
        f"Candlestick: {', '.join(candle_reasons)}" if candle_reasons else None,
        f"Distribution days: {dist_days}" if dist_days >= 2 else None,
        "ATR parabolic extension" if atr_ext_score > 0 else None,
    ] if r]
    bonus_reasons += penalty_reasons

    # Conviction: RSI exhaustion + MACD fading + at least 2 of (distribution/extension/candle)
    confirmations = 0
    if scores.get("rsi_exhaustion", 0) >= 9:      confirmations += 1
    if scores.get("momentum_fading", 0) >= 10:    confirmations += 1
    if scores.get("distribution", 0) >= 7:        confirmations += 1
    if scores.get("extension", 0) >= 8:           confirmations += 1
    if scores.get("candle_trigger", 0) >= 4:      confirmations += 1
    conviction_pass = total >= 65 and confirmations >= 3

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
