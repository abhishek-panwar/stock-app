import streamlit as st
from datetime import datetime, timedelta
import pytz

PT = pytz.timezone("America/Los_Angeles")


def _calc_entry(p: dict) -> float:
    bl = p.get("buy_range_low") or 0
    bh = p.get("buy_range_high") or 0
    return (bl + bh) / 2 if bl > 0 and bh > 0 else (p.get("price_at_prediction") or 0)


def _calc_profit_pct(p: dict) -> float:
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


@st.cache_data(ttl=3600)
def _get_company_name(ticker: str) -> str:
    try:
        from services.yfinance_service import get_ticker_info
        return get_ticker_info(ticker).get("name", ticker)
    except Exception:
        return ticker


def _days_held(p: dict) -> int:
    try:
        v = datetime.fromisoformat(p.get("verified_on", "").replace("Z", "+00:00")).astimezone(PT).date()
        q = datetime.fromisoformat(p.get("predicted_on", "").replace("Z", "+00:00")).astimezone(PT).date()
        return (v - q).days
    except Exception:
        return 999


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


def _recalculate_metrics():
    status = st.status("Recalculating metrics with new logic…", expanded=True)
    try:
        from database.db import get_predictions, update_prediction
        all_preds = get_predictions(limit=1000)
        closed = [p for p in all_preds if p.get("outcome") in ("WIN", "LOSS")]

        status.write(f"Found {len(closed)} closed predictions to recalculate…")

        updated = 0
        skipped = 0

        for p in closed:
            buy_low  = p.get("buy_range_low") or 0
            buy_high = p.get("buy_range_high") or 0
            entry     = (buy_low + buy_high) / 2 if buy_low > 0 and buy_high > 0 else (p.get("price_at_prediction") or 0)
            direction  = p.get("direction", "NEUTRAL")
            target_low = p.get("target_low") or 0
            target_high= p.get("target_high") or 0
            stop_loss  = p.get("stop_loss") or 0
            closed_reason = p.get("closed_reason", "")
            recorded_close = p.get("price_at_close") or 0

            if entry <= 0 or not closed_reason:
                skipped += 1
                continue

            if closed_reason == "TARGET_HIT":
                if direction == "BULLISH" and target_low > 0:
                    # use target_high if close overshot it, else target_low
                    price_at_close = target_high if (target_high > 0 and recorded_close >= target_high) else target_low
                    return_pct = round((price_at_close - entry) / entry * 100, 2)
                elif direction == "BEARISH" and target_high > 0:
                    # use target_low if close overshot it, else target_high
                    price_at_close = target_low if (target_low > 0 and recorded_close <= target_low) else target_high
                    return_pct = round((entry - price_at_close) / entry * 100, 2)
                else:
                    skipped += 1
                    continue

            elif closed_reason == "STOP_LOSS":
                if stop_loss <= 0:
                    skipped += 1
                    continue
                price_at_close = stop_loss
                if direction == "BULLISH":
                    return_pct = round((stop_loss - entry) / entry * 100, 2)
                elif direction == "BEARISH":
                    return_pct = round((entry - stop_loss) / entry * 100, 2)
                else:
                    skipped += 1
                    continue

            else:
                # EXPIRED — keep recorded close price, recompute return with correct direction sign
                if recorded_close <= 0:
                    skipped += 1
                    continue
                price_at_close = recorded_close
                if direction == "BEARISH":
                    return_pct = round((entry - recorded_close) / entry * 100, 2)
                else:
                    return_pct = round((recorded_close - entry) / entry * 100, 2)

            try:
                update_prediction(p["id"], {
                    "price_at_close": price_at_close,
                    "return_pct":     return_pct,
                })
                updated += 1
                status.write(f"  {p['ticker']} ({direction}, {closed_reason}): {return_pct:+.2f}%")
            except Exception as e:
                status.write(f"  {p['ticker']}: error — {e}")
                skipped += 1

        status.update(
            label=f"Done — {updated} updated, {skipped} skipped",
            state="complete",
            expanded=False,
        )
        st.rerun()

    except Exception as e:
        status.update(label=f"Error: {e}", state="error", expanded=True)


def render():
    st.title("📜 Closed Predictions")

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

    # ── Bulk selection state ──────────────────────────────────────────────────
    if "closed_selected" not in st.session_state:
        st.session_state.closed_selected = set()

    selected = st.session_state.closed_selected
    if selected:
        sel_col1, sel_col2, sel_col3 = st.columns([3, 2, 5])
        with sel_col1:
            st.warning(f"**{len(selected)} prediction{'s' if len(selected) > 1 else ''} selected**")
        with sel_col2:
            if st.button(f"🗑 Delete {len(selected)} selected", key="bulk_delete_closed"):
                try:
                    from database.db import soft_delete_prediction
                    for pid in selected:
                        soft_delete_prediction(pid)
                    st.session_state.closed_selected = set()
                    st.rerun()
                except Exception as e:
                    st.error(f"Bulk delete failed: {e}")
        with sel_col3:
            if st.button("✕ Clear selection", key="clear_selection_closed"):
                st.session_state.closed_selected = set()
                st.rerun()

    # ── Recalculate button ────────────────────────────────────────────────────
    st.markdown('<div class="btn-safe">', unsafe_allow_html=True)
    recalc_clicked = st.button(
        "🔄 Recalculate All Metrics",
        help="Recomputes return_pct and price_at_close using target/stop levels instead of market price",
    )
    if recalc_clicked:
        _recalculate_metrics()

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

    # ── Bullish vs Bearish breakdown ─────────────────────────────────────────
    bull_all  = [p for p in all_closed if p.get("direction") == "BULLISH"]
    bear_all  = [p for p in all_closed if p.get("direction") == "BEARISH"]
    bull_wins = [p for p in bull_all if p.get("outcome") == "WIN"]
    bear_wins = [p for p in bear_all if p.get("outcome") == "WIN"]
    bull_rate = len(bull_wins) / len(bull_all) * 100 if bull_all else 0
    bear_rate = len(bear_wins) / len(bear_all) * 100 if bear_all else 0
    bull_net  = sum(p.get("return_pct") or 0 for p in bull_all)
    bear_net  = sum(p.get("return_pct") or 0 for p in bear_all)
    bull_avg_win  = sum(p.get("return_pct") or 0 for p in bull_wins)  / len(bull_wins)  if bull_wins  else 0
    bear_avg_win  = sum(p.get("return_pct") or 0 for p in bear_wins)  / len(bear_wins)  if bear_wins  else 0
    bull_losses   = [p for p in bull_all if p.get("outcome") == "LOSS"]
    bear_losses   = [p for p in bear_all if p.get("outcome") == "LOSS"]
    bull_avg_loss = sum(p.get("return_pct") or 0 for p in bull_losses) / len(bull_losses) if bull_losses else 0
    bear_avg_loss = sum(p.get("return_pct") or 0 for p in bear_losses) / len(bear_losses) if bear_losses else 0

    bull_rate_color = "#15803d" if bull_rate >= 60 else "#b45309" if bull_rate >= 40 else "#b91c1c"
    bear_rate_color = "#15803d" if bear_rate >= 60 else "#b45309" if bear_rate >= 40 else "#b91c1c"
    bull_net_color  = "#15803d" if bull_net >= 0 else "#b91c1c"
    bear_net_color  = "#15803d" if bear_net >= 0 else "#b91c1c"

    st.markdown(
        f"""<div style="display:flex;gap:12px;margin-bottom:20px;flex-wrap:wrap">
          <div style="flex:1;min-width:260px;background:#f0fdf4;border:1px solid #bbf7d0;
              border-radius:12px;padding:16px 20px">
            <div style="font-size:12px;font-weight:700;color:#15803d;text-transform:uppercase;
                letter-spacing:0.8px;margin-bottom:10px">▲ Bullish — {len(bull_all)} trades</div>
            <div style="display:flex;gap:20px;flex-wrap:wrap;align-items:flex-end">
              <div>
                <div style="font-size:11px;color:#64748b;margin-bottom:2px">Win Rate</div>
                <div style="font-size:28px;font-weight:800;color:{bull_rate_color}">{bull_rate:.1f}%</div>
                <div style="font-size:12px;color:#64748b">{len(bull_wins)}W / {len(bull_losses)}L</div>
              </div>
              <div>
                <div style="font-size:11px;color:#64748b;margin-bottom:2px">Net Return</div>
                <div style="font-size:22px;font-weight:700;color:{bull_net_color}">{bull_net:+.1f}%</div>
              </div>
              <div>
                <div style="font-size:11px;color:#64748b;margin-bottom:2px">Avg Win</div>
                <div style="font-size:18px;font-weight:600;color:#15803d">+{bull_avg_win:.1f}%</div>
              </div>
              <div>
                <div style="font-size:11px;color:#64748b;margin-bottom:2px">Avg Loss</div>
                <div style="font-size:18px;font-weight:600;color:#b91c1c">{bull_avg_loss:.1f}%</div>
              </div>
            </div>
          </div>
          <div style="flex:1;min-width:260px;background:#fef2f2;border:1px solid #fecaca;
              border-radius:12px;padding:16px 20px">
            <div style="font-size:12px;font-weight:700;color:#b91c1c;text-transform:uppercase;
                letter-spacing:0.8px;margin-bottom:10px">▼ Bearish — {len(bear_all)} trades</div>
            <div style="display:flex;gap:20px;flex-wrap:wrap;align-items:flex-end">
              <div>
                <div style="font-size:11px;color:#64748b;margin-bottom:2px">Win Rate</div>
                <div style="font-size:28px;font-weight:800;color:{bear_rate_color}">{bear_rate:.1f}%</div>
                <div style="font-size:12px;color:#64748b">{len(bear_wins)}W / {len(bear_losses)}L</div>
              </div>
              <div>
                <div style="font-size:11px;color:#64748b;margin-bottom:2px">Net Return</div>
                <div style="font-size:22px;font-weight:700;color:{bear_net_color}">{bear_net:+.1f}%</div>
              </div>
              <div>
                <div style="font-size:11px;color:#64748b;margin-bottom:2px">Avg Win</div>
                <div style="font-size:18px;font-weight:600;color:#15803d">+{bear_avg_win:.1f}%</div>
              </div>
              <div>
                <div style="font-size:11px;color:#64748b;margin-bottom:2px">Avg Loss</div>
                <div style="font-size:18px;font-weight:600;color:#b91c1c">{bear_avg_loss:.1f}%</div>
              </div>
            </div>
          </div>
        </div>""",
        unsafe_allow_html=True,
    )

    # ── Daily success rate — last 30 days ────────────────────────────────────
    _render_daily_chart(all_closed)

    # ── Filters + Sort ────────────────────────────────────────────────────────
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        tf_filter = st.selectbox("Timeframe", ["All", "short", "medium", "long"])
    with col2:
        outcome_filter = st.selectbox("Outcome", ["All", "WIN", "LOSS"])
    with col3:
        tickers = sorted({p["ticker"] for p in all_closed})
        ticker_filter = st.selectbox("Ticker", ["All"] + tickers)
    with col4:
        conf_min = st.slider("Min Confidence", 0, 100, 0)
    with col5:
        SORT_OPTIONS = {
            "Profit % (default)": lambda p: -abs(_calc_profit_pct(p)),
            "Confidence":         lambda p: -(p.get("confidence") or 0),
            "Score":              lambda p: -(p.get("score") or 0),
            "Actual return":      lambda p: -(p.get("return_pct") or 0),
            "Risk/Reward": lambda p: -(
                abs(((p.get("target_low") or 0) + (p.get("target_high") or 0)) / 2 - _calc_entry(p)) /
                abs(_calc_entry(p) - (p.get("stop_loss") or _calc_entry(p)) or 1)
                if _calc_entry(p) > 0 and (p.get("stop_loss") or 0) > 0 else 0
            ),
            "Days held":          lambda p: _days_held(p),
            "Newest first":       lambda p: -(
                datetime.fromisoformat(p.get("predicted_on", "1970").replace("Z", "+00:00")).timestamp()
                if p.get("predicted_on") else 0
            ),
        }
        sort_by = st.selectbox("Sort by", list(SORT_OPTIONS.keys()), key="closed_sort_by")
    sort_fn = SORT_OPTIONS[sort_by]

    filtered = all_closed
    if tf_filter != "All":
        filtered = [p for p in filtered if p.get("timeframe") == tf_filter]
    if outcome_filter != "All":
        filtered = [p for p in filtered if p.get("outcome") == outcome_filter]
    if ticker_filter != "All":
        filtered = [p for p in filtered if p.get("ticker") == ticker_filter]
    filtered = [p for p in filtered if (p.get("confidence") or 0) >= conf_min]
    filtered = sorted(filtered, key=sort_fn)

    st.markdown("---")

    # ── Prediction list grouped by timeframe → recency ───────────────────────
    st.markdown(f"### Closed Predictions ({len(filtered)} shown)")

    today_pt = datetime.now(PT).date()

    def _closed_date(p):
        raw = p.get("verified_on") or p.get("predicted_on") or ""
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(PT).date()
        except Exception:
            return today_pt

    DATE_ORDER = ["Today", "Yesterday", "This Week", "This Month", "This Year", "Older"]

    def _date_bucket(p):
        d = _closed_date(p)
        delta = (today_pt - d).days
        if delta == 0:   return "Today"
        if delta == 1:   return "Yesterday"
        if delta <= 7:   return "This Week"
        if delta <= 30:  return "This Month"
        if delta <= 365: return "This Year"
        return "Older"

    TF_CONFIG = [
        ("short",  "⚡ Short-term",  "#0369a1"),
        ("medium", "📈 Medium-term", "#7c3aed"),
        ("long",   "🌱 Long-term",   "#15803d"),
    ]

    for tf_key, tf_label, tf_color in TF_CONFIG:
        tf_preds = [p for p in filtered if p.get("timeframe") == tf_key]
        if not tf_preds:
            continue

        tf_wins   = sum(1 for p in tf_preds if p.get("outcome") == "WIN")
        tf_losses = len(tf_preds) - tf_wins
        tf_net    = sum(p.get("return_pct") or 0 for p in tf_preds)
        tf_rate   = tf_wins / len(tf_preds) * 100 if tf_preds else 0
        net_color = "green" if tf_net >= 0 else "red"
        net_sign  = "+" if tf_net >= 0 else ""

        st.markdown(
            f'<div style="font-size:15px;font-weight:700;color:{tf_color};'
            f'margin:18px 0 6px;padding-left:2px">'
            f'{tf_label} — {len(tf_preds)} trade{"s" if len(tf_preds) != 1 else ""}  ·  '
            f'{tf_wins}W / {tf_losses}L  ·  {tf_rate:.0f}% win rate  ·  '
            f'Net {net_sign}{tf_net:.1f}%</div>',
            unsafe_allow_html=True,
        )

        date_groups: dict = {k: [] for k in DATE_ORDER}
        for p in tf_preds:
            date_groups[_date_bucket(p)].append(p)

        for bucket_label in DATE_ORDER:
            bucket_preds = date_groups[bucket_label]
            if not bucket_preds:
                continue
            wins_g   = sum(1 for p in bucket_preds if p.get("outcome") == "WIN")
            losses_g = len(bucket_preds) - wins_g
            net_g    = sum(p.get("return_pct") or 0 for p in bucket_preds)
            net_str  = f"{net_g:+.1f}%"
            net_colored = f"Net :green[**{net_str}**]" if net_g >= 0 else f"Net :red[**{net_str}**]"
            with st.expander(
                f"**{bucket_label}** — {len(bucket_preds)} trade{'s' if len(bucket_preds) != 1 else ''}  ·  "
                f":green[{wins_g} WINS]  ·  :red[{losses_g} LOSSES]  ·  {net_colored}",
                expanded=(bucket_label in ("Today", "Yesterday")),
            ):
                for p in bucket_preds:
                    _prediction_card(p)


def _prediction_card(p: dict):
    outcome    = p.get("outcome", "LOSS")
    ticker     = p.get("ticker", "—")
    direction  = p.get("direction", "NEUTRAL")
    timeframe  = p.get("timeframe", "short")
    confidence = p.get("confidence", 0)
    score      = p.get("score", 0)
    position   = p.get("position", "HOLD")
    ret        = p.get("return_pct")
    ret_str    = f"{ret:+.2f}%" if ret is not None else "—"

    entry      = _calc_entry(p)
    tgt_low    = p.get("target_low") or 0
    tgt_high   = p.get("target_high") or 0
    tgt_mid    = (tgt_low + tgt_high) / 2 if tgt_low > 0 and tgt_high > 0 else tgt_low
    stop       = p.get("stop_loss") or 0
    profit_pct = _calc_profit_pct(p)
    profit_str = f"+{profit_pct:.1f}%" if profit_pct > 0 else f"{profit_pct:.1f}%"
    rr = abs(tgt_mid - entry) / abs(entry - stop) if entry > 0 and stop > 0 and abs(entry - stop) > 0 else 0

    days_to_target = p.get("days_to_target")

    predicted_on = p.get("predicted_on", "")
    verified_on  = p.get("verified_on", "")
    actual_days = None
    if predicted_on and verified_on:
        try:
            v_dt = datetime.fromisoformat(verified_on.replace("Z", "+00:00")).astimezone(PT).date()
            p_dt = datetime.fromisoformat(predicted_on.replace("Z", "+00:00")).astimezone(PT).date()
            actual_days = (v_dt - p_dt).days
        except Exception:
            pass
    if actual_days is None:
        tenure_str = f"~{days_to_target}d est." if days_to_target else "—"
    elif actual_days == 0:
        tenure_str = "held 0d"
    else:
        tenure_str = f"held {actual_days}d"

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
        f"{confidence}% conf  ·  {profit_str} potential  ·  {tenure_str}"
        f"{pos_tag}  ·  {ret_str}{reason_tag}"
    )

    pred_id = p.get("id") or f"{ticker}_{timeframe}_{p.get('predicted_on','')[:10]}"

    # ── Checkbox for bulk selection ───────────────────────────────────────────
    if "closed_selected" in st.session_state:
        chk_col, card_col = st.columns([0.4, 11])
        with chk_col:
            checked = pred_id in st.session_state.closed_selected
            if st.checkbox("", value=checked, key=f"hchk_{pred_id}", label_visibility="collapsed"):
                st.session_state.closed_selected.add(pred_id)
            else:
                st.session_state.closed_selected.discard(pred_id)

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

        bl = p.get('buy_range_low', 0); bh = p.get('buy_range_high', 0)
        tl = p.get('target_low', 0);   th = p.get('target_high', 0)
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("**Entry**")
            st.write(f"Price at signal: ${entry:.2f}")
            st.write(f"Buy range: ${bl:.2f} – ${bh:.2f}")
            st.write(f"Confidence: {confidence}%  ·  Score: {score}/100")
            mcap = p.get("market_cap")
            avgvol = p.get("avg_volume")
            if mcap:
                mcap_str = f"${mcap/1e9:.1f}B" if mcap >= 1e9 else f"${mcap/1e6:.0f}M"
                st.write(f"Market cap: {mcap_str}")
            if avgvol:
                vol_str = f"{avgvol/1e6:.1f}M" if avgvol >= 1e6 else f"{avgvol/1e3:.0f}K"
                st.write(f"Avg volume: {vol_str}")
        with c2:
            st.markdown("**Exit**")
            close_price = p.get("price_at_close")
            st.write(f"Close price: ${close_price:.2f}" if close_price else "Not closed yet")
            st.write(f"Target: ${tl:.2f} – ${th:.2f}")
            st.write(f"Stop loss: ${stop:.2f}")
            if closed_reason:
                st.write(f"Closed by: {closed_reason}")
        with c3:
            st.markdown("**Timing**")
            st.write(f"Timeframe: {timeframe}  ·  Position: {position}")
            st.write(f"Est. days to target: {days_to_target or '—'}")
            if actual_days is not None:
                st.write(f"Actual days held: {actual_days}d")
            if p.get("timing_rationale"):
                st.caption(f"💡 {p['timing_rationale']}")

        if bl > 0 and bh > 0 and tl > 0:
            if direction == "BEARISH":
                pot_formula = f"( ({bl:.2f}+{bh:.2f})/2 - ({tl:.2f}+{th:.2f})/2 ) / ({bl:.2f}+{bh:.2f})/2 = {profit_pct:+.1f}%"
            else:
                pot_formula = f"( ({tl:.2f}+{th:.2f})/2 - ({bl:.2f}+{bh:.2f})/2 ) / ({bl:.2f}+{bh:.2f})/2 = {profit_pct:+.1f}%"
            st.markdown(f"**Profit formula:** `{pot_formula}`")
        if close_price and entry > 0 and ret is not None:
            if direction == "BEARISH":
                act_formula = f"( {entry:.2f} - {close_price:.2f} ) / {entry:.2f} = {ret:+.2f}%"
            else:
                act_formula = f"( {close_price:.2f} - {entry:.2f} ) / {entry:.2f} = {ret:+.2f}%"
            st.markdown(f"**Actual return:** `{act_formula}`")

        if p.get("reasoning"):
            st.markdown(
                f"""<div style="background:#f8fafc;border-left:3px solid #94a3b8;
                border-radius:0 6px 6px 0;padding:8px 12px;margin-top:8px;
                font-size:13px;color:#374151">{p['reasoning']}</div>""",
                unsafe_allow_html=True,
            )

        if position == "SHORT":
            st.warning("SHORT position — margin/options account required")


def _render_daily_chart(all_closed: list):
    import plotly.graph_objects as go
    from collections import defaultdict

    today_pt = datetime.now(PT).date()

    day_wins   = defaultdict(int)
    day_losses = defaultdict(int)
    day_bull_wins   = defaultdict(int)
    day_bull_losses = defaultdict(int)
    day_bear_wins   = defaultdict(int)
    day_bear_losses = defaultdict(int)

    for p in all_closed:
        raw = p.get("verified_on") or p.get("predicted_on") or ""
        if not raw:
            continue
        try:
            d = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(PT).date()
        except Exception:
            continue
        if (today_pt - d).days > 30:
            continue
        direction = p.get("direction", "")
        win = p.get("outcome") == "WIN"
        if win:
            day_wins[d] += 1
        else:
            day_losses[d] += 1
        if direction == "BULLISH":
            if win: day_bull_wins[d] += 1
            else:   day_bull_losses[d] += 1
        elif direction == "BEARISH":
            if win: day_bear_wins[d] += 1
            else:   day_bear_losses[d] += 1

    all_days = sorted(set(list(day_wins.keys()) + list(day_losses.keys())))
    if len(all_days) < 2:
        return

    rates = []; bull_rates = []; bear_rates = []
    labels = []; colors = []; totals = []
    bull_totals = []; bear_totals = []

    for d in all_days:
        w = day_wins[d]; l = day_losses[d]; t = w + l
        rate = w / t * 100 if t > 0 else 0
        rates.append(rate); totals.append(t)
        labels.append(d.strftime("%b %d"))
        colors.append("#16a34a" if rate >= 60 else "#f59e0b" if rate >= 40 else "#dc2626")

        bw = day_bull_wins[d]; bl = day_bull_losses[d]; bt = bw + bl
        bull_rates.append(bw / bt * 100 if bt > 0 else None)
        bull_totals.append(bt)

        rw = day_bear_wins[d]; rl = day_bear_losses[d]; rt = rw + rl
        bear_rates.append(rw / rt * 100 if rt > 0 else None)
        bear_totals.append(rt)

    fig = go.Figure()

    fig.add_trace(go.Bar(
        x=labels, y=rates,
        marker_color=colors,
        name="Overall",
        text=[f"{r:.0f}%({t})" for r, t in zip(rates, totals)],
        textposition="outside",
        textfont=dict(size=10, color="#1e293b"),
        hovertemplate="%{x} Overall: %{y:.1f}% — %{customdata} trades<extra></extra>",
        customdata=totals,
        opacity=0.5,
    ))

    fig.add_trace(go.Scatter(
        x=labels, y=bull_rates,
        mode="lines+markers",
        line=dict(color="#16a34a", width=2.5),
        marker=dict(size=7, symbol="circle"),
        name="▲ Bullish",
        connectgaps=True,
        hovertemplate="%{x} Bullish: %{y:.1f}% (%{customdata} trades)<extra></extra>",
        customdata=bull_totals,
    ))

    fig.add_trace(go.Scatter(
        x=labels, y=bear_rates,
        mode="lines+markers",
        line=dict(color="#dc2626", width=2.5),
        marker=dict(size=7, symbol="circle"),
        name="▼ Bearish",
        connectgaps=True,
        hovertemplate="%{x} Bearish: %{y:.1f}% (%{customdata} trades)<extra></extra>",
        customdata=bear_totals,
    ))

    fig.add_hline(y=60, line_color="rgba(22,163,74,0.25)", line_dash="dot", line_width=1)
    fig.add_hline(y=40, line_color="rgba(220,38,38,0.25)", line_dash="dot", line_width=1)

    fig.update_layout(
        height=260,
        margin=dict(l=0, r=0, t=28, b=0),
        paper_bgcolor="white", plot_bgcolor="white",
        legend=dict(orientation="h", y=1.14, x=0, font=dict(size=11)),
        yaxis=dict(range=[0, 120], showgrid=True, gridcolor="rgba(0,0,0,0.05)",
                   ticksuffix="%", tickfont=dict(size=11)),
        xaxis=dict(showgrid=False, tickfont=dict(size=11)),
        bargap=0.35,
        font=dict(size=11),
    )

    st.markdown("**Daily Success Rate — Last 30 Days**")
    st.caption("Green ≥ 60% · Yellow 40–60% · Red < 40%  ·  Dotted line = 7-day rolling average")
    st.plotly_chart(fig, use_container_width=True, key="daily_success_chart")
    st.markdown("---")


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
        f'background:#f1f5f9;border:1px solid #e2e8f0;border-radius:20px;'
        f'padding:4px 10px;font-size:12px">'
        f'<span style="color:#64748b;font-weight:400">{label}:</span>'
        f'<strong style="color:{color}">{value}</strong></span>'
    )
