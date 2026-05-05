"""
Weekend transcript fetcher — runs Sat/Sun/Mon/Tue via Modal cron.

Fetches earnings call transcript tone for the full long-term universe (100 tickers).
FMP budget: 1 call per ticker × 100 tickers = 100 calls.
Available budget: 250 calls/day × 4 days (Sat–Tue) = 1,000 calls — well within limit.
Cached 90 days — each transcript only fetched once per quarter.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
import pytz
from dotenv import load_dotenv
load_dotenv()

PT = pytz.timezone("America/Los_Angeles")

from services.long_term_bullish_universe import get_long_bullish_hot_tickers
from services.long_term_bearish_universe import get_long_bearish_hot_tickers
from services.transcript_service import get_earnings_transcript_tone
from database.db import log_error


def run():
    start_time = datetime.now(PT)
    run_date   = start_time.strftime("%Y-%m-%d")
    print(f"[{start_time.strftime('%I:%M %p PT')}] Weekend transcript fetcher starting...")

    # Build full long-term universe
    try:
        bullish_hot = get_long_bullish_hot_tickers()
    except Exception as e:
        log_error("transcript_fetcher", f"Failed to get bullish tickers: {e}", level="WARNING")
        bullish_hot = []

    try:
        bearish_hot = get_long_bearish_hot_tickers()
    except Exception as e:
        log_error("transcript_fetcher", f"Failed to get bearish tickers: {e}", level="WARNING")
        bearish_hot = []

    # Deduplicated union
    all_tickers = sorted(set(list(bullish_hot) + list(bearish_hot)))
    print(f"  Universe: {len(all_tickers)} tickers ({len(bullish_hot)} bullish + {len(bearish_hot)} bearish, after dedup)")

    fetched = 0
    cached  = 0
    errors  = 0

    for i, ticker in enumerate(all_tickers):
        try:
            from database.db import get_cache
            if get_cache(f"transcript_tone_{ticker}") is not None:
                cached += 1
                continue

            tone = get_earnings_transcript_tone(ticker, log_api=True, run_date=run_date)
            if tone and tone.get("guidance_tone") is not None:
                fetched += 1
                q = tone.get("transcript_quarter", "?")
                print(f"  [{i+1}/{len(all_tickers)}] {ticker}: {tone['guidance_tone']} | {q} | score={tone['transcript_score']}")
            else:
                print(f"  [{i+1}/{len(all_tickers)}] {ticker}: no transcript available")

        except Exception as e:
            errors += 1
            log_error("transcript_fetcher", f"Error on {ticker}: {e}", level="WARNING")
            print(f"  Error on {ticker}: {e}")

    elapsed = (datetime.now(PT) - start_time).seconds
    summary = (f"Transcript fetch complete in {elapsed}s — "
               f"{fetched} fetched, {cached} cached, {errors} errors")
    print(summary)
    log_error("transcript_fetcher", summary, level="INFO")
    return {"fetched": fetched, "cached": cached, "errors": errors}


if __name__ == "__main__":
    run()
