"""
Nightly scanner — runs at 10:20 PM PT via Modal.

Mon–Thu (short-term):
  Bullish: top 30 momentum stocks → Claude bullish → BULLISH predictions
  Bearish: top 20 overbought reversal candidates → Claude bearish → BEARISH predictions

Friday (long-term):
  Bullish: top 30 fundamental re-rating candidates → Claude long bullish → BULLISH predictions
  Bearish: top 20 fundamental deterioration candidates → Claude long bearish → BEARISH predictions

Both modes share: superset fetch, _run_claude_prediction helper, alias dedup, Telegram summary.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv
load_dotenv()

PT = pytz.timezone("America/Los_Angeles")

from services.finnhub_service import get_upcoming_earnings_universe
from services.screener_service import rank_predictions, compute_buy_window, get_asset_class
from services.short_term_bullish_universe import get_bullish_hot_tickers, get_bullish_candidates, filter_bullish_universe, fetch_alpha_vantage_gainers
from services.short_term_bearish_universe import get_bearish_hot_tickers, filter_bearish_universe
from services.long_term_bullish_universe import get_long_bullish_hot_tickers, filter_long_bullish_universe
from services.long_term_bearish_universe import get_long_bearish_hot_tickers, filter_long_bearish_universe
from services.market_data_fetcher import fetch_all
from indicators.scoring import compute_buy_range
from indicators.short_term_bullish_scorer import compute_short_term_bullish_score, FORMULA_VERSION as BULLISH_FORMULA_VERSION
from indicators.short_term_bearish_scorer import compute_short_term_bearish_score, FORMULA_VERSION as BEARISH_FORMULA_VERSION
from indicators.long_term_bullish_scorer import compute_long_term_bullish_score, FORMULA_VERSION as LONG_BULLISH_FORMULA_VERSION
from indicators.long_term_bearish_scorer import compute_long_term_bearish_score, FORMULA_VERSION as LONG_BEARISH_FORMULA_VERSION
from services.ai_service import analyze_stock_bullish, analyze_stock_bearish, analyze_stock_long, analyze_stock_long_bearish, estimate_cost
from services.telegram_service import send_nightly_summary
from database.db import insert_prediction, insert_scan_log, insert_shadow_price, get_accuracy_stats, log_error, save_hot_tickers, replace_prediction_if_stronger, run_migrations

# Short-term thresholds (Mon–Thu)
BULLISH_SCORE_THRESHOLD      = 45
BEARISH_SCORE_THRESHOLD      = 45
# Long-term thresholds (Friday) — raised from 35: filter out "meh but not terrible" names
LONG_BULLISH_SCORE_THRESHOLD = 50
LONG_BEARISH_SCORE_THRESHOLD = 30
MAX_BULLISH_STOCKS           = 30   # top N bullish stocks sent to Claude
MAX_BEARISH_STOCKS           = 20   # top N bearish stocks sent to Claude
MIN_PROFIT_PCT               = 4.0  # minimum absolute profit % to save a prediction
EARNINGS_WINDOW_DAYS         = 14   # how far ahead to fetch the earnings calendar
CLAUDE_LOG_CACHE_TTL_H       = 168  # cache TTL for raw Claude scan log (7 days)

FORMULA_VERSION      = BULLISH_FORMULA_VERSION  # kept for scan_log compatibility
ANALYST_TARGET_TTL_H = 24    # cache TTL for per-ticker analyst price targets

# Claude's days_to_target → timeframe bucket
def _bucket(days: int) -> str:
    if days <= 10:
        return "short"
    if days <= 35:
        return "medium"
    return "long"


def run(debug: bool = False):
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

    is_friday = datetime.now(PT).weekday() == 4
    scan_mode = "long" if (is_friday and not debug) else "short"

    # ── Step 1: Collect raw ticker lists (HTTP only) ──────────────────────────
    # Alpha Vantage called ONCE here, result shared with both builders (Issue #4 fix)
    print("Collecting raw ticker lists...")
    av_gainers = fetch_alpha_vantage_gainers()

    try:
        bullish_hot = get_bullish_hot_tickers(av_gainers=av_gainers)
    except Exception as e:
        log_error("scanner", f"Failed to fetch bullish hot tickers: {e}", level="ERROR")
        raise

    bearish_raw = []
    if scan_mode != "long":
        try:
            bearish_raw = get_bearish_hot_tickers(av_gainers=av_gainers)
        except Exception as e:
            log_error("scanner", f"Failed to fetch bearish raw tickers: {e}", level="WARNING")
            bearish_raw = []

    # Persist hot tickers to DB for display on dashboard
    try:
        save_hot_tickers(bullish_hot, start_time.isoformat())
        print(f"  Saved {len(bullish_hot)} bullish hot tickers to DB")
    except Exception as e:
        log_error("scanner", f"Failed to save hot tickers: {e}", level="WARNING")

    accuracy_context = _build_accuracy_context()

    # ── Step 2: Load earnings calendar once ───────────────────────────────────
    if debug:
        try:
            from database.db import get_client as _db_client
            _db_client().table("earnings_calendar").delete().neq("id", 0).execute()
        except Exception:
            pass
    print("Loading bulk earnings calendar (Finnhub call at most once per 7 days)...")
    earnings_universe = get_upcoming_earnings_universe(days_ahead=EARNINGS_WINDOW_DAYS)

    print(f"  Scan mode: {scan_mode.upper()} ({'Friday long-term' if scan_mode == 'long' else 'short-term'})")

    # ── Step 3: Build superset — union of all candidates, deduplicated ────────
    # earnings_universe already loaded above — derive tickers from it directly (no extra DB call)
    earnings_tickers = set(earnings_universe.keys())
    nasdaq_earnings_candidates, nasdaq100 = get_bullish_candidates(earnings_tickers)

    if scan_mode == "long":
        long_bearish_raw = get_long_bearish_hot_tickers()
        long_bullish_hot = get_long_bullish_hot_tickers(av_gainers=av_gainers)
        superset: set[str] = set(long_bullish_hot) | set(long_bearish_raw) | nasdaq_earnings_candidates
        print(f"  Superset (Friday long): {len(superset)} unique tickers "
              f"({len(long_bullish_hot)} long bullish + {len(long_bearish_raw)} long bearish + "
              f"{len(nasdaq_earnings_candidates)} Nasdaq earnings, after dedup)")
    else:
        superset: set[str] = set(bullish_hot) | set(bearish_raw) | nasdaq_earnings_candidates
        print(f"  Superset: {len(superset)} unique tickers "
              f"({len(bullish_hot)} bullish hot + {len(bearish_raw)} bearish raw + "
              f"{len(nasdaq_earnings_candidates)} Nasdaq earnings, after dedup)")

    run_date = start_time.strftime("%Y-%m-%d")
    try:
        from database.db import clear_api_call_log
        clear_api_call_log(run_date)
    except Exception:
        pass

    # ── Step 4: Single concurrent fetch pass over entire superset ─────────────
    print(f"Fetching data for {len(superset)} tickers (10 workers, one pass)...")
    ticker_data, fetch_stats = fetch_all(
        tickers=sorted(superset),
        run_date=run_date,
        earnings_universe=earnings_universe,
        log_api=True,
    )
    scan_stats["yfinance_rows_fetched"] = fetch_stats["rows_fetched"]
    scan_stats["finnhub_news_fetched"]  = fetch_stats["news_fetched"]
    scan_stats["errors_encountered"]   += fetch_stats["errors"]

    # ── Step 5: Filter universes from pre-fetched data (no API calls) ─────────
    if scan_mode == "long":
        print("Filtering long bearish universe from pre-fetched data...")
        bearish_universe, bearish_tickers = filter_long_bearish_universe(long_bearish_raw, ticker_data)

        print("Filtering long bullish universe from pre-fetched data...")
        bullish_universe, nasdaq_count, hot_count, overlap_count = filter_long_bullish_universe(
            hot_tickers=long_bullish_hot,
            nasdaq_earnings_candidates=nasdaq_earnings_candidates,
            nasdaq100=nasdaq100,
            ticker_data=ticker_data,
            long_bearish_tickers=bearish_tickers,
        )
    else:
        print("Filtering bearish universe from pre-fetched data...")
        bearish_universe, bearish_tickers = filter_bearish_universe(bearish_raw, ticker_data)

        print("Filtering bullish universe from pre-fetched data...")
        bullish_universe, nasdaq_count, hot_count, overlap_count = filter_bullish_universe(
            hot_tickers=bullish_hot,
            nasdaq_earnings_candidates=nasdaq_earnings_candidates,
            nasdaq100=nasdaq100,
            ticker_data=ticker_data,
            bearish_tickers=bearish_tickers,
        )

    universe_total = len(bullish_universe) + len(bearish_universe)
    scan_stats.update({
        "nasdaq100_count":        nasdaq_count,
        "hot_stock_count":        hot_count,
        "overlap_count":          overlap_count,
        "universe_total":         universe_total,
        "bullish_universe_count": len(bullish_universe),
        "bearish_universe_count": len(bearish_universe),
    })
    log_error("scanner",
              f"Universe ({scan_mode}): {len(bullish_universe)} bullish + {len(bearish_universe)} bearish "
              f"(fetched {len(ticker_data)}/{len(superset)} tickers)", level="INFO")

    # ── Step 6: Score both universes — pure computation, zero API calls ────────
    print(f"Scoring {len(bullish_universe)} bullish + {len(bearish_universe)} bearish (pure computation)...")
    bullish_scored = []
    bearish_scored = []
    shadow = []

    # Pick scorer and threshold based on scan mode
    if scan_mode == "long":
        _bullish_threshold  = LONG_BULLISH_SCORE_THRESHOLD
        _bearish_threshold  = LONG_BEARISH_SCORE_THRESHOLD
        _bullish_fv         = LONG_BULLISH_FORMULA_VERSION
        _bearish_fv         = LONG_BEARISH_FORMULA_VERSION
    else:
        _bullish_threshold  = BULLISH_SCORE_THRESHOLD
        _bearish_threshold  = BEARISH_SCORE_THRESHOLD
        _bullish_fv         = BULLISH_FORMULA_VERSION
        _bearish_fv         = BEARISH_FORMULA_VERSION

    for item in bullish_universe:
        ticker = item["ticker"]
        source = item["source"]
        data = ticker_data.get(ticker)
        if not data:
            continue
        ind  = data["ind"]
        try:
            if scan_mode == "long":
                score_data = compute_long_term_bullish_score(
                    ind, data["sentiment"], data["analyst"], data["earnings"],
                    source=source, earnings_calendar=data["earnings_calendar"],
                    analyst_target=data["analyst_target"],
                    insider_buying=data["insider_buying"], fundamentals=data["fundamentals"],
                )
            else:
                score_data = compute_short_term_bullish_score(
                    ind, data["sentiment"], data["analyst"], data["earnings"],
                    source=source, earnings_calendar=data["earnings_calendar"],
                    analyst_target=data["analyst_target"],
                    insider_buying=data["insider_buying"], fundamentals=data["fundamentals"],
                    social_velocity=data["social_velocity"],
                    rel_strength_vs_spy=data.get("rel_strength_vs_spy"),
                    sector_return_5d=data.get("sector_return_5d"),
                    short_interest_pct=data.get("short_interest_pct"),
                )

            total = score_data["total"]
            if total < _bullish_threshold:
                if _bullish_threshold - 5 <= total < _bullish_threshold:
                    shadow.append({
                        "ticker": ticker, "scan_timestamp": start_time.isoformat(),
                        "score_at_rejection": total, "price": ind.get("price"),
                        "volume": None, "rsi": ind.get("rsi"),
                        "macd_signal": ind.get("macd_signal"), "bb_squeeze": ind.get("bb_squeeze"),
                        "volume_surge_ratio": ind.get("volume_surge_ratio"),
                        "obv_trend": ind.get("obv_trend"), "formula_version": _bullish_fv,
                    })
                continue

            bullish_scored.append({
                "ticker": ticker, "company_name": data["company_name"],
                "market_cap": data["market_cap"], "avg_volume": data["avg_volume"],
                "source": source, "score": total, "score_data": score_data,
                "indicators": ind, "sentiment": data["sentiment"], "analyst": data["analyst"],
                "earnings": data["earnings"], "earnings_calendar": data["earnings_calendar"],
                "analyst_upside_pct": score_data.get("analyst_upside_pct"),
                "insider_buying": data["insider_buying"], "fundamentals": data["fundamentals"],
                "social_velocity": data["social_velocity"],
                "rel_strength_vs_spy": data.get("rel_strength_vs_spy"),
                "sector_return_5d": data.get("sector_return_5d"),
                "sector_etf": data.get("sector_etf"),
                "sector": data.get("sector"),
                "short_interest_pct": data.get("short_interest_pct"),
            })
        except Exception as e:
            scan_stats["errors_encountered"] += 1
            log_error("scanner", f"Bullish score error {ticker}: {e}", detail=str(e), ticker=ticker)

    for item in bearish_universe:
        ticker = item["ticker"]
        data = ticker_data.get(ticker)
        if not data:
            continue
        ind  = data["ind"]
        try:
            if scan_mode == "long":
                score_data = compute_long_term_bearish_score(
                    ind, data["sentiment"], data["analyst"], data["earnings"],
                    source=item["source"], earnings_calendar=data["earnings_calendar"],
                    analyst_target=data["analyst_target"],
                    insider_buying=data["insider_buying"], fundamentals=data["fundamentals"],
                )
            else:
                score_data = compute_short_term_bearish_score(
                    ind, data["sentiment"], data["analyst"], data["earnings"],
                    source=item["source"], earnings_calendar=data["earnings_calendar"],
                    rel_strength_vs_spy=data.get("rel_strength_vs_spy"),
                    sector_return_5d=data.get("sector_return_5d"),
                    short_interest_pct=data.get("short_interest_pct"),
                )

            total = score_data["total"]
            if total < _bearish_threshold:
                if _bearish_threshold - 5 <= total < _bearish_threshold:
                    shadow.append({
                        "ticker": ticker, "scan_timestamp": start_time.isoformat(),
                        "score_at_rejection": total, "price": ind.get("price"),
                        "volume": None, "rsi": ind.get("rsi"),
                        "macd_signal": ind.get("macd_signal"), "bb_squeeze": ind.get("bb_squeeze"),
                        "volume_surge_ratio": ind.get("volume_surge_ratio"),
                        "obv_trend": ind.get("obv_trend"), "formula_version": _bearish_fv,
                    })
                continue

            bearish_scored.append({
                "ticker": ticker, "company_name": data["company_name"],
                "market_cap": data["market_cap"], "avg_volume": data["avg_volume"],
                "source": item["source"], "score": total, "score_data": score_data,
                "indicators": ind, "sentiment": data["sentiment"], "analyst": data["analyst"],
                "earnings": data["earnings"], "earnings_calendar": data["earnings_calendar"],
                "analyst_upside_pct": score_data.get("analyst_upside_pct"),
                "insider_buying": data["insider_buying"], "fundamentals": data["fundamentals"],
                "social_velocity": None,
                "rel_strength_vs_spy": data.get("rel_strength_vs_spy"),
                "sector_return_5d": data.get("sector_return_5d"),
                "sector_etf": data.get("sector_etf"),
                "sector": data.get("sector"),
            })
        except Exception as e:
            scan_stats["errors_encountered"] += 1
            log_error("scanner", f"Bearish score error {ticker}: {e}", detail=str(e), ticker=ticker)

    # ── Deduplicate alias pairs ───────────────────────────────────────────────
    ALIASES = [{"GOOGL", "GOOG"}, {"BRK-A", "BRK-B"}]

    def _dedupe(scored_list: list) -> list:
        scored_list.sort(key=lambda x: x["score"], reverse=True)
        seen_groups: list[set] = []
        result = []
        for s in scored_list:
            t = s["ticker"]
            group = next((g for g in ALIASES if t in g), None)
            if group:
                if group in seen_groups:
                    continue
                seen_groups.append(group)
            result.append(s)
        return result

    bullish_scored = _dedupe(bullish_scored)
    bearish_scored = _dedupe(bearish_scored)

    top_bullish = bullish_scored[:MAX_BULLISH_STOCKS]
    top_bearish = bearish_scored[:MAX_BEARISH_STOCKS]

    scan_stats["stocks_analyzed"] = len(top_bullish) + len(top_bearish)
    print(f"Superset: {len(superset)} → fetched {len(ticker_data)} → "
          f"{len(bullish_universe)} bullish + {len(bearish_universe)} bearish scored")
    print(f"Claude batch: {len(top_bullish)} bullish + {len(top_bearish)} bearish")

    for s in shadow:
        try:
            insert_shadow_price(s)
        except Exception as e:
            log_error("scanner", f"Shadow insert failed {s.get('ticker')}: {e}", level="WARNING")

    # ── Claude calls — shared helper ──────────────────────────────────────────
    all_predictions = []
    claude_raw_log  = []

    def _run_claude_prediction(item: dict, pipeline: str) -> None:
        """Calls Claude for one stock, saves prediction. pipeline = 'bullish' | 'bearish'"""
        ticker     = item["ticker"]
        ind        = item["indicators"]
        sentiment  = item["sentiment"]
        analyst    = item["analyst"]
        score_data = item["score_data"]

        try:
            expected_direction = "BEARISH" if pipeline == "bearish" else "BULLISH"
            ticker_history = _get_ticker_history(ticker, expected_direction)

            if pipeline == "bearish" and scan_mode == "long":
                ai = analyze_stock_long_bearish(
                    ticker, ind, sentiment, analyst,
                    score_data=score_data,
                    accuracy_context=accuracy_context,
                    ticker_history=ticker_history,
                    earnings=item["earnings"],
                    earnings_calendar=item.get("earnings_calendar"),
                    analyst_upside_pct=item.get("analyst_upside_pct"),
                    insider_buying=item.get("insider_buying"),
                    fundamentals=item.get("fundamentals"),
                )
            elif pipeline == "bearish":
                ai = analyze_stock_bearish(
                    ticker, ind, sentiment, analyst,
                    score_data=score_data,
                    accuracy_context=accuracy_context,
                    ticker_history=ticker_history,
                    earnings=item["earnings"],
                    earnings_calendar=item.get("earnings_calendar"),
                    rel_strength_vs_spy=item.get("rel_strength_vs_spy"),
                    sector_return_5d=item.get("sector_return_5d"),
                    sector_etf=item.get("sector_etf"),
                )
            elif scan_mode == "long":
                ai = analyze_stock_long(
                    ticker, ind, sentiment, analyst,
                    score_data=score_data,
                    accuracy_context=accuracy_context,
                    ticker_history=ticker_history,
                    earnings=item["earnings"],
                    earnings_calendar=item.get("earnings_calendar"),
                    analyst_upside_pct=item.get("analyst_upside_pct"),
                    insider_buying=item.get("insider_buying"),
                    fundamentals=item.get("fundamentals"),
                    rel_strength_vs_spy=item.get("rel_strength_vs_spy"),
                    sector_return_5d=item.get("sector_return_5d"),
                    sector_etf=item.get("sector_etf"),
                    short_interest_pct=item.get("short_interest_pct"),
                )
            else:
                ai = analyze_stock_bullish(
                    ticker, ind, sentiment, analyst,
                    score_data=score_data,
                    accuracy_context=accuracy_context,
                    ticker_history=ticker_history,
                    earnings=item["earnings"],
                    earnings_calendar=item.get("earnings_calendar"),
                    analyst_upside_pct=item.get("analyst_upside_pct"),
                    insider_buying=item.get("insider_buying"),
                    fundamentals=item.get("fundamentals"),
                    social_velocity=item.get("social_velocity"),
                    rel_strength_vs_spy=item.get("rel_strength_vs_spy"),
                    sector_return_5d=item.get("sector_return_5d"),
                    sector_etf=item.get("sector_etf"),
                    short_interest_pct=item.get("short_interest_pct"),
                )
            scan_stats["claude_calls_made"] += 1

            direction  = ai.get("direction", "NEUTRAL")
            position   = ai.get("position", "HOLD")
            confidence = ai.get("confidence", 50)
            price      = ind.get("price", 0)
            atr        = ind.get("atr", price * 0.02) or (price * 0.02)

            # Discard wrong-direction responses — each pipeline is intentional
            if pipeline == "bearish" and direction != "BEARISH":
                claude_raw_log.append({
                    "ticker": ticker, "pipeline": pipeline, "score": item["score"],
                    "price": price, "direction": direction, "passed_filter": False,
                    "reasoning": ai.get("reasoning", ""), "key_signals": ai.get("key_signals", []),
                })
                print(f"  {ticker} bearish skipped — Claude returned {direction}")
                return
            if pipeline == "bullish" and direction != "BULLISH":
                claude_raw_log.append({
                    "ticker": ticker, "pipeline": pipeline, "score": item["score"],
                    "price": price, "direction": direction, "passed_filter": False,
                    "reasoning": ai.get("reasoning", ""), "key_signals": ai.get("key_signals", []),
                })
                print(f"  {ticker} bullish skipped — Claude returned {direction}")
                return

            target_price = ai.get("target_price")
            stop_price   = ai.get("stop_price")
            raw_target   = target_price
            raw_stop     = stop_price

            if not target_price or target_price <= 0:
                mult = {"BULLISH": 1.5, "BEARISH": -1.5}.get(direction, 1.0)
                atr_mult = 15 if scan_mode == "long" else 1.5
                target_price = round(price + atr * mult * atr_mult, 2)
            if not stop_price or stop_price <= 0 or abs(stop_price - price) < atr * 0.3:
                pct = 0.08 if scan_mode == "long" else 0.02
                stop_price = round(price * (1 - pct) if direction == "BULLISH" else price * (1 + pct), 2)

            decimals     = 6 if price < 1 else 4 if price < 10 else 2
            target_price = round(float(target_price), decimals)
            stop_price   = round(float(stop_price), decimals)
            target_low   = round(target_price * 0.97, decimals)
            target_high  = round(target_price * 1.03, decimals)

            # For BULLISH: conservative bound is target_low (3% below target).
            # For BEARISH: conservative bound is target_high (3% above target — closer to entry).
            conservative_target = target_high if direction == "BEARISH" else target_low
            profit_pct    = abs(conservative_target - price) / price * 100 if price > 0 else 0
            passed_filter = profit_pct >= MIN_PROFIT_PCT

            claude_raw_log.append({
                "ticker": ticker, "pipeline": pipeline, "score": item["score"], "price": price,
                "direction": direction, "position": position, "confidence": confidence,
                "raw_target": raw_target, "raw_stop": raw_stop,
                "used_target": target_price, "used_stop": stop_price,
                "profit_pct": round(profit_pct, 2), "passed_filter": passed_filter,
                "days_to_target": ai.get("days_to_target"),
                "reasoning": ai.get("reasoning", ""),
                "key_signals": ai.get("key_signals", []),
            })

            if not passed_filter:
                print(f"  {ticker} skipped — profit {profit_pct:.1f}% < {MIN_PROFIT_PCT}%")
                return

            buy_low, buy_high = compute_buy_range(price, atr, direction)
            buy_window        = ai.get("buy_window") or compute_buy_window(scan_mode, score_data["total"])

            days_to_target = ai.get("days_to_target")
            if not days_to_target or days_to_target <= 0:
                dist = abs(target_price - price)
                days_to_target = max(60 if scan_mode == "long" else 2, round(dist / atr))

            timeframe  = _bucket(days_to_target)
            expires_on = (start_time + timedelta(days=round(days_to_target * 1.2))).isoformat()

            ec = item.get("earnings_calendar") or {}
            if ec.get("has_upcoming"):
                days_e = ec.get("days_to_earnings", 0)
                earnings_label = ("⚡ EARNINGS TODAY" if days_e == 0
                                  else "⚡ EARNINGS TOMORROW" if days_e == 1
                                  else f"⚡ EARNINGS IN {days_e} DAYS")
            else:
                earnings_label = ""

            ib = item.get("insider_buying") or {}
            if ib.get("has_insider_buying"):
                strength  = ib.get("signal_strength", "")
                total_usd = ib.get("total_purchased_usd", 0)
                total_str = f"${total_usd/1e6:.1f}M" if total_usd >= 1_000_000 else f"${total_usd/1e3:.0f}K"
                insider_signal = f"👤 INSIDER BUY {total_str} ★" if strength == "STRONG" else f"👤 INSIDER BUY {total_str}"
            else:
                insider_signal = ""

            if pipeline == "bullish":
                fv = LONG_BULLISH_FORMULA_VERSION if scan_mode == "long" else BULLISH_FORMULA_VERSION
            else:
                fv = LONG_BEARISH_FORMULA_VERSION if scan_mode == "long" else BEARISH_FORMULA_VERSION
            pred = {
                "ticker":              ticker,
                "asset_class":         get_asset_class(ticker),
                "company_name":        item.get("company_name", ticker),
                "predicted_on":        start_time.isoformat(),
                "expires_on":          expires_on,
                "days_to_target":      days_to_target,
                "timing_rationale":    ai.get("timing_rationale", ""),
                "timeframe":           timeframe,
                "direction":           direction,
                "position":            position,
                "confidence":          confidence,
                "score":               score_data["total"],
                "price_at_prediction": price,
                "buy_range_low":       buy_low,
                "buy_range_high":      buy_high,
                "target_low":          target_low,
                "target_high":         target_high,
                "stop_loss":           stop_price,
                "reasoning":           ai.get("reasoning", ""),
                "source":              item["source"],
                "formula_version":     fv,
                "outcome":             "PENDING",
                "earnings_label":      earnings_label or None,
                "insider_signal":      insider_signal or None,
                "market_cap":          item.get("market_cap") or None,
                "avg_volume":          item.get("avg_volume") or None,
            }

            action = replace_prediction_if_stronger(ticker, profit_pct, pred)
            if action == "skipped":
                print(f"  {ticker} skipped — existing prediction is stronger or equal")
                return
            if action == "replaced":
                print(f"  {ticker} replaced — new prediction stronger (+{profit_pct:.1f}%)")

            saved = insert_prediction(pred)
            pred["id"]         = saved.get("id")
            pred["buy_window"] = buy_window
            pred["buy_low"]    = buy_low
            pred["buy_high"]   = buy_high
            all_predictions.append(pred)
            scan_stats["predictions_created"] += 1
            print(f"  [{pipeline}] {ticker} ({item['company_name']}) → {direction} {timeframe}-term, {days_to_target}d, {confidence}% conf")

            try:
                from services.analyst_service import save_articles_for_prediction
                articles = item.get("sentiment", {}).get("articles", [])
                if articles and pred["id"]:
                    save_articles_for_prediction(pred["id"], ticker, articles, pred["predicted_on"])
            except Exception as e:
                log_error("scanner", f"Analyst article save failed {ticker}: {e}", level="WARNING")

        except Exception as e:
            scan_stats["errors_encountered"] += 1
            log_error("scanner", f"Prediction error {ticker}: {e}", detail=str(e), ticker=ticker)
            print(f"  Error on {ticker}: {e}")

    print(f"Running Claude: {len(top_bullish)} bullish predictions...")
    for item in top_bullish:
        _run_claude_prediction(item, "bullish")

    print(f"Running Claude: {len(top_bearish)} bearish predictions...")
    for item in top_bearish:
        _run_claude_prediction(item, "bearish")

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
            direction_counts=ranked.get("direction_counts"),
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
        set_cache(f"claude_raw_{date_str}", raw_payload, ttl_hours=CLAUDE_LOG_CACHE_TTL_H)
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


def _get_ticker_history(ticker: str, direction: str = "") -> str:
    try:
        from database.db import get_predictions
        preds = get_predictions({"ticker": ticker}, limit=20)
        closed = [p for p in preds if p.get("outcome") in ("WIN", "LOSS")]
        # Filter to matching direction so bearish Claude gets bearish track record only
        if direction:
            closed = [p for p in closed if p.get("direction") == direction]
        if len(closed) < 3:
            return ""
        wins = sum(1 for p in closed if p["outcome"] == "WIN")
        pct = wins / len(closed) * 100
        dir_label = f" {direction.lower()}" if direction else ""
        return f"{ticker}{dir_label}: {wins}/{len(closed)} wins ({pct:.0f}%) — {'be cautious' if pct < 50 else 'reliable track record'}"
    except Exception:
        return ""


if __name__ == "__main__":
    run()
