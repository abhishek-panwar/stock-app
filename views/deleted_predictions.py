import streamlit as st
from datetime import datetime
import pytz

PT = pytz.timezone("America/Los_Angeles")

DIR_COLORS = {
    "BULLISH": ("#f0fdf4", "#16a34a", "#15803d"),
    "BEARISH": ("#fef2f2", "#dc2626", "#b91c1c"),
    "NEUTRAL": ("#f8fafc", "#94a3b8", "#64748b"),
}


def _purge_one(pred_id: str):
    from database.db import get_client
    get_client().table("predictions").delete().eq("id", pred_id).execute()


def render():
    st.title("🗑️ Deleted Predictions")
    st.caption("Soft-deleted predictions. Use Undo to restore — the prediction reappears with today's timestamp so it sorts as new.")

    try:
        from database.db import get_deleted_predictions
        deleted = get_deleted_predictions(limit=200)
    except Exception as e:
        st.error(f"Database error: {e}")
        return

    if not deleted:
        st.info("No deleted predictions. Deletions from the dashboard and history page appear here.")
        return

    # ── Bulk actions ──────────────────────────────────────────────────────────
    col_count, col_clear = st.columns([6, 2])
    with col_count:
        st.markdown(f"**{len(deleted)} deleted prediction{'s' if len(deleted) != 1 else ''}**")
    with col_clear:
        if st.button("🗑️ Permanently delete all", type="secondary"):
            st.session_state["confirm_purge"] = True

    if st.session_state.get("confirm_purge"):
        st.warning("This permanently removes all deleted predictions from the database. This cannot be undone.")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Yes, permanently delete all", type="primary"):
                try:
                    from database.db import get_client
                    get_client().table("predictions").delete().not_.is_("deleted_at", "null").execute()
                    st.session_state["confirm_purge"] = False
                    st.success("All deleted predictions permanently removed.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed: {e}")
        with c2:
            if st.button("Cancel"):
                st.session_state["confirm_purge"] = False
                st.rerun()

    st.markdown("---")

    # ── Deleted list ──────────────────────────────────────────────────────────
    for p in deleted:
        pred_id    = p.get("id", "")
        ticker     = p.get("ticker", "—")
        company    = p.get("company_name") or ticker
        direction  = p.get("direction", "NEUTRAL")
        confidence = p.get("confidence", 0)
        score      = p.get("score", 0)
        timeframe  = p.get("timeframe", "short")
        outcome    = p.get("outcome", "PENDING")
        position   = p.get("position", "HOLD")

        entry  = p.get("price_at_prediction") or 0
        target = p.get("target_low") or 0
        profit_pct = ((target - entry) / entry * 100) if entry > 0 and target > 0 else 0
        profit_str = f"+{profit_pct:.1f}%" if profit_pct > 0 else f"{profit_pct:.1f}%"
        days_to_target = p.get("days_to_target")
        tenure_str = f"{days_to_target}d" if days_to_target else "—"

        deleted_at_str = "—"
        try:
            dt = datetime.fromisoformat(p["deleted_at"].replace("Z", "+00:00"))
            deleted_at_str = dt.astimezone(PT).strftime("%b %d  %I:%M %p PT")
        except Exception:
            pass

        predicted_str = "—"
        try:
            dt = datetime.fromisoformat(p.get("predicted_on", "").replace("Z", "+00:00"))
            predicted_str = dt.astimezone(PT).strftime("%b %d  %I:%M %p PT")
        except Exception:
            pass

        dir_icon     = "▲" if direction == "BULLISH" else "▼" if direction == "BEARISH" else "●"
        outcome_icon = "🟢" if outcome == "WIN" else "🔴" if outcome == "LOSS" else "🟡"

        header = (
            f"🗑️ **{ticker}** — {company}  ·  "
            f"{dir_icon} {direction}  ·  "
            f"{confidence}% conf  ·  {score}/100  ·  "
            f"{profit_str}  ·  ~{tenure_str}  ·  "
            f"deleted {deleted_at_str}"
        )

        with st.container(border=True):
            title_col, btn_col = st.columns([9, 2])
            with title_col:
                st.markdown(header)
                st.markdown(
                    f'<div style="font-size:12px;color:#64748b;margin-top:2px">'
                    f'Predicted: {predicted_str}  ·  {outcome_icon} {outcome}  ·  {timeframe}  ·  {position}'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            with btn_col:
                undo_c, purge_c = st.columns(2)
                with undo_c:
                    if st.button("↩ Undo", key=f"undo_{pred_id}", type="primary"):
                        try:
                            from database.db import restore_prediction
                            restore_prediction(pred_id)
                            st.success(f"{ticker} restored.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Restore failed: {e}")
                with purge_c:
                    if st.button("🗑️ Delete", key=f"purge_{pred_id}"):
                        try:
                            _purge_one(pred_id)
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed: {e}")

            with st.expander("Details", expanded=False):
                stop = p.get("stop_loss") or 0
                rr   = abs(target - entry) / abs(entry - stop) if entry > 0 and stop > 0 and abs(entry - stop) > 0 else 0

                c1, c2, c3 = st.columns(3)
                with c1:
                    st.markdown("**Entry**")
                    st.write(f"Price at signal: ${entry:.2f}")
                    st.write(f"Buy range: ${p.get('buy_range_low', 0):.2f} – ${p.get('buy_range_high', 0):.2f}")
                    st.write(f"Stop loss: ${stop:.2f}")
                with c2:
                    st.markdown("**Target**")
                    st.write(f"Range: ${p.get('target_low', 0):.2f} – ${p.get('target_high', 0):.2f}")
                    st.write(f"Profit potential: {profit_str}")
                    st.write(f"R/R: 1 : {rr:.1f}")
                with c3:
                    st.markdown("**Meta**")
                    st.write(f"Source: {p.get('source', '—')}")
                    st.write(f"Formula: {p.get('formula_version', '—')}")
                    if p.get("timing_rationale"):
                        st.caption(f"💡 {p['timing_rationale']}")

                if p.get("reasoning"):
                    st.markdown(
                        f'<div style="background:#f8fafc;border-left:3px solid #94a3b8;border-radius:0 6px 6px 0;'
                        f'padding:10px 14px;margin-top:8px;font-size:13px;color:#1e293b;line-height:1.6">'
                        f'{p["reasoning"]}</div>',
                        unsafe_allow_html=True,
                    )
