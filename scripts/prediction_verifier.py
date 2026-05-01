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

from database.db import get_open_predictions, update_prediction, log_error
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
        log_error("verifier", "No open predictions to verify", level="INFO")
        return

    tickers = list({p["ticker"] for p in open_preds})
    try:
        prices = get_multiple_prices(tickers)
    except Exception as e:
        log_error("verifier", f"Failed to fetch prices: {e}", detail=str(e), level="ERROR")
        raise
    print(f"Checking {len(open_preds)} open predictions across {len(tickers)} tickers...")

    verified = 0
    for pred in open_preds:
        ticker = pred["ticker"]
        current = prices.get(ticker)
        if not current:
            continue

        buy_low  = pred.get("buy_range_low") or 0
        buy_high = pred.get("buy_range_high") or 0
        entry = (buy_low + buy_high) / 2 if buy_low > 0 and buy_high > 0 else (pred.get("price_at_prediction") or 0)
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
        current_return_pct = round(((current - entry) / entry) * 100, 2) if entry > 0 else 0

        if direction == "BULLISH":
            if current >= target_low:
                outcome, closed_reason = "WIN", "TARGET_HIT"
            elif current <= stop_loss and stop_loss > 0:
                outcome, closed_reason = "LOSS", "STOP_LOSS"
            elif expired:
                outcome = "WIN" if current_return_pct >= WIN_THRESHOLD_PCT else "LOSS"
                closed_reason = "EXPIRED"
        elif direction == "BEARISH":
            if current <= target_high and target_high > 0:
                outcome, closed_reason = "WIN", "TARGET_HIT"
            elif current >= stop_loss and stop_loss > 0:
                outcome, closed_reason = "LOSS", "STOP_LOSS"
            elif expired:
                outcome = "WIN" if current_return_pct <= -WIN_THRESHOLD_PCT else "LOSS"
                closed_reason = "EXPIRED"
        elif expired:
            outcome = "WIN" if abs(current_return_pct) >= WIN_THRESHOLD_PCT else "LOSS"
            closed_reason = "EXPIRED"

        # Compute close price and return_pct at the level actually hit.
        # BULLISH TARGET_HIT: use target_high if current overshot it, else target_low.
        # BEARISH TARGET_HIT: use target_low if current overshot it, else target_high.
        # STOP_LOSS: always use stop_loss price.
        # EXPIRED: use actual current market price.
        if outcome and entry > 0:
            if closed_reason == "TARGET_HIT":
                if direction == "BULLISH":
                    close_price = target_high if (target_high > 0 and current >= target_high) else target_low
                    return_pct = round((close_price - entry) / entry * 100, 2)
                else:  # BEARISH
                    close_price = target_low if (target_low > 0 and current <= target_low) else target_high
                    return_pct = round((entry - close_price) / entry * 100, 2)
            elif closed_reason == "STOP_LOSS":
                close_price = stop_loss
                return_pct = round((stop_loss - entry) / entry * 100, 2) if direction == "BULLISH" \
                    else round((entry - stop_loss) / entry * 100, 2)
            else:  # EXPIRED
                close_price = current
                return_pct = round((current - entry) / entry * 100, 2) if direction != "BEARISH" \
                    else round((entry - current) / entry * 100, 2)
        else:
            close_price = current
            return_pct = 0

        if outcome:
            try:
                price_at_close = close_price

                update_prediction(pred["id"], {
                    "outcome": outcome,
                    "closed_reason": closed_reason,
                    "price_at_close": price_at_close,
                    "return_pct": return_pct,
                    "verified_on": now.isoformat(),
                })
                verified += 1
                print(f"  {ticker} {timeframe}: {outcome} ({return_pct:+.2f}%) — {closed_reason}")

                if closed_reason == "STOP_LOSS":
                    ok = send_stop_loss_alert(ticker, entry, price_at_close, abs(return_pct))
                    if not ok:
                        log_error("telegram", f"Stop loss alert failed for {ticker}", ticker=ticker, level="WARNING")
                elif closed_reason == "TARGET_HIT":
                    ok = send_target_hit_alert(ticker, entry, price_at_close, return_pct)
                    if not ok:
                        log_error("telegram", f"Target hit alert failed for {ticker}", ticker=ticker, level="WARNING")
            except Exception as e:
                msg = f"Error updating {ticker}: {e}"
                print(f"  {msg}")
                log_error("verifier", msg, detail=str(e), ticker=ticker, level="ERROR")

    summary = f"Verified {verified} predictions."
    print(summary)
    log_error("verifier", summary, level="INFO")


if __name__ == "__main__":
    run()
