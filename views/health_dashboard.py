import streamlit as st
from datetime import datetime
import pytz

PT = pytz.timezone("America/Los_Angeles")


def render():
    st.title("🔧 System Health Dashboard")
    st.caption(f"Checked at: {datetime.now(PT).strftime('%b %d, %Y  %I:%M %p PT')}")

    if st.button("🔄 Refresh"):
        st.rerun()

    # ── Live component status ─────────────────────────────────────────────────
    st.markdown("### Component Status")
    cols = st.columns(5)
    statuses = _check_all_components()

    labels = ["Claude API", "Supabase", "Finnhub", "yfinance", "GitHub Actions"]
    for i, (label, status) in enumerate(zip(labels, statuses)):
        with cols[i]:
            icon = "✅" if status["ok"] else "❌"
            st.metric(label, icon, status.get("detail", ""))

    st.markdown("---")

    # ── Scan history ──────────────────────────────────────────────────────────
    try:
        from database.db import get_scan_logs
        logs = get_scan_logs(limit=10)

        if logs:
            st.markdown("### API Traffic — Latest Scan")
            latest = logs[0]
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                st.metric("Universe", f"{latest.get('universe_total', 0)} stocks")
                st.caption(f"{latest.get('hot_stock_count', 0)} hot (Yahoo + AV) + {latest.get('nasdaq100_count', 0)} Nasdaq earnings → {latest.get('overlap_count', 0)} overlap")
            with c2:
                st.metric("Claude Calls", latest.get("claude_calls_made", 0))
                cost = latest.get("claude_cost_usd") or 0
                st.caption(f"${cost:.4f} cost")
            with c3:
                st.metric("yfinance Rows", f"{latest.get('yfinance_rows_fetched', 0):,}")
            with c4:
                st.metric("Predictions Created", latest.get("predictions_created", 0))

            st.markdown("### Scan History")
            import pandas as pd
            log_data = []
            for log in logs:
                ts = log.get("timestamp", "")
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    ts_str = dt.astimezone(PT).strftime("%b %d  %I:%M %p PT")
                except Exception:
                    ts_str = ts[:16]
                log_data.append({
                    "Time (PT)": ts_str,
                    "Universe": log.get("universe_total", "—"),
                    "Analyzed": log.get("stocks_analyzed", "—"),
                    "Predictions": log.get("predictions_created", "—"),
                    "Claude calls": log.get("claude_calls_made", "—"),
                    "Cost": f"${log.get('claude_cost_usd') or 0:.4f}",
                    "Errors": log.get("errors_encountered", 0),
                })
            st.dataframe(pd.DataFrame(log_data), use_container_width=True, hide_index=True)

            # All-time totals
            st.markdown("### All-Time Totals")
            all_logs = get_scan_logs(limit=1000)
            t1, t2, t3, t4 = st.columns(4)
            with t1:
                total_scans = len(all_logs)
                st.metric("Total Scans Run", total_scans)
            with t2:
                total_claude = sum(l.get("claude_calls_made") or 0 for l in all_logs)
                st.metric("Total Claude Calls", f"{total_claude:,}")
            with t3:
                total_cost = sum(l.get("claude_cost_usd") or 0 for l in all_logs)
                st.metric("Total Claude Cost", f"${total_cost:.2f}")
            with t4:
                total_predictions = sum(l.get("predictions_created") or 0 for l in all_logs)
                st.metric("Total Predictions", total_predictions)
        else:
            st.info("No scan history yet. Run the nightly scanner to populate this page.")
    except Exception as e:
        st.warning(f"Could not load scan history: {e}")

    # ── Manual actions ────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Manual Actions")
    col1, col2, col3 = st.columns(3)
    with col1:
        tg_clicked = st.button("📨 Test Telegram")
    with col2:
        scan_clicked = st.button("🚀 Run Scanner Now", type="primary")
    with col3:
        verify_clicked = st.button("✅ Run Verifier Now")

    if tg_clicked:
        _test_telegram()
    elif scan_clicked:
        _run_scanner()
    elif verify_clicked:
        _run_verifier()


    st.markdown("---")

    # ── Earnings Next 2 Weeks ─────────────────────────────────────────────────
    with st.expander("📅 Earnings Next 2 Weeks", expanded=False):
        try:
            from database.db import get_earnings_calendar_from_db
            rows = get_earnings_calendar_from_db()
            if rows:
                scanned_at = rows[0].get("scanned_at", "")
                try:
                    dt = datetime.fromisoformat(scanned_at.replace("Z", "+00:00"))
                    ts = dt.astimezone(PT).strftime("%b %d  %I:%M %p PT")
                except Exception:
                    ts = scanned_at[:10]
                st.caption(f"Stocks reporting earnings in the next 14 days · fetched {ts} · {len(rows)} tickers")

                from collections import defaultdict
                by_day = defaultdict(list)
                for row in rows:
                    by_day[row.get("days_to_earnings", 99)].append(row)

                for days in sorted(by_day.keys()):
                    tickers = by_day[days]
                    label = "Today" if days == 0 else "Tomorrow" if days == 1 else f"In {days} days"
                    date_str = tickers[0].get("earnings_date", "")
                    st.markdown(f"**{label}** · {date_str}")
                    badges = " ".join(
                        f'<span style="background:#78350f;color:#fde68a;padding:2px 8px;border-radius:12px;font-size:0.78rem;margin:2px">'
                        f'📅 {r["ticker"]}</span>'
                        for r in tickers
                    )
                    st.markdown(badges, unsafe_allow_html=True)
            else:
                st.info("No earnings data yet. Run the scanner to populate.")
        except Exception as e:
            st.warning(f"Could not load earnings calendar: {e}")

    st.markdown("---")
    st.markdown("---")
    _render_api_call_log()

    st.markdown("---")
    _render_error_logs()


def _render_api_call_log():
    st.markdown("### 📡 API Call Log")

    try:
        from database.db import get_api_call_log_dates, get_api_call_log
    except ImportError:
        st.warning("api_call_log not available yet — will populate after next scanner run.")
        return

    dates = get_api_call_log_dates()
    if not dates:
        st.info("No API call log yet — will populate after the next scanner run.")
        return

    selected_date = st.selectbox("Run date", dates, key="api_log_date")
    rows = get_api_call_log(selected_date)
    if not rows:
        st.info("No log entries for this date.")
        return

    from collections import defaultdict
    by_api = defaultdict(list)
    for r in rows:
        by_api[r["api"]].append(r)

    API_LABELS = {
        "finnhub_news":         "Finnhub — News Sentiment",
        "finnhub_social":       "Finnhub — Social Sentiment",
        "finnhub_analyst":      "Finnhub — Analyst Recommendations",
        "finnhub_earnings":     "Finnhub — Earnings History",
        "finnhub_price_target": "Finnhub — Price Target",
        "yfinance_info":        "yfinance — Ticker Info",
        "yfinance_fundamentals":"yfinance — Fundamentals",
        "sec_edgar":            "SEC EDGAR — Insider Buying",
    }

    for api_key in sorted(by_api.keys()):
        api_rows = by_api[api_key]
        total   = len(api_rows)
        success = sum(1 for r in api_rows if r["success"])
        failed  = total - success
        label   = API_LABELS.get(api_key, api_key)
        icon    = "✅" if failed == 0 else "⚠️" if failed < total * 0.2 else "❌"

        with st.expander(
            f"{icon} **{label}** — {success}/{total} succeeded · {failed} failed",
            expanded=(failed > 0),
        ):
            h1, h2, h3 = st.columns(3)
            h1.metric("Total", total)
            h2.metric("Succeeded", success)
            h3.metric("Failed", failed, delta=f"-{failed}" if failed else None,
                      delta_color="inverse" if failed > 0 else "off")

            failures = [r for r in api_rows if not r["success"]]
            if failures:
                st.markdown("**Failed tickers:**")
                import pandas as pd
                fail_df = pd.DataFrame([{
                    "Ticker": r["ticker"],
                    "Error":  r.get("error") or "—",
                } for r in failures])
                st.dataframe(fail_df, use_container_width=True, hide_index=True)
            else:
                st.success("All calls succeeded.")


def _test_telegram():
    status = st.status("Sending Telegram test message…", expanded=True)
    try:
        from services.telegram_service import send_test_message
        status.write("Calling send_test_message()…")
        ok = send_test_message()
        if ok:
            status.update(label="✅ Telegram message sent!", state="complete", expanded=False)
            st.success("Message sent!")
        else:
            status.update(label="❌ Telegram failed", state="error", expanded=True)
            st.error("Failed — check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in your environment.")
    except Exception as e:
        status.update(label="❌ Telegram error", state="error", expanded=True)
        st.error(f"Error: {e}")


def _run_scanner():
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    log_lines = []
    status = st.status("Running nightly scanner…", expanded=True)

    def _capture(msg):
        log_lines.append(msg)
        status.write(msg)

    try:
        import scripts.nightly_scanner as scanner
        import importlib
        importlib.reload(scanner)  # pick up any code changes

        # Monkey-patch print so progress shows in the UI
        import builtins
        _orig_print = builtins.print
        builtins.print = lambda *a, **k: (_capture(" ".join(str(x) for x in a)), _orig_print(*a, **k))

        try:
            stats = scanner.run(debug=False)
        finally:
            builtins.print = _orig_print

        status.update(label="✅ Scanner finished!", state="complete", expanded=False)
        preds = stats.get("predictions_created", 0)
        cost  = stats.get("claude_cost_usd", 0)
        errs  = stats.get("errors_encountered", 0)
        st.success(f"Done — **{preds} predictions** created  ·  ${cost:.4f} Claude cost  ·  {errs} errors")
        st.rerun()

    except Exception as e:
        status.update(label="❌ Scanner failed", state="error", expanded=True)
        st.error(f"Error: {e}")


def _run_verifier():
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    status = st.status("Running prediction verifier…", expanded=True)
    try:
        import scripts.prediction_verifier as verifier
        import importlib
        importlib.reload(verifier)

        import builtins
        _orig_print = builtins.print
        builtins.print = lambda *a, **k: (status.write(" ".join(str(x) for x in a)), _orig_print(*a, **k))

        try:
            verifier.run()
        finally:
            builtins.print = _orig_print

        status.update(label="✅ Verifier finished!", state="complete", expanded=False)
        st.success("Predictions verified and outcomes updated.")
        st.rerun()

    except Exception as e:
        status.update(label="❌ Verifier failed", state="error", expanded=True)
        st.error(f"Error: {e}")


def _render_error_logs():
    st.markdown("### 🪵 Error Logs (last 30 days)")

    try:
        from database.db import get_error_logs
    except ImportError:
        st.warning("get_error_logs not available — deploy latest db.py")
        return

    # Filters
    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        src_filter = st.selectbox("Source", ["All", "scanner", "verifier", "telegram", "app"], key="log_src")
    with fc2:
        lvl_filter = st.selectbox("Level", ["All", "ERROR", "WARNING", "INFO"], key="log_lvl")
    with fc3:
        days_filter = st.selectbox("Period", [7, 14, 30], index=2, format_func=lambda x: f"Last {x} days", key="log_days")

    try:
        logs = get_error_logs(
            days=days_filter,
            source=None if src_filter == "All" else src_filter,
            level=None if lvl_filter == "All" else lvl_filter,
        )
    except Exception as e:
        st.warning(f"Could not load logs — run this SQL in Supabase first:\n\n"
                   f"```sql\nCREATE TABLE IF NOT EXISTS error_logs (\n"
                   f"  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),\n"
                   f"  occurred_at timestamptz NOT NULL DEFAULT now(),\n"
                   f"  source text NOT NULL,\n"
                   f"  level text NOT NULL DEFAULT 'ERROR',\n"
                   f"  ticker text,\n"
                   f"  message text NOT NULL,\n"
                   f"  detail text,\n"
                   f"  created_at timestamptz DEFAULT now()\n"
                   f");\n"
                   f"CREATE INDEX IF NOT EXISTS error_logs_occurred_at ON error_logs (occurred_at DESC);\n```")
        return

    if not logs:
        st.success("No log entries found for the selected filters.")
        return

    # Summary counts
    errors   = sum(1 for l in logs if l.get("level") == "ERROR")
    warnings = sum(1 for l in logs if l.get("level") == "WARNING")
    infos    = sum(1 for l in logs if l.get("level") == "INFO")
    sc1, sc2, sc3, sc4 = st.columns(4)
    sc1.metric("Total entries", len(logs))
    sc2.metric("Errors",   errors,   delta=None if errors == 0 else f"{errors} need attention",
               delta_color="inverse" if errors > 0 else "off")
    sc3.metric("Warnings", warnings)
    sc4.metric("Info",     infos)

    st.markdown("")

    LEVEL_STYLE = {
        "ERROR":   ("background:#fee2e2;color:#7f1d1d", "❌"),
        "WARNING": ("background:#fef9c3;color:#713f12", "⚠️"),
        "INFO":    ("background:#f0fdf4;color:#14532d", "ℹ️"),
    }

    for log in logs:
        level   = log.get("level", "INFO")
        source  = log.get("source", "—")
        ticker  = log.get("ticker") or ""
        message = log.get("message", "")
        detail  = log.get("detail") or ""
        try:
            dt = datetime.fromisoformat(log.get("occurred_at", "").replace("Z", "+00:00"))
            ts = dt.astimezone(PT).strftime("%b %d  %I:%M %p PT")
        except Exception:
            ts = log.get("occurred_at", "—")[:16]

        style, icon = LEVEL_STYLE.get(level, LEVEL_STYLE["INFO"])
        ticker_tag = f"<span style='background:#e0e7ff;color:#3730a3;border-radius:4px;padding:1px 6px;font-size:11px;margin-left:4px'>{ticker}</span>" if ticker else ""

        with st.expander(
            f"{icon} [{source}] {message[:80]}{'…' if len(message) > 80 else ''}  —  {ts}",
            expanded=False,
        ):
            st.markdown(
                f"<div style='{style};border-radius:8px;padding:10px 14px;font-size:13px'>"
                f"<strong>{icon} {level}</strong> · <code>{source}</code>{ticker_tag}<br>"
                f"<span style='color:#374151'>{message}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
            if detail:
                st.code(detail, language=None)
            st.caption(f"Logged at: {ts}")


def _check_all_components() -> list[dict]:
    results = []

    # Claude API
    try:
        import anthropic, os
        c = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        c.messages.create(model="claude-haiku-4-5", max_tokens=5, messages=[{"role": "user", "content": "hi"}])
        results.append({"ok": True, "detail": "Online"})
    except Exception as e:
        results.append({"ok": False, "detail": str(e)[:30]})

    # Supabase
    try:
        from database.db import get_scan_logs
        get_scan_logs(limit=1)
        results.append({"ok": True, "detail": "Connected"})
    except Exception as e:
        results.append({"ok": False, "detail": str(e)[:30]})

    # Finnhub
    try:
        import finnhub, os
        c = finnhub.Client(api_key=os.environ.get("FINNHUB_API_KEY", ""))
        c.quote("AAPL")
        results.append({"ok": True, "detail": "Online"})
    except Exception as e:
        results.append({"ok": False, "detail": str(e)[:30]})

    # yfinance
    try:
        import yfinance as yf
        t = yf.Ticker("AAPL")
        _ = t.fast_info.last_price
        results.append({"ok": True, "detail": "Online"})
    except Exception as e:
        results.append({"ok": False, "detail": str(e)[:30]})

    # GitHub Actions — can't check from app, just show last scan time
    try:
        from database.db import get_scan_logs
        logs = get_scan_logs(limit=1)
        if logs:
            ts = logs[0].get("timestamp", "")
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            hours_ago = (datetime.now(pytz.utc) - dt.replace(tzinfo=pytz.utc)).total_seconds() / 3600
            ok = hours_ago < 26
            results.append({"ok": ok, "detail": f"{hours_ago:.0f}h ago"})
        else:
            results.append({"ok": True, "detail": "No data yet"})
    except Exception:
        results.append({"ok": True, "detail": "Unknown"})

    return results
