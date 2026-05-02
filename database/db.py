import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

_client: Client | None = None

def get_client() -> Client:
    global _client
    if _client is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
        _client = create_client(url, key)
    return _client


# ── Auto-migrations ────────────────────────────────────────────────────────────
# Each entry: (table, column, postgres_type)
# Safe to run repeatedly — IF NOT EXISTS is a no-op.
_COLUMN_MIGRATIONS = [
    ("optimization_queue", "success_pattern", "text"),
    ("predictions", "expires_on",       "text"),
    ("predictions", "days_to_target",   "integer"),
    ("predictions", "timing_rationale", "text"),
    ("predictions", "company_name",     "text"),
    ("predictions", "asset_class",      "text"),
    ("predictions", "earnings_label",   "text"),
    ("predictions", "insider_signal",   "text"),
    ("predictions", "market_cap",       "bigint"),
    ("predictions", "avg_volume",       "bigint"),
    ("predictions", "deleted_at",       "timestamptz"),
    ("predictions", "verified_on",      "timestamptz"),
    ("predictions", "price_at_close",   "numeric"),
    ("predictions", "return_pct",       "numeric"),
    ("predictions", "closed_reason",    "text"),
]

_TABLE_MIGRATIONS = [
    """CREATE TABLE IF NOT EXISTS hot_tickers (
        id bigint generated always as identity primary key,
        ticker text not null,
        scanned_at text
    )""",
    """CREATE TABLE IF NOT EXISTS error_logs (
        id bigint generated always as identity primary key,
        source text, level text, ticker text,
        message text, detail text,
        occurred_at timestamptz default now()
    )""",
    """CREATE TABLE IF NOT EXISTS api_cache (
        key text primary key,
        value text not null,
        expires_at timestamptz not null,
        updated_at timestamptz default now()
    )""",
    """CREATE TABLE IF NOT EXISTS earnings_calendar (
        id bigint generated always as identity primary key,
        ticker text not null,
        days_to_earnings integer,
        earnings_date text,
        scanned_at text
    )""",
    """CREATE TABLE IF NOT EXISTS api_call_log (
        id bigint generated always as identity primary key,
        run_date text not null,
        api text not null,
        ticker text not null,
        success boolean not null,
        error text,
        logged_at timestamptz default now()
    )""",
]

def run_migrations() -> None:
    """
    Apply any missing schema changes to Supabase.
    Uses direct Postgres connection via DATABASE_URL secret.
    Called at nightly scanner startup — safe to run on every deploy.
    """
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        return  # secret not configured — skip silently
    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cur = conn.cursor()
        for sql in _TABLE_MIGRATIONS:
            cur.execute(sql)
        for table, column, col_type in _COLUMN_MIGRATIONS:
            cur.execute(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_type};"
            )
        cur.close()
        conn.close()
        print("  Migrations: all schema changes applied.")
    except Exception as e:
        try:
            log_error("migrations", f"Migration warning: {e}", level="WARNING")
        except Exception:
            pass




# ── API Cache ─────────────────────────────────────────────────────────────────

def get_cache(key: str):
    """Returns cached value (parsed JSON) if it exists and hasn't expired. Else None."""
    try:
        import json
        from datetime import datetime, timezone
        rows = (get_client().table("api_cache")
                .select("value,expires_at")
                .eq("key", key)
                .execute().data)
        if not rows:
            return None
        row = rows[0]
        expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > expires:
            return None
        return json.loads(row["value"])
    except Exception:
        return None

def delete_cache(key: str) -> None:
    """Remove a cache entry so the next read triggers a fresh fetch."""
    try:
        get_client().table("api_cache").delete().eq("key", key).execute()
    except Exception:
        pass

def set_cache(key: str, value, ttl_hours: float) -> None:
    """Store value (serialised to JSON) in cache with given TTL in hours."""
    try:
        import json
        from datetime import datetime, timezone, timedelta
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat()
        get_client().table("api_cache").upsert(
            {"key": key, "value": json.dumps(value), "expires_at": expires_at},
            on_conflict="key"
        ).execute()
    except Exception:
        pass  # cache failure is never fatal


# ── Predictions ────────────────────────────────────────────────────────────────

_NEW_PREDICTION_COLS = {"expires_on", "days_to_target", "timing_rationale", "company_name", "asset_class", "earnings_label", "insider_signal"}

def prediction_exists_today(ticker: str, scan_date: str) -> bool:
    """Returns True if a PENDING prediction for this ticker already exists from today's scan."""
    rows = (get_client().table("predictions").select("id")
            .eq("ticker", ticker)
            .eq("outcome", "PENDING")
            .gte("predicted_on", scan_date)
            .is_("deleted_at", "null")
            .execute().data)
    return len(rows) > 0

def get_pending_prediction_for_ticker(ticker: str) -> dict | None:
    """Returns the existing PENDING prediction for ticker, or None."""
    rows = (get_client().table("predictions").select("*")
            .eq("ticker", ticker)
            .eq("outcome", "PENDING")
            .is_("deleted_at", "null")
            .order("predicted_on", desc=True)
            .limit(1)
            .execute().data)
    return rows[0] if rows else None

def replace_prediction_if_stronger(ticker: str, new_profit_pct: float, new_pred: dict) -> str:
    """
    Compares new_profit_pct against any existing PENDING prediction for ticker.
    - If no existing: returns "insert" (caller should insert).
    - If directions differ (e.g. BULLISH vs BEARISH): returns "insert" (different trade thesis).
    - If existing profit + 2% < new_profit: soft-deletes old, returns "replaced".
    - Otherwise: returns "skipped".
    Threshold = 2 percentage points to avoid churn on minor differences.
    """
    existing = get_pending_prediction_for_ticker(ticker)
    if not existing:
        return "insert"

    # Different directions = different trade theses — don't compete, just insert
    if existing.get("direction") != new_pred.get("direction"):
        return "insert"

    entry  = existing.get("price_at_prediction") or 0
    target = existing.get("target_low") or 0
    old_profit_pct = abs(target - entry) / entry * 100 if entry > 0 else 0

    if new_profit_pct > old_profit_pct + 2.0:
        soft_delete_prediction(existing["id"])
        return "replaced"
    return "skipped"

def insert_prediction(data: dict) -> dict:
    try:
        return get_client().table("predictions").insert(data).execute().data[0]
    except Exception as e:
        # If insert fails because new columns don't exist yet, retry without them
        if any(col in str(e) for col in _NEW_PREDICTION_COLS):
            slim = {k: v for k, v in data.items() if k not in _NEW_PREDICTION_COLS}
            return get_client().table("predictions").insert(slim).execute().data[0]
        raise

def get_open_predictions() -> list:
    return (get_client().table("predictions").select("*")
            .eq("outcome", "PENDING")
            .is_("deleted_at", "null")
            .execute().data)

def get_predictions(filters: dict = None, limit: int = 500) -> list:
    q = (get_client().table("predictions").select("*")
         .is_("deleted_at", "null")          # exclude soft-deleted
         .order("predicted_on", desc=True).limit(limit))
    if filters:
        for k, v in filters.items():
            q = q.eq(k, v)
    return q.execute().data

def bulk_delete_open_predictions() -> int:
    from datetime import datetime
    result = get_client().table("predictions").update(
        {"deleted_at": datetime.utcnow().isoformat()}
    ).eq("outcome", "PENDING").is_("deleted_at", "null").execute()
    return len(result.data)

def soft_delete_prediction(prediction_id: str) -> None:
    from datetime import datetime
    get_client().table("predictions").update(
        {"deleted_at": datetime.utcnow().isoformat()}
    ).eq("id", prediction_id).execute()

def restore_prediction(prediction_id: str) -> None:
    from datetime import datetime
    get_client().table("predictions").update(
        {"deleted_at": None,
         "predicted_on": datetime.utcnow().isoformat()}  # refresh timestamp
    ).eq("id", prediction_id).execute()

def get_deleted_predictions(limit: int = 200) -> list:
    return (get_client().table("predictions").select("*")
            .not_.is_("deleted_at", "null")
            .order("deleted_at", desc=True).limit(limit)
            .execute().data)

def update_prediction(prediction_id: str, data: dict) -> dict:
    return get_client().table("predictions").update(data).eq("id", prediction_id).execute().data[0]


# ── Scan Logs ──────────────────────────────────────────────────────────────────

def insert_scan_log(data: dict) -> dict:
    return get_client().table("scan_logs").insert(data).execute().data[0]

def get_scan_logs(limit: int = 50) -> list:
    return get_client().table("scan_logs").select("*").order("timestamp", desc=True).limit(limit).execute().data


# ── Shadow Portfolio ───────────────────────────────────────────────────────────

def insert_shadow_price(data: dict) -> dict:
    return get_client().table("shadow_prices").insert(data).execute().data[0]

def insert_missed_opportunity(data: dict) -> dict:
    return get_client().table("missed_opportunities").insert(data).execute().data[0]

def get_missed_opportunities(since_date: str = None) -> list:
    q = get_client().table("missed_opportunities").select("*").order("rejection_date", desc=True)
    if since_date:
        q = q.gte("rejection_date", since_date)
    return q.execute().data


# ── Formula Suggestions ────────────────────────────────────────────────────────

def insert_formula_suggestion(data: dict) -> dict:
    return get_client().table("formula_suggestions").insert(data).execute().data[0]

def get_pending_suggestions() -> list:
    return get_client().table("formula_suggestions").select("*").eq("status", "PENDING").order("suggestion_date").execute().data

def update_suggestion_status(suggestion_id: str, status: str, reviewed_on: str) -> dict:
    return get_client().table("formula_suggestions").update({
        "status": status, "reviewed_on": reviewed_on, "reviewed_by": "user"
    }).eq("id", suggestion_id).execute().data[0]

def get_formula_history() -> list:
    return get_client().table("formula_history").select("*").order("applied_on", desc=True).execute().data


# ── Accuracy Stats ─────────────────────────────────────────────────────────────

def upsert_accuracy_stat(data: dict) -> dict:
    return get_client().table("accuracy_stats").upsert(data, on_conflict="signal_combo,ticker,timeframe").execute().data[0]

def get_accuracy_stats(reliable_only: bool = True) -> list:
    q = get_client().table("accuracy_stats").select("*")
    if reliable_only:
        q = q.eq("sample_reliable", True)
    return q.execute().data


# ── Analysts ───────────────────────────────────────────────────────────────────

def upsert_analyst(data: dict) -> dict:
    return get_client().table("analysts").upsert(data, on_conflict="name,publication").execute().data[0]

def get_analysts(order_by: str = "weighted_score") -> list:
    return get_client().table("analysts").select("*").order(order_by, desc=True).execute().data

def insert_analyst_prediction(data: dict) -> dict:
    return get_client().table("analyst_predictions").insert(data).execute().data[0]

def get_analyst_predictions(analyst_id: str) -> list:
    return get_client().table("analyst_predictions").select("*").eq("analyst_id", analyst_id).order("article_published_at", desc=True).execute().data


# ── API Call Log ─────────────────────────────────────────────────────────────

def log_api_call(run_date: str, api: str, ticker: str, success: bool, error: str = ""):
    """Log a single API call result. Silently no-ops if DB unavailable."""
    try:
        get_client().table("api_call_log").insert({
            "run_date": run_date,
            "api": api,
            "ticker": ticker,
            "success": success,
            "error": str(error)[:300] if error else None,
        }).execute()
    except Exception:
        pass

def clear_api_call_log(run_date: str):
    """Delete all log rows for a given run_date (called at start of each run)."""
    try:
        get_client().table("api_call_log").delete().eq("run_date", run_date).execute()
    except Exception:
        pass

def get_api_call_log(run_date: str) -> list:
    try:
        return get_client().table("api_call_log").select("*").eq("run_date", run_date).order("logged_at").execute().data
    except Exception:
        return []

def get_api_call_log_dates() -> list:
    """Returns distinct run_dates available, most recent first."""
    try:
        rows = get_client().table("api_call_log").select("run_date").order("run_date", desc=True).execute().data
        seen = []
        for r in rows:
            if r["run_date"] not in seen:
                seen.append(r["run_date"])
        return seen
    except Exception:
        return []


# ── Error Logs ────────────────────────────────────────────────────────────────

def log_error(source: str, message: str, detail: str = "", ticker: str = "", level: str = "ERROR"):
    """Write an error/warning/info entry. Silently no-ops if DB is unavailable.

    Limits enforced after each write (insert-then-trim avoids race conditions):
    - Max 5 rows per calendar day (UTC)
    - Max 5 days history
    """
    try:
        from datetime import datetime, timedelta, timezone
        client = get_client()
        now_utc = datetime.now(timezone.utc)

        # 1. Insert first — avoids read-check-insert race with concurrent workers
        client.table("error_logs").insert({
            "source": source,
            "level": level,
            "ticker": ticker or None,
            "message": str(message)[:500],
            "detail": str(detail)[:2000] if detail else None,
        }).execute()

        # 2. Trim today to 5 most recent (desc order → keep [0:5], delete rest)
        today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        today_rows = (client.table("error_logs")
                      .select("id")
                      .gte("occurred_at", today_start)
                      .order("occurred_at", desc=True)
                      .execute().data)
        if len(today_rows) > 5:
            for row in today_rows[5:]:
                client.table("error_logs").delete().eq("id", row["id"]).execute()

        # 3. Purge rows older than 5 days
        cutoff = (now_utc - timedelta(days=5)).isoformat()
        client.table("error_logs").delete().lt("occurred_at", cutoff).execute()
    except Exception:
        pass  # never let logging crash the caller

def get_error_logs(days: int = 5, source: str = None, level: str = None) -> list:
    from datetime import datetime, timedelta
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    q = (get_client().table("error_logs")
         .select("*")
         .gte("occurred_at", since)
         .order("occurred_at", desc=True))
    if source:
        q = q.eq("source", source)
    if level:
        q = q.eq("level", level)
    return q.execute().data


# ── Forensic Sessions ──────────────────────────────────────────────────────────

def insert_forensic_session(data: dict) -> dict:
    return get_client().table("forensic_sessions").insert(data).execute().data[0]

def get_forensic_sessions(ticker: str = None) -> list:
    q = get_client().table("forensic_sessions").select("*").order("analyzed_on", desc=True)
    if ticker:
        q = q.eq("ticker", ticker)
    return q.execute().data


# ── Hot Tickers ───────────────────────────────────────────────────────────────

def save_hot_tickers(tickers: list, scanned_at: str) -> None:
    client = get_client()
    client.table("hot_tickers").delete().neq("id", 0).execute()  # clear all
    rows = [{"ticker": t, "scanned_at": scanned_at} for t in tickers]
    if rows:
        client.table("hot_tickers").insert(rows).execute()

def get_hot_tickers_from_db() -> list:
    return get_client().table("hot_tickers").select("*").order("id").execute().data

def save_earnings_calendar(earnings_map: dict, scanned_at: str) -> None:
    """Persist earnings universe to DB. Clears old rows and inserts fresh data."""
    client = get_client()
    client.table("earnings_calendar").delete().neq("id", 0).execute()
    rows = [
        {"ticker": ticker, "days_to_earnings": v["days_to_earnings"],
         "earnings_date": v["earnings_date"], "scanned_at": scanned_at}
        for ticker, v in earnings_map.items()
    ]
    if rows:
        client.table("earnings_calendar").insert(rows).execute()

def get_earnings_calendar_from_db() -> list:
    return (get_client().table("earnings_calendar").select("*")
            .order("days_to_earnings").execute().data)


# ── Optimization Queue ────────────────────────────────────────────────────────

def insert_optimization(data: dict) -> dict:
    return get_client().table("optimization_queue").insert(data).execute().data[0]

def get_pending_optimizations() -> list:
    return (get_client().table("optimization_queue").select("*")
            .eq("status", "PENDING")
            .order("created_at", desc=True)
            .execute().data)

def get_all_optimizations(limit: int = 50) -> list:
    return (get_client().table("optimization_queue").select("*")
            .order("created_at", desc=True)
            .limit(limit)
            .execute().data)

def update_optimization_status(opt_id: str, status: str) -> dict:
    from datetime import datetime
    return get_client().table("optimization_queue").update({
        "status": status,
        "reviewed_at": datetime.utcnow().isoformat(),
    }).eq("id", opt_id).execute().data[0]

def delete_optimization(opt_id: str) -> None:
    get_client().table("optimization_queue").delete().eq("id", opt_id).execute()

def mark_optimization_applied(opt_id: str, applied_on: str) -> dict:
    return get_client().table("optimization_queue").update({
        "applied": True,
        "applied_on": applied_on,
    }).eq("id", opt_id).execute().data[0]
