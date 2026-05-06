"""
Price watcher — runs every 5 minutes during market hours (6:30 AM – 1:00 PM PT).

Cadence split to conserve yfinance API calls:
  - Tracked predictions: every run (every 5 min) — live signals need fresh data
  - Non-tracked predictions: every 30 min only — target/stop checks don't need 5-min precision
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

    tracked_preds    = [p for p in open_preds if p.get("is_tracked")]
    non_tracked_preds = [p for p in open_preds if not p.get("is_tracked")]

    # Non-tracked: only check at the :00 and :30 marks (every 30 min)
    run_non_tracked = now.minute < 5 or (30 <= now.minute < 35)

    # Fetch prices only for the predictions we'll actually process this run
    tickers_to_fetch = {p["ticker"] for p in tracked_preds}
    if run_non_tracked:
        tickers_to_fetch |= {p["ticker"] for p in non_tracked_preds}

    if not tickers_to_fetch:
        return

    prices = get_multiple_prices(list(tickers_to_fetch))

    preds_to_process = tracked_preds + (non_tracked_preds if run_non_tracked else [])

    for pred in preds_to_process:
        ticker = pred["ticker"]
        current = prices.get(ticker)
        if not current:
            continue

        is_tracked = pred.get("is_tracked", False)

        buy_low  = pred.get("buy_range_low") or 0
        buy_high = pred.get("buy_range_high") or 0
        entry = (buy_low + buy_high) / 2 if buy_low > 0 and buy_high > 0 else (pred.get("price_at_prediction") or 0)
        direction   = pred.get("direction", "NEUTRAL")
        target_low  = pred.get("target_low") or 0
        target_high = pred.get("target_high") or 0
        stop_loss   = pred.get("stop_loss") or 0

        hit_target       = direction == "BULLISH" and current >= target_low
        hit_stop         = direction == "BULLISH" and stop_loss > 0 and current <= stop_loss
        hit_target_short = direction == "BEARISH" and current <= (target_high or 0)
        hit_stop_short   = direction == "BEARISH" and stop_loss > 0 and current >= stop_loss

        if is_tracked:
            # Tracked predictions: update live signal every 5 min (matches cron cadence).
            # Never auto-close — user owns the exit decision.
            timeframe = pred.get("timeframe", "short")
            try:
                from indicators.intraday_technicals import compute_tracking_signal
                signals = compute_tracking_signal(
                    ticker, timeframe, entry, stop_loss,
                    target_low, target_high, direction
                )
                if signals:
                    # Price-level overrides: if stop or target clearly breached, force SELL signal
                    if hit_stop or hit_stop_short:
                        signal = "SELL"
                        reason = f"Stop loss ${stop_loss:.2f} breached — price ${current:.2f}"
                    elif hit_target or hit_target_short:
                        signal = "SELL"
                        reason = f"Target reached — price ${current:.2f} hit target zone"
                    else:
                        signal = signals["signal"]
                        reason = signals["reason"]

                    # Track peak price for context
                    prev_peak = pred.get("live_peak_price") or 0
                    new_peak = max(prev_peak, current) if direction == "BULLISH" else min(prev_peak or current, current)

                    update_prediction(pred["id"], {
                        "live_signal":            signal,
                        "live_signal_reason":     reason,
                        "live_signal_updated_at": now.isoformat(),
                        "live_current_price":     current,
                        "live_peak_price":        new_peak,
                    })
            except Exception:
                pass
            continue  # skip auto-close logic entirely for tracked predictions

        # Non-tracked predictions: normal auto-close logic
        if hit_target or hit_target_short:
            if direction == "BULLISH":
                exit_price = (target_low + target_high) / 2 if target_low > 0 and target_high > 0 else target_low
                return_pct = round((exit_price - entry) / entry * 100, 2) if entry > 0 else 0
            else:
                exit_price = (target_low + target_high) / 2 if target_low > 0 and target_high > 0 else target_high
                return_pct = round((entry - exit_price) / entry * 100, 2) if entry > 0 else 0
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
            if direction == "BULLISH":
                return_pct = round((stop_loss - entry) / entry * 100, 2) if entry > 0 else 0
            else:
                return_pct = round((entry - stop_loss) / entry * 100, 2) if entry > 0 else 0
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
