"""
Midweek FMP pre-fetch — runs Wed 9:00 PM PT and Thu 9:00 PM PT via Modal cron.

Splits the Nasdaq 100 list alphabetically across two nights to stay within
the FMP free tier (250 calls/day) while fetching 4 endpoints per ticker:
  key-metrics-ttm + income-statement + ratios + analyst-estimates = 4 calls

  Wednesday: first 50 tickers (sorted A–M) × 4 calls = 200 calls
  Thursday:  last  50 tickers (sorted N–Z) × 4 calls = 200 calls

Cache TTL: 72h — Wednesday cache stays valid through Friday's 7:30 PM scan (~46h gap).
Thursday cache stays valid through Friday (~22h gap).

The split is deterministic: Nasdaq 100 list sorted alphabetically, sliced [:50] / [50:].
Same tickers on the same night every week regardless of list order in watchlist.json.
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
FMP_CACHE_TTL_H = 72   # 3 days — covers Wed → Friday scan (~46h)
SKIP_IF_YOUNGER_H = 20  # skip ticker if cache is fresher than this


def _get_nasdaq100_tickers() -> list[str]:
    """Load and sort Nasdaq 100 tickers from config/watchlist.json."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "watchlist.json")
    try:
        with open(path) as f:
            data = json.load(f)
        tickers = data.get("nasdaq100", [])
        return sorted(tickers)  # deterministic alphabetical sort
    except Exception as e:
        print(f"  Could not load watchlist.json: {e}")
        return []


def run():
    now_pt  = datetime.now(PT)
    weekday = now_pt.weekday()  # 2=Wed, 3=Thu

    all_tickers = _get_nasdaq100_tickers()
    if not all_tickers:
        print("  No Nasdaq 100 tickers found — skipping")
        return

    # Split: Wednesday = first 50, Thursday = last 50
    midpoint = len(all_tickers) // 2
    if weekday == 2:
        tickers = all_tickers[:midpoint]
        label   = "Wednesday (first half A–M)"
    elif weekday == 3:
        tickers = all_tickers[midpoint:]
        label   = "Thursday (second half N–Z)"
    else:
        # If run manually on another day, fetch all
        tickers = all_tickers
        label   = f"{now_pt.strftime('%A')} (manual run — all tickers)"

    print(f"Midweek FMP pre-fetch — {now_pt.strftime('%A %b %d %Y %I:%M %p PT')}")
    print(f"  Slot: {label} — {len(tickers)} tickers to process")

    api_key = os.environ.get("FMP_API_KEY", "")
    if not api_key:
        print("  FMP_API_KEY not set — skipping pre-fetch")
        return

    fetched   = 0
    skipped   = 0
    fmp_calls = 0
    errors    = 0

    for ticker in tickers:
        cache_key = f"fundamentals_fmp_{ticker}"
        cached = get_cache(cache_key)
        if cached:
            fetched_at = cached.get("fetched_at", "")
            if fetched_at:
                try:
                    age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(fetched_at)).total_seconds() / 3600
                    if age_h < SKIP_IF_YOUNGER_H:
                        skipped += 1
                        continue
                except Exception:
                    pass

        try:
            data = get_fundamentals(ticker)
            if data and not data.get("error"):
                set_cache(cache_key, data, ttl_hours=FMP_CACHE_TTL_H)
                fetched   += 1
                fmp_calls += 4  # key-metrics + income-statement + ratios + analyst-estimates
            else:
                errors += 1
        except Exception as e:
            errors += 1
            print(f"  Error {ticker}: {e}")

        if fetched % 10 == 0 and fetched > 0:
            print(f"  Progress: {fetched}/{len(tickers)} fetched, ~{fmp_calls} FMP calls used")

    print(f"  Done — {fetched} fetched, {skipped} already fresh, {errors} errors")
    print(f"  FMP calls used: ~{fmp_calls} / 250 (free tier limit)")


if __name__ == "__main__":
    run()
