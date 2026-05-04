"""
Short-term bullish scorer — v2.0

Changes from v1.0 (based on ChatGPT structural review):
  - Groups rebalanced: Momentum 20, Trend 25, Volume 20, Structure 15,
    Sentiment 5, Catalyst 10, Quality 5 = 100 pts total
  - Bonus stacking capped at +20 to prevent score inflation
  - Extension penalty: price >8% above MA20 = -4 pts (late momentum)
  - RSI soft cap: RSI >72 without volume confirmation = -3 pts
  - OBV asymmetry fixed: bearish -5 (was -10), confirming +6 (was +7)
  - Structure raised 10→15: BB squeeze + NR7 volatility compression added
  - Sentiment reduced 10→5: social velocity noisy for large caps
  - External split into Catalyst (earnings/analyst/news, 10 pts) +
    Quality (fundamentals capped at 5 pts)
  - New: pullback quality (MA20 bounce + higher low + bullish engulfing)
  - New: gap up + holds VWAP
  - New: dollar volume liquidity bonus
"""

FORMULA_VERSION = "bullish_v2.0"


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
    scores = {}
    price = ind.get("price", 0)
    ma20  = ind.get("ma20") or price
    ma50  = ind.get("ma50") or price
    ma200 = ind.get("ma200") or price
    atr   = ind.get("atr") or (price * 0.02)
    ext_pct = (price - ma20) / ma20 * 100 if ma20 > 0 else 0

    # ── Group 1: Momentum (20 pts) ────────────────────────────────────────────
    rsi = ind.get("rsi", 50)
    if 55 <= rsi <= 70:
        rsi_score = 8
    elif 45 <= rsi < 55:
        rsi_score = 6
    elif rsi < 30:
        rsi_score = 4
    elif 30 <= rsi < 45:
        rsi_score = 3
    elif 70 < rsi <= 72:
        rsi_score = 2
    else:
        rsi_score = 0  # >72 or unknown

    macd_line      = ind.get("macd_line", 0)
    macd_signal    = ind.get("macd_signal", 0)
    macd_hist      = ind.get("macd_hist", 0)
    macd_hist_prev = ind.get("macd_hist_prev", 0)

    if ind.get("macd_crossover"):
        macd_score = 8
    elif ind.get("macd_crossover_recent"):
        macd_score = 6
    elif macd_line > macd_signal and macd_hist > macd_hist_prev > 0:
        macd_score = 4
    elif macd_line > macd_signal and macd_hist > 0:
        macd_score = 2
    else:
        macd_score = 0

    # ROC: mild reward — partial signal, not dominant (correlated with RSI/MACD)
    roc = ind.get("roc_5", 0) or 0
    roc_score = 4 if roc >= 5 else 2 if roc >= 2 else 0

    momentum_raw = rsi_score + macd_score + roc_score

    # Hard cap if RSI overbought without strong volume + MACD confirmation
    if rsi > 70 and not (ind.get("macd_crossover") and ind.get("golden_cross")):
        momentum_raw = min(momentum_raw, 8)

    # RSI soft penalty: >72 without volume surge = late momentum
    vsr = ind.get("volume_surge_ratio", 1.0)
    if rsi > 72 and vsr < 2.0:
        momentum_raw = max(0, momentum_raw - 3)

    scores["momentum"] = round(min(momentum_raw, 20), 1)

    # ── Group 2: Trend (25 pts) ───────────────────────────────────────────────
    if price > ma20 and ma20 > ma50 and ma50 > ma200:
        ma_score = 15
    elif price > ma20 and ma20 > ma50:
        ma_score = 12
    elif price > ma20 and price > ma50:
        ma_score = 9
    elif price > ma20:
        ma_score = 6
    elif price > ma50:
        ma_score = 3
    else:
        ma_score = 0

    adx = ind.get("adx", 20)
    adx_score = 8 if adx > 30 else 5 if adx >= 25 else 2

    # Pullback quality: MA20 bounce + higher low = clean continuation entry
    # This rewards pre-breakout entries rather than chasing extended moves
    pullback_score = 0
    pullback_reasons = []
    if ind.get("near_ma20_bounce") and price > ma20:
        pullback_score += 3
        pullback_reasons.append("MA20 bounce")
    if ind.get("higher_low"):
        pullback_score += 2
        pullback_reasons.append("higher low")
    if ind.get("bullish_engulfing"):
        pullback_score += 3
        pullback_reasons.append("bullish engulfing")

    trend_raw = ma_score + adx_score + min(pullback_score, 5)
    scores["trend"] = round(min(trend_raw, 25), 1)

    # ── Group 3: Volume (20 pts) ──────────────────────────────────────────────
    if vsr >= 3.0:
        vsurge_score = 8
    elif vsr >= 2.0:
        vsurge_score = 6
    elif vsr >= 1.5:
        vsurge_score = 3
    else:
        vsurge_score = 1

    obv = ind.get("obv_trend", "NEUTRAL")
    if obv == "CONFIRMING":
        obv_score = 6
    elif obv == "DIVERGING_BULLISH":
        obv_score = 5
    elif obv == "NEUTRAL":
        obv_score = 2
    else:
        obv_score = 0

    vwap_score = 3 if ind.get("price_above_vwap") else 0

    # Gap up + holds: institutional accumulation signal
    gap_score = 3 if ind.get("gap_up_holds") else 0

    volume_raw = vsurge_score + obv_score + vwap_score + gap_score

    # OBV bearish: reduced from -10 to -5 (less aggressive, noise reduction)
    if obv == "DIVERGING_BEARISH":
        volume_raw = max(0, volume_raw - 5)

    scores["volume"] = round(min(volume_raw, 20), 1)

    # ── Group 4: Structure (15 pts) ───────────────────────────────────────────
    struct_score = 0
    if ind.get("bb_breakout_up"):
        struct_score += 7
    elif ind.get("bb_squeeze") and ind.get("bb_width_pct", 1.0) <= 0.20:
        struct_score += 5
    if ind.get("nr7"):
        struct_score += 4  # narrowest range in 7 bars = compression before expansion
    if ind.get("atr_rising"):
        struct_score += 3
    if ind.get("near_52w_high"):
        struct_score += 1

    scores["structure"] = round(min(struct_score, 15), 1)

    # ── Group 5: Sentiment (5 pts) — reduced, noisy for large caps ───────────
    news_s = sentiment.get("score", 0)
    news_score = 3 if news_s > 0.6 else 2 if news_s > 0.3 else 0

    st_score = 0
    if social_velocity:
        st_vel = social_velocity.get("stocktwits_velocity_pct", 0)
        st_score = 2 if st_vel >= 500 else 1 if st_vel >= 200 else 0

    scores["sentiment"] = round(min(news_score + st_score, 5), 1)

    # ── Group 6: Catalyst (10 pts) — short-term relevant only ────────────────
    consensus = analyst.get("consensus", "HOLD")
    analyst_score = {"STRONG_BUY": 5, "BUY": 3, "HOLD": 1, "SELL": 0, "STRONG_SELL": 0}.get(consensus, 1)

    consecutive = earnings.get("consecutive_beats", 0)
    earnings_beat_score = min(consecutive, 3) if consecutive > 0 else 0

    # Upcoming earnings catalyst
    earnings_cat_score = 0
    if earnings_calendar and earnings_calendar.get("has_upcoming"):
        days_to_earn = earnings_calendar.get("days_to_earnings", 99)
        if 6 <= days_to_earn <= 21 and consecutive >= 2:
            earnings_cat_score = 3

    scores["catalyst"] = round(min(analyst_score + earnings_beat_score + earnings_cat_score, 10), 1)

    # ── Group 7: Quality / Fundamentals (5 pts max) ───────────────────────────
    quality_score = 0
    if fundamentals:
        rev_growth = fundamentals.get("revenue_growth_pct")
        earn_growth = fundamentals.get("earnings_growth_pct")
        op_margin = fundamentals.get("operating_margin_pct")
        fcf = fundamentals.get("free_cashflow")
        peg = fundamentals.get("peg_ratio")
        if rev_growth is not None and rev_growth >= 20:   quality_score += 2
        elif rev_growth is not None and rev_growth >= 10: quality_score += 1
        if earn_growth is not None and earn_growth >= 20: quality_score += 1
        if op_margin is not None and op_margin >= 20:     quality_score += 1
        if fcf is not None and fcf > 0:                   quality_score += 1
        if peg is not None and 0 < peg < 1:               quality_score += 1

    scores["quality"] = round(min(quality_score, 5), 1)

    # ── Extension penalty — late momentum, higher reversal risk ───────────────
    ext_penalty = 0
    ext_penalty_reasons = []
    if ext_pct >= 8:
        ext_penalty = 4
        ext_penalty_reasons.append(f"Price {ext_pct:.1f}% above MA20 — late momentum (-4)")

    # ── Bonuses (capped at +20 total) ─────────────────────────────────────────
    bonus = 0
    bonus_reasons = []

    if ind.get("golden_cross"):
        bonus += 3
        bonus_reasons.append("Golden cross (+3)")
    if ind.get("broke_52w_high") and vsr >= 1.5:
        bonus += 4
        bonus_reasons.append("52-week high breakout with volume (+4)")
    if source == "both":
        bonus += 2
        bonus_reasons.append("Dual-list appearance (+2)")

    if earnings_calendar and earnings_calendar.get("has_upcoming"):
        days_to_earn = earnings_calendar.get("days_to_earnings", 99)
        if days_to_earn <= 5:
            bonus -= 5
            bonus_reasons.append(f"Earnings in {days_to_earn}d — gap risk (-5)")
        else:
            label = "tomorrow" if days_to_earn <= 1 else f"in {days_to_earn}d"
            if consecutive >= 3:
                bonus += 8
                bonus_reasons.append(f"Earnings catalyst {label} + {consecutive} consecutive beats (+8)")
            elif consecutive >= 1:
                bonus += 4
                bonus_reasons.append(f"Earnings {label} + {consecutive} beat(s) (+4)")

    if analyst_target and analyst_target.get("mean_target") and price > 0:
        upside_pct = (analyst_target["mean_target"] - price) / price * 100
        if upside_pct >= 20:
            bonus += 4
            bonus_reasons.append(f"Analyst upside {upside_pct:.0f}% (+4)")

    if rel_strength_vs_spy is not None:
        if rel_strength_vs_spy >= 5:
            bonus += 5
            bonus_reasons.append(f"Outperforming SPY by {rel_strength_vs_spy:.1f}% (+5)")
        elif rel_strength_vs_spy >= 2:
            bonus += 2
            bonus_reasons.append(f"Outperforming SPY by {rel_strength_vs_spy:.1f}% (+2)")
        elif rel_strength_vs_spy <= -3:
            bonus -= 4
            bonus_reasons.append(f"Underperforming SPY by {abs(rel_strength_vs_spy):.1f}% (-4)")

    if sector_return_5d is not None:
        if sector_return_5d >= 3:
            bonus += 3
            bonus_reasons.append(f"Sector up {sector_return_5d:.1f}% (tailwind) (+3)")
        elif sector_return_5d <= -2:
            bonus -= 3
            bonus_reasons.append(f"Sector down {sector_return_5d:.1f}% (headwind) (-3)")

    if short_interest_pct is not None:
        if short_interest_pct >= 20:
            bonus += 5
            bonus_reasons.append(f"Short interest {short_interest_pct:.0f}% — squeeze potential (+5)")
        elif short_interest_pct >= 10:
            bonus += 2
            bonus_reasons.append(f"Short interest {short_interest_pct:.0f}% of float (+2)")

    if insider_buying and insider_buying.get("has_insider_buying"):
        strength  = insider_buying.get("signal_strength", "NONE")
        total_usd = insider_buying.get("total_purchased_usd", 0)
        n         = insider_buying.get("num_insiders", 1)
        if strength == "STRONG":
            bonus += 10
            bonus_reasons.append(f"Insider buying STRONG — ${total_usd/1e6:.1f}M by {n} insider(s) (+10)")
        elif strength == "MODERATE":
            bonus += 5
            bonus_reasons.append(f"Insider buying MODERATE — ${total_usd/1e3:.0f}K by {n} insider(s) (+5)")

    # Dollar volume liquidity bonus — prevents false signals in illiquid names
    avg_dv = ind.get("avg_dollar_volume", 0)
    if avg_dv >= 100_000_000:
        bonus += 2
        bonus_reasons.append("High liquidity ≥$100M daily (+2)")

    if pullback_reasons:
        bonus_reasons.append(f"Pullback quality: {', '.join(pullback_reasons)}")

    # Cap total bonus at +20
    if bonus > 20:
        bonus_reasons.append(f"Bonus capped at +20 (raw: +{bonus})")
        bonus = 20

    base  = sum(scores.values())
    total = min(round(base + bonus - ext_penalty), 100)
    total = max(0, total)

    analyst_upside_pct = None
    if analyst_target and analyst_target.get("mean_target") and price > 0:
        analyst_upside_pct = round((analyst_target["mean_target"] - price) / price * 100, 1)

    all_bonus_reasons = bonus_reasons + ext_penalty_reasons

    # Conviction: trend + at least 2 of (momentum/volume/structure/catalyst)
    confirmations = 0
    if scores.get("trend", 0) >= 15:      confirmations += 1
    if scores.get("momentum", 0) >= 12:   confirmations += 1
    if scores.get("volume", 0) >= 12:     confirmations += 1
    if scores.get("structure", 0) >= 8:   confirmations += 1
    if scores.get("catalyst", 0) >= 6:    confirmations += 1
    conviction_pass = total >= 45 and confirmations >= 3

    return {
        "total": total,
        "base": round(base),
        "bonus": bonus - ext_penalty,
        "bonus_reasons": all_bonus_reasons,
        "breakdown": scores,
        "formula_version": FORMULA_VERSION,
        "analyst_upside_pct": analyst_upside_pct,
        "earnings_calendar": earnings_calendar,
        "insider_buying": insider_buying,
        "conviction_pass": conviction_pass,
    }
