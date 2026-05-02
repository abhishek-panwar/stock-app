import streamlit as st
from datetime import datetime, timedelta
import pytz

PT = pytz.timezone("America/Los_Angeles")

DIR_COLORS = {
    "BULLISH": ("#f0fdf4", "#16a34a", "#15803d"),
    "BEARISH": ("#fef2f2", "#dc2626", "#b91c1c"),
    "NEUTRAL": ("#f8fafc", "#94a3b8", "#64748b"),
}


@st.cache_data(ttl=3600)
def _fetch_open_predictions() -> list:
    from database.db import get_predictions
    return get_predictions({"outcome": "PENDING"}, limit=200)

@st.cache_data(ttl=3600)
def _fetch_scan_logs() -> list:
    from database.db import get_scan_logs
    return get_scan_logs(limit=1)

@st.cache_data(ttl=3600)
def _fetch_hot_tickers() -> list:
    from database.db import get_hot_tickers_from_db
    return get_hot_tickers_from_db()

@st.cache_data(ttl=3600)
def _fetch_earnings_calendar() -> list:
    from database.db import get_earnings_calendar_from_db
    return get_earnings_calendar_from_db()



def _age_info(predicted_on: str):
    try:
        pred_dt = datetime.fromisoformat(predicted_on.replace("Z", "+00:00")).astimezone(PT)
        today_pt = datetime.now(PT).date()
        from datetime import timedelta
        eff_date = pred_dt.date() + timedelta(days=1) if pred_dt.hour >= 16 else pred_dt.date()
        age = (today_pt - eff_date).days
    except Exception:
        return 0, ""
    if age < 0:
        return age, f'<span style="background:#eff6ff;color:#1d4ed8;border-radius:20px;padding:2px 9px;font-size:11px;font-weight:600">for tomorrow</span>'
    if age == 0:
        return age, f'<span style="background:#fef9c3;color:#713f12;border-radius:20px;padding:2px 9px;font-size:11px;font-weight:600">today</span>'
    return age, f'<span style="background:#f1f5f9;color:#64748b;border-radius:20px;padding:2px 9px;font-size:11px;font-weight:500">{age}d old</span>'


def _calc_entry(p: dict) -> float:
    """Mid of buy range, falls back to price_at_prediction."""
    bl = p.get("buy_range_low") or 0
    bh = p.get("buy_range_high") or 0
    return (bl + bh) / 2 if bl > 0 and bh > 0 else (p.get("price_at_prediction") or 0)


def _calc_profit_pct(p: dict) -> float:
    """Profit potential using mid buy range and mid target range."""
    entry    = _calc_entry(p)
    tgt_low  = p.get("target_low") or 0
    tgt_high = p.get("target_high") or 0
    tgt_mid  = (tgt_low + tgt_high) / 2 if tgt_low > 0 and tgt_high > 0 else tgt_low
    if entry <= 0 or tgt_mid <= 0:
        return 0.0
    direction = p.get("direction", "NEUTRAL")
    if direction == "BEARISH":
        return (entry - tgt_mid) / entry * 100
    return (tgt_mid - entry) / entry * 100


def _sort_key(p: dict):
    try:
        pred_dt = datetime.fromisoformat(p.get("predicted_on", "").replace("Z", "+00:00")).astimezone(PT)
        age = (datetime.now(PT).date() - pred_dt.date()).days
    except Exception:
        age = 999
    profit = _calc_profit_pct(p)
    return (age, -abs(profit), -p.get("score", 0))


def _expiry(p: dict):
    raw = p.get("expires_on") or ""
    if not raw:
        return "—", None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(PT)
        days_left = (dt.date() - datetime.now(PT).date()).days
        return dt.strftime("%b %d"), days_left
    except Exception:
        return "—", None


def _recalculate_open_math():
    status = st.status("Recalculating Math on predictions…", expanded=True)
    try:
        from database.db import get_predictions, update_prediction
        all_preds = get_predictions({"outcome": "PENDING"}, limit=200)
        status.write(f"Found {len(all_preds)} open predictions to recalculate…")
        updated = skipped = 0
        for p in all_preds:
            entry = _calc_entry(p)
            if entry <= 0:
                skipped += 1
                continue
            tgt_low  = p.get("target_low") or 0
            tgt_high = p.get("target_high") or 0
            tgt_mid  = (tgt_low + tgt_high) / 2 if tgt_low > 0 and tgt_high > 0 else tgt_low
            stop     = p.get("stop_loss") or 0
            direction = p.get("direction", "NEUTRAL")
            if tgt_mid <= 0:
                skipped += 1
                continue
            profit_pct = (tgt_mid - entry) / entry * 100 if direction != "BEARISH" \
                else (entry - tgt_mid) / entry * 100
            try:
                update_prediction(p["id"], {"price_at_prediction": round(entry, 6)})
                updated += 1
                status.write(f"  {p['ticker']} ({direction}): entry=${entry:.2f}  potential={profit_pct:+.1f}%")
            except Exception as e:
                status.write(f"  {p['ticker']}: error — {e}")
                skipped += 1
        status.update(
            label=f"Done — {updated} updated, {skipped} skipped",
            state="complete", expanded=False,
        )
        st.rerun()
    except Exception as e:
        status.update(label=f"Error: {e}", state="error", expanded=True)


def render():
    global _CARD_CSS_INJECTED
    _CARD_CSS_INJECTED = False  # re-inject once per render pass
    st.title("📊 Open Predictions")
    now_pt = datetime.now(PT)
    st.caption(f"Last updated: {now_pt.strftime('%b %d, %Y  %I:%M %p PT')}")

    try:
        predictions = _fetch_open_predictions()
        scan_logs   = _fetch_scan_logs()
    except Exception as e:
        st.error(f"Database connection error: {e}")
        st.info("No predictions yet. Use the **Prediction Tool** page to run the scanner.")
        return

    # Apply in-session deletes so removed predictions disappear instantly
    deleted = st.session_state.get("_open_deleted", set())
    if deleted:
        predictions = [p for p in predictions if p.get("id") not in deleted]

    if not predictions:
        st.info("No open predictions yet. Use the **Prediction Tool** page to run the scanner.")
        return

    if scan_logs:
        log = scan_logs[0]
        st.info(
            f"Universe: **{log.get('universe_total','—')} stocks** scanned  ·  "
            f"{log.get('hot_stock_count','—')} hot (Yahoo + Alpha Vantage) + "
            f"{log.get('nasdaq100_count','—')} Nasdaq with earnings  ·  "
            f"{log.get('overlap_count','—')} overlap deduplicated"
        )

    high_conviction = sorted(
        [p for p in predictions if (p.get("confidence") or 0) >= 75],
        key=_sort_key
    )

    st.markdown("---")

    # ── High conviction picks ─────────────────────────────────────────────────
    if high_conviction:
        st.markdown("### 🎯 High Conviction Picks")
        chunks = [high_conviction[i:i+5] for i in range(0, len(high_conviction), 5)]
        for chunk in chunks:
            cols = st.columns(len(chunk))
            for col, p in zip(cols, chunk):
                ticker     = p.get("ticker", "—")
                direction  = p.get("direction", "NEUTRAL")
                profit_pct = _calc_profit_pct(p)
                days       = p.get("days_to_target", "?")
                company   = p.get("company_name") or ticker
                _, age_badge = _age_info(p.get("predicted_on", ""))
                tf_label  = {"short": "⚡ Short", "medium": "📈 Mid", "long": "🌱 Long"}.get(p.get("timeframe", ""), "")
                conf      = p.get("confidence", 0)

                if direction == "BULLISH":
                    card_bg    = "linear-gradient(145deg,#f0fdf4,#dcfce7)"
                    border_col = "#16a34a"
                    glow       = "rgba(22,163,74,0.12)"
                    dir_color  = "#15803d"
                    dir_icon   = "▲"
                elif direction == "BEARISH":
                    card_bg    = "linear-gradient(145deg,#fef2f2,#fee2e2)"
                    border_col = "#dc2626"
                    glow       = "rgba(220,38,38,0.12)"
                    dir_color  = "#b91c1c"
                    dir_icon   = "▼"
                else:
                    card_bg    = "linear-gradient(145deg,#f8fafc,#f1f5f9)"
                    border_col = "#94a3b8"
                    glow       = "rgba(71,85,105,0.1)"
                    dir_color  = "#475569"
                    dir_icon   = "●"

                profit_color = "#15803d" if profit_pct >= 0 else "#b91c1c"
                profit_str   = f"+{profit_pct:.1f}%" if profit_pct >= 0 else f"{profit_pct:.1f}%"

                with col:
                    st.markdown(
                        f"""<div style="background:{card_bg};border:1.5px solid {border_col};
                            border-radius:12px;padding:14px 14px 12px;
                            box-shadow:0 4px 20px {glow};position:relative;overflow:hidden">
                          <div style="font-size:20px;font-weight:800;color:#0f172a;letter-spacing:-0.5px">{ticker}</div>
                          <div style="font-size:11px;color:#64748b;margin-bottom:6px;white-space:nowrap;
                              overflow:hidden;text-overflow:ellipsis">{company}</div>
                          <div style="font-size:12px;font-weight:700;color:{dir_color};margin-bottom:4px">
                              {dir_icon} {direction} · {tf_label}
                          </div>
                          <div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px">
                            <span style="font-size:18px;font-weight:800;color:{profit_color}">{profit_str}</span>
                            <span style="background:rgba(0,0,0,0.06);border-radius:8px;padding:2px 8px;
                                font-size:11px;color:#475569">~{days}d</span>
                          </div>
                          <div style="font-size:11px;color:#64748b;margin-top:4px">{conf}% conf</div>
                          <div style="margin-top:6px">{age_badge}{_asset_badge(p)}</div>
                        </div>""",
                        unsafe_allow_html=True,
                    )
        st.markdown("")

    # ── Sort control ──────────────────────────────────────────────────────────
    SORT_OPTIONS = {
        "Profit % (default)": lambda p: -abs(_calc_profit_pct(p)),
        "Confidence":         lambda p: -(p.get("confidence") or 0),
        "Score":              lambda p: -(p.get("score") or 0),
        "Risk/Reward": lambda p: -(
            abs(((p.get("target_low") or 0) + (p.get("target_high") or 0)) / 2 - _calc_entry(p)) /
            abs(_calc_entry(p) - (p.get("stop_loss") or _calc_entry(p)) or 1)
            if _calc_entry(p) > 0 and (p.get("stop_loss") or 0) > 0 else 0
        ),
        "Days to target":     lambda p: (p.get("days_to_target") or 999),
        "Newest first":       lambda p: p.get("predicted_on", ""),
    }
    sort_col, _ = st.columns([2, 8])
    with sort_col:
        sort_by = st.selectbox("Sort by", list(SORT_OPTIONS.keys()), key="open_sort_by", label_visibility="collapsed")
    sort_fn = SORT_OPTIONS[sort_by]

    # ── Timeframe + date-grouped prediction sections ──────────────────────────
    today_pt = datetime.now(PT).date()

    def _pred_date(p):
        try:
            return datetime.fromisoformat(p.get("predicted_on", "").replace("Z", "+00:00")).astimezone(PT).date()
        except Exception:
            return today_pt

    MARKET_CLOSE_HOUR = 16  # 4 PM PT

    def _effective_date(p):
        """Predictions made after 4 PM PT are for the next trading day."""
        try:
            pred_dt = datetime.fromisoformat(p.get("predicted_on", "").replace("Z", "+00:00")).astimezone(PT)
            if pred_dt.hour >= MARKET_CLOSE_HOUR:
                from datetime import timedelta
                return pred_dt.date() + timedelta(days=1)
            return pred_dt.date()
        except Exception:
            return today_pt

    def _date_bucket(p):
        eff = _effective_date(p)
        delta = (today_pt - eff).days
        if delta < 0:    return "📅 Tomorrow"
        if delta == 0:   return "✨ Today"
        if delta == 1:   return "Yesterday"
        if delta <= 7:   return "This Week"
        if delta <= 30:  return "This Month"
        return "Older"

    DATE_ORDER = ["📅 Tomorrow", "✨ Today", "Yesterday", "This Week", "This Month", "Older"]

    TF_CONFIG = [
        ("short",  "⚡ Short-term",  "#0369a1"),
        ("medium", "📈 Medium-term", "#7c3aed"),
        ("long",   "🌱 Long-term",   "#15803d"),
    ]

    for tf_key, tf_label, tf_color in TF_CONFIG:
        tf_preds = [p for p in predictions if p.get("timeframe") == tf_key]
        if not tf_preds:
            continue

        tf_hc = sum(1 for p in tf_preds if (p.get("confidence") or 0) >= 75)
        hc_badge = f"  · 🎯 {tf_hc} high conviction" if tf_hc else ""
        st.markdown(
            f'<div style="font-size:15px;font-weight:700;color:{tf_color};'
            f'margin:18px 0 6px;padding-left:2px">'
            f'{tf_label} — {len(tf_preds)} prediction{"s" if len(tf_preds) != 1 else ""}{hc_badge}</div>',
            unsafe_allow_html=True,
        )

        # sub-group by date within this timeframe
        date_groups: dict = {k: [] for k in DATE_ORDER}
        for p in tf_preds:
            date_groups[_date_bucket(p)].append(p)

        for bucket_label in DATE_ORDER:
            bucket_preds = date_groups[bucket_label]
            if not bucket_preds:
                continue
            bucket_preds = sorted(bucket_preds, key=sort_fn)
            with st.expander(
                f"**{bucket_label}** — {len(bucket_preds)} prediction{'s' if len(bucket_preds) != 1 else ''}",
                expanded=(bucket_label in ("📅 Tomorrow", "✨ Today")),
            ):
                for p in bucket_preds:
                    _prediction_card(p)

    if not predictions:
        st.info("No open predictions yet. Use the **Prediction Tool** page to run the scanner.")






_CARD_CSS_INJECTED = False

def _inject_card_css():
    global _CARD_CSS_INJECTED
    if _CARD_CSS_INJECTED:
        return
    _CARD_CSS_INJECTED = True
    st.markdown("""
<style>
/* Make card header buttons look like expander rows */
div[data-testid="stHorizontalBlock"]:has(button[data-card-header]) {
    gap: 0 !important;
}
button[data-card-header] {
    background: white !important;
    border: 1px solid #e2e8f0 !important;
    border-radius: 10px 0 0 10px !important;
    text-align: left !important;
    font-size: 13.5px !important;
    font-weight: 500 !important;
    color: #1e293b !important;
    padding: 10px 14px !important;
    width: 100% !important;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04) !important;
    transition: border-color 0.15s, box-shadow 0.15s !important;
}
button[data-card-header]:hover {
    border-color: #cbd5e1 !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08) !important;
    color: #0f172a !important;
}
button[data-card-del] {
    background: white !important;
    border: 1px solid #e2e8f0 !important;
    border-left: none !important;
    border-radius: 0 10px 10px 0 !important;
    color: #94a3b8 !important;
    font-size: 13px !important;
    padding: 10px 10px !important;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04) !important;
}
button[data-card-del]:hover {
    color: #dc2626 !important;
    border-color: #fca5a5 !important;
}
</style>
""", unsafe_allow_html=True)


def _prediction_card(p: dict, _unused: set = None):
    _inject_card_css()

    ticker       = p.get("ticker", "—")
    direction    = p.get("direction", "NEUTRAL")
    confidence   = p.get("confidence", 0)
    score        = p.get("score", 0)
    position     = p.get("position", "HOLD")
    timeframe    = p.get("timeframe", "short")
    predicted_on = p.get("predicted_on", "")

    company    = p.get("company_name") or ticker
    entry      = _calc_entry(p)
    tgt_low    = p.get("target_low") or 0
    tgt_high   = p.get("target_high") or 0
    tgt_mid    = (tgt_low + tgt_high) / 2 if tgt_low > 0 and tgt_high > 0 else tgt_low
    stop       = p.get("stop_loss") or 0
    profit_pct = _calc_profit_pct(p)
    rr         = abs(tgt_mid - entry) / abs(entry - stop) if entry > 0 and stop > 0 and abs(entry - stop) > 0 else 0
    profit_str = f"+{profit_pct:.1f}%" if profit_pct > 0 else f"{profit_pct:.1f}%"

    expiry_str, days_left = _expiry(p)
    days_to_target = p.get("days_to_target")
    tenure_str     = f"~{days_to_target}d" if days_to_target else "?"
    age_days, age_badge = _age_info(predicted_on)

    dir_icon   = "▲" if direction == "BULLISH" else "▼" if direction == "BEARISH" else "●"
    dir_color  = "#15803d" if direction == "BULLISH" else "#b91c1c" if direction == "BEARISH" else "#475569"
    prof_color = "#15803d" if profit_pct > 0 else "#b91c1c"
    dir_circle = "🟢" if direction == "BULLISH" else "🔴" if direction == "BEARISH" else "⚪"
    hc_tag     = "  🎯" if confidence >= 75 else ""
    pos_tag    = f"  ·  {position}" if position not in ("HOLD", "") else ""
    exp_tag    = (
        f"  ·  {days_left}d left" if days_left and days_left > 0
        else ("  ·  expired" if days_left is not None and days_left <= 0 else "")
    )

    pred_id = p.get("id") or f"{ticker}_{timeframe}_{predicted_on[:10]}"
    exp_key = f"exp_{pred_id}"
    is_open = st.session_state.get(exp_key, False)

    arrow  = "▼" if is_open else "▶"
    header = (
        f"{arrow}  {dir_circle} **{ticker}** — {company}  ·  {dir_icon} {direction}  ·  "
        f"{confidence}% conf  ·  {profit_str} potential  ·  {tenure_str}"
        f"{pos_tag}{exp_tag}  ·  {age_days}d old{hc_tag}"
    )

    # ── Header row: toggle button + delete (2 widget calls total when collapsed) ─
    hdr_col, del_col = st.columns([11.2, 0.4])
    with hdr_col:
        if st.button(header, key=f"toggle_{pred_id}", use_container_width=True):
            st.session_state[exp_key] = not is_open
            st.rerun()
    with del_col:
        if st.button("✕", key=f"del_{pred_id}", help="Delete"):
            try:
                from database.db import soft_delete_prediction
                soft_delete_prediction(pred_id)
                if "_open_deleted" not in st.session_state:
                    st.session_state["_open_deleted"] = set()
                st.session_state["_open_deleted"].add(pred_id)
                _fetch_open_predictions.clear()
                st.rerun()
            except Exception as e:
                st.error(f"Delete failed: {e}")

    # ── Body — skipped entirely when collapsed ─────────────────────────────────
    if not is_open:
        return

    with st.container(border=True):
        badge_html = _asset_badge(p)
        if badge_html:
            st.markdown(f"<div style='margin-bottom:6px'>{badge_html}</div>", unsafe_allow_html=True)

        st.markdown(
            f"""<div style="display:flex;gap:6px;flex-wrap:wrap;margin:6px 0 10px;align-items:center">
            {_pill("Direction", f"{dir_icon} {direction}", dir_color)}
            {_pill("Confidence", f"{confidence}%", "#1d4ed8")}
            {_pill("Score", f"{score}/100", "#7c3aed")}
            {_pill("Profit", profit_str, prof_color)}
            {_pill("R/R", f"1:{rr:.1f}", "#b45309")}
            {_pill("Hold", tenure_str, "#0369a1")}
            {_pill("Position", position, "#374151")}
            <span style="margin-left:2px">{age_badge}</span>
            </div>""",
            unsafe_allow_html=True,
        )

        bl = p.get('buy_range_low', 0); bh = p.get('buy_range_high', 0)
        tl = p.get('target_low', 0);   th = p.get('target_high', 0)
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("**Entry**")
            st.write(f"Price at signal: ${entry:.2f}")
            st.write(f"Buy range: ${bl:.2f} – ${bh:.2f}")
            st.write(f"Stop loss: ${stop:.2f}")
            mcap   = p.get("market_cap")
            avgvol = p.get("avg_volume")
            if mcap:
                st.write(f"Market cap: {'${:.1f}B'.format(mcap/1e9) if mcap >= 1e9 else '${:.0f}M'.format(mcap/1e6)}")
            if avgvol:
                st.write(f"Avg volume: {'{:.1f}M'.format(avgvol/1e6) if avgvol >= 1e6 else '{:.0f}K'.format(avgvol/1e3)}")
        with c2:
            st.markdown("**Target**")
            st.write(f"Range: ${tl:.2f} – ${th:.2f}")
            st.write(f"Profit potential: {profit_str}")
            st.write(f"Risk/Reward: 1 : {rr:.1f}")
            st.write(f"Score: {score}/100")
        with c3:
            st.markdown("**Timing**")
            try:
                pred_dt_str = datetime.fromisoformat(predicted_on.replace("Z", "+00:00")).astimezone(PT).strftime("%b %d  %I:%M %p PT")
            except Exception:
                pred_dt_str = "—"
            st.write(f"Predicted: {pred_dt_str}")
            st.write(f"Est. days to target: {days_to_target or '—'}")
            if expiry_str != "—":
                st.write(f"Expires: {expiry_str}{f'  ({days_left}d left)' if days_left and days_left > 0 else ''}")
            else:
                st.write("Expires: run scanner to populate")
            if p.get("timing_rationale"):
                st.caption(f"💡 {p['timing_rationale']}")

        if bl > 0 and bh > 0 and tl > 0:
            if direction == "BEARISH":
                formula_str = f"( ({bl:.2f}+{bh:.2f})/2 - ({tl:.2f}+{th:.2f})/2 ) / ({bl:.2f}+{bh:.2f})/2 = {profit_pct:+.1f}%"
            else:
                formula_str = f"( ({tl:.2f}+{th:.2f})/2 - ({bl:.2f}+{bh:.2f})/2 ) / ({bl:.2f}+{bh:.2f})/2 = {profit_pct:+.1f}%"
            st.markdown(f"**Profit formula:** `{formula_str}`")

        if p.get("reasoning"):
            st.markdown(
                f"""<div style="background:#f8fafc;border-left:3px solid #94a3b8;border-radius:0 6px 6px 0;
                padding:8px 12px;margin-top:8px;font-size:13px;color:#374151">{p['reasoning']}</div>""",
                unsafe_allow_html=True,
            )

        _news_links(ticker)

        if position == "SHORT":
            st.warning("SHORT position — margin/options account required")

        st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)
        ms_col, mf_col, _ = st.columns([1.5, 1.5, 7])
        with ms_col:
            if st.button("✅ Mark Success", key=f"win_{pred_id}"):
                try:
                    from database.db import update_prediction
                    update_prediction(pred_id, {
                        "outcome": "WIN",
                        "closed_reason": "MANUAL",
                        "verified_on": datetime.now(PT).isoformat(),
                    })
                    _fetch_open_predictions.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed: {e}")
        with mf_col:
            if st.button("❌ Mark Failure", key=f"loss_{pred_id}"):
                try:
                    from database.db import update_prediction
                    update_prediction(pred_id, {
                        "outcome": "LOSS",
                        "closed_reason": "MANUAL",
                        "verified_on": datetime.now(PT).isoformat(),
                    })
                    _fetch_open_predictions.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed: {e}")


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
                    date_str = datetime.fromtimestamp(ts, tz=PT).strftime("%b %d") if ts else ""
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
    badges = ""
    if asset == "crypto":
        badges += '<span style="background:#1e1b4b;color:#a5b4fc;border-radius:20px;padding:2px 8px;font-size:11px;font-weight:700;margin-left:4px">₿ CRYPTO</span>'
    elif asset == "commodity":
        badges += '<span style="background:#451a03;color:#fcd34d;border-radius:20px;padding:2px 8px;font-size:11px;font-weight:700;margin-left:4px">⬡ COMMODITY</span>'
    if p.get("earnings_label"):
        badges += f'<span style="background:#78350f;color:#fde68a;border-radius:20px;padding:2px 8px;font-size:11px;font-weight:700;margin-left:4px">{p["earnings_label"]}</span>'
    if p.get("insider_signal"):
        badges += f'<span style="background:#3b0764;color:#e9d5ff;border-radius:20px;padding:2px 8px;font-size:11px;font-weight:700;margin-left:4px">{p["insider_signal"]}</span>'
    return badges


def _pill(label: str, value: str, color: str) -> str:
    return (
        f'<span style="display:inline-flex;align-items:center;gap:4px;'
        f'background:#f1f5f9;border:1px solid #e2e8f0;border-radius:20px;padding:4px 10px;font-size:12px">'
        f'<span style="color:#64748b;font-weight:400">{label}:</span>'
        f'<strong style="color:{color}">{value}</strong></span>'
    )


