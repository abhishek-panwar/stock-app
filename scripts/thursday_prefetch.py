"""
Thursday FMP pre-fetch — runs Thu 9:00 PM PT via Modal cron.

Pre-fetches FMP fundamentals for the static Nasdaq 100 universe so that Friday's
long-term scanner hits the cache instead of spending FMP quota.

Call budget:
  Nasdaq 100 tickers × 2 FMP calls each = ~200 calls (within free tier 250/day)
  Friday scanner then only needs FMP for cache-miss dynamic tickers (~20-40 calls)

Cache TTL: 48h — Thursday pre-fetch stays valid through Friday scan (~22h gap).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import json
from datetime import datetime, timezone
import pytz

from database.db import get_cache, set_cache
from services.fmp_service import get_fundamentals

PT = pytz.timezone("America/Los_Angeles")
FMP_CACHE_TTL_H = 48


def _get_nasdaq100_tickers() -> list[str]:
    """Load Nasdaq 100 ticker list from config/watchlist.json."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "watchlist.json")
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get("nasdaq100", [])
    except Exception as e:
        print(f"  Could not load watchlist.json: {e}")
        return []


def run():
    now_pt = datetime.now(PT)
    print(f"Thursday FMP pre-fetch — {now_pt.strftime('%A %b %d %Y %I:%M %p PT')}")

    api_key = os.environ.get("FMP_API_KEY", "")
    if not api_key:
        print("  FMP_API_KEY not set — skipping pre-fetch")
        return

    tickers = _get_nasdaq100_tickers()
    if not tickers:
        print("  No Nasdaq 100 tickers found — skipping")
        return

    print(f"  {len(tickers)} Nasdaq 100 tickers to pre-fetch")

    fetched   = 0
    skipped   = 0
    fmp_calls = 0
    errors    = 0

    for ticker in tickers:
        cache_key = f"fundamentals_fmp_{ticker}"
        cached = get_cache(cache_key)
        if cached:
            # Only skip if fresh (< 24h old) — always refresh on Thursday pre-fetch
            fetched_at = cached.get("fetched_at", "")
            if fetched_at:
                try:
                    age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(fetched_at)).total_seconds() / 3600
                    if age_h < 24:
                        skipped += 1
                        continue
                except Exception:
                    pass

        try:
            data = get_fundamentals(ticker)
            if data and not data.get("error"):
                set_cache(cache_key, data, ttl_hours=FMP_CACHE_TTL_H)
                fetched += 1
                fmp_calls += 2  # key-metrics + income-statement
            else:
                errors += 1
        except Exception as e:
            errors += 1
            print(f"  Error {ticker}: {e}")

        if fetched % 20 == 0 and fetched > 0:
            print(f"  Progress: {fetched}/{len(tickers)} fetched, {fmp_calls} FMP calls used")

    print(f"  Done — {fetched} fetched, {skipped} already fresh, {errors} errors")
    print(f"  FMP calls used: {fmp_calls} / 250 (free tier)")


if __name__ == "__main__":
    run()
