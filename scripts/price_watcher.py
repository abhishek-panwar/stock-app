"""
Price watcher — runs every 5 minutes during market hours (6:30 AM – 1:00 PM PT).
Only checks open predictions. Fires stop loss / target hit alerts immediately.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

import pytz
PT = pytz.timezone("America/Los_Angeles")

from database.db import get_open_predictions, update_prediction
from services.yfinance_service import get_multiple_prices
from services.telegram_service import send_stop_loss_alert, send_target_hit_alert


def run():
    now = datetime.now(PT)
    open_preds = get_open_predictions()
    if not open_preds:
        return

    tickers = list({p["ticker"] for p in open_preds})
    prices = get_multiple_prices(tickers)

    for pred in open_preds:
        ticker = pred["ticker"]
        current = prices.get(ticker)
        if not current:
            continue

        entry = pred.get("price_at_prediction") or 0
        direction = pred.get("direction", "NEUTRAL")
        target_low = pred.get("target_low") or 0
        stop_loss = pred.get("stop_loss") or 0
        return_pct = round(((current - entry) / entry) * 100, 2) if entry > 0 else 0

        hit_target = direction == "BULLISH" and current >= target_low
        hit_stop = direction == "BULLISH" and stop_loss > 0 and current <= stop_loss
        hit_target_short = direction == "BEARISH" and current <= (pred.get("target_high") or 0)
        hit_stop_short = direction == "BEARISH" and stop_loss > 0 and current >= stop_loss

        if hit_target or hit_target_short:
            try:
                update_prediction(pred["id"], {
                    "outcome": "WIN",
                    "closed_reason": "TARGET_HIT",
                    "price_at_close": current,
                    "return_pct": return_pct,
                    "verified_on": now.isoformat(),
                })
                send_target_hit_alert(ticker, entry, current, return_pct,
                                      predicted_on=pred.get("predicted_on", ""),
                                      target_low=pred.get("target_low", 0),
                                      direction=pred.get("direction", ""))
            except Exception:
                pass

        elif hit_stop or hit_stop_short:
            try:
                update_prediction(pred["id"], {
                    "outcome": "LOSS",
                    "closed_reason": "STOP_LOSS",
                    "price_at_close": current,
                    "return_pct": return_pct,
                    "verified_on": now.isoformat(),
                })
                send_stop_loss_alert(ticker, entry, current, abs(return_pct),
                                     predicted_on=pred.get("predicted_on", ""),
                                     stop_loss=pred.get("stop_loss", 0),
                                     direction=pred.get("direction", ""))
            except Exception:
                pass


if __name__ == "__main__":
    run()
