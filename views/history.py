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

    # ── Accuracy Summary ──────────────────────────────────────────────────────
    closed = [p for p in filtered if p.get("outcome") in ("WIN", "LOSS")]
    wins = [p for p in closed if p.get("outcome") == "WIN"]
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

    # Timeframe breakdown
    st.markdown("**By timeframe:**")
    tf_cols = st.columns(3)
    for i, tf in enumerate(["short", "medium", "long"]):
        tf_closed = [p for p in closed if p.get("timeframe") == tf]
        tf_wins = sum(1 for p in tf_closed if p.get("outcome") == "WIN")
        rate = tf_wins / len(tf_closed) * 100 if tf_closed else 0
        with tf_cols[i]:
            st.metric(f"{tf.capitalize()}-term", f"{rate:.1f}%", f"{len(tf_closed)} trades")

    st.markdown("---")

    # ── Prediction Table ──────────────────────────────────────────────────────
    st.markdown(f"### All Predictions ({len(filtered)} shown)")

    for p in filtered:
        outcome = p.get("outcome", "PENDING")
        color = "🟢" if outcome == "WIN" else "🔴" if outcome == "LOSS" else "🟡"
        ret = p.get("return_pct")
        ret_str = f"{ret:+.2f}%" if ret is not None else "—"
        closed_reason = p.get("closed_reason", "")

        with st.expander(
            f"{color} **{p['ticker']}**  {p.get('timeframe','').capitalize()}-term  {p.get('direction','')}  |  {ret_str}  |  {outcome}",
            expanded=False,
        ):
            c1, c2, c3 = st.columns(3)
            # Compute expires_on for display — prefer scanner's data-driven value
            expires_display = "—"
            try:
                raw_expiry = p.get("expires_on", "")
                if raw_expiry:
                    expires_display = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00")).strftime("%b %d, %Y")
                else:
                    pred_dt = datetime.fromisoformat(p.get("predicted_on", "").replace("Z", "+00:00")).replace(tzinfo=None)
                    expires_display = (pred_dt + timedelta(days=TIMEFRAME_DAYS.get(p.get("timeframe","short"), 5))).strftime("%b %d, %Y")
            except Exception:
                pass

            with c1:
                st.markdown("**Entry**")
                st.write(f"Price: ${p.get('price_at_prediction', 0):.2f}")
                st.write(f"Range: ${p.get('buy_range_low', 0):.2f} – ${p.get('buy_range_high', 0):.2f}")
                st.write(f"Confidence: {p.get('confidence', 0)}%")
                st.write(f"Score: {p.get('score', 0)}/100")
            with c2:
                st.markdown("**Exit**")
                close_price = p.get("price_at_close")
                st.write(f"Close price: ${close_price:.2f}" if close_price else "Not closed yet")
                st.write(f"Expires: {expires_display}")
                st.write(f"Target: ${p.get('target_low', 0):.2f} – ${p.get('target_high', 0):.2f}")
                st.write(f"Stop loss: ${p.get('stop_loss', 0):.2f}")
                if closed_reason:
                    st.write(f"Closed by: {closed_reason}")
            with c3:
                st.markdown("**Details**")
                st.write(f"Timeframe: {p.get('timeframe', '—')}")
                st.write(f"Position: {p.get('position', '—')}")
                st.write(f"Source: {p.get('source', '—')}")
                st.write(f"Formula: {p.get('formula_version', '—')}")

            if p.get("reasoning"):
                st.markdown(f"**Reasoning:** {p['reasoning']}")

            if p.get("position") == "SHORT":
                st.warning("SHORT — margin account required")
