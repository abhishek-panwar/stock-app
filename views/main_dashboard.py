import streamlit as st
from streamlit_lightweight_charts import renderLightweightCharts
from datetime import datetime, timedelta
import pytz

PT = pytz.timezone("America/Los_Angeles")
TIMEFRAME_DAYS = {"short": 5, "medium": 28, "long": 180}


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
            f"Universe: **{log.get('universe_total','—')} stocks** "
            f"({log.get('nasdaq100_count','—')} Nasdaq + {log.get('hot_stock_count','—')} hot "
            f"→ {log.get('overlap_count','—')} overlap, deduplicated)"
        )

    short  = sorted([p for p in predictions if p.get("timeframe") == "short"],  key=lambda x: x.get("score", 0), reverse=True)
    medium = sorted([p for p in predictions if p.get("timeframe") == "medium"], key=lambda x: x.get("score", 0), reverse=True)
    long_  = sorted([p for p in predictions if p.get("timeframe") == "long"],   key=lambda x: x.get("score", 0), reverse=True)

    all_agree_tickers = (
        {p["ticker"] for p in short} &
        {p["ticker"] for p in medium} &
        {p["ticker"] for p in long_}
    )

    # ── Session state: which stock's chart to show ────────────────────────────
    if "chart_ticker" not in st.session_state:
        st.session_state.chart_ticker = None
        st.session_state.chart_pred   = None

    # ── Chart panel (always at top, outside any expander) ────────────────────
    _chart_panel()

    st.markdown("---")

    # ── All Timeframes Agree ──────────────────────────────────────────────────
    if all_agree_tickers:
        st.markdown("### 🎯 All Timeframes Agree — Highest Conviction")
        chunks = [list(all_agree_tickers)[i:i+5] for i in range(0, len(all_agree_tickers), 5)]
        for chunk in chunks:
            cols = st.columns(len(chunk))
            for col, ticker in zip(cols, chunk):
                p = next((x for x in short if x["ticker"] == ticker), None) or \
                    next((x for x in medium if x["ticker"] == ticker), None)
                if not p:
                    continue
                direction = p.get("direction", "NEUTRAL")
                color = "#16a34a" if direction == "BULLISH" else "#dc2626" if direction == "BEARISH" else "#6b7280"
                with col:
                    st.markdown(
                        f"""<div style="border:1px solid {color};border-radius:8px;padding:8px 10px;
                        text-align:center">
                        <div style="font-size:15px;font-weight:700;color:{color}">{ticker}</div>
                        <div style="font-size:11px;color:#555">{direction}</div>
                        <div style="font-size:11px">{p.get('confidence',0)}% · {p.get('score',0)}/100</div>
                        </div>""",
                        unsafe_allow_html=True,
                    )
        st.markdown("")

    # ── Timeframe sections ────────────────────────────────────────────────────
    for label, emoji, preds in [
        ("Short-term (2–5 days)",  "⚡", short[:10]),
        ("Medium-term (1–4 weeks)","📈", medium[:10]),
        ("Long-term (1–6 months)", "🌱", long_[:10]),
    ]:
        if not preds:
            continue
        st.markdown(f"### {emoji} {label}")
        for p in preds:
            _prediction_card(p, all_agree_tickers)

    if not short and not medium and not long_:
        _show_empty_state()


def _chart_panel():
    """Fixed chart panel at the top. Renders TradingView chart for selected ticker."""
    ticker = st.session_state.get("chart_ticker")
    pred   = st.session_state.get("chart_pred")

    if not ticker:
        st.info("👆 Click **📈 View Chart** on any stock below to load its chart here.")
        return

    st.markdown(f"### 📈 {ticker} — Interactive Chart")
    st.caption("MA20 (orange) · MA50 (blue) · Bollinger Bands · Volume · RSI(14)  |  Zoom: scroll · Pan: drag")

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
    ticker     = p.get("ticker", "—")
    direction  = p.get("direction", "NEUTRAL")
    confidence = p.get("confidence", 0)
    score      = p.get("score", 0)
    position   = p.get("position", "HOLD")
    timeframe  = p.get("timeframe", "short")
    agreed     = ticker in all_agree_tickers
    predicted_on = p.get("predicted_on", "")

    # Expiry
    expiry_str, days_left_str = "—", ""
    try:
        pred_dt   = datetime.fromisoformat(predicted_on.replace("Z", "+00:00")).replace(tzinfo=None)
        expiry_dt = pred_dt + timedelta(days=TIMEFRAME_DAYS.get(timeframe, 5))
        expiry_str = expiry_dt.strftime("%b %d, %Y")
        days_left  = (expiry_dt - datetime.utcnow()).days
        days_left_str = f" ({days_left}d left)" if days_left > 0 else " **(expired)**"
    except Exception:
        pass

    agree_badge = "  🎯" if agreed else ""
    short_badge = "  ⚠️ SHORT (margin)" if position == "SHORT" else ""

    with st.expander(
        f"**{ticker}**  ·  {direction}  ·  {confidence}% conf  ·  {score}/100{agree_badge}{short_badge}",
        expanded=False,
    ):
        # Chart button — sets session state, triggers rerun
        if st.button(f"📈 View Chart — {ticker}", key=f"chartbtn_{ticker}_{timeframe}"):
            st.session_state.chart_ticker = ticker
            st.session_state.chart_pred   = p
            st.rerun()

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.markdown("**Entry**")
            st.markdown(f"Buy: `${p.get('buy_range_low', 0):.2f} – ${p.get('buy_range_high', 0):.2f}`")
            st.markdown(f"Stop: `${p.get('stop_loss', 0):.2f}`")
        with c2:
            st.markdown("**Target**")
            st.markdown(f"`${p.get('target_low', 0):.2f} – ${p.get('target_high', 0):.2f}`")
            entry  = p.get("price_at_prediction") or 0
            target = p.get("target_low") or 0
            stop   = p.get("stop_loss") or 0
            if entry > 0 and target > 0 and stop > 0:
                rr = abs(target - entry) / abs(entry - stop) if abs(entry - stop) > 0 else 0
                st.markdown(f"R/R: `1 : {rr:.1f}`")
        with c3:
            st.markdown("**Timing**")
            try:
                pred_dt_str = datetime.fromisoformat(predicted_on.replace("Z", "+00:00")).strftime("%b %d  %I:%M %p PT")
            except Exception:
                pred_dt_str = "—"
            st.markdown(f"Predicted: `{pred_dt_str}`")
            st.markdown(f"Expires: `{expiry_str}`{days_left_str}")
        with c4:
            st.markdown("**Meta**")
            st.markdown(f"Source: `{p.get('source', '—')}`")
            st.markdown(f"Formula: `{p.get('formula_version', 'v1.0')}`")
            st.markdown(f"Position: `{position}`")

        if p.get("reasoning"):
            st.markdown(f"> {p['reasoning']}")


def _show_empty_state():
    st.info("No predictions yet. The nightly scanner runs at 8:00 PM PT.")
    st.markdown("**Run manually:** `python3 scripts/nightly_scanner.py`")
