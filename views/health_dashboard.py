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
                st.caption(f"{latest.get('nasdaq100_count', 0)} Nasdaq + {latest.get('hot_stock_count', 0)} hot → {latest.get('overlap_count', 0)} overlap")
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
        if st.button("📨 Test Telegram"):
            try:
                from services.telegram_service import send_test_message
                ok = send_test_message()
                st.success("Message sent!") if ok else st.error("Failed — check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
            except Exception as e:
                st.error(f"Error: {e}")
    with col2:
        if st.button("🚀 Run Scanner Now", type="primary"):
            _run_scanner()
    with col3:
        if st.button("✅ Run Verifier Now"):
            _run_verifier()


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
            stats = scanner.run()
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
