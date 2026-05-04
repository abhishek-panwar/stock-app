"""
Long-term bearish scorer — Friday scan, 60-180 day fundamental deterioration plays.

Target: liquid large-cap stocks where the business is structurally weakening —
not just technically extended (that's short-term Type 1). The thesis is that
declining fundamentals will eventually force a re-rating downward.

Signal philosophy:
  - Fundamental deterioration is the primary signal (revenue decline, margin compression,
    negative FCF, earnings misses)
  - Analyst downgrades / SELL consensus = institutional conviction that fundamentals are worse
  - Narrative/structural breakdown = why institutions will reprice the stock lower (new in v2.0)
  - Below MA200 = price has already confirmed the fundamental weakness
  - Earnings misses = execution failure compounding over multiple quarters
  - Hard penalty for upcoming earnings within 10 days (gap risk both directions)

Changes from v1.0 (ChatGPT structural review):
  - Add Group 6: Narrative/Structural Risk (15 pts) — competitive disruption, market share loss,
    secular decline, regulatory overhang (the "why will it go down" group)
  - Raise score threshold 30 → 40, conviction threshold 35 → 45
  - Reduce analyst group 25 → 20 pts (analysts lag; shift weight to narrative group)
  - Compress confidence scale in prompt: 4 factors → 75-85 (was 80-90)
  - PEG > 3 logic replaced with P/E vs growth decoupling (more reliable)
  - Remove BEARISH SIGNAL SCORE from prompt (anchoring fix)
  - Add macro/sector context to prompt
  - Add "why now" forward catalyst requirement to prompt
  - Add category-based days-to-target mapping
"""

FORMULA_VERSION = "long_bearish_v2.0"


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
    narrative_risk: dict = None,
    sector: str = None,
    sector_pe_ratios: dict = None,
) -> dict:
    """
    Returns score dict with breakdown and total (0–100).
    Higher score = stronger long-term bearish deterioration thesis.

    Groups (100 pts total):
      Fundamental Deterioration  30 pts  — revenue decline, margin compression, negative FCF
      Analyst Bearishness        20 pts  — SELL consensus, target cuts, downside to mean target
      Earnings Miss Pattern      20 pts  — consecutive misses, miss magnitude trend
      Narrative/Structural Risk  15 pts  — competitive disruption, secular decline, regulatory risk
      Insider Activity           modifier — buying penalizes score; silence strengthens thesis
      Structural Breakdown       15 pts  — below MA200, death cross, ADX confirming trend
    """
    scores = {}
    bonus = 0
    bonus_reasons = []

    price = ind.get("price", 0)

    # ── Group 1: Fundamental Deterioration (30 pts) ───────────────────────────
    deteri_score = 0
    if fundamentals:
        rev_growth  = fundamentals.get("revenue_growth_pct")
        earn_growth = fundamentals.get("earnings_growth_pct")
        op_margin   = fundamentals.get("operating_margin_pct")
        fcf         = fundamentals.get("free_cashflow")
        trailing_pe = fundamentals.get("trailing_pe")
        fwd_pe      = fundamentals.get("forward_pe")

        if rev_growth is not None:
            if rev_growth <= -15:
                deteri_score += 12
                bonus_reasons.append(f"Revenue declining {rev_growth:.0f}% YoY (+12)")
            elif rev_growth <= -5:
                deteri_score += 8
                bonus_reasons.append(f"Revenue declining {rev_growth:.0f}% YoY (+8)")
            elif rev_growth <= 0:
                deteri_score += 4

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
            bonus_reasons.append("Negative FCF — cash burn (+7)")

        # P/E vs growth decoupling: high P/E with declining earnings = compression candidate
        # More reliable than PEG alone when earnings are collapsing (denominator problem)
        if trailing_pe is not None and trailing_pe > 40 and (earn_growth or 0) <= 0:
            deteri_score += 5
            bonus_reasons.append(f"P/E {trailing_pe:.0f} with declining earnings — multiple compression risk (+5)")
        elif fwd_pe is not None and fwd_pe > 35 and (earn_growth or 0) <= -10:
            deteri_score += 4
            bonus_reasons.append(f"Fwd P/E {fwd_pe:.0f} while earnings collapsing — compression risk (+4)")

        # Debt/leverage — high debt + deteriorating earnings = distress amplifier
        debt_to_equity = fundamentals.get("debt_to_equity")
        if debt_to_equity is not None:
            if debt_to_equity > 2.0 and (earn_growth or 0) <= 0:
                deteri_score += 5
                bonus_reasons.append(f"High leverage D/E {debt_to_equity:.2f} with declining earnings — distress risk (+5)")
            elif debt_to_equity > 3.0:
                deteri_score += 3
                bonus_reasons.append(f"Excessive leverage D/E {debt_to_equity:.2f} — balance sheet risk (+3)")
            elif debt_to_equity < 0.3:
                deteri_score -= 3
                bonus_reasons.append(f"Low leverage D/E {debt_to_equity:.2f} — resilient balance sheet, limits downside (-3)")

        # EPS revision trend — forward-looking analyst conviction signal
        eps_trend = fundamentals.get("eps_revision_trend")
        if eps_trend == "FALLING":
            deteri_score += 6
            bonus_reasons.append("EPS estimates being cut — institutional conviction on deterioration (+6)")
        elif eps_trend == "RISING":
            deteri_score -= 5
            bonus_reasons.append("EPS estimates rising — contradicts bearish thesis (-5)")

        # Sector-relative PE — stock expensive vs sector = compression candidate
        pe_for_comparison = fundamentals.get("forward_pe") or fundamentals.get("trailing_pe")
        sector_avg_pe = (sector_pe_ratios or {}).get(sector) if sector else None
        if pe_for_comparison and sector_avg_pe and sector_avg_pe > 0:
            premium_pct = (pe_for_comparison - sector_avg_pe) / sector_avg_pe * 100
            if premium_pct >= 30:
                deteri_score += 6
                bonus_reasons.append(f"PE {pe_for_comparison:.0f} is {premium_pct:.0f}% above sector avg {sector_avg_pe:.0f} — expensive vs peers, compression candidate (+6)")
            elif premium_pct >= 15:
                deteri_score += 3
                bonus_reasons.append(f"PE {pe_for_comparison:.0f} above sector avg {sector_avg_pe:.0f} — valuation stretched (+3)")
            elif premium_pct <= -20:
                deteri_score -= 3
                bonus_reasons.append(f"PE {pe_for_comparison:.0f} below sector avg {sector_avg_pe:.0f} — already cheap, limits compression (-3)")

    scores["fundamental_deterioration"] = round(min(max(deteri_score, 0), 30), 1)

    # ── Group 2: Analyst Bearishness (20 pts) — reduced from 25; analysts lag ──
    analyst_score = 0
    consensus = analyst.get("consensus", "HOLD")
    analyst_score += {"STRONG_SELL": 10, "SELL": 7, "HOLD": 3, "BUY": 0, "STRONG_BUY": 0}.get(consensus, 3)

    analyst_upside_pct = None
    if analyst_target and analyst_target.get("mean_target") and price > 0:
        upside_pct = (analyst_target["mean_target"] - price) / price * 100
        analyst_upside_pct = round(upside_pct, 1)
        if upside_pct <= -20:
            analyst_score += 10
            bonus_reasons.append(f"Analyst target {upside_pct:.0f}% below current — strong institutional bearishness (+10)")
        elif upside_pct <= -10:
            analyst_score += 7
            bonus_reasons.append(f"Analyst target {upside_pct:.0f}% below current (+7)")
        elif upside_pct <= 0:
            analyst_score += 4
        elif upside_pct > 30 and consensus in ("SELL", "STRONG_SELL"):
            analyst_score = max(0, analyst_score - 8)
        elif upside_pct > 15 and consensus in ("SELL", "STRONG_SELL"):
            analyst_score = max(0, analyst_score - 4)

    scores["analyst_bearishness"] = round(min(analyst_score, 20), 1)

    # ── Group 3: Earnings Miss Pattern (20 pts) ───────────────────────────────
    total_beats       = earnings.get("beats", 0)
    consecutive_beats = earnings.get("consecutive_beats", 0)

    miss_score = 0
    if total_beats == 0:
        miss_score = 20
        bonus_reasons.append("0/4 earnings beats — consistent execution failure (+20)")
    elif total_beats == 1:
        miss_score = 12
        bonus_reasons.append(f"Only {total_beats}/4 earnings beats — mostly missing (+12)")
    elif total_beats == 2:
        miss_score = 6
        bonus_reasons.append(f"{total_beats}/4 earnings beats — mixed execution (+6)")
    elif total_beats == 3:
        miss_score = 2

    if consecutive_beats == 0 and total_beats > 0:
        miss_score = min(miss_score + 4, 20)
        bonus_reasons.append("Most recent quarter missed — deteriorating execution trend (+4)")

    scores["earnings_misses"] = round(min(miss_score, 20), 1)

    # ── Group 4: Narrative / Structural Risk (15 pts) — NEW in v2.0 ───────────
    # "Why will institutions reprice this lower?" — the story-break group
    # Populated via narrative_risk dict passed from universe screener or manual flags
    narrative_score = 0
    if narrative_risk:
        if narrative_risk.get("competitive_disruption"):
            narrative_score += 8
            bonus_reasons.append("Competitive disruption / market share loss (+8)")
        if narrative_risk.get("secular_decline"):
            narrative_score += 7
            bonus_reasons.append("Secular demand decline / industry shrinking (+7)")
        if narrative_risk.get("regulatory_risk"):
            narrative_score += 5
            bonus_reasons.append("Regulatory / legal overhang (+5)")
        if narrative_risk.get("pricing_compression"):
            narrative_score += 5
            bonus_reasons.append("Pricing compression / margin structurally declining (+5)")
        if narrative_risk.get("business_model_risk"):
            narrative_score += 6
            bonus_reasons.append("Business model under structural pressure (+6)")

    scores["narrative_risk"] = round(min(narrative_score, 15), 1)

    # ── Group 5: Insider Activity (conviction modifier) ───────────────────────
    # EDGAR only provides buying data. Absence of buying during deterioration = thesis support.
    # Insider buying PRESENT = management conviction against short thesis → penalty.
    insider_score = 0
    if insider_buying and insider_buying.get("has_insider_buying"):
        strength = insider_buying.get("signal_strength", "NONE")
        if strength == "STRONG":
            insider_score = -10
            bonus_reasons.append("Insider buying STRONG — management conviction contradicts bearish thesis (-10)")
        else:
            insider_score = -5
            bonus_reasons.append("Insider buying present — anti-bearish headwind (-5)")

    scores["insider_modifier"] = insider_score

    # ── Group 6: Structural Breakdown (15 pts) — raised from 10 ──────────────
    struct_score = 0
    ma50  = ind.get("ma50") or price
    ma200 = ind.get("ma200") or price

    if price < ma200:
        struct_score += 6
        bonus_reasons.append("Price below MA200 — institutional trend broken (+6)")
    if ma50 < ma200:
        struct_score += 5
        bonus_reasons.append("Death cross (MA50 < MA200) — confirmed downtrend (+5)")
    if ind.get("adx", 0) > 25 and price < ma50:
        struct_score += 4
        bonus_reasons.append("Strong ADX with price below MA50 — trending down with conviction (+4)")

    scores["structural_breakdown"] = round(min(struct_score, 15), 1)

    # ── Hard penalties ─────────────────────────────────────────────────────────
    base = sum(scores.values())
    penalty = 0
    penalty_reasons = []

    if earnings_calendar and earnings_calendar.get("has_upcoming"):
        days_to_earn = earnings_calendar.get("days_to_earnings", 99)
        if days_to_earn <= 10:
            penalty += 20
            penalty_reasons.append(f"Earnings in {days_to_earn}d — gap risk in either direction (-20)")

    news_s = sentiment.get("score", 0)
    if news_s < -0.3:
        bonus += 5
        bonus_reasons.append(f"Negative news sentiment {news_s:.2f} — confirms deterioration thesis (+5)")
    elif news_s < 0:
        bonus += 2
    elif news_s > 0.5:
        penalty += 8
        penalty_reasons.append(f"Strong positive news sentiment {news_s:.2f} — thesis headwind (-8)")

    total = max(0, min(round(base + bonus - penalty), 100))
    bonus_reasons += penalty_reasons

    # Conviction: fundamental deterioration + at least 2 confirming groups
    # Raised from 35 → 45 to filter "meh but weak" names
    confirmations = 0
    if scores.get("fundamental_deterioration", 0) >= 10: confirmations += 1
    if scores.get("analyst_bearishness", 0) >= 7:        confirmations += 1
    if scores.get("earnings_misses", 0) >= 8:            confirmations += 1
    if scores.get("narrative_risk", 0) >= 5:             confirmations += 1
    if scores.get("structural_breakdown", 0) >= 6:       confirmations += 1
    conviction_pass = total >= 45 and confirmations >= 2

    return {
        "total": total,
        "base": round(base),
        "penalty": penalty,
        "bonus_reasons": bonus_reasons,
        "breakdown": scores,
        "formula_version": FORMULA_VERSION,
        "analyst_upside_pct": analyst_upside_pct,
        "earnings_calendar": earnings_calendar,
        "insider_buying": insider_buying,
        "conviction_pass": conviction_pass,
    }
