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
    try:
        pred_dt = datetime.fromisoformat(predicted_on.replace("Z", "+00:00")).replace(tzinfo=None)
        age = (datetime.utcnow() - pred_dt).days
    except Exception:
        return 0, ""
    if age == 0:
        return 0, '<span style="background:linear-gradient(135deg,#16a34a,#15803d);color:#fff;border-radius:20px;padding:2px 10px;font-size:11px;font-weight:700;letter-spacing:0.5px">✨ NEW TODAY</span>'
    if age == 1:
        return 1, '<span style="background:#fef9c3;color:#713f12;border-radius:20px;padding:2px 9px;font-size:11px;font-weight:600">1 day old</span>'
    return age, f'<span style="background:#f1f5f9;color:#64748b;border-radius:20px;padding:2px 9px;font-size:11px;font-weight:500">{age}d old</span>'


def _sort_key(p: dict):
    try:
        age = (datetime.utcnow() - datetime.fromisoformat(
            p.get("predicted_on", "").replace("Z", "+00:00")).replace(tzinfo=None)).days
    except Exception:
        age = 999
    entry  = p.get("price_at_prediction") or 0
    target = p.get("target_low") or 0
    profit = ((target - entry) / entry * 100) if entry > 0 and target > 0 else 0
    return (age, -abs(profit), -p.get("score", 0))


def _expiry(p: dict):
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
    now_pt = datetime.now(PT)

    # ── Rich page header ──────────────────────────────────────────────────────
    st.markdown(
        f"""<div style="background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 100%);
            border-radius:14px;padding:22px 28px 18px;margin-bottom:20px;
            box-shadow:0 4px 24px rgba(0,0,0,0.18)">
          <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px">
            <div>
              <div style="font-size:24px;font-weight:800;color:#f8fafc;letter-spacing:-0.5px">
                📊 Today's Best Setups
              </div>
              <div style="font-size:12px;color:#94a3b8;margin-top:4px">
                Last updated: {now_pt.strftime('%b %d, %Y  %I:%M %p PT')}
              </div>
            </div>
            <div style="background:rgba(255,255,255,0.07);border:1px solid rgba(255,255,255,0.12);
                border-radius:10px;padding:8px 16px;text-align:center">
              <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:1px">Next Scan</div>
              <div style="font-size:14px;font-weight:700;color:#60a5fa">8:00 PM PT</div>
            </div>
          </div>
        </div>""",
        unsafe_allow_html=True,
    )

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

    # ── Scan universe bar ─────────────────────────────────────────────────────
    if scan_logs:
        log = scan_logs[0]
        total   = log.get("universe_total", "—")
        nasdaq  = log.get("nasdaq100_count", "—")
        hot     = log.get("hot_stock_count", "—")
        overlap = log.get("overlap_count", "—")
        n_preds = len(predictions)
        st.markdown(
            f"""<div style="background:#0f172a;border:1px solid #1e293b;border-radius:10px;
                padding:10px 18px;margin-bottom:16px;display:flex;gap:24px;flex-wrap:wrap;align-items:center">
              <span style="color:#94a3b8;font-size:12px">🔭 Universe</span>
              <span style="color:#f8fafc;font-size:13px;font-weight:600">{total} stocks</span>
              <span style="color:#475569;font-size:12px">{nasdaq} core · {hot} hot · {overlap} overlap</span>
              <span style="margin-left:auto;background:#1e3a5f;border:1px solid #2563eb;border-radius:20px;
                  padding:2px 12px;color:#93c5fd;font-size:12px;font-weight:600">{n_preds} active predictions</span>
            </div>""",
            unsafe_allow_html=True,
        )

    short  = sorted([p for p in predictions if p.get("timeframe") == "short"],  key=_sort_key)
    medium = sorted([p for p in predictions if p.get("timeframe") == "medium"], key=_sort_key)
    long_  = sorted([p for p in predictions if p.get("timeframe") == "long"],   key=_sort_key)
    high_conviction = sorted(
        [p for p in predictions if (p.get("confidence") or 0) >= 75],
        key=_sort_key
    )

    if "chart_ticker" not in st.session_state:
        st.session_state.chart_ticker = None
        st.session_state.chart_pred   = None

    _chart_panel()
    st.markdown("<div style='margin:8px 0'></div>", unsafe_allow_html=True)

    # ── High conviction picks ─────────────────────────────────────────────────
    if high_conviction:
        st.markdown(
            """<div style="display:flex;align-items:center;gap:10px;margin:8px 0 14px">
              <div style="height:2px;flex:1;background:linear-gradient(90deg,#f59e0b,transparent)"></div>
              <span style="background:linear-gradient(135deg,#f59e0b,#d97706);color:#fff;
                  border-radius:20px;padding:4px 16px;font-size:13px;font-weight:700;
                  box-shadow:0 2px 8px rgba(245,158,11,0.35)">🎯 High Conviction Picks</span>
              <div style="height:2px;flex:1;background:linear-gradient(90deg,transparent,#f59e0b)"></div>
            </div>""",
            unsafe_allow_html=True,
        )
        chunks = [high_conviction[i:i+5] for i in range(0, len(high_conviction), 5)]
        for chunk in chunks:
            cols = st.columns(len(chunk))
            for col, p in zip(cols, chunk):
                ticker    = p.get("ticker", "—")
                direction = p.get("direction", "NEUTRAL")
                entry     = p.get("price_at_prediction") or 0
                target    = p.get("target_low") or 0
                profit_pct = ((target - entry) / entry * 100) if entry > 0 and target > 0 else 0
                days      = p.get("days_to_target", "?")
                company   = p.get("company_name") or _get_company_name(ticker)
                _, age_badge = _age_info(p.get("predicted_on", ""))
                tf_label  = {"short": "⚡ Short", "medium": "📈 Mid", "long": "🌱 Long"}.get(p.get("timeframe", ""), "")
                conf      = p.get("confidence", 0)

                if direction == "BULLISH":
                    card_bg    = "linear-gradient(145deg,#052e16,#14532d)"
                    border_col = "#16a34a"
                    glow       = "rgba(22,163,74,0.25)"
                    dir_color  = "#4ade80"
                    dir_icon   = "▲"
                elif direction == "BEARISH":
                    card_bg    = "linear-gradient(145deg,#2d0a0a,#7f1d1d)"
                    border_col = "#dc2626"
                    glow       = "rgba(220,38,38,0.25)"
                    dir_color  = "#f87171"
                    dir_icon   = "▼"
                else:
                    card_bg    = "linear-gradient(145deg,#0f172a,#1e293b)"
                    border_col = "#475569"
                    glow       = "rgba(71,85,105,0.2)"
                    dir_color  = "#94a3b8"
                    dir_icon   = "●"

                profit_color = "#4ade80" if profit_pct >= 0 else "#f87171"
                profit_str   = f"+{profit_pct:.1f}%" if profit_pct >= 0 else f"{profit_pct:.1f}%"

                with col:
                    st.markdown(
                        f"""<div style="background:{card_bg};border:1.5px solid {border_col};
                            border-radius:12px;padding:14px 14px 12px;
                            box-shadow:0 4px 20px {glow};position:relative;overflow:hidden">
                          <div style="position:absolute;top:0;right:0;width:60px;height:60px;
                              background:radial-gradient(circle,{glow} 0%,transparent 70%)"></div>
                          <div style="font-size:20px;font-weight:800;color:#f8fafc;letter-spacing:-0.5px">{ticker}</div>
                          <div style="font-size:11px;color:#94a3b8;margin-bottom:6px;white-space:nowrap;
                              overflow:hidden;text-overflow:ellipsis">{company}</div>
                          <div style="font-size:12px;font-weight:700;color:{dir_color};margin-bottom:4px">
                              {dir_icon} {direction} · {tf_label}
                          </div>
                          <div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px">
                            <span style="font-size:18px;font-weight:800;color:{profit_color}">{profit_str}</span>
                            <span style="background:rgba(255,255,255,0.1);border-radius:8px;padding:2px 8px;
                                font-size:11px;color:#e2e8f0">~{days}d</span>
                          </div>
                          <div style="font-size:11px;color:#94a3b8;margin-top:4px">{conf}% conf</div>
                          <div style="margin-top:6px">{age_badge}{_asset_badge(p)}</div>
                        </div>""",
                        unsafe_allow_html=True,
                    )
        st.markdown("<div style='margin-bottom:8px'></div>", unsafe_allow_html=True)

    # ── Timeframe sections ────────────────────────────────────────────────────
    section_styles = {
        "short":  ("linear-gradient(90deg,#0369a1,transparent)", "#38bdf8", "⚡"),
        "medium": ("linear-gradient(90deg,#15803d,transparent)", "#4ade80", "📈"),
        "long":   ("linear-gradient(90deg,#7c3aed,transparent)", "#a78bfa", "🌱"),
    }
    for label, tf_key, preds in [
        ("Short-term  (≤10 days)",   "short",  short[:10]),
        ("Medium-term (11–35 days)", "medium", medium[:10]),
        ("Long-term   (>35 days)",   "long",   long_[:10]),
    ]:
        if not preds:
            continue
        grad, accent, emoji = section_styles[tf_key]
        st.markdown(
            f"""<div style="display:flex;align-items:center;gap:10px;margin:20px 0 10px">
              <div style="height:2px;width:40px;background:{grad}"></div>
              <span style="font-size:15px;font-weight:700;color:#1e293b">{emoji} {label}</span>
              <div style="height:1px;flex:1;background:{grad}"></div>
              <span style="background:#f1f5f9;border:1px solid #e2e8f0;border-radius:20px;
                  padding:1px 10px;font-size:11px;color:#64748b">{len(preds)}</span>
            </div>""",
            unsafe_allow_html=True,
        )
        for p in preds:
            _prediction_card(p)

    if not short and not medium and not long_:
        _show_empty_state()


def _chart_panel():
    ticker = st.session_state.get("chart_ticker")
    pred   = st.session_state.get("chart_pred")

    if not ticker:
        st.markdown(
            """<div style="background:linear-gradient(135deg,#0f172a,#1e293b);border:1px dashed #334155;
                border-radius:12px;padding:20px;text-align:center;color:#64748b;font-size:14px;
                margin-bottom:4px">
              <div style="font-size:28px;margin-bottom:6px">📈</div>
              <div style="color:#94a3b8;font-weight:500">Click <strong style="color:#60a5fa">View Chart</strong> on any prediction to load its interactive chart</div>
            </div>""",
            unsafe_allow_html=True,
        )
        return

    col_title, col_close = st.columns([9, 1])
    with col_title:
        company = pred.get("company_name") or _get_company_name(ticker) if pred else ticker
        st.markdown(
            f"""<div style="background:linear-gradient(135deg,#0f172a,#1e3a5f);border-radius:10px;
                padding:12px 18px;margin-bottom:8px">
              <span style="font-size:18px;font-weight:800;color:#f8fafc">📈 {ticker}</span>
              <span style="font-size:14px;color:#94a3b8;margin-left:8px">— {company}</span>
              <div style="font-size:11px;color:#475569;margin-top:2px">
                MA20 · MA50 · Bollinger Bands · Volume · RSI(14) &nbsp;|&nbsp; Scroll to zoom · Drag to pan
              </div>
            </div>""",
            unsafe_allow_html=True,
        )
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
    ticker       = p.get("ticker", "—")
    direction    = p.get("direction", "NEUTRAL")
    confidence   = p.get("confidence", 0)
    score        = p.get("score", 0)
    position     = p.get("position", "HOLD")
    timeframe    = p.get("timeframe", "short")
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

    hc_tag  = "  🎯" if confidence >= 75 else ""
    age_tag = "  ·  ✨ NEW" if age_days == 0 else f"  ·  {age_days}d old"
    pos_tag = f"  ·  {position}" if position not in ("HOLD", "") else ""
    exp_tag = f"  ·  {days_left}d left" if days_left and days_left > 0 else ("  ·  expired" if days_left is not None and days_left <= 0 else "")

    dir_label = ":green[▲ BULLISH]" if direction == "BULLISH" else ":red[▼ BEARISH]" if direction == "BEARISH" else "● NEUTRAL"

    header = (
        f"**{ticker}** — {company}  ·  "
        f"{dir_label}  ·  "
        f"{confidence}% conf  ·  {score}/100  ·  "
        f"{profit_str}{pos_tag}  ·  ~{tenure_str}"
        f"{exp_tag}{age_tag}{hc_tag}"
    )

    pred_id = p.get("id") or f"{ticker}_{timeframe}_{predicted_on[:10]}"

    # Colored header background via CSS sibling selector
    if age_days == 0:
        card_bg = "background:linear-gradient(90deg,#f0fdf4,#dcfce7);border-left:4px solid #16a34a;"
    elif direction == "BEARISH":
        card_bg = "background:linear-gradient(90deg,#fef2f2,#fee2e2);border-left:4px solid #dc2626;"
    elif direction == "BULLISH":
        card_bg = "background:linear-gradient(90deg,#f0fdf4,#f8fafc);border-left:4px solid #15803d;"
    else:
        card_bg = "background:linear-gradient(90deg,#f8fafc,#f1f5f9);border-left:4px solid #94a3b8;"

    st.markdown(
        f'<style>'
        f'.pred-{pred_id} + div [data-testid="stExpander"] {{'
        f'  {card_bg}'
        f'  border-radius:10px !important;'
        f'  box-shadow:0 1px 4px rgba(0,0,0,0.06) !important;'
        f'  margin-bottom:4px !important;'
        f'}}'
        f'</style>'
        f'<div class="pred-{pred_id}" style="display:none"></div>',
        unsafe_allow_html=True,
    )

    with st.expander(header, expanded=False):
        btn_col, badge_col, del_col = st.columns([2, 7, 1])
        with btn_col:
            if st.button("📈 View Chart", key=f"chartbtn_{pred_id}"):
                st.session_state.chart_ticker = ticker
                st.session_state.chart_pred   = p
                st.rerun()
        with badge_col:
            st.markdown(f"<div style='padding-top:6px'>{age_badge}{_asset_badge(p)}</div>", unsafe_allow_html=True)
        with del_col:
            if st.button("✕", key=f"del_{pred_id}", help="Delete prediction"):
                try:
                    from database.db import soft_delete_prediction
                    soft_delete_prediction(pred_id)
                    st.rerun()
                except Exception as e:
                    st.error(f"Delete failed: {e}")

        # ── Stat pills ────────────────────────────────────────────────────────
        dir_color  = "#15803d" if direction == "BULLISH" else "#b91c1c" if direction == "BEARISH" else "#475569"
        prof_color = "#15803d" if profit_pct > 0 else "#b91c1c"
        st.markdown(
            f"""<div style="display:flex;gap:6px;flex-wrap:wrap;margin:10px 0 14px">
            {_pill("Direction", f"{'▲' if direction=='BULLISH' else '▼' if direction=='BEARISH' else '●'} {direction}", dir_color)}
            {_pill("Confidence", f"{confidence}%", "#1d4ed8")}
            {_pill("Score", f"{score}/100", "#7c3aed")}
            {_pill("Profit", profit_str, prof_color)}
            {_pill("Hold", f"~{tenure_str}", "#0369a1")}
            {_pill("R/R", f"1:{rr:.1f}", "#b45309")}
            {_pill("Position", position, "#374151")}
            </div>""",
            unsafe_allow_html=True,
        )

        # ── Entry / Target / Timing ───────────────────────────────────────────
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(
                f"""<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px 14px">
                  <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.8px;color:#94a3b8;margin-bottom:6px">Entry</div>
                  <div style="font-size:15px;font-weight:700;color:#0f172a">${entry:.2f}</div>
                  <div style="font-size:12px;color:#64748b;margin-top:4px">
                    Buy: ${p.get('buy_range_low',0):.2f} – ${p.get('buy_range_high',0):.2f}
                  </div>
                  <div style="font-size:12px;color:#ef4444;margin-top:2px">Stop: ${stop:.2f}</div>
                </div>""",
                unsafe_allow_html=True,
            )
        with c2:
            target_high = p.get("target_high", 0) or 0
            st.markdown(
                f"""<div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px 14px">
                  <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.8px;color:#16a34a;margin-bottom:6px">Target</div>
                  <div style="font-size:15px;font-weight:700;color:#15803d">${target:.2f} – ${target_high:.2f}</div>
                  <div style="font-size:13px;font-weight:700;color:{'#15803d' if profit_pct>0 else '#dc2626'};margin-top:4px">
                    {profit_str} potential
                  </div>
                  <div style="font-size:12px;color:#64748b;margin-top:2px">R/R: 1:{rr:.1f}</div>
                </div>""",
                unsafe_allow_html=True,
            )
        with c3:
            try:
                pred_dt_str = datetime.fromisoformat(predicted_on.replace("Z", "+00:00")).strftime("%b %d  %I:%M %p PT")
            except Exception:
                pred_dt_str = "—"
            exp_line = f"Expires: {expiry_str}" if expiry_str != "—" else "Expires: run scanner"
            days_exp = f" ({days_left}d left)" if days_left and days_left > 0 else ""
            st.markdown(
                f"""<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px 14px">
                  <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.8px;color:#94a3b8;margin-bottom:6px">Timing</div>
                  <div style="font-size:12px;color:#1e293b">📅 {pred_dt_str}</div>
                  <div style="font-size:12px;color:#1e293b;margin-top:3px">⏱ ~{days_to_target or '—'} days to target</div>
                  <div style="font-size:12px;color:#64748b;margin-top:3px">{exp_line}{days_exp}</div>
                  {f'<div style="font-size:11px;color:#7c3aed;margin-top:4px">💡 {p["timing_rationale"]}</div>' if p.get("timing_rationale") else ""}
                </div>""",
                unsafe_allow_html=True,
            )

        if p.get("reasoning"):
            st.markdown(
                f"""<div style="background:#f8fafc;border-left:3px solid #6366f1;border-radius:0 8px 8px 0;
                    padding:12px 16px;margin:12px 0 8px;font-size:13px;color:#1e293b;line-height:1.7">
                  <span style="font-size:11px;text-transform:uppercase;letter-spacing:0.8px;
                      color:#6366f1;font-weight:600;display:block;margin-bottom:4px">AI Reasoning</span>
                  {p['reasoning']}
                </div>""",
                unsafe_allow_html=True,
            )

        _news_links(ticker)

        if position == "SHORT":
            st.warning("⚠️ SHORT position — margin/options account required")


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
                        f'<div style="margin:5px 0;font-size:13px">'
                        f'<a href="{url}" target="_blank" style="color:#3b82f6;text-decoration:none">{headline}</a>{meta}</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f'<div style="margin:5px 0;font-size:13px;color:#374151">{headline}{meta}</div>',
                        unsafe_allow_html=True,
                    )
        else:
            st.caption("No recent news found via Finnhub.")

        st.markdown(
            f"""<div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
            <a href="https://finviz.com/quote.ashx?t={ticker}" target="_blank"
               style="background:#1e293b;border:1px solid #334155;border-radius:6px;padding:5px 12px;
               font-size:12px;color:#e2e8f0;text-decoration:none;font-weight:500">📊 FinViz</a>
            <a href="https://finance.yahoo.com/quote/{ticker}/news" target="_blank"
               style="background:#1e293b;border:1px solid #334155;border-radius:6px;padding:5px 12px;
               font-size:12px;color:#e2e8f0;text-decoration:none;font-weight:500">📰 Yahoo News</a>
            <a href="https://seekingalpha.com/symbol/{ticker}" target="_blank"
               style="background:#1e293b;border:1px solid #334155;border-radius:6px;padding:5px 12px;
               font-size:12px;color:#e2e8f0;text-decoration:none;font-weight:500">📝 Seeking Alpha</a>
            <a href="https://www.marketwatch.com/investing/stock/{ticker}" target="_blank"
               style="background:#1e293b;border:1px solid #334155;border-radius:6px;padding:5px 12px;
               font-size:12px;color:#e2e8f0;text-decoration:none;font-weight:500">📈 MarketWatch</a>
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
        f'background:#fff;border:1px solid #e2e8f0;border-radius:20px;'
        f'padding:4px 11px;font-size:12px;box-shadow:0 1px 2px rgba(0,0,0,0.04)">'
        f'<span style="color:#94a3b8;font-weight:400">{label}</span>'
        f'<strong style="color:{color}">{value}</strong></span>'
    )


def _show_empty_state():
    st.markdown(
        """<div style="background:linear-gradient(135deg,#0f172a,#1e293b);border:1px solid #334155;
            border-radius:14px;padding:40px;text-align:center;margin:20px 0">
          <div style="font-size:40px;margin-bottom:12px">🔍</div>
          <div style="font-size:18px;font-weight:700;color:#f8fafc;margin-bottom:6px">No predictions yet</div>
          <div style="font-size:14px;color:#64748b">The nightly scanner runs at 8:00 PM PT</div>
        </div>""",
        unsafe_allow_html=True,
    )
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
