import streamlit as st
from datetime import datetime, timedelta
import pytz

TIMEFRAME_DAYS = {"short": 5, "medium": 28, "long": 180}
PT = pytz.timezone("America/Los_Angeles")


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
    # Sort by confidence desc
    filtered = sorted(filtered, key=lambda x: x.get("confidence", 0), reverse=True)

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
        expiry_dt = None
        try:
            raw = p.get("expires_on", "")
            if raw:
                expiry_dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
            else:
                pred_dt = datetime.fromisoformat(p.get("predicted_on", "").replace("Z", "+00:00")).replace(tzinfo=None)
                expiry_dt = pred_dt + timedelta(days=TIMEFRAME_DAYS.get(timeframe, 5))
        except Exception:
            pass
        days_left = (expiry_dt - datetime.utcnow()).days if expiry_dt else None
        expiry_str = expiry_dt.strftime("%b %d, %Y") if expiry_dt else "—"
        days_to_target = p.get("days_to_target")
        tenure_str = f"{days_to_target}d" if days_to_target else f"{TIMEFRAME_DAYS.get(timeframe, '?')}d"

        outcome_icon = "🟢" if outcome == "WIN" else "🔴" if outcome == "LOSS" else "🟡"
        dir_icon = "▲" if direction == "BULLISH" else "▼" if direction == "BEARISH" else "●"
        days_label = f"  ·  {days_left}d left" if days_left and days_left > 0 else ("  ·  expired" if days_left is not None and days_left <= 0 else "")
        pos_tag = f"  ·  {position}" if position not in ("HOLD", "") else ""

        header = (
            f"{outcome_icon} **{ticker}**  ·  {dir_icon} {direction}  ·  "
            f"{confidence}% conf  ·  {score}/100  ·  "
            f"{profit_str} potential  ·  ~{tenure_str}"
            f"{pos_tag}  ·  {ret_str}{days_label}"
        )

        with st.expander(header, expanded=False):
            stop  = p.get("stop_loss") or 0
            rr = abs(target - entry) / abs(entry - stop) if entry > 0 and stop > 0 and abs(entry - stop) > 0 else 0
            closed_reason = p.get("closed_reason", "")

            # Stat pills
            pill_color = "#15803d" if direction == "BULLISH" else "#b91c1c" if direction == "BEARISH" else "#64748b"
            outcome_color = "#15803d" if outcome == "WIN" else "#b91c1c" if outcome == "LOSS" else "#d97706"
            st.markdown(
                f"""<div style="display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 12px">
                {_pill("Direction", f"{dir_icon} {direction}", pill_color)}
                {_pill("Confidence", f"{confidence}%", "#1d4ed8")}
                {_pill("Score", f"{score}/100", "#7c3aed")}
                {_pill("Profit target", profit_str, "#15803d" if profit_pct > 0 else "#b91c1c")}
                {_pill("Est. tenure", f"~{tenure_str}", "#0369a1")}
                {_pill("R/R", f"1 : {rr:.1f}", "#d97706")}
                {_pill("Outcome", outcome, outcome_color)}
                {_pill("Return", ret_str, "#15803d" if (ret or 0) > 0 else "#b91c1c")}
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
                st.write(f"Expires: {expiry_str}{f'  ({days_left}d left)' if days_left and days_left > 0 else ''}")
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


def _pill(label: str, value: str, color: str) -> str:
    return (
        f'<span style="background:#f1f5f9;border:1px solid #e2e8f0;border-radius:20px;'
        f'padding:4px 10px;font-size:12px;color:#374151">'
        f'<span style="color:#94a3b8">{label}: </span>'
        f'<strong style="color:{color}">{value}</strong></span>'
    )
