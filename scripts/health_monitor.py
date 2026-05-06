"""
Health monitor — runs at 6:00 AM PT daily.
Checks all components. Only sends Telegram if something needs attention.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()

import pytz, requests
PT = pytz.timezone("America/Los_Angeles")

from services.telegram_service import send_health_alert


def run():
    alerts = []
    ok_lines = []

    # ── Anthropic API ─────────────────────────────────────────────────────────
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        # Just test connectivity — a lightweight call
        client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=5,
            messages=[{"role": "user", "content": "ping"}]
        )
        ok_lines.append("✅ Claude API: OK")
    except Exception as e:
        alerts.append(f"❌ Claude API error: {str(e)[:80]}")

    # ── Supabase ──────────────────────────────────────────────────────────────
    try:
        from database.db import get_scan_logs
        logs = get_scan_logs(limit=1)
        ok_lines.append("✅ Supabase: OK")

        # Check if nightly scan ran last night
        if logs:
            last_scan = datetime.fromisoformat(logs[0]["timestamp"].replace("Z", "+00:00"))
            hours_since = (datetime.now(pytz.utc) - last_scan.replace(tzinfo=pytz.utc)).total_seconds() / 3600
            if hours_since > 50:
                alerts.append(f"⚠️ Nightly scan hasn't run in {hours_since:.0f} hours — check Modal dashboard (scanner runs on Modal, not GitHub Actions)")
    except Exception as e:
        alerts.append(f"❌ Supabase error: {str(e)[:80]}")

    # ── Finnhub ───────────────────────────────────────────────────────────────
    try:
        import finnhub
        client = finnhub.Client(api_key=os.environ["FINNHUB_API_KEY"])
        client.quote("AAPL")
        ok_lines.append("✅ Finnhub: OK")
    except Exception as e:
        alerts.append(f"❌ Finnhub error: {str(e)[:80]}")

    # ── yfinance ──────────────────────────────────────────────────────────────
    try:
        import yfinance as yf
        t = yf.Ticker("AAPL")
        _ = t.fast_info.last_price
        ok_lines.append("✅ yfinance: OK")
    except Exception as e:
        alerts.append(f"⚠️ yfinance issue: {str(e)[:80]}")

    if alerts:
        send_health_alert(alerts + [""] + ok_lines)
    else:
        print("All systems healthy — no alert sent.")


if __name__ == "__main__":
    run()
