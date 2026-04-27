import streamlit as st
import plotly.graph_objects as go
from datetime import datetime
import pytz

PT = pytz.timezone("America/Los_Angeles")


def render():
    st.title("📊 Today's Best Setups")
    now_pt = datetime.now(PT)
    st.caption(f"Last updated: {now_pt.strftime('%b %d, %Y  %I:%M %p PT')}")

    try:
        from database.db import get_predictions, get_scan_logs
        predictions = get_predictions({"outcome": "PENDING"}, limit=200)
        scan_logs = get_scan_logs(limit=1)
    except Exception as e:
        st.error(f"Database connection error: {e}")
        _show_empty_state()
        return

    if not predictions:
        _show_empty_state()
        return

    # Universe info from latest scan log
    if scan_logs:
        log = scan_logs[0]
        total = log.get("universe_total", "—")
        n100 = log.get("nasdaq100_count", "—")
        hot = log.get("hot_stock_count", "—")
        overlap = log.get("overlap_count", "—")
        st.info(f"Universe: **{total} stocks** ({n100} Nasdaq + {hot} hot → {overlap} overlap, deduplicated)")

    # Group by timeframe
    short = [p for p in predictions if p.get("timeframe") == "short"]
    medium = [p for p in predictions if p.get("timeframe") == "medium"]
    long_ = [p for p in predictions if p.get("timeframe") == "long"]

    # All-timeframes agree
    short_tickers = {p["ticker"] for p in short}
    medium_tickers = {p["ticker"] for p in medium}
    long_tickers = {p["ticker"] for p in long_}
    all_agree_tickers = short_tickers & medium_tickers & long_tickers

    # All Timeframes Agree section
    if all_agree_tickers:
        st.markdown("### 🎯 All Timeframes Agree — Highest Conviction")
        agree_cols = st.columns(min(len(all_agree_tickers), 4))
        for i, ticker in enumerate(all_agree_tickers):
            p = next((x for x in short if x["ticker"] == ticker), None) or predictions[0]
            with agree_cols[i % 4]:
                direction_color = "🟢" if p.get("direction") == "BULLISH" else "🔴" if p.get("direction") == "BEARISH" else "⚪"
                st.metric(
                    label=f"{direction_color} {ticker}",
                    value=f"{p.get('confidence', 0)}% confidence",
                    delta=f"Score: {p.get('score', 0)}/100",
                )
        st.markdown("---")

    # Timeframe sections
    for label, emoji, preds in [
        ("Short-term (2–5 days)", "⚡", short[:10]),
        ("Medium-term (1–4 weeks)", "📈", medium[:10]),
        ("Long-term (1–6 months)", "🌱", long_[:10]),
    ]:
        if not preds:
            continue
        st.markdown(f"### {emoji} {label}")
        for p in preds:
            _prediction_card(p, all_agree_tickers)
        st.markdown("")

    if not short and not medium and not long_:
        _show_empty_state()


def _prediction_card(p: dict, all_agree_tickers: set):
    ticker = p.get("ticker", "—")
    direction = p.get("direction", "NEUTRAL")
    confidence = p.get("confidence", 0)
    score = p.get("score", 0)
    position = p.get("position", "HOLD")
    agreed = ticker in all_agree_tickers

    color = "#1a9e1a" if direction == "BULLISH" else "#c0392b" if direction == "BEARISH" else "#7f8c8d"
    agree_badge = " 🎯" if agreed else ""

    with st.expander(
        f"**{ticker}**  {direction} {position}  |  Confidence {confidence}%  |  Score {score}/100{agree_badge}",
        expanded=False,
    ):
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(f"**Buy Range**")
            st.markdown(f"${p.get('buy_range_low', 0):.2f} – ${p.get('buy_range_high', 0):.2f}")
            st.markdown(f"**Stop Loss**")
            st.markdown(f"${p.get('stop_loss', 0):.2f}")
        with c2:
            st.markdown(f"**Target**")
            st.markdown(f"${p.get('target_low', 0):.2f} – ${p.get('target_high', 0):.2f}")
            st.markdown(f"**Source**")
            st.markdown(p.get("source", "—"))
        with c3:
            st.markdown(f"**Formula**")
            st.markdown(p.get("formula_version", "v1.0"))
            predicted = p.get("predicted_on", "")
            if predicted:
                try:
                    dt = datetime.fromisoformat(predicted.replace("Z", "+00:00"))
                    st.caption(f"Predicted: {dt.strftime('%b %d  %I:%M %p PT')}")
                except Exception:
                    pass

        if p.get("reasoning"):
            st.markdown(f"> {p['reasoning']}")

        if position == "SHORT":
            st.warning("⚠️ SHORT position requires a margin account.")

    # Fetch and show chart
    _mini_chart(ticker)


def _mini_chart(ticker: str):
    try:
        from services.yfinance_service import get_price_history
        from indicators.technicals import compute_all
        df = get_price_history(ticker, period="3mo")
        if df.empty:
            return
        ind = compute_all(df)
        fig = go.Figure()
        fig.add_trace(go.Candlestick(
            x=df.index, open=df["open"], high=df["high"],
            low=df["low"], close=df["close"],
            name=ticker, showlegend=False,
        ))
        if ind.get("ma20"):
            from indicators.technicals import get_ma_series
            ma20 = get_ma_series(df["close"], 20)
            fig.add_trace(go.Scatter(x=df.index, y=ma20, name="MA20", line=dict(color="orange", width=1)))
        if ind.get("ma50"):
            from indicators.technicals import get_ma_series
            ma50 = get_ma_series(df["close"], 50)
            fig.add_trace(go.Scatter(x=df.index, y=ma50, name="MA50", line=dict(color="blue", width=1)))
        fig.update_layout(
            height=200, margin=dict(l=0, r=0, t=20, b=0),
            xaxis_rangeslider_visible=False,
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True, key=f"chart_{ticker}_{id(fig)}")
    except Exception:
        pass


def _show_empty_state():
    st.info("No predictions yet. The nightly scanner runs at 8:00 PM PT and will populate this page.")
    st.markdown("""
**How to run the first scan manually:**
```bash
cd "Stock app"
python3 scripts/nightly_scanner.py
```
""")
