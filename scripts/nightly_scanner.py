"""
Nightly scanner — runs at 8:00 PM PT via GitHub Actions.
Scores universe, picks top 20 stocks, one Claude call each, buckets by days_to_target.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv
load_dotenv()

PT = pytz.timezone("America/Los_Angeles")

from services.yfinance_service import get_price_history, get_ticker_info
from services.finnhub_service import get_news_sentiment, get_social_sentiment, get_analyst_recommendation, get_earnings_history, get_upcoming_earnings_universe, get_analyst_price_target
from services.edgar_service import get_insider_buying
from services.screener_service import build_universe, get_hot_tickers, rank_predictions, compute_buy_window, get_asset_class
from indicators.technicals import compute_all
from indicators.scoring import compute_signal_score, compute_buy_range, FORMULA_VERSION
from services.ai_service import analyze_stock, estimate_cost
from services.telegram_service import send_nightly_summary
from database.db import insert_prediction, insert_scan_log, insert_shadow_price, get_accuracy_stats, log_error, save_hot_tickers, get_pending_prediction_for_ticker, replace_prediction_if_stronger, run_migrations

SCORE_THRESHOLD    = 45   # minimum score to be eligible
MAX_STOCKS         = 50   # top N scored stocks sent to Claude
MAX_EARNINGS_PICKS = 15   # extra earnings-catalyst stocks added to Claude batch
MIN_PROFIT_PCT     = 4.0  # minimum absolute profit % to entry

# Claude's days_to_target → timeframe bucket
def _bucket(days: int) -> str:
    if days <= 10:
        return "short"
    if days <= 35:
        return "medium"
    return "long"


def run():
    start_time = datetime.now(PT)
    print(f"[{start_time.strftime('%I:%M %p PT')}] Nightly scanner starting...")
    run_migrations()

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
    print("Building universe...")
    try:
        hot_tickers = get_hot_tickers(top_n=50)
        universe, nasdaq_count, hot_count, overlap_count = build_universe(hot_tickers)
    except Exception as e:
        log_error("scanner", f"Failed to build universe: {e}", level="ERROR")
        raise

    universe_total = len(universe)
    scan_stats.update({
        "nasdaq100_count": nasdaq_count,
        "hot_stock_count": hot_count,
        "overlap_count": overlap_count,
        "universe_total": universe_total,
    })
    print(f"Universe: {universe_total} stocks ({nasdaq_count} Nasdaq + {hot_count} hot → {overlap_count} overlap)")
    log_error("scanner", f"Universe: {universe_total} stocks", level="INFO")

    # Persist hot tickers to DB for display on dashboard
    try:
        save_hot_tickers(hot_tickers, start_time.isoformat())
        print(f"  Saved {len(hot_tickers)} hot tickers to DB")
    except Exception as e:
        log_error("scanner", f"Failed to save hot tickers: {e}", level="WARNING")

    accuracy_context = _build_accuracy_context()

    # ── Load bulk data once (cached) ──────────────────────────────────────────
    print("Loading bulk earnings calendar (1 Finnhub call, cached 24h)...")
    earnings_universe = get_upcoming_earnings_universe(days_ahead=7)
    universe_tickers = {item["ticker"] for item in universe}

    # ── Score every stock once (no timeframe) ─────────────────────────────────
    print(f"Scoring {universe_total} stocks...")
    scored = []
    shadow = []

    for item in universe:
        ticker = item["ticker"]
        source = item["source"]
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
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
            analyst        = get_analyst_recommendation(ticker)
            earnings       = get_earnings_history(ticker)
            analyst_target = get_analyst_price_target(ticker)
            insider_buying = get_insider_buying(ticker, days_back=14)
            info           = get_ticker_info(ticker)

            # Earnings calendar from bulk lookup — no per-stock API call
            ec_data = earnings_universe.get(ticker.upper())
            earnings_calendar = (
                {"has_upcoming": True,
                 "days_to_earnings": ec_data["days_to_earnings"],
                 "earnings_date": ec_data["earnings_date"]}
                if ec_data else {"has_upcoming": False, "days_to_earnings": None, "earnings_date": None}
            )

            # Single score — no timeframe bias
            score_data = compute_signal_score(
                ind, sentiment, analyst, earnings, source=source,
                earnings_calendar=earnings_calendar, analyst_target=analyst_target,
                insider_buying=insider_buying,
            )
            total = score_data["total"]

            if total < SCORE_THRESHOLD:
                if 40 <= total < SCORE_THRESHOLD:
                    shadow.append({
                        "ticker": ticker,
                        "scan_timestamp": start_time.isoformat(),
                        "score_at_rejection": total,
                        "price": ind.get("price"),
                        "volume": None,
                        "rsi": ind.get("rsi"),
                        "macd_signal": ind.get("macd_signal"),
                        "bb_squeeze": ind.get("bb_squeeze"),
                        "volume_surge_ratio": ind.get("volume_surge_ratio"),
                        "obv_trend": ind.get("obv_trend"),
                        "formula_version": FORMULA_VERSION,
                    })
                continue

            scored.append({
                "ticker": ticker,
                "company_name": info.get("name", ticker),
                "source": source,
                "score": total,
                "score_data": score_data,
                "indicators": ind,
                "sentiment": sentiment,
                "analyst": analyst,
                "earnings": earnings,
                "earnings_calendar": earnings_calendar,
                "analyst_upside_pct": score_data.get("analyst_upside_pct"),
                "insider_buying": insider_buying,
            })

        except Exception as e:
            scan_stats["errors_encountered"] += 1
            log_error("scanner", f"Scoring error: {ticker}: {e}", detail=str(e), ticker=ticker)
            print(f"  Error on {ticker}: {e}")

    # Deduplicate alias pairs — keep highest scoring one
    ALIASES = [
        {"GOOGL", "GOOG"},
        {"BRK-A", "BRK-B"},
        {"META", "FB"},
    ]
    scored.sort(key=lambda x: x["score"], reverse=True)
    seen_groups: list[set] = []
    deduped = []
    for s in scored:
        ticker = s["ticker"]
        in_group = next((g for g in ALIASES if ticker in g), None)
        if in_group:
            if in_group in seen_groups:
                print(f"  {ticker} skipped — alias already represented")
                continue
            seen_groups.append(in_group)
        deduped.append(s)

    top_stocks = deduped[:MAX_STOCKS]

    # ── Earnings pickup — add below-threshold stocks with upcoming earnings ────
    # These stocks scored < SCORE_THRESHOLD but have earnings in 1–7 days.
    # They get a separate Claude call tagged as earnings_pickup so Claude knows.
    scored_tickers = {s["ticker"] for s in top_stocks}
    earnings_pickup = []
    for item in universe:
        ticker = item["ticker"]
        if ticker in scored_tickers:
            continue  # already in main batch
        if ticker not in earnings_universe:
            continue  # no upcoming earnings
        if len(earnings_pickup) >= MAX_EARNINGS_PICKS:
            break
        ec_data = earnings_universe[ticker]
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                df = get_price_history(ticker, period="6mo")
            if df.empty:
                continue
            ind = compute_all(df)
            if not ind:
                continue
            info = get_ticker_info(ticker)
            earnings = get_earnings_history(ticker)
            earnings_calendar = {
                "has_upcoming": True,
                "days_to_earnings": ec_data["days_to_earnings"],
                "earnings_date": ec_data["earnings_date"],
            }
            earnings_pickup.append({
                "ticker": ticker,
                "company_name": info.get("name", ticker),
                "source": item["source"],
                "score": 0,  # below threshold — earnings pickup only
                "score_data": {"total": 0, "base": 0, "bonus": 0, "bonus_reasons": ["Earnings pickup"], "breakdown": {}, "formula_version": FORMULA_VERSION, "analyst_upside_pct": None, "earnings_calendar": earnings_calendar, "insider_buying": None},
                "indicators": ind,
                "sentiment": {"score": 0, "volume": 0, "mentions": 0},
                "analyst": {"consensus": "HOLD", "score": 0.5},
                "earnings": earnings,
                "earnings_calendar": earnings_calendar,
                "analyst_upside_pct": None,
                "insider_buying": None,
                "is_earnings_pickup": True,
            })
        except Exception:
            continue

    if earnings_pickup:
        print(f"  Earnings pickup: {len(earnings_pickup)} below-threshold stocks with upcoming earnings added to Claude batch")

    top_stocks = top_stocks + earnings_pickup
    scan_stats["stocks_analyzed"] = len(top_stocks)
    print(f"Top {len(top_stocks)} stocks → Claude analysis ({MAX_STOCKS} scored + {len(earnings_pickup)} earnings pickup)...")

    for s in shadow:
        try:
            insert_shadow_price(s)
        except Exception as e:
            log_error("scanner", f"Shadow insert failed {s.get('ticker')}: {e}", level="WARNING")

    # ── One Claude call per stock ─────────────────────────────────────────────
    all_predictions = []
    claude_raw_log  = []

    for item in top_stocks:
        ticker = item["ticker"]
        ind    = item["indicators"]
        sentiment = item["sentiment"]
        analyst   = item["analyst"]
        score_data = item["score_data"]

        try:
            ticker_history = _get_ticker_history(ticker)
            ai = analyze_stock(
                ticker, ind, sentiment, analyst, score_data,
                accuracy_context=accuracy_context,
                ticker_history=ticker_history,
                earnings_calendar=item.get("earnings_calendar"),
                analyst_upside_pct=item.get("analyst_upside_pct"),
                insider_buying=item.get("insider_buying"),
            )
            scan_stats["claude_calls_made"] += 1

            direction  = ai.get("direction", "NEUTRAL")
            position   = ai.get("position", "HOLD")
            confidence = ai.get("confidence", 50)
            price      = ind.get("price", 0)
            atr        = ind.get("atr", price * 0.02) or (price * 0.02)

            # Use Claude's target/stop if valid, otherwise derive from ATR
            target_price = ai.get("target_price")
            stop_price   = ai.get("stop_price")

            raw_target = target_price
            raw_stop   = stop_price

            if not target_price or target_price <= 0:
                mult = {"BULLISH": 1.5, "BEARISH": -1.5}.get(direction, 1.0)
                target_price = round(price + atr * mult * 1.5, 2)
            if not stop_price or stop_price <= 0 or abs(stop_price - price) < atr * 0.3:
                pct = 0.02
                stop_price = round(price * (1 - pct) if direction == "BULLISH" else price * (1 + pct), 2)

            # Use enough decimal places for low-price assets (crypto like DOGE)
            decimals = 6 if price < 1 else 4 if price < 10 else 2
            target_price = round(float(target_price), decimals)
            stop_price   = round(float(stop_price), decimals)

            # target_low/high for UI compatibility — use ±3% of target_price
            target_low  = round(target_price * 0.97, decimals)
            target_high = round(target_price * 1.03, decimals)

            # Profit % filter — based on target_low (what UI shows as "Profit potential")
            profit_pct = abs(target_low - price) / price * 100 if price > 0 else 0
            passed_filter = profit_pct >= MIN_PROFIT_PCT

            # ── Collect raw Claude response before any filter ─────────────────
            claude_raw_log.append({
                "ticker":           ticker,
                "score":            item["score"],
                "price":            price,
                "direction":        direction,
                "position":         position,
                "confidence":       confidence,
                "raw_target":       raw_target,
                "raw_stop":         raw_stop,
                "used_target":      target_price,
                "used_stop":        stop_price,
                "profit_pct":       round(profit_pct, 2),
                "passed_filter":    passed_filter,
                "days_to_target":   ai.get("days_to_target"),
                "reasoning":        ai.get("reasoning", ""),
                "key_signals":      ai.get("key_signals", []),
            })

            if not passed_filter:
                print(f"  {ticker} skipped — profit {profit_pct:.1f}% < {MIN_PROFIT_PCT}%")
                continue

            buy_low, buy_high = compute_buy_range(price, atr, direction)
            buy_window = ai.get("buy_window") or compute_buy_window("short", score_data["total"])

            # Bucket by Claude's days estimate
            days_to_target = ai.get("days_to_target")
            if not days_to_target or days_to_target <= 0:
                # ATR fallback: how many days to cover target distance
                dist = abs(target_price - price)
                days_to_target = max(2, round(dist / atr))

            timeframe  = _bucket(days_to_target)
            expires_on = (start_time + timedelta(days=round(days_to_target * 1.2))).isoformat()

            # Build earnings label for UI display
            ec = item.get("earnings_calendar") or {}
            if ec.get("has_upcoming"):
                days_e = ec.get("days_to_earnings", 0)
                if days_e == 0:
                    earnings_label = "⚡ EARNINGS TODAY"
                elif days_e == 1:
                    earnings_label = "⚡ EARNINGS TOMORROW"
                else:
                    earnings_label = f"⚡ EARNINGS IN {days_e} DAYS"
            else:
                earnings_label = ""

            # Build insider signal label for UI display
            ib = item.get("insider_buying") or {}
            if ib.get("has_insider_buying"):
                strength = ib.get("signal_strength", "")
                total_usd = ib.get("total_purchased_usd", 0)
                total_str = f"${total_usd/1e6:.1f}M" if total_usd >= 1_000_000 else f"${total_usd/1e3:.0f}K"
                insider_signal = f"👤 INSIDER BUY {total_str} ★" if strength == "STRONG" else f"👤 INSIDER BUY {total_str}"
            else:
                insider_signal = ""

            pred = {
                "ticker":               ticker,
                "asset_class":          get_asset_class(ticker),
                "company_name":         item.get("company_name", ticker),
                "predicted_on":         start_time.isoformat(),
                "expires_on":           expires_on,
                "days_to_target":       days_to_target,
                "timing_rationale":     ai.get("timing_rationale", ""),
                "timeframe":            timeframe,
                "direction":            direction,
                "position":             position,
                "confidence":           confidence,
                "score":                score_data["total"],
                "price_at_prediction":  price,
                "buy_range_low":        buy_low,
                "buy_range_high":       buy_high,
                "target_low":           target_low,
                "target_high":          target_high,
                "stop_loss":            stop_price,
                "reasoning":            ai.get("reasoning", ""),
                "source":               item["source"],
                "formula_version":      FORMULA_VERSION,
                "outcome":              "PENDING",
                "earnings_label":       earnings_label or None,
                "insider_signal":       insider_signal or None,
            }

            action = replace_prediction_if_stronger(ticker, profit_pct, pred)
            if action == "skipped":
                print(f"  {ticker} skipped — existing prediction is stronger or equal")
                continue
            if action == "replaced":
                print(f"  {ticker} replaced — new prediction is stronger (+{profit_pct:.1f}%)")
            # action == "insert" → no existing prediction, insert normally

            saved = insert_prediction(pred)
            pred["id"]        = saved.get("id")
            pred["buy_window"] = buy_window
            pred["buy_low"]   = buy_low
            pred["buy_high"]  = buy_high
            all_predictions.append(pred)
            scan_stats["predictions_created"] += 1
            print(f"  {ticker} ({item['company_name']}) → {direction} {timeframe}-term, {days_to_target}d, {confidence}% conf")

        except Exception as e:
            scan_stats["errors_encountered"] += 1
            log_error("scanner", f"Prediction error {ticker}: {e}", detail=str(e), ticker=ticker)
            print(f"  Error on {ticker}: {e}")

    scan_stats["claude_cost_usd"] = estimate_cost(scan_stats["claude_calls_made"])

    # ── Telegram summary ──────────────────────────────────────────────────────
    ranked = rank_predictions(all_predictions)
    top_pick = ranked.get("top_pick")
    if top_pick:
        agree_tickers = {a["ticker"] for a in ranked.get("all_timeframes_agree", [])}
        top_pick["all_timeframes_agree"] = top_pick["ticker"] in agree_tickers

    try:
        from database.db import get_open_predictions
        open_trades = get_open_predictions()
        winning = sum(1 for t in open_trades if (t.get("price_at_prediction") or 0) < (t.get("price_at_close") or t.get("price_at_prediction") or 0))
        losing  = sum(1 for t in open_trades if (t.get("price_at_prediction") or 0) > (t.get("price_at_close") or t.get("price_at_prediction") or 1e9))
        neutral = len(open_trades) - winning - losing
    except Exception as e:
        open_trades, winning, losing, neutral = [], 0, 0, 0
        log_error("scanner", f"Could not load open trades: {e}", level="WARNING")

    try:
        ok = send_nightly_summary(
            picks={"short": ranked["short"][:3], "medium": ranked["medium"][:3],
                   "long": ranked["long"][:3], "top_pick": top_pick},
            open_trades=len(open_trades), winning=winning, losing=losing, neutral=neutral,
            universe_total=universe_total, nasdaq_count=nasdaq_count,
            hot_count=hot_count, overlap=overlap_count,
        )
        if not ok:
            log_error("telegram", "send_nightly_summary returned False", level="WARNING")
    except Exception as e:
        log_error("telegram", f"Telegram send failed: {e}", detail=str(e), level="ERROR")

    try:
        insert_scan_log(scan_stats)
    except Exception as e:
        log_error("scanner", f"Scan log insert failed: {e}", detail=str(e), level="ERROR")

    # ── Save raw Claude log — Supabase cache + GitHub file ───────────────────
    import json
    date_str = start_time.strftime("%Y-%m-%d")
    raw_payload = {
        "scan_date": date_str,
        "total_calls": len(claude_raw_log),
        "passed_filter": sum(1 for r in claude_raw_log if r["passed_filter"]),
        "responses": claude_raw_log,
    }

    # 1. Supabase cache (works everywhere, 7-day TTL)
    try:
        from database.db import set_cache
        set_cache(f"claude_raw_{date_str}", raw_payload, ttl_hours=168)
        print(f"  Raw Claude log → Supabase cache (claude_raw_{date_str})")
    except Exception as e:
        print(f"  Warning: Supabase cache save failed: {e}")

    # 2. GitHub file via API (works on Modal — uses GITHUB_TOKEN secret)
    try:
        import base64, requests
        token = os.environ.get("GITHUB_TOKEN", "")
        repo  = os.environ.get("GITHUB_REPO", "")
        if token and repo:
            file_path = f"debug/claude_raw_{date_str}.json"
            content   = base64.b64encode(json.dumps(raw_payload, indent=2).encode()).decode()
            api       = f"https://api.github.com/repos/{repo}/contents/{file_path}"
            headers   = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
            sha = None
            try:
                r = requests.get(api, headers=headers, timeout=10)
                if r.status_code == 200:
                    sha = r.json().get("sha")
            except Exception:
                pass
            payload = {"message": f"debug: claude raw responses {date_str}", "content": content}
            if sha:
                payload["sha"] = sha
            r = requests.put(api, headers=headers, json=payload, timeout=15)
            r.raise_for_status()
            print(f"  Raw Claude log → GitHub debug/claude_raw_{date_str}.json")
        else:
            # Fallback: local git push (works when running locally without secrets)
            base_dir  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            debug_dir = os.path.join(base_dir, "debug")
            os.makedirs(debug_dir, exist_ok=True)
            with open(os.path.join(debug_dir, f"claude_raw_{date_str}.json"), "w") as f:
                json.dump(raw_payload, f, indent=2)
            import subprocess
            rel_path = f"debug/claude_raw_{date_str}.json"
            subprocess.run(["git", "add", rel_path], cwd=base_dir, check=True)
            subprocess.run(["git", "commit", "-m", f"debug: claude raw responses {date_str}"], cwd=base_dir, check=True)
            subprocess.run(["git", "push"], cwd=base_dir, check=True)
            print(f"  Raw Claude log → local git push ({rel_path})")
    except Exception as e:
        print(f"  Warning: GitHub file save failed: {e}")

    # Always return raw log so UI debug button can save via GitHub API
    scan_stats["claude_raw_log"] = claude_raw_log

    elapsed = (datetime.now(PT) - start_time).seconds
    summary = f"Done in {elapsed}s — {scan_stats['predictions_created']} predictions, {scan_stats['errors_encountered']} errors."
    print(summary)
    log_error("scanner", summary, level="INFO")
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


def _get_ticker_history(ticker: str) -> str:
    try:
        from database.db import get_predictions
        preds = get_predictions({"ticker": ticker}, limit=20)
        closed = [p for p in preds if p.get("outcome") in ("WIN", "LOSS")]
        if len(closed) < 3:
            return ""
        wins = sum(1 for p in closed if p["outcome"] == "WIN")
        pct = wins / len(closed) * 100
        return f"{ticker}: {wins}/{len(closed)} wins ({pct:.0f}%) — {'be cautious' if pct < 50 else 'reliable track record'}"
    except Exception:
        return ""


if __name__ == "__main__":
    run()
