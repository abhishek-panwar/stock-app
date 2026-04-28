import streamlit as st
from streamlit_lightweight_charts import renderLightweightCharts
from datetime import datetime, timedelta
import pytz

PT = pytz.timezone("America/Los_Angeles")

DIR_COLORS = {
    "BULLISH": ("#f0fdf4", "#16a34a", "#15803d"),
    "BEARISH": ("#fef2f2", "#dc2626", "#b91c1c"),
    "NEUTRAL": ("#f8fafc", "#94a3b8", "#64748b"),
}


@st.cache_data(ttl=3600)
def _get_company_name(ticker: str) -> str:
    try:
        from services.yfinance_service import get_ticker_info
        return get_ticker_info(ticker).get("name", ticker)
    except Exception:
        return ticker


def _age_info(predicted_on: str):
    """Returns (age_days, badge_html)."""
    try:
        pred_dt = datetime.fromisoformat(predicted_on.replace("Z", "+00:00")).replace(tzinfo=None)
        age = (datetime.utcnow() - pred_dt).days
    except Exception:
        return 0, ""
    if age == 0:
        return 0, '<span style="background:#dcfce7;color:#14532d;border-radius:20px;padding:2px 9px;font-size:11px;font-weight:700">NEW TODAY</span>'
    if age == 1:
        return 1, '<span style="background:#fef9c3;color:#713f12;border-radius:20px;padding:2px 9px;font-size:11px;font-weight:600">1 day old</span>'
    return age, f'<span style="background:#fee2e2;color:#7f1d1d;border-radius:20px;padding:2px 9px;font-size:11px;font-weight:600">{age}d old</span>'


def _sort_key(p: dict):
    """Newest first → highest profit% → highest score."""
    try:
        age = (datetime.utcnow() - datetime.fromisoformat(
            p.get("predicted_on", "").replace("Z", "+00:00")).replace(tzinfo=None)).days
    except Exception:
        age = 999
    entry  = p.get("price_at_prediction") or 0
    target = p.get("target_low") or 0
    profit = ((target - entry) / entry * 100) if entry > 0 and target > 0 else 0
    return (age, -profit, -p.get("score", 0))


def _expiry(p: dict):
    """Returns (expiry_str, days_left) using only stored expires_on. No hardcoded fallback."""
    raw = p.get("expires_on") or ""
    if not raw:
        return "—", None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
        days_left = (dt - datetime.utcnow()).days
        return dt.strftime("%b %d"), days_left
    except Exception:
        return "—", None


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

    short  = sorted([p for p in predictions if p.get("timeframe") == "short"],  key=_sort_key)
    medium = sorted([p for p in predictions if p.get("timeframe") == "medium"], key=_sort_key)
    long_  = sorted([p for p in predictions if p.get("timeframe") == "long"],   key=_sort_key)

    # High conviction = confidence >= 75
    high_conviction = sorted(
        [p for p in predictions if (p.get("confidence") or 0) >= 75],
        key=_sort_key
    )

    if "chart_ticker" not in st.session_state:
        st.session_state.chart_ticker = None
        st.session_state.chart_pred   = None

    _chart_panel()
    st.markdown("---")

    # ── High conviction picks ─────────────────────────────────────────────────
    if high_conviction:
        st.markdown("### 🎯 High Conviction Picks")
        chunks = [high_conviction[i:i+5] for i in range(0, len(high_conviction), 5)]
        for chunk in chunks:
            cols = st.columns(len(chunk))
            for col, p in zip(cols, chunk):
                ticker    = p.get("ticker", "—")
                direction = p.get("direction", "NEUTRAL")
                bg, border, text = DIR_COLORS.get(direction, DIR_COLORS["NEUTRAL"])
                entry  = p.get("price_at_prediction") or 0
                target = p.get("target_low") or 0
                profit_pct = ((target - entry) / entry * 100) if entry > 0 and target > 0 else 0
                days = p.get("days_to_target", "?")
                company = p.get("company_name") or _get_company_name(ticker)
                _, age_badge = _age_info(p.get("predicted_on", ""))
                tf_label = {"short": "⚡ Short", "medium": "📈 Mid", "long": "🌱 Long"}.get(p.get("timeframe",""), "")
                with col:
                    st.markdown(
                        f"""<div style="background:{bg};border:1.5px solid {border};border-radius:10px;
                        padding:10px 12px;text-align:left">
                        <div style="font-size:17px;font-weight:700;color:{text}">{ticker}</div>
                        <div style="font-size:11px;color:#475569;margin-bottom:4px">{company}</div>
                        <div style="font-size:11px;font-weight:600;color:{text}">{direction} · {tf_label}</div>
                        <div style="font-size:12px;color:#1e293b">{p.get('confidence',0)}% conf · {p.get('score',0)}/100</div>
                        <div style="font-size:12px;font-weight:600;color:#15803d">+{profit_pct:.1f}% · ~{days}d</div>
                        <div style="margin-top:5px">{age_badge}{_asset_badge(p)}</div>
                        </div>""",
                        unsafe_allow_html=True,
                    )
        st.markdown("")

    # ── Timeframe sections ────────────────────────────────────────────────────
    for label, emoji, preds in [
        ("Short-term  (≤10 days)",   "⚡", short[:10]),
        ("Medium-term (11–35 days)", "📈", medium[:10]),
        ("Long-term   (>35 days)",   "🌱", long_[:10]),
    ]:
        if not preds:
            continue
        st.markdown(f"### {emoji} {label}")
        for p in preds:
            _prediction_card(p, set())   # no all_agree concept any more

    if not short and not medium and not long_:
        _show_empty_state()


def _chart_panel():
    ticker = st.session_state.get("chart_ticker")
    pred   = st.session_state.get("chart_pred")

    if not ticker:
        st.markdown(
            """<div style="background:#f8fafc;border:1px dashed #cbd5e1;border-radius:10px;
            padding:16px;text-align:left;color:#64748b;font-size:14px">
            📈 Click <strong style="color:#1e293b">View Chart</strong> on any prediction below to load its interactive chart here
            </div>""",
            unsafe_allow_html=True,
        )
        return

    col_title, col_close = st.columns([9, 1])
    with col_title:
        company = pred.get("company_name") or _get_company_name(ticker) if pred else ticker
        st.markdown(f"### 📈 {ticker} — {company}")
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


def _prediction_card(p: dict, _unused: set = None):
    ticker      = p.get("ticker", "—")
    direction   = p.get("direction", "NEUTRAL")
    confidence  = p.get("confidence", 0)
    score       = p.get("score", 0)
    position    = p.get("position", "HOLD")
    timeframe   = p.get("timeframe", "short")
    predicted_on = p.get("predicted_on", "")

    company = p.get("company_name") or _get_company_name(ticker)

    entry  = p.get("price_at_prediction") or 0
    target = p.get("target_low") or 0
    stop   = p.get("stop_loss") or 0
    profit_pct = ((target - entry) / entry * 100) if entry > 0 and target > 0 else 0
    rr = abs(target - entry) / abs(entry - stop) if entry > 0 and stop > 0 and abs(entry - stop) > 0 else 0
    profit_str = f"+{profit_pct:.1f}%" if profit_pct > 0 else f"{profit_pct:.1f}%"

    expiry_str, days_left = _expiry(p)
    days_to_target = p.get("days_to_target")
    tenure_str = f"{days_to_target}d" if days_to_target else "—"

    age_days, age_badge = _age_info(predicted_on)

    dir_icon = "▲" if direction == "BULLISH" else "▼" if direction == "BEARISH" else "●"
    hc_tag   = "  🎯" if confidence >= 75 else ""
    age_tag  = "  ·  NEW" if age_days == 0 else f"  ·  {age_days}d old"
    pos_tag  = f"  ·  {position}" if position not in ("HOLD", "") else ""
    exp_tag  = f"  ·  {days_left}d left" if days_left and days_left > 0 else ("  ·  **expired**" if days_left is not None and days_left <= 0 else "")

    header = (
        f"**{ticker}** — {company}  ·  "
        f"{dir_icon} {direction}  ·  "
        f"{confidence}% conf  ·  {score}/100  ·  "
        f"{profit_str}{pos_tag}  ·  ~{tenure_str}"
        f"{exp_tag}{age_tag}{hc_tag}"
    )

    pred_id = p.get("id") or f"{ticker}_{timeframe}_{predicted_on[:10]}"

    with st.container(border=True):
        # ── Card header row ───────────────────────────────────────────────────
        title_col, del_col = st.columns([11, 1])
        with title_col:
            st.markdown(f"{header}", unsafe_allow_html=False)
            st.markdown(f"<div style='margin-top:2px'>{age_badge}{_asset_badge(p)}</div>", unsafe_allow_html=True)
        with del_col:
            if st.button("✕", key=f"del_{pred_id}", help="Delete prediction"):
                try:
                    from database.db import soft_delete_prediction
                    soft_delete_prediction(pred_id)
                    st.rerun()
                except Exception as e:
                    st.error(f"Delete failed: {e}")

        # ── Expandable details ────────────────────────────────────────────────
        with st.expander("Details", expanded=False):
            btn_col, _ = st.columns([2, 8])
            with btn_col:
                if st.button("📈 View Chart", key=f"chartbtn_{pred_id}"):
                    st.session_state.chart_ticker = ticker
                    st.session_state.chart_pred   = p
                    st.rerun()

            dir_color  = "#15803d" if direction == "BULLISH" else "#b91c1c" if direction == "BEARISH" else "#475569"
            prof_color = "#15803d" if profit_pct > 0 else "#b91c1c"
            st.markdown(
                f"""<div style="display:flex;gap:6px;flex-wrap:wrap;margin:10px 0 14px;text-align:left">
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
                if expiry_str != "—":
                    st.markdown(f"Expires: `{expiry_str}`{f'  ({days_left}d left)' if days_left and days_left > 0 else ''}")
                else:
                    st.markdown("Expires: `run scanner to populate`")
                if p.get("timing_rationale"):
                    st.caption(f"💡 {p['timing_rationale']}")

            if p.get("reasoning"):
                st.markdown(
                    f"""<div style="background:#f8fafc;border-left:3px solid #94a3b8;border-radius:0 6px 6px 0;
                    padding:10px 14px;margin:12px 0 8px;font-size:13px;color:#1e293b;
                    line-height:1.6;text-align:left">{p['reasoning']}</div>""",
                    unsafe_allow_html=True,
                )

            _news_links(ticker)

            if position == "SHORT":
                st.warning("SHORT position — margin/options account required")


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
                    date_str = datetime.utcfromtimestamp(ts).strftime("%b %d") if ts else ""
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
    if asset == "crypto":
        return '<span style="background:#1e1b4b;color:#a5b4fc;border-radius:20px;padding:2px 8px;font-size:11px;font-weight:700;margin-left:4px">₿ CRYPTO</span>'
    if asset == "commodity":
        return '<span style="background:#451a03;color:#fcd34d;border-radius:20px;padding:2px 8px;font-size:11px;font-weight:700;margin-left:4px">⬡ COMMODITY</span>'
    return ""


def _pill(label: str, value: str, color: str) -> str:
    return (
        f'<span style="display:inline-flex;align-items:center;gap:4px;'
        f'background:#f1f5f9;border:1px solid #e2e8f0;border-radius:20px;padding:4px 10px;font-size:12px">'
        f'<span style="color:#64748b;font-weight:400">{label}:</span>'
        f'<strong style="color:{color}">{value}</strong></span>'
    )


def _show_empty_state():
    st.info("No predictions yet. The nightly scanner runs at 8:00 PM PT.")
    if st.button("🚀 Run Scanner Now", type="primary", key="run_scanner_empty"):
        _trigger_scanner()


def _trigger_scanner():
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    status = st.status("Running scanner…", expanded=True)
    try:
        import scripts.nightly_scanner as scanner
        import importlib
        importlib.reload(scanner)
        import builtins
        _orig = builtins.print
        builtins.print = lambda *a, **k: (status.write(" ".join(str(x) for x in a)), _orig(*a, **k))
        try:
            stats = scanner.run()
        finally:
            builtins.print = _orig
        status.update(label="✅ Done!", state="complete", expanded=False)
        st.success(f"{stats.get('predictions_created', 0)} predictions created")
        st.rerun()
    except Exception as e:
        status.update(label="❌ Failed", state="error", expanded=True)
        st.error(f"Scanner error: {e}")
