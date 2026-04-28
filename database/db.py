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




# ── Predictions ────────────────────────────────────────────────────────────────

_NEW_PREDICTION_COLS = {"expires_on", "days_to_target", "timing_rationale", "company_name"}

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
    return get_client().table("predictions").select("*").eq("outcome", "PENDING").execute().data

def get_predictions(filters: dict = None, limit: int = 500) -> list:
    q = get_client().table("predictions").select("*").order("predicted_on", desc=True).limit(limit)
    if filters:
        for k, v in filters.items():
            q = q.eq(k, v)
    return q.execute().data

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


# ── Forensic Sessions ──────────────────────────────────────────────────────────

def insert_forensic_session(data: dict) -> dict:
    return get_client().table("forensic_sessions").insert(data).execute().data[0]

def get_forensic_sessions(ticker: str = None) -> list:
    q = get_client().table("forensic_sessions").select("*").order("analyzed_on", desc=True)
    if ticker:
        q = q.eq("ticker", ticker)
    return q.execute().data
