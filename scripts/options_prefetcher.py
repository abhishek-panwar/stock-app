"""
Options data pre-fetcher — runs Mon–Fri at 12:00 PM PT (market open, 1h before close).

Fetches options contract recommendations for the full universe and caches them
in Supabase with a 70h TTL. The nightly scanner and UI card both read from this
cache — zero live API calls needed at scan time or on card open.

Universe:
  - Nasdaq 100 (fixed, ~100 tickers)
  - Previous night's hot tickers from hot_tickers DB table (~60-80 tickers)

For each ticker: fetches BULLISH + BEARISH × short + long = 4 cache entries.
Uses standard days_to_target: 5 for short, 60 for long.
"""
import time
from database.db import get_hot_tickers_from_db, log_error
from services.screener_service import load_nasdaq100
from services.options_recommendation import get_option_recommendation

_DAYS_SHORT = 5
_DAYS_LONG  = 60
_TTL_HOURS  = 70
_RATE_LIMIT_SLEEP = 0.5  # seconds between yfinance calls — avoid rate limiting


def run():
    print("\n=== Options Pre-fetcher ===")

    # Build universe
    nasdaq = set(load_nasdaq100())
    hot_rows = get_hot_tickers_from_db()
    hot = {r["ticker"] for r in hot_rows}
    universe = sorted(nasdaq | hot)
    print(f"  Universe: {len(nasdaq)} Nasdaq + {len(hot)} hot tickers = {len(universe)} unique")

    # Skip non-equity tickers that never have options
    _NO_OPTIONS = {"BTC-USD", "ETH-USD", "SOL-USD", "GLD", "USO"}
    universe = [t for t in universe if t not in _NO_OPTIONS]
    print(f"  After filtering crypto/commodities: {len(universe)} tickers")

    success = 0
    found   = 0
    failed  = 0

    for i, ticker in enumerate(universe, 1):
        try:
            for direction in ("BULLISH", "BEARISH"):
                for timeframe, days in (("short", _DAYS_SHORT), ("long", _DAYS_LONG)):
                    # Pass dummy entry/target — get_option_recommendation uses live spot price
                    # We pass stock_entry=1 and stock_target=1.05 just to satisfy validation;
                    # the function fetches t.fast_info.last_price as the real spot
                    rec = get_option_recommendation(
                        ticker=ticker,
                        direction=direction,
                        days_to_target=days,
                        stock_entry=1.0,
                        stock_target=1.05 if direction == "BULLISH" else 0.95,
                        timeframe=timeframe,
                        has_earnings=False,
                        _ttl_override=_TTL_HOURS,
                    )
                    if rec.get("available"):
                        found += 1
            success += 1
            if i % 20 == 0:
                print(f"  Progress: {i}/{len(universe)} tickers processed ({found} contracts found)")
            time.sleep(_RATE_LIMIT_SLEEP)
        except Exception as e:
            failed += 1
            log_error("options_prefetcher", f"Failed {ticker}: {e}", ticker=ticker, level="WARNING")

    print(f"\n  Done: {success} tickers processed, {found} contracts cached, {failed} failed")
    print(f"  Cache TTL: {_TTL_HOURS}h — valid through next weekday fetch\n")
