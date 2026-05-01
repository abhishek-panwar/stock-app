"""
Publication credibility tracker.

Finnhub returns source (publication name) per article, not individual author names.
Each publication is tracked as an "analyst" — binary score (+1 WIN / -1 LOSS) and
weighted score (return_pct / 5, capped ±5 per trade).
"""
from database.db import get_client, log_error


def save_articles_for_prediction(prediction_id: str, ticker: str, articles: list, predicted_on: str):
    """
    Called by the scanner after inserting a prediction.
    Stores each article's source as an analyst_predictions row (outcome=PENDING).
    No API calls — uses the articles already fetched during scoring.
    """
    if not articles or not prediction_id:
        return
    client = get_client()
    for art in articles[:10]:
        source = art.get("source", "").strip()
        if not source:
            continue
        try:
            # Upsert the publication into analysts table
            analyst = client.table("analysts").upsert(
                {"name": source, "publication": source},
                on_conflict="name,publication"
            ).execute().data
            if not analyst:
                continue
            analyst_id = analyst[0]["id"]

            # Compute lead time in days (positive = article before prediction close)
            lead_time = None
            art_ts = art.get("datetime")
            if art_ts and predicted_on:
                try:
                    from datetime import datetime, timezone
                    art_dt = datetime.fromtimestamp(art_ts, tz=timezone.utc)
                    pred_dt = datetime.fromisoformat(predicted_on.replace("Z", "+00:00"))
                    lead_time = round((pred_dt - art_dt).total_seconds() / 86400, 1)
                except Exception:
                    pass

            client.table("analyst_predictions").insert({
                "analyst_id":          analyst_id,
                "prediction_id":       prediction_id,
                "article_title":       (art.get("headline") or "")[:300],
                "article_url":         art.get("url") or None,
                "article_published_at": predicted_on,
                "lead_time_days":      lead_time,
                "outcome":             "PENDING",
            }).execute()
        except Exception as e:
            log_error("analyst_service", f"save_articles: {e}", ticker=ticker, level="WARNING")


def update_scores_for_prediction(prediction_id: str, outcome: str, return_pct: float, timeframe: str = ""):
    """
    Called by the verifier after a prediction closes.
    Updates all analyst_predictions rows linked to this prediction,
    then recomputes aggregate scores on the analysts table.
    """
    if not prediction_id or outcome not in ("WIN", "LOSS"):
        return
    client = get_client()
    try:
        rows = client.table("analyst_predictions").select("*").eq("prediction_id", prediction_id).execute().data
        if not rows:
            return

        weighted = round(max(-5.0, min(5.0, (return_pct or 0) / 5)), 3)

        for row in rows:
            try:
                client.table("analyst_predictions").update({
                    "outcome":               outcome,
                    "return_pct":            return_pct,
                    "weighted_contribution": weighted if outcome == "WIN" else -abs(weighted),
                    "timeframe":             timeframe or row.get("timeframe"),
                }).eq("id", row["id"]).execute()

                _recompute_analyst(row["analyst_id"])
            except Exception as e:
                log_error("analyst_service", f"update_scores row {row['id']}: {e}", level="WARNING")
    except Exception as e:
        log_error("analyst_service", f"update_scores_for_prediction {prediction_id}: {e}", level="WARNING")


def _recompute_analyst(analyst_id: str):
    """Recomputes aggregate stats for one analyst from their closed prediction rows."""
    client = get_client()
    rows = client.table("analyst_predictions").select("*").eq("analyst_id", analyst_id).execute().data
    closed = [r for r in rows if r.get("outcome") in ("WIN", "LOSS")]
    if not closed:
        return

    wins    = [r for r in closed if r["outcome"] == "WIN"]
    losses  = [r for r in closed if r["outcome"] == "LOSS"]
    binary  = len(wins) - len(losses)
    weighted = round(sum(r.get("weighted_contribution") or 0 for r in closed), 2)
    lead_times = [r["lead_time_days"] for r in closed if r.get("lead_time_days") is not None]
    avg_lead = round(sum(lead_times) / len(lead_times), 1) if lead_times else None

    from datetime import datetime, timezone
    client.table("analysts").update({
        "binary_score":       binary,
        "weighted_score":     weighted,
        "total_predictions":  len(closed),
        "wins":               len(wins),
        "losses":             len(losses),
        "avg_lead_time_days": avg_lead,
        "last_updated":       datetime.now(timezone.utc).isoformat(),
    }).eq("id", analyst_id).execute()


def rebuild_all_scores(live: bool = False) -> dict:
    """
    Full rebuild from scratch — reads all closed predictions, optionally fetches
    live news from Finnhub (live=True) or uses only api_cache (live=False).
    Returns stats dict.
    """
    import time
    client = get_client()
    stats = {
        "predictions_processed": 0, "articles_linked": 0,
        "publications_found": 0, "skipped_no_cache": 0, "live_fetched": 0,
    }

    try:
        from database.db import get_predictions, get_cache, set_cache
        all_preds = get_predictions(limit=1000)
        closed = [p for p in all_preds if p.get("outcome") in ("WIN", "LOSS")]

        # Wipe existing data for clean rebuild
        client.table("analyst_predictions").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
        client.table("analysts").update({
            "binary_score": 0, "weighted_score": 0.0,
            "total_predictions": 0, "wins": 0, "losses": 0,
        }).neq("id", "00000000-0000-0000-0000-000000000000").execute()

        # Pre-fetch live news per unique ticker if live=True
        live_cache: dict = {}
        if live:
            unique_tickers = list({p["ticker"] for p in closed})
            from services.finnhub_service import get_news_sentiment
            for ticker in unique_tickers:
                try:
                    result = get_news_sentiment(ticker, hours=72)
                    live_cache[ticker] = result.get("articles", [])
                    set_cache(f"news_sentiment_{ticker}", result, ttl_hours=168)
                    stats["live_fetched"] += 1
                    time.sleep(0.5)  # stay well within 60/min rate limit
                except Exception:
                    live_cache[ticker] = []

        for pred in closed:
            ticker = pred["ticker"]
            pred_id = pred["id"]
            outcome = pred["outcome"]
            return_pct = pred.get("return_pct") or 0
            predicted_on = pred.get("predicted_on", "")
            timeframe = pred.get("timeframe", "")

            # Resolve articles: live cache → api_cache → skip
            if live and ticker in live_cache:
                articles = live_cache[ticker]
            else:
                cached = get_cache(f"news_sentiment_{ticker}")
                articles = cached.get("articles", []) if cached and isinstance(cached, dict) else []

            if not articles:
                stats["skipped_no_cache"] += 1
                stats["predictions_processed"] += 1
                continue

            for art in articles[:10]:
                source = art.get("source", "").strip()
                if not source:
                    continue
                try:
                    analyst = client.table("analysts").upsert(
                        {"name": source, "publication": source},
                        on_conflict="name,publication"
                    ).execute().data
                    if not analyst:
                        continue
                    analyst_id = analyst[0]["id"]

                    lead_time = None
                    art_ts = art.get("datetime")
                    if art_ts and predicted_on:
                        try:
                            from datetime import datetime, timezone
                            art_dt = datetime.fromtimestamp(art_ts, tz=timezone.utc)
                            pred_dt = datetime.fromisoformat(predicted_on.replace("Z", "+00:00"))
                            lead_time = round((pred_dt - art_dt).total_seconds() / 86400, 1)
                        except Exception:
                            pass

                    weighted_contrib = round(max(-5.0, min(5.0, return_pct / 5)), 3)
                    if outcome == "LOSS":
                        weighted_contrib = -abs(weighted_contrib)

                    client.table("analyst_predictions").insert({
                        "analyst_id":            analyst_id,
                        "prediction_id":         pred_id,
                        "article_title":         (art.get("headline") or "")[:300],
                        "article_url":           art.get("url") or None,
                        "article_published_at":  predicted_on,
                        "lead_time_days":        lead_time,
                        "outcome":               outcome,
                        "return_pct":            return_pct,
                        "weighted_contribution": weighted_contrib,
                        "timeframe":             timeframe,
                    }).execute()
                    stats["articles_linked"] += 1
                except Exception:
                    pass

            stats["predictions_processed"] += 1

        # Recompute all aggregate scores
        all_analysts = client.table("analysts").select("id").execute().data
        for a in all_analysts:
            _recompute_analyst(a["id"])
        stats["publications_found"] = len(all_analysts)

    except Exception as e:
        log_error("analyst_service", f"rebuild_all_scores: {e}", level="ERROR")
        stats["error"] = str(e)

    return stats
