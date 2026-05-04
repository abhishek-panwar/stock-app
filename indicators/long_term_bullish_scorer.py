"""
Long-term bullish scorer — Friday scan, 60-180 day moves.

Signal philosophy:
  - De-weights short-term technicals entirely
  - Fundamentals split: growth/margins/FCF (Group 1) + valuation multiples (Group 2)
  - Insider accumulation weighted at 15 pts (was 25 — tightened per ChatGPT review)
  - Analyst conviction: consensus + mean target upside
  - Earnings beats = proxy for management execution quality
  - Trend (MA200, bull regime) = institutional position confirmation
  - Social velocity deliberately excluded — short-term noise signal

Changes from v1.0 (ChatGPT structural review):
  - Add Group 2: Valuation Context (15 pts) — forward P/E, PEG vs peers, multiple contraction
  - Reduce insider weight 25 → 15 pts (powerful but rare; was overriding weak fundamentals)
  - Raise conviction threshold 35 → 50 (35 let in "meh but not terrible" names)
  - Conviction logic: require 2 drivers + 1 confirmation (mirrors real re-rating mechanics)
  - PEG moved from fundamentals to valuation group
  - Trend: add higher-lows structure signal (institutional accumulation pattern)
"""

FORMULA_VERSION = "long_bullish_v2.0"


def compute_long_term_bullish_score(
    ind: dict,
    sentiment: dict,
    analyst: dict,
    earnings: dict,
    source: str = "nasdaq100",
    earnings_calendar: dict = None,
    analyst_target: dict = None,
    insider_buying: dict = None,
    fundamentals: dict = None,
    sector: str = None,
    sector_pe_ratios: dict = None,
) -> dict:
    """
    Returns score dict with breakdown and total (0–100).
    Higher score = stronger long-term bullish re-rating thesis.

    Groups (100 pts total):
      Fundamentals  25 pts  — revenue/earnings growth, margins, FCF
      Valuation     15 pts  — PEG, forward P/E vs sector, multiple expansion room
      Insider       15 pts  — executives buying own stock (powerful but rare)
      Analyst       20 pts  — consensus + mean target upside %
      Earnings      15 pts  — consecutive beats = management execution quality
      Trend         10 pts  — MA200 position, bull regime, higher-lows structure
    """
    scores = {}
    bonus = 0
    bonus_reasons = []

    price = ind.get("price", 0)

    # ── Group 1: Fundamentals — growth, margins, FCF (25 pts) ────────────────
    fund_score = 0
    if fundamentals:
        rev_growth  = fundamentals.get("revenue_growth_pct")
        earn_growth = fundamentals.get("earnings_growth_pct")
        op_margin   = fundamentals.get("operating_margin_pct")
        fcf         = fundamentals.get("free_cashflow")

        if rev_growth is not None:
            if rev_growth >= 25:
                fund_score += 10
                bonus_reasons.append(f"Revenue growth {rev_growth:.0f}% YoY (+10)")
            elif rev_growth >= 15:
                fund_score += 7
                bonus_reasons.append(f"Revenue growth {rev_growth:.0f}% YoY (+7)")
            elif rev_growth >= 8:
                fund_score += 4
            elif rev_growth < 0:
                fund_score -= 3

        if earn_growth is not None:
            if earn_growth >= 25:
                fund_score += 8
                bonus_reasons.append(f"Earnings growth {earn_growth:.0f}% YoY (+8)")
            elif earn_growth >= 10:
                fund_score += 5
                bonus_reasons.append(f"Earnings growth {earn_growth:.0f}% YoY (+5)")
            elif earn_growth < 0:
                fund_score -= 2

        if op_margin is not None:
            if op_margin >= 25:
                fund_score += 5
                bonus_reasons.append(f"Strong operating margin {op_margin:.0f}% (+5)")
            elif op_margin >= 15:
                fund_score += 3
            elif op_margin >= 5:
                fund_score += 1

        if fcf is not None and fcf > 0:
            fund_score += 4
            bonus_reasons.append("Positive FCF (+4)")

        # Debt/leverage — low debt = resilience, high debt = rate sensitivity headwind
        debt_to_equity = fundamentals.get("debt_to_equity")
        if debt_to_equity is not None:
            if debt_to_equity < 0.3:
                fund_score += 3
                bonus_reasons.append(f"Low leverage D/E {debt_to_equity:.2f} — balance sheet strength (+3)")
            elif debt_to_equity > 2.0:
                fund_score -= 3
                bonus_reasons.append(f"High leverage D/E {debt_to_equity:.2f} — rate sensitivity risk (-3)")

        # EPS revision trend — leading indicator of institutional repricing
        eps_trend = fundamentals.get("eps_revision_trend")
        if eps_trend == "RISING":
            fund_score += 5
            bonus_reasons.append("EPS estimates rising — analyst upgrades in progress (+5)")
        elif eps_trend == "FALLING":
            fund_score -= 4
            bonus_reasons.append("EPS estimates cut — analyst downgrades headwind (-4)")

    scores["fundamentals"] = round(min(max(fund_score, -10), 25), 1)

    # ── Group 2: Valuation Context (15 pts) ───────────────────────────────────
    # Core question: is the growth priced in, or is there multiple expansion room?
    val_score = 0
    if fundamentals:
        peg         = fundamentals.get("peg_ratio")
        trailing_pe = fundamentals.get("trailing_pe")
        fwd_pe      = fundamentals.get("forward_pe")

        if peg is not None:
            if 0 < peg < 1:
                val_score += 8
                bonus_reasons.append(f"PEG {peg:.2f} — growth underpriced vs peers (+8)")
            elif 1 <= peg < 1.5:
                val_score += 4
                bonus_reasons.append(f"PEG {peg:.2f} — reasonable valuation (+4)")
            elif peg >= 3:
                val_score -= 4  # already priced for perfection

        # Forward P/E reasonable (not already at peak multiple)
        if fwd_pe is not None:
            if 0 < fwd_pe <= 20:
                val_score += 5
                bonus_reasons.append(f"Forward P/E {fwd_pe:.1f} — room for multiple expansion (+5)")
            elif 20 < fwd_pe <= 30:
                val_score += 2
            elif fwd_pe > 50:
                val_score -= 3  # priced for perfection, no expansion room

        # Trailing P/E fallback if no forward P/E
        elif trailing_pe is not None and fwd_pe is None:
            if 0 < trailing_pe <= 20:
                val_score += 3
            elif trailing_pe > 60:
                val_score -= 2

        # Sector-relative PE — stock cheap vs sector average = multiple expansion room
        pe_for_comparison = fwd_pe or trailing_pe
        sector_avg_pe = (sector_pe_ratios or {}).get(sector) if sector else None
        if pe_for_comparison and sector_avg_pe and sector_avg_pe > 0:
            discount_pct = (sector_avg_pe - pe_for_comparison) / sector_avg_pe * 100
            if discount_pct >= 20:
                val_score += 5
                bonus_reasons.append(f"PE {pe_for_comparison:.0f} is {discount_pct:.0f}% below sector avg {sector_avg_pe:.0f} — deep discount to peers (+5)")
            elif discount_pct >= 10:
                val_score += 3
                bonus_reasons.append(f"PE {pe_for_comparison:.0f} below sector avg {sector_avg_pe:.0f} — room to re-rate (+3)")
            elif discount_pct <= -25:
                val_score -= 3
                bonus_reasons.append(f"PE {pe_for_comparison:.0f} is {abs(discount_pct):.0f}% above sector avg {sector_avg_pe:.0f} — premium multiple, less room to expand (-3)")

    scores["valuation"] = round(min(max(val_score, -6), 15), 1)

    # ── Group 3: Insider Buying (15 pts) ──────────────────────────────────────
    # Reduced from 25 pts — powerful when present but rare; was overriding weak fundamentals
    insider_score = 0
    if insider_buying and insider_buying.get("has_insider_buying"):
        strength  = insider_buying.get("signal_strength", "NONE")
        total_usd = insider_buying.get("total_purchased_usd", 0)
        n         = insider_buying.get("num_insiders", 1)
        total_str = f"${total_usd/1e6:.1f}M" if total_usd >= 1e6 else f"${total_usd/1e3:.0f}K"
        if strength == "STRONG":
            insider_score = 15
            bonus_reasons.append(f"Insider buying STRONG — {total_str} by {n} insider(s) (+15)")
        elif strength == "MODERATE":
            insider_score = 10
            bonus_reasons.append(f"Insider buying MODERATE — {total_str} by {n} insider(s) (+10)")
        else:
            insider_score = 5

    scores["insider"] = insider_score

    # ── Group 4: Analyst Conviction (20 pts) ──────────────────────────────────
    analyst_score = 0
    consensus = analyst.get("consensus", "HOLD")
    analyst_score += {"STRONG_BUY": 10, "BUY": 7, "HOLD": 3, "SELL": 0, "STRONG_SELL": 0}.get(consensus, 3)

    analyst_upside_pct = None
    if analyst_target and analyst_target.get("mean_target") and price > 0:
        upside_pct = (analyst_target["mean_target"] - price) / price * 100
        analyst_upside_pct = round(upside_pct, 1)
        if upside_pct >= 30:
            analyst_score += 10
            bonus_reasons.append(f"Analyst upside {upside_pct:.0f}% — strong institutional conviction (+10)")
        elif upside_pct >= 20:
            analyst_score += 7
            bonus_reasons.append(f"Analyst upside {upside_pct:.0f}% (+7)")
        elif upside_pct >= 10:
            analyst_score += 4

    scores["analyst"] = round(min(analyst_score, 20), 1)

    # ── Group 5: Earnings Quality (15 pts) ────────────────────────────────────
    earnings_score = 0
    consecutive = earnings.get("consecutive_beats", 0)
    total_beats  = earnings.get("beats", 0)
    if consecutive >= 4:
        earnings_score = 15
        bonus_reasons.append(f"{consecutive} consecutive earnings beats — institutional re-rating likely (+15)")
    elif consecutive >= 3:
        earnings_score = 11
        bonus_reasons.append(f"{consecutive} consecutive earnings beats (+11)")
    elif consecutive >= 2:
        earnings_score = 7
    elif consecutive >= 1:
        earnings_score = 4

    if total_beats >= 4 and consecutive < 2:
        earnings_score = max(earnings_score, 8)
        bonus_reasons.append("4/4 earnings beats (streak broken) — consistent execution (+8 floor)")
    elif total_beats >= 3 and consecutive == 0:
        earnings_score = max(earnings_score, 5)

    scores["earnings"] = earnings_score

    # ── Group 6: Trend (10 pts) — long-term structure only ────────────────────
    trend_score = 0
    ma50  = ind.get("ma50") or price
    ma200 = ind.get("ma200") or price

    if price > ma200:
        trend_score += 5
    if price > ma50 and ma50 > ma200:
        trend_score += 3
        bonus_reasons.append("Price above MA50 > MA200 — bull regime (+3)")
    if ind.get("adx", 0) > 25:
        trend_score += 1
    # Higher low = institutional accumulation pattern (not a short-term signal)
    if ind.get("higher_low"):
        trend_score += 1

    scores["trend"] = round(min(trend_score, 10), 1)

    # ── Source bonus ──────────────────────────────────────────────────────────
    if source == "both":
        bonus += 2
        bonus_reasons.append("Dual-list appearance (+2)")

    base  = sum(scores.values())
    total = min(round(base + bonus), 100)

    # Conviction: require 2 drivers (fundamentals/insider/analyst) + 1 confirmation (earnings/trend)
    drivers = 0
    if scores.get("fundamentals", 0) >= 10: drivers += 1
    if scores.get("valuation", 0) >= 5:     drivers += 1
    if scores.get("insider", 0) >= 10:      drivers += 1
    if scores.get("analyst", 0) >= 10:      drivers += 1
    confirmations = 0
    if scores.get("earnings", 0) >= 7:      confirmations += 1
    if scores.get("trend", 0) >= 5:         confirmations += 1
    conviction_pass = total >= 50 and drivers >= 2 and confirmations >= 1

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
