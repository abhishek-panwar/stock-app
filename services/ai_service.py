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


def analyze_stock(ticker: str, indicators: dict, sentiment: dict, analyst: dict,
                  score_data: dict, accuracy_context: str = "", ticker_history: str = "") -> dict:
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
- Earnings beats (last 4Q): {analyst.get('beats', 0) if hasattr(analyst, 'get') else 0}

SIGNAL SCORE: {score_data.get('total', 0)}/100
Active bonus signals: {', '.join(score_data.get('bonus_reasons', [])) or 'None'}

{f"SYSTEM ACCURACY CONTEXT:{chr(10)}{accuracy_context}" if accuracy_context else ""}
{f"THIS TICKER'S HISTORY:{chr(10)}{ticker_history}" if ticker_history else ""}

TASK: Make a single prediction for this stock.
- Pick a realistic price target based on resistance levels and the magnitude of active signals
- Pick a stop loss based on support levels and ATR
- Estimate days_to_target honestly from ATR and momentum — if signals are weak or mixed, say so with lower confidence and more days
- If there is no clear setup, say NEUTRAL with confidence < 40

Respond in this exact JSON:
{{
  "direction": "BULLISH" | "BEARISH" | "NEUTRAL",
  "position": "LONG" | "SHORT" | "HOLD",
  "confidence": <0-100>,
  "target_price": <float — actual price target, not %>,
  "stop_price": <float — actual stop loss price>,
  "days_to_target": <integer — realistic trading days to reach target>,
  "timing_rationale": "<1 sentence: what drives the timing estimate>",
  "reasoning": "<2-3 sentences: what signals make this a good or bad setup>",
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


def estimate_cost(claude_calls: int) -> float:
    """Rough cost estimate. Haiku 4.5: ~$0.00025 per call at our token usage."""
    return round(claude_calls * 0.00025, 4)
