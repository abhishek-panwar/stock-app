"""
Single source of truth for all job schedules.

All times are PT (America/Los_Angeles).
UTC offsets: PDT (Mar–Nov) = UTC-7,  PST (Nov–Mar) = UTC-8.

To change a job's schedule, edit JOBS below, then run:
    python3 config/schedule.py
That will rewrite the Modal cron strings and GitHub Actions YAML files automatically.
"""
from datetime import datetime, time
import pytz

PT = pytz.timezone("America/Los_Angeles")


# ── Job definitions ────────────────────────────────────────────────────────────
# Each entry:
#   id          : unique key used in modal_jobs.py and GHA workflow filenames
#   label       : human-readable name
#   platform    : "modal" | "gha"
#   time_pt     : (hour, minute) in PT — 24h format
#   days        : "daily" | "weekdays" | list of weekday ints (0=Mon…6=Sun) | "interval_min"
#   interval_min: only when days="interval_min" — how often in minutes
#   hour_range  : only when days="interval_min" — (start_hour_pt, end_hour_pt) window
#   description : brief label shown in logs and the schedule PDF

JOBS = [
    # ── Modal ──────────────────────────────────────────────────────────────────
    {
        "id":          "nightly_scanner",
        "label":       "Nightly Scanner",
        "platform":    "modal",
        "time_pt":     (19, 30),        # 7:30 PM PT
        "days":        [0, 1, 2, 3, 4, 6],  # Mon–Fri + Sun (not Sat)
        "description": "Score universe, pick top 50, Claude predictions, Telegram summary",
    },
    {
        "id":          "feedback_engine_modal",
        "label":       "Feedback Engine",
        "platform":    "modal",
        "time_pt":     (15, 30),        # 3:30 PM PT
        "days":        "weekdays",
        "description": "Update signal accuracy stats from closed predictions",
    },
    {
        "id":          "failure_analyzer",
        "label":       "Failure Analyzer",
        "platform":    "gha",
        "time_pt":     (18, 0),         # 6:00 PM PT
        "days":        [0],             # Mondays only
        "description": "Send wins/losses to Claude, save scoring improvement suggestions",
    },
    {
        "id":          "fundamentals_fetcher",
        "label":       "Fundamentals Fetcher",
        "platform":    "modal",
        "time_pt":     (8, 0),          # 8:00 AM PT
        "days":        [4, 5, 6],       # Fri, Sat, Sun
        "description": "Fetch revenue growth, margins, FCF, PEG via yfinance + Alpha Vantage",
    },

    # ── GitHub Actions ─────────────────────────────────────────────────────────
    {
        "id":          "verifier",
        "label":       "Prediction Verifier (GHA)",
        "platform":    "gha",
        "time_pt":     (20, 30),        # 8:30 PM PT
        "days":        "daily",
        "description": "Backup verifier after market close",
    },
    {
        "id":          "opportunity_analyzer",
        "label":       "Opportunity Analyzer",
        "platform":    "gha",
        "time_pt":     (20, 0),         # 8:00 PM PT
        "days":        [6],             # Sundays only
        "description": "Find missed moves, ask Claude why they were skipped",
    },
    {
        "id":          "health_check",
        "label":       "Health Monitor",
        "platform":    "gha",
        "time_pt":     (6, 0),          # 6:00 AM PT
        "days":        "weekdays",
        "description": "Ping Claude, Finnhub, Supabase — alert via Telegram if down",
    },
    {
        "id":          "price_watcher",
        "label":       "Price Watcher",
        "platform":    "modal",
        "time_pt":     None,            # interval-based, not a fixed time
        "days":        "interval_min",
        "interval_min": 5,
        "hour_range":  (6, 13),         # 6:00 AM – 1:00 PM PT
        "description": "Intraday price check every 5 min during market hours",
    },
]


# ── Conversion helpers ─────────────────────────────────────────────────────────

def pt_to_utc(hour_pt: int, minute_pt: int) -> tuple[int, int]:
    """Convert a PT wall-clock time to UTC using today's DST offset."""
    now_pt = datetime.now(PT)
    local = PT.localize(datetime(now_pt.year, now_pt.month, now_pt.day, hour_pt, minute_pt))
    utc = local.astimezone(pytz.utc)
    return utc.hour, utc.minute


def cron_days(days, day_offset: int = 0) -> str:
    """Convert days spec to cron day-of-week field.
    day_offset=1 when the UTC time rolls into the next calendar day vs PT."""
    if days == "daily":
        return "*"
    # list of ints (0=Mon…6=Sun) → cron uses 0=Sun…6=Sat
    CRON_MAP = {0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 0}
    if days == "weekdays":
        if day_offset == 0:
            return "1-5"
        return "2-6"  # weekdays shifted +1 day: Tue–Sat UTC = Mon–Fri PT
    return ",".join(str((CRON_MAP[d] + day_offset) % 7) for d in sorted(days))


def to_cron(job: dict) -> str | list[str]:
    """Return the UTC cron string(s) for a job."""
    if job["days"] == "interval_min":
        start_h, end_h = job["hour_range"]
        utc_start, _ = pt_to_utc(start_h, 0)
        utc_end, _   = pt_to_utc(end_h, 0)
        interval      = job["interval_min"]
        # Split into three cron lines the same way the original YAMLs did
        # Full hours in range, plus edge hours
        full_start = utc_start + 1
        full_end   = utc_end - 1
        if full_start <= full_end:
            return [
                f"*/{interval} {utc_start} * * 1-5",
                f"*/{interval} {full_start}-{full_end} * * 1-5",
                f"*/{interval} {utc_end} * * 1-5",
            ]
        return [f"*/{interval} {utc_start}-{utc_end} * * 1-5"]

    h, m = job["time_pt"]
    uh, um = pt_to_utc(h, m)
    # If the UTC hour is earlier than the PT hour, the day rolled over
    day_offset = 1 if uh < h else 0
    dow = cron_days(job["days"], day_offset)
    return f"{um} {uh} * * {dow}"


def pt_label(job: dict) -> str:
    """Human-readable PT schedule string for comments and docs."""
    if job["days"] == "interval_min":
        sh, _ = job["hour_range"]
        eh, _ = job["hour_range"][1], 0
        start = datetime.now(PT).replace(hour=job["hour_range"][0], minute=0)
        end   = datetime.now(PT).replace(hour=job["hour_range"][1], minute=0)
        return (f"Every {job['interval_min']} min, "
                f"{start.strftime('%-I:%M %p')}–{end.strftime('%-I:%M %p')} PT  "
                f"{_days_label(job['days'])}")
    h, m = job["time_pt"]
    t = datetime.now(PT).replace(hour=h, minute=m)
    return f"{t.strftime('%-I:%M %p')} PT  {_days_label(job['days'])}"


def _days_label(days) -> str:
    if days == "daily":      return "Daily"
    if days == "weekdays":   return "Mon–Fri"
    if days == "interval_min": return "Mon–Fri"
    NAMES = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
    return ", ".join(NAMES[d] for d in sorted(days))


def get_job(job_id: str) -> dict:
    """Look up a job by id."""
    return next(j for j in JOBS if j["id"] == job_id)


# ── Sync script — run this to push schedule changes to Modal + GHA ─────────────

def sync_all():
    """Rewrite modal_jobs.py cron strings and GHA YAML cron lines from JOBS."""
    import os, re

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # ── 1. Patch modal_jobs.py ────────────────────────────────────────────────
    modal_path = os.path.join(root, "modal_jobs.py")
    with open(modal_path) as f:
        src = f.read()

    MODAL_ID_MAP = {
        "nightly_scanner":       "nightly_scanner",
        "feedback_engine_modal": "feedback_engine",
        "failure_analyzer":      "failure_analyzer",
        "fundamentals_fetcher":  "fundamentals_fetcher",
    }

    for job in JOBS:
        if job["platform"] != "modal":
            continue
        fn_name = MODAL_ID_MAP.get(job["id"])
        if not fn_name:
            continue
        cron = to_cron(job)
        label = pt_label(job)
        new_line = f'    schedule=modal.Cron("{cron}"),  # {label}'
        # Replace the schedule= line inside the function decorated block
        src = re.sub(
            rf'(schedule=modal\.Cron\("[^"]*"\)[^\n]*)\n(.*def {fn_name})',
            f'{new_line}\n\\2def {fn_name}',
            src,
        )
        # Simpler pattern: just replace the Cron string + comment on that line
        src = re.sub(
            rf'schedule=modal\.Cron\("[^"]*"\)[^\n]*(?=\n.*\ndef {fn_name}|\n.*def {fn_name})',
            f'schedule=modal.Cron("{cron}"),  # {label}',
            src,
        )

    with open(modal_path, "w") as f:
        f.write(src)
    print(f"  Patched {modal_path}")

    # ── 2. Patch GHA YAML files ────────────────────────────────────────────────
    gha_dir = os.path.join(root, ".github", "workflows")

    for job in JOBS:
        if job["platform"] != "gha":
            continue
        yml_path = os.path.join(gha_dir, f"{job['id']}.yml")
        if not os.path.exists(yml_path):
            print(f"  WARN: {yml_path} not found, skipping")
            continue

        with open(yml_path) as f:
            yml = f.read()

        label = pt_label(job)
        cron = to_cron(job)

        if isinstance(cron, list):
            # interval job — replace all existing cron lines
            new_crons = "\n".join(f"    - cron: '{c}'  # {label}" for c in cron)
            yml = re.sub(
                r"(  schedule:\n)((?:    - cron: '[^']*'[^\n]*\n)+)",
                f"  schedule:\n{new_crons}\n",
                yml,
            )
        else:
            yml = re.sub(
                r"(  schedule:\n    - cron: ')[^']*('[^\n]*)",
                f"\\g<1>{cron}\\g<2>",
                yml,
            )
            # Also update the comment
            yml = re.sub(
                r"(    - cron: '[^']*')  #[^\n]*",
                f"\\g<1>  # {label}",
                yml,
            )

        with open(yml_path, "w") as f:
            f.write(yml)
        print(f"  Patched {yml_path}")

    print("Done — all schedules synced.")


def print_schedule():
    """Print a readable schedule summary to stdout."""
    print("\n=== Job Schedule (PT) ===\n")
    print(f"{'Job':<35} {'Platform':<10} {'Schedule'}")
    print("-" * 80)
    for job in JOBS:
        print(f"{job['label']:<35} {job['platform'].upper():<10} {pt_label(job)}")
    print()


if __name__ == "__main__":
    import sys
    if "--sync" in sys.argv:
        print("Syncing schedules to modal_jobs.py and GHA YAMLs...")
        sync_all()
    elif "--print" in sys.argv:
        print_schedule()
    else:
        print("Usage:")
        print("  python3 config/schedule.py --print   # show schedule in PT")
        print("  python3 config/schedule.py --sync    # write changes to modal_jobs.py + GHA YAMLs")
