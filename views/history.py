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
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(PT)
        days_left = (dt.date() - datetime.now(PT).date()).days
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

    # Only show closed predictions on this page
    all_closed = [p for p in all_preds if p.get("outcome") in ("WIN", "LOSS")]

    if not all_closed:
        st.info("No closed predictions yet. Predictions are closed automatically when target or stop loss is hit, or manually from the main dashboard.")
        return

    # ── Global success rate banner ────────────────────────────────────────────
    all_wins   = [p for p in all_closed if p.get("outcome") == "WIN"]
    all_losses = [p for p in all_closed if p.get("outcome") == "LOSS"]
    global_rate  = len(all_wins) / len(all_closed) * 100 if all_closed else 0
    rate_color   = "#15803d" if global_rate >= 60 else "#b45309" if global_rate >= 40 else "#b91c1c"
    avg_win_all  = sum(p.get("return_pct") or 0 for p in all_wins)  / len(all_wins)  if all_wins  else 0
    avg_loss_all = sum(p.get("return_pct") or 0 for p in all_losses) / len(all_losses) if all_losses else 0
    net_profit_all = sum(p.get("return_pct") or 0 for p in all_closed)
    net_color_all  = "#15803d" if net_profit_all >= 0 else "#b91c1c"
    net_str_all    = f"+{net_profit_all:.1f}%" if net_profit_all >= 0 else f"{net_profit_all:.1f}%"

    st.markdown(
        f"""<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;
            padding:18px 24px;margin-bottom:20px;display:flex;gap:32px;flex-wrap:wrap;align-items:center">
          <div>
            <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:4px">Overall Success Rate</div>
            <div style="font-size:36px;font-weight:800;color:{rate_color}">{global_rate:.1f}%</div>
            <div style="font-size:12px;color:#64748b">{len(all_closed)} closed trades</div>
          </div>
          <div style="width:1px;background:#e2e8f0;align-self:stretch"></div>
          <div>
            <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:4px">Net Profit</div>
            <div style="font-size:36px;font-weight:800;color:{net_color_all}">{net_str_all}</div>
            <div style="font-size:12px;color:#64748b">sum of all returns</div>
          </div>
          <div style="width:1px;background:#e2e8f0;align-self:stretch"></div>
          <div style="display:flex;gap:24px;flex-wrap:wrap">
            <div>
              <div style="font-size:11px;color:#64748b;margin-bottom:2px">Wins</div>
              <div style="font-size:22px;font-weight:700;color:#15803d">{len(all_wins)}</div>
              <div style="font-size:12px;color:#15803d">avg +{avg_win_all:.1f}%</div>
            </div>
            <div>
              <div style="font-size:11px;color:#64748b;margin-bottom:2px">Losses</div>
              <div style="font-size:22px;font-weight:700;color:#b91c1c">{len(all_losses)}</div>
              <div style="font-size:12px;color:#b91c1c">avg {avg_loss_all:.1f}%</div>
            </div>
            <div>
              <div style="font-size:11px;color:#64748b;margin-bottom:2px">Expectancy</div>
              <div style="font-size:22px;font-weight:700;color:#1e293b">{((global_rate/100*avg_win_all)+((1-global_rate/100)*avg_loss_all)):+.1f}%</div>
              <div style="font-size:12px;color:#64748b">per trade</div>
            </div>
          </div>
        </div>""",
        unsafe_allow_html=True,
    )

    # ── Filters ───────────────────────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        tf_filter = st.selectbox("Timeframe", ["All", "short", "medium", "long"])
    with col2:
        outcome_filter = st.selectbox("Outcome", ["All", "WIN", "LOSS"])
    with col3:
        tickers = sorted({p["ticker"] for p in all_closed})
        ticker_filter = st.selectbox("Ticker", ["All"] + tickers)
    with col4:
        conf_min = st.slider("Min Confidence", 0, 100, 0)

    filtered = all_closed
    if tf_filter != "All":
        filtered = [p for p in filtered if p.get("timeframe") == tf_filter]
    if outcome_filter != "All":
        filtered = [p for p in filtered if p.get("outcome") == outcome_filter]
    if ticker_filter != "All":
        filtered = [p for p in filtered if p.get("ticker") == ticker_filter]
    filtered = [p for p in filtered if (p.get("confidence") or 0) >= conf_min]

    def _sort_key(p):
        try:
            pred_dt = datetime.fromisoformat(p.get("predicted_on", "").replace("Z", "+00:00")).astimezone(PT)
            age = (datetime.now(PT).date() - pred_dt.date()).days
        except Exception:
            age = 999
        entry  = p.get("price_at_prediction") or 0
        target = p.get("target_low") or 0
        profit = ((target - entry) / entry * 100) if entry > 0 and target > 0 else 0
        return (age, -abs(profit), -p.get("score", 0))
    filtered = sorted(filtered, key=_sort_key)

    # ── By timeframe breakdown ────────────────────────────────────────────────
    st.markdown("**By timeframe:**")
    tf_cols = st.columns(3)
    for i, tf in enumerate(["short", "medium", "long"]):
        tf_closed = [p for p in all_closed if p.get("timeframe") == tf]
        tf_wins   = sum(1 for p in tf_closed if p.get("outcome") == "WIN")
        rate = tf_wins / len(tf_closed) * 100 if tf_closed else 0
        with tf_cols[i]:
            st.metric(f"{tf.capitalize()}-term", f"{rate:.1f}%", f"{len(tf_closed)} trades")

    st.markdown("---")

    # ── Prediction list grouped by recency ───────────────────────────────────
    st.markdown(f"### Closed Predictions ({len(filtered)} shown)")

    today_pt = datetime.now(PT).date()

    def _pred_date(p):
        try:
            return datetime.fromisoformat(p.get("predicted_on", "").replace("Z", "+00:00")).astimezone(PT).date()
        except Exception:
            return today_pt

    groups = {"Today": [], "Yesterday": [], "This Week": [], "This Month": [], "This Year": [], "Older": []}
    for p in filtered:
        d = _pred_date(p)
        delta = (today_pt - d).days
        if delta == 0:
            groups["Today"].append(p)
        elif delta == 1:
            groups["Yesterday"].append(p)
        elif delta <= 7:
            groups["This Week"].append(p)
        elif delta <= 30:
            groups["This Month"].append(p)
        elif delta <= 365:
            groups["This Year"].append(p)
        else:
            groups["Older"].append(p)

    for group_label, group_preds in groups.items():
        if not group_preds:
            continue
        wins_g  = sum(1 for p in group_preds if p.get("outcome") == "WIN")
        net_g   = sum(p.get("return_pct") or 0 for p in group_preds)
        net_str = f"{net_g:+.1f}%"
        with st.expander(
            f"**{group_label}** — {len(group_preds)} trades · "
            f"{wins_g}W / {len(group_preds)-wins_g}L · Net {net_str}",
            expanded=(group_label in ("Today", "Yesterday")),
        ):
            for p in group_preds:
                outcome    = p.get("outcome", "LOSS")
                ticker     = p.get("ticker", "—")
                direction  = p.get("direction", "NEUTRAL")
                timeframe  = p.get("timeframe", "short")
                confidence = p.get("confidence", 0)
                score      = p.get("score", 0)
                position   = p.get("position", "HOLD")
                ret        = p.get("return_pct")
                ret_str    = f"{ret:+.2f}%" if ret is not None else "—"

                entry      = p.get("price_at_prediction") or 0
                target     = p.get("target_low") or 0
                stop       = p.get("stop_loss") or 0
                profit_pct = ((target - entry) / entry * 100) if entry > 0 and target > 0 else 0
                profit_str = f"+{profit_pct:.1f}%" if profit_pct > 0 else f"{profit_pct:.1f}%"
                rr = abs(target - entry) / abs(entry - stop) if entry > 0 and stop > 0 and abs(entry - stop) > 0 else 0

                expiry_str, days_left = _expiry(p)
                days_to_target = p.get("days_to_target")
                tenure_str = f"{days_to_target}d" if days_to_target else "—"

                company      = p.get("company_name") or _get_company_name(ticker)
                outcome_icon = "🟢" if outcome == "WIN" else "🔴"
                dir_icon     = "▲" if direction == "BULLISH" else "▼" if direction == "BEARISH" else "●"
                pos_tag      = f"  ·  {position}" if position not in ("HOLD", "") else ""
                closed_reason = p.get("closed_reason", "")
                if closed_reason == "TARGET_HIT":
                    reason_tag = "  ·  :green[TARGET HIT]"
                elif closed_reason == "STOP_LOSS":
                    reason_tag = "  ·  :red[STOP LOSS]"
                elif closed_reason:
                    reason_tag = f"  ·  {closed_reason}"
                else:
                    reason_tag = ""

                header = (
                    f"{outcome_icon} **{ticker}** — {company}  ·  {dir_icon} {direction}  ·  "
                    f"{confidence}% conf  ·  {profit_str} potential  ·  ~{tenure_str}"
                    f"{pos_tag}  ·  {ret_str}{reason_tag}"
                )

                pred_id = p.get("id") or f"{ticker}_{timeframe}_{p.get('predicted_on','')[:10]}"
                with st.expander(header, expanded=False):
                    badge_html = _asset_badge(p)
                    bcol, dcol = st.columns([9, 1])
                    with bcol:
                        if badge_html:
                            st.markdown(f"<div style='margin-bottom:6px'>{badge_html}</div>", unsafe_allow_html=True)
                    with dcol:
                        if st.button("✕", key=f"hdel_{pred_id}", help="Delete"):
                            try:
                                from database.db import soft_delete_prediction
                                soft_delete_prediction(pred_id)
                                st.rerun()
                            except Exception as e:
                                st.error(f"Delete failed: {e}")

                    dir_color     = "#15803d" if direction == "BULLISH" else "#b91c1c" if direction == "BEARISH" else "#475569"
                    outcome_color = "#15803d" if outcome == "WIN" else "#b91c1c"
                    prof_color    = "#15803d" if profit_pct > 0 else "#b91c1c"
                    ret_color     = "#15803d" if (ret or 0) > 0 else "#b91c1c"
                    st.markdown(
                        f"""<div style="display:flex;gap:6px;flex-wrap:wrap;margin:8px 0 12px">
                        {_pill("Direction", f"{dir_icon} {direction}", dir_color)}
                        {_pill("Confidence", f"{confidence}%", "#1d4ed8")}
                        {_pill("Score", f"{score}/100", "#7c3aed")}
                        {_pill("Profit target", profit_str, prof_color)}
                        {_pill("R/R", f"1:{rr:.1f}", "#b45309")}
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
                        predicted_on = p.get("predicted_on", "")
                        verified_on  = p.get("verified_on", "")
                        if predicted_on and verified_on:
                            try:
                                v_dt = datetime.fromisoformat(verified_on.replace("Z", "+00:00")).astimezone(PT).date()
                                p_dt = datetime.fromisoformat(predicted_on.replace("Z", "+00:00")).astimezone(PT).date()
                                actual_days = (v_dt - p_dt).days
                                st.write(f"Actual days held: {actual_days}d")
                            except Exception:
                                pass
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
