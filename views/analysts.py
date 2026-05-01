import streamlit as st
from datetime import datetime


def render():
    st.title("👤 Publication Credibility Tracker")
    st.caption("Tracks which news sources publish accurate calls. Scores update automatically when predictions close.")

    # ── Count closed predictions for warning label ───────────────────────────
    try:
        from database.db import get_predictions
        all_preds = get_predictions(limit=1000)
        closed_count = sum(1 for p in all_preds if p.get("outcome") in ("WIN", "LOSS"))
        unique_tickers = len({p["ticker"] for p in all_preds if p.get("outcome") in ("WIN", "LOSS")})
    except Exception:
        closed_count = 0
        unique_tickers = 0

    # ── Buttons ───────────────────────────────────────────────────────────────
    bc1, bc2, _ = st.columns([2, 2, 6])
    with bc1:
        st.markdown('<div class="btn-safe">', unsafe_allow_html=True)
        rebuild_clicked = st.button("🔄 Rebuild from Cache", key="rebuild_cache_btn",
                                    help="Uses only cached news — zero API calls")
    with bc2:
        st.markdown('<div class="btn-api">', unsafe_allow_html=True)
        live_clicked = st.button("🌐 Fetch Live Scores", key="rebuild_live_btn",
                                 help=f"Makes {unique_tickers} Finnhub API calls — one per unique ticker")

    if live_clicked:
        st.warning(
            f"⚠️ This will make **{unique_tickers} Finnhub API calls** "
            f"(one per unique ticker across {closed_count} closed predictions). "
            f"Safe within free tier limits (60/min).",
            icon=None,
        )

    if rebuild_clicked:
        _rebuild_scores(live=False)
    elif live_clicked:
        _rebuild_scores(live=True)

    try:
        from database.db import get_analysts, get_client
        analysts = get_analysts(order_by="weighted_score")
        try:
            pub_scores = get_client().table("publication_scores").select("*").order("weighted_score", desc=True).execute().data
        except Exception:
            pub_scores = []
    except Exception as e:
        st.error(f"Database error: {e}")
        return

    if not analysts:
        st.info("No publication data yet — click **Rebuild from Cache** or **Fetch Live Scores** to populate.")
        _show_explainer()
        return

    # ── Summary metrics ───────────────────────────────────────────────────────
    total_analysts = len(analysts)
    positive = sum(1 for a in analysts if a.get("weighted_score", 0) > 0)
    top = analysts[0] if analysts else None

    m1, m2, m3 = st.columns(3)
    with m1:
        st.metric("Analysts Tracked", total_analysts)
    with m2:
        st.metric("Positive Score", f"{positive}/{total_analysts}")
    with m3:
        if top:
            st.metric("Top Analyst", top["name"], f"+{top.get('weighted_score', 0):.1f} weighted")

    sort_by = st.selectbox("Sort by", ["Weighted Score", "Binary Score", "Win Rate", "Total Predictions", "Lead Time"])

    sort_map = {
        "Weighted Score": lambda a: a.get("weighted_score", 0),
        "Binary Score": lambda a: a.get("binary_score", 0),
        "Win Rate": lambda a: a.get("wins", 0) / max(a.get("total_predictions", 1), 1),
        "Total Predictions": lambda a: a.get("total_predictions", 0),
        "Lead Time": lambda a: a.get("avg_lead_time_days", 0),
    }
    sorted_analysts = sorted(analysts, key=sort_map[sort_by], reverse=True)

    st.markdown("---")
    st.markdown("### Analyst Leaderboard")

    for analyst in sorted_analysts:
        _analyst_card(analyst)

    # ── Publication leaderboard ───────────────────────────────────────────────
    if pub_scores:
        st.markdown("---")
        st.markdown("### Publication Leaderboard")
        import pandas as pd
        pub_df = pd.DataFrame(pub_scores)[["publication_name", "binary_score", "weighted_score", "total_predictions", "win_rate"]]
        pub_df.columns = ["Publication", "Binary", "Weighted", "Predictions", "Win Rate"]
        pub_df["Win Rate"] = pub_df["Win Rate"].apply(lambda x: f"{(x or 0)*100:.1f}%")
        st.dataframe(pub_df, use_container_width=True, hide_index=True)


def _rebuild_scores(live: bool = False):
    label = "Fetching live news and rebuilding scores…" if live else "Rebuilding scores from cache…"
    status = st.status(label, expanded=True)
    try:
        from services.analyst_service import rebuild_all_scores
        stats = rebuild_all_scores(live=live)
        if "error" in stats:
            status.update(label=f"❌ Error: {stats['error']}", state="error", expanded=True)
        else:
            status.write(f"✅ Processed {stats['predictions_processed']} closed predictions")
            status.write(f"📰 {stats['articles_linked']} article–prediction links created")
            status.write(f"📊 {stats['publications_found']} publications scored")
            if stats.get("skipped_no_cache"):
                status.write(f"⚠️ {stats['skipped_no_cache']} predictions had no cached news")
            if stats.get("live_fetched"):
                status.write(f"🌐 {stats['live_fetched']} live Finnhub calls made")
            status.update(
                label=f"Done — {stats['publications_found']} publications scored from {stats['predictions_processed']} predictions",
                state="complete", expanded=False,
            )
            st.rerun()
    except Exception as e:
        status.update(label=f"❌ Failed: {e}", state="error", expanded=True)


def _analyst_card(analyst: dict):
    name = analyst.get("name", "Unknown")
    wins = analyst.get("wins", 0)
    losses = analyst.get("losses", 0)
    total = analyst.get("total_predictions", 0)
    weighted = analyst.get("weighted_score", 0)
    win_rate = wins / total * 100 if total > 0 else 0
    score_emoji = "🟢" if weighted > 0 else "🔴" if weighted < 0 else "⚪"

    # Load prediction history to compute direction breakdown for the header
    try:
        from database.db import get_analyst_predictions
        ap = get_analyst_predictions(analyst["id"])
    except Exception:
        ap = []

    closed = [p for p in ap if p.get("outcome") in ("WIN", "LOSS")]
    bull = [p for p in closed if p.get("direction") == "BULLISH"]
    bear = [p for p in closed if p.get("direction") == "BEARISH"]
    bull_wr = sum(1 for p in bull if p["outcome"] == "WIN") / len(bull) * 100 if bull else None
    bear_wr = sum(1 for p in bear if p["outcome"] == "WIN") / len(bear) * 100 if bear else None

    dir_note = ""
    if bull_wr is not None:
        dir_note += f"  ▲ {bull_wr:.0f}%({len(bull)})"
    if bear_wr is not None:
        dir_note += f"  ▼ {bear_wr:.0f}%({len(bear)})"

    with st.expander(
        f"{score_emoji} **{name}**  |  Overall: {win_rate:.0f}% ({total} calls)"
        f"  |  Weighted: {weighted:+.1f}{dir_note}",
        expanded=False,
    ):
        if ap:
            _show_prediction_history(ap)
        else:
            st.caption("No prediction history yet.")


def _show_prediction_history(ap: list):
    from collections import defaultdict

    closed = [p for p in ap if p.get("outcome") in ("WIN", "LOSS")]
    if not closed:
        st.caption("No closed predictions linked yet.")
        return

    # ── Direction breakdown ───────────────────────────────────────────────────
    bull = [p for p in closed if p.get("direction") == "BULLISH"]
    bear = [p for p in closed if p.get("direction") == "BEARISH"]

    bull_wins = sum(1 for p in bull if p["outcome"] == "WIN")
    bear_wins = sum(1 for p in bear if p["outcome"] == "WIN")
    bull_wr   = bull_wins / len(bull) * 100 if bull else None
    bear_wr   = bear_wins / len(bear) * 100 if bear else None
    bull_net  = sum(p.get("return_pct") or 0 for p in bull)
    bear_net  = sum(p.get("return_pct") or 0 for p in bear)

    bull_color = "#15803d" if (bull_wr or 0) >= 60 else "#b45309" if (bull_wr or 0) >= 40 else "#b91c1c"
    bear_color = "#15803d" if (bear_wr or 0) >= 60 else "#b45309" if (bear_wr or 0) >= 40 else "#b91c1c"

    st.markdown("**Direction breakdown**")
    d1, d2 = st.columns(2)
    with d1:
        if bull_wr is not None:
            verdict = "✅ Trust their BULLISH calls" if bull_wr >= 60 else "⚠️ Mixed on BULLISH" if bull_wr >= 40 else "❌ Avoid their BULLISH calls"
            st.markdown(
                f"""<div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:12px 14px">
                <div style="font-size:11px;color:#15803d;font-weight:700;text-transform:uppercase;margin-bottom:4px">▲ Bullish calls</div>
                <div style="font-size:24px;font-weight:800;color:{bull_color}">{bull_wr:.0f}%</div>
                <div style="font-size:12px;color:#64748b">{bull_wins}W / {len(bull)-bull_wins}L  ·  Net {bull_net:+.1f}%</div>
                <div style="font-size:11px;margin-top:6px;color:#374151">{verdict}</div>
                </div>""",
                unsafe_allow_html=True,
            )
        else:
            st.caption("No bullish calls yet.")
    with d2:
        if bear_wr is not None:
            verdict = "✅ Trust their BEARISH calls" if bear_wr >= 60 else "⚠️ Mixed on BEARISH" if bear_wr >= 40 else "❌ Avoid their BEARISH calls"
            st.markdown(
                f"""<div style="background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:12px 14px">
                <div style="font-size:11px;color:#b91c1c;font-weight:700;text-transform:uppercase;margin-bottom:4px">▼ Bearish calls</div>
                <div style="font-size:24px;font-weight:800;color:{bear_color}">{bear_wr:.0f}%</div>
                <div style="font-size:12px;color:#64748b">{bear_wins}W / {len(bear)-bear_wins}L  ·  Net {bear_net:+.1f}%</div>
                <div style="font-size:11px;margin-top:6px;color:#374151">{verdict}</div>
                </div>""",
                unsafe_allow_html=True,
            )
        else:
            st.caption("No bearish calls yet.")

    st.markdown("")

    # ── Timeframe breakdown ───────────────────────────────────────────────────
    tf_stats = defaultdict(lambda: {"wins": 0, "total": 0, "net": 0.0})
    for p in closed:
        tf = p.get("timeframe") or "unknown"
        tf_stats[tf]["total"] += 1
        tf_stats[tf]["net"] += p.get("return_pct") or 0
        if p.get("outcome") == "WIN":
            tf_stats[tf]["wins"] += 1

    if tf_stats:
        st.markdown("**By timeframe**")
        tf_cols = st.columns(len(tf_stats))
        for i, (tf, s) in enumerate(sorted(tf_stats.items())):
            wr = s["wins"] / s["total"] * 100 if s["total"] > 0 else 0
            col = "#15803d" if wr >= 60 else "#b45309" if wr >= 40 else "#b91c1c"
            with tf_cols[i]:
                st.markdown(
                    f'<div style="text-align:center;padding:8px;background:#f8fafc;'
                    f'border:1px solid #e2e8f0;border-radius:8px">'
                    f'<div style="font-size:11px;color:#64748b">{tf.capitalize()}</div>'
                    f'<div style="font-size:20px;font-weight:700;color:{col}">{wr:.0f}%</div>'
                    f'<div style="font-size:11px;color:#94a3b8">{s["total"]} calls · {s["net"]:+.1f}%</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    # ── Recent articles ───────────────────────────────────────────────────────
    st.markdown("")
    wins_list   = [p for p in closed if p["outcome"] == "WIN"]
    losses_list = [p for p in closed if p["outcome"] == "LOSS"]
    col_w, col_l = st.columns(2)
    with col_w:
        if wins_list:
            st.markdown(f"**Recent wins ({len(wins_list)})**")
            for p in wins_list[:5]:
                ret   = p.get("return_pct") or 0
                title = p.get("article_title") or "—"
                url   = p.get("article_url") or ""
                direction = p.get("direction", "")
                dir_tag = "▲" if direction == "BULLISH" else "▼" if direction == "BEARISH" else ""
                line  = f"{dir_tag} {title[:55]}"
                if url:
                    st.markdown(f'<div style="font-size:12px;margin:3px 0"><a href="{url}" target="_blank">{line}</a> <span style="color:#15803d">+{ret:.1f}%</span></div>', unsafe_allow_html=True)
                else:
                    st.markdown(f'<div style="font-size:12px;margin:3px 0;color:#374151">{line} <span style="color:#15803d">+{ret:.1f}%</span></div>', unsafe_allow_html=True)
    with col_l:
        if losses_list:
            st.markdown(f"**Recent losses ({len(losses_list)})**")
            for p in losses_list[:5]:
                ret   = p.get("return_pct") or 0
                title = p.get("article_title") or "—"
                url   = p.get("article_url") or ""
                direction = p.get("direction", "")
                dir_tag = "▲" if direction == "BULLISH" else "▼" if direction == "BEARISH" else ""
                line  = f"{dir_tag} {title[:55]}"
                if url:
                    st.markdown(f'<div style="font-size:12px;margin:3px 0"><a href="{url}" target="_blank">{line}</a> <span style="color:#b91c1c">{ret:.1f}%</span></div>', unsafe_allow_html=True)
                else:
                    st.markdown(f'<div style="font-size:12px;margin:3px 0;color:#374151">{line} <span style="color:#b91c1c">{ret:.1f}%</span></div>', unsafe_allow_html=True)


def _fmt_ts(ts) -> str:
    if not ts:
        return "—"
    try:
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts).strftime("%b %d")
        return str(ts)[:10]
    except Exception:
        return "—"


def _show_explainer():
    st.markdown("""
**How this works:**
1. When the nightly scanner finds news articles about a stock, it records the author name and publication
2. When a prediction closes as WIN or LOSS, each article that influenced it gets credited/penalized
3. Over time, a leaderboard of reliable analysts builds automatically

**Scoring:**
- **Binary:** +1 per WIN, -1 per LOSS
- **Weighted:** return ÷ 5 (so +15% WIN = +3 pts, -15% LOSS = -3 pts, capped ±5 per trade)
- **Lead time:** articles published *before* the move score higher than recappers

This page becomes useful after 30–50 closed predictions (typically 2–3 months of scanning).
""")
