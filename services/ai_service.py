import os
import anthropic
from dotenv import load_dotenv

load_dotenv()

_client = None

def get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client

MODEL = "claude-haiku-4-5"


def _earnings_context(earnings_calendar: dict) -> str:
    if not earnings_calendar or not earnings_calendar.get("has_upcoming"):
        return ""
    days = earnings_calendar.get("days_to_earnings", 0)
    date = earnings_calendar.get("earnings_date", "")
    if days == 0:
        label = "TODAY"
    elif days == 1:
        label = "TOMORROW"
    else:
        label = f"IN {days} DAYS ({date})"
    return f"- ⚡ EARNINGS CATALYST: Reports {label} — factor this into your target and timing\n"


def _analyst_upside_context(upside_pct: float) -> str:
    if upside_pct is None:
        return ""
    if upside_pct >= 20:
        return f"- 📈 ANALYST UPSIDE: Mean price target is {upside_pct:.0f}% above current price — strong institutional conviction\n"
    if upside_pct > 0:
        return f"- Analyst mean target: {upside_pct:.0f}% above current price\n"
    return f"- Analyst mean target: {upside_pct:.0f}% vs current price (below)\n"


def _insider_context(insider_buying: dict) -> str:
    if not insider_buying or not insider_buying.get("has_insider_buying"):
        return ""
    strength = insider_buying.get("signal_strength", "")
    total = insider_buying.get("total_purchased_usd", 0)
    n = insider_buying.get("num_insiders", 1)
    date = insider_buying.get("latest_filing_date", "")
    total_str = f"${total/1e6:.1f}M" if total >= 1_000_000 else f"${total/1e3:.0f}K"
    if strength == "STRONG":
        return f"- 👤 INSIDER BUYING (STRONG): {n} insider(s) purchased {total_str} in last 14 days (latest: {date}) — executives buying their own stock\n"
    return f"- 👤 INSIDER BUYING (MODERATE): {total_str} purchased by {n} insider(s) in last 14 days\n"


def _social_velocity_context(social_velocity: dict) -> str:
    if not social_velocity:
        return ""
    lines = []
    st_vel = social_velocity.get("stocktwits_velocity_pct", 0)
    rd_vel = social_velocity.get("reddit_velocity_pct", 0)
    bull_ratio = social_velocity.get("stocktwits_bull_ratio", 0.5)
    st_vol = social_velocity.get("stocktwits_volume", 0)

    if st_vel >= 50 or rd_vel >= 50:
        if st_vel >= 50:
            lines.append(f"StockTwits mentions up {st_vel:+.0f}% vs prior 12h ({st_vol} messages)")
        if rd_vel >= 50:
            lines.append(f"Reddit mentions up {rd_vel:+.0f}% vs prior 12h")
        if st_vel >= 200 or rd_vel >= 200:
            lines.append(f"Sentiment: {bull_ratio*100:.0f}% bullish on StockTwits")
            return "- 🔥 SOCIAL VELOCITY SPIKE:\n" + "\n".join(f"  · {l}" for l in lines) + "\n"
        return "- 📈 Social activity rising:\n" + "\n".join(f"  · {l}" for l in lines) + "\n"
    return ""


def _market_context_bullish(rel_strength_vs_spy: float, sector_return_5d: float, sector_etf: str, short_interest_pct: float) -> str:
    lines = []
    if rel_strength_vs_spy is not None:
        direction = "outperforming" if rel_strength_vs_spy >= 0 else "underperforming"
        lines.append(f"Relative strength vs SPY (5d): {rel_strength_vs_spy:+.1f}% ({direction} market)")
    if sector_return_5d is not None and sector_etf:
        lines.append(f"Sector ETF ({sector_etf}) 5d return: {sector_return_5d:+.1f}% ({'tailwind' if sector_return_5d >= 1 else 'headwind' if sector_return_5d <= -1 else 'neutral'})")
    if short_interest_pct is not None and short_interest_pct >= 5:
        squeeze = " — SQUEEZE POTENTIAL" if short_interest_pct >= 15 else ""
        lines.append(f"Short interest: {short_interest_pct:.0f}% of float{squeeze}")
    if not lines:
        return ""
    return "- 📊 MARKET CONTEXT:\n" + "\n".join(f"  · {l}" for l in lines) + "\n"


def _market_context_bearish(rel_strength_vs_spy: float, sector_return_5d: float, sector_etf: str) -> str:
    lines = []
    if rel_strength_vs_spy is not None:
        direction = "outperforming" if rel_strength_vs_spy >= 0 else "underperforming"
        lines.append(f"Relative strength vs SPY (5d): {rel_strength_vs_spy:+.1f}% ({direction} market)")
    if sector_return_5d is not None and sector_etf:
        lines.append(f"Sector ETF ({sector_etf}) 5d return: {sector_return_5d:+.1f}% ({'confirms weakness' if sector_return_5d <= -1 else 'sector still strong — fights reversal' if sector_return_5d >= 2 else 'neutral'})")
    if not lines:
        return ""
    return "- 📊 MARKET CONTEXT:\n" + "\n".join(f"  · {l}" for l in lines) + "\n"


def _fundamentals_context(fundamentals: dict) -> str:
    if not fundamentals:
        return ""
    lines = []
    if fundamentals.get("revenue_growth_pct") is not None:
        lines.append(f"Revenue growth (YoY): {fundamentals['revenue_growth_pct']:+.0f}%")
    if fundamentals.get("earnings_growth_pct") is not None:
        lines.append(f"Earnings growth (YoY): {fundamentals['earnings_growth_pct']:+.0f}%")
    if fundamentals.get("operating_margin_pct") is not None:
        lines.append(f"Operating margin: {fundamentals['operating_margin_pct']:.0f}%")
    if fundamentals.get("free_cashflow") is not None:
        fcf = fundamentals["free_cashflow"]
        fcf_str = f"${fcf/1e9:.1f}B" if abs(fcf) >= 1e9 else f"${fcf/1e6:.0f}M"
        lines.append(f"Free cash flow: {fcf_str} ({'positive' if fcf > 0 else 'negative'})")
    if fundamentals.get("peg_ratio") is not None:
        lines.append(f"PEG ratio: {fundamentals['peg_ratio']:.2f} ({'undervalued' if fundamentals['peg_ratio'] < 1 else 'fair/expensive'})")
    if fundamentals.get("trailing_pe") is not None:
        lines.append(f"Trailing P/E: {fundamentals['trailing_pe']:.1f}")
    if not lines:
        return ""
    return "- 📊 FUNDAMENTALS:\n" + "\n".join(f"  · {l}" for l in lines) + "\n"


def analyze_stock(ticker: str, indicators: dict, sentiment: dict, analyst: dict,
                  earnings: dict = None, score_data: dict = None,
                  accuracy_context: str = "", ticker_history: str = "",
                  earnings_calendar: dict = None, analyst_upside_pct: float = None,
                  insider_buying: dict = None, fundamentals: dict = None,
                  social_velocity: dict = None) -> dict:
    """
    Single Claude call per stock — no timeframe hint.
    Claude reads the data and decides its own target price, stop, and days.
    Returns: direction, position, confidence, target_price, stop_price,
             days_to_target, timing_rationale, reasoning, buy_window
    """
    price = indicators.get("price", 0)
    atr   = indicators.get("atr", price * 0.02) or (price * 0.02)
    ma20  = indicators.get("ma20") or price
    ma50  = indicators.get("ma50") or price
    ma200 = indicators.get("ma200") or price

    prompt = f"""You are a stock analyst. Look at {ticker}'s data below and make one honest prediction.
Do NOT assume a timeframe — let the data tell you how long the move will take.

PRICE & VOLATILITY:
- Current price: ${price:.2f}
- ATR(14): ${atr:.2f}/day  (~{atr/price*100:.1f}% daily range)
- MA20: ${ma20:.2f}  MA50: ${ma50:.2f}  MA200: ${ma200:.2f}
- Price vs MA20: {'ABOVE' if price > ma20 else 'BELOW'}  |  vs MA50: {'ABOVE' if price > ma50 else 'BELOW'}

MOMENTUM:
- RSI(14): {indicators.get('rsi', 50):.1f} {'← OVERSOLD' if indicators.get('rsi', 50) < 30 else '← OVERBOUGHT' if indicators.get('rsi', 50) > 70 else ''}
- MACD crossover (bullish): {'YES' if indicators.get('macd_crossover') else 'No'}
- MACD line vs signal: {indicators.get('macd_line', 0):.3f} vs {indicators.get('macd_signal', 0):.3f}
- RSI divergence: {'YES — hidden bullish' if indicators.get('rsi_divergence') else 'No'}

TREND:
- ADX: {indicators.get('adx', 20):.1f} {'(STRONG)' if indicators.get('adx', 0) > 30 else '(WEAK/RANGING)' if indicators.get('adx', 0) < 20 else '(MODERATE)'}
- Golden cross (MA20>MA50): {'YES' if indicators.get('golden_cross') else 'No'}
- 52-week high: {'JUST BROKE OUT' if indicators.get('broke_52w_high') else 'Near high' if indicators.get('near_52w_high') else 'Not near'}

VOLUME & STRUCTURE:
- Volume surge: {indicators.get('volume_surge_ratio', 1.0):.1f}x average
- OBV trend: {indicators.get('obv_trend', 'NEUTRAL')}
- Bollinger squeeze: {'YES — breakout imminent' if indicators.get('bb_squeeze') else 'No'}
- Price above VWAP: {'YES' if indicators.get('price_above_vwap') else 'No'}

EXTERNAL:
- News sentiment (48h): {sentiment.get('score', 0):.2f}  ({sentiment.get('volume', 0)} articles)
- Analyst consensus: {analyst.get('consensus', 'HOLD')}
- Earnings beats (last 4Q): {(earnings or {}).get('beats', 0)}
{_earnings_context(earnings_calendar)}{_analyst_upside_context(analyst_upside_pct)}{_insider_context(insider_buying)}{_fundamentals_context(fundamentals)}{_social_velocity_context(social_velocity)}

SIGNAL SCORE: {score_data.get('total', 0)}/100
Active bonus signals: {', '.join(score_data.get('bonus_reasons', [])) or 'None'}

{f"SYSTEM ACCURACY CONTEXT:{chr(10)}{accuracy_context}" if accuracy_context else ""}
{f"THIS TICKER'S HISTORY:{chr(10)}{ticker_history}" if ticker_history else ""}

TASK: Make a single prediction for this stock. Every field must be derived from the data above — no defaults, no guessing.

DIRECTION: Only BULLISH if price structure, momentum, and volume all lean the same way. Only BEARISH if they do. NEUTRAL if they conflict or the setup is unclear.

TARGET PRICE: Set at the nearest meaningful resistance (BULLISH) or support (BEARISH) level visible from MA levels, Bollinger Bands, and ATR multiples. Do not invent a round number — anchor it to the data.

STOP PRICE: Set just beyond the nearest support (BULLISH) or resistance (BEARISH). Use 1.5–2× ATR from entry as a guide. A tight stop on a volatile stock will be hit by noise — widen it accordingly.

DAYS TO TARGET: Divide the distance from price to target by the ATR to get a realistic day estimate. Multiply by 1.5 if momentum is weak or trend is ranging (ADX < 20).

CONFIDENCE — derive it from signal agreement, not a gut feel:
- Count how many of these 5 signal groups clearly support your direction:
  (1) Momentum (RSI + MACD direction), (2) Trend (MA alignment + ADX), (3) Volume (surge + OBV), (4) External (sentiment + analyst), (5) Structure (Bollinger, VWAP)
- 5/5 groups aligned → 85–95
- 4/5 aligned → 70–84
- 3/5 aligned → 55–69
- 2/5 or fewer aligned → below 55, strongly consider NEUTRAL
- If you cannot point to at least 3 groups supporting the direction, do NOT output confidence above 60.
- Never output a round number like 62 or 58 as a default — the number must reflect the actual count of aligned signals.

Respond in this exact JSON:
{{
  "direction": "BULLISH" | "BEARISH" | "NEUTRAL",
  "position": "LONG" | "SHORT" | "HOLD",
  "confidence": <integer derived from signal group count above>,
  "target_price": <float — anchored to resistance/support level>,
  "stop_price": <float — anchored to support/resistance and ATR>,
  "days_to_target": <integer — price distance / ATR × momentum multiplier>,
  "timing_rationale": "<1 sentence: which specific signals drive the timing and how many ATR to target>",
  "reasoning": "<2-3 sentences: name the specific signals that agree and any that conflict>",
  "key_signals": ["signal1", "signal2", "signal3"],
  "buy_window": "<time range in PT when to enter, e.g. 7:15 AM – 8:30 AM PT>"
}}

Only output the JSON."""

    try:
        response = get_client().messages.create(
            model=MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        import json
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)
        result["model"] = MODEL
        return result
    except Exception as e:
        return {
            "direction": "NEUTRAL",
            "position": "HOLD",
            "confidence": 0,
            "target_price": None,
            "stop_price": None,
            "days_to_target": None,
            "timing_rationale": "",
            "reasoning": f"Analysis unavailable: {str(e)}",
            "key_signals": [],
            "buy_window": "N/A",
            "model": MODEL,
        }


def analyze_stock_bullish(ticker: str, indicators: dict, sentiment: dict, analyst: dict,
                          earnings: dict = None, score_data: dict = None,
                          accuracy_context: str = "", ticker_history: str = "",
                          earnings_calendar: dict = None, analyst_upside_pct: float = None,
                          insider_buying: dict = None, fundamentals: dict = None,
                          social_velocity: dict = None, rel_strength_vs_spy: float = None,
                          sector_return_5d: float = None, sector_etf: str = None,
                          short_interest_pct: float = None) -> dict:
    """
    Short-term bullish Claude prediction — momentum continuation setups only.
    Focused on: how far and how fast will this stock continue its move up?
    DO NOT output BEARISH — if setup is unclear, output NEUTRAL.
    """
    price = indicators.get("price", 0)
    atr   = indicators.get("atr", price * 0.02) or (price * 0.02)
    ma20  = indicators.get("ma20") or price
    ma50  = indicators.get("ma50") or price
    ma200 = indicators.get("ma200") or price

    prompt = f"""You are a short-term stock analyst specializing in bullish momentum setups.
Analyze {ticker} for a near-term continuation move to the upside.

PRICE & TREND:
- Current price: ${price:.2f}
- ATR(14): ${atr:.2f}/day  (~{atr/price*100:.1f}% daily range)
- MA20: ${ma20:.2f}  MA50: ${ma50:.2f}  MA200: ${ma200:.2f}
- Price vs MA20: {'ABOVE' if price > ma20 else 'BELOW'}  |  vs MA50: {'ABOVE' if price > ma50 else 'BELOW'}  |  vs MA200: {'ABOVE' if price > ma200 else 'BELOW'}

MOMENTUM:
- RSI(14): {indicators.get('rsi', 50):.1f} {'← OVERBOUGHT — watch for exhaustion' if indicators.get('rsi', 50) > 70 else '← HEALTHY MOMENTUM' if 50 <= indicators.get('rsi', 50) <= 70 else '← OVERSOLD' if indicators.get('rsi', 50) < 30 else ''}
- MACD crossover (bullish): {'YES — momentum confirmed' if indicators.get('macd_crossover') else 'No'}
- MACD recent crossover (last 3 bars): {'YES' if indicators.get('macd_crossover_recent') else 'No'}
- MACD line vs signal: {indicators.get('macd_line', 0):.3f} vs {indicators.get('macd_signal', 0):.3f}  {'(histogram expanding)' if indicators.get('macd_hist', 0) > indicators.get('macd_hist_prev', 0) > 0 else ''}
- RSI divergence (hidden bullish): {'YES — accumulation signal' if indicators.get('rsi_divergence') else 'No'}

TREND & STRUCTURE:
- ADX: {indicators.get('adx', 20):.1f} {'(STRONG TREND)' if indicators.get('adx', 0) > 30 else '(WEAK/RANGING)' if indicators.get('adx', 0) < 20 else '(MODERATE)'}
- Golden cross (MA20>MA50): {'YES' if indicators.get('golden_cross') else 'No'}
- 52-week high: {'JUST BROKE OUT' if indicators.get('broke_52w_high') else 'Near high' if indicators.get('near_52w_high') else 'Not near'}
- MA20 bounce (price within 1.5% above MA20): {'YES — clean continuation entry' if indicators.get('near_ma20_bounce') else 'No'}
- Higher low (vs 5 bars ago): {'YES — uptrend structure intact' if indicators.get('higher_low') else 'No'}
- Bullish engulfing candle: {'YES — demand confirmation' if indicators.get('bullish_engulfing') else 'No'}
- Extension above MA20: {((indicators.get('price', 0) - (indicators.get('ma20') or indicators.get('price', 1))) / (indicators.get('ma20') or indicators.get('price', 1)) * 100):.1f}%  {'← LATE MOMENTUM, higher reversal risk' if ((indicators.get('price', 0) - (indicators.get('ma20') or indicators.get('price', 1))) / (indicators.get('ma20') or indicators.get('price', 1)) * 100) >= 8 else ''}

VOLUME & VOLATILITY:
- Volume surge: {indicators.get('volume_surge_ratio', 1.0):.1f}x average
- OBV trend: {indicators.get('obv_trend', 'NEUTRAL')} {'← SMART MONEY BUYING' if indicators.get('obv_trend') == 'CONFIRMING' else '← SMART MONEY SELLING — caution' if indicators.get('obv_trend') == 'DIVERGING_BEARISH' else ''}
- BB squeeze (compression): {'YES — breakout imminent' if indicators.get('bb_squeeze') else 'No'}
- NR7 (narrowest range in 7 bars): {'YES — volatility compression' if indicators.get('nr7') else 'No'}
- Gap up + holds: {'YES — institutional buying' if indicators.get('gap_up_holds') else 'No'}
- Price above VWAP: {'YES' if indicators.get('price_above_vwap') else 'No'}

EXTERNAL:
- News sentiment (48h): {sentiment.get('score', 0):.2f}  ({sentiment.get('volume', 0)} articles)
- Analyst consensus: {analyst.get('consensus', 'HOLD')}
- Earnings beats (last 4Q): {(earnings or {}).get('beats', 0)}
{_earnings_context(earnings_calendar)}{_analyst_upside_context(analyst_upside_pct)}{_insider_context(insider_buying)}{_fundamentals_context(fundamentals)}{_social_velocity_context(social_velocity)}
Active signals: {', '.join(score_data.get('bonus_reasons', [])) or 'None'}

{_market_context_bullish(rel_strength_vs_spy, sector_return_5d, sector_etf, short_interest_pct)}{f"SYSTEM ACCURACY CONTEXT:{chr(10)}{accuracy_context}" if accuracy_context else ""}
{f"THIS TICKER'S HISTORY:{chr(10)}{ticker_history}" if ticker_history else ""}

TASK: This is a SHORT-TERM BULLISH (LONG) analysis only. The question is: does this stock have enough momentum to continue higher in the near term?

DO NOT output BEARISH. If the setup is not clearly bullish, output NEUTRAL.

TARGET PRICE: Use this priority order — (1) nearest prior swing high, (2) if breakout setup: 1.5–2× ATR above entry, (3) Bollinger upper band ONLY if it is within 3% of price. Do not use a round number or invent a level.

STOP PRICE: Set just below the nearest support (MA20, prior consolidation). Use 1.5–2× ATR from entry as a guide.

DAYS TO TARGET: Divide distance from price to target by ATR to get a raw day count. Minimum 2 days, maximum 10 days. Multiply by 1.5 if ADX < 20 OR volume surge < 1.5x.

CONFIDENCE — derived strictly from signal count. Count each core signal that clearly applies:
  C1: RSI between 50–70 (healthy momentum zone)
  C2: MACD crossover confirmed OR histogram expanding from positive baseline
  C3: Volume surge ≥1.5x average (institutional participation)
  C4: Price above MA20 AND above MA50 (trend alignment)
  C5: ADX > 25 (trend has real strength, not ranging)

Quality signals (upgrade confidence by 3–5 if present): MA20 bounce + higher low, NR7 or BB squeeze, bullish engulfing candle, gap up holds.

Hard confidence rules (non-negotiable):
  - Fewer than 3 core signals → confidence MUST be ≤ 60
  - Extension >8% above MA20 → subtract 10 from confidence, hard cap at 75
  - RSI >72 without volume surge ≥1.5x → subtract 3 from confidence
  - Global cap: 85 max UNLESS breakout + volume surge ≥1.5x + ADX > 30 are ALL present

Score ranges after applying rules:
  - 5 core signals → 75–85 (before quality upgrades)
  - 4 core signals → 65–74
  - 3 core signals → 52–64
  - 2 or fewer → ≤55, strongly consider NEUTRAL

Respond in this exact JSON:
{{
  "direction": "BULLISH" | "NEUTRAL",
  "position": "LONG" | "HOLD",
  "confidence": <integer derived from signal count — must obey hard rules above>,
  "core_signals_count": <integer 0–5: how many of C1–C5 are clearly present>,
  "target_price": <float — anchored to swing high, ATR multiple, or BB upper per priority order>,
  "stop_price": <float — anchored to support and ATR>,
  "days_to_target": <integer — min 2, max 10; ×1.5 if ADX<20 or no volume surge>,
  "timing_rationale": "<1 sentence: which specific signals drive the timing and ATR distance to target>",
  "reasoning": "<2-3 sentences: name the specific signals that agree and any that conflict>",
  "key_signals": ["signal1", "signal2", "signal3"],
  "buy_window": "<time range in PT when to enter, e.g. 7:15 AM – 8:30 AM PT>"
}}

Only output the JSON."""

    try:
        response = get_client().messages.create(
            model=MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        import json
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)
        result["model"] = MODEL
        return result
    except Exception as e:
        return {
            "direction": "NEUTRAL",
            "position": "HOLD",
            "confidence": 0,
            "core_signals_count": 0,
            "target_price": None,
            "stop_price": None,
            "days_to_target": None,
            "timing_rationale": "",
            "reasoning": f"Analysis unavailable: {str(e)}",
            "key_signals": [],
            "buy_window": "N/A",
            "model": MODEL,
        }


def analyze_stock_bearish(ticker: str, indicators: dict, sentiment: dict, analyst: dict,
                          earnings: dict = None, score_data: dict = None,
                          accuracy_context: str = "", ticker_history: str = "",
                          earnings_calendar: dict = None, rel_strength_vs_spy: float = None,
                          sector_return_5d: float = None, sector_etf: str = None) -> dict:
    """
    Short-term bearish Claude prediction — overbought reversal setups only.
    Focused on: how far and how fast will this extended stock pull back?
    """
    price = indicators.get("price", 0)
    atr   = indicators.get("atr", price * 0.02) or (price * 0.02)
    ma20  = indicators.get("ma20") or price
    ma50  = indicators.get("ma50") or price

    roc_5  = indicators.get("roc_5", 0) or 0
    roc_10 = indicators.get("roc_10", 0) or 0
    ext_pct = (price - ma20) / ma20 * 100 if ma20 > 0 else 0
    ext_from_ma50 = (price - ma50) / ma50 * 100 if ma50 > 0 else 0
    atr_extended = atr > 0 and (price - ma20) >= 2 * atr

    # Tiered target: ext 8-12% → MA20, ext 12-20% → midpoint MA20-MA50, ext >20% → MA50
    if ext_pct >= 20:
        tier_label = f"Tier 3 — extreme extension ({ext_pct:.1f}% above MA20, {ext_from_ma50:.1f}% above MA50): target MA50 = ${ma50:.2f}"
    elif ext_pct >= 12:
        mid_target = (ma20 + ma50) / 2
        tier_label = f"Tier 2 — strong extension ({ext_pct:.1f}% above MA20): first target MA20 = ${ma20:.2f}, stretch target midpoint MA20-MA50 = ${mid_target:.2f}"
    else:
        tier_label = f"Tier 1 — normal extension ({ext_pct:.1f}% above MA20): target MA20 = ${ma20:.2f}"

    prompt = f"""You are a short-term stock analyst specializing in overbought reversals.
{ticker} has had a strong recent run and is showing exhaustion signals. Analyze whether a pullback is likely.

PRICE & EXTENSION:
- Current price: ${price:.2f}
- ATR(14): ${atr:.2f}/day  (~{atr/price*100:.1f}% daily range)
- MA20: ${ma20:.2f}  MA50: ${ma50:.2f}
- Extension above MA20: {ext_pct:.1f}%  |  Extension above MA50: {ext_from_ma50:.1f}%
- ATR extension (price > 2× ATR above MA20): {'YES — parabolic exhaustion' if atr_extended else 'No'}
- 5-day return: {roc_5:+.1f}%   10-day return: {roc_10:+.1f}%

MOMENTUM ROLLOVER:
- RSI(14): {indicators.get('rsi', 50):.1f}  {'← OVERBOUGHT' if indicators.get('rsi', 50) > 70 else '← APPROACHING OVERBOUGHT' if indicators.get('rsi', 50) > 65 else ''}
- Bearish RSI divergence (price up 10d, RSI down 10d): {'YES — stronger signal, momentum fading despite higher price' if indicators.get('rsi_bearish_divergence') else 'No'}
- MACD histogram vs prior bar: {indicators.get('macd_hist', 0):.3f} vs {indicators.get('macd_hist_prev', 0):.3f}  {'← SHRINKING — earliest reversal warning' if indicators.get('macd_hist', 0) < indicators.get('macd_hist_prev', 0) else '(growing — no rollover yet)'}
- MACD bearish crossover (line < signal): {'YES — confirmed momentum reversal' if indicators.get('macd_crossover_bearish') else 'No'}
- BB rejection (touched upper band, closed back inside): {'YES — sellers at resistance' if indicators.get('bb_touched_upper') and not indicators.get('bb_breakout_up') else 'No'}

CANDLESTICK TRIGGERS (reversing NOW — weighted by strength):
- Bearish engulfing: {'YES ← STRONGEST: body fully covers prior bullish candle' if indicators.get('bearish_engulfing') else 'No'}
- Shooting star: {'YES ← MEDIUM: long upper wick, sellers rejected at high' if indicators.get('shooting_star') else 'No'}
- Upper wick rejection: {'YES ← WEAK: partial intraday rejection only' if indicators.get('upper_wick_rejection') else 'No'}
- Blow-off top: {'YES ← STRONGEST: 3 accelerating candles + volume spike = parabolic exhaustion' if indicators.get('blowoff_top') else 'No'}

DISTRIBUTION SIGNALS:
- OBV trend: {indicators.get('obv_trend', 'NEUTRAL')}  {'← SMART MONEY SELLING — price/volume divergence' if indicators.get('obv_trend') == 'DIVERGING_BEARISH' else ''}
- Distribution days (close down on high vol, last 10 bars): {indicators.get('distribution_days', 0)}  {'← INSTITUTIONAL SELLING PATTERN' if indicators.get('distribution_days', 0) >= 3 else ''}
- Volume surge ratio: {indicators.get('volume_surge_ratio', 1.0):.1f}x average

TREND RESISTANCE:
- MA50 slope rising: {'YES — strong uptrend, mean reversion much harder' if indicators.get('ma50_slope_rising') else 'No'}
- Extension above MA50: {ext_from_ma50:.1f}%  {'← far from mean, deep reversal candidate' if ext_from_ma50 >= 15 else ''}

EXTERNAL:
- News sentiment (48h): {sentiment.get('score', 0):.2f}  ({sentiment.get('volume', 0)} articles)
- Analyst consensus: {analyst.get('consensus', 'HOLD')}
{'- ⚠️ EARNINGS IN ' + str(earnings_calendar.get("days_to_earnings")) + ' DAYS — gap risk, avoid short' if earnings_calendar and earnings_calendar.get("has_upcoming") else '- No upcoming earnings'}

Active signals: {', '.join(score_data.get('bonus_reasons', [])) or 'None'}

{_market_context_bearish(rel_strength_vs_spy, sector_return_5d, sector_etf)}{f"ACCURACY CONTEXT:{chr(10)}{accuracy_context}" if accuracy_context else ""}
{f"THIS TICKER'S HISTORY:{chr(10)}{ticker_history}" if ticker_history else ""}

TASK: This is a SHORT-TERM BEARISH (SHORT) analysis only. Has this stock run too far, too fast, and is a pullback imminent?

DO NOT output BULLISH. If the setup is not clearly bearish, output NEUTRAL.

TREND FILTER — output NEUTRAL if: MA50 slope is rising AND no distribution signals AND no candlestick trigger. Strong trends persist far longer than they should.

TARGET PRICE — use this tier system:
{tier_label}
Do not invent a round number. Use the tier appropriate to the extension level.

STOP PRICE: The level that invalidates the short — a new high, or where the run clearly resumes. Typically 1–2× ATR above entry.

DAYS TO TARGET: Raw formula: (pullback distance / ATR) × 1.3. Minimum 3 days, maximum 10 days.
Shorts chop and stall before breaking — the 1.3× multiplier accounts for this.

CONFIDENCE — derived strictly from signal count. Count each core bucket that clearly applies:
  B1: Extension (price >8% above MA20 OR ATR-extended — pick ONE, not both)
  B2: Momentum rollover (MACD histogram shrinking OR bearish RSI divergence — divergence is stronger)
  B3: Distribution (OBV diverging bearish OR distribution days ≥3)
  B4: Trigger (bearish engulfing=strong, shooting star=medium, upper wick=weak, blow-off top=strongest)
  B5: Trend resistance (extension >15% above MA50 OR price below rising MA50 — confirms shorts have room)

Hard confidence rules (non-negotiable):
  - Fewer than 3 buckets → confidence MUST be ≤ 60, strongly consider NEUTRAL
  - MA50 slope rising → subtract 5–8 from confidence (trend fights the thesis)
  - Global cap: 85 max UNLESS bearish RSI divergence + distribution confirmed + rejection candle + weak sector ALL present (then up to 88)
  - Shorts have upward-bias headwind — never go above 88 regardless

Score ranges after applying rules:
  - 5 buckets → 78–85
  - 4 buckets → 65–77
  - 3 buckets → 55–64
  - ≤2 buckets → <55, NEUTRAL recommended

Respond in this exact JSON:
{{
  "direction": "BEARISH" | "NEUTRAL",
  "position": "SHORT" | "HOLD",
  "confidence": <integer — must obey hard rules above>,
  "core_signals_count": <integer 0–5: how many of B1–B5 buckets are clearly present>,
  "target_price": <float — use tier system above, anchored to MA20 or MA50>,
  "stop_price": <float — level that invalidates the short>,
  "days_to_target": <integer — (distance/ATR)×1.3, min 3, max 10>,
  "timing_rationale": "<1 sentence: which exhaustion signals are firing and which tier target applies>",
  "reasoning": "<2-3 sentences: name each signal present, note any that conflict with bearish thesis>",
  "key_signals": ["signal1", "signal2", "signal3"],
  "buy_window": "<time range in PT to enter short, e.g. 7:15 AM – 8:30 AM PT>"
}}

Only output the JSON."""

    try:
        response = get_client().messages.create(
            model=MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        import json
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)
        result["model"] = MODEL
        return result
    except Exception as e:
        return {
            "direction": "NEUTRAL",
            "position": "HOLD",
            "confidence": 0,
            "core_signals_count": 0,
            "target_price": None,
            "stop_price": None,
            "days_to_target": None,
            "timing_rationale": "",
            "reasoning": f"Analysis unavailable: {str(e)}",
            "key_signals": [],
            "buy_window": "N/A",
            "model": MODEL,
        }


def analyze_stock_long(ticker: str, indicators: dict, sentiment: dict, analyst: dict,
                       earnings: dict = None, score_data: dict = None,
                       accuracy_context: str = "", ticker_history: str = "",
                       earnings_calendar: dict = None, analyst_upside_pct: float = None,
                       insider_buying: dict = None, fundamentals: dict = None,
                       rel_strength_vs_spy: float = None, sector_return_5d: float = None,
                       sector_etf: str = None, short_interest_pct: float = None) -> dict:
    """
    Long-term Claude prediction — Friday scan only.
    Focuses on fundamental re-rating and institutional accumulation over 60-180 days.
    Deliberately ignores short-term noise signals.
    """
    price  = indicators.get("price", 0)
    ma50   = indicators.get("ma50") or price
    ma200  = indicators.get("ma200") or price

    _fwd_pe = (fundamentals or {}).get("forward_pe")
    _trailing_pe = (fundamentals or {}).get("trailing_pe")
    _peg = (fundamentals or {}).get("peg_ratio")

    # Sector/macro context line
    _sector_line = ""
    if sector_return_5d is not None and sector_etf:
        _sector_line = f"- Sector ETF ({sector_etf}) 5d return: {sector_return_5d:+.1f}% ({'LEADING — tailwind' if sector_return_5d >= 3 else 'LAGGING — headwind' if sector_return_5d <= -2 else 'NEUTRAL'})\n"
    _spy_line = ""
    if rel_strength_vs_spy is not None:
        _spy_line = f"- Relative strength vs SPY (5d): {rel_strength_vs_spy:+.1f}% ({'outperforming' if rel_strength_vs_spy >= 0 else 'underperforming'})\n"
    _short_line = ""
    if short_interest_pct is not None and short_interest_pct >= 5:
        _short_line = f"- Short interest: {short_interest_pct:.0f}% of float{' — SQUEEZE POTENTIAL as flows shift' if short_interest_pct >= 15 else ''}\n"

    prompt = f"""You are a long-term stock analyst. Analyze {ticker} for a 60–180 day position.
IGNORE short-term noise (RSI, MACD, volume spikes). Focus on fundamental re-rating catalysts and institutional conviction.

PRICE & TREND:
- Current price: ${price:.2f}
- MA50: ${ma50:.2f}  MA200: ${ma200:.2f}
- Price vs MA50: {'ABOVE' if price > ma50 else 'BELOW'}  |  vs MA200: {'ABOVE' if price > ma200 else 'BELOW'}
- ADX (trend strength): {indicators.get('adx', 20):.1f}
- Multi-week higher-low structure: {'YES — institutional accumulation pattern' if indicators.get('higher_low') else 'No'}

VALUATION:
- Forward P/E: {f"{_fwd_pe:.1f}" if _fwd_pe else "UNKNOWN"}  {'← room for multiple expansion' if _fwd_pe and _fwd_pe <= 20 else '← elevated multiple, less expansion room' if _fwd_pe and _fwd_pe > 40 else ''}
- Trailing P/E: {f"{_trailing_pe:.1f}" if _trailing_pe else "UNKNOWN"}
- PEG ratio: {f"{_peg:.2f}" if _peg else "UNKNOWN"}  {'← undervalued vs growth rate' if _peg and _peg < 1 else '← fairly valued' if _peg and _peg < 2 else '← expensive relative to growth' if _peg and _peg >= 3 else ''}

FUNDAMENTALS & CATALYST:
- Analyst consensus: {analyst.get('consensus', 'HOLD')}
- Earnings beats (last 4Q): {(earnings or {}).get('beats', 0)}/4  consecutive: {(earnings or {}).get('consecutive_beats', 0)}
{_earnings_context(earnings_calendar)}{_analyst_upside_context(analyst_upside_pct)}{_insider_context(insider_buying)}{_fundamentals_context(fundamentals)}
MACRO & SECTOR CONTEXT:
{_sector_line}{_spy_line}{_short_line}- If sector is leading and SPY RS is positive → supportive backdrop for re-rating
- If sector is lagging or SPY RS is negative → macro headwind, higher bar required for BULLISH

Active signals: {', '.join(score_data.get('bonus_reasons', [])) or 'None'}

{f"SYSTEM ACCURACY CONTEXT:{chr(10)}{accuracy_context}" if accuracy_context else ""}
{f"THIS TICKER'S HISTORY:{chr(10)}{ticker_history}" if ticker_history else ""}

TASK: Make a single long-term prediction (60–180 trading days). Every field must be derived from the data above — no defaults, no guessing.

DO NOT output BEARISH. If setup is unclear, output NEUTRAL.

DIRECTION: Only BULLISH if there is a concrete fundamental catalyst AND the macro/sector backdrop is not a strong headwind. NEUTRAL if no identifiable catalyst, valuation is stretched, or sector is significantly lagging.

TARGET PRICE: Must be anchored to exactly one of these mechanisms — name it in timing_rationale:
  (1) Analyst convergence: price converges toward mean analyst target
  (2) Multiple expansion: current P/E reprices higher as earnings accelerate (state before/after multiple)
  (3) Earnings revision cycle: EPS estimate upgrades drive repricing over next 1–2 quarters
  (4) Sector re-rating: sector multiple expansion carries the name (name the sector catalyst)
  (5) Event-driven catalyst: discrete event reprices the stock (product launch, regulatory approval, restructuring)
Minimum 15% move required — if you cannot anchor to one of these with 15% upside, output NEUTRAL.

STOP PRICE: Anchor to MA200 or a clear prior consolidation base. Account for volatility — wider for high-beta names. Typically 10–15% below entry but adjust for the stock's ATR.

DAYS TO TARGET: Map to the named catalyst type — allow ±15% flexibility within each range:
  - Earnings-driven re-rating → 50–105 days (base 60–90)
  - Multiple expansion / sentiment shift → 75–170 days (base 90–150)
  - Full fundamental / event-driven re-rating → 100–210 days (base 120–180)
State which category applies. Do not pick arbitrarily — it must follow from the mechanism.

CONFIDENCE — derive strictly from factor count. Count each that clearly applies:
  F1: Earnings quality (consecutive beats + growth rate both present)
  F2: Analyst conviction (strong consensus AND ≥20% mean target upside)
  F3: Insider activity (meaningful cluster buying, not token purchases)
  F4: Valuation setup (PEG < 1.5 OR fwd P/E ≤ 25 with room to expand)
  F5: Fundamental momentum (revenue AND earnings growth both accelerating YoY)

Hard rules (non-negotiable):
  - Fewer than 2 factors present → confidence MUST be ≤ 55, output NEUTRAL
  - ≥2 of F1–F5 are UNKNOWN → cap confidence at 60, strongly prefer NEUTRAL
  - Missing F1 (earnings) OR F4 (valuation) → automatically drop one confidence tier
  - Valuation stretched (PEG ≥3 or fwd P/E >50) → subtract 8 from confidence
  - Macro headwind (sector lagging AND underperforming SPY) → subtract 5 from confidence

Score ranges after applying hard rules:
  - 5 factors → 78–88
  - 4 factors → 65–77
  - 3 factors → 55–64
  - ≤2 factors → <55, NEUTRAL recommended

Respond in this exact JSON:
{{
  "direction": "BULLISH" | "NEUTRAL",
  "position": "LONG" | "HOLD",
  "confidence": <integer — must obey hard rules above>,
  "core_signals_count": <integer 0–5: how many of F1–F5 are clearly present>,
  "target_price": <float — anchored to named mechanism>,
  "stop_price": <float — MA200 or consolidation base, adjusted for volatility>,
  "days_to_target": <integer — within allowed range for the named catalyst category>,
  "timing_rationale": "<1 sentence: name the exact mechanism (1–5) and catalyst timeline>",
  "reasoning": "<3-4 sentences: name each factor present, valuation context, macro backdrop, what is missing>",
  "key_signals": ["signal1", "signal2", "signal3"],
  "buy_window": "Any time — long-term position, but prefer entry on pullback to MA50 or support retest"
}}

Only output the JSON."""

    try:
        response = get_client().messages.create(
            model=MODEL,
            max_tokens=700,
            messages=[{"role": "user", "content": prompt}]
        )
        import json
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)
        result["model"] = MODEL
        return result
    except Exception as e:
        return {
            "direction": "NEUTRAL",
            "position": "HOLD",
            "confidence": 0,
            "target_price": None,
            "stop_price": None,
            "days_to_target": None,
            "timing_rationale": "",
            "reasoning": f"Analysis unavailable: {str(e)}",
            "key_signals": [],
            "buy_window": "Any time",
            "model": MODEL,
        }


def analyze_stock_long_bearish(ticker: str, indicators: dict, sentiment: dict, analyst: dict,
                               earnings: dict = None, score_data: dict = None,
                               accuracy_context: str = "", ticker_history: str = "",
                               earnings_calendar: dict = None,
                               analyst_upside_pct: float = None,
                               insider_buying: dict = None,
                               fundamentals: dict = None,
                               rel_strength_vs_spy: float = None,
                               sector_return_5d: float = None,
                               sector_etf: str = None) -> dict:
    """
    Long-term bearish Claude prediction — Friday scan only.
    Focuses on fundamental deterioration and institutional re-rating downward over 60-180 days.
    DO NOT output BULLISH — if no clear bearish catalyst, output NEUTRAL.
    """
    price  = indicators.get("price", 0)
    ma50   = indicators.get("ma50") or price
    ma200  = indicators.get("ma200") or price
    _fwd_pe      = (fundamentals or {}).get("forward_pe")
    _trailing_pe = (fundamentals or {}).get("trailing_pe")
    _earn_growth = (fundamentals or {}).get("earnings_growth_pct")

    _sector_line = ""
    if sector_return_5d is not None and sector_etf:
        _sector_line = f"- Sector ETF ({sector_etf}) 5d return: {sector_return_5d:+.1f}% ({'WEAK — confirms bearish backdrop' if sector_return_5d <= -2 else 'STRONG — sector fights the short thesis' if sector_return_5d >= 3 else 'NEUTRAL'})\n"
    _spy_line = ""
    if rel_strength_vs_spy is not None:
        _spy_line = f"- Relative strength vs SPY (5d): {rel_strength_vs_spy:+.1f}% ({'outperforming — stock-specific strength, caution on short' if rel_strength_vs_spy >= 2 else 'underperforming — stock-specific weakness, confirms short thesis'})\n"

    prompt = f"""You are a long-term stock analyst. Analyze {ticker} for a SHORT position over 60–180 days.
IGNORE short-term noise (RSI, MACD, volume spikes). Focus on fundamental deterioration and structural breakdown.

PRICE & TREND:
- Current price: ${price:.2f}
- MA50: ${ma50:.2f}  MA200: ${ma200:.2f}
- Price vs MA50: {'ABOVE' if price > ma50 else 'BELOW ← structural weakness'}  |  vs MA200: {'ABOVE' if price > ma200 else 'BELOW ← institutional trend broken'}
- ADX (trend strength): {indicators.get('adx', 20):.1f}
- Death cross (MA50 < MA200): {'YES — confirmed downtrend' if ma50 < ma200 else 'No'}

VALUATION VS DETERIORATION:
- Forward P/E: {f"{_fwd_pe:.1f}" if _fwd_pe else "UNKNOWN"}  {'← elevated multiple with declining earnings = compression risk' if _fwd_pe and _fwd_pe > 35 and (_earn_growth or 0) <= 0 else '← valuation may already be compressed, verify before shorting' if _fwd_pe and _fwd_pe < 15 else ''}
- Trailing P/E: {f"{_trailing_pe:.1f}" if _trailing_pe else "UNKNOWN"}  (shown here only — not repeated in fundamentals below)

FUNDAMENTAL DETERIORATION:
- Analyst consensus: {analyst.get('consensus', 'HOLD')} {'← SELL SIGNAL' if analyst.get('consensus') in ('SELL', 'STRONG_SELL') else ''}
- Earnings beats (last 4Q): {(earnings or {}).get('beats', 0)}/4  (misses = {4 - (earnings or {}).get('beats', 0)})  consecutive beats: {(earnings or {}).get('consecutive_beats', 0)}
{_earnings_context(earnings_calendar)}{_analyst_upside_context(analyst_upside_pct)}{_insider_context(insider_buying)}{_fundamentals_context(fundamentals)}
MACRO & SECTOR CONTEXT:
{_sector_line}{_spy_line}- Weak company in WEAK sector = strong short. Weak company in STRONG sector = poor timing.

Active signals: {', '.join(score_data.get('bonus_reasons', [])) or 'None'}

{f"SYSTEM ACCURACY CONTEXT:{chr(10)}{accuracy_context}" if accuracy_context else ""}
{f"THIS TICKER'S HISTORY:{chr(10)}{ticker_history}" if ticker_history else ""}

TASK: Make a single long-term BEARISH prediction. Every field must derive from the data above.

DO NOT output BULLISH. If there is no clear deterioration thesis or the macro backdrop fights the short, output NEUTRAL.

AVOID "CHEAP BAD" STOCKS — if valuation is already compressed (low P/E, well below peers), the stock may not fall further. Require stronger structural deterioration evidence OR output NEUTRAL. Best shorts still look expensive despite bad fundamentals.

DIRECTION: Only BEARISH if: (a) concrete fundamental deterioration is present AND (b) a "why now" forward catalyst exists. Exception: if BOTH D1 (confirmed decline) AND D5 (structural breakdown) are strong, ongoing deterioration itself qualifies as catalyst.
NEUTRAL if: fundamentals are mixed, no forward catalyst, valuation already compressed, or sector is strongly outperforming with no idiosyncratic catalyst.

"WHY NOW" CATALYST — must identify at least one (or use D1+D5 exception above):
  - Upcoming earnings risk (guidance cut likely, estimates still too high)
  - Analyst downgrade cycle in progress
  - Margin compression becoming visible / accelerating
  - Competitive/structural disruption accelerating
  - Regulatory / legal event approaching
Macro filter: if sector is strongly outperforming (+3%+) AND no idiosyncratic catalyst present → output NEUTRAL (don't fight sector flows).

TARGET PRICE: Anchored to one of these mechanisms — name it explicitly:
  (1) Multiple compression: elevated P/E reprices lower as earnings decline — state before/after multiple (e.g. 45x → 25x)
  (2) Support reversion: price falls to prior base / MA200 structural support
  (3) Analyst target convergence: price moves toward mean analyst target
  (4a) Margin compression repricing: falling margins → lower valuation multiple (state margin before/after)
  (4b) Growth deceleration repricing: high-growth → low-growth multiple shift (state growth rate change)
  (4c) Business model impairment: permanent lower profitability forces structural re-rating
Minimum 15% downside required. Do not anchor to a round number — use the mechanism math.

STOP PRICE: Anchor to MA200 or clear prior resistance. Typically 10–15% above entry. Widen for high-beta or volatile names. Note if this stop is tight relative to the stock's volatility.

DAYS TO TARGET — map to catalyst type (allow ±15% flexibility):
  - Earnings-driven disappointment → 25–75 days (base 30–60)
  - Multiple / margin compression → 55–125 days (base 60–120)
  - Structural / narrative deterioration → 100–210 days (base 120–180)
Note: >180 days requires explicit multi-quarter deterioration thesis — do not use as a default.

CONFIDENCE — derive strictly from factor count:
  D1: Revenue or earnings decline confirmed (actually declining YoY, not just slowing)
  D2: Analyst SELL/downgrade with mean target below current price
  D3: Negative FCF or severe margin collapse (<5% operating margin)
  D4: Consecutive earnings misses (2+ quarters in a row)
  D5: Narrative/structural breakdown (competitive disruption, secular decline, model risk)
  D6: Valuation disconnect — stock still priced at premium multiple despite deterioration (fwd P/E elevated vs peers or vs growth rate)
Note: absence of insider buying during active deterioration strengthens the bearish thesis (strong companies attract insider buying; silence is telling).

Hard rules (non-negotiable):
  - Fewer than 2 factors → confidence MUST be ≤ 55, output NEUTRAL
  - No "why now" catalyst (and D1+D5 exception does not apply) → output NEUTRAL
  - Valuation already compressed (low P/E, below peers) without D5 → output NEUTRAL
  - Insider buying STRONG → subtract 8 from confidence
  - Sector strongly outperforming (+3%+) with no idiosyncratic catalyst → subtract 8, prefer NEUTRAL
  - ≥2 of D1–D6 are UNKNOWN → cap confidence at 60, prefer NEUTRAL

Score ranges after applying rules:
  - 6 factors → 78–85
  - 5 factors → 72–77
  - 4 factors → 63–71
  - 3 factors → 55–62
  - ≤2 factors → <55, NEUTRAL recommended
  - Hard cap: 85 max (shorts have permanent upward-bias headwind; most outputs should land 60–75)

Respond in this exact JSON:
{{
  "direction": "BEARISH" | "NEUTRAL",
  "position": "SHORT" | "HOLD",
  "confidence": <integer — must obey hard rules above>,
  "core_signals_count": <integer 0–6: how many of D1–D6 are clearly present>,
  "target_price": <float — anchored to named mechanism with explicit math>,
  "stop_price": <float — MA200 or resistance, adjusted for volatility>,
  "days_to_target": <integer — within allowed range for named catalyst category; >180 requires justification>,
  "timing_rationale": "<1 sentence: name the 'why now' catalyst (or D1+D5 exception) and timeline category>",
  "reasoning": "<3-4 sentences: name each factor, the forward catalyst, valuation context, macro backdrop, what is missing>",
  "key_signals": ["signal1", "signal2", "signal3"],
  "buy_window": "Prefer entry on: weak bounce to MA50/MA200, failed breakout attempt, or post-earnings relief rally"
}}

Only output the JSON."""

    try:
        response = get_client().messages.create(
            model=MODEL,
            max_tokens=700,
            messages=[{"role": "user", "content": prompt}]
        )
        import json
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)
        result["model"] = MODEL
        return result
    except Exception as e:
        return {
            "direction": "NEUTRAL",
            "position": "HOLD",
            "confidence": 0,
            "core_signals_count": 0,
            "target_price": None,
            "stop_price": None,
            "days_to_target": None,
            "timing_rationale": "",
            "reasoning": f"Analysis unavailable: {str(e)}",
            "key_signals": [],
            "buy_window": "Any time",
            "model": MODEL,
        }


def analyze_forensic(ticker: str, price_summary: str, indicators_timeline: str,
                     news_timeline: str, date_range: str) -> dict:
    """Deep forensic analysis for the Deep Dive page."""
    prompt = f"""You are analyzing {ticker} for a forensic post-mortem over the period {date_range}.

PRICE ACTION SUMMARY:
{price_summary}

INDICATOR TIMELINE (key signals that fired):
{indicators_timeline}

NEWS TIMELINE:
{news_timeline}

Analyze what drove the major price move(s) in this period. Provide your response in this exact JSON format:
{{
  "event_summary": "<2-3 sentences: what happened and why>",
  "earliest_signal": "<which indicator gave the earliest warning and when>",
  "signals_that_fired": ["signal1", "signal2"],
  "signals_missed": ["signal that was present but not in formula", ...],
  "formula_suggestions": [
    {{
      "plain_english": "<what to add/change in plain English>",
      "technical_detail": "<specific indicator, weight, condition>",
      "projected_benefit": "<estimated improvement>"
    }}
  ],
  "analyst_quality": "<brief note on whether news/analyst coverage was predictive or lagging>"
}}

Only output the JSON."""

    try:
        response = get_client().messages.create(
            model=MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )
        import json
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception as e:
        return {
            "event_summary": f"Analysis unavailable: {str(e)}",
            "earliest_signal": "N/A",
            "signals_that_fired": [],
            "signals_missed": [],
            "formula_suggestions": [],
            "analyst_quality": "N/A",
        }


def analyze_missed_opportunities(missed_list: list) -> dict:
    """Weekly opportunity analyzer — finds patterns in what the formula missed."""
    if not missed_list:
        return {}

    lines = []
    for m in missed_list:
        lines.append(
            f"{m['ticker']}: Score {m['score_at_rejection']}, moved {m['move_pct']:+.1f}% "
            f"in {m['days_to_move']} days. Signals present: {m.get('signals_present', {})}"
        )

    prompt = f"""You are analyzing stocks that were REJECTED by a stock screening formula (scored 55–74)
but then moved significantly (≥3%) shortly after rejection.

MISSED OPPORTUNITIES:
{chr(10).join(lines)}

Identify patterns in what the formula is missing. Respond in this exact JSON format:
{{
  "pattern_summary": "<2 sentences: what common pattern do these missed stocks share>",
  "suggestions": [
    {{
      "plain_english": "<what to change in plain English>",
      "technical_detail": "<specific: which indicator, what weight change, what condition>",
      "evidence_tickers": ["TICK1", "TICK2"],
      "projected_improvement_pct": <number like 5.0>
    }}
  ]
}}

Only output the JSON."""

    try:
        response = get_client().messages.create(
            model=MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}]
        )
        import json
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception as e:
        return {"pattern_summary": f"Analysis failed: {e}", "suggestions": []}


def analyze_prediction_outcomes(wins: list, losses: list, existing_suggestions: list = None) -> dict:
    """Analyze why predictions failed and whether winning timing was accurate."""
    def _summarize(p: dict) -> str:
        entry  = p.get("price_at_prediction") or 0
        close  = p.get("price_at_close") or 0
        target = p.get("target_low") or 0
        stop   = p.get("stop_loss") or 0
        days_pred = p.get("days_to_target") or "?"
        predicted_on = p.get("predicted_on", "")[:10]
        verified_on  = p.get("verified_on", "") or ""
        try:
            from datetime import datetime
            actual_days = (datetime.fromisoformat(verified_on[:10]) - datetime.fromisoformat(predicted_on)).days if verified_on else None
        except Exception:
            actual_days = None
        days_str = f"predicted {days_pred}d, actual {actual_days}d" if actual_days is not None else f"predicted {days_pred}d"
        return (
            f"{p.get('ticker')} [{p.get('outcome')}] {p.get('direction')} {p.get('timeframe')}-term | "
            f"entry=${entry:.2f} target=${target:.2f} stop=${stop:.2f} close=${close:.2f} | "
            f"conf={p.get('confidence')}% score={p.get('score')} | timing: {days_str} | "
            f"reason={p.get('closed_reason','')} | signals={p.get('reasoning','')[:120]}"
        )

    loss_lines = [_summarize(p) for p in losses]
    win_lines  = [_summarize(p) for p in wins]

    already_known = ""
    if existing_suggestions:
        already_known = "\nALREADY ADDRESSED (do NOT suggest these again):\n" + \
            "\n".join(f"- {s}" for s in existing_suggestions if s) + "\n"

    scoring_context = """
CURRENT SCORING FORMULA (0-100):
- Momentum (25 pts): RSI <30=11pts, <40=7pts, 60-70=6pts, >70=1pt | MACD crossover=8pts, recent=7pts, bullish=5pts | ROC >=5%=5pts, >=2%=3pts
- Trend (20 pts): price>MA20>MA50=11pts, price>MA20=9pts, price>MA50=6pts | ADX>30=8pts, >20=4pts | flat ADX multiplier=0.7x
- Volatility (15 pts): BB squeeze+position<0.6=9pts, BB breakout up=7pts | ATR rising=4pts
- Volume (20 pts): volume surge >=3x=10pts, >=2x=7pts, >=1.5x=4pts | OBV confirming=6pts, diverging bullish=5pts | VWAP above=3pts
- Sentiment (10 pts): news score>0.6=6pts, >0.3=4pts | social mentions>50=4pts, >20=2pts
- External (10 pts): analyst STRONG_BUY=6pts, BUY=4pts, HOLD=2pts | earnings consecutive beats min(n+1,4)pts
- Bonuses: RSI divergence+3, Golden cross+3, BB squeeze+2, 52w high breakout+4, Dual-list+3
- Earnings catalyst bonus: 3+ consecutive beats+10, 1-2 beats+5
- Analyst upside >20%: +5
- Insider buying STRONG ($500K+ or 3+ insiders): +15, MODERATE ($100K+): +8
- Score threshold to enter Claude batch: 45/100
- Min profit % to save prediction: 4%
- Confidence threshold shown to user: any, high confidence = >=75%"""

    prompt = f"""You are analyzing a stock prediction system's track record to improve its {len(wins)/(len(wins)+len(losses))*100:.1f}% win rate (target: >65%).

{scoring_context}

LOSSES ({len(loss_lines)}):
{chr(10).join(loss_lines) if loss_lines else "None yet"}

WINS ({len(win_lines)}):
{chr(10).join(win_lines) if win_lines else "None yet"}
{already_known}
Analyze the full picture and produce ONE combined improvement plan that:
1. Identifies the root causes of losses without conflicting with what made wins work
2. Proposes a single coherent set of formula changes — no overlapping or redundant rules
3. Each change must reference a specific score group, threshold, or weight from the formula above
4. The changes together should make sense as a complete update, not independent patches

Respond in this exact JSON:
{{
  "failure_pattern": "<2-3 sentences: root causes of losses>",
  "success_pattern": "<2-3 sentences: what winning predictions had in common>",
  "timing_accuracy_note": "<1-2 sentences: timing accuracy on winning trades>",
  "suggestions": [
    {{
      "plain_english": "<what to change and why, in plain English>",
      "technical_detail": "<exact change: e.g. 'raise SCORE_THRESHOLD from 45 to 52' or 'reduce trend weight from 1.0 to 0.7 when ADX < 20'>",
      "evidence_tickers": ["TICK1", "TICK2"],
      "projected_improvement_pct": <estimated win rate improvement, e.g. 8.0>
    }}
  ]
}}

Only output the JSON. Produce 3-5 suggestions that form a coherent non-overlapping improvement plan."""

    try:
        response = get_client().messages.create(
            model=MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}]
        )
        import json
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception as e:
        return {"failure_pattern": f"Analysis failed: {e}", "timing_accuracy_note": "", "suggestions": []}


def estimate_cost(claude_calls: int) -> float:
    """Rough cost estimate. Haiku 4.5: ~$0.00025 per call at our token usage."""
    return round(claude_calls * 0.00025, 4)
