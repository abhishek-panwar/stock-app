"""
FMP (Financial Modeling Prep) fundamentals service.

Replaces yfinance for long-term prediction fundamentals — yfinance returns None
for revenue_growth_pct, earnings_growth_pct, forward_pe too often to be useful.

Free tier: 250 calls/day
Usage pattern:
  - Wednesday cron pre-fetches first 50 Nasdaq tickers × 4 calls = 200 calls
  - Thursday cron pre-fetches last 50 Nasdaq tickers × 4 calls = 200 calls
  - Friday scanner uses cache for Nasdaq 100; FMP only for cache-miss dynamic tickers
  - TTL: 72h (Wednesday cache valid through Friday scan ~46h gap)

Endpoints used per ticker (4 calls):
  1. /key-metrics-ttm/{ticker}      — PE, PEG, P/B
  2. /income-statement/{ticker}     — revenue/earnings growth (3 years for trend)
  3. /ratios/{ticker}               — forward PE
  4. /analyst-estimates/{ticker}    — EPS revision trend (are estimates rising or falling?)
"""
import os
import time
import requests
from datetime import datetime, timezone

FMP_BASE = "https://financialmodelingprep.com/api/v3"
_REQUEST_DELAY = 0.25  # 250ms between calls — well within rate limit


def _get(endpoint: str, params: dict = None) -> dict | list | None:
    api_key = os.environ.get("FMP_API_KEY", "")
    if not api_key:
        return None
    try:
        p = {"apikey": api_key}
        if params:
            p.update(params)
        r = requests.get(f"{FMP_BASE}{endpoint}", params=p, timeout=15)
        if r.status_code == 429:
            print(f"  FMP rate limit hit — sleeping 60s")
            time.sleep(60)
            r = requests.get(f"{FMP_BASE}{endpoint}", params=p, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        if isinstance(data, dict) and "Error Message" in data:
            return None
        return data
    except Exception as e:
        print(f"  FMP error {endpoint}: {e}")
        return None


def _derive_eps_revision_trend(estimates: list) -> str | None:
    """
    Derives EPS revision direction from FMP analyst-estimates response.
    Compares the most recent EPS consensus estimate against the prior period.
    Returns: "RISING" | "FALLING" | "STABLE" | None
    """
    if not estimates or len(estimates) < 2:
        return None
    try:
        curr = estimates[0].get("estimatedEpsAvg") or estimates[0].get("estimatedEpsHigh")
        prev = estimates[1].get("estimatedEpsAvg") or estimates[1].get("estimatedEpsHigh")
        if curr is None or prev is None or prev == 0:
            return None
        change_pct = (float(curr) - float(prev)) / abs(float(prev)) * 100
        if change_pct >= 3:
            return "RISING"
        elif change_pct <= -3:
            return "FALLING"
        return "STABLE"
    except Exception:
        return None


def _derive_revenue_declining_years(income: list) -> int:
    """
    Returns how many consecutive years revenue has declined (most recent first).
    Used to detect secular decline for narrative_risk.
    """
    if not income or len(income) < 2:
        return 0
    try:
        declining = 0
        for i in range(len(income) - 1):
            curr_rev = income[i].get("revenue", 0) or 0
            prev_rev = income[i + 1].get("revenue", 0) or 0
            if prev_rev > 0 and curr_rev < prev_rev:
                declining += 1
            else:
                break
        return declining
    except Exception:
        return 0


def get_fundamentals(ticker: str) -> dict:
    """
    Fetches fundamentals from FMP for one ticker.
    Returns dict matching the schema expected by scorers and Claude prompts.
    Uses 4 API calls per ticker.
    Falls back gracefully — any field that FMP can't provide remains None.
    """
    result = {
        "ticker":                  ticker,
        "revenue_growth_pct":      None,
        "earnings_growth_pct":     None,
        "operating_margin_pct":    None,
        "gross_margin_pct":        None,
        "profit_margin_pct":       None,
        "free_cashflow":           None,
        "trailing_pe":             None,
        "forward_pe":              None,
        "peg_ratio":               None,
        "price_to_book":           None,
        "analyst_mean_target":     None,
        "analyst_upside_pct":      None,
        "analyst_count":           None,
        # New fields for narrative risk + EPS revision
        "eps_revision_trend":      None,   # "RISING" | "FALLING" | "STABLE" | None
        "revenue_declining_years": None,   # int: how many consecutive years of decline
        "gross_margin_prev_pct":   None,   # prior year gross margin for compression check
        "operating_margin_prev_pct": None, # prior year op margin for compression check
        "fetched_at":              datetime.now(timezone.utc).isoformat(),
        "source":                  "fmp",
    }

    # ── Call 1: key-metrics-ttm — PE, PEG, P/B ───────────────────────────────
    time.sleep(_REQUEST_DELAY)
    metrics = _get(f"/key-metrics-ttm/{ticker}")
    if metrics and isinstance(metrics, list) and metrics:
        m = metrics[0]
        pe_ttm = m.get("peRatioTTM") or m.get("priceEarningsRatioTTM")
        peg    = m.get("pegRatioTTM")
        pb     = m.get("pbRatioTTM") or m.get("priceToBookRatioTTM")

        result["trailing_pe"]   = round(float(pe_ttm), 1) if pe_ttm else None
        result["peg_ratio"]     = round(float(peg), 2) if peg and float(peg) > 0 else None
        result["price_to_book"] = round(float(pb), 2) if pb else None

    # ── Call 2: income-statement — 3 years for growth + narrative risk trend ──
    time.sleep(_REQUEST_DELAY)
    income = _get(f"/income-statement/{ticker}", {"limit": 3, "period": "annual"})
    if income and isinstance(income, list) and len(income) >= 1:
        curr = income[0]
        prev = income[1] if len(income) > 1 else None
        yr2  = income[2] if len(income) > 2 else None

        curr_rev  = curr.get("revenue", 0) or 0
        curr_earn = curr.get("netIncome", 0) or 0
        curr_op   = curr.get("operatingIncome", 0) or 0
        curr_gp   = curr.get("grossProfit", 0) or 0
        curr_fcf  = curr.get("freeCashFlow") or curr.get("operatingCashFlow", 0)

        result["free_cashflow"] = int(curr_fcf) if curr_fcf else None

        if curr_rev > 0:
            result["operating_margin_pct"] = round(curr_op / curr_rev * 100, 1)
            result["gross_margin_pct"]     = round(curr_gp / curr_rev * 100, 1)
            result["profit_margin_pct"]    = round(curr_earn / curr_rev * 100, 1)

        if prev:
            prev_rev   = prev.get("revenue", 0) or 0
            prev_earn  = prev.get("netIncome", 0) or 0
            prev_op    = prev.get("operatingIncome", 0) or 0
            prev_gp    = prev.get("grossProfit", 0) or 0

            if prev_rev > 0 and curr_rev > 0:
                result["revenue_growth_pct"] = round((curr_rev - prev_rev) / abs(prev_rev) * 100, 1)
            if prev_earn != 0 and curr_earn is not None:
                result["earnings_growth_pct"] = round((curr_earn - prev_earn) / abs(prev_earn) * 100, 1)

            # Prior year margins for compression detection
            if prev_rev > 0:
                result["gross_margin_prev_pct"]     = round(prev_gp / prev_rev * 100, 1)
                result["operating_margin_prev_pct"] = round(prev_op / prev_rev * 100, 1)

        # Consecutive years of revenue decline (secular decline signal)
        result["revenue_declining_years"] = _derive_revenue_declining_years(income)

    # ── Call 3: ratios — forward PE ───────────────────────────────────────────
    time.sleep(_REQUEST_DELAY)
    ratios = _get(f"/ratios/{ticker}", {"limit": 1})
    if ratios and isinstance(ratios, list) and ratios:
        r = ratios[0]
        fwd = r.get("priceEarningsRatio")
        if fwd and float(fwd) > 0:
            result["forward_pe"] = round(float(fwd), 1)
    # Fallback: use trailing PE as forward PE proxy if still None
    if result["forward_pe"] is None and result["trailing_pe"] is not None:
        result["forward_pe"] = result["trailing_pe"]

    # ── Call 4: analyst-estimates — EPS revision trend ────────────────────────
    time.sleep(_REQUEST_DELAY)
    estimates = _get(f"/analyst-estimates/{ticker}", {"limit": 4, "period": "annual"})
    if estimates and isinstance(estimates, list):
        result["eps_revision_trend"] = _derive_eps_revision_trend(estimates)

    return result


def get_fundamentals_batch(tickers: list[str], log_progress: bool = True) -> dict[str, dict]:
    """
    Fetch FMP fundamentals for a list of tickers.
    Returns {ticker: fundamentals_dict}.
    """
    results = {}
    total = len(tickers)
    for i, ticker in enumerate(tickers):
        try:
            data = get_fundamentals(ticker)
            if data:
                results[ticker] = data
        except Exception as e:
            print(f"  FMP batch error {ticker}: {e}")
        if log_progress and (i + 1) % 10 == 0:
            print(f"  FMP progress: {i+1}/{total}")
    return results
