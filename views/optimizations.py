import streamlit as st
from datetime import datetime
import pytz
import os

PT = pytz.timezone("America/Los_Angeles")

EDITABLE_FILES = {
    "indicators/scoring.py":      "Scoring formula — signal weights and thresholds",
    "scripts/nightly_scanner.py": "Scanner — thresholds, MAX_STOCKS, R/R filter",
    "services/ai_service.py":     "AI prompt — Claude instructions",
}


def render():
    st.title("🧠 Optimizations")
    st.caption("Every analysis run is stored here permanently — approve or reject suggestions, and track what's been done over time.")

    # ── Check if new closed predictions exist since last analysis ─────────────
    try:
        from database.db import get_all_optimizations as _get_opts, get_predictions as _get_preds
        _existing = _get_opts(limit=1)
        _closed_count = len([p for p in _get_preds(limit=500) if p.get("outcome") in ("WIN", "LOSS")])
        _last_analyzed = (_existing[0].get("total_analyzed", 0) or 0) if _existing else 0
        _has_new_data = _closed_count > _last_analyzed
    except Exception:
        _has_new_data = True  # if check fails, allow the run

    # ── Run analysis button ───────────────────────────────────────────────────
    run_col, _ = st.columns([2, 8])
    with run_col:
        if st.button("▶ Run Analysis Now", type="primary", key="run_analysis_btn",
                     disabled=not _has_new_data,
                     help=None if _has_new_data else "No new closed predictions since last analysis"):
            with st.spinner("Running failure analysis..."):
                try:
                    import sys, os
                    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                    import scripts.failure_analyzer as fa
                    import importlib
                    importlib.reload(fa)
                    result = fa.run()
                    if result and result.get("skipped"):
                        st.info("No new closed predictions since last analysis — nothing to re-analyze.")
                    elif result:
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
    applied_icon = " 🔧" if opt.get("applied") else ""
    proj = opt.get("projected_improvement", 0) or 0

    with st.expander(
        f"{status_icon} **{date_str}** — {opt.get('suggestion_plain', '')[:80]}  ·  "
        f"+{proj:.0f}% projected{applied_icon}",
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
                st.markdown(
                    f"<div style='text-align:right;font-size:12px;color:#64748b'>"
                    f"{status_icon} {status} on {reviewed_at}</div>",
                    unsafe_allow_html=True,
                )

        if opt.get("failure_pattern"):
            st.markdown(
                f"""<div style="background:#fef2f2;border-left:3px solid #dc2626;border-radius:0 6px 6px 0;
                padding:8px 12px;margin:8px 0;font-size:13px;color:#374151">
                <strong>Why predictions failed:</strong><br>{opt['failure_pattern']}</div>""",
                unsafe_allow_html=True,
            )
        if opt.get("success_pattern"):
            st.markdown(
                f"""<div style="background:#f0fdf4;border-left:3px solid #16a34a;border-radius:0 6px 6px 0;
                padding:8px 12px;margin:8px 0;font-size:13px;color:#374151">
                <strong>What wins had in common:</strong><br>{opt['success_pattern']}</div>""",
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
            a_col, r_col, d_col, _ = st.columns([1.2, 1.2, 1.2, 5])
            with a_col:
                if st.button("✅ Approve", key=f"approve_{opt_id}"):
                    try:
                        update_optimization_status(opt_id, "APPROVED")
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
            with d_col:
                if st.button("🗑️ Delete", key=f"delete_{opt_id}"):
                    try:
                        from database.db import delete_optimization
                        delete_optimization(opt_id)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed: {e}")

        # ── Apply to code (approved only, not yet applied) ────────────────────
        st.caption(f"DEBUG: status={status!r}  applied={opt.get('applied')!r}")
        if status == "APPROVED" and not opt.get("applied"):
            st.markdown("---")
            st.markdown("**Apply to code:**")

            preview_key = f"preview_{opt_id}"
            diff_key    = f"diff_{opt_id}"

            file_options = list(EDITABLE_FILES.keys())
            sel_file = st.selectbox(
                "Which file to modify?",
                file_options,
                format_func=lambda f: f"{f}  —  {EDITABLE_FILES[f]}",
                key=f"file_sel_{opt_id}",
            )

            if st.button("🔍 Preview Change", key=f"preview_btn_{opt_id}"):
                with st.spinner("Asking Claude to generate the change..."):
                    diff = _generate_diff(opt, sel_file)
                st.session_state[diff_key] = diff

            if diff_key in st.session_state:
                diff = st.session_state[diff_key]
                if diff.get("error"):
                    st.error(diff["error"])
                else:
                    st.markdown("**Proposed change:**")
                    st.code(diff.get("new_code", ""), language="python")
                    st.caption(f"Replaces lines {diff.get('start_line')}–{diff.get('end_line')} in `{sel_file}`")

                    c1, c2, _ = st.columns([1.5, 1.5, 7])
                    with c1:
                        if st.button("✅ Confirm & Apply", key=f"apply_{opt_id}", type="primary"):
                            try:
                                _apply_diff(diff, sel_file, opt_id, opt.get("suggestion_plain", ""))
                                st.success("✅ Change applied and committed to GitHub!")
                                del st.session_state[diff_key]
                                st.rerun()
                            except Exception as e:
                                st.error(f"Apply failed: {e}")
                    with c2:
                        if st.button("✕ Discard", key=f"discard_{opt_id}"):
                            del st.session_state[diff_key]
                            st.rerun()

        if status == "APPROVED" and opt.get("applied"):
            st.success(f"🔧 Applied to code on {opt.get('applied_on', '—')}")


def _generate_diff(opt: dict, sel_file: str) -> dict:
    """Ask Claude to produce the minimal code change for this suggestion."""
    import anthropic
    import json
    import os

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    file_path = os.path.join(base_dir, sel_file)
    try:
        with open(file_path, "r") as f:
            current_code = f.read()
    except Exception as e:
        return {"error": f"Could not read {sel_file}: {e}"}

    numbered = "\n".join(f"{i+1}: {line}" for i, line in enumerate(current_code.splitlines()))

    prompt = f"""You are modifying a Python file based on a specific optimization suggestion.

FILE: {sel_file}
CURRENT CONTENTS (with line numbers):
{numbered}

OPTIMIZATION TO APPLY:
Plain English: {opt.get('suggestion_plain', '')}
Technical Detail: {opt.get('suggestion_technical', '')}

Your task:
1. Identify exactly which lines to replace
2. Write the replacement code (only the changed section, not the whole file)
3. Be minimal — change only what is needed for this specific suggestion
4. Preserve all indentation exactly

Respond in this exact JSON (no other text):
{{
  "start_line": <integer — first line to replace, 1-indexed>,
  "end_line": <integer — last line to replace, 1-indexed inclusive>,
  "new_code": "<the replacement code as a string, preserving indentation, using \\n for newlines>"
}}"""

    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)
        if not all(k in result for k in ("start_line", "end_line", "new_code")):
            return {"error": "Claude returned incomplete response — try again"}
        return result
    except Exception as e:
        return {"error": f"Failed to generate change: {e}"}


def _apply_diff(diff: dict, sel_file: str, opt_id: str, suggestion_plain: str):
    """Commit the change via GitHub API, then mark as applied in DB."""
    import os
    import base64
    import requests
    from datetime import datetime

    token = os.environ.get("GITHUB_TOKEN", "")
    repo  = os.environ.get("GITHUB_REPO", "")   # e.g. "abhishek-panwar/stock-app"
    if not token or not repo:
        raise ValueError("GITHUB_TOKEN and GITHUB_REPO must be set in Streamlit secrets")

    api = f"https://api.github.com/repos/{repo}/contents/{sel_file}"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}

    # Fetch current file to get sha + content
    r = requests.get(api, headers=headers)
    r.raise_for_status()
    file_info = r.json()
    current_content = base64.b64decode(file_info["content"]).decode("utf-8")
    sha = file_info["sha"]

    # Apply the line replacement
    lines = current_content.splitlines()
    start = diff["start_line"] - 1
    end   = diff["end_line"]
    new_lines = diff["new_code"].splitlines()
    updated = "\n".join(lines[:start] + new_lines + lines[end:]) + "\n"

    # Push via GitHub API
    commit_msg = f"auto-apply: {suggestion_plain[:72]}"
    payload = {
        "message": commit_msg,
        "content": base64.b64encode(updated.encode("utf-8")).decode("utf-8"),
        "sha": sha,
    }
    r2 = requests.put(api, headers=headers, json=payload)
    r2.raise_for_status()

    # Mark as applied in DB
    from database.db import mark_optimization_applied
    mark_optimization_applied(opt_id, datetime.utcnow().strftime("%b %d, %Y"))
