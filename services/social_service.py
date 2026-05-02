"""
Social velocity service — StockTwits + Reddit mention velocity.
Measures rate-of-change in social attention, not raw mention count.
Cached 1h in Supabase (social moves fast).
"""
import os
import requests
import time
import threading
from datetime import datetime, timezone, timedelta

SOCIAL_CACHE_TTL_H = 1

# Reddit enforces ~1 req/sec globally across all threads
_reddit_lock = threading.Lock()
_reddit_last_call: float = 0.0
_REDDIT_DELAY = 1.1  # seconds between requests


def get_social_velocity(ticker: str) -> dict:
    """
    Returns combined social velocity signal for a ticker.
    Reads from cache first; fetches live if stale.
    """
    from database.db import get_cache, set_cache
    cache_key = f"social_velocity_{ticker}"
    cached = get_cache(cache_key)
    if cached:
        return cached

    st_data = _fetch_stocktwits(ticker)
    rd_data = _fetch_reddit(ticker)

    result = {
        "ticker": ticker,
        "stocktwits_volume": st_data.get("volume", 0),
        "stocktwits_velocity_pct": st_data.get("velocity_pct", 0),
        "stocktwits_bull_ratio": st_data.get("bull_ratio", 0.5),
        "reddit_mentions": rd_data.get("mentions", 0),
        "reddit_velocity_pct": rd_data.get("velocity_pct", 0),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

    set_cache(cache_key, result, ttl_hours=SOCIAL_CACHE_TTL_H)
    return result


def _fetch_stocktwits(ticker: str) -> dict:
    """
    Fetches last 30 messages from StockTwits for ticker.
    Computes volume and bull/bear ratio.
    No API key needed.
    """
    try:
        url = f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return {}

        messages = r.json().get("messages", [])
        if not messages:
            return {}

        volume = len(messages)

        # Bull/bear ratio from sentiment tags
        bulls = sum(1 for m in messages if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bullish")
        bears = sum(1 for m in messages if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bearish")
        tagged = bulls + bears
        bull_ratio = bulls / tagged if tagged > 0 else 0.5

        # Velocity: compare last 15 messages vs previous 15 by timestamp
        # More recent messages come first in the list
        now = datetime.now(timezone.utc)
        recent_cutoff = now - timedelta(hours=12)
        older_cutoff  = now - timedelta(hours=24)

        recent_count = 0
        older_count  = 0
        for m in messages:
            try:
                ts = datetime.fromisoformat(m["created_at"].replace("Z", "+00:00"))
                if ts >= recent_cutoff:
                    recent_count += 1
                elif ts >= older_cutoff:
                    older_count += 1
            except Exception:
                pass

        velocity_pct = 0.0
        if older_count > 0:
            velocity_pct = round((recent_count - older_count) / older_count * 100, 1)
        elif recent_count > 0:
            velocity_pct = 500.0  # all activity in recent window = maximum spike

        return {
            "volume": volume,
            "velocity_pct": velocity_pct,
            "bull_ratio": round(bull_ratio, 2),
        }
    except Exception:
        return {}


def _fetch_reddit(ticker: str) -> dict:
    """
    Searches r/wallstreetbets, r/stocks, r/investing for ticker mentions.
    Computes 12h vs 24h velocity.
    Uses Reddit's JSON API — no key needed.
    """
    subreddits = ["wallstreetbets", "stocks", "investing", "SecurityAnalysis"]
    headers = {"User-Agent": f"StockScanner/1.0 (research tool)"}

    now = datetime.now(timezone.utc)
    recent_cutoff = now - timedelta(hours=12)
    older_cutoff  = now - timedelta(hours=24)

    recent_count = 0
    older_count  = 0
    total_mentions = 0

    for sub in subreddits:
        try:
            url = f"https://www.reddit.com/r/{sub}/search.json"
            params = {
                "q": ticker,
                "restrict_sr": "true",
                "sort": "new",
                "limit": 25,
                "t": "day",
            }
            # Global rate limiter — all threads serialize through this lock
            global _reddit_last_call
            with _reddit_lock:
                elapsed = time.time() - _reddit_last_call
                if elapsed < _REDDIT_DELAY:
                    time.sleep(_REDDIT_DELAY - elapsed)
                _reddit_last_call = time.time()

            r = requests.get(url, headers=headers, params=params, timeout=10)
            if r.status_code == 429:
                break  # rate limited — stop fetching for this ticker
            if r.status_code != 200:
                continue

            posts = r.json().get("data", {}).get("children", [])
            for post in posts:
                data = post.get("data", {})
                created = data.get("created_utc", 0)
                ts = datetime.fromtimestamp(created, tz=timezone.utc)
                total_mentions += 1
                if ts >= recent_cutoff:
                    recent_count += 1
                elif ts >= older_cutoff:
                    older_count += 1
        except Exception:
            continue

    velocity_pct = 0.0
    if older_count > 0:
        velocity_pct = round((recent_count - older_count) / older_count * 100, 1)
    elif recent_count > 0:
        velocity_pct = 500.0

    return {
        "mentions": total_mentions,
        "velocity_pct": velocity_pct,
    }
