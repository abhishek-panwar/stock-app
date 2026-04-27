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


def send_stop_loss_alert(ticker: str, entry: float, current: float, loss_pct: float) -> bool:
    msg = (
        f"⚠️ <b>{ticker} stop loss triggered</b>\n"
        f"Entry: ${entry:.2f} → Current: ${current:.2f}\n"
        f"Loss: {loss_pct:.2f}% | Trade closed as LOSS\n"
        f"Time: {_now_pt()}"
    )
    return _send(msg)


def send_target_hit_alert(ticker: str, entry: float, current: float, return_pct: float) -> bool:
    msg = (
        f"✅ <b>{ticker} hit target</b>\n"
        f"Entry: ${entry:.2f} → Current: ${current:.2f}\n"
        f"Return: +{return_pct:.2f}% | Consider taking profit\n"
        f"Time: {_now_pt()}"
    )
    return _send(msg)


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


def send_nightly_summary(picks: dict, open_trades: int, winning: int,
                         losing: int, neutral: int, universe_total: int,
                         nasdaq_count: int, hot_count: int, overlap: int) -> bool:
    now = _now_pt()
    short = [p["ticker"] for p in picks.get("short", [])]
    medium = [p["ticker"] for p in picks.get("medium", [])]
    long_ = [p["ticker"] for p in picks.get("long", [])]
    top = picks.get("top_pick")

    universe_line = f"Universe tonight: {universe_total} stocks ({nasdaq_count} Nasdaq + {hot_count} hot → {overlap} overlap)"

    top_section = ""
    if top:
        top_section = (
            f"\n\nTop pick:\n"
            f"<b>{top['ticker']}</b> — {top['timeframe'].capitalize()}-term {top['direction']}\n"
            f"Confidence: {top['confidence']}%\n"
            f"Buy window: {top.get('buy_window', 'N/A')} tomorrow\n"
            f"Buy: ${top.get('buy_low', 0):.2f}–${top.get('buy_high', 0):.2f} | "
            f"Target: ${top.get('target_low', 0):.2f}–${top.get('target_high', 0):.2f} | "
            f"Stop: ${top.get('stop_loss', 0):.2f}"
        )
        if top.get("all_timeframes_agree"):
            top_section += "\nAll timeframes aligned 🎯"

    msg = (
        f"📊 <b>Tonight's Top Picks — {now}</b>\n\n"
        f"{universe_line}\n\n"
        f"⚡ Short-term: {', '.join(short) or 'None'}\n"
        f"📈 Medium-term: {', '.join(medium) or 'None'}\n"
        f"🌱 Long-term: {', '.join(long_) or 'None'}"
        f"{top_section}\n\n"
        f"Open trades: {open_trades} | Winning: {winning} | Losing: {losing} | Neutral: {neutral}"
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
