import os
import requests
from datetime import datetime
import pytz
from dotenv import load_dotenv

load_dotenv()

PT = pytz.timezone("America/Los_Angeles")

def _now_pt() -> str:
    return datetime.now(PT).strftime("%b %d, %Y  %I:%M %p PT")

def _send(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
        return r.status_code == 200
    except Exception:
        return False


def send_stop_loss_alert(ticker: str, entry: float, current: float, loss_pct: float,
                         predicted_on: str = "", stop_loss: float = 0) -> bool:
    age_str = _age_str(predicted_on)
    loss_amt = abs(current - entry)
    stop_str = f"  |  Stop: ${stop_loss:.2f}" if stop_loss else ""
    msg = (
        f"🔴 <b>{ticker} — Stop Loss Triggered</b>\n"
        f"{age_str}\n"
        f"Entry: ${entry:.2f} → Close: ${current:.2f}{stop_str}\n"
        f"Loss: <b>-{loss_pct:.2f}%</b>  (${loss_amt:.2f} per share)\n"
        f"Trade closed as LOSS  ·  {_now_pt()}"
    )
    return _send(msg)


def send_target_hit_alert(ticker: str, entry: float, current: float, return_pct: float,
                          predicted_on: str = "", target_low: float = 0) -> bool:
    age_str = _age_str(predicted_on)
    profit_amt = abs(current - entry)
    target_str = f"  |  Target: ${target_low:.2f}" if target_low else ""
    msg = (
        f"🟢 <b>{ticker} — Target Hit!</b>\n"
        f"{age_str}\n"
        f"Entry: ${entry:.2f} → Close: ${current:.2f}{target_str}\n"
        f"Profit: <b>+{return_pct:.2f}%</b>  (${profit_amt:.2f} per share)\n"
        f"Trade closed as WIN  ·  {_now_pt()}"
    )
    return _send(msg)


def _age_str(predicted_on: str) -> str:
    if not predicted_on:
        return "Prediction age: unknown"
    try:
        pred_dt = datetime.fromisoformat(predicted_on.replace("Z", "+00:00")).astimezone(PT)
        age = (datetime.now(PT).date() - pred_dt.date()).days
        if age == 0:
            return "📅 Today's prediction"
        if age == 1:
            return "📅 Yesterday's prediction"
        return f"📅 {age}-day-old prediction"
    except Exception:
        return "Prediction age: unknown"


def send_new_prediction(ticker: str, timeframe: str, direction: str,
                        confidence: int, score: int, buy_low: float, buy_high: float,
                        current_price: float, target_low: float, target_high: float,
                        stop_loss: float, buy_window: str, all_timeframes_agree: bool = False) -> bool:
    in_range = buy_low <= current_price <= buy_high
    range_note = "✅ now in range" if in_range else f"⚡ now: ${current_price:.2f}"
    agree_note = "\nAll timeframes aligned 🎯" if all_timeframes_agree else ""
    msg = (
        f"📊 <b>{ticker} — {timeframe.capitalize()}-term {direction}</b>\n"
        f"Confidence: {confidence}% | Score: {score}/100\n"
        f"Buy: ${buy_low:.2f} – ${buy_high:.2f} ({range_note})\n"
        f"Target: ${target_low:.2f} – ${target_high:.2f}\n"
        f"Stop Loss: ${stop_loss:.2f}\n"
        f"Best entry window: {buy_window}{agree_note}"
    )
    return _send(msg)


def send_rsi_alert(ticker: str, rsi: float, current_price: float) -> bool:
    condition = "Overbought" if rsi > 70 else "Oversold"
    emoji = "🚨"
    msg = (
        f"{emoji} <b>{ticker} RSI Alert</b>\n"
        f"RSI hit {rsi:.1f} — {condition} territory\n"
        f"Current: ${current_price:.2f} | Watch for reversal\n"
        f"Time: {_now_pt()}"
    )
    return _send(msg)


def send_sentiment_spike(ticker: str, article_count: int, current_price: float) -> bool:
    msg = (
        f"📰 <b>{ticker} Sentiment Alert</b>\n"
        f"Negative sentiment spike detected\n"
        f"{article_count} negative articles in last 2 hours\n"
        f"Current: ${current_price:.2f} | Monitor closely"
    )
    return _send(msg)


def _prediction_line(p: dict) -> str:
    ticker    = p.get("ticker", "—")
    company   = p.get("company_name") or ticker
    direction = p.get("direction", "NEUTRAL")
    confidence = p.get("confidence", 0)
    entry     = p.get("price_at_prediction") or 0
    target    = p.get("target_low") or 0
    stop      = p.get("stop_loss") or 0
    buy_low   = p.get("buy_range_low") or p.get("buy_low") or entry
    buy_high  = p.get("buy_range_high") or p.get("buy_high") or entry
    days      = p.get("days_to_target") or "?"
    buy_win   = p.get("buy_window") or "—"
    profit_pct = ((target - entry) / entry * 100) if entry > 0 and target > 0 else 0

    dir_icon  = "🟢 BULLISH" if direction == "BULLISH" else "🔴 BEARISH" if direction == "BEARISH" else "⚪ NEUTRAL"
    profit_str = f"+{profit_pct:.1f}%" if profit_pct >= 0 else f"{profit_pct:.1f}%"

    # Buy date = tomorrow in PT
    tomorrow = datetime.now(PT).strftime("%b %d")

    return (
        f"<b>{ticker}</b> — {company}\n"
        f"  {dir_icon}  |  {profit_str} profit  |  {confidence}% conf\n"
        f"  Buy: ${buy_low:.2f}–${buy_high:.2f}  →  Target: ${target:.2f}  |  Stop: ${stop:.2f}\n"
        f"  Window: {buy_win} on {tomorrow}  |  Hold ~{days}d"
    )


def send_nightly_summary(picks: dict, open_trades: int, winning: int,
                         losing: int, neutral: int, universe_total: int,
                         nasdaq_count: int, hot_count: int, overlap: int) -> bool:
    now = _now_pt()

    # Collect all predictions and sort by absolute profit descending
    all_preds = (
        picks.get("short", []) +
        picks.get("medium", []) +
        picks.get("long", [])
    )
    seen = set()
    unique_preds = []
    for p in all_preds:
        if p["ticker"] not in seen:
            seen.add(p["ticker"])
            unique_preds.append(p)

    def _profit(p):
        e = p.get("price_at_prediction") or 0
        t = p.get("target_low") or 0
        return abs((t - e) / e * 100) if e > 0 and t > 0 else 0

    unique_preds.sort(key=_profit, reverse=True)

    universe_line = (
        f"🔭 Universe: <b>{universe_total} stocks</b> "
        f"({nasdaq_count} core + {hot_count} hot → {overlap} overlap)"
    )

    pred_lines = "\n\n".join(_prediction_line(p) for p in unique_preds) if unique_preds else "No predictions tonight."

    tf_summary = (
        f"⚡ Short: {len(picks.get('short',[]))}  "
        f"📈 Mid: {len(picks.get('medium',[]))}  "
        f"🌱 Long: {len(picks.get('long',[]))}"
    )

    msg = (
        f"📊 <b>Tonight's Picks — {now}</b>\n\n"
        f"{universe_line}\n"
        f"{tf_summary}\n\n"
        f"{pred_lines}\n\n"
        f"Open trades: {open_trades} | ✅ {winning} winning | ❌ {losing} losing | ➖ {neutral} neutral"
    )
    return _send(msg)


def send_morning_reminder(top_picks: list, open_count: int, system_status: str) -> bool:
    now_pt = datetime.now(PT)
    date_str = now_pt.strftime("%b %d, %Y")
    top_line = ""
    if top_picks:
        p = top_picks[0]
        top_line = f"\nTop entry today: <b>{p['ticker']}</b>  Buy: ${p.get('buy_low',0):.2f}–${p.get('buy_high',0):.2f}  Window: {p.get('buy_window','N/A')}"
    msg = (
        f"📅 <b>Market opens in 10 minutes — {date_str}</b>\n"
        f"Active predictions: {open_count}"
        f"{top_line}\n"
        f"System: {system_status}"
    )
    return _send(msg)


def send_market_close_summary(open_trades: int, winning: int, losing: int,
                               neutral: int, best_ticker: str, best_pct: float) -> bool:
    now_pt = datetime.now(PT)
    msg = (
        f"🔔 <b>Market closed — {now_pt.strftime('%b %d, %Y')}  1:00 PM PT</b>\n"
        f"Open trades: {open_trades} | {winning} winning | {losing} losing | {neutral} neutral\n"
        f"Best performer: {best_ticker} {best_pct:+.1f}%\n"
        f"Next scan: tonight 8:00 PM PT"
    )
    return _send(msg)


def send_health_alert(alerts: list) -> bool:
    if not alerts:
        return True
    lines = "\n".join(alerts)
    msg = f"🔧 <b>SYSTEM HEALTH ALERT — {_now_pt()}</b>\n\n{lines}"
    return _send(msg)


def send_test_message() -> bool:
    return _send(f"✅ Stock Analysis Bot connected successfully!\nTime: {_now_pt()}")
