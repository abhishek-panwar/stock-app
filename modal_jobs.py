"""
Modal scheduled jobs — replaces GitHub Actions cron triggers.
Deploy with: modal deploy modal_jobs.py
"""
import modal
from pathlib import Path

app = modal.App("stock-app")

project_dir = Path(__file__).parent

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "yfinance>=0.2.37",
        "finnhub-python>=2.4.19",
        "pandas>=2.1.0",
        "ta>=0.11.0",
        "anthropic>=0.25.0",
        "supabase>=2.4.0",
        "python-dotenv>=1.0.0",
        "requests>=2.31.0",
        "pytz>=2024.1",
        "psycopg2-binary>=2.9.9",
    )
    .add_local_dir(str(project_dir), remote_path="/root/app",
        ignore=["venv", ".git", "__pycache__", "*.pyc", ".env", "*.log"]
    )
)

secrets = [modal.Secret.from_name("stock-app-secrets")]


# ── Nightly Scanner ────────────────────────────────────────────────────────────
# Tonight: fires at 3:10 AM UTC. Change to "0 3 * * *" for 8 PM PT tomorrow.
@app.function(
    image=image,
    secrets=secrets,
    timeout=1200,
    schedule=modal.Cron("0 3 * * *"),
)
def nightly_scanner():
    import sys
    sys.path.insert(0, "/root/app")
    import scripts.nightly_scanner as s
    s.run()


# ── Prediction Verifier ────────────────────────────────────────────────────────
@app.function(
    image=image,
    secrets=secrets,
    timeout=300,
    schedule=modal.Cron("0 22 * * 1-5"),  # 2:00 PM PT (22:00 UTC)
)
def prediction_verifier():
    import sys
    sys.path.insert(0, "/root/app")
    import scripts.prediction_verifier as s
    s.run()


# ── Feedback Engine ────────────────────────────────────────────────────────────
@app.function(
    image=image,
    secrets=secrets,
    timeout=300,
    schedule=modal.Cron("30 22 * * 1-5"),  # 2:30 PM PT (22:30 UTC)
)
def feedback_engine():
    import sys
    sys.path.insert(0, "/root/app")
    import scripts.feedback_engine as s
    s.run()


# ── Health Monitor ─────────────────────────────────────────────────────────────
@app.function(
    image=image,
    secrets=secrets,
    timeout=300,
    schedule=modal.Cron("0 13 * * 1"),  # 6:00 AM PT Mondays only
)
def health_monitor():
    import sys
    sys.path.insert(0, "/root/app")
    import scripts.health_monitor as s
    s.run()


# Opportunity Analyzer moved to GitHub Actions (Modal free tier limit is 5 crons)

# ── Failure Analyzer ──────────────────────────────────────────────────────────
@app.function(
    image=image,
    secrets=secrets,
    timeout=300,
    schedule=modal.Cron("0 1 * * 1"),  # 5:00 PM PT Mondays only (every ~3 trading days)
)
def failure_analyzer():
    import sys
    sys.path.insert(0, "/root/app")
    import scripts.failure_analyzer as s
    s.run()


# Price Watcher stays on GitHub Actions (free tier cron limit is 5)
