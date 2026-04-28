import streamlit as st
from datetime import datetime
import pytz

PT = pytz.timezone("America/Los_Angeles")


def render():
    st.title("🧠 System Evolution")
    st.caption("Every formula improvement, pending suggestion, and rejected change — full audit trail.")

    try:
        from database.db import get_pending_suggestions, get_formula_history, update_suggestion_status
        pending = get_pending_suggestions()
        history = get_formula_history()
    except Exception as e:
        st.error(f"Database error: {e}")
        return

    # ── Header stats ──────────────────────────────────────────────────────────
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("Formula Version", f"v1.{len(history)}" if history else "v1.0")
    with m2:
        st.metric("Improvements Made", len(history))
    with m3:
        st.metric("Pending Suggestions", len(pending))
    with m4:
        if history:
            last = history[0].get("applied_on", "")[:10]
            st.metric("Last Improvement", last)
        else:
            st.metric("Last Improvement", "None yet")

    st.markdown("---")

    # ── Pending suggestions ───────────────────────────────────────────────────
    if pending:
        st.markdown(f"### ⏳ Pending Suggestions ({len(pending)})")
        st.caption("Oldest first. No expiry — review in your own time.")
        for s in pending:
            _pending_card(s, update_suggestion_status)
        st.markdown("---")

    # ── Improvement history ───────────────────────────────────────────────────
    if history:
        st.markdown("### ✅ Improvement History")
        for i, h in enumerate(history):
            _history_card(h, len(history) - i)
    else:
        st.info("No formula changes approved yet. The system will generate suggestions after a few weeks of scanning.")
        st.markdown("""
**What to expect:**
- After 2–3 weeks: Shadow portfolio starts flagging missed opportunities
- After 1 month: First formula suggestions appear here for your review
- After 2–3 months: Accuracy stats become reliable enough for Claude's self-calibration
""")

    # ── Rejected suggestions ──────────────────────────────────────────────────
    try:
        from database.db import get_client
        rejected = get_client().table("formula_suggestions").select("*").eq("status", "REJECTED").order("reviewed_on", desc=True).execute().data
        if rejected:
            st.markdown("---")
            st.markdown("### ❌ Rejected Suggestions")
            for r in rejected:
                date_str = (r.get("reviewed_on") or "")[:10]
                st.markdown(f"- **{date_str}** — {r.get('plain_english', '—')[:100]}  *(rejected)*")
    except Exception:
        pass


def _pending_card(s: dict, update_fn):
    source = s.get("source", "unknown")
    source_emoji = {"shadow_portfolio": "👻", "deep_dive": "🔬", "feedback_engine": "🔄"}.get(source, "🤖")
    date_str = (s.get("suggestion_date") or "")[:10]

    with st.expander(
        f"⏳ **PENDING** — {date_str}  {source_emoji} from {source.replace('_', ' ')}",
        expanded=True,
    ):
        st.markdown(f"**What to change (plain English):**")
        st.info(s.get("plain_english", "—"))

        st.markdown(f"**Technical detail:**")
        st.code(s.get("technical_detail", "—"))

        evidence = s.get("evidence") or {}
        if evidence:
            st.markdown(f"**Evidence:** {evidence}")

        projected = s.get("projected_improvement")
        if projected:
            st.markdown(f"**Projected improvement:** +{projected:.1f}% win rate")

        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("✅ Approve", key=f"approve_{s['id']}", type="primary"):
                try:
                    update_fn(s["id"], "APPROVED", datetime.now(PT).isoformat())
                    st.success("Approved! Update the formula in scoring.py to apply this change.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")
        with col2:
            if st.button("❌ Reject", key=f"reject_{s['id']}"):
                try:
                    update_fn(s["id"], "REJECTED", datetime.now(PT).isoformat())
                    st.info("Rejected and logged.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")
        with col3:
            if st.button("🔔 Remind Later", key=f"remind_{s['id']}"):
                st.info("Still in queue — come back when ready.")


def _history_card(h: dict, number: int):
    date_str = (h.get("applied_on") or "")[:10]
    wr_before = h.get("win_rate_before")
    wr_after = h.get("win_rate_after")

    impact_str = ""
    if wr_before and wr_after:
        delta = wr_after - wr_before
        impact_str = f"  |  Win rate: {wr_before:.0f}% → {wr_after:.0f}% ({delta:+.0f}%)"

    with st.expander(
        f"✅ **IMPROVEMENT #{number}** — {date_str}{impact_str}",
        expanded=False,
    ):
        if h.get("plain_english"):
            st.markdown("**What changed (plain English):**")
            st.info(h["plain_english"])

        if h.get("technical_detail"):
            st.markdown("**What changed (technical):**")
            st.code(h["technical_detail"])

        evidence = h.get("evidence") or {}
        if evidence:
            st.markdown(f"**Evidence:** {evidence}")

        if wr_before and wr_after:
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Win Rate Before", f"{wr_before:.1f}%")
            with col2:
                st.metric("Win Rate After", f"{wr_after:.1f}%")
            with col3:
                delta = wr_after - wr_before
                st.metric("Improvement", f"{delta:+.1f}%")
        elif h.get("applied_on"):
            st.caption("Impact will be measured 4 weeks after this change was applied.")
