import streamlit as st
from datetime import datetime, timedelta
import pytz

PT = pytz.timezone("America/Los_Angeles")


@st.cache_data(ttl=3600)
def _fetch_hot_tickers() -> list:
    from database.db import get_hot_tickers_from_db
    return get_hot_tickers_from_db()


@st.cache_data(ttl=3600)
def _fetch_earnings_calendar() -> list:
    from database.db import get_earnings_calendar_from_db
    return get_earnings_calendar_from_db()


@st.cache_data(ttl=3600)
def _fetch_scan_logs() -> list:
    from database.db import get_scan_logs
    return get_scan_logs(limit=1)


def render():
    st.title("🛠 Prediction Tool")
    now_pt = datetime.now(PT)
    st.caption(f"Last updated: {now_pt.strftime('%b %d, %Y  %I:%M %p PT')}")

    # ── Scanner buttons ────────────────────────────────────────────────────────
    btn_c1, btn_c2, btn_c3, btn_c4, btn_c5 = st.columns([2, 2, 2, 2, 2])
    with btn_c1:
        st.markdown('<div class="btn-api">', unsafe_allow_html=True)
        run_clicked = st.button("🚀 Run Nightly Scanner", key="pt_run_scanner")
    with btn_c2:
        st.markdown('<div class="btn-api">', unsafe_allow_html=True)
        debug_clicked = st.button("🐛 Run Nightly Scanner Debug", key="pt_run_scanner_debug")
    with btn_c3:
        st.markdown('<div class="btn-safe">', unsafe_allow_html=True)
        log_clicked = st.button("📋 Last Scan Raw Log", key="pt_view_raw_log")
    with btn_c4:
        st.markdown('<div class="btn-safe">', unsafe_allow_html=True)
        recalc_clicked = st.button("🔄 Re-calculate Math", key="pt_recalc_math")
    with btn_c5:
        st.markdown('<div class="btn-safe">', unsafe_allow_html=True)
        clear_clicked = st.button("🗑 Clear All Open Predictions", key="pt_clear_predictions")

    if run_clicked:
        _trigger_scanner()
    elif debug_clicked:
        _trigger_scanner(debug=True)
    elif log_clicked:
        _show_raw_log()
    elif recalc_clicked:
        _recalculate_open_math()
    elif clear_clicked:
        if st.session_state.get("pt_confirm_clear"):
            _clear_open_predictions()
        else:
            st.session_state["pt_confirm_clear"] = True
            st.warning("Click again to confirm — this will remove all open predictions.")
            st.rerun()

    st.markdown("---")

    # ── Last scan info ─────────────────────────────────────────────────────────
    try:
        scan_logs = _fetch_scan_logs()
        if scan_logs:
            log = scan_logs[0]
            st.info(
                f"Last scan — Universe: **{log.get('universe_total','—')} stocks**  ·  "
                f"{log.get('hot_stock_count','—')} hot + {log.get('nasdaq100_count','—')} Nasdaq earnings  ·  "
                f"{log.get('overlap_count','—')} overlap  ·  "
                f"{log.get('predictions_created','—')} predictions created"
            )
    except Exception:
        pass

    # ── Hot 50 ─────────────────────────────────────────────────────────────────
    with st.expander("🔥 Today's Hot 50 (from market news)", expanded=False):
        try:
            rows = _fetch_hot_tickers()
            if rows:
                scanned_at = rows[0].get("scanned_at", "")
                try:
                    ts = datetime.fromisoformat(scanned_at.replace("Z", "+00:00")).astimezone(PT).strftime("%b %d  %I:%M %p PT")
                except Exception:
                    ts = scanned_at[:10]
                st.caption(f"From nightly scan · {ts}")
                tickers = [r["ticker"] for r in rows]
                cols = st.columns(10)
                for i, ticker in enumerate(tickers):
                    cols[i % 10].markdown(
                        f'<span style="background:#f1f5f9;border:1px solid #e2e8f0;'
                        f'border-radius:6px;padding:3px 8px;font-size:12px;'
                        f'font-weight:600;color:#1e293b">{ticker}</span>',
                        unsafe_allow_html=True,
                    )
            else:
                st.caption("No data yet — will populate after the next nightly scan.")
        except Exception as e:
            st.error(f"Could not load hot tickers: {e}")

    # ── Earnings calendar ──────────────────────────────────────────────────────
    with st.expander("📅 Earnings Next 2 Weeks", expanded=False):
        try:
            rows = _fetch_earnings_calendar()
            if rows:
                scanned_at = rows[0].get("scanned_at", "")
                try:
                    ts = datetime.fromisoformat(scanned_at.replace("Z", "+00:00")).astimezone(PT).strftime("%b %d  %I:%M %p PT")
                except Exception:
                    ts = scanned_at[:10]
                st.caption(f"Stocks reporting earnings in the next 14 days · fetched {ts}")

                from collections import defaultdict
                by_day = defaultdict(list)
                for r in rows:
                    by_day[r.get("days_to_earnings", 99)].append(r)

                for days in sorted(by_day.keys()):
                    day_rows = by_day[days]
                    label = "📌 Today" if days == 0 else "📌 Tomorrow" if days == 1 else f"In {days} days"
                    st.markdown(f"**{label}**")
                    cols = st.columns(10)
                    for i, row in enumerate(day_rows):
                        ticker = row["ticker"]
                        cols[i % 10].markdown(
                            f'<span style="background:#fefce8;border:1px solid #fde68a;'
                            f'border-radius:6px;padding:3px 8px;font-size:12px;'
                            f'font-weight:600;color:#92400e">{ticker}</span>',
                            unsafe_allow_html=True,
                        )
            else:
                st.caption("No data yet — will populate after the next nightly scan.")
        except Exception as e:
            st.error(f"Could not load earnings calendar: {e}")

    st.markdown("---")

    # ── Manual Prediction ──────────────────────────────────────────────────────
    st.markdown("### 🎯 Manual Prediction")
    st.caption("Generate a prediction for any stock — no score or price filters applied.")

    POPULAR = ["AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","GLD","BTC-USD","ETH-USD",
               "SPY","QQQ","PLTR","AMD","NFLX","CRM","ORCL","UBER","SHOP","COIN"]

    col1, col2 = st.columns([3, 1])
    with col1:
        manual_ticker = st.selectbox(
            "Ticker", options=[""] + POPULAR, index=0,
            key="pt_manual_ticker_select",
            help="Select from list or type any ticker symbol"
        )
        custom_ticker = st.text_input(
            "Or enter any ticker", placeholder="e.g. GLD, BRK-B, SOL-USD",
            key="pt_manual_ticker_input"
        ).strip().upper()
        ticker = custom_ticker or manual_ticker
    with col2:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown('<div class="btn-api">', unsafe_allow_html=True)
        run_manual = st.button("🔍 Generate", key="pt_manual_predict_btn", disabled=not ticker)

    if run_manual and ticker:
        _run_manual_prediction(ticker)


def _run_manual_prediction(ticker: str):
    import sys, os, warnings
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    status = st.status(f"Analyzing {ticker}...", expanded=True)
    try:
        from services.yfinance_service import get_price_history, get_ticker_info, get_fundamentals
        from services.social_service import get_social_velocity
        from services.finnhub_service import get_news_sentiment, get_social_sentiment, get_analyst_recommendation, get_earnings_history, get_analyst_price_target
        from services.edgar_service import get_insider_buying
        from indicators.technicals import compute_all
        from indicators.scoring import compute_signal_score, compute_buy_range, FORMULA_VERSION
        from services.ai_service import analyze_stock
        from services.screener_service import get_asset_class
        from database.db import insert_prediction
        start_time = datetime.now(PT)

        status.write(f"Fetching price history for {ticker}...")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            df = get_price_history(ticker, period="6mo")
        if df.empty:
            status.update(label=f"❌ No price data found for {ticker}", state="error")
            return

        ind = compute_all(df)
        if not ind:
            status.update(label=f"❌ Could not compute indicators for {ticker}", state="error")
            return

        status.write("Fetching sentiment, analyst data, insider buying, earnings, fundamentals, social velocity...")
        sentiment      = get_news_sentiment(ticker, hours=48)
        social         = get_social_sentiment(ticker)
        sentiment["mentions"] = social.get("mentions", 0)
        analyst        = get_analyst_recommendation(ticker)
        earnings       = get_earnings_history(ticker)
        analyst_target = get_analyst_price_target(ticker)
        insider_buying = get_insider_buying(ticker)
        fundamentals   = get_fundamentals(ticker)
        social_vel     = get_social_velocity(ticker)
        info           = get_ticker_info(ticker)

        from database.db import get_earnings_calendar_from_db
        ec_rows = {row["ticker"]: row for row in get_earnings_calendar_from_db()}
        ec_data = ec_rows.get(ticker.upper())
        earnings_calendar = (
            {"has_upcoming": True, "days_to_earnings": ec_data["days_to_earnings"], "earnings_date": ec_data["earnings_date"]}
            if ec_data else {"has_upcoming": False}
        )

        score_data = compute_signal_score(
            ind, sentiment, analyst, earnings,
            analyst_target=analyst_target, insider_buying=insider_buying,
            earnings_calendar=earnings_calendar, fundamentals=fundamentals,
            social_velocity=social_vel,
        )

        status.write(f"Score: {score_data['total']}/100 — sending to Claude...")
        ai = analyze_stock(ticker, ind, sentiment, analyst, score_data,
                           earnings_calendar=earnings_calendar,
                           analyst_upside_pct=score_data.get("analyst_upside_pct"),
                           insider_buying=insider_buying,
                           fundamentals=fundamentals,
                           social_velocity=social_vel)

        direction  = ai.get("direction", "NEUTRAL")
        confidence = ai.get("confidence", 50)
        price      = ind.get("price", 0)
        atr        = ind.get("atr", price * 0.02) or (price * 0.02)

        target_price = ai.get("target_price") or round(price + atr * 1.5, 2)
        stop_price   = ai.get("stop_price") or round(price * 0.98, 2)
        decimals     = 6 if price < 1 else 4 if price < 10 else 2
        target_price = round(float(target_price), decimals)
        stop_price   = round(float(stop_price), decimals)
        target_low   = round(target_price * 0.97, decimals)
        target_high  = round(target_price * 1.03, decimals)

        days_to_target = ai.get("days_to_target") or max(2, round(abs(target_price - price) / atr))
        expires_on     = (start_time + timedelta(days=round(days_to_target * 1.2))).isoformat()
        timeframe      = "short" if days_to_target <= 10 else "medium" if days_to_target <= 35 else "long"
        buy_low, buy_high = compute_buy_range(price, atr, direction)
        profit_pct     = abs(target_low - price) / price * 100 if price > 0 else 0

        pred = {
            "ticker":              ticker,
            "asset_class":         get_asset_class(ticker),
            "company_name":        info.get("name", ticker),
            "predicted_on":        start_time.isoformat(),
            "expires_on":          expires_on,
            "days_to_target":      days_to_target,
            "timing_rationale":    ai.get("timing_rationale", ""),
            "timeframe":           timeframe,
            "direction":           direction,
            "position":            ai.get("position", "HOLD"),
            "confidence":          confidence,
            "score":               score_data["total"],
            "price_at_prediction": price,
            "buy_range_low":       buy_low,
            "buy_range_high":      buy_high,
            "target_low":          target_low,
            "target_high":         target_high,
            "stop_loss":           stop_price,
            "reasoning":           ai.get("reasoning", ""),
            "source":              "manual",
            "formula_version":     FORMULA_VERSION,
            "outcome":             "PENDING",
            "market_cap":          info.get("market_cap") or None,
            "avg_volume":          info.get("avg_volume") or None,
        }

        insert_prediction(pred)
        status.update(
            label=f"✅ {ticker} — {direction} · {confidence}% conf · {profit_pct:.1f}% potential · ~{days_to_target}d",
            state="complete", expanded=True
        )
        status.write(f"**Reasoning:** {ai.get('reasoning', '')}")
        st.rerun()

    except Exception as e:
        status.update(label=f"❌ Failed: {e}", state="error", expanded=True)


def _recalculate_open_math():
    from views.main_dashboard import _fetch_open_predictions
    status = st.status("Recalculating Math on predictions…", expanded=True)
    try:
        from database.db import get_predictions, update_prediction
        from views.main_dashboard import _calc_entry, _calc_profit_pct
        all_preds = get_predictions({"outcome": "PENDING"}, limit=200)
        status.write(f"Found {len(all_preds)} open predictions to recalculate…")
        updated = skipped = 0
        for p in all_preds:
            entry = _calc_entry(p)
            if entry <= 0:
                skipped += 1
                continue
            tgt_low  = p.get("target_low") or 0
            tgt_high = p.get("target_high") or 0
            tgt_mid  = (tgt_low + tgt_high) / 2 if tgt_low > 0 and tgt_high > 0 else tgt_low
            if tgt_mid <= 0:
                skipped += 1
                continue
            try:
                update_prediction(p["id"], {"price_at_prediction": round(entry, 6)})
                updated += 1
                status.write(f"  {p['ticker']}: entry=${entry:.2f}")
            except Exception as e:
                status.write(f"  {p['ticker']}: error — {e}")
                skipped += 1
        status.update(label=f"Done — {updated} updated, {skipped} skipped", state="complete", expanded=False)
        _fetch_open_predictions.clear()
        st.rerun()
    except Exception as e:
        status.update(label=f"Error: {e}", state="error", expanded=True)


def _clear_open_predictions():
    from views.main_dashboard import _fetch_open_predictions
    status = st.status("Clearing open predictions…", expanded=True)
    try:
        from database.db import bulk_delete_open_predictions
        import builtins
        _orig = builtins.print
        builtins.print = lambda *a, **k: (status.write(" ".join(str(x) for x in a)), _orig(*a, **k))
        try:
            count = bulk_delete_open_predictions()
        finally:
            builtins.print = _orig
        status.update(label=f"✅ Cleared {count} predictions!", state="complete", expanded=False)
        st.success(f"Removed {count} open predictions.")
        st.session_state["pt_confirm_clear"] = False
        st.session_state["_open_deleted"] = set()
        _fetch_open_predictions.clear()
        _fetch_scan_logs.clear()
        st.rerun()
    except Exception as e:
        status.update(label="❌ Failed", state="error", expanded=True)
        st.error(f"Error: {e}")


def _trigger_scanner(debug: bool = False):
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from views.main_dashboard import _fetch_open_predictions, _fetch_scan_logs as _mds_scan_logs

    label = "Running scanner (debug mode)…" if debug else "Running scanner…"
    status = st.status(label, expanded=True)
    try:
        import scripts.nightly_scanner as scanner
        import importlib
        importlib.reload(scanner)
        import builtins
        _orig = builtins.print
        builtins.print = lambda *a, **k: (status.write(" ".join(str(x) for x in a)), _orig(*a, **k))
        try:
            stats = scanner.run(debug=debug)
        finally:
            builtins.print = _orig
        status.update(label="✅ Done!", state="complete", expanded=False)
        st.success(f"{stats.get('predictions_created', 0)} predictions created")

        _save_debug_log(stats.get("claude_raw_log", []))

        _fetch_open_predictions.clear()
        _mds_scan_logs.clear()
        _fetch_hot_tickers.clear()
        _fetch_earnings_calendar.clear()
        _fetch_scan_logs.clear()
        st.session_state["_open_deleted"] = set()
        st.rerun()
    except Exception as e:
        status.update(label="❌ Failed", state="error", expanded=True)
        st.error(f"Scanner error: {e}")


def _show_raw_log():
    from database.db import get_cache
    date_str = datetime.now(PT).strftime("%Y-%m-%d")
    data = get_cache(f"claude_raw_{date_str}")
    if not data:
        yesterday = (datetime.now(PT) - timedelta(days=1)).strftime("%Y-%m-%d")
        data = get_cache(f"claude_raw_{yesterday}")
        if data:
            st.info(f"No log for today yet — showing yesterday ({yesterday})")
        else:
            st.warning("No raw scan log found. Run the scanner first.")
            return

    responses = data.get("responses", [])
    total  = data.get("total_calls", 0)
    passed = data.get("passed_filter", 0)
    st.markdown(f"### 📋 Raw Scan Log — {data.get('scan_date')}  ({total} Claude calls · {passed} passed filter)")

    for r in sorted(responses, key=lambda x: x.get("score", 0), reverse=True):
        ticker    = r.get("ticker", "")
        score     = r.get("score", 0)
        direction = r.get("direction", "")
        profit    = r.get("profit_pct", 0)
        passed_f  = r.get("passed_filter", False)
        reasoning = r.get("reasoning", "")
        key_sigs  = r.get("key_signals", [])
        status_str = "✅ SAVED" if passed_f else "❌ filtered"
        dir_color  = "green" if direction == "BULLISH" else "red" if direction == "BEARISH" else "grey"
        with st.expander(f"{status_str}  **{ticker}**  score={score}  :{dir_color}[{direction}]  profit={profit:.1f}%", expanded=False):
            st.write(f"**Target:** ${r.get('used_target')}  **Stop:** ${r.get('used_stop')}  **Confidence:** {r.get('confidence')}%")
            if key_sigs:
                st.write(f"**Key signals:** {', '.join(key_sigs)}")
            if reasoning:
                st.caption(reasoning)


def _save_debug_log(raw_log: list):
    import json, base64, requests, os
    if not raw_log:
        st.warning("No raw Claude data to save.")
        return

    token = os.environ.get("GITHUB_TOKEN", "")
    repo  = os.environ.get("GITHUB_REPO", "")
    if not token or not repo:
        st.error("GITHUB_TOKEN / GITHUB_REPO secrets not set — cannot save debug log.")
        return

    date_str  = datetime.now(PT).strftime("%Y-%m-%d")
    file_path = f"debug/claude_raw_{date_str}.json"
    content   = json.dumps({
        "scan_date":     date_str,
        "total_calls":   len(raw_log),
        "passed_filter": sum(1 for r in raw_log if r.get("passed_filter")),
        "responses":     raw_log,
    }, indent=2)

    api     = f"https://api.github.com/repos/{repo}/contents/{file_path}"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}

    sha = None
    try:
        r = requests.get(api, headers=headers)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except Exception:
        pass

    payload = {"message": f"debug: claude raw responses {date_str}",
               "content": base64.b64encode(content.encode()).decode()}
    if sha:
        payload["sha"] = sha

    try:
        r = requests.put(api, headers=headers, json=payload)
        r.raise_for_status()
        st.success(f"✅ Debug log saved → `{file_path}` on GitHub")
    except Exception as e:
        st.error(f"Failed to save debug log: {e}")
