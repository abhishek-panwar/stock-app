"""
Modal scheduled jobs — replaces GitHub Actions cron triggers.
Deploy with: modal deploy modal_jobs.py

All schedules are defined in config/schedule.py.
To change a timing, edit config/schedule.py and run:
    python3 config/schedule.py --sync
"""
import modal
import sys
from pathlib import Path

app = modal.App("stock-app")

project_dir = Path(__file__).parent

# Import schedule config to read cron strings
# On Modal: modal_jobs.py sits at /root/ but project files land at /root/app/
# Locally: project_dir is the repo root, config/ is directly inside it
sys.path.insert(0, str(project_dir / "app"))  # Modal: /root/app
sys.path.insert(0, str(project_dir))           # local: repo root
from config.schedule import to_cron, get_job

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
@app.function(
    image=image,
    secrets=secrets,
    timeout=1200,
    schedule=modal.Cron(to_cron(get_job("nightly_scanner"))),
)
def nightly_scanner():
    import sys
    sys.path.insert(0, "/root/app")
    import scripts.nightly_scanner as s
    s.run()


# ── Feedback Engine ────────────────────────────────────────────────────────────
@app.function(
    image=image,
    secrets=secrets,
    timeout=300,
    schedule=modal.Cron(to_cron(get_job("feedback_engine_modal"))),
)
def feedback_engine():
    import sys
    sys.path.insert(0, "/root/app")
    import scripts.feedback_engine as s
    s.run()


# Health Monitor moved to GitHub Actions (health_check.yml) to free up Modal slot.
# Opportunity Analyzer moved to GitHub Actions (Modal free tier limit is 5 crons)
# Failure Analyzer moved to GitHub Actions (failure_analyzer.yml)


# ── Fundamentals Fetcher — Fri/Sat/Sun 8 AM PT ────────────────────────────────
# AV budget: Fri=24 calls, Sat=25, Sun=25 (script handles this automatically)
@app.function(
    image=image,
    secrets=secrets,
    timeout=600,
    schedule=modal.Cron(to_cron(get_job("fundamentals_fetcher"))),
)
def fundamentals_fetcher():
    import sys
    sys.path.insert(0, "/root/app")
    import scripts.fundamentals_fetcher as s
    s.run()


# ── Price Watcher — every 5 min during market hours Mon–Fri ───────────────────
# Modal Cron takes a single string; use the full UTC window 13-20 UTC = 6 AM-1 PM PT
@app.function(
    image=image,
    secrets=secrets,
    timeout=60,
    schedule=modal.Cron("*/5 13-20 * * 1-5"),  # 6:00 AM – 1:00 PM PT  Mon–Fri
)
def price_watcher():
    import sys
    sys.path.insert(0, "/root/app")
    import scripts.price_watcher as s
    s.run()
