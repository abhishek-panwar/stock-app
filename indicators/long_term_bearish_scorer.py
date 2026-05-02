"""
Long-term bearish scorer — Friday scan, 60-180 day fundamental deterioration plays.

Target: liquid large-cap stocks where the business is structurally weakening —
not just technically extended (that's short-term Type 1). The thesis is that
declining fundamentals will eventually force a re-rating downward.

Signal philosophy:
  - Fundamental deterioration is the primary signal (revenue decline, margin compression,
    negative FCF, earnings misses)
  - Analyst downgrades / SELL consensus = institutional conviction that fundamentals are worse
  - Insider selling = management knows something is wrong
  - Below MA200 = price has already confirmed the fundamental weakness
  - Earnings misses = execution failure compounding over multiple quarters
  - Hard penalty for upcoming earnings within 10 days (gap risk both directions)
"""

FORMULA_VERSION = "long_bearish_v1.0"


def compute_long_term_bearish_score(
    ind: dict,
    sentiment: dict,
    analyst: dict,
    earnings: dict,
    source: str = "long_bearish_candidate",
    earnings_calendar: dict = None,
    analyst_target: dict = None,
    insider_buying: dict = None,
    fundamentals: dict = None,
) -> dict:
    """
    Returns score dict with breakdown and total (0–100).
    Higher score = stronger long-term bearish deterioration thesis.

    Groups (100 pts total):
      Fundamental Deterioration  35 pts  — revenue decline, margin compression, negative FCF, PEG overvalued
      Analyst Bearishness        25 pts  — SELL consensus, target cuts, downside to mean target
      Earnings Miss Pattern      20 pts  — consecutive misses, miss magnitude trend
      Insider Selling            10 pts  — management reducing exposure
      Structural Breakdown       10 pts  — below MA200, death cross, ADX confirming trend
    """
    scores = {}
    bonus = 0
    bonus_reasons = []

    price = ind.get("price", 0)

    # ── Group 1: Fundamental Deterioration (35 pts) ───────────────────────────
    deteri_score = 0
    if fundamentals:
        rev_growth  = fundamentals.get("revenue_growth_pct")
        earn_growth = fundamentals.get("earnings_growth_pct")
        op_margin   = fundamentals.get("operating_margin_pct")
        fcf         = fundamentals.get("free_cashflow")
        peg         = fundamentals.get("peg_ratio")
        trailing_pe = fundamentals.get("trailing_pe")

        if rev_growth is not None:
            if rev_growth <= -15:
                deteri_score += 12
                bonus_reasons.append(f"Revenue declining {rev_growth:.0f}% YoY (+12)")
            elif rev_growth <= -5:
                deteri_score += 8
                bonus_reasons.append(f"Revenue declining {rev_growth:.0f}% YoY (+8)")
            elif rev_growth <= 0:
                deteri_score += 4
            # Positive revenue growth is not a penalty — just no score

        if earn_growth is not None:
            if earn_growth <= -25:
                deteri_score += 10
                bonus_reasons.append(f"Earnings collapsing {earn_growth:.0f}% YoY (+10)")
            elif earn_growth <= -10:
                deteri_score += 7
                bonus_reasons.append(f"Earnings declining {earn_growth:.0f}% YoY (+7)")
            elif earn_growth <= 0:
                deteri_score += 3

        if op_margin is not None and op_margin < 5:
            deteri_score += 4
            bonus_reasons.append(f"Operating margin compressed to {op_margin:.0f}% (+4)")

        if fcf is not None and fcf < 0:
            deteri_score += 7
            bonus_reasons.append(f"Negative FCF — cash burn (+7)")

        # Overvalued vs. declining fundamentals = mean reversion candidate
        if peg is not None and peg > 3:
            deteri_score += 5
            bonus_reasons.append(f"PEG {peg:.2f} — overvalued with weak fundamentals (+5)")
        elif trailing_pe is not None and trailing_pe > 40 and (earn_growth or 0) <= 0:
            deteri_score += 4
            bonus_reasons.append(f"PE {trailing_pe:.0f} with no earnings growth (+4)")

    scores["fundamental_deterioration"] = round(min(max(deteri_score, 0), 35), 1)

    # ── Group 2: Analyst Bearishness (25 pts) ─────────────────────────────────
    analyst_score = 0
    consensus = analyst.get("consensus", "HOLD")
    analyst_score += {"STRONG_SELL": 12, "SELL": 9, "HOLD": 4, "BUY": 0, "STRONG_BUY": 0}.get(consensus, 4)

    analyst_upside_pct = None
    if analyst_target and analyst_target.get("mean_target") and price > 0:
        upside_pct = (analyst_target["mean_target"] - price) / price * 100
        analyst_upside_pct = round(upside_pct, 1)
        # Negative upside = analysts see downside from current price
        if upside_pct <= -20:
            analyst_score += 13
            bonus_reasons.append(f"Analyst consensus target {upside_pct:.0f}% below current — strong institutional bearishness (+13)")
        elif upside_pct <= -10:
            analyst_score += 9
            bonus_reasons.append(f"Analyst target {upside_pct:.0f}% below current (+9)")
        elif upside_pct <= 0:
            analyst_score += 5
        # Positive analyst upside with bearish consensus = conflicted, lower conviction
        elif upside_pct > 15 and consensus in ("SELL", "STRONG_SELL"):
            analyst_score = max(0, analyst_score - 4)  # conviction penalty

    scores["analyst_bearishness"] = round(min(analyst_score, 25), 1)

    # ── Group 3: Earnings Miss Pattern (20 pts) ───────────────────────────────
    # consecutive_beats from get_earnings_history — for bearish we care about misses
    # A stock with 0 consecutive beats has been missing; the more misses the worse
    total_beats     = earnings.get("beats", 0)
    consecutive_beats = earnings.get("consecutive_beats", 0)

    # Infer misses: if beats < 2 out of last 4Q, management is consistently missing
    miss_score = 0
    if total_beats == 0:
        miss_score = 20
        bonus_reasons.append("0/4 earnings beats — consistent execution failure (+20)")
    elif total_beats == 1:
        miss_score = 14
        bonus_reasons.append(f"Only {total_beats}/4 earnings beats — mostly missing (+14)")
    elif total_beats == 2:
        miss_score = 8
        bonus_reasons.append(f"{total_beats}/4 earnings beats — mixed execution (+8)")
    elif total_beats == 3:
        miss_score = 3  # one miss in 4Q — not great but not catastrophic

    # If consecutive_beats == 0 but total_beats > 0, the recent trend is misses
    if consecutive_beats == 0 and total_beats > 0:
        miss_score = min(miss_score + 5, 20)
        bonus_reasons.append("Most recent quarter missed — deteriorating execution (+5)")

    scores["earnings_misses"] = round(min(miss_score, 20), 1)

    # ── Group 4: Insider Selling (10 pts) ─────────────────────────────────────
    # Note: EDGAR data only captures BUYING via get_insider_buying().
    # No selling data → score 0. If buying is present, that's ANTI-bearish.
    insider_score = 0
    if insider_buying and insider_buying.get("has_insider_buying"):
        # Insiders are BUYING → this is bearish headwind, reduce conviction
        insider_score = 0
        bonus_reasons.append("Insider buying present — anti-bearish headwind (0, no score)")
    else:
        # No insider buying = neutral/mildly bearish (management not stepping in to support)
        insider_score = 5
        bonus_reasons.append("No insider buying — management not defending stock (+5)")

    scores["insider_selling"] = insider_score

    # ── Group 5: Structural Breakdown (10 pts) ────────────────────────────────
    struct_score = 0
    ma50  = ind.get("ma50") or price
    ma200 = ind.get("ma200") or price

    if price < ma200:
        struct_score += 5
        bonus_reasons.append("Price below MA200 — institutional trend broken (+5)")
    if ma50 < ma200:
        struct_score += 3
        bonus_reasons.append("Death cross (MA50 < MA200) — confirmed downtrend (+3)")
    if ind.get("adx", 0) > 25 and price < ma50:
        struct_score += 2  # strong trend AND price falling = trending down with conviction

    scores["structural_breakdown"] = round(min(struct_score, 10), 1)

    # ── Hard penalties ─────────────────────────────────────────────────────────
    base = sum(scores.values())
    penalty = 0
    penalty_reasons = []

    # Upcoming earnings within 10 days = gap risk in either direction
    if earnings_calendar and earnings_calendar.get("has_upcoming"):
        days_to_earn = earnings_calendar.get("days_to_earnings", 99)
        if days_to_earn <= 10:
            penalty += 20
            penalty_reasons.append(f"Earnings in {days_to_earn}d — gap risk in either direction (-20)")

    # Strong positive news = potential catalyst that could reverse the thesis
    news_s = sentiment.get("score", 0)
    if news_s > 0.5:
        penalty += 8
        penalty_reasons.append(f"Strong positive news sentiment {news_s:.2f} — thesis headwind (-8)")

    total = max(0, min(round(base - penalty), 100))
    bonus_reasons += penalty_reasons

    # Conviction: need fundamental deterioration + at least analyst or structure confirming
    confirmations = 0
    if scores.get("fundamental_deterioration", 0) >= 12:  confirmations += 1
    if scores.get("analyst_bearishness", 0) >= 9:         confirmations += 1
    if scores.get("earnings_misses", 0) >= 8:             confirmations += 1
    if scores.get("structural_breakdown", 0) >= 5:        confirmations += 1
    conviction_pass = total >= 35 and confirmations >= 2

    return {
        "total": total,
        "base": round(base),
        "bonus": -penalty,
        "bonus_reasons": bonus_reasons,
        "breakdown": scores,
        "formula_version": FORMULA_VERSION,
        "analyst_upside_pct": analyst_upside_pct,
        "earnings_calendar": earnings_calendar,
        "insider_buying": insider_buying,
        "conviction_pass": conviction_pass,
    }
