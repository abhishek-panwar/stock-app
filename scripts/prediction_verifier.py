"""
Prediction verifier — runs at 8:30 PM PT nightly.
Labels all open predictions as WIN / LOSS based on current prices.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()

import pytz
PT = pytz.timezone("America/Los_Angeles")

from database.db import get_open_predictions, update_prediction
from services.yfinance_service import get_multiple_prices
from services.telegram_service import send_stop_loss_alert, send_target_hit_alert

TIMEFRAME_DAYS = {"short": 5, "medium": 28, "long": 180}
WIN_THRESHOLD_PCT = 2.0


def run():
    now = datetime.now(PT)
    print(f"[{now.strftime('%I:%M %p PT')}] Verifier running...")

    open_preds = get_open_predictions()
    if not open_preds:
        print("No open predictions.")
        return

    tickers = list({p["ticker"] for p in open_preds})
    prices = get_multiple_prices(tickers)
    print(f"Checking {len(open_preds)} open predictions across {len(tickers)} tickers...")

    verified = 0
    for pred in open_preds:
        ticker = pred["ticker"]
        current = prices.get(ticker)
        if not current:
            continue

        entry = pred.get("price_at_prediction") or 0
        direction = pred.get("direction", "NEUTRAL")
        target_low = pred.get("target_low") or 0
        target_high = pred.get("target_high") or 0
        stop_loss = pred.get("stop_loss") or 0
        timeframe = pred.get("timeframe", "short")
        predicted_on = pred.get("predicted_on", "")

        # Check if timeframe expired
        try:
            pred_dt = datetime.fromisoformat(predicted_on.replace("Z", "+00:00"))
            days_elapsed = (now.replace(tzinfo=None) - pred_dt.replace(tzinfo=None)).days
        except Exception:
            days_elapsed = 0

        max_days = TIMEFRAME_DAYS.get(timeframe, 5)
        expired = days_elapsed >= max_days

        outcome = None
        closed_reason = None
        return_pct = round(((current - entry) / entry) * 100, 2) if entry > 0 else 0

        if direction == "BULLISH":
            if current >= target_low:
                outcome, closed_reason = "WIN", "TARGET_HIT"
            elif current <= stop_loss and stop_loss > 0:
                outcome, closed_reason = "LOSS", "STOP_LOSS"
            elif expired:
                outcome = "WIN" if return_pct >= WIN_THRESHOLD_PCT else "LOSS"
                closed_reason = "EXPIRED"
        elif direction == "BEARISH":
            if current <= target_high and target_high > 0:
                outcome, closed_reason = "WIN", "TARGET_HIT"
            elif current >= stop_loss and stop_loss > 0:
                outcome, closed_reason = "LOSS", "STOP_LOSS"
            elif expired:
                outcome = "WIN" if return_pct <= -WIN_THRESHOLD_PCT else "LOSS"
                closed_reason = "EXPIRED"
        elif expired:
            outcome = "WIN" if abs(return_pct) >= WIN_THRESHOLD_PCT else "LOSS"
            closed_reason = "EXPIRED"

        if outcome:
            try:
                update_prediction(pred["id"], {
                    "outcome": outcome,
                    "closed_reason": closed_reason,
                    "price_at_close": current,
                    "return_pct": return_pct,
                    "verified_on": now.isoformat(),
                })
                verified += 1
                print(f"  {ticker} {timeframe}: {outcome} ({return_pct:+.2f}%) — {closed_reason}")

                if closed_reason == "STOP_LOSS":
                    send_stop_loss_alert(ticker, entry, current, abs(return_pct))
                elif closed_reason == "TARGET_HIT":
                    send_target_hit_alert(ticker, entry, current, return_pct)
            except Exception as e:
                print(f"  Error updating {ticker}: {e}")

    print(f"Verified {verified} predictions.")


if __name__ == "__main__":
    run()
