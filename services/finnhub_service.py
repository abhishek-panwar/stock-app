import os
import finnhub
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

_client = None

def get_client():
    global _client
    if _client is None:
        _client = finnhub.Client(api_key=os.environ["FINNHUB_API_KEY"])
    return _client


def get_news_sentiment(ticker: str, hours: int = 48) -> dict:
    """Returns aggregated news sentiment for the last N hours."""
    try:
        now = datetime.utcnow()
        from_dt = (now - timedelta(hours=hours)).strftime("%Y-%m-%d")
        to_dt = now.strftime("%Y-%m-%d")
        news = get_client().company_news(ticker, _from=from_dt, to=to_dt)
        if not news:
            return {"score": 0.0, "volume": 0, "articles": []}
        scores = []
        articles = []
        for item in news[:20]:
            sentiment = item.get("sentiment", {})
            score = sentiment.get("score", 0.0) if sentiment else 0.0
            scores.append(score)
            articles.append({
                "headline": item.get("headline", ""),
                "url": item.get("url", ""),
                "source": item.get("source", ""),
                "summary": item.get("summary", ""),
                "datetime": item.get("datetime", 0),
            })
        avg_score = sum(scores) / len(scores) if scores else 0.0
        return {"score": round(avg_score, 3), "volume": len(news), "articles": articles}
    except Exception:
        return {"score": 0.0, "volume": 0, "articles": []}


def get_news_with_authors(ticker: str, hours: int = 48) -> list[dict]:
    """Returns articles with author/source info for analyst credibility tracking."""
    try:
        now = datetime.utcnow()
        from_dt = (now - timedelta(hours=hours)).strftime("%Y-%m-%d")
        to_dt = now.strftime("%Y-%m-%d")
        news = get_client().company_news(ticker, _from=from_dt, to=to_dt)
        if not news:
            return []
        results = []
        for item in news[:20]:
            results.append({
                "headline": item.get("headline", ""),
                "url": item.get("url", ""),
                "source": item.get("source", ""),
                "summary": item.get("summary", ""),
                "datetime": item.get("datetime", 0),
                "category": item.get("category", ""),
            })
        return results
    except Exception:
        return []


def get_historical_news(ticker: str, from_date: str, to_date: str) -> list[dict]:
    """For forensic / deep dive analysis. from_date/to_date: 'YYYY-MM-DD'"""
    try:
        news = get_client().company_news(ticker, _from=from_date, to=to_date)
        if not news:
            return []
        results = []
        for item in news:
            results.append({
                "headline": item.get("headline", ""),
                "url": item.get("url", ""),
                "source": item.get("source", ""),
                "summary": item.get("summary", ""),
                "datetime": item.get("datetime", 0),
            })
        return results
    except Exception:
        return []


def get_social_sentiment(ticker: str) -> dict:
    try:
        data = get_client().stock_social_sentiment(ticker)
        if not data:
            return {"reddit_score": 0, "twitter_score": 0, "mentions": 0}
        reddit = data.get("reddit", [])
        twitter = data.get("twitter", [])
        r_score = sum(r.get("score", 0) for r in reddit[-5:]) / max(len(reddit[-5:]), 1)
        t_score = sum(t.get("score", 0) for t in twitter[-5:]) / max(len(twitter[-5:]), 1)
        r_mentions = sum(r.get("mention", 0) for r in reddit[-5:])
        return {
            "reddit_score": round(r_score, 3),
            "twitter_score": round(t_score, 3),
            "mentions": r_mentions,
        }
    except Exception:
        return {"reddit_score": 0, "twitter_score": 0, "mentions": 0}


def get_analyst_recommendation(ticker: str) -> dict:
    try:
        recs = get_client().recommendation_trends(ticker)
        if not recs:
            return {"consensus": "HOLD", "score": 0.5, "strong_buy": 0, "buy": 0, "hold": 0, "sell": 0, "strong_sell": 0}
        latest = recs[0]
        sb = latest.get("strongBuy", 0)
        b = latest.get("buy", 0)
        h = latest.get("hold", 0)
        s = latest.get("sell", 0)
        ss = latest.get("strongSell", 0)
        total = sb + b + h + s + ss or 1
        score = (sb * 1.0 + b * 0.75 + h * 0.5 + s * 0.25 + ss * 0.0) / total
        if score >= 0.75:
            consensus = "STRONG_BUY"
        elif score >= 0.60:
            consensus = "BUY"
        elif score >= 0.40:
            consensus = "HOLD"
        elif score >= 0.25:
            consensus = "SELL"
        else:
            consensus = "STRONG_SELL"
        return {
            "consensus": consensus, "score": round(score, 3),
            "strong_buy": sb, "buy": b, "hold": h, "sell": s, "strong_sell": ss,
        }
    except Exception:
        return {"consensus": "HOLD", "score": 0.5, "strong_buy": 0, "buy": 0, "hold": 0, "sell": 0, "strong_sell": 0}


def get_earnings_history(ticker: str) -> dict:
    try:
        earnings = get_client().company_earnings(ticker, limit=4)
        if not earnings:
            return {"beats": 0, "consecutive_beats": 0}
        beats = 0
        consecutive = 0
        for e in earnings:
            actual = e.get("actual")
            estimate = e.get("estimate")
            if actual is not None and estimate is not None and actual > estimate:
                beats += 1
                if consecutive == beats - 1:
                    consecutive = beats
        return {"beats": beats, "consecutive_beats": consecutive}
    except Exception:
        return {"beats": 0, "consecutive_beats": 0}


def get_upcoming_earnings_universe(days_ahead: int = 7) -> dict:
    """
    Single Finnhub call for ALL upcoming earnings in the next days_ahead days.
    Returns {ticker: {"days_to_earnings": int, "earnings_date": str}}
    Cached in Supabase for 24 hours — call once per nightly run, not per stock.
    """
    try:
        from database.db import get_cache, set_cache
        cache_key = f"earnings_universe_{days_ahead}d"
        cached = get_cache(cache_key)
        if cached is not None:
            print(f"  Earnings calendar: loaded from cache ({len(cached)} tickers)")
            return cached

        today = datetime.utcnow().date()
        to_dt = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        data = get_client().earnings_calendar(
            _from=today.strftime("%Y-%m-%d"), to=to_dt, symbol=""
        )
        events = (data or {}).get("earningsCalendar", [])
        result = {}
        for e in events:
            symbol = e.get("symbol", "").upper()
            edate_str = e.get("date", "")
            if not symbol or not edate_str:
                continue
            try:
                edate = datetime.strptime(edate_str, "%Y-%m-%d").date()
                days = (edate - today).days
                if 0 <= days <= days_ahead:
                    # Keep soonest if duplicate
                    if symbol not in result or days < result[symbol]["days_to_earnings"]:
                        result[symbol] = {"days_to_earnings": days, "earnings_date": edate_str}
            except Exception:
                continue

        set_cache(cache_key, result, ttl_hours=24)
        print(f"  Earnings calendar: fetched {len(result)} tickers with upcoming earnings")
        return result
    except Exception as e:
        print(f"  Earnings calendar fetch failed: {e}")
        return {}


def get_earnings_calendar(ticker: str, days_ahead: int = 7) -> dict:
    """
    Returns upcoming earnings date for ticker if within days_ahead.
    Result: {"has_upcoming": bool, "days_to_earnings": int|None, "earnings_date": str|None}
    """
    try:
        from datetime import date
        today = datetime.utcnow().date()
        to_dt = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        data = get_client().earnings_calendar(
            _from=today.strftime("%Y-%m-%d"), to=to_dt, symbol=ticker
        )
        events = (data or {}).get("earningsCalendar", [])
        if not events:
            return {"has_upcoming": False, "days_to_earnings": None, "earnings_date": None}
        # Pick the soonest event
        soonest = None
        soonest_days = None
        for e in events:
            edate_str = e.get("date", "")
            try:
                edate = date.fromisoformat(edate_str)
                days = (edate - today).days
                if 0 <= days <= days_ahead:
                    if soonest_days is None or days < soonest_days:
                        soonest = edate_str
                        soonest_days = days
            except Exception:
                continue
        if soonest is None:
            return {"has_upcoming": False, "days_to_earnings": None, "earnings_date": None}
        return {"has_upcoming": True, "days_to_earnings": soonest_days, "earnings_date": soonest}
    except Exception:
        return {"has_upcoming": False, "days_to_earnings": None, "earnings_date": None}


def get_analyst_price_target(ticker: str) -> dict:
    """
    Returns analyst mean price target. Cached per ticker for 24h.
    Result: {"mean_target": float|None, "num_analysts": int}
    """
    try:
        from database.db import get_cache, set_cache
        cache_key = f"analyst_target_{ticker}"
        cached = get_cache(cache_key)
        if cached is not None:
            return cached

        data = get_client().price_target(ticker)
        if not data:
            result = {"mean_target": None, "num_analysts": 0}
        else:
            mean_target = data.get("targetMean")
            result = {
                "mean_target": round(float(mean_target), 2) if mean_target else None,
                "num_analysts": int(bool(data.get("targetHigh", 0))),
            }
        set_cache(cache_key, result, ttl_hours=24)
        return result
    except Exception:
        return {"mean_target": None, "num_analysts": 0}


def compute_hot_score(ticker: str) -> float:
    """0–100 hot score for dynamic universe selection."""
    try:
        from services.yfinance_service import get_price_momentum
        news = get_news_sentiment(ticker, hours=48)
        social = get_social_sentiment(ticker)
        analyst = get_analyst_recommendation(ticker)
        momentum = get_price_momentum(ticker, days=3) or 0.0

        news_score = min(news["volume"] / 20.0, 1.0) * 25
        analyst_score = analyst["score"] * 25
        social_score = min(abs(social["reddit_score"]) + abs(social["twitter_score"]), 1.0) * 25
        momentum_score = min(abs(momentum) / 5.0, 1.0) * 25

        return round(news_score + analyst_score + social_score + momentum_score, 1)
    except Exception:
        return 0.0
