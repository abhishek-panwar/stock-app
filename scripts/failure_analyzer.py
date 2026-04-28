"""
Failure Analyzer — runs at 5 PM PT on weekdays.
Analyzes closed predictions (WIN/LOSS) to learn why failures happened
and whether winning predictions were accurate on timing.
Saves optimization suggestions to optimization_queue for user approval.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()

import pytz
PT = pytz.timezone("America/Los_Angeles")

from database.db import get_predictions, insert_optimization, log_error
from services.ai_service import analyze_prediction_outcomes


def run():
    print(f"[{datetime.now(PT).strftime('%I:%M %p PT')}] Failure analyzer starting...")

    all_preds = get_predictions(limit=500)
    closed = [p for p in all_preds if p.get("outcome") in ("WIN", "LOSS")]

    if len(closed) < 3:
        print("Not enough closed predictions to analyze yet.")
        return

    losses = [p for p in closed if p.get("outcome") == "LOSS"]
    wins   = [p for p in closed if p.get("outcome") == "WIN"]

    print(f"  {len(closed)} closed predictions: {len(wins)} wins, {len(losses)} losses")

    result = analyze_prediction_outcomes(wins, losses)
    if not result:
        print("  Analysis returned empty result.")
        return

    suggestions = result.get("suggestions", [])
    print(f"  Got {len(suggestions)} optimization suggestions")

    saved = 0
    for s in suggestions:
        try:
            insert_optimization({
                "created_at":            datetime.utcnow().isoformat(),
                "analysis_date":         datetime.now(PT).strftime("%Y-%m-%d"),
                "status":                "PENDING",
                "failure_pattern":       result.get("failure_pattern", ""),
                "timing_accuracy_note":  result.get("timing_accuracy_note", ""),
                "suggestion_plain":      s.get("plain_english", ""),
                "suggestion_technical":  s.get("technical_detail", ""),
                "evidence_tickers":      ",".join(s.get("evidence_tickers", [])),
                "projected_improvement": s.get("projected_improvement_pct", 0),
                "total_analyzed":        len(closed),
                "wins_analyzed":         len(wins),
                "losses_analyzed":       len(losses),
            })
            saved += 1
        except Exception as e:
            log_error("failure_analyzer", f"Failed to save suggestion: {e}", level="WARNING")

    print(f"  Saved {saved} suggestions to optimization_queue.")
    return {"suggestions_saved": saved, "closed_analyzed": len(closed)}


if __name__ == "__main__":
    run()
