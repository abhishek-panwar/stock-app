"""
Opportunity analyzer — runs weekly Sunday 8:00 PM PT.
Finds missed opportunities in shadow portfolio, sends suggestions to System Evolution queue.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()

import pytz
PT = pytz.timezone("America/Los_Angeles")

from database.db import get_scan_logs, insert_shadow_price, insert_missed_opportunity, insert_formula_suggestion
from services.yfinance_service import get_price_history
from services.ai_service import analyze_missed_opportunities


def run():
    print("Opportunity analyzer running...")
    now = datetime.now(PT)
    week_ago = (now - timedelta(days=7)).isoformat()

    try:
        from database.db import get_client
        shadow = get_client().table("shadow_prices").select("*").gte("scan_timestamp", week_ago).execute().data
    except Exception as e:
        print(f"Error fetching shadow prices: {e}")
        return

    if not shadow:
        print("No shadow data for this week.")
        return

    # Check which shadow stocks moved ≥3%
    missed = []
    tickers_seen = set()
    for row in shadow:
        ticker = row["ticker"]
        if ticker in tickers_seen:
            continue
        tickers_seen.add(ticker)
        try:
            df = get_price_history(ticker, period="1mo")
            if df.empty or len(df) < 4:
                continue
            rejection_price = row.get("price") or float(df["close"].iloc[-4])
            current_price = float(df["close"].iloc[-1])
            move_pct = ((current_price - rejection_price) / rejection_price) * 100

            if abs(move_pct) >= 3.0:
                missed_row = {
                    "ticker": ticker,
                    "rejection_date": row["scan_timestamp"],
                    "score_at_rejection": row["score_at_rejection"],
                    "move_pct": round(move_pct, 2),
                    "move_direction": "UP" if move_pct > 0 else "DOWN",
                    "days_to_move": 3,
                    "signals_present": {
                        "rsi": row.get("rsi"),
                        "bb_squeeze": row.get("bb_squeeze"),
                        "volume_surge_ratio": row.get("volume_surge_ratio"),
                        "obv_trend": row.get("obv_trend"),
                    },
                    "formula_version": row.get("formula_version", "v1.0"),
                }
                missed.append(missed_row)
                try:
                    insert_missed_opportunity(missed_row)
                except Exception:
                    pass
        except Exception as e:
            print(f"  Error checking {ticker}: {e}")

    print(f"Found {len(missed)} missed opportunities.")
    if not missed:
        return

    # Ask Claude to analyze patterns
    analysis = analyze_missed_opportunities(missed)
    suggestions = analysis.get("suggestions", [])

    for s in suggestions:
        try:
            insert_formula_suggestion({
                "suggestion_date": now.isoformat(),
                "suggested_by": "claude",
                "source": "shadow_portfolio",
                "plain_english": s.get("plain_english", ""),
                "technical_detail": s.get("technical_detail", ""),
                "evidence": {"tickers": s.get("evidence_tickers", []), "pattern": analysis.get("pattern_summary", "")},
                "projected_improvement": s.get("projected_improvement_pct", 0),
                "status": "PENDING",
            })
            print(f"  Suggestion queued: {s.get('plain_english', '')[:60]}...")
        except Exception as e:
            print(f"  Error saving suggestion: {e}")

    print(f"Done. {len(suggestions)} suggestions added to approval queue.")


if __name__ == "__main__":
    run()
