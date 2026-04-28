import streamlit as st
from datetime import datetime
import pytz

PT = pytz.timezone("America/Los_Angeles")


@st.cache_data(ttl=3600)
def _get_company_name(ticker: str) -> str:
    try:
        from services.yfinance_service import get_ticker_info
        return get_ticker_info(ticker).get("name", ticker)
    except Exception:
        return ticker


def _expiry(p: dict):
    raw = p.get("expires_on") or ""
    if not raw:
        return "—", None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
        days_left = (dt - datetime.utcnow()).days
        return dt.strftime("%b %d, %Y"), days_left
    except Exception:
        return "—", None


def render():
    st.title("📜 History & Accuracy")

    try:
        from database.db import get_predictions
        all_preds = get_predictions(limit=1000)
    except Exception as e:
        st.error(f"Database error: {e}")
        return

    if not all_preds:
        st.info("No predictions logged yet.")
        return

    # ── Filters ───────────────────────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        tf_filter = st.selectbox("Timeframe", ["All", "short", "medium", "long"])
    with col2:
        outcome_filter = st.selectbox("Outcome", ["All", "WIN", "LOSS", "PENDING"])
    with col3:
        tickers = sorted({p["ticker"] for p in all_preds})
        ticker_filter = st.selectbox("Ticker", ["All"] + tickers)
    with col4:
        conf_min = st.slider("Min Confidence", 0, 100, 0)

    filtered = all_preds
    if tf_filter != "All":
        filtered = [p for p in filtered if p.get("timeframe") == tf_filter]
    if outcome_filter != "All":
        filtered = [p for p in filtered if p.get("outcome") == outcome_filter]
    if ticker_filter != "All":
        filtered = [p for p in filtered if p.get("ticker") == ticker_filter]
    filtered = [p for p in filtered if (p.get("confidence") or 0) >= conf_min]
    # Sort: newest first, then max profit, then max score
    def _sort_key(p):
        try:
            age = (datetime.utcnow() - datetime.fromisoformat(
                p.get("predicted_on", "").replace("Z", "+00:00")).replace(tzinfo=None)).days
        except Exception:
            age = 999
        entry  = p.get("price_at_prediction") or 0
        target = p.get("target_low") or 0
        profit = ((target - entry) / entry * 100) if entry > 0 and target > 0 else 0
        return (age, -profit, -p.get("score", 0))
    filtered = sorted(filtered, key=_sort_key)

    # ── Accuracy Summary ──────────────────────────────────────────────────────
    closed = [p for p in filtered if p.get("outcome") in ("WIN", "LOSS")]
    wins   = [p for p in closed if p.get("outcome") == "WIN"]
    losses = [p for p in closed if p.get("outcome") == "LOSS"]

    st.markdown("### Accuracy Summary")
    m1, m2, m3, m4, m5 = st.columns(5)
    with m1:
        win_rate = len(wins) / len(closed) * 100 if closed else 0
        st.metric("Win Rate", f"{win_rate:.1f}%", f"{len(closed)} closed trades")
    with m2:
        avg_win = sum(p.get("return_pct") or 0 for p in wins) / len(wins) if wins else 0
        st.metric("Avg Win", f"+{avg_win:.2f}%")
    with m3:
        avg_loss = sum(p.get("return_pct") or 0 for p in losses) / len(losses) if losses else 0
        st.metric("Avg Loss", f"{avg_loss:.2f}%")
    with m4:
        expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss) if closed else 0
        st.metric("Expectancy/Trade", f"{expectancy:+.2f}%", "positive = system works")
    with m5:
        pending = len([p for p in all_preds if p.get("outcome") == "PENDING"])
        st.metric("Open Trades", pending)

    st.markdown("**By timeframe:**")
    tf_cols = st.columns(3)
    for i, tf in enumerate(["short", "medium", "long"]):
        tf_closed = [p for p in closed if p.get("timeframe") == tf]
        tf_wins = sum(1 for p in tf_closed if p.get("outcome") == "WIN")
        rate = tf_wins / len(tf_closed) * 100 if tf_closed else 0
        with tf_cols[i]:
            st.metric(f"{tf.capitalize()}-term", f"{rate:.1f}%", f"{len(tf_closed)} trades")

    st.markdown("---")

    # ── Prediction list ───────────────────────────────────────────────────────
    st.markdown(f"### All Predictions ({len(filtered)} shown)")

    for p in filtered:
        outcome  = p.get("outcome", "PENDING")
        ticker   = p.get("ticker", "—")
        direction = p.get("direction", "NEUTRAL")
        timeframe = p.get("timeframe", "short")
        confidence = p.get("confidence", 0)
        score    = p.get("score", 0)
        position = p.get("position", "HOLD")
        ret      = p.get("return_pct")
        ret_str  = f"{ret:+.2f}%" if ret is not None else "—"

        entry  = p.get("price_at_prediction") or 0
        target = p.get("target_low") or 0
        profit_pct = ((target - entry) / entry * 100) if entry > 0 and target > 0 else 0
        profit_str = f"+{profit_pct:.1f}%" if profit_pct > 0 else f"{profit_pct:.1f}%"

        # Expiry
        expiry_str, days_left = _expiry(p)
        days_to_target = p.get("days_to_target")
        tenure_str = f"{days_to_target}d" if days_to_target else "—"

        company = p.get("company_name") or _get_company_name(ticker)
        outcome_icon = "🟢" if outcome == "WIN" else "🔴" if outcome == "LOSS" else "🟡"
        dir_icon = "▲" if direction == "BULLISH" else "▼" if direction == "BEARISH" else "●"
        days_label = f"  ·  {days_left}d left" if days_left and days_left > 0 else ("  ·  expired" if days_left is not None and days_left <= 0 else "")
        pos_tag = f"  ·  {position}" if position not in ("HOLD", "") else ""

        header = (
            f"{outcome_icon} **{ticker}** — {company}  ·  {dir_icon} {direction}  ·  "
            f"{confidence}% conf  ·  {score}/100  ·  "
            f"{profit_str} potential  ·  ~{tenure_str}"
            f"{pos_tag}  ·  {ret_str}{days_label}"
        )

        pred_id = p.get("id") or f"{ticker}_{timeframe}_{p.get('predicted_on','')[:10]}"
        with st.expander(header, expanded=False):
            stop  = p.get("stop_loss") or 0
            rr = abs(target - entry) / abs(entry - stop) if entry > 0 and stop > 0 and abs(entry - stop) > 0 else 0
            closed_reason = p.get("closed_reason", "")

            # Asset badge + delete button
            badge_html = _asset_badge(p)
            if badge_html:
                st.markdown(f"<div style='margin-bottom:6px'>{badge_html}</div>", unsafe_allow_html=True)
            if st.button("✕ Delete prediction", key=f"hdel_{pred_id}"):
                try:
                    from database.db import soft_delete_prediction
                    soft_delete_prediction(pred_id)
                    st.rerun()
                except Exception as e:
                    st.error(f"Delete failed: {e}")

            # Stat pills
            dir_color     = "#15803d" if direction == "BULLISH" else "#b91c1c" if direction == "BEARISH" else "#475569"
            outcome_color = "#15803d" if outcome == "WIN" else "#b91c1c" if outcome == "LOSS" else "#b45309"
            prof_color    = "#15803d" if profit_pct > 0 else "#b91c1c"
            ret_color     = "#15803d" if (ret or 0) > 0 else "#b91c1c"
            st.markdown(
                f"""<div style="display:flex;gap:6px;flex-wrap:wrap;margin:8px 0 12px">
                {_pill("Direction", f"{dir_icon} {direction}", dir_color)}
                {_pill("Confidence", f"{confidence}%", "#1d4ed8")}
                {_pill("Score", f"{score}/100", "#7c3aed")}
                {_pill("Profit target", profit_str, prof_color)}
                {_pill("Est. tenure", f"~{tenure_str}", "#0369a1")}
                {_pill("R/R", f"1 : {rr:.1f}", "#b45309")}
                {_pill("Outcome", outcome, outcome_color)}
                {_pill("Return", ret_str, ret_color)}
                </div>""",
                unsafe_allow_html=True,
            )

            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown("**Entry**")
                st.write(f"Price at signal: ${entry:.2f}")
                st.write(f"Buy range: ${p.get('buy_range_low', 0):.2f} – ${p.get('buy_range_high', 0):.2f}")
                st.write(f"Confidence: {confidence}%  ·  Score: {score}/100")
            with c2:
                st.markdown("**Exit**")
                close_price = p.get("price_at_close")
                st.write(f"Close price: ${close_price:.2f}" if close_price else "Not closed yet")
                st.write(f"Target: ${p.get('target_low', 0):.2f} – ${p.get('target_high', 0):.2f}")
                st.write(f"Stop loss: ${stop:.2f}")
                if closed_reason:
                    st.write(f"Closed by: {closed_reason}")
            with c3:
                st.markdown("**Timing**")
                st.write(f"Timeframe: {timeframe}  ·  Position: {position}")
                st.write(f"Est. days to target: {days_to_target or '—'}")
                if expiry_str != "—":
                    st.write(f"Expires: {expiry_str}{f'  ({days_left}d left)' if days_left and days_left > 0 else ''}")
                else:
                    st.write("Expires: run scanner to populate")
                if p.get("timing_rationale"):
                    st.caption(f"💡 {p['timing_rationale']}")

            if p.get("reasoning"):
                st.markdown(
                    f"""<div style="background:#f8fafc;border-left:3px solid #94a3b8;
                    border-radius:0 6px 6px 0;padding:8px 12px;margin-top:8px;
                    font-size:13px;color:#374151">{p['reasoning']}</div>""",
                    unsafe_allow_html=True,
                )

            if position == "SHORT":
                st.warning("SHORT position — margin/options account required")


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
        f'background:#f1f5f9;border:1px solid #e2e8f0;border-radius:20px;'
        f'padding:4px 10px;font-size:12px">'
        f'<span style="color:#64748b;font-weight:400">{label}:</span>'
        f'<strong style="color:{color}">{value}</strong></span>'
    )
