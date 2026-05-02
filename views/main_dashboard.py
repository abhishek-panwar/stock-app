import streamlit as st
from datetime import datetime, timedelta
import pytz

PT = pytz.timezone("America/Los_Angeles")

DIR_COLORS = {
    "BULLISH": ("#f0fdf4", "#16a34a", "#15803d"),
    "BEARISH": ("#fef2f2", "#dc2626", "#b91c1c"),
    "NEUTRAL": ("#f8fafc", "#94a3b8", "#64748b"),
}


@st.cache_data(ttl=3600)
def _fetch_open_predictions() -> list:
    from database.db import get_predictions
    return get_predictions({"outcome": "PENDING"}, limit=200)

@st.cache_data(ttl=3600)
def _fetch_scan_logs() -> list:
    from database.db import get_scan_logs
    return get_scan_logs(limit=1)

@st.cache_data(ttl=3600)
def _fetch_hot_tickers() -> list:
    from database.db import get_hot_tickers_from_db
    return get_hot_tickers_from_db()

@st.cache_data(ttl=3600)
def _fetch_earnings_calendar() -> list:
    from database.db import get_earnings_calendar_from_db
    return get_earnings_calendar_from_db()



def _age_info(predicted_on: str):
    try:
        pred_dt = datetime.fromisoformat(predicted_on.replace("Z", "+00:00")).astimezone(PT)
        today_pt = datetime.now(PT).date()
        from datetime import timedelta
        eff_date = pred_dt.date() + timedelta(days=1) if pred_dt.hour >= 16 else pred_dt.date()
        age = (today_pt - eff_date).days
    except Exception:
        return 0, ""
    if age < 0:
        return age, f'<span style="background:#eff6ff;color:#1d4ed8;border-radius:20px;padding:2px 9px;font-size:11px;font-weight:600">for tomorrow</span>'
    if age == 0:
        return age, f'<span style="background:#fef9c3;color:#713f12;border-radius:20px;padding:2px 9px;font-size:11px;font-weight:600">today</span>'
    return age, f'<span style="background:#f1f5f9;color:#64748b;border-radius:20px;padding:2px 9px;font-size:11px;font-weight:500">{age}d old</span>'


def _calc_entry(p: dict) -> float:
    """Mid of buy range, falls back to price_at_prediction."""
    bl = p.get("buy_range_low") or 0
    bh = p.get("buy_range_high") or 0
    return (bl + bh) / 2 if bl > 0 and bh > 0 else (p.get("price_at_prediction") or 0)


def _calc_profit_pct(p: dict) -> float:
    """Profit potential using mid buy range and mid target range."""
    entry    = _calc_entry(p)
    tgt_low  = p.get("target_low") or 0
    tgt_high = p.get("target_high") or 0
    tgt_mid  = (tgt_low + tgt_high) / 2 if tgt_low > 0 and tgt_high > 0 else tgt_low
    if entry <= 0 or tgt_mid <= 0:
        return 0.0
    direction = p.get("direction", "NEUTRAL")
    if direction == "BEARISH":
        return (entry - tgt_mid) / entry * 100
    return (tgt_mid - entry) / entry * 100


def _sort_key(p: dict):
    try:
        pred_dt = datetime.fromisoformat(p.get("predicted_on", "").replace("Z", "+00:00")).astimezone(PT)
        age = (datetime.now(PT).date() - pred_dt.date()).days
    except Exception:
        age = 999
    profit = _calc_profit_pct(p)
    return (age, -abs(profit), -p.get("score", 0))


def _expiry(p: dict):
    raw = p.get("expires_on") or ""
    if not raw:
        return "—", None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(PT)
        days_left = (dt.date() - datetime.now(PT).date()).days
        return dt.strftime("%b %d"), days_left
    except Exception:
        return "—", None


def _recalculate_open_math():
    status = st.status("Recalculating Math on predictions…", expanded=True)
    try:
        from database.db import get_predictions, update_prediction
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
            stop     = p.get("stop_loss") or 0
            direction = p.get("direction", "NEUTRAL")
            if tgt_mid <= 0:
                skipped += 1
                continue
            profit_pct = (tgt_mid - entry) / entry * 100 if direction != "BEARISH" \
                else (entry - tgt_mid) / entry * 100
            try:
                update_prediction(p["id"], {"price_at_prediction": round(entry, 6)})
                updated += 1
                status.write(f"  {p['ticker']} ({direction}): entry=${entry:.2f}  potential={profit_pct:+.1f}%")
            except Exception as e:
                status.write(f"  {p['ticker']}: error — {e}")
                skipped += 1
        status.update(
            label=f"Done — {updated} updated, {skipped} skipped",
            state="complete", expanded=False,
        )
        st.rerun()
    except Exception as e:
        status.update(label=f"Error: {e}", state="error", expanded=True)


def render():
    st.title("📊 Open Predictions")
    now_pt = datetime.now(PT)
    st.caption(f"Last updated: {now_pt.strftime('%b %d, %Y  %I:%M %p PT')}")

    # ── Scanner buttons ───────────────────────────────────────────────────────
    btn_c1, btn_c2, btn_c3, btn_c4, btn_c5 = st.columns([2, 2, 2, 2, 2])
    with btn_c1:
        st.markdown('<div class="btn-api">', unsafe_allow_html=True)
        run_clicked = st.button("🚀 Run Nightly Scanner", key="run_scanner_top")
    with btn_c2:
        st.markdown('<div class="btn-api">', unsafe_allow_html=True)
        debug_clicked = st.button("🐛 Run Nightly Scanner Debug", key="run_scanner_debug_top")
    with btn_c3:
        st.markdown('<div class="btn-safe">', unsafe_allow_html=True)
        log_clicked = st.button("📋 Last Scan Raw Log", key="view_raw_log_top")
    with btn_c4:
        st.markdown('<div class="btn-safe">', unsafe_allow_html=True)
        recalc_clicked = st.button("🔄 Re-calculate Math on predictions", key="recalc_math_top")
    with btn_c5:
        st.markdown('<div class="btn-safe">', unsafe_allow_html=True)
        clear_clicked = st.button("🗑 Clear All Open Predictions", key="clear_predictions_top")

    # Handle button actions outside column context so st.status() renders correctly
    if run_clicked:
        _trigger_scanner()
    elif debug_clicked:
        _trigger_scanner(debug=True)
    elif log_clicked:
        _show_raw_log()
    elif recalc_clicked:
        _recalculate_open_math()
    elif clear_clicked:
        if st.session_state.get("confirm_clear"):
            _clear_open_predictions()
        else:
            st.session_state["confirm_clear"] = True
            st.warning("Click again to confirm — this will remove all open predictions.")
            st.rerun()

    try:
        predictions = _fetch_open_predictions()
        scan_logs   = _fetch_scan_logs()
    except Exception as e:
        st.error(f"Database connection error: {e}")
        _show_empty_state()
        return

    # Apply in-session deletes so removed predictions disappear instantly
    deleted = st.session_state.get("_open_deleted", set())
    if deleted:
        predictions = [p for p in predictions if p.get("id") not in deleted]


    if not predictions:
        _show_empty_state()
        return

    if scan_logs:
        log = scan_logs[0]
        st.info(
            f"Universe: **{log.get('universe_total','—')} stocks** scanned  ·  "
            f"{log.get('hot_stock_count','—')} hot (Yahoo + Alpha Vantage) + "
            f"{log.get('nasdaq100_count','—')} Nasdaq with earnings  ·  "
            f"{log.get('overlap_count','—')} overlap deduplicated"
        )

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

                # Group by days_to_earnings
                from collections import defaultdict
                by_day = defaultdict(list)
                for r in rows:
                    by_day[r.get("days_to_earnings", 99)].append(r)

                for days in sorted(by_day.keys()):
                    day_rows = by_day[days]
                    if days == 0:
                        label = "📌 Today"
                    elif days == 1:
                        label = "📌 Tomorrow"
                    else:
                        label = f"In {days} days"
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

    high_conviction = sorted(
        [p for p in predictions if (p.get("confidence") or 0) >= 75],
        key=_sort_key
    )

    st.markdown("---")

    # ── High conviction picks ─────────────────────────────────────────────────
    if high_conviction:
        st.markdown("### 🎯 High Conviction Picks")
        chunks = [high_conviction[i:i+5] for i in range(0, len(high_conviction), 5)]
        for chunk in chunks:
            cols = st.columns(len(chunk))
            for col, p in zip(cols, chunk):
                ticker     = p.get("ticker", "—")
                direction  = p.get("direction", "NEUTRAL")
                profit_pct = _calc_profit_pct(p)
                days       = p.get("days_to_target", "?")
                company   = p.get("company_name") or ticker
                _, age_badge = _age_info(p.get("predicted_on", ""))
                tf_label  = {"short": "⚡ Short", "medium": "📈 Mid", "long": "🌱 Long"}.get(p.get("timeframe", ""), "")
                conf      = p.get("confidence", 0)

                if direction == "BULLISH":
                    card_bg    = "linear-gradient(145deg,#f0fdf4,#dcfce7)"
                    border_col = "#16a34a"
                    glow       = "rgba(22,163,74,0.12)"
                    dir_color  = "#15803d"
                    dir_icon   = "▲"
                elif direction == "BEARISH":
                    card_bg    = "linear-gradient(145deg,#fef2f2,#fee2e2)"
                    border_col = "#dc2626"
                    glow       = "rgba(220,38,38,0.12)"
                    dir_color  = "#b91c1c"
                    dir_icon   = "▼"
                else:
                    card_bg    = "linear-gradient(145deg,#f8fafc,#f1f5f9)"
                    border_col = "#94a3b8"
                    glow       = "rgba(71,85,105,0.1)"
                    dir_color  = "#475569"
                    dir_icon   = "●"

                profit_color = "#15803d" if profit_pct >= 0 else "#b91c1c"
                profit_str   = f"+{profit_pct:.1f}%" if profit_pct >= 0 else f"{profit_pct:.1f}%"

                with col:
                    st.markdown(
                        f"""<div style="background:{card_bg};border:1.5px solid {border_col};
                            border-radius:12px;padding:14px 14px 12px;
                            box-shadow:0 4px 20px {glow};position:relative;overflow:hidden">
                          <div style="font-size:20px;font-weight:800;color:#0f172a;letter-spacing:-0.5px">{ticker}</div>
                          <div style="font-size:11px;color:#64748b;margin-bottom:6px;white-space:nowrap;
                              overflow:hidden;text-overflow:ellipsis">{company}</div>
                          <div style="font-size:12px;font-weight:700;color:{dir_color};margin-bottom:4px">
                              {dir_icon} {direction} · {tf_label}
                          </div>
                          <div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px">
                            <span style="font-size:18px;font-weight:800;color:{profit_color}">{profit_str}</span>
                            <span style="background:rgba(0,0,0,0.06);border-radius:8px;padding:2px 8px;
                                font-size:11px;color:#475569">~{days}d</span>
                          </div>
                          <div style="font-size:11px;color:#64748b;margin-top:4px">{conf}% conf</div>
                          <div style="margin-top:6px">{age_badge}{_asset_badge(p)}</div>
                        </div>""",
                        unsafe_allow_html=True,
                    )
        st.markdown("")

    # ── Sort control ──────────────────────────────────────────────────────────
    SORT_OPTIONS = {
        "Profit % (default)": lambda p: -abs(_calc_profit_pct(p)),
        "Confidence":         lambda p: -(p.get("confidence") or 0),
        "Score":              lambda p: -(p.get("score") or 0),
        "Risk/Reward": lambda p: -(
            abs(((p.get("target_low") or 0) + (p.get("target_high") or 0)) / 2 - _calc_entry(p)) /
            abs(_calc_entry(p) - (p.get("stop_loss") or _calc_entry(p)) or 1)
            if _calc_entry(p) > 0 and (p.get("stop_loss") or 0) > 0 else 0
        ),
        "Days to target":     lambda p: (p.get("days_to_target") or 999),
        "Newest first":       lambda p: p.get("predicted_on", ""),
    }
    sort_col, _ = st.columns([2, 8])
    with sort_col:
        sort_by = st.selectbox("Sort by", list(SORT_OPTIONS.keys()), key="open_sort_by", label_visibility="collapsed")
    sort_fn = SORT_OPTIONS[sort_by]

    # ── Timeframe + date-grouped prediction sections ──────────────────────────
    today_pt = datetime.now(PT).date()

    def _pred_date(p):
        try:
            return datetime.fromisoformat(p.get("predicted_on", "").replace("Z", "+00:00")).astimezone(PT).date()
        except Exception:
            return today_pt

    MARKET_CLOSE_HOUR = 16  # 4 PM PT

    def _effective_date(p):
        """Predictions made after 4 PM PT are for the next trading day."""
        try:
            pred_dt = datetime.fromisoformat(p.get("predicted_on", "").replace("Z", "+00:00")).astimezone(PT)
            if pred_dt.hour >= MARKET_CLOSE_HOUR:
                from datetime import timedelta
                return pred_dt.date() + timedelta(days=1)
            return pred_dt.date()
        except Exception:
            return today_pt

    def _date_bucket(p):
        eff = _effective_date(p)
        delta = (today_pt - eff).days
        if delta < 0:    return "📅 Tomorrow"
        if delta == 0:   return "✨ Today"
        if delta == 1:   return "Yesterday"
        if delta <= 7:   return "This Week"
        if delta <= 30:  return "This Month"
        return "Older"

    DATE_ORDER = ["📅 Tomorrow", "✨ Today", "Yesterday", "This Week", "This Month", "Older"]

    TF_CONFIG = [
        ("short",  "⚡ Short-term",  "#0369a1"),
        ("medium", "📈 Medium-term", "#7c3aed"),
        ("long",   "🌱 Long-term",   "#15803d"),
    ]

    for tf_key, tf_label, tf_color in TF_CONFIG:
        tf_preds = [p for p in predictions if p.get("timeframe") == tf_key]
        if not tf_preds:
            continue

        tf_hc = sum(1 for p in tf_preds if (p.get("confidence") or 0) >= 75)
        hc_badge = f"  · 🎯 {tf_hc} high conviction" if tf_hc else ""
        st.markdown(
            f'<div style="font-size:15px;font-weight:700;color:{tf_color};'
            f'margin:18px 0 6px;padding-left:2px">'
            f'{tf_label} — {len(tf_preds)} prediction{"s" if len(tf_preds) != 1 else ""}{hc_badge}</div>',
            unsafe_allow_html=True,
        )

        # sub-group by date within this timeframe
        date_groups: dict = {k: [] for k in DATE_ORDER}
        for p in tf_preds:
            date_groups[_date_bucket(p)].append(p)

        for bucket_label in DATE_ORDER:
            bucket_preds = date_groups[bucket_label]
            if not bucket_preds:
                continue
            bucket_preds = sorted(bucket_preds, key=sort_fn)
            with st.expander(
                f"**{bucket_label}** — {len(bucket_preds)} prediction{'s' if len(bucket_preds) != 1 else ''}",
                expanded=(bucket_label in ("📅 Tomorrow", "✨ Today")),
            ):
                for p in bucket_preds:
                    _prediction_card(p)

    if not predictions:
        _show_empty_state()

    # ── Manual Prediction ─────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 🎯 Manual Prediction")
    st.caption("Generate a prediction for any stock — no score or price filters applied.")

    POPULAR = ["AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","GLD","BTC-USD","ETH-USD",
               "SPY","QQQ","PLTR","AMD","NFLX","CRM","ORCL","UBER","SHOP","COIN"]

    col1, col2 = st.columns([3, 1])
    with col1:
        manual_ticker = st.selectbox(
            "Ticker", options=[""] + POPULAR, index=0,
            key="manual_ticker_select",
            help="Select from list or type any ticker symbol"
        )
        custom_ticker = st.text_input(
            "Or enter any ticker", placeholder="e.g. GLD, BRK-B, SOL-USD",
            key="manual_ticker_input"
        ).strip().upper()
        ticker = custom_ticker or manual_ticker
    with col2:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown('<div class="btn-api">', unsafe_allow_html=True)
        run_manual = st.button("🔍 Generate", key="manual_predict_btn", disabled=not ticker)

    if run_manual and ticker:
        _run_manual_prediction(ticker)


def _run_manual_prediction(ticker: str):
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import warnings

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
        from datetime import datetime, timedelta
        import pytz
        PT = pytz.timezone("America/Los_Angeles")
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
        _fetch_open_predictions.clear()
        st.rerun()

    except Exception as e:
        status.update(label=f"❌ Failed: {e}", state="error", expanded=True)




def _prediction_card(p: dict, _unused: set = None):
    ticker       = p.get("ticker", "—")
    direction    = p.get("direction", "NEUTRAL")
    confidence   = p.get("confidence", 0)
    score        = p.get("score", 0)
    position     = p.get("position", "HOLD")
    timeframe    = p.get("timeframe", "short")
    predicted_on = p.get("predicted_on", "")

    company = p.get("company_name") or ticker

    entry      = _calc_entry(p)
    tgt_low    = p.get("target_low") or 0
    tgt_high   = p.get("target_high") or 0
    tgt_mid    = (tgt_low + tgt_high) / 2 if tgt_low > 0 and tgt_high > 0 else tgt_low
    stop       = p.get("stop_loss") or 0
    profit_pct = _calc_profit_pct(p)
    rr = abs(tgt_mid - entry) / abs(entry - stop) if entry > 0 and stop > 0 and abs(entry - stop) > 0 else 0
    profit_str = f"+{profit_pct:.1f}%" if profit_pct > 0 else f"{profit_pct:.1f}%"

    expiry_str, days_left = _expiry(p)
    days_to_target = p.get("days_to_target")
    tenure_str = f"~{days_to_target}d" if days_to_target else "?"

    age_days, age_badge = _age_info(predicted_on)

    dir_icon  = "▲" if direction == "BULLISH" else "▼" if direction == "BEARISH" else "●"
    dir_color = "#15803d" if direction == "BULLISH" else "#b91c1c" if direction == "BEARISH" else "#475569"
    prof_color = "#15803d" if profit_pct > 0 else "#b91c1c"
    hc_tag    = "  🎯" if confidence >= 75 else ""
    pos_tag   = f"  ·  {position}" if position not in ("HOLD", "") else ""
    exp_tag   = (
        f"  ·  {days_left}d left" if days_left and days_left > 0
        else ("  ·  expired" if days_left is not None and days_left <= 0 else "")
    )

    pred_id = p.get("id") or f"{ticker}_{timeframe}_{predicted_on[:10]}"

    dir_circle = "🟢" if direction == "BULLISH" else "🔴" if direction == "BEARISH" else "⚪"
    header = (
        f"{dir_circle} **{ticker}** — {company}  ·  {dir_icon} {direction}  ·  "
        f"{confidence}% conf  ·  {profit_str} potential  ·  {tenure_str}"
        f"{pos_tag}{exp_tag}  ·  {age_days}d old{hc_tag}"
    )

    with st.expander(header, expanded=False):
        badge_html = _asset_badge(p)
        bcol, dcol = st.columns([9, 1])
        with bcol:
            if badge_html:
                st.markdown(f"<div style='margin-bottom:6px'>{badge_html}</div>", unsafe_allow_html=True)
        with dcol:
            if st.button("✕", key=f"del_{pred_id}", help="Delete prediction"):
                try:
                    from database.db import soft_delete_prediction
                    soft_delete_prediction(pred_id)
                    if "_open_deleted" not in st.session_state:
                        st.session_state["_open_deleted"] = set()
                    st.session_state["_open_deleted"].add(pred_id)
                    _fetch_open_predictions.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Delete failed: {e}")

        st.markdown(
            f"""<div style="display:flex;gap:6px;flex-wrap:wrap;margin:6px 0 10px;align-items:center">
            {_pill("Direction", f"{dir_icon} {direction}", dir_color)}
            {_pill("Confidence", f"{confidence}%", "#1d4ed8")}
            {_pill("Score", f"{score}/100", "#7c3aed")}
            {_pill("Profit", profit_str, prof_color)}
            {_pill("R/R", f"1:{rr:.1f}", "#b45309")}
            {_pill("Hold", tenure_str, "#0369a1")}
            {_pill("Position", position, "#374151")}
            <span style="margin-left:2px">{age_badge}</span>
            </div>""",
            unsafe_allow_html=True,
        )

        bl = p.get('buy_range_low', 0); bh = p.get('buy_range_high', 0)
        tl = p.get('target_low', 0);   th = p.get('target_high', 0)
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("**Entry**")
            st.write(f"Price at signal: ${entry:.2f}")
            st.write(f"Buy range: ${bl:.2f} – ${bh:.2f}")
            st.write(f"Stop loss: ${stop:.2f}")
            mcap = p.get("market_cap")
            avgvol = p.get("avg_volume")
            if mcap:
                mcap_str = f"${mcap/1e9:.1f}B" if mcap >= 1e9 else f"${mcap/1e6:.0f}M"
                st.write(f"Market cap: {mcap_str}")
            if avgvol:
                vol_str = f"{avgvol/1e6:.1f}M" if avgvol >= 1e6 else f"{avgvol/1e3:.0f}K"
                st.write(f"Avg volume: {vol_str}")
        with c2:
            st.markdown("**Target**")
            st.write(f"Range: ${tl:.2f} – ${th:.2f}")
            st.write(f"Profit potential: {profit_str}")
            st.write(f"Risk/Reward: 1 : {rr:.1f}")
            st.write(f"Score: {score}/100")
        with c3:
            st.markdown("**Timing**")
            try:
                pred_dt_str = datetime.fromisoformat(predicted_on.replace("Z", "+00:00")).astimezone(PT).strftime("%b %d  %I:%M %p PT")
            except Exception:
                pred_dt_str = "—"
            st.write(f"Predicted: {pred_dt_str}")
            st.write(f"Est. days to target: {days_to_target or '—'}")
            if expiry_str != "—":
                st.write(f"Expires: {expiry_str}{f'  ({days_left}d left)' if days_left and days_left > 0 else ''}")
            else:
                st.write("Expires: run scanner to populate")
            if p.get("timing_rationale"):
                st.caption(f"💡 {p['timing_rationale']}")

        if bl > 0 and bh > 0 and tl > 0:
            if direction == "BEARISH":
                formula_str = f"( ({bl:.2f}+{bh:.2f})/2 - ({tl:.2f}+{th:.2f})/2 ) / ({bl:.2f}+{bh:.2f})/2 = {profit_pct:+.1f}%"
            else:
                formula_str = f"( ({tl:.2f}+{th:.2f})/2 - ({bl:.2f}+{bh:.2f})/2 ) / ({bl:.2f}+{bh:.2f})/2 = {profit_pct:+.1f}%"
            st.markdown(f"**Profit formula:** `{formula_str}`")

        if p.get("reasoning"):
            st.markdown(
                f"""<div style="background:#f8fafc;border-left:3px solid #94a3b8;border-radius:0 6px 6px 0;
                padding:8px 12px;margin-top:8px;font-size:13px;color:#374151">{p['reasoning']}</div>""",
                unsafe_allow_html=True,
            )

        _news_links(ticker)

        if position == "SHORT":
            st.warning("SHORT position — margin/options account required")

        st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)
        ms_col, mf_col, _ = st.columns([1.5, 1.5, 7])
        with ms_col:
            if st.button("✅ Mark Success", key=f"win_{pred_id}"):
                try:
                    from database.db import update_prediction
                    update_prediction(pred_id, {
                        "outcome": "WIN",
                        "closed_reason": "MANUAL",
                        "verified_on": datetime.now(PT).isoformat(),
                    })
                    _fetch_open_predictions.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed: {e}")
        with mf_col:
            if st.button("❌ Mark Failure", key=f"loss_{pred_id}"):
                try:
                    from database.db import update_prediction
                    update_prediction(pred_id, {
                        "outcome": "LOSS",
                        "closed_reason": "MANUAL",
                        "verified_on": datetime.now(PT).isoformat(),
                    })
                    _fetch_open_predictions.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed: {e}")


def _news_links(ticker: str):
    with st.expander(f"📰 News & analysis for {ticker}", expanded=False):
        try:
            from services.finnhub_service import get_news_sentiment
            data = get_news_sentiment(ticker, hours=72)
            articles = data.get("articles", [])
        except Exception:
            articles = []

        if articles:
            for a in articles[:6]:
                headline = a.get("headline", "")[:90]
                url      = a.get("url", "")
                source   = a.get("source", "")
                ts       = a.get("datetime", 0)
                try:
                    date_str = datetime.fromtimestamp(ts, tz=PT).strftime("%b %d") if ts else ""
                except Exception:
                    date_str = ""
                meta = f'<span style="color:#94a3b8;font-size:11px;margin-left:6px">{source} · {date_str}</span>'
                if url:
                    st.markdown(
                        f'<div style="margin:5px 0;font-size:13px;text-align:left">'
                        f'<a href="{url}" target="_blank" style="color:#1d4ed8;text-decoration:none">{headline}</a>{meta}</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f'<div style="margin:5px 0;font-size:13px;color:#374151;text-align:left">{headline}{meta}</div>',
                        unsafe_allow_html=True,
                    )
        else:
            st.caption("No recent news found via Finnhub.")

        st.markdown(
            f"""<div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;text-align:left">
            <a href="https://finviz.com/quote.ashx?t={ticker}" target="_blank"
               style="background:#f1f5f9;border:1px solid #e2e8f0;border-radius:6px;padding:5px 11px;
               font-size:12px;color:#1e293b;text-decoration:none;font-weight:500">📊 FinViz</a>
            <a href="https://finance.yahoo.com/quote/{ticker}/news" target="_blank"
               style="background:#f1f5f9;border:1px solid #e2e8f0;border-radius:6px;padding:5px 11px;
               font-size:12px;color:#1e293b;text-decoration:none;font-weight:500">📰 Yahoo News</a>
            <a href="https://seekingalpha.com/symbol/{ticker}" target="_blank"
               style="background:#f1f5f9;border:1px solid #e2e8f0;border-radius:6px;padding:5px 11px;
               font-size:12px;color:#1e293b;text-decoration:none;font-weight:500">📝 Seeking Alpha</a>
            <a href="https://www.marketwatch.com/investing/stock/{ticker}" target="_blank"
               style="background:#f1f5f9;border:1px solid #e2e8f0;border-radius:6px;padding:5px 11px;
               font-size:12px;color:#1e293b;text-decoration:none;font-weight:500">📈 MarketWatch</a>
            </div>""",
            unsafe_allow_html=True,
        )


_CRYPTO_TICKERS = {
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "BNB-USD",
    "ADA-USD", "AVAX-USD", "DOGE-USD", "LINK-USD", "DOT-USD",
}
_COMMODITY_TICKERS = {
    "GLD", "IAU", "GDX", "GDXJ", "GOLD", "SLV", "PPLT", "USO", "UNG",
}

def _asset_badge(p: dict) -> str:
    asset = p.get("asset_class") or (
        "crypto" if (p.get("ticker", "").endswith("-USD") or p.get("ticker") in _CRYPTO_TICKERS)
        else "commodity" if p.get("ticker") in _COMMODITY_TICKERS
        else "stock"
    )
    badges = ""
    if asset == "crypto":
        badges += '<span style="background:#1e1b4b;color:#a5b4fc;border-radius:20px;padding:2px 8px;font-size:11px;font-weight:700;margin-left:4px">₿ CRYPTO</span>'
    elif asset == "commodity":
        badges += '<span style="background:#451a03;color:#fcd34d;border-radius:20px;padding:2px 8px;font-size:11px;font-weight:700;margin-left:4px">⬡ COMMODITY</span>'
    if p.get("earnings_label"):
        badges += f'<span style="background:#78350f;color:#fde68a;border-radius:20px;padding:2px 8px;font-size:11px;font-weight:700;margin-left:4px">{p["earnings_label"]}</span>'
    if p.get("insider_signal"):
        badges += f'<span style="background:#3b0764;color:#e9d5ff;border-radius:20px;padding:2px 8px;font-size:11px;font-weight:700;margin-left:4px">{p["insider_signal"]}</span>'
    return badges


def _pill(label: str, value: str, color: str) -> str:
    return (
        f'<span style="display:inline-flex;align-items:center;gap:4px;'
        f'background:#f1f5f9;border:1px solid #e2e8f0;border-radius:20px;padding:4px 10px;font-size:12px">'
        f'<span style="color:#64748b;font-weight:400">{label}:</span>'
        f'<strong style="color:{color}">{value}</strong></span>'
    )


def _show_empty_state():
    st.info("No predictions yet. The nightly scanner runs at 8:00 PM PT.")
    c1, c2 = st.columns([2, 2])
    with c1:
        st.markdown('<div class="btn-api">', unsafe_allow_html=True)
        if st.button("🚀 Run Nightly Scanner", key="run_scanner_empty"):
            _trigger_scanner()
    with c2:
        st.markdown('<div class="btn-api">', unsafe_allow_html=True)
        if st.button("🐛 Run Nightly Scanner Debug", key="run_scanner_debug_empty"):
            _trigger_scanner(debug=True)


def _clear_open_predictions():
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
        print(f"Soft-deleted {count} open predictions.")
        status.update(label=f"✅ Cleared {count} predictions!", state="complete", expanded=False)
        st.success(f"Removed {count} open predictions.")
        st.session_state["confirm_clear"] = False
        st.session_state["_open_deleted"] = set()
        _fetch_open_predictions.clear()
        st.rerun()
    except Exception as e:
        status.update(label="❌ Failed", state="error", expanded=True)
        st.error(f"Error: {e}")


def _trigger_scanner(debug: bool = False):
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
        _fetch_scan_logs.clear()
        _fetch_hot_tickers.clear()
        _fetch_earnings_calendar.clear()
        st.session_state["_open_deleted"] = set()
        st.rerun()
    except Exception as e:
        status.update(label="❌ Failed", state="error", expanded=True)
        st.error(f"Scanner error: {e}")


def _show_raw_log():
    """Read last nightly scan raw Claude log from Supabase cache and display it."""
    from database.db import get_cache
    from datetime import datetime
    import pytz
    PT = pytz.timezone("America/Los_Angeles")
    date_str = datetime.now(PT).strftime("%Y-%m-%d")
    data = get_cache(f"claude_raw_{date_str}")
    if not data:
        # Try yesterday
        from datetime import timedelta
        yesterday = (datetime.now(PT) - timedelta(days=1)).strftime("%Y-%m-%d")
        data = get_cache(f"claude_raw_{yesterday}")
        if data:
            st.info(f"No log for today yet — showing yesterday ({yesterday})")
        else:
            st.warning("No raw scan log found. Run the scanner first.")
            return

    responses = data.get("responses", [])
    total = data.get("total_calls", 0)
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
        status    = "✅ SAVED" if passed_f else "❌ filtered"
        dir_color = "green" if direction == "BULLISH" else "red" if direction == "BEARISH" else "grey"
        with st.expander(f"{status}  **{ticker}**  score={score}  :{dir_color}[{direction}]  profit={profit:.1f}%", expanded=False):
            st.write(f"**Target:** ${r.get('used_target')}  **Stop:** ${r.get('used_stop')}  **Confidence:** {r.get('confidence')}%")
            if key_sigs:
                st.write(f"**Key signals:** {', '.join(key_sigs)}")
            if reasoning:
                st.caption(reasoning)


def _save_debug_log(raw_log: list):
    import json, base64, requests, os
    from datetime import datetime

    if not raw_log:
        st.warning("No raw Claude data to save.")
        return

    token = os.environ.get("GITHUB_TOKEN", "")
    repo  = os.environ.get("GITHUB_REPO", "")
    if not token or not repo:
        st.error("GITHUB_TOKEN / GITHUB_REPO secrets not set — cannot save debug log.")
        return

    date_str = datetime.now(PT).strftime("%Y-%m-%d")
    file_path = f"debug/claude_raw_{date_str}.json"
    content = json.dumps({
        "scan_date":   date_str,
        "total_calls": len(raw_log),
        "passed_filter": sum(1 for r in raw_log if r.get("passed_filter")),
        "responses":   raw_log,
    }, indent=2)

    api = f"https://api.github.com/repos/{repo}/contents/{file_path}"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}

    # Get existing sha if file already exists (for update)
    sha = None
    try:
        r = requests.get(api, headers=headers)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except Exception:
        pass

    payload = {
        "message": f"debug: claude raw responses {date_str}",
        "content": base64.b64encode(content.encode()).decode(),
    }
    if sha:
        payload["sha"] = sha

    try:
        r = requests.put(api, headers=headers, json=payload)
        r.raise_for_status()
        st.success(f"✅ Debug log saved → `{file_path}` on GitHub")
    except Exception as e:
        st.error(f"Failed to save debug log: {e}")
