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
        st.markdown('</div>', unsafe_allow_html=True)
    with bc2:
        st.markdown('<div class="btn-api">', unsafe_allow_html=True)
        live_clicked = st.button("🌐 Fetch Live Scores", key="rebuild_live_btn",
                                 help=f"Makes {unique_tickers} Finnhub API calls — one per unique ticker")
        st.markdown('</div>', unsafe_allow_html=True)

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
    pub = analyst.get("publication", "—")
    binary = analyst.get("binary_score", 0)
    weighted = analyst.get("weighted_score", 0)
    wins = analyst.get("wins", 0)
    losses = analyst.get("losses", 0)
    total = analyst.get("total_predictions", 0)
    win_rate = wins / total * 100 if total > 0 else 0
    lead_time = analyst.get("avg_lead_time_days")

    score_emoji = "🟢" if weighted > 0 else "🔴" if weighted < 0 else "⚪"
    lead_note = ""
    if lead_time is not None:
        lead_note = f" · Lead time: {lead_time:+.1f}d"
        if lead_time < 0:
            lead_note += " ⚠️ recapping"

    with st.expander(
        f"{score_emoji} **{name}** — {pub}  |  Binary: {binary:+d}  |  Weighted: {weighted:+.1f}  |  {win_rate:.0f}% win ({total} predictions){lead_note}",
        expanded=False,
    ):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**Binary score:** {binary:+d}")
            st.markdown(f"**Weighted score:** {weighted:+.1f}")
            st.markdown(f"**Win rate:** {win_rate:.1f}% ({wins}W / {losses}L)")
            if lead_time is not None:
                sentiment = "Predictive ✅" if lead_time > 0 else "Same-day ⚪" if lead_time >= -0.5 else "Recapping ⚠️"
                st.markdown(f"**Avg lead time:** {lead_time:+.1f} days — {sentiment}")

        with c2:
            st.markdown("**Score interpretation:**")
            st.markdown("""
| Binary | Weighted | Meaning |
|---|---|---|
| +1 per WIN | return_pct ÷ 5 | Weighted reveals magnitude |
| -1 per LOSS | capped ±5/trade | High weighted = big correct calls |
""")

        # Load detailed prediction history
        try:
            from database.db import get_analyst_predictions
            ap = get_analyst_predictions(analyst["id"])
            if ap:
                _show_prediction_history(ap)
        except Exception:
            pass


def _show_prediction_history(ap: list):
    wins = [p for p in ap if p.get("outcome") == "WIN"]
    losses = [p for p in ap if p.get("outcome") == "LOSS"]

    # Sector breakdown
    from collections import defaultdict
    sector_stats = defaultdict(lambda: {"wins": 0, "total": 0, "weighted": 0})
    for p in ap:
        if p.get("outcome") in ("WIN", "LOSS"):
            s = p.get("sector") or "Unknown"
            sector_stats[s]["total"] += 1
            sector_stats[s]["weighted"] += p.get("weighted_contribution") or 0
            if p.get("outcome") == "WIN":
                sector_stats[s]["wins"] += 1

    if sector_stats:
        st.markdown("**Sector breakdown:**")
        for sector, stats in sorted(sector_stats.items(), key=lambda x: x[1]["weighted"], reverse=True):
            rate = stats["wins"] / stats["total"] * 100 if stats["total"] > 0 else 0
            flag = "✅" if stats["weighted"] > 0 else "⚠️"
            st.caption(f"{flag} {sector}: {rate:.0f}% win ({stats['total']} trades), weighted: {stats['weighted']:+.1f}")

    # Timeframe fit
    tf_stats = defaultdict(lambda: {"wins": 0, "total": 0})
    for p in ap:
        if p.get("outcome") in ("WIN", "LOSS"):
            tf = p.get("timeframe") or "unknown"
            tf_stats[tf]["total"] += 1
            if p.get("outcome") == "WIN":
                tf_stats[tf]["wins"] += 1

    if tf_stats:
        st.markdown("**Timeframe fit:**")
        for tf, stats in tf_stats.items():
            rate = stats["wins"] / stats["total"] * 100 if stats["total"] > 0 else 0
            st.caption(f"{tf.capitalize()}: {rate:.0f}% ({stats['total']} trades)")

    col_w, col_l = st.columns(2)
    with col_w:
        if wins:
            st.markdown(f"**Wins ({len(wins)}):**")
            for p in wins[:5]:
                ret = p.get("return_pct") or 0
                title = p.get("article_title") or "—"
                url = p.get("article_url") or ""
                date_str = _fmt_ts(p.get("article_published_at"))
                if url:
                    st.markdown(f"- [{title[:50]}]({url}) +{ret:.1f}% ({date_str})")
                else:
                    st.markdown(f"- {title[:50]} +{ret:.1f}% ({date_str})")
    with col_l:
        if losses:
            st.markdown(f"**Losses ({len(losses)}):**")
            for p in losses[:5]:
                ret = p.get("return_pct") or 0
                title = p.get("article_title") or "—"
                url = p.get("article_url") or ""
                date_str = _fmt_ts(p.get("article_published_at"))
                if url:
                    st.markdown(f"- [{title[:50]}]({url}) {ret:.1f}% ({date_str})")
                else:
                    st.markdown(f"- {title[:50]} {ret:.1f}% ({date_str})")


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
