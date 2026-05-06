import streamlit as st
from datetime import datetime
import pytz

st.set_page_config(
    page_title="Live Tracking",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)

from views._shared import inject_css
inject_css()

PT = pytz.timezone("America/Los_Angeles")

# Auto-refresh every 60 seconds
try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=60_000, key="live_refresh")
except ImportError:
    pass


@st.cache_data(ttl=55)
def _fetch_tracked() -> list:
    from database.db import get_tracked_predictions
    return get_tracked_predictions()


def _calc_entry(p: dict) -> float:
    bl = p.get("buy_range_low") or 0
    bh = p.get("buy_range_high") or 0
    return (bl + bh) / 2 if bl > 0 and bh > 0 else (p.get("price_at_prediction") or 0)


def _return_so_far(p: dict) -> float | None:
    entry   = _calc_entry(p)
    current = p.get("live_current_price")
    if not current or entry <= 0:
        return None
    direction = p.get("direction", "NEUTRAL")
    if direction == "BEARISH":
        return (entry - current) / entry * 100
    return (current - entry) / entry * 100


def _signal_badge(signal: str) -> str:
    if signal == "SELL":
        return '<span style="background:#fef2f2;color:#b91c1c;border:1px solid #fecaca;border-radius:20px;padding:3px 12px;font-size:13px;font-weight:700">🔴 SELL</span>'
    return '<span style="background:#f0fdf4;color:#15803d;border:1px solid #bbf7d0;border-radius:20px;padding:3px 12px;font-size:13px;font-weight:700">🟢 HOLD</span>'


def _last_updated(ts: str | None) -> str:
    if not ts:
        return "Not yet updated"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(PT)
        delta = datetime.now(PT) - dt
        mins = int(delta.total_seconds() // 60)
        if mins == 0:
            return "just now"
        if mins == 1:
            return "1 min ago"
        return f"{mins} min ago"
    except Exception:
        return "—"


def _close_tracked(pred_id: str, outcome: str, p: dict):
    from database.db import update_prediction
    entry   = _calc_entry(p)
    current = p.get("live_current_price") or 0
    direction = p.get("direction", "NEUTRAL")
    target_low  = p.get("target_low") or 0
    target_high = p.get("target_high") or 0
    stop_loss   = p.get("stop_loss") or 0

    if outcome == "WIN":
        # Use avg target as exit price for return calc
        exit_price = (target_low + target_high) / 2 if target_low > 0 and target_high > 0 else target_low
        if direction == "BEARISH":
            exit_price = (target_low + target_high) / 2 if target_low > 0 and target_high > 0 else target_high
            return_pct = round((entry - exit_price) / entry * 100, 2) if entry > 0 else 0
        else:
            return_pct = round((exit_price - entry) / entry * 100, 2) if entry > 0 else 0
        closed_reason = "MANUAL"
    else:
        # Use stop loss for return calc
        if direction == "BEARISH":
            return_pct = round((entry - stop_loss) / entry * 100, 2) if entry > 0 else 0
        else:
            return_pct = round((stop_loss - entry) / entry * 100, 2) if entry > 0 else 0
        closed_reason = "MANUAL"

    update_prediction(pred_id, {
        "outcome": outcome,
        "closed_reason": closed_reason,
        "price_at_close": current if current > 0 else None,
        "return_pct": return_pct,
        "verified_on": datetime.now(PT).isoformat(),
        "is_tracked": False,
    })
    _fetch_tracked.clear()


def render():
    st.title("📡 Live Tracking")
    now_pt = datetime.now(PT)
    st.caption(f"Signals updated every 5 min by price watcher · Page refreshes every 60s · {now_pt.strftime('%I:%M %p PT')}")

    try:
        tracked = _fetch_tracked()
    except Exception as e:
        st.error(f"Database error: {e}")
        return

    if not tracked:
        st.info("No predictions being tracked. Open any prediction on the **Open Predictions** page and click **📡 Start Tracking**.")
        return

    st.markdown(f"**{len(tracked)} position{'s' if len(tracked) != 1 else ''} tracked**")
    st.markdown("---")

    for p in tracked:
        pred_id   = p.get("id")
        ticker    = p.get("ticker", "—")
        direction = p.get("direction", "NEUTRAL")
        timeframe = p.get("timeframe", "short")
        company   = p.get("company_name") or ticker
        entry     = _calc_entry(p)
        tgt_low   = p.get("target_low") or 0
        tgt_high  = p.get("target_high") or 0
        stop      = p.get("stop_loss") or 0
        current   = p.get("live_current_price")
        signal    = p.get("live_signal") or "—"
        reason    = p.get("live_signal_reason") or "Waiting for next price_watcher run…"
        updated   = _last_updated(p.get("live_signal_updated_at"))
        ret       = _return_so_far(p)
        peak      = p.get("live_peak_price")

        dir_color = "#15803d" if direction == "BULLISH" else "#b91c1c" if direction == "BEARISH" else "#475569"
        dir_icon  = "▲" if direction == "BULLISH" else "▼" if direction == "BEARISH" else "●"
        tf_label  = {"short": "⚡ Short", "medium": "📈 Mid", "long": "🌱 Long"}.get(timeframe, timeframe)

        ret_str = f"{ret:+.2f}%" if ret is not None else "—"
        ret_color = "#15803d" if (ret or 0) >= 0 else "#b91c1c"

        signal_html = _signal_badge(signal) if signal in ("HOLD", "SELL") else \
            '<span style="background:#f8fafc;color:#94a3b8;border:1px solid #e2e8f0;border-radius:20px;padding:3px 12px;font-size:13px">— Pending</span>'

        card_border = "#dc2626" if signal == "SELL" else "#16a34a" if signal == "HOLD" else "#e2e8f0"
        card_bg     = "#fff5f5" if signal == "SELL" else "#f0fdf4" if signal == "HOLD" else "#f8fafc"

        st.markdown(
            f"""<div style="border:2px solid {card_border};border-radius:12px;padding:16px 20px;
                background:{card_bg};margin-bottom:16px">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px">
                <div>
                  <span style="font-size:22px;font-weight:800;color:#0f172a">{ticker}</span>
                  <span style="font-size:13px;color:#64748b;margin-left:8px">{company}</span>
                  <span style="font-size:12px;font-weight:600;color:{dir_color};margin-left:10px">{dir_icon} {direction} · {tf_label}</span>
                </div>
                <div style="display:flex;align-items:center;gap:12px">
                  {signal_html}
                  <span style="font-size:12px;color:#94a3b8">Updated {updated}</span>
                </div>
              </div>
              <div style="margin-top:12px;font-size:13px;color:#475569;font-style:italic">{reason}</div>
              <div style="display:flex;gap:24px;margin-top:12px;flex-wrap:wrap;font-size:13px">
                <div><span style="color:#94a3b8">Entry</span> &nbsp;<strong>${entry:.2f}</strong></div>
                <div><span style="color:#94a3b8">Current</span> &nbsp;<strong>${current:.2f}</strong></div>
                <div><span style="color:#94a3b8">Return</span> &nbsp;<strong style="color:{ret_color}">{ret_str}</strong></div>
                <div><span style="color:#94a3b8">Target</span> &nbsp;<strong>${tgt_low:.2f} – ${tgt_high:.2f}</strong></div>
                <div><span style="color:#94a3b8">Stop</span> &nbsp;<strong>${stop:.2f}</strong></div>
                {f'<div><span style="color:#94a3b8">Peak</span> &nbsp;<strong>${peak:.2f}</strong></div>' if peak else ''}
              </div>
            </div>""",
            unsafe_allow_html=True,
        ) if current else st.markdown(
            f"""<div style="border:2px solid #e2e8f0;border-radius:12px;padding:16px 20px;
                background:#f8fafc;margin-bottom:16px">
              <span style="font-size:20px;font-weight:700">{ticker}</span>
              <span style="font-size:13px;color:#94a3b8;margin-left:8px">Waiting for first price update…</span>
            </div>""",
            unsafe_allow_html=True,
        )

        # Action buttons below each card
        if current:
            b1, b2, b3, _ = st.columns([1.6, 1.6, 1.8, 6])
            with b1:
                if st.button("✅ Close as Win", key=f"lt_win_{pred_id}"):
                    _close_tracked(pred_id, "WIN", p)
                    st.rerun()
            with b2:
                if st.button("❌ Close as Loss", key=f"lt_loss_{pred_id}"):
                    _close_tracked(pred_id, "LOSS", p)
                    st.rerun()
            with b3:
                if st.button("🔴 Stop Tracking", key=f"lt_untrack_{pred_id}"):
                    from database.db import update_prediction
                    update_prediction(pred_id, {
                        "is_tracked": False,
                        "live_signal": None,
                        "live_signal_reason": None,
                        "live_signal_updated_at": None,
                        "live_current_price": None,
                        "live_peak_price": None,
                    })
                    _fetch_tracked.clear()
                    st.rerun()

        st.markdown("")


render()
