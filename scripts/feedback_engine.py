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

    # Split by formula family: long-term vs short-term
    long_fvs  = {"long_bullish_v2.0", "long_bearish_v2.0"}
    long_preds  = [p for p in closed if p.get("formula_version", "") in long_fvs]
    short_preds = [p for p in closed if p.get("formula_version", "") not in long_fvs]

    # Global by timeframe — split by formula family so stats don't bleed across pipelines
    for tf in ["short", "medium", "long"]:
        _write_stat("all_signals", None, tf, [p for p in short_preds if p.get("timeframe") == tf])
        _write_stat("all_signals_long", None, tf, [p for p in long_preds if p.get("timeframe") == tf])

    # By ticker + timeframe
    tickers = {p["ticker"] for p in closed}
    for ticker in tickers:
        for tf in ["short", "medium", "long"]:
            tp_short = [p for p in short_preds if p["ticker"] == ticker and p.get("timeframe") == tf]
            tp_long  = [p for p in long_preds  if p["ticker"] == ticker and p.get("timeframe") == tf]
            _write_stat("all_signals", ticker, tf, tp_short)
            _write_stat("all_signals_long", ticker, tf, tp_long)

    # Global overall per family
    _write_stat("all_signals", None, "all", short_preds)
    _write_stat("all_signals_long", None, "all", long_preds)

    print(f"Accuracy stats updated for {len(tickers)} tickers ({len(long_preds)} long-term, {len(short_preds)} short-term closed).")


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
