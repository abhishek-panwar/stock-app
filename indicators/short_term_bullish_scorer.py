"""
Short-term bullish scorer.

Optimized for stocks in uptrends with momentum confirmation.
Key differences from the old shared scorer:
  - Rewards RSI 50-70 (trending momentum) more than RSI <30 (oversold/falling knife)
  - Requires price above MA20 as a soft gate (scored, not hard filter)
  - Up-day volume bias check rewarded
  - MACD crossover + histogram expansion weighted heavily
  - Bearish signals (OBV distribution, bb_breakout_down) penalized more aggressively
"""

FORMULA_VERSION = "bullish_v1.1"


def compute_short_term_bullish_score(
    ind: dict,
    sentiment: dict,
    analyst: dict,
    earnings: dict,
    source: str = "nasdaq100",
    earnings_calendar: dict = None,
    analyst_target: dict = None,
    insider_buying: dict = None,
    fundamentals: dict = None,
    social_velocity: dict = None,
    rel_strength_vs_spy: float = None,
    sector_return_5d: float = None,
    short_interest_pct: float = None,
) -> dict:
    """
    Returns score dict with breakdown and total (0–100).
    Designed for stocks that are in or entering uptrends.
    """
    scores = {}
    price = ind.get("price", 0)

    # ── Group 1: Momentum (25 pts) ─────────────────────────────────────────────
    rsi = ind.get("rsi", 50)

    # Reward trending momentum (50-70) > oversold bounce (<30)
    # RSI 50-70 = healthy uptrend. RSI <30 = catching a falling knife.
    if 55 <= rsi <= 70:
        rsi_score = 10
    elif 45 <= rsi < 55:
        rsi_score = 7
    elif rsi < 30:
        rsi_score = 5   # oversold but risky — could keep falling
    elif 30 <= rsi < 45:
        rsi_score = 4
    elif rsi > 75:
        rsi_score = 0   # overbought — exhaustion zone
    elif 70 < rsi <= 75:
        rsi_score = 2
    else:
        rsi_score = 3

    macd_line    = ind.get("macd_line", 0)
    macd_signal  = ind.get("macd_signal", 0)
    macd_hist    = ind.get("macd_hist", 0)
    macd_hist_prev = ind.get("macd_hist_prev", 0)

    if ind.get("macd_crossover"):
        macd_score = 10   # fresh bullish crossover = strongest signal
    elif ind.get("macd_crossover_recent"):
        macd_score = 8
    elif macd_line > macd_signal and macd_hist > macd_hist_prev > 0:
        macd_score = 6   # histogram expanding = acceleration
    elif macd_line > macd_signal and macd_hist > 0:
        macd_score = 4
    else:
        macd_score = 0

    roc = ind.get("roc_5", 0) or 0
    if roc >= 5:
        roc_score = 5
    elif roc >= 2:
        roc_score = 3
    elif roc >= 0:
        roc_score = 1
    else:
        roc_score = 0   # negative ROC = stock declining, no reward

    # Bullish RSI divergence: price lower but RSI higher = hidden accumulation, leading signal
    rsi_div_score = 3 if ind.get("rsi_divergence") else 0

    momentum_raw = rsi_score + macd_score + roc_score + rsi_div_score

    # Hard cap if RSI overbought without both MACD + golden cross confirmation
    if rsi > 70 and not (ind.get("macd_crossover") and ind.get("golden_cross")):
        momentum_raw = min(momentum_raw, 10)

    scores["momentum"] = round(min(momentum_raw, 25), 1)

    # ── Group 2: Trend (25 pts) — upward bias, weighted higher than old scorer ─
    ma20  = ind.get("ma20") or price
    ma50  = ind.get("ma50") or price
    ma200 = ind.get("ma200") or price

    # Strong uptrend alignment required for high score
    if price > ma20 and ma20 > ma50 and ma50 > ma200:
        ma_score = 15   # full alignment — price above all MAs in order
    elif price > ma20 and ma20 > ma50:
        ma_score = 12
    elif price > ma20 and price > ma50:
        ma_score = 9
    elif price > ma20:
        ma_score = 6
    elif price > ma50:
        ma_score = 3
    else:
        ma_score = 0    # below MA20 and MA50 = not a bullish setup

    adx = ind.get("adx", 20)
    adx_score = 8 if adx > 30 else 5 if adx >= 25 else 2

    # Rising MA50 confirms the uptrend has momentum, not just current price level
    ma50_slope_score = 2 if ind.get("ma50_slope_rising") else 0

    trend_raw = ma_score + adx_score + ma50_slope_score
    scores["trend"] = round(min(trend_raw, 25), 1)

    # ── Group 3: Volume (20 pts) ───────────────────────────────────────────────
    vsr = ind.get("volume_surge_ratio", 1.0)
    if vsr >= 3.0:
        vsurge_score = 10
    elif vsr >= 2.0:
        vsurge_score = 7
    elif vsr >= 1.5:
        vsurge_score = 4
    else:
        vsurge_score = 1

    obv = ind.get("obv_trend", "NEUTRAL")
    if obv == "CONFIRMING":
        obv_score = 7
    elif obv == "DIVERGING_BULLISH":
        obv_score = 5   # price falling but OBV rising = accumulation
    elif obv == "NEUTRAL":
        obv_score = 2
    else:
        obv_score = 0   # DIVERGING_BEARISH or DECLINING = distribution

    vwap_score = 3 if ind.get("price_above_vwap") else 0

    volume_raw = vsurge_score + obv_score + vwap_score

    # Penalize bearish OBV strongly — distribution while scoring bullish is a red flag
    if obv == "DIVERGING_BEARISH":
        volume_raw = max(0, volume_raw - 10)

    # Distribution days: institutional selling pattern reduces volume score
    dist_days = ind.get("distribution_days", 0)
    if dist_days >= 5:
        volume_raw = max(0, volume_raw - 10)
    elif dist_days >= 3:
        volume_raw = max(0, volume_raw - 5)

    scores["volume"] = round(min(volume_raw, 20), 1)

    # ── Group 4: Volatility / Structure (10 pts) ──────────────────────────────
    struct_score = 0
    if ind.get("bb_breakout_up"):
        struct_score += 6
    elif ind.get("bb_squeeze") and ind.get("bb_width_pct", 1.0) <= 0.20:
        struct_score += 3   # squeeze building, not yet broken out
    if ind.get("atr_rising"):
        struct_score += 3

    # Entry quality signals: reward low-risk pullback entries and structural confirmation
    if ind.get("near_ma20_bounce"):
        struct_score += 3   # tight pullback to MA20 with confirmation = textbook setup
    if ind.get("higher_low"):
        struct_score += 2   # buyers stepping in higher = uptrend intact
    if ind.get("gap_up_holds"):
        struct_score += 2   # institutional conviction gap, not a gap-and-trap
    if ind.get("nr7") or (ind.get("bb_squeeze") and ind.get("bb_width_pct", 1.0) <= 0.20):
        struct_score += 2   # volatility compression = coil before breakout

    # Bearish candlestick penalties: demand rejection patterns lower structure quality
    if ind.get("blowoff_top"):
        struct_score -= 5   # exhaustion pattern — wrong entry timing
    if ind.get("bearish_engulfing"):
        struct_score -= 3   # demand rejection candle
    if ind.get("shooting_star"):
        struct_score -= 2   # price rejected at highs
    if ind.get("upper_wick_rejection") and not ind.get("shooting_star"):
        struct_score -= 1   # minor upper wick rejection (no double-penalty with shooting_star)

    scores["structure"] = round(min(max(struct_score, -5), 10), 1)

    # ── Group 5: Sentiment (10 pts) ───────────────────────────────────────────
    news_s = sentiment.get("score", 0)
    if news_s > 0.6:
        news_score = 5
    elif news_s > 0.3:
        news_score = 3
    elif news_s > -0.3:
        news_score = 1
    else:
        news_score = 0

    st_score = 0
    if social_velocity:
        st_vel = social_velocity.get("stocktwits_velocity_pct", 0)
        if st_vel >= 500:   st_score = 4
        elif st_vel >= 200: st_score = 3
        elif st_vel >= 50:  st_score = 1

    rd_score = 0
    if social_velocity:
        rd_vel = social_velocity.get("reddit_velocity_pct", 0)
        if rd_vel >= 500:   rd_score = 3
        elif rd_vel >= 200: rd_score = 2
        elif rd_vel >= 50:  rd_score = 1

    bull_score = 0
    if social_velocity and (st_score >= 1 or rd_score >= 1):
        bull_ratio = social_velocity.get("stocktwits_bull_ratio", 0.5)
        if bull_ratio >= 0.70:  bull_score = 2
        elif bull_ratio >= 0.60: bull_score = 1

    scores["sentiment"] = round(min(news_score + st_score + rd_score + bull_score, 10), 1)

    # ── Group 6: External (10 pts) ─────────────────────────────────────────────
    consensus = analyst.get("consensus", "HOLD")
    analyst_score = {"STRONG_BUY": 6, "BUY": 4, "HOLD": 2, "SELL": 0, "STRONG_SELL": 0}.get(consensus, 2)
    consecutive = earnings.get("consecutive_beats", 0)
    earnings_score = min(consecutive + 1, 4) if consecutive > 0 else 0
    scores["external"] = round(min(analyst_score + earnings_score, 10), 1)

    # ── Bonuses ───────────────────────────────────────────────────────────────
    bonus = 0
    bonus_reasons = []

    if ind.get("golden_cross"):
        bonus += 3
        bonus_reasons.append("Golden cross (+3)")
    if ind.get("broke_52w_high") and ind.get("volume_surge_ratio", 1) >= 1.5:
        bonus += 4
        bonus_reasons.append("52-week high breakout with volume (+4)")
    if source == "both":
        bonus += 3
        bonus_reasons.append("Dual-list appearance (+3)")

    if earnings_calendar and earnings_calendar.get("has_upcoming"):
        consecutive = earnings.get("consecutive_beats", 0)
        days_to_earn = earnings_calendar.get("days_to_earnings", 99)
        if days_to_earn <= 5:
            bonus -= 5
            bonus_reasons.append(f"Earnings in {days_to_earn}d — gap risk (-5)")
        else:
            label = "tomorrow" if days_to_earn <= 1 else f"in {days_to_earn}d"
            if consecutive >= 3:
                bonus += 10
                bonus_reasons.append(f"Earnings catalyst {label} + {consecutive} consecutive beats (+10)")
            elif consecutive >= 1:
                bonus += 5
                bonus_reasons.append(f"Earnings {label} + {consecutive} beat(s) (+5)")

    if analyst_target and analyst_target.get("mean_target") and price > 0:
        upside_pct = (analyst_target["mean_target"] - price) / price * 100
        if upside_pct >= 20:
            bonus += 5
            bonus_reasons.append(f"Analyst upside {upside_pct:.0f}% (+5)")

    # Relative strength vs SPY — stock outperforming market = own catalyst, not just tide
    if rel_strength_vs_spy is not None:
        if rel_strength_vs_spy >= 5:
            bonus += 6
            bonus_reasons.append(f"Outperforming SPY by {rel_strength_vs_spy:.1f}% — stock-specific catalyst (+6)")
        elif rel_strength_vs_spy >= 2:
            bonus += 3
            bonus_reasons.append(f"Outperforming SPY by {rel_strength_vs_spy:.1f}% (+3)")
        elif rel_strength_vs_spy <= -3:
            bonus -= 4
            bonus_reasons.append(f"Underperforming SPY by {abs(rel_strength_vs_spy):.1f}% — rising with tide only (-4)")

    # Sector momentum — sector ETF confirming the move strengthens thesis
    if sector_return_5d is not None:
        if sector_return_5d >= 3:
            bonus += 4
            bonus_reasons.append(f"Sector up {sector_return_5d:.1f}% (tailwind) (+4)")
        elif sector_return_5d <= -2:
            bonus -= 3
            bonus_reasons.append(f"Sector down {sector_return_5d:.1f}% (headwind) (-3)")

    # Short interest — high SI + bullish setup = squeeze potential
    if short_interest_pct is not None:
        if short_interest_pct >= 20:
            bonus += 6
            bonus_reasons.append(f"Short interest {short_interest_pct:.0f}% of float — squeeze potential (+6)")
        elif short_interest_pct >= 10:
            bonus += 3
            bonus_reasons.append(f"Short interest {short_interest_pct:.0f}% of float (+3)")

    if insider_buying and insider_buying.get("has_insider_buying"):
        strength  = insider_buying.get("signal_strength", "NONE")
        total_usd = insider_buying.get("total_purchased_usd", 0)
        n         = insider_buying.get("num_insiders", 1)
        if strength == "STRONG":
            bonus += 15
            bonus_reasons.append(f"Insider buying STRONG — ${total_usd/1e6:.1f}M by {n} insider(s) (+15)")
        elif strength == "MODERATE":
            bonus += 8
            bonus_reasons.append(f"Insider buying MODERATE — ${total_usd/1e3:.0f}K by {n} insider(s) (+8)")

    if fundamentals:
        rev_growth  = fundamentals.get("revenue_growth_pct")
        earn_growth = fundamentals.get("earnings_growth_pct")
        op_margin   = fundamentals.get("operating_margin_pct")
        fcf         = fundamentals.get("free_cashflow")
        peg         = fundamentals.get("peg_ratio")
        if rev_growth is not None and rev_growth >= 20:
            bonus += 6; bonus_reasons.append(f"Revenue growth {rev_growth:.0f}% YoY (+6)")
        elif rev_growth is not None and rev_growth >= 10:
            bonus += 3; bonus_reasons.append(f"Revenue growth {rev_growth:.0f}% YoY (+3)")
        if earn_growth is not None and earn_growth >= 20:
            bonus += 4; bonus_reasons.append(f"Earnings growth {earn_growth:.0f}% YoY (+4)")
        if op_margin is not None and op_margin >= 20:
            bonus += 3; bonus_reasons.append(f"Strong operating margin {op_margin:.0f}% (+3)")
        if fcf is not None and fcf > 0:
            bonus += 2; bonus_reasons.append(f"Positive FCF (+2)")
        if peg is not None and 0 < peg < 1:
            bonus += 4; bonus_reasons.append(f"PEG {peg:.2f} — undervalued growth (+4)")

    base  = sum(scores.values())
    bonus = min(bonus, 25)  # cap bonus — prevents fundamental inflation of weak technicals
    total = min(round(base + bonus), 100)

    analyst_upside_pct = None
    if analyst_target and analyst_target.get("mean_target") and price > 0:
        analyst_upside_pct = round((analyst_target["mean_target"] - price) / price * 100, 1)

    # Conviction filter: need trend + at least one of momentum/volume confirming
    # Also block if active distribution or bearish reversal candle present
    confirmations = 0
    if scores.get("trend", 0) >= 15:     confirmations += 1
    if scores.get("momentum", 0) >= 15:  confirmations += 1
    if scores.get("volume", 0) >= 12:    confirmations += 1
    no_major_bearish = not (
        ind.get("distribution_days", 0) >= 3 or
        ind.get("blowoff_top") or
        ind.get("bearish_engulfing")
    )
    conviction_pass = total >= 45 and confirmations >= 2 and no_major_bearish

    return {
        "total": total,
        "base": round(base),
        "bonus": bonus,
        "bonus_reasons": bonus_reasons,
        "breakdown": scores,
        "formula_version": FORMULA_VERSION,
        "analyst_upside_pct": analyst_upside_pct,
        "earnings_calendar": earnings_calendar,
        "insider_buying": insider_buying,
        "conviction_pass": conviction_pass,
    }
