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
from services.analyst_service import update_scores_for_prediction

TIMEFRAME_DAYS = {"short": 5, "medium": 28, "long": 180}
WIN_THRESHOLD_PCT = 2.0
# Long-term predictions need a higher bar at expiry — a 60-day thesis up only 2% barely moved
LONG_WIN_THRESHOLD_PCT = 8.0


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

        # Check if prediction has expired.
        # Primary: use expires_on (set by Claude as days_to_target * 1.2) — respects per-prediction timing.
        # Fallback: TIMEFRAME_DAYS bucket ceiling when expires_on is missing (older predictions).
        try:
            pred_dt = datetime.fromisoformat(predicted_on.replace("Z", "+00:00"))
            days_elapsed = (now.replace(tzinfo=None) - pred_dt.replace(tzinfo=None)).days
        except Exception:
            days_elapsed = 0

        expires_on = pred.get("expires_on")
        if expires_on:
            try:
                exp_dt = datetime.fromisoformat(expires_on.replace("Z", "+00:00"))
                expired = now.replace(tzinfo=None) >= exp_dt.replace(tzinfo=None)
            except Exception:
                expired = days_elapsed >= TIMEFRAME_DAYS.get(timeframe, 5)
        else:
            expired = days_elapsed >= TIMEFRAME_DAYS.get(timeframe, 5)

        # WIN threshold at expiry: long-term needs a real move, not just 2%
        win_threshold = LONG_WIN_THRESHOLD_PCT if timeframe == "long" else WIN_THRESHOLD_PCT

        outcome = None
        closed_reason = None
        current_return_pct = round(((current - entry) / entry) * 100, 2) if entry > 0 else 0

        if direction == "BULLISH":
            if current >= target_low:
                outcome, closed_reason = "WIN", "TARGET_HIT"
            elif current <= stop_loss and stop_loss > 0:
                outcome, closed_reason = "LOSS", "STOP_LOSS"
            elif expired:
                outcome = "WIN" if current_return_pct >= win_threshold else "LOSS"
                closed_reason = "EXPIRED"
        elif direction == "BEARISH":
            if current <= target_high and target_high > 0:
                outcome, closed_reason = "WIN", "TARGET_HIT"
            elif current >= stop_loss and stop_loss > 0:
                outcome, closed_reason = "LOSS", "STOP_LOSS"
            elif expired:
                outcome = "WIN" if current_return_pct <= -win_threshold else "LOSS"
                closed_reason = "EXPIRED"
        elif expired:
            outcome = "WIN" if abs(current_return_pct) >= win_threshold else "LOSS"
            closed_reason = "EXPIRED"

        # price_at_close = real market price always (factual record of what happened).
        # return_pct = what a disciplined trader following the signal would have realized:
        #   TARGET_HIT → avg(target_low, target_high), since that's when they would have sold
        #   STOP_LOSS  → stop_loss price, since that's their defined exit
        #   EXPIRED    → actual current market price
        if outcome and entry > 0:
            close_price = current  # always the real market price
            if closed_reason == "TARGET_HIT":
                if direction == "BULLISH":
                    exit_price = (target_low + target_high) / 2 if target_low > 0 and target_high > 0 else target_low
                    return_pct = round((exit_price - entry) / entry * 100, 2)
                else:  # BEARISH
                    exit_price = (target_low + target_high) / 2 if target_low > 0 and target_high > 0 else target_high
                    return_pct = round((entry - exit_price) / entry * 100, 2)
            elif closed_reason == "STOP_LOSS":
                return_pct = round((stop_loss - entry) / entry * 100, 2) if direction == "BULLISH" \
                    else round((entry - stop_loss) / entry * 100, 2)
            else:  # EXPIRED
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

                # Update publication credibility scores
                try:
                    update_scores_for_prediction(pred["id"], outcome, return_pct, timeframe)
                except Exception as e:
                    log_error("verifier", f"Analyst score update failed {ticker}: {e}", level="WARNING")

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
