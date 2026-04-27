"""
Feedback engine — runs at 8:45 PM PT nightly after verifier.
Computes accuracy stats and writes to Supabase for Claude prompt injection.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()

import pytz
PT = pytz.timezone("America/Los_Angeles")

from database.db import get_predictions, upsert_accuracy_stat

RELIABLE_THRESHOLD = 15


def run():
    print("Feedback engine running...")
    since = (datetime.now(PT) - timedelta(days=60)).isoformat()
    all_preds = get_predictions(limit=1000)
    closed = [p for p in all_preds if p.get("outcome") in ("WIN", "LOSS")]

    if not closed:
        print("No closed predictions yet.")
        return

    # Global by timeframe
    for tf in ["short", "medium", "long"]:
        tf_preds = [p for p in closed if p.get("timeframe") == tf]
        _write_stat("all_signals", None, tf, tf_preds)

    # By ticker + timeframe
    tickers = {p["ticker"] for p in closed}
    for ticker in tickers:
        for tf in ["short", "medium", "long"]:
            tp = [p for p in closed if p["ticker"] == ticker and p.get("timeframe") == tf]
            _write_stat("all_signals", ticker, tf, tp)

    # Global overall
    _write_stat("all_signals", None, "all", closed)

    print(f"Accuracy stats updated for {len(tickers)} tickers.")


def _write_stat(combo: str, ticker, timeframe: str, preds: list):
    if not preds:
        return
    wins = sum(1 for p in preds if p.get("outcome") == "WIN")
    returns = [p.get("return_pct") or 0 for p in preds]
    avg_return = sum(returns) / len(returns) if returns else 0
    win_rate = wins / len(preds)
    try:
        upsert_accuracy_stat({
            "signal_combo": combo,
            "ticker": ticker,
            "timeframe": timeframe,
            "total_trades": len(preds),
            "wins": wins,
            "win_rate": round(win_rate, 4),
            "avg_return_pct": round(avg_return, 2),
            "last_updated": datetime.now(pytz.timezone("America/Los_Angeles")).isoformat(),
            "sample_reliable": len(preds) >= RELIABLE_THRESHOLD,
        })
    except Exception as e:
        print(f"  Stat write error ({combo}/{ticker}/{timeframe}): {e}")


if __name__ == "__main__":
    run()
