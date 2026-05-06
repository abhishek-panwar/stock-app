"""
One-time repair script: fix corrupted price_at_close values on TARGET_HIT predictions.

The old snap logic set price_at_close = target_low or target_high instead of the real
market price. This script detects corrupted records (price_at_close exactly matches
target_low or target_high) and replaces them with the actual historical close price
from yfinance on the verified_on date.

return_pct is NOT touched — it was already fixed to use avg target exit.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()

import yfinance as yf

from database.db import get_predictions, update_prediction


def get_close_on_date(ticker: str, date_str: str) -> float | None:
    """Return the daily close price for ticker on the given date (YYYY-MM-DD).
    Tries the exact date first, then the next 3 trading days (handles weekends/holidays)."""
    try:
        target_date = datetime.fromisoformat(date_str[:10]).date()
        # Download a small window around the date
        start = target_date - timedelta(days=1)
        end = target_date + timedelta(days=5)
        df = yf.download(ticker, start=start.isoformat(), end=end.isoformat(),
                         interval="1d", progress=False, auto_adjust=True)
        if df.empty:
            return None
        df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
        df.index = df.index.date
        # Try exact date first, then next available trading day
        for delta in range(4):
            check = target_date + timedelta(days=delta)
            if check in df.index:
                return float(df.loc[check, "close"])
        return None
    except Exception as e:
        print(f"  yfinance error for {ticker} on {date_str}: {e}")
        return None


def is_corrupted(p: dict) -> bool:
    """Returns True if price_at_close looks like it was snapped to target_low or target_high."""
    close = p.get("price_at_close")
    if not close:
        return False
    tl = p.get("target_low") or 0
    th = p.get("target_high") or 0
    # Match within $0.01 to handle float precision
    return (tl > 0 and abs(close - tl) < 0.01) or (th > 0 and abs(close - th) < 0.01)


def run():
    preds = get_predictions(limit=1000)
    target_hit = [
        p for p in preds
        if p.get("closed_reason") == "TARGET_HIT" and is_corrupted(p)
    ]

    print(f"Found {len(target_hit)} corrupted TARGET_HIT records to repair.")
    if not target_hit:
        print("Nothing to do.")
        return

    fixed = 0
    skipped = 0
    for p in target_hit:
        ticker = p["ticker"]
        verified_on = p.get("verified_on") or p.get("predicted_on")
        if not verified_on:
            print(f"  SKIP {ticker}: no verified_on date")
            skipped += 1
            continue

        real_close = get_close_on_date(ticker, verified_on)
        if not real_close:
            print(f"  SKIP {ticker}: could not fetch price for {verified_on[:10]}")
            skipped += 1
            continue

        old_close = p.get("price_at_close")
        update_prediction(p["id"], {"price_at_close": real_close})
        print(f"  FIXED {ticker}: {old_close} → {real_close:.2f} (verified {verified_on[:10]})")
        fixed += 1

    print(f"\nDone — {fixed} fixed, {skipped} skipped.")


if __name__ == "__main__":
    run()
