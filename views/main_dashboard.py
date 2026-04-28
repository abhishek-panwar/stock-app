import streamlit as st
from streamlit_lightweight_charts import renderLightweightCharts
from datetime import datetime, timedelta
import pytz

PT = pytz.timezone("America/Los_Angeles")
TIMEFRAME_DAYS = {"short": 5, "medium": 28, "long": 180}

DIR_COLORS = {
    "BULLISH": ("#f0fdf4", "#16a34a", "#15803d"),
    "BEARISH": ("#fef2f2", "#dc2626", "#b91c1c"),
    "NEUTRAL": ("#f8fafc", "#94a3b8", "#64748b"),
}


def _age_info(predicted_on: str):
    """Returns (age_days, badge_html) based on how old the prediction is."""
    try:
        pred_dt = datetime.fromisoformat(predicted_on.replace("Z", "+00:00")).replace(tzinfo=None)
        age = (datetime.utcnow() - pred_dt).days
    except Exception:
        return 0, ""
    if age == 0:
        badge = '<span style="background:#dcfce7;color:#15803d;border-radius:20px;padding:2px 8px;font-size:11px;font-weight:600">NEW TODAY</span>'
    elif age == 1:
        badge = '<span style="background:#fef9c3;color:#854d0e;border-radius:20px;padding:2px 8px;font-size:11px;font-weight:600">1 day old</span>'
    else:
        opacity = min(0.3 + age * 0.12, 1.0)  # gets more red as it ages
        badge = f'<span style="background:#fee2e2;color:#991b1b;border-radius:20px;padding:2px 8px;font-size:11px;font-weight:600">{age}d old</span>'
    return age, badge


def _sort_key(p: dict):
    """Sort by: newest first, then profit% desc, then score desc."""
    try:
        age = (datetime.utcnow() - datetime.fromisoformat(
            p.get("predicted_on", "").replace("Z", "+00:00")).replace(tzinfo=None)).days
    except Exception:
        age = 999
    entry  = p.get("price_at_prediction") or 0
    target = p.get("target_low") or 0
    profit = ((target - entry) / entry * 100) if entry > 0 and target > 0 else 0
    return (age, -profit, -p.get("score", 0))


def render():
    st.title("📊 Today's Best Setups")
    now_pt = datetime.now(PT)
    st.caption(f"Last updated: {now_pt.strftime('%b %d, %Y  %I:%M %p PT')}")

    try:
        from database.db import get_predictions, get_scan_logs
        predictions = get_predictions({"outcome": "PENDING"}, limit=200)
        scan_logs   = get_scan_logs(limit=1)
    except Exception as e:
        st.error(f"Database connection error: {e}")
        _show_empty_state()
        return

    if not predictions:
        _show_empty_state()
        return

    if scan_logs:
        log = scan_logs[0]
        st.info(
            f"Universe: **{log.get('universe_total','—')} stocks** scanned  ·  "
            f"{log.get('nasdaq100_count','—')} Nasdaq + {log.get('hot_stock_count','—')} hot "
            f"→ {log.get('overlap_count','—')} overlap, deduplicated"
        )

    # Sort: newest → max profit → max score
    short  = sorted([p for p in predictions if p.get("timeframe") == "short"],  key=_sort_key)
    medium = sorted([p for p in predictions if p.get("timeframe") == "medium"], key=_sort_key)
    long_  = sorted([p for p in predictions if p.get("timeframe") == "long"],   key=_sort_key)

    all_agree_tickers = (
        {p["ticker"] for p in short} &
        {p["ticker"] for p in medium} &
        {p["ticker"] for p in long_}
    )

    if "chart_ticker" not in st.session_state:
        st.session_state.chart_ticker = None
        st.session_state.chart_pred   = None

    _chart_panel()
    st.markdown("---")

    # ── All Timeframes Agree ──────────────────────────────────────────────────
    if all_agree_tickers:
        st.markdown("### 🎯 All Timeframes Agree — Highest Conviction")
        agree_list = sorted(all_agree_tickers)
        chunks = [agree_list[i:i+5] for i in range(0, len(agree_list), 5)]
        for chunk in chunks:
            cols = st.columns(len(chunk))
            for col, ticker in zip(cols, chunk):
                p = next((x for x in short if x["ticker"] == ticker), None) or \
                    next((x for x in medium if x["ticker"] == ticker), None)
                if not p:
                    continue
                direction = p.get("direction", "NEUTRAL")
                bg, border, text = DIR_COLORS.get(direction, DIR_COLORS["NEUTRAL"])
                entry  = p.get("price_at_prediction") or 0
                target = p.get("target_low") or 0
                profit_pct = ((target - entry) / entry * 100) if entry > 0 and target > 0 else 0
                days = p.get("days_to_target", "?")
                _, age_badge = _age_info(p.get("predicted_on", ""))
                with col:
                    st.markdown(
                        f"""<div style="background:{bg};border:1.5px solid {border};border-radius:10px;
                        padding:10px 12px;text-align:center">
                        <div style="font-size:16px;font-weight:700;color:{text}">{ticker}</div>
                        <div style="font-size:11px;font-weight:600;color:{text};margin:2px 0">{direction}</div>
                        <div style="font-size:12px;color:#1e293b">{p.get('confidence',0)}% conf · {p.get('score',0)}/100</div>
                        <div style="font-size:12px;font-weight:600;color:#15803d">+{profit_pct:.1f}% · ~{days}d</div>
                        <div style="margin-top:4px">{age_badge}</div>
                        </div>""",
                        unsafe_allow_html=True,
                    )
        st.markdown("")

    # ── Timeframe sections ────────────────────────────────────────────────────
    for label, emoji, preds in [
        ("Short-term",  "⚡", short[:10]),
        ("Medium-term", "📈", medium[:10]),
        ("Long-term",   "🌱", long_[:10]),
    ]:
        if not preds:
            continue
        st.markdown(f"### {emoji} {label}")
        for p in preds:
            _prediction_card(p, all_agree_tickers)

    if not short and not medium and not long_:
        _show_empty_state()


def _chart_panel():
    ticker = st.session_state.get("chart_ticker")
    pred   = st.session_state.get("chart_pred")

    if not ticker:
        st.markdown(
            """<div style="background:#f8fafc;border:1px dashed #cbd5e1;border-radius:10px;
            padding:16px;text-align:center;color:#64748b;font-size:14px">
            📈 Click <strong style="color:#1e293b">View Chart</strong> on any prediction below to load its interactive chart here
            </div>""",
            unsafe_allow_html=True,
        )
        return

    col_title, col_close = st.columns([9, 1])
    with col_title:
        st.markdown(f"### 📈 {ticker} — Interactive Chart")
        st.caption("MA20 (orange) · MA50 (blue) · Bollinger Bands · Volume · RSI(14)  |  Scroll to zoom · Drag to pan")
    with col_close:
        if st.button("✕ Close", key="close_chart"):
            st.session_state.chart_ticker = None
            st.session_state.chart_pred   = None
            st.rerun()

    with st.spinner(f"Loading {ticker}..."):
        try:
            from services.chart_service import build_stock_chart
            from services.yfinance_service import get_price_history
            df = get_price_history(ticker, period="3mo")
            if df.empty:
                st.warning(f"No price data for {ticker}.")
                return
            charts = build_stock_chart(df, prediction=pred, ticker=ticker, height=500)
            if charts:
                renderLightweightCharts(charts, key=f"main_chart_{ticker}")
        except Exception as e:
            st.error(f"Chart error: {e}")


def _prediction_card(p: dict, all_agree_tickers: set):
    ticker      = p.get("ticker", "—")
    direction   = p.get("direction", "NEUTRAL")
    confidence  = p.get("confidence", 0)
    score       = p.get("score", 0)
    position    = p.get("position", "HOLD")
    timeframe   = p.get("timeframe", "short")
    agreed      = ticker in all_agree_tickers
    predicted_on = p.get("predicted_on", "")

    entry  = p.get("price_at_prediction") or 0
    target = p.get("target_low") or 0
    stop   = p.get("stop_loss") or 0
    profit_pct = ((target - entry) / entry * 100) if entry > 0 and target > 0 else 0
    rr = abs(target - entry) / abs(entry - stop) if entry > 0 and stop > 0 and abs(entry - stop) > 0 else 0
    profit_str = f"+{profit_pct:.1f}%" if profit_pct > 0 else f"{profit_pct:.1f}%"

    # Expiry
    expiry_dt = None
    try:
        raw = p.get("expires_on") or ""
        if raw:
            expiry_dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
        else:
            pred_dt   = datetime.fromisoformat(predicted_on.replace("Z", "+00:00")).replace(tzinfo=None)
            expiry_dt = pred_dt + timedelta(days=TIMEFRAME_DAYS.get(timeframe, 5))
    except Exception:
        pass
    days_left = (expiry_dt - datetime.utcnow()).days if expiry_dt else None
    expiry_str = expiry_dt.strftime("%b %d") if expiry_dt else "—"
    days_to_target = p.get("days_to_target")
    tenure_str = f"{days_to_target}d" if days_to_target else f"{TIMEFRAME_DAYS.get(timeframe, '?')}d"

    age_days, age_badge = _age_info(predicted_on)

    dir_icon = "▲" if direction == "BULLISH" else "▼" if direction == "BEARISH" else "●"
    days_left_tag = f"  ·  {days_left}d left" if days_left and days_left > 0 else ("  ·  **expired**" if days_left is not None and days_left <= 0 else "")
    agree_tag = "  🎯" if agreed else ""
    pos_tag   = f"  ·  {position}" if position not in ("HOLD", "") else ""
    age_tag   = f"  ·  {age_days}d old" if age_days > 0 else "  ·  NEW"

    header = (
        f"**{ticker}**  ·  {dir_icon} {direction}  ·  "
        f"{confidence}% conf  ·  {score}/100  ·  "
        f"{profit_str}{pos_tag}  ·  ~{tenure_str}"
        f"{days_left_tag}{age_tag}{agree_tag}"
    )

    with st.expander(header, expanded=False):
        # Top row: chart button + age badge
        btn_col, badge_col = st.columns([3, 7])
        with btn_col:
            if st.button(f"📈 View Chart", key=f"chartbtn_{ticker}_{timeframe}"):
                st.session_state.chart_ticker = ticker
                st.session_state.chart_pred   = p
                st.rerun()
        with badge_col:
            st.markdown(f"<div style='padding-top:6px'>{age_badge}</div>", unsafe_allow_html=True)

        # ── Stat pills ────────────────────────────────────────────────────────
        dir_color  = "#15803d" if direction == "BULLISH" else "#b91c1c" if direction == "BEARISH" else "#475569"
        prof_color = "#15803d" if profit_pct > 0 else "#b91c1c"
        st.markdown(
            f"""<div style="display:flex;gap:6px;flex-wrap:wrap;margin:10px 0 14px">
            {_pill("Direction", f"{dir_icon} {direction}", dir_color)}
            {_pill("Confidence", f"{confidence}%", "#1d4ed8")}
            {_pill("Score", f"{score}/100", "#7c3aed")}
            {_pill("Profit target", profit_str, prof_color)}
            {_pill("Est. tenure", f"~{tenure_str}", "#0369a1")}
            {_pill("R/R", f"1 : {rr:.1f}", "#b45309")}
            {_pill("Position", position, "#374151")}
            </div>""",
            unsafe_allow_html=True,
        )

        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("**Entry**")
            st.markdown(f"Price at signal: `${entry:.2f}`")
            st.markdown(f"Buy range: `${p.get('buy_range_low',0):.2f} – ${p.get('buy_range_high',0):.2f}`")
            st.markdown(f"Stop loss: `${stop:.2f}`")
        with c2:
            st.markdown("**Target**")
            st.markdown(f"Range: `${p.get('target_low',0):.2f} – ${p.get('target_high',0):.2f}`")
            st.markdown(f"Profit potential: `{profit_str}`")
            st.markdown(f"Risk/Reward: `1 : {rr:.1f}`")
        with c3:
            st.markdown("**Timing**")
            try:
                pred_dt_str = datetime.fromisoformat(predicted_on.replace("Z", "+00:00")).strftime("%b %d  %I:%M %p PT")
            except Exception:
                pred_dt_str = "—"
            st.markdown(f"Predicted: `{pred_dt_str}`")
            st.markdown(f"Est. days to target: `{days_to_target or '—'}`")
            st.markdown(f"Expires: `{expiry_str}`{f'  ({days_left}d left)' if days_left and days_left > 0 else ''}")
            if p.get("timing_rationale"):
                st.caption(f"💡 {p['timing_rationale']}")

        # ── Reasoning ────────────────────────────────────────────────────────
        if p.get("reasoning"):
            st.markdown(
                f"""<div style="background:#f8fafc;border-left:3px solid #94a3b8;border-radius:0 6px 6px 0;
                padding:10px 14px;margin:12px 0 8px;font-size:13px;color:#1e293b;line-height:1.5">
                {p['reasoning']}</div>""",
                unsafe_allow_html=True,
            )

        # ── News / analysis links ─────────────────────────────────────────────
        _news_links(ticker)

        if position == "SHORT":
            st.warning("SHORT position — margin/options account required")


def _news_links(ticker: str):
    """Fetch and display recent news articles supporting this prediction."""
    with st.expander(f"📰 News & analysis for {ticker}", expanded=False):
        try:
            from services.finnhub_service import get_news_sentiment
            data = get_news_sentiment(ticker, hours=72)
            articles = data.get("articles", [])
        except Exception:
            articles = []

        if not articles:
            st.caption("No recent news found.")
            # Fallback: search links
        else:
            for a in articles[:6]:
                headline = a.get("headline", "")[:90]
                url      = a.get("url", "")
                source   = a.get("source", "")
                ts       = a.get("datetime", 0)
                try:
                    date_str = datetime.utcfromtimestamp(ts).strftime("%b %d") if ts else ""
                except Exception:
                    date_str = ""
                if url:
                    st.markdown(
                        f'<div style="margin:4px 0;font-size:13px">'
                        f'<a href="{url}" target="_blank" style="color:#1d4ed8;text-decoration:none">{headline}</a>'
                        f'<span style="color:#94a3b8;font-size:11px;margin-left:6px">{source} · {date_str}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f'<div style="margin:4px 0;font-size:13px;color:#374151">{headline}'
                        f'<span style="color:#94a3b8;font-size:11px;margin-left:6px">{source} · {date_str}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

        # Always show search links
        st.markdown(
            f"""<div style="margin-top:10px;display:flex;gap:10px;flex-wrap:wrap">
            <a href="https://finviz.com/quote.ashx?t={ticker}" target="_blank"
               style="background:#f1f5f9;border:1px solid #e2e8f0;border-radius:6px;padding:4px 10px;
               font-size:12px;color:#1e293b;text-decoration:none">📊 FinViz</a>
            <a href="https://finance.yahoo.com/quote/{ticker}/news" target="_blank"
               style="background:#f1f5f9;border:1px solid #e2e8f0;border-radius:6px;padding:4px 10px;
               font-size:12px;color:#1e293b;text-decoration:none">📰 Yahoo News</a>
            <a href="https://seekingalpha.com/symbol/{ticker}" target="_blank"
               style="background:#f1f5f9;border:1px solid #e2e8f0;border-radius:6px;padding:4px 10px;
               font-size:12px;color:#1e293b;text-decoration:none">📝 Seeking Alpha</a>
            <a href="https://www.marketwatch.com/investing/stock/{ticker}" target="_blank"
               style="background:#f1f5f9;border:1px solid #e2e8f0;border-radius:6px;padding:4px 10px;
               font-size:12px;color:#1e293b;text-decoration:none">📈 MarketWatch</a>
            </div>""",
            unsafe_allow_html=True,
        )


def _pill(label: str, value: str, color: str) -> str:
    return (
        f'<span style="display:inline-flex;align-items:center;gap:4px;'
        f'background:#f1f5f9;border:1px solid #e2e8f0;border-radius:20px;'
        f'padding:4px 10px;font-size:12px">'
        f'<span style="color:#64748b;font-weight:400">{label}:</span>'
        f'<strong style="color:{color}">{value}</strong></span>'
    )


def _show_empty_state():
    st.info("No predictions yet. The nightly scanner runs at 8:00 PM PT.")
    st.markdown("**Run manually:** `python3 scripts/nightly_scanner.py`")
