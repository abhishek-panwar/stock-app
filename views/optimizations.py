import streamlit as st
from datetime import datetime
import pytz

PT = pytz.timezone("America/Los_Angeles")


def render():
    st.title("🧠 Optimizations")
    st.caption("Every analysis run is stored here permanently — approve or reject suggestions, and track what's been done over time.")

    # ── Run analysis button ───────────────────────────────────────────────────
    run_col, _ = st.columns([2, 8])
    with run_col:
        if st.button("▶ Run Analysis Now", type="primary", key="run_analysis_btn"):
            with st.spinner("Running failure analysis..."):
                try:
                    import sys, os
                    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                    import scripts.failure_analyzer as fa
                    import importlib
                    importlib.reload(fa)
                    result = fa.run()
                    if result:
                        st.success(f"Analysis done — {result.get('suggestions_saved', 0)} new suggestions added.")
                    else:
                        st.info("Not enough closed predictions to analyze yet.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Analysis failed: {e}")

    # ── Load all optimizations ────────────────────────────────────────────────
    try:
        from database.db import get_all_optimizations, update_optimization_status
        opts = get_all_optimizations(limit=200)
    except Exception as e:
        st.error(f"Could not load optimizations: {e}")
        return

    if not opts:
        st.info("No analysis run yet. Click 'Run Analysis Now' or wait for the automated 5 PM PT run.")
        return

    pending  = [o for o in opts if o.get("status") == "PENDING"]
    approved = [o for o in opts if o.get("status") == "APPROVED"]
    rejected = [o for o in opts if o.get("status") == "REJECTED"]

    # ── Summary bar ──────────────────────────────────────────────────────────
    st.markdown(
        f"""<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;
            padding:14px 20px;margin:12px 0 20px;display:flex;gap:28px;flex-wrap:wrap">
          <div><div style="font-size:11px;color:#64748b">Total Suggestions</div>
               <div style="font-size:22px;font-weight:700;color:#1e293b">{len(opts)}</div></div>
          <div><div style="font-size:11px;color:#64748b">Pending Review</div>
               <div style="font-size:22px;font-weight:700;color:#b45309">{len(pending)}</div></div>
          <div><div style="font-size:11px;color:#64748b">Approved</div>
               <div style="font-size:22px;font-weight:700;color:#15803d">{len(approved)}</div></div>
          <div><div style="font-size:11px;color:#64748b">Rejected</div>
               <div style="font-size:22px;font-weight:700;color:#b91c1c">{len(rejected)}</div></div>
        </div>""",
        unsafe_allow_html=True,
    )

    # ── Pending ───────────────────────────────────────────────────────────────
    if pending:
        st.markdown(f"### ⏳ Pending Review ({len(pending)})")
        for opt in pending:
            _render_opt(opt, expanded=True)
        st.markdown("---")

    # ── Approved ──────────────────────────────────────────────────────────────
    if approved:
        st.markdown(f"### ✅ Approved ({len(approved)})")
        st.caption("These have been approved — apply the technical detail to the scoring/scanner code if not done yet.")
        for opt in approved:
            _render_opt(opt, expanded=False)
        st.markdown("---")

    # ── Rejected ──────────────────────────────────────────────────────────────
    if rejected:
        st.markdown(f"### ❌ Rejected ({len(rejected)})")
        for opt in rejected:
            _render_opt(opt, expanded=False)


def _render_opt(opt: dict, expanded: bool):
    from database.db import update_optimization_status

    opt_id   = opt.get("id", "")
    status   = opt.get("status", "PENDING")
    date_str = opt.get("analysis_date", "—")
    reviewed_at = opt.get("reviewed_at")
    if reviewed_at:
        try:
            reviewed_at = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00")).astimezone(PT).strftime("%b %d, %Y")
        except Exception:
            pass

    status_icon = "⏳" if status == "PENDING" else "✅" if status == "APPROVED" else "❌"
    proj = opt.get("projected_improvement", 0) or 0

    with st.expander(
        f"{status_icon} **{date_str}** — {opt.get('suggestion_plain', '')[:80]}  ·  "
        f"+{proj:.0f}% projected improvement",
        expanded=expanded,
    ):
        col_meta, col_status = st.columns([7, 3])
        with col_meta:
            st.markdown(
                f"**Date:** {date_str}  ·  "
                f"**Trades analyzed:** {opt.get('total_analyzed', 0)} "
                f"({opt.get('wins_analyzed', 0)}W / {opt.get('losses_analyzed', 0)}L)"
            )
        with col_status:
            if reviewed_at and status != "PENDING":
                st.markdown(f"<div style='text-align:right;font-size:12px;color:#64748b'>{status_icon} {status} on {reviewed_at}</div>", unsafe_allow_html=True)

        if opt.get("failure_pattern"):
            st.markdown(
                f"""<div style="background:#fef2f2;border-left:3px solid #dc2626;border-radius:0 6px 6px 0;
                padding:8px 12px;margin:8px 0;font-size:13px;color:#374151">
                <strong>Why predictions failed:</strong><br>{opt['failure_pattern']}</div>""",
                unsafe_allow_html=True,
            )
        if opt.get("timing_accuracy_note"):
            st.markdown(
                f"""<div style="background:#f0fdf4;border-left:3px solid #16a34a;border-radius:0 6px 6px 0;
                padding:8px 12px;margin:8px 0;font-size:13px;color:#374151">
                <strong>Timing accuracy:</strong><br>{opt['timing_accuracy_note']}</div>""",
                unsafe_allow_html=True,
            )

        st.markdown(f"**What to change:** {opt.get('suggestion_plain', '—')}")
        st.markdown(f"**Technical detail:** `{opt.get('suggestion_technical', '—')}`")
        if opt.get("evidence_tickers"):
            st.markdown(f"**Evidence tickers:** {opt['evidence_tickers']}")
        st.markdown(f"**Projected win rate improvement:** +{proj:.0f}%")

        if status == "PENDING":
            a_col, r_col, _ = st.columns([1.2, 1.2, 7])
            with a_col:
                if st.button("✅ Approve", key=f"approve_{opt_id}"):
                    try:
                        update_optimization_status(opt_id, "APPROVED")
                        st.success("Approved!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed: {e}")
            with r_col:
                if st.button("❌ Reject", key=f"reject_{opt_id}"):
                    try:
                        update_optimization_status(opt_id, "REJECTED")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed: {e}")
