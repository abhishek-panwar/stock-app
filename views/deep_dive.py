import streamlit as st
from datetime import datetime, timedelta
import pytz

PT = pytz.timezone("America/Los_Angeles")


def render():
    st.title("🔬 Deep Dive — Forensic Stock Analysis")
    st.caption("Analyze any US stock to understand what drove a move and what signals could have caught it.")

    # ── Stock search ──────────────────────────────────────────────────────────
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        ticker_input = st.text_input(
            "Enter ticker(s)", placeholder="e.g. NVDA or NVDA, TSLA, AAPL",
            help="Any valid US stock ticker. Not limited to the nightly universe."
        ).upper().strip()
    with col2:
        date_from = st.date_input("From", value=datetime.now().date() - timedelta(days=90))
    with col3:
        date_to = st.date_input("To", value=datetime.now().date())

    analyze_btn = st.button("🔍 Analyze", type="primary")

    if not analyze_btn or not ticker_input:
        _show_past_sessions()
        return

    tickers = [t.strip() for t in ticker_input.split(",") if t.strip()]

    for ticker in tickers:
        st.markdown(f"---")
        st.markdown(f"## {ticker}")
        _run_analysis(ticker, str(date_from), str(date_to))


def _run_analysis(ticker: str, date_from: str, date_to: str):
    from services.yfinance_service import get_price_history, get_ticker_info
    from services.finnhub_service import get_historical_news
    from indicators.technicals import compute_all
    from indicators.scoring import compute_signal_score, FORMULA_VERSION
    from services.ai_service import analyze_forensic
    from database.db import insert_forensic_session, insert_formula_suggestion
    import pandas as pd

    with st.spinner(f"Fetching data for {ticker}..."):
        df = get_price_history(ticker, period="1y")
        if df.empty:
            st.error(f"No price data found for {ticker}. Check the ticker symbol.")
            return

        # Filter to date range
        df_range = df[df.index >= pd.Timestamp(date_from)]
        df_range = df_range[df_range.index <= pd.Timestamp(date_to)]
        if df_range.empty:
            st.warning(f"No data in selected date range for {ticker}.")
            df_range = df.tail(90)

        info = get_ticker_info(ticker)
        news = get_historical_news(ticker, date_from, date_to)

    # ── 1. Event Timeline Chart ───────────────────────────────────────────────
    st.markdown("### 1. Event Timeline")

    ind = compute_all(df)

    # Detect largest move for metrics
    total_move = 0
    if len(df_range) > 1:
        total_move = ((df_range["close"].iloc[-1] - df_range["close"].iloc[0]) / df_range["close"].iloc[0]) * 100
        max_drawup = df_range["close"].pct_change().max() * 100
        max_drawdown = df_range["close"].pct_change().min() * 100
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric(f"{ticker} — {info.get('name', ticker)}", f"{total_move:+.2f}%", "Period move")
        with col2:
            st.metric("Best single day", f"+{max_drawup:.2f}%")
        with col3:
            st.metric("Worst single day", f"{max_drawdown:.2f}%")

    # Build news timestamps for markers
    news_timestamps = []
    for article in news[:10]:
        ts = article.get("datetime", 0)
        if ts:
            try:
                news_timestamps.append(int(ts))
            except Exception:
                pass

    from services.chart_service import build_forensic_chart
    from streamlit_lightweight_charts import renderLightweightCharts

    charts = build_forensic_chart(df_range, news_dates=news_timestamps, ticker=ticker, height=500)
    if charts:
        st.caption(f"**{ticker}** · MA20 (orange) · MA50 (blue) · Bollinger Bands · Volume · RSI  |  📰 = news article")
        renderLightweightCharts(charts, key=f"forensic_{ticker}_{date_from}")

    # ── 2. Signal Autopsy ─────────────────────────────────────────────────────
    st.markdown("### 2. Signal Autopsy")
    st.caption("Which indicators fired before the move, and which were missed?")

    ind_full = compute_all(df)
    if ind_full:
        signal_data = [
            ("RSI", f"{ind_full.get('rsi', 0):.1f}", "✅ Oversold signal" if ind_full.get('rsi', 50) < 35 else "⚪ Neutral" if ind_full.get('rsi', 50) < 65 else "⚠️ Overbought"),
            ("MACD Crossover", "Yes" if ind_full.get("macd_crossover") else "No", "✅ Bullish crossover" if ind_full.get("macd_crossover") else "—"),
            ("RSI Divergence", "Yes" if ind_full.get("rsi_divergence") else "No", "✅ Hidden bullish" if ind_full.get("rsi_divergence") else "—"),
            ("Bollinger Squeeze", "Yes" if ind_full.get("bb_squeeze") else "No", "✅ Breakout imminent" if ind_full.get("bb_squeeze") else "—"),
            ("Volume Surge", f"{ind_full.get('volume_surge_ratio', 1):.1f}x", "✅ Strong" if ind_full.get('volume_surge_ratio', 1) >= 2 else "⚠️ Weak" if ind_full.get('volume_surge_ratio', 1) < 1.2 else "⚪"),
            ("OBV Trend", ind_full.get("obv_trend", "—"), "✅ Confirming" if ind_full.get("obv_trend") == "CONFIRMING" else "✅ Bullish div" if "BULLISH" in (ind_full.get("obv_trend") or "") else "—"),
            ("Golden Cross", "Yes" if ind_full.get("golden_cross") else "No", "✅ Bullish" if ind_full.get("golden_cross") else "—"),
            ("52w High Breakout", "Yes" if ind_full.get("broke_52w_high") else "Near" if ind_full.get("near_52w_high") else "No", "✅ Breakout" if ind_full.get("broke_52w_high") else "—"),
            ("Price vs VWAP", "Above" if ind_full.get("price_above_vwap") else "Below", "✅ Bullish bias" if ind_full.get("price_above_vwap") else "⚠️ Bearish bias"),
        ]
        import pandas as pd
        autopsy_df = pd.DataFrame(signal_data, columns=["Indicator", "Value", "Signal"])
        st.dataframe(autopsy_df, use_container_width=True, hide_index=True)

    # ── 3. Claude Forensic Analysis ───────────────────────────────────────────
    st.markdown("### 3. AI Forensic Analysis & Formula Suggestions")

    with st.spinner("Running Claude forensic analysis..."):
        price_summary = f"{ticker}: {total_move:+.2f}% over {len(df_range)} trading days. "
        if ind_full:
            price_summary += f"RSI: {ind_full.get('rsi', 0):.1f}, Volume surge: {ind_full.get('volume_surge_ratio', 1):.1f}x"

        news_summary = "\n".join(
            f"- {a['headline'][:80]} ({a['source']})" for a in news[:5]
        ) or "No news data available"

        indicators_fired = []
        if ind_full:
            if ind_full.get("macd_crossover"):
                indicators_fired.append("MACD bullish crossover")
            if ind_full.get("rsi_divergence"):
                indicators_fired.append("RSI divergence (bullish)")
            if ind_full.get("bb_squeeze"):
                indicators_fired.append("Bollinger squeeze")
            if (ind_full.get("volume_surge_ratio") or 1) >= 2:
                indicators_fired.append(f"Volume surge ({ind_full.get('volume_surge_ratio', 1):.1f}x)")
            if ind_full.get("golden_cross"):
                indicators_fired.append("Golden cross (MA20 > MA50)")
            if ind_full.get("broke_52w_high"):
                indicators_fired.append("52-week high breakout")

        result = analyze_forensic(
            ticker=ticker,
            price_summary=price_summary,
            indicators_timeline=", ".join(indicators_fired) or "No clear signals detected",
            news_timeline=news_summary,
            date_range=f"{date_from} to {date_to}",
        )

    if result:
        st.markdown(f"**What happened:** {result.get('event_summary', '—')}")
        st.markdown(f"**Earliest signal:** {result.get('earliest_signal', '—')}")

        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**Signals that fired:**")
            for s in result.get("signals_that_fired", []):
                st.markdown(f"- ✅ {s}")
        with col_b:
            st.markdown("**Signals missed by formula:**")
            for s in result.get("signals_missed", []):
                st.markdown(f"- ⚠️ {s}")

        suggestions = result.get("formula_suggestions", [])
        if suggestions:
            st.markdown("**Formula improvement suggestions:**")
            for s in suggestions:
                with st.expander(f"💡 {s.get('plain_english', '')[:80]}"):
                    st.markdown(f"**Technical:** {s.get('technical_detail', '—')}")
                    st.markdown(f"**Projected benefit:** {s.get('projected_benefit', '—')}")
                    if st.button(f"Send to Approval Queue", key=f"suggest_{ticker}_{suggestions.index(s)}"):
                        try:
                            insert_formula_suggestion({
                                "suggestion_date": datetime.now(PT).isoformat(),
                                "suggested_by": "claude",
                                "source": "deep_dive",
                                "plain_english": s.get("plain_english", ""),
                                "technical_detail": s.get("technical_detail", ""),
                                "evidence": {"ticker": ticker, "date_range": f"{date_from} to {date_to}"},
                                "projected_improvement": 0,
                                "status": "PENDING",
                            })
                            st.success("Added to System Evolution queue.")
                        except Exception as e:
                            st.error(f"Error saving: {e}")

        # Log the session
        try:
            insert_forensic_session({
                "ticker": ticker,
                "analyzed_on": datetime.now(PT).isoformat(),
                "date_range_start": date_from,
                "date_range_end": date_to,
                "move_detected_pct": round(total_move, 2) if "total_move" in dir() else 0,
                "move_direction": "UP" if total_move > 0 else "DOWN",
                "signals_that_fired": result.get("signals_that_fired", []),
                "signals_missed": result.get("signals_missed", []),
                "suggestions_generated": len(suggestions),
                "session_source": "deep_dive",
            })
        except Exception:
            pass

    # ── Past news ─────────────────────────────────────────────────────────────
    if news:
        st.markdown("### News Articles in Period")
        for a in news[:10]:
            ts = a.get("datetime", 0)
            date_str = datetime.fromtimestamp(ts).strftime("%b %d") if ts else "—"
            url = a.get("url", "")
            headline = a.get("headline", "—")
            source = a.get("source", "—")
            if url:
                st.markdown(f"- [{headline[:80]}]({url}) — *{source}* ({date_str})")
            else:
                st.markdown(f"- {headline[:80]} — *{source}* ({date_str})")


def _show_past_sessions():
    try:
        from database.db import get_forensic_sessions
        sessions = get_forensic_sessions()
        if sessions:
            st.markdown("### Past Forensic Sessions")
            for s in sessions[:10]:
                ticker = s.get("ticker", "—")
                move = s.get("move_detected_pct", 0)
                direction = s.get("move_direction", "")
                analyzed = s.get("analyzed_on", "")
                suggestions = s.get("suggestions_generated", 0)
                st.markdown(
                    f"- **{ticker}** — {move:+.1f}% {direction}  |  "
                    f"{suggestions} suggestion(s) generated  |  {analyzed[:10]}"
                )
    except Exception:
        pass
    st.info("Enter a ticker above to run a forensic analysis.")
