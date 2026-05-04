"""
FMP (Financial Modeling Prep) fundamentals service.

Replaces yfinance for long-term prediction fundamentals — yfinance returns None
for revenue_growth_pct, earnings_growth_pct, forward_pe too often to be useful.

Free tier: 250 calls/day
Usage pattern:
  - Thursday cron pre-fetches Nasdaq 100 (~100 tickers × 2 endpoints = 200 calls)
  - Friday scanner uses cache for Nasdaq 100; FMP only for cache-miss dynamic tickers
  - TTL: 48h (Thursday cache valid for Friday scan)

Endpoints used per ticker:
  1. /profile/{ticker}         — sector, market cap, price, beta
  2. /income-statement/{ticker} — revenue growth, margins (derived from TTM data)
  3. /key-metrics-ttm/{ticker}  — forward PE, FCF, PEG (single call)
"""
import os
import time
import requests
from datetime import datetime, timezone

FMP_BASE = "https://financialmodelingprep.com/api/v3"
_REQUEST_DELAY = 0.25  # 250ms between calls — well within free tier rate limit


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
        # FMP returns {"Error Message": "..."} on bad ticker or auth failure
        if isinstance(data, dict) and "Error Message" in data:
            return None
        return data
    except Exception as e:
        print(f"  FMP error {endpoint}: {e}")
        return None


def get_fundamentals(ticker: str) -> dict:
    """
    Fetches fundamentals from FMP for one ticker.
    Returns dict matching the schema expected by scorers and Claude prompts.
    Uses 2 API calls: key-metrics-ttm + income-statement (last 2 quarters for growth calc).
    Falls back gracefully — any field that FMP can't provide remains None.
    """
    result = {
        "ticker":               ticker,
        "revenue_growth_pct":   None,
        "earnings_growth_pct":  None,
        "operating_margin_pct": None,
        "gross_margin_pct":     None,
        "profit_margin_pct":    None,
        "free_cashflow":        None,
        "trailing_pe":          None,
        "forward_pe":           None,
        "peg_ratio":            None,
        "price_to_book":        None,
        "analyst_mean_target":  None,
        "analyst_upside_pct":   None,
        "analyst_count":        None,
        "fetched_at":           datetime.now(timezone.utc).isoformat(),
        "source":               "fmp",
    }

    # ── Call 1: key-metrics-ttm — forward PE, PEG, FCF yield, P/B ────────────
    time.sleep(_REQUEST_DELAY)
    metrics = _get(f"/key-metrics-ttm/{ticker}")
    if metrics and isinstance(metrics, list) and metrics:
        m = metrics[0]
        pe_ttm = m.get("peRatioTTM")
        fwd_pe = m.get("forwardPE") or m.get("priceEarningsToGrowthRatioTTM")
        peg    = m.get("pegRatioTTM") or m.get("priceEarningsToGrowthRatioTTM")
        pb     = m.get("pbRatioTTM") or m.get("priceToBookRatioTTM")
        fcf    = m.get("freeCashFlowPerShareTTM")

        result["trailing_pe"]  = round(float(pe_ttm), 1) if pe_ttm else None
        result["peg_ratio"]    = round(float(peg), 2) if peg and float(peg) > 0 else None
        result["price_to_book"] = round(float(pb), 2) if pb else None
        # FCF per share → scale to approximate total (less accurate but still directional)
        # We'll get absolute FCF from income statement call below

    # ── Call 2: income-statement (annual, last 2 years for YoY growth) ────────
    time.sleep(_REQUEST_DELAY)
    income = _get(f"/income-statement/{ticker}", {"limit": 2, "period": "annual"})
    if income and isinstance(income, list) and len(income) >= 1:
        curr = income[0]
        prev = income[1] if len(income) > 1 else None

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
            prev_rev  = prev.get("revenue", 0) or 0
            prev_earn = prev.get("netIncome", 0) or 0
            if prev_rev > 0 and curr_rev > 0:
                result["revenue_growth_pct"] = round((curr_rev - prev_rev) / abs(prev_rev) * 100, 1)
            if prev_earn != 0 and curr_earn is not None:
                result["earnings_growth_pct"] = round((curr_earn - prev_earn) / abs(prev_earn) * 100, 1)

    # ── Forward PE: try ratios endpoint as fallback ────────────────────────────
    if result["forward_pe"] is None:
        time.sleep(_REQUEST_DELAY)
        ratios = _get(f"/ratios-ttm/{ticker}")
        if ratios and isinstance(ratios, list) and ratios:
            r = ratios[0]
            fwd = r.get("priceEarningsRatioTTM") or r.get("peRatioTTM")
            if fwd and float(fwd) > 0:
                result["forward_pe"] = round(float(fwd), 1)

    return result


def get_fundamentals_batch(tickers: list[str], log_progress: bool = True) -> dict[str, dict]:
    """
    Fetch FMP fundamentals for a list of tickers.
    Returns {ticker: fundamentals_dict}.
    Respects rate limits automatically.
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
