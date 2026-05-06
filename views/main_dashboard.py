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

        def _fmt(val, suffix=""):
            return f"{val}{suffix}" if val not in (None, "", "—") else "—"

        superset        = _fmt(log.get("superset_count"))
        fetched         = _fmt(log.get("tickers_fetched"))
        passed_filter   = _fmt(log.get("universe_total"))
        scored          = _fmt(log.get("stocks_scored"))
        sent_claude     = _fmt(log.get("stocks_analyzed"))
        predictions_cnt = _fmt(log.get("predictions_created"))
        hot             = _fmt(log.get("hot_stock_count"))
        nasdaq_earn     = _fmt(log.get("nasdaq100_count"))
        overlap         = _fmt(log.get("overlap_count"))

        # Compute pass rates where possible
        def _pct(num, denom):
            try:
                return f" ({int(num)/int(denom)*100:.0f}%)"
            except Exception:
                return ""

        st.markdown(
            f"""<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:12px 16px;font-size:13px;line-height:2">
            <span style="font-weight:700;color:#1e293b">Last scan funnel</span><br>
            <span style="color:#64748b">Raw candidates</span> &nbsp;
            <span style="font-weight:600">{superset}</span>
            <span style="color:#94a3b8"> ({hot} hot · {nasdaq_earn} Nasdaq earnings · {overlap} overlap deduped)</span>
            &nbsp;→&nbsp;
            <span style="color:#64748b">Fetched</span> &nbsp;
            <span style="font-weight:600">{fetched}</span>{_pct(fetched, superset)}
            &nbsp;→&nbsp;
            <span style="color:#64748b">Passed filters</span> &nbsp;
            <span style="font-weight:600">{passed_filter}</span>{_pct(passed_filter, fetched)}
            &nbsp;→&nbsp;
            <span style="color:#64748b">Passed scorer</span> &nbsp;
            <span style="font-weight:600">{scored}</span>{_pct(scored, passed_filter)}
            &nbsp;→&nbsp;
            <span style="color:#64748b">Sent to Claude</span> &nbsp;
            <span style="font-weight:600">{sent_claude}</span>{_pct(sent_claude, scored)}
            &nbsp;→&nbsp;
            <span style="color:#15803d;font-weight:700">Predictions: {predictions_cnt}</span>{_pct(predictions_cnt, sent_claude)}
            </div>""",
            unsafe_allow_html=True,
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
/* Style D: color-coded left border on toggle button via sentinel div + :has() */
[data-testid="stColumn"]:has(div.card-bullish) button {
    background: #f0fdf4 !important;
    border: 1px solid #bbf7d0 !important;
    border-left: 4px solid #16a34a !important;
    border-radius: 8px 0 0 8px !important;
    text-align: left !important;
    justify-content: flex-start !important;
    font-size: 13.5px !important;
    font-weight: 500 !important;
    color: #1e293b !important;
    padding: 10px 14px !important;
    width: 100% !important;
}
[data-testid="stColumn"]:has(div.card-bearish) button {
    background: #fef2f2 !important;
    border: 1px solid #fecaca !important;
    border-left: 4px solid #dc2626 !important;
    border-radius: 8px 0 0 8px !important;
    text-align: left !important;
    justify-content: flex-start !important;
    font-size: 13.5px !important;
    font-weight: 500 !important;
    color: #1e293b !important;
    padding: 10px 14px !important;
    width: 100% !important;
}
[data-testid="stColumn"]:has(div.card-neutral) button {
    background: #f8fafc !important;
    border: 1px solid #e2e8f0 !important;
    border-left: 4px solid #94a3b8 !important;
    border-radius: 8px 0 0 8px !important;
    text-align: left !important;
    justify-content: flex-start !important;
    font-size: 13.5px !important;
    font-weight: 500 !important;
    color: #1e293b !important;
    padding: 10px 14px !important;
    width: 100% !important;
}
[data-testid="stColumn"]:has(div.card-bullish) button[data-testid="stBaseButton-secondary"] > div,
[data-testid="stColumn"]:has(div.card-bearish) button[data-testid="stBaseButton-secondary"] > div,
[data-testid="stColumn"]:has(div.card-neutral) button[data-testid="stBaseButton-secondary"] > div {
    justify-content: flex-start !important;
    width: 100% !important;
}
[data-testid="stColumn"]:has(div.card-bullish) button[data-testid="stBaseButton-secondary"] span,
[data-testid="stColumn"]:has(div.card-bearish) button[data-testid="stBaseButton-secondary"] span,
[data-testid="stColumn"]:has(div.card-neutral) button[data-testid="stBaseButton-secondary"] span {
    width: 100% !important;
    text-align: left !important;
}
/* Delete button */
[data-testid="stColumn"]:has(div.card-del) button {
    background: #f8fafc !important;
    border: 1px solid #e2e8f0 !important;
    border-left: none !important;
    border-radius: 0 8px 8px 0 !important;
    color: #94a3b8 !important;
    font-size: 13px !important;
    padding: 10px 10px !important;
    box-shadow: none !important;
    min-height: unset !important;
}
[data-testid="stColumn"]:has(div.card-del) button:hover {
    color: #dc2626 !important;
    background: #fff1f2 !important;
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

    css_class = "card-bullish" if direction == "BULLISH" else "card-bearish" if direction == "BEARISH" else "card-neutral"

    buy_str = f"\\${entry:.2f}" if entry > 0 else "—"
    tgt_str = f"\\${tgt_mid:.2f}" if tgt_mid > 0 else "—"

    arrow  = "▼" if is_open else "▶"
    header = (
        f"{arrow}  {dir_circle} **{ticker}** — {company}  ·  {dir_icon} {direction}  ·  "
        f"{confidence}% conf  ·  buy {buy_str}  ·  tgt {tgt_str}  ·  {profit_str} potential  ·  {tenure_str}"
        f"{pos_tag}{exp_tag}  ·  {age_days}d old{hc_tag}"
    )

    # ── Header row: toggle button + delete (2 widget calls total when collapsed) ─
    hdr_col, del_col = st.columns([11.2, 0.4])
    with hdr_col:
        st.markdown(f'<div class="{css_class}"></div>', unsafe_allow_html=True)
        if st.button(header, key=f"toggle_{pred_id}", use_container_width=True):
            st.session_state[exp_key] = not is_open
            st.rerun()
    with del_col:
        st.markdown('<div class="card-del"></div>', unsafe_allow_html=True)
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
            st.markdown(f"Buy range: \${bl:.2f} – \${bh:.2f}")
            st.write(f"Stop loss: ${stop:.2f}")
            mcap   = p.get("market_cap")
            avgvol = p.get("avg_volume")
            if mcap:
                mcap_str = "${:.1f}B".format(mcap/1e9) if mcap >= 1e9 else "${:.0f}M".format(mcap/1e6)
                st.write(f"Market cap: {mcap_str}")
            if avgvol:
                st.write(f"Avg volume: {'{:.1f}M'.format(avgvol/1e6) if avgvol >= 1e6 else '{:.0f}K'.format(avgvol/1e3)}")
        with c2:
            st.markdown("**Target**")
            st.markdown(f"Range: \${tl:.2f} – \${th:.2f}")
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

        _option_section(p)
        _news_links(ticker)

        if position == "SHORT":
            st.warning("SHORT position — margin/options account required")

        st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)
        is_tracked = p.get("is_tracked", False)
        ms_col, mf_col, track_col, _ = st.columns([1.5, 1.5, 1.8, 5])
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
        with track_col:
            if is_tracked:
                if st.button("🔴 Stop Tracking", key=f"untrack_{pred_id}"):
                    try:
                        from database.db import update_prediction
                        update_prediction(pred_id, {
                            "is_tracked": False,
                            "live_signal": None,
                            "live_signal_reason": None,
                            "live_signal_updated_at": None,
                            "live_current_price": None,
                            "live_peak_price": None,
                        })
                        _fetch_open_predictions.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed: {e}")
            else:
                if st.button("📡 Start Tracking", key=f"track_{pred_id}"):
                    try:
                        from database.db import update_prediction
                        update_prediction(pred_id, {
                            "is_tracked": True,
                            "tracked_since": datetime.now(PT).isoformat(),
                        })
                        _fetch_open_predictions.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed: {e}")


def _option_section(p: dict):
    """Renders the options contract recommendation section inside an expanded card."""
    direction = p.get("direction", "NEUTRAL")
    if direction not in ("BULLISH", "BEARISH"):
        return

    ticker         = p.get("ticker", "")
    timeframe      = p.get("timeframe", "short")
    days_to_target = p.get("days_to_target") or (60 if timeframe == "long" else 5)
    entry          = _calc_entry(p)
    tgt_low        = p.get("target_low") or 0
    tgt_high       = p.get("target_high") or 0
    tgt_mid        = (tgt_low + tgt_high) / 2 if tgt_low > 0 and tgt_high > 0 else tgt_low

    if entry <= 0 or tgt_mid <= 0:
        return

    # Skip if predicted move too small for options to make sense (<3%)
    profit_pct = abs(tgt_mid - entry) / entry * 100 if entry > 0 else 0
    if profit_pct < 3.0:
        return

    has_earnings = bool((p.get("earnings_calendar") or {}).get("has_upcoming"))
    is_short_term = timeframe in ("short", "medium")

    opt_key = f"opt_{p.get('id', ticker)}_{direction}_{timeframe}"
    fetched = st.session_state.get(opt_key)

    # Auto-load from Supabase cache if scanner pre-fetched it (no button click needed)
    if fetched is None:
        try:
            from database.db import get_cache
            cache_key = f"opt_rec_{ticker}_{direction}_{timeframe}_{days_to_target}"
            cached = get_cache(cache_key)
            if cached is not None:
                st.session_state[opt_key] = cached
                fetched = cached
        except Exception:
            pass

    is_call    = direction == "BULLISH"
    opt_color  = "#15803d" if is_call else "#b91c1c"
    opt_bg     = "#f0fdf4" if is_call else "#fef2f2"
    opt_border = "#bbf7d0" if is_call else "#fecaca"
    opt_emoji  = "📈" if is_call else "📉"

    # Timeframe label shown in header
    tf_note = {
        "short":  "35 DTE — buy time, exit when stock hits target",
        "medium": "35 DTE — hold 1-3 weeks, sell before expiry",
        "long":   f"{days_to_target + 30}d DTE target — long conviction hold",
    }.get(timeframe, "")

    st.markdown(
        f"""<div style="margin-top:14px;padding:12px 14px;background:{opt_bg};
            border:1px solid {opt_border};border-radius:10px">
          <div style="font-size:13px;font-weight:700;color:{opt_color};margin-bottom:2px">
            {opt_emoji} OPTIONS CONTRACT RECOMMENDATION
          </div>
          <div style="font-size:11px;color:#64748b;margin-bottom:8px">{tf_note}</div>""",
        unsafe_allow_html=True,
    )

    if fetched is None:
        fetch_col, _ = st.columns([2, 8])
        with fetch_col:
            if st.button("Fetch Best Contract", key=f"fetch_{opt_key}", type="secondary"):
                with st.spinner("Fetching live options chain…"):
                    try:
                        from services.options_recommendation import get_option_recommendation
                        rec = get_option_recommendation(
                            ticker, direction, days_to_target, entry, tgt_mid,
                            timeframe=timeframe, has_earnings=has_earnings,
                        )
                        st.session_state[opt_key] = rec
                        st.rerun()
                    except Exception as e:
                        st.session_state[opt_key] = {"available": False, "reason": str(e)}
                        st.rerun()
        st.markdown(
            '<div style="font-size:11px;color:#94a3b8;padding:2px 0 4px">'
            'Live fetch — yfinance, no API key · cached 4h</div>',
            unsafe_allow_html=True,
        )

    elif not fetched.get("available"):
        reason   = fetched.get("reason", "No liquid contract found")
        opt_type = fetched.get("option_type", "CALL BUY OPTION" if is_call else "PUT BUY OPTION")
        st.markdown(
            f'<div style="font-size:12px;color:#64748b;padding:4px 0">'
            f'<strong>{opt_type}</strong> — unavailable: {reason}</div>',
            unsafe_allow_html=True,
        )
        refetch_col, _ = st.columns([1.5, 8.5])
        with refetch_col:
            if st.button("Retry", key=f"retry_{opt_key}", type="secondary"):
                try:
                    from database.db import delete_cache
                    delete_cache(f"opt_rec_{ticker}_{direction}_{timeframe}_{days_to_target}")
                except Exception:
                    pass
                del st.session_state[opt_key]
                st.rerun()

    else:
        rec        = fetched
        opt_type   = rec["option_type"]
        strike     = rec["strike"]
        exp_label  = rec["expiry_label"]
        entry_mid  = rec["entry_mid"]
        target_est = rec["target_est"]
        gain_pct   = rec["gain_pct_est"]
        oi         = rec["oi"]
        vol        = rec["volume"]
        spread     = rec["spread_pct"]
        iv         = rec["iv_pct"]
        grade      = rec["grade"]
        delta      = rec["delta_approx"]
        days_exp   = rec.get("days_to_expiry")

        grade_color = "#15803d" if grade == "A" else "#b45309"
        gain_color  = "#15803d" if gain_pct >= 0 else "#b91c1c"
        gain_str    = f"+{gain_pct:.0f}%" if gain_pct >= 0 else f"{gain_pct:.0f}%"
        days_exp_str = f"  ·  {days_exp}d to expiry" if days_exp else ""
        iv_str       = f"{iv:.0f}%" if iv else "N/A"

        if rec.get("after_hours"):
            st.markdown(
                '<div style="font-size:11px;color:#b45309;background:#fffbeb;border:1px solid #fde68a;'
                'border-radius:6px;padding:4px 8px;margin-bottom:6px">'
                '⚠️ After-hours: prices estimated from last trade. Refresh during market hours (9:30 AM–4 PM ET) for live bid/ask.</div>',
                unsafe_allow_html=True,
            )

        st.markdown(
            f"""<div style="font-size:14px;font-weight:700;color:{opt_color};margin-bottom:8px">
              {opt_type}: ${strike:.2f} strike — {exp_label}{days_exp_str}
            </div>""",
            unsafe_allow_html=True,
        )

        st.markdown(
            f"""<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">
              {_pill("Entry (mid)", f"${entry_mid:.2f}", opt_color)}
              {_pill("Target (est)", f"${target_est:.2f}", gain_color)}
              {_pill("Option gain est", gain_str, gain_color)}
              {_pill("OI", f"{oi:,}", "#374151")}
              {_pill("Volume", f"{vol:,}", "#374151")}
              {_pill("Spread", f"{spread:.1f}%", "#374151")}
              {_pill("IV", iv_str, "#7c3aed")}
              {_pill("Delta ≈", f"{delta:.2f}", "#0369a1")}
              <span style="background:{'#f0fdf4' if grade=='A' else '#fefce8'};
                border:1px solid {'#86efac' if grade=='A' else '#fde047'};
                border-radius:20px;padding:4px 10px;font-size:12px;
                font-weight:700;color:{grade_color}">Grade: {grade}</span>
            </div>""",
            unsafe_allow_html=True,
        )

        # Disclaimer always shown
        st.markdown(
            f'<div style="font-size:11px;color:#64748b;line-height:1.6">'
            f'⚠️ <strong>Estimated values only.</strong> '
            f'Option target uses first-order delta (delta≈{delta:.2f} × stock move). '
            f'Actual price depends on gamma, theta decay, and IV changes.'
            f'</div>',
            unsafe_allow_html=True,
        )

        # Exit discipline note — shown on ALL timeframes, wording differs
        if is_short_term:
            st.markdown(
                '<div style="font-size:12px;font-weight:600;color:#1d4ed8;'
                'background:#eff6ff;border:1px solid #bfdbfe;border-radius:7px;'
                'padding:8px 10px;margin-top:6px;line-height:1.6">'
                '💡 <strong>Exit rule:</strong> This is a 35-DTE contract held for only '
                f'{days_to_target}d. Theta at 30+ DTE is minimal (~$0.05–0.15/day). '
                '<strong>Sell the option as soon as the stock hits the target price</strong> '
                '— do not wait for expiry. The option still has 25-30 days of time value, '
                'so it will sell easily at a tight spread.'
                '</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div style="font-size:12px;font-weight:600;color:#1d4ed8;'
                'background:#eff6ff;border:1px solid #bfdbfe;border-radius:7px;'
                'padding:8px 10px;margin-top:6px;line-height:1.6">'
                '💡 <strong>Exit rule:</strong> Do not hold this option to expiry. '
                f'Once the stock moves {round(profit_pct * 0.6, 0):.0f}–{round(profit_pct * 0.8, 0):.0f}% '
                'toward the target (60-80% of the move), the option will have captured most of the gain '
                'while delta is still high. '
                '<strong>Sell into strength</strong> — the last 20-40% of a stock move '
                'yields diminishing option returns as delta plateaus near 1.0.'
                '</div>',
                unsafe_allow_html=True,
            )

        # Earnings warning: IV spike into earnings, crush on exit
        if rec.get("earnings_warning"):
            st.markdown(
                '<div style="font-size:11px;color:#b45309;margin-top:5px;line-height:1.5">'
                '⚡ <strong>Earnings within window.</strong> IV spikes into the report and '
                'collapses after — you may be right on direction but still lose on IV crush. '
                'Consider selling the option before the earnings date.'
                '</div>',
                unsafe_allow_html=True,
            )

        refetch_col, _ = st.columns([1.5, 8.5])
        with refetch_col:
            if st.button("↻ Refresh", key=f"refetch_{opt_key}", type="secondary"):
                try:
                    from database.db import delete_cache
                    delete_cache(f"opt_rec_{ticker}_{direction}_{timeframe}_{days_to_target}")
                except Exception:
                    pass
                del st.session_state[opt_key]
                st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)


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
    if p.get("prediction_label"):
        label = p["prediction_label"]
        if "RALLY" in label:
            badges += f'<span style="background:#14532d;color:#86efac;border-radius:20px;padding:2px 8px;font-size:11px;font-weight:700;margin-left:4px">{label}</span>'
        else:
            badges += f'<span style="background:#450a0a;color:#fca5a5;border-radius:20px;padding:2px 8px;font-size:11px;font-weight:700;margin-left:4px">{label}</span>'
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


