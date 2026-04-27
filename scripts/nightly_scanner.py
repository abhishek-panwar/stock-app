"""
Nightly scanner — runs at 8:00 PM PT via GitHub Actions.
Scans full deduplicated universe, generates predictions, logs to Supabase, sends Telegram.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
import pytz
from dotenv import load_dotenv
load_dotenv()

PT = pytz.timezone("America/Los_Angeles")

from services.yfinance_service import get_price_history, get_ticker_info
from services.finnhub_service import get_news_sentiment, get_social_sentiment, get_analyst_recommendation, get_earnings_history
from services.screener_service import build_universe, get_hot_tickers, rank_predictions, compute_buy_window
from indicators.technicals import compute_all
from indicators.scoring import compute_signal_score, determine_direction, compute_buy_range, compute_targets, FORMULA_VERSION
from services.ai_service import analyze_stock, estimate_cost
from services.telegram_service import send_nightly_summary
from database.db import insert_prediction, insert_scan_log, insert_shadow_price, get_accuracy_stats

SCORE_THRESHOLD_DROP = 60
SCORE_THRESHOLD_HIGHLIGHT = 85
MAX_CLAUDE_CALLS = 20


def run():
    start_time = datetime.now(PT)
    print(f"[{start_time.strftime('%I:%M %p PT')}] Nightly scanner starting...")

    scan_stats = {
        "timestamp": start_time.isoformat(),
        "scan_type": "nightly",
        "yfinance_rows_fetched": 0,
        "finnhub_news_fetched": 0,
        "claude_calls_made": 0,
        "predictions_created": 0,
        "errors_encountered": 0,
        "errors_recovered": 0,
    }

    # ── Build universe ────────────────────────────────────────────────────────
    print("Building hot stock list...")
    hot_tickers = get_hot_tickers(top_n=50)
    universe, nasdaq_count, hot_count, overlap_count = build_universe(hot_tickers)
    universe_total = len(universe)
    scan_stats.update({
        "nasdaq100_count": nasdaq_count,
        "hot_stock_count": hot_count,
        "overlap_count": overlap_count,
        "universe_total": universe_total,
    })
    print(f"Universe: {universe_total} stocks ({nasdaq_count} Nasdaq + {hot_count} hot → {overlap_count} overlap)")

    # ── Load accuracy context for Claude prompts ──────────────────────────────
    accuracy_context = _build_accuracy_context()

    # ── Score all stocks ──────────────────────────────────────────────────────
    print(f"Scoring {universe_total} stocks...")
    scored = []
    shadow = []

    for item in universe:
        ticker = item["ticker"]
        source = item["source"]
        try:
            df = get_price_history(ticker, period="6mo")
            if df.empty:
                continue
            scan_stats["yfinance_rows_fetched"] += len(df)

            ind = compute_all(df)
            if not ind:
                continue

            sentiment = get_news_sentiment(ticker, hours=48)
            scan_stats["finnhub_news_fetched"] += sentiment.get("volume", 0)
            social = get_social_sentiment(ticker)
            sentiment["mentions"] = social.get("mentions", 0)
            analyst = get_analyst_recommendation(ticker)
            earnings = get_earnings_history(ticker)

            # Score for all three timeframes, take max
            best_score = 0
            best_tf = "short"
            for tf in ["short", "medium", "long"]:
                s = compute_signal_score(ind, sentiment, analyst, earnings, timeframe=tf, source=source)
                if s["total"] > best_score:
                    best_score = s["total"]
                    best_tf = tf

            if best_score < SCORE_THRESHOLD_DROP:
                continue

            scored.append({
                "ticker": ticker,
                "source": source,
                "score": best_score,
                "timeframe": best_tf,
                "indicators": ind,
                "sentiment": sentiment,
                "analyst": analyst,
                "earnings": earnings,
            })

            # Shadow portfolio: track score 55–74 for missed opportunity detection
            if 55 <= best_score < 75:
                shadow.append({
                    "ticker": ticker,
                    "scan_timestamp": start_time.isoformat(),
                    "score_at_rejection": best_score,
                    "price": ind.get("price"),
                    "volume": None,
                    "rsi": ind.get("rsi"),
                    "macd_signal": ind.get("macd_signal"),
                    "bb_squeeze": ind.get("bb_squeeze"),
                    "volume_surge_ratio": ind.get("volume_surge_ratio"),
                    "obv_trend": ind.get("obv_trend"),
                    "formula_version": FORMULA_VERSION,
                })

        except Exception as e:
            scan_stats["errors_encountered"] += 1
            print(f"  Error on {ticker}: {e}")

    # Sort by score, take top MAX_CLAUDE_CALLS for deep analysis
    scored.sort(key=lambda x: x["score"], reverse=True)
    top_stocks = scored[:MAX_CLAUDE_CALLS]
    scan_stats["stocks_analyzed"] = len(top_stocks)
    print(f"Top {len(top_stocks)} stocks sent to Claude...")

    # ── Log shadow portfolio ──────────────────────────────────────────────────
    for s in shadow:
        try:
            insert_shadow_price(s)
        except Exception:
            pass

    # ── Claude deep analysis + prediction logging ──────────────────────────────
    all_predictions = []

    for item in top_stocks:
        ticker = item["ticker"]
        ind = item["indicators"]
        sentiment = item["sentiment"]
        analyst = item["analyst"]

        for tf in ["short", "medium", "long"]:
            try:
                score_data = compute_signal_score(ind, sentiment, analyst, item["earnings"],
                                                   timeframe=tf, source=item["source"])
                if score_data["total"] < SCORE_THRESHOLD_DROP:
                    continue

                ticker_history = _get_ticker_history(ticker, tf)
                ai_result = analyze_stock(ticker, tf, ind, sentiment, analyst, score_data,
                                          accuracy_context=accuracy_context,
                                          ticker_history=ticker_history)
                scan_stats["claude_calls_made"] += 1

                direction = ai_result.get("direction", "NEUTRAL")
                position = ai_result.get("position", "HOLD")
                confidence = ai_result.get("confidence", 50)
                price = ind.get("price", 0)
                atr = ind.get("atr", price * 0.02)
                buy_low, buy_high = compute_buy_range(price, atr, direction)
                target_low, target_high, stop_loss = compute_targets(price, atr, direction)
                buy_window = ai_result.get("buy_window") or compute_buy_window(tf, score_data["total"])

                pred = {
                    "ticker": ticker,
                    "predicted_on": start_time.isoformat(),
                    "timeframe": tf,
                    "direction": direction,
                    "position": position,
                    "confidence": confidence,
                    "score": score_data["total"],
                    "price_at_prediction": price,
                    "buy_range_low": buy_low,
                    "buy_range_high": buy_high,
                    "target_low": target_low,
                    "target_high": target_high,
                    "stop_loss": stop_loss,
                    "reasoning": ai_result.get("reasoning", ""),
                    "source": item["source"],
                    "formula_version": FORMULA_VERSION,
                    "outcome": "PENDING",
                }

                saved = insert_prediction(pred)
                pred["id"] = saved.get("id")
                pred["buy_window"] = buy_window
                pred["buy_low"] = buy_low
                pred["buy_high"] = buy_high
                all_predictions.append(pred)
                scan_stats["predictions_created"] += 1

            except Exception as e:
                scan_stats["errors_encountered"] += 1
                print(f"  Prediction error {ticker}/{tf}: {e}")

    scan_stats["claude_cost_usd"] = estimate_cost(scan_stats["claude_calls_made"])

    # ── Rank and send Telegram ────────────────────────────────────────────────
    ranked = rank_predictions(all_predictions)
    top_pick = ranked.get("top_pick")
    if top_pick:
        agree_tickers = {a["ticker"] for a in ranked.get("all_timeframes_agree", [])}
        top_pick["all_timeframes_agree"] = top_pick["ticker"] in agree_tickers

    try:
        from database.db import get_open_predictions
        open_trades = get_open_predictions()
        winning = sum(1 for t in open_trades if (t.get("price_at_prediction") or 0) < (t.get("price_at_close") or t.get("price_at_prediction") or 0))
        losing = sum(1 for t in open_trades if (t.get("price_at_prediction") or 0) > (t.get("price_at_close") or t.get("price_at_prediction") or 1e9))
        neutral = len(open_trades) - winning - losing
    except Exception:
        open_trades, winning, losing, neutral = [], 0, 0, 0

    picks_for_telegram = {
        "short": ranked["short"][:3],
        "medium": ranked["medium"][:3],
        "long": ranked["long"][:3],
        "top_pick": top_pick,
    }

    send_nightly_summary(
        picks=picks_for_telegram,
        open_trades=len(open_trades),
        winning=winning,
        losing=losing,
        neutral=neutral,
        universe_total=universe_total,
        nasdaq_count=nasdaq_count,
        hot_count=hot_count,
        overlap=overlap_count,
    )

    # ── Write scan log ────────────────────────────────────────────────────────
    try:
        insert_scan_log(scan_stats)
    except Exception as e:
        print(f"Scan log error: {e}")

    elapsed = (datetime.now(PT) - start_time).seconds
    print(f"Done in {elapsed}s. {scan_stats['predictions_created']} predictions logged.")
    return scan_stats


def _build_accuracy_context() -> str:
    try:
        stats = get_accuracy_stats(reliable_only=True)
        if not stats:
            return ""
        lines = ["Signal accuracy (last 60 days):"]
        for s in stats[:8]:
            lines.append(f"  {s['signal_combo']}: {s['win_rate']*100:.0f}% win ({s['total_trades']} trades)")
        return "\n".join(lines)
    except Exception:
        return ""


def _get_ticker_history(ticker: str, timeframe: str) -> str:
    try:
        from database.db import get_predictions
        preds = get_predictions({"ticker": ticker, "timeframe": timeframe}, limit=20)
        closed = [p for p in preds if p.get("outcome") in ("WIN", "LOSS")]
        if len(closed) < 3:
            return ""
        wins = sum(1 for p in closed if p["outcome"] == "WIN")
        pct = wins / len(closed) * 100
        return f"{ticker} {timeframe}-term: {wins}/{len(closed)} wins ({pct:.0f}%) — {'be cautious' if pct < 50 else 'reliable'}"
    except Exception:
        return ""


if __name__ == "__main__":
    run()
