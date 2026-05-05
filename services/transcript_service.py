"""
Earnings call transcript tone scoring — FMP stable API.

Endpoint: GET /stable/earning-call-transcript?symbol=AAPL&year=2025&quarter=1
Returns: [{symbol, quarter, year, date, content}]  (~45K chars full transcript)

Call budget: 1 call per ticker per quarter. Cached 90 days (transcript doesn't change).
Fetched on Sat/Sun/Mon/Tue (4 free days × 250 calls = 1,000 available, 100 tickers = 100 calls).

Tone signals derived (no external NLP library — pure keyword counting):
  guidance_tone      — "POSITIVE" | "CAUTIOUS" | "NEGATIVE" | None
  demand_signals     — "STRONG" | "WEAK" | None
  margin_language    — "EXPANDING" | "COMPRESSING" | None
  management_defensiveness — True/False  (hedging / excuses language)
  transcript_score   — int -10..+10, composite signal for scorer injection
"""
import os
import time
import requests
from datetime import datetime, date, timezone
from database.db import get_cache, set_cache


FMP_BASE = "https://financialmodelingprep.com/stable"
_CACHE_TTL_HOURS = 90 * 24  # 90 days


# ── Keyword lists ─────────────────────────────────────────────────────────────

_GUIDANCE_POSITIVE = {
    "raised guidance", "raising guidance", "increased guidance", "strong demand",
    "record revenue", "accelerating growth", "outperformed", "exceeded expectations",
    "above expectations", "beat expectations", "strong pipeline", "robust demand",
    "favorable outlook", "positive momentum", "improving trends", "we are confident",
    "strong visibility", "solid execution",
}

_GUIDANCE_CAUTIOUS = {
    "macro uncertainty", "uncertain environment", "challenging environment",
    "headwinds", "softness", "moderated demand", "conservatively",
    "we remain cautious", "monitor closely", "below expectations",
    "slower than expected", "lower than expected", "reduced visibility",
    "difficult to predict", "dependent on", "contingent on",
}

_GUIDANCE_NEGATIVE = {
    "lowered guidance", "lowering guidance", "reduced guidance", "significantly below",
    "material weakness", "missed expectations", "well below", "substantially lower",
    "deteriorating", "meaningful decline", "market share loss", "losing market share",
    "customers churning", "elevated churn", "slowing dramatically",
}

_DEMAND_STRONG = {
    "strong demand", "record bookings", "pipeline growth", "bookings up",
    "backlog growing", "elevated demand", "unprecedented demand", "demand accelerating",
    "sold out", "waitlist", "customers requesting", "increased adoption",
}

_DEMAND_WEAK = {
    "demand softness", "soft demand", "demand weakness", "weakening demand",
    "elongated sales cycles", "delayed decisions", "budget constraints",
    "cautious spending", "customers deferring", "slower adoption", "project delays",
    "inventory digestion", "digesting inventory", "channel inventory",
}

_MARGIN_EXPANDING = {
    "margin expansion", "expanding margins", "operating leverage", "scale benefits",
    "efficiency gains", "cost discipline", "improving profitability",
    "higher gross margin", "gross margin improvement", "accretive", "margin accretion",
}

_MARGIN_COMPRESSING = {
    "margin pressure", "margin compression", "cost inflation", "cost headwinds",
    "elevated costs", "higher costs", "pricing pressure", "gross margin declined",
    "gross margin decreased", "below prior year", "cost investments", "invest heavily",
    "reinvesting", "near-term headwinds to margin",
}

_DEFENSIVENESS = {
    "one-time", "one time", "non-recurring", "excluding the impact",
    "if you adjust for", "on an adjusted basis", "fx headwind", "currency headwind",
    "normalizing for", "transitory", "temporary", "not indicative",
    "not how we look at it", "the way we think about",
    "disappointed", "we understand the concern", "fair question",
    "i want to be clear", "let me be clear",
}


def _score_keywords(text: str, positive_set: set, negative_set: set) -> int:
    """Returns +count/-count of matched keywords. Text already lowercased."""
    pos = sum(1 for kw in positive_set if kw in text)
    neg = sum(1 for kw in negative_set if kw in text)
    return pos - neg


def _parse_transcript(content: str) -> dict:
    """
    Pure keyword extraction — no external NLP.
    Returns tone signal dict.
    """
    if not content:
        return {}

    text = content.lower()

    # ── Guidance tone ──────────────────────────────────────────────────────────
    guidance_delta = _score_keywords(text, _GUIDANCE_POSITIVE, _GUIDANCE_NEGATIVE)
    caution_count  = sum(1 for kw in _GUIDANCE_CAUTIOUS if kw in text)

    if guidance_delta >= 3:
        guidance_tone = "POSITIVE"
    elif guidance_delta <= -2:
        guidance_tone = "NEGATIVE"
    elif caution_count >= 3 or guidance_delta <= 0:
        guidance_tone = "CAUTIOUS"
    else:
        guidance_tone = "POSITIVE" if guidance_delta >= 1 else "CAUTIOUS"

    # ── Demand signals ─────────────────────────────────────────────────────────
    demand_delta = _score_keywords(text, _DEMAND_STRONG, _DEMAND_WEAK)
    if demand_delta >= 2:
        demand_signals = "STRONG"
    elif demand_delta <= -1:
        demand_signals = "WEAK"
    else:
        demand_signals = None

    # ── Margin language ────────────────────────────────────────────────────────
    margin_delta = _score_keywords(text, _MARGIN_EXPANDING, _MARGIN_COMPRESSING)
    if margin_delta >= 2:
        margin_language = "EXPANDING"
    elif margin_delta <= -1:
        margin_language = "COMPRESSING"
    else:
        margin_language = None

    # ── Management defensiveness ───────────────────────────────────────────────
    defense_count = sum(1 for kw in _DEFENSIVENESS if kw in text)
    management_defensiveness = defense_count >= 4

    # ── Composite transcript score (-10..+10) ─────────────────────────────────
    score = 0
    score += {"POSITIVE": 4, "CAUTIOUS": -1, "NEGATIVE": -6}.get(guidance_tone, 0)
    score += {"STRONG": 3, "WEAK": -3}.get(demand_signals or "", 0)
    score += {"EXPANDING": 2, "COMPRESSING": -2}.get(margin_language or "", 0)
    if management_defensiveness:
        score -= 2

    transcript_score = max(-10, min(10, score))

    return {
        "guidance_tone":              guidance_tone,
        "demand_signals":             demand_signals,
        "margin_language":            margin_language,
        "management_defensiveness":   management_defensiveness,
        "transcript_score":           transcript_score,
    }


def _get_latest_quarter() -> tuple[int, int]:
    """Returns (year, quarter) of the most recently completed earnings quarter."""
    today = date.today()
    month = today.month
    year  = today.year
    # Q1 ends Mar 31, Q2 Jun 30, Q3 Sep 30, Q4 Dec 31
    # After end of quarter, give 30 days for transcript to appear
    if month <= 4:          # Jan-Apr → Q4 of prior year
        return year - 1, 4
    elif month <= 7:        # May-Jul → Q1 of this year
        return year, 1
    elif month <= 10:       # Aug-Oct → Q2 of this year
        return year, 2
    else:                   # Nov-Dec → Q3 of this year
        return year, 3


def get_earnings_transcript_tone(ticker: str, log_api: bool = True, run_date: str = "") -> dict:
    """
    Fetches the most recent earnings call transcript from FMP and scores its tone.

    Returns:
      {
        "guidance_tone":            "POSITIVE" | "CAUTIOUS" | "NEGATIVE" | None
        "demand_signals":           "STRONG" | "WEAK" | None
        "margin_language":          "EXPANDING" | "COMPRESSING" | None
        "management_defensiveness": bool
        "transcript_score":         int  (-10..+10)
        "transcript_quarter":       str  (e.g. "Q1 2025")
        "source":                   "fmp_transcript"
      }
    Cached 90 days.
    """
    cache_key = f"transcript_tone_{ticker}"
    cached = get_cache(cache_key)
    if cached is not None:
        return cached

    empty = {
        "guidance_tone": None, "demand_signals": None, "margin_language": None,
        "management_defensiveness": False, "transcript_score": 0,
        "transcript_quarter": None, "source": "fmp_transcript",
    }

    api_key = os.environ.get("FMP_API_KEY", "")
    if not api_key:
        return empty

    year, quarter = _get_latest_quarter()

    try:
        time.sleep(0.25)  # respect FMP rate limit
        params = {"symbol": ticker, "year": year, "quarter": quarter, "apikey": api_key}
        r = requests.get(f"{FMP_BASE}/earning-call-transcript", params=params, timeout=20)

        if r.status_code == 429:
            print(f"  FMP rate limit hit — sleeping 60s")
            time.sleep(60)
            r = requests.get(f"{FMP_BASE}/earning-call-transcript", params=params, timeout=20)

        if log_api and run_date:
            from database.db import log_api_call
            success = r.status_code == 200
            log_api_call(run_date, "fmp_transcript", ticker, success)

        if r.status_code != 200:
            set_cache(cache_key, empty, ttl_hours=_CACHE_TTL_HOURS)
            return empty

        data = r.json()
        if not data or not isinstance(data, list) or not data[0].get("content"):
            # Try previous quarter as fallback
            prev_year, prev_q = (year - 1, 4) if quarter == 1 else (year, quarter - 1)
            time.sleep(0.25)
            params["year"]    = prev_year
            params["quarter"] = prev_q
            r2 = requests.get(f"{FMP_BASE}/earning-call-transcript", params=params, timeout=20)
            if r2.status_code == 200:
                data2 = r2.json()
                if data2 and isinstance(data2, list) and data2[0].get("content"):
                    data    = data2
                    year    = prev_year
                    quarter = prev_q
            if not data or not isinstance(data, list) or not data[0].get("content"):
                set_cache(cache_key, empty, ttl_hours=_CACHE_TTL_HOURS)
                return empty

        content = data[0]["content"]
        tone    = _parse_transcript(content)
        result  = {
            **tone,
            "transcript_quarter": f"Q{quarter} {year}",
            "source": "fmp_transcript",
        }
        set_cache(cache_key, result, ttl_hours=_CACHE_TTL_HOURS)
        return result

    except Exception as e:
        print(f"  Transcript error {ticker}: {e}")
        set_cache(cache_key, empty, ttl_hours=_CACHE_TTL_HOURS)
        return empty


def get_transcripts_batch(tickers: list[str], run_date: str = "", log_progress: bool = True) -> dict[str, dict]:
    """
    Batch fetch transcript tone for a list of tickers.
    Called by weekend_transcript_fetcher.py — runs Sat/Sun/Mon/Tue only.
    Returns {ticker: tone_dict}.
    """
    results = {}
    total = len(tickers)
    for i, ticker in enumerate(tickers):
        try:
            tone = get_earnings_transcript_tone(ticker, log_api=True, run_date=run_date)
            if tone and tone.get("guidance_tone") is not None:
                results[ticker] = tone
        except Exception as e:
            print(f"  Transcript batch error {ticker}: {e}")
        if log_progress and (i + 1) % 20 == 0:
            print(f"  Transcript progress: {i+1}/{total}")
    return results
