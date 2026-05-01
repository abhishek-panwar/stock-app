FORMULA_VERSION = "v1.0"


def compute_signal_score(ind: dict, sentiment: dict, analyst: dict, earnings: dict,
                         timeframe: str = "short", source: str = "nasdaq100",
                         earnings_calendar: dict = None, analyst_target: dict = None,
                         insider_buying: dict = None, fundamentals: dict = None,
                         social_velocity: dict = None) -> dict:
    """
    Returns a score dict with breakdown and total (0–100).
    timeframe param kept for backwards compatibility but weights are uniform.
    source: 'nasdaq100' | 'hot_stock' | 'both'
    """
    weights = {"momentum": 1.0, "trend": 1.0, "volatility": 1.0, "volume": 1.0, "sentiment": 1.0, "external": 1.0}
    scores = {}

    # ── Group 1: Momentum (25 pts max) ────────────────────────────────────────
    rsi = ind.get("rsi", 50)
    rsi_score = 0
    if rsi < 30:
        rsi_score = 11
    elif rsi < 40:
        rsi_score = 7
    elif 60 <= rsi <= 70:
        rsi_score = 6
    elif rsi > 70:
        rsi_score = 1

    macd_score = 0
    macd_line = ind.get("macd_line", 0)
    macd_signal = ind.get("macd_signal", 0)
    macd_hist = ind.get("macd_hist", 0)
    macd_hist_prev = ind.get("macd_hist_prev", 0)
    macd_crossover_recent = ind.get("macd_crossover_recent", False)
    
    if ind.get("macd_crossover"):
        macd_score = 8
    elif macd_crossover_recent:
        macd_score = 7
    elif macd_line > macd_signal and macd_hist > 0 and macd_hist_prev > 0:
        macd_score = 5
    elif macd_line > macd_signal and macd_hist > 0:
        macd_score = 3
    else:
        macd_score = 0
    
    roc = ind.get("roc_5", 0) or ind.get("roc_20", 0) or 0
    roc_score = 0
    if abs(roc) >= 5:
        roc_score = 5
    elif abs(roc) >= 2:
        roc_score = 3
    else:
        roc_score = 1

    momentum_raw = rsi_score + macd_score + roc_score

    # Penalize flat/declining MACD even if line is above signal
    if macd_line > macd_signal and macd_hist <= 0:
        momentum_raw = max(0, momentum_raw - 15)
    
    # RSI exhaustion gate: penalize overbought/oversold without fresh confirmation
    has_macd_confirm = ind.get("macd_crossover") or ind.get("macd_crossover_recent")
    has_golden_cross = ind.get("golden_cross")
    rsi_value = ind.get("rsi", 50)
    
    if rsi_value > 70 and not (has_macd_confirm and has_golden_cross):
        momentum_raw = min(momentum_raw, 12)
    elif rsi_value < 30 and not (has_macd_confirm and has_golden_cross):
        momentum_raw = min(momentum_raw, 12)
    
    # Cap at 25, then scale by timeframe weight (weight redistributes points, not shrinks them)
    scores["momentum"] = round(min(momentum_raw / 25 * 25 * weights["momentum"], 25), 1)

    # ── Group 2: Trend (20 pts max) ───────────────────────────────────────────
    price = ind.get("price", 0)
    ma20 = ind.get("ma20") or price
    ma50 = ind.get("ma50") or price
    ma200 = ind.get("ma200") or price

    ma_score = 0
    if price > ma20 and ma20 > ma50:
        ma_score = 11
    elif price > ma20:
        ma_score = 9
    elif price > ma50:
        ma_score = 6
    elif price < ma20 and price < ma50:
        ma_score = 1

    adx = ind.get("adx", 20)
    adx_score = 0
    if adx > 30:
        adx_score = 8
    elif adx >= 25:
        adx_score = 4
    else:
        adx_score = 1

    adx_multiplier = 1.0 if adx > 25 else 0.7
    trend_raw = (ma_score + adx_score) * adx_multiplier
    
    # Medium-term trades (>10d) with ADX <25 auto-cap at 50/100
    predicted_timeframe = 10
    if predicted_timeframe > 10 and adx < 25:
        trend_raw = min(trend_raw, 8)
    
    scores["trend"] = round(min(trend_raw / 20 * 20 * weights["trend"], 20), 1)

    # ── Group 3: Volatility (15 pts max) ──────────────────────────────────────
    bb_score = 0
    bb_position = ind.get("bb_position", 0.5)  # 0=lower band, 0.5=middle, 1=upper band
    bb_squeeze_valid = ind.get("bb_squeeze") and bb_position < 0.6
    
    # BB squeeze requires breakout confirmation (price outside BB) to score full points
    bb_breakout_confirmed = ind.get("bb_breakout_up") or ind.get("bb_breakout_down")
    
    if bb_squeeze_valid and bb_breakout_confirmed:
        bb_score = 9  # Full squeeze + breakout confirmation
    elif ind.get("bb_breakout_up"):
        bb_score = 7
    elif ind.get("bb_breakout_down"):
        bb_score = 2
    elif bb_squeeze_valid:
        bb_score = 2  # Squeeze alone without breakout = bonus tier only
    else:
        bb_score = 3

    atr_score = 0
    if ind.get("atr_rising"):
        atr_score = 4
    elif ind.get("bb_squeeze"):
        atr_score = 5
    else:
        atr_score = 2

    vol_raw = bb_score + atr_score
    scores["volatility"] = round(min(vol_raw / 15 * 15 * weights["volatility"], 15), 1)

    # ── Group 4: Volume (20 pts max) ──────────────────────────────────────────
    vsr = ind.get("volume_surge_ratio", 1.0)
    vsurge_score = 0
    if vsr >= 3.0:
        vsurge_score = 10
    elif vsr >= 2.0:
        vsurge_score = 7
    elif vsr >= 1.5:
        vsurge_score = 4
    else:
        vsurge_score = 1

    obv = ind.get("obv_trend", "NEUTRAL")
    obv_score = 0
    if obv == "CONFIRMING":
        obv_score = 6
    elif obv == "DIVERGING_BULLISH":
        obv_score = 5
    elif obv == "NEUTRAL":
        obv_score = 2
    else:
        obv_score = 0

    vwap_score = 3 if ind.get("price_above_vwap") else 1

    volume_raw = vsurge_score + obv_score + vwap_score
    
    # Penalize bearish OBV divergence on bullish trades
    direction_preview = determine_direction(ind, 0)[0]
    if direction_preview == "BULLISH" and obv == "DIVERGING_BEARISH":
        volume_raw = max(0, volume_raw - 8)
    # Reward bearish OBV confirmation on bearish trades
    elif direction_preview == "BEARISH" and obv == "CONFIRMING":
        volume_raw = min(volume_raw + 2, 20)  # +2 additional to the base 6
    
    scores["volume"] = round(min(volume_raw / 20 * 20 * weights["volume"], 20), 1)

    # ── Group 5: Sentiment (20 pts max) ───────────────────────────────────────
    news_s = sentiment.get("score", 0)
    news_score = 0
    if news_s > 0.6:
        news_score = 6
    elif news_s > 0.3:
        news_score = 4
    elif news_s > -0.3:
        news_score = 2
    else:
        news_score = 0

    # Finnhub static mentions (reduced weight — velocity signals carry more)
    mentions_score = 0
    mentions = sentiment.get("mentions", 0)
    if mentions > 50:
        mentions_score = 2
    elif mentions > 20:
        mentions_score = 1
    else:
        mentions_score = 0

    # StockTwits velocity (0–6 pts)
    st_score = 0
    if social_velocity:
        st_vel = social_velocity.get("stocktwits_velocity_pct", 0)
        if st_vel >= 500:
            st_score = 6
        elif st_vel >= 200:
            st_score = 5
        elif st_vel >= 50:
            st_score = 2
        else:
            st_score = 0

    # Reddit velocity (0–4 pts)
    rd_score = 0
    if social_velocity:
        rd_vel = social_velocity.get("reddit_velocity_pct", 0)
        if rd_vel >= 500:
            rd_score = 4
        elif rd_vel >= 200:
            rd_score = 3
        elif rd_vel >= 50:
            rd_score = 2
        else:
            rd_score = 0

    # StockTwits bull/bear ratio (0–2 pts) — only meaningful on a velocity spike
    bull_score = 0
    if social_velocity and (st_score >= 2 or rd_score >= 2):
        bull_ratio = social_velocity.get("stocktwits_bull_ratio", 0.5)
        if bull_ratio >= 0.75:
            bull_score = 2
        elif bull_ratio >= 0.6:
            bull_score = 1

    sentiment_raw = news_score + mentions_score + st_score + rd_score + bull_score
    scores["sentiment"] = round(min(sentiment_raw, 20), 1)

    # ── Group 6: External (10 pts max) ────────────────────────────────────────
    consensus = analyst.get("consensus", "HOLD")
    analyst_score = {"STRONG_BUY": 6, "BUY": 4, "HOLD": 2, "SELL": 0, "STRONG_SELL": 0}.get(consensus, 2)

    consecutive_beats = earnings.get("consecutive_beats", 0)
    earnings_score = min(consecutive_beats + 1, 4) if consecutive_beats > 0 else 0

    scores["external"] = round(min((analyst_score + earnings_score) / 10 * 10 * weights["external"], 10), 1)

    # ── Bonuses ───────────────────────────────────────────────────────────────
    bonus = 0
    bonus_reasons = []

    if ind.get("rsi_divergence"):
        bonus += 3
        bonus_reasons.append("RSI divergence (+3)")
    if ind.get("golden_cross"):
        bonus += 3
        bonus_reasons.append("Golden cross (+3)")
    if ind.get("bb_squeeze") and not ind.get("bb_breakout_up"):
        bonus += 2
        bonus_reasons.append("Bollinger squeeze (+2)")
    if ind.get("broke_52w_high") and ind.get("volume_surge_ratio", 1) >= 1.5:
        bonus += 4
        bonus_reasons.append("52-week high breakout (+4)")
    if source == "both":
        bonus += 3
        bonus_reasons.append("Dual-list appearance (+3)")

    # ── Phase 1: Earnings Catalyst Bonus ─────────────────────────────────────
    if earnings_calendar and earnings_calendar.get("has_upcoming"):
        consecutive = earnings.get("consecutive_beats", 0)
        days_to_earn = earnings_calendar.get("days_to_earnings", 99)
        if consecutive >= 3:
            bonus += 10
            label = "tomorrow" if days_to_earn <= 1 else f"in {days_to_earn}d"
            bonus_reasons.append(f"Earnings catalyst {label} + {consecutive} consecutive beats (+10)")
        elif consecutive >= 1:
            bonus += 5
            label = "tomorrow" if days_to_earn <= 1 else f"in {days_to_earn}d"
            bonus_reasons.append(f"Earnings {label} + {consecutive} beat(s) (+5)")

    # ── Phase 1: Analyst Upside Bonus ─────────────────────────────────────────
    if analyst_target and analyst_target.get("mean_target"):
        price = ind.get("price", 0)
        if price and price > 0:
            upside_pct = (analyst_target["mean_target"] - price) / price * 100
            if upside_pct >= 20:
                bonus += 5
                bonus_reasons.append(f"Analyst upside {upside_pct:.0f}% (+5)")

    # ── Phase 2: Insider Buying Bonus ─────────────────────────────────────────
    if insider_buying and insider_buying.get("has_insider_buying"):
        strength = insider_buying.get("signal_strength", "NONE")
        total_usd = insider_buying.get("total_purchased_usd", 0)
        n = insider_buying.get("num_insiders", 1)
        if strength == "STRONG":
            bonus += 15
            bonus_reasons.append(f"Insider buying STRONG — ${total_usd/1e6:.1f}M by {n} insider(s) (+15)")
        elif strength == "MODERATE":
            bonus += 8
            bonus_reasons.append(f"Insider buying MODERATE — ${total_usd/1e3:.0f}K by {n} insider(s) (+8)")

    # ── Phase 3: Fundamental Scoring Bonus ────────────────────────────────────
    if fundamentals:
        rev_growth = fundamentals.get("revenue_growth_pct")
        earn_growth = fundamentals.get("earnings_growth_pct")
        op_margin = fundamentals.get("operating_margin_pct")
        fcf = fundamentals.get("free_cashflow")
        peg = fundamentals.get("peg_ratio")

        # Strong revenue growth acceleration
        if rev_growth is not None and rev_growth >= 20:
            bonus += 6
            bonus_reasons.append(f"Revenue growth {rev_growth:.0f}% YoY (+6)")
        elif rev_growth is not None and rev_growth >= 10:
            bonus += 3
            bonus_reasons.append(f"Revenue growth {rev_growth:.0f}% YoY (+3)")

        # Earnings growth
        if earn_growth is not None and earn_growth >= 20:
            bonus += 4
            bonus_reasons.append(f"Earnings growth {earn_growth:.0f}% YoY (+4)")

        # Healthy and expanding operating margins
        if op_margin is not None and op_margin >= 20:
            bonus += 3
            bonus_reasons.append(f"Strong operating margin {op_margin:.0f}% (+3)")

        # Positive free cash flow
        if fcf is not None and fcf > 0:
            bonus += 2
            bonus_reasons.append(f"Positive FCF ${fcf/1e9:.1f}B (+2)")

        # PEG < 1 = undervalued growth
        if peg is not None and 0 < peg < 1:
            bonus += 4
            bonus_reasons.append(f"PEG ratio {peg:.2f} — undervalued growth (+4)")

    base = sum(scores.values())
    total = min(round(base + bonus), 100)

    # Compute analyst_upside_pct for Claude prompt usage
    analyst_upside_pct = None
    if analyst_target and analyst_target.get("mean_target"):
        price = ind.get("price", 0)
        if price and price > 0:
            analyst_upside_pct = round((analyst_target["mean_target"] - price) / price * 100, 1)

    # ── Conviction Filter: Reject low-ATR long timeframe predictions ──────────
    atr = ind.get("atr", 0)
    price = ind.get("price", 1)
    atr_pct = (atr / price * 100) if price > 0 else 0
    predicted_timeframe = 10  # Default assumption from scoring context
    
    # Reject if predicted_timeframe > 10d AND ATR < 2% of price (low volatility, timing risk)
    conviction_pass = True
    if predicted_timeframe > 10 and atr_pct < 2:
        conviction_pass = False
    elif total < 55:
        conviction_pass = False
    else:
        # Count confirmations: trend ≥15, momentum ≥18, volume ≥15
        confirmations = 0
        if scores.get("trend", 0) >= 15:
            confirmations += 1
        if scores.get("momentum", 0) >= 18:
            confirmations += 1
        if scores.get("volume", 0) >= 15:
            confirmations += 1
        if confirmations < 2:
            conviction_pass = False

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



def compute_long_score(ind: dict, sentiment: dict, analyst: dict, earnings: dict,
                       source: str = "nasdaq100", earnings_calendar: dict = None,
                       analyst_target: dict = None, insider_buying: dict = None,
                       fundamentals: dict = None) -> dict:
    """
    Long-term scoring (Friday scan) — 60-180 day moves.
    De-weights short-term technicals, heavily weights fundamentals + insider + analyst conviction.
    Social velocity deliberately excluded — it's a short-term signal.

    Groups (100 pts total):
      Fundamentals  30 pts  ← revenue growth, margins, FCF, PEG
      Insider       25 pts  ← strongest long-term signal
      Analyst       20 pts  ← upside %, consecutive upgrades
      Earnings      15 pts  ← consecutive beats, beat magnitude
      Trend          10 pts  ← MA200 position, golden cross (only meaningful trend signals)
    """
    scores = {}
    bonus = 0
    bonus_reasons = []

    price = ind.get("price", 0)

    # ── Group 1: Fundamentals (30 pts) ────────────────────────────────────────
    fund_score = 0
    if fundamentals:
        rev_growth  = fundamentals.get("revenue_growth_pct")
        earn_growth = fundamentals.get("earnings_growth_pct")
        op_margin   = fundamentals.get("operating_margin_pct")
        fcf         = fundamentals.get("free_cashflow")
        peg         = fundamentals.get("peg_ratio")

        if rev_growth is not None:
            if rev_growth >= 25:   fund_score += 10
            elif rev_growth >= 15: fund_score += 7
            elif rev_growth >= 8:  fund_score += 4
            elif rev_growth < 0:   fund_score -= 3

        if earn_growth is not None:
            if earn_growth >= 25:   fund_score += 8
            elif earn_growth >= 10: fund_score += 5
            elif earn_growth < 0:   fund_score -= 2

        if op_margin is not None:
            if op_margin >= 25:   fund_score += 6
            elif op_margin >= 15: fund_score += 4
            elif op_margin >= 5:  fund_score += 2

        if fcf is not None and fcf > 0:
            fund_score += 4

        if peg is not None and 0 < peg < 1:
            fund_score += 6
            bonus_reasons.append(f"PEG {peg:.2f} — undervalued growth")
        elif peg is not None and 1 <= peg < 2:
            fund_score += 2

    scores["fundamentals"] = round(min(max(fund_score, 0), 30), 1)

    # ── Group 2: Insider Buying (25 pts) ──────────────────────────────────────
    insider_score = 0
    if insider_buying and insider_buying.get("has_insider_buying"):
        strength  = insider_buying.get("signal_strength", "NONE")
        total_usd = insider_buying.get("total_purchased_usd", 0)
        n         = insider_buying.get("num_insiders", 1)
        if strength == "STRONG":
            insider_score = 25
            total_str = f"${total_usd/1e6:.1f}M" if total_usd >= 1e6 else f"${total_usd/1e3:.0f}K"
            bonus_reasons.append(f"Insider buying STRONG — {total_str} by {n} insider(s)")
        elif strength == "MODERATE":
            insider_score = 15
            total_str = f"${total_usd/1e3:.0f}K"
            bonus_reasons.append(f"Insider buying MODERATE — {total_str} by {n} insider(s)")
        else:
            insider_score = 8
    scores["insider"] = insider_score

    # ── Group 3: Analyst Conviction (20 pts) ──────────────────────────────────
    analyst_score = 0
    consensus = analyst.get("consensus", "HOLD")
    analyst_score += {"STRONG_BUY": 10, "BUY": 7, "HOLD": 3, "SELL": 0, "STRONG_SELL": 0}.get(consensus, 3)

    if analyst_target and analyst_target.get("mean_target") and price > 0:
        upside_pct = (analyst_target["mean_target"] - price) / price * 100
        if upside_pct >= 30:
            analyst_score += 10
            bonus_reasons.append(f"Analyst upside {upside_pct:.0f}% — strong conviction")
        elif upside_pct >= 20:
            analyst_score += 7
            bonus_reasons.append(f"Analyst upside {upside_pct:.0f}%")
        elif upside_pct >= 10:
            analyst_score += 4

    scores["analyst"] = round(min(analyst_score, 20), 1)

    # ── Group 4: Earnings Quality (15 pts) ────────────────────────────────────
    earnings_score = 0
    consecutive = earnings.get("consecutive_beats", 0)
    if consecutive >= 4:
        earnings_score = 15
        bonus_reasons.append(f"{consecutive} consecutive earnings beats — institutional re-rating likely")
    elif consecutive >= 3:
        earnings_score = 11
        bonus_reasons.append(f"{consecutive} consecutive earnings beats")
    elif consecutive >= 2:
        earnings_score = 7
    elif consecutive >= 1:
        earnings_score = 4

    # Upcoming earnings is a catalyst for long-term too (next quarter beat likely)
    if earnings_calendar and earnings_calendar.get("has_upcoming") and consecutive >= 2:
        earnings_score = min(earnings_score + 3, 15)

    scores["earnings"] = earnings_score

    # ── Group 5: Trend (10 pts) — only long-term trend signals ───────────────
    trend_score = 0
    ma50  = ind.get("ma50") or price
    ma200 = ind.get("ma200") or price

    if price > ma200:
        trend_score += 5
    if price > ma50 and ma50 > ma200:
        trend_score += 3  # golden cross on longer MAs
    if ind.get("adx", 0) > 25:
        trend_score += 2

    scores["trend"] = round(min(trend_score, 10), 1)

    # ── Source bonus ──────────────────────────────────────────────────────────
    if source == "both":
        bonus += 2
        bonus_reasons.append("Dual-list appearance (+2)")

    base  = sum(scores.values())
    total = min(round(base + bonus), 100)

    analyst_upside_pct = None
    if analyst_target and analyst_target.get("mean_target") and price > 0:
        analyst_upside_pct = round((analyst_target["mean_target"] - price) / price * 100, 1)

    return {
        "total": total,
        "base": round(base),
        "bonus": bonus,
        "bonus_reasons": bonus_reasons,
        "breakdown": scores,
        "formula_version": FORMULA_VERSION + "_long",
        "analyst_upside_pct": analyst_upside_pct,
        "earnings_calendar": earnings_calendar,
        "insider_buying": insider_buying,
        "conviction_pass": total >= 40,  # lower bar — fundamentals need time to play out
    }


def determine_direction(ind: dict, score: int) -> tuple[str, str]:
    """Returns (direction, position) based on indicator signals."""
    bullish_signals = 0
    bearish_signals = 0

    if ind.get("rsi", 50) < 40:
        bullish_signals += 2
    elif ind.get("rsi", 50) > 65:
        bearish_signals += 1

    if ind.get("macd_crossover"):
        bullish_signals += 2
    elif ind.get("macd_line", 0) < ind.get("macd_signal", 0):
        bearish_signals += 1

    if ind.get("price", 0) > (ind.get("ma20") or 0):
        bullish_signals += 1
    else:
        bearish_signals += 1

    if ind.get("rsi_divergence"):
        bullish_signals += 2

    # Hard filter: reject bullish trades when RSI > 70 (overbought, prone to reversals)
    if bullish_signals > bearish_signals + 1:
        if ind.get("rsi", 50) > 70:
            return "NEUTRAL", "HOLD"
        return "BULLISH", "LONG"
    elif bearish_signals > bullish_signals + 1:
        return "BEARISH", "SHORT"
    else:
        return "NEUTRAL", "HOLD"


def compute_buy_range(price: float, atr: float, direction: str) -> tuple[float, float]:
    decimals = 6 if price < 1 else 4 if price < 10 else 2
    offset = atr * 0.3
    if direction == "BULLISH":
        return round(price - offset, decimals), round(price + offset * 0.5, decimals)
    elif direction == "BEARISH":
        return round(price - offset * 0.5, decimals), round(price + offset, decimals)
    return round(price - offset, decimals), round(price + offset, decimals)


def compute_targets(price: float, atr: float, direction: str, rsi: float = 50) -> tuple[float, float, float]:
    """Returns (target_low, target_high, stop_loss). Dynamic stop-loss based on RSI level."""
    # Tighter stop-loss when RSI is elevated (60-70) to reduce whipsaw on overbought rallies
    if 60 <= rsi <= 70:
        stop_distance = price * 0.02  # 2% stop for elevated RSI
    else:
        stop_distance = atr * 1.5  # Standard 5% equivalent stop
    
    if direction == "BULLISH":
        target_low = round(price + atr * 1.5, 2)
        target_high = round(price + atr * 2.5, 2)
        stop_loss = round(price - stop_distance, 2)
    elif direction == "BEARISH":
        target_low = round(price - atr * 2.5, 2)
        target_high = round(price - atr * 1.5, 2)
        stop_loss = round(price + stop_distance, 2)
    else:
        target_low = target_high = price
        stop_loss = round(price - stop_distance, 2)
    return target_low, target_high, stop_loss
