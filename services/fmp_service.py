"""
FMP (Financial Modeling Prep) fundamentals service.

Migrated from legacy api/v3 (deprecated Aug 2025, now 403) to stable/ endpoints.

Free tier: 250 calls/day
Usage pattern:
  - Wednesday cron pre-fetches first 50 Nasdaq tickers × 4 calls = 200 calls
  - Thursday cron pre-fetches last 50 Nasdaq tickers × 4 calls = 200 calls
  - Friday scanner uses cache for Nasdaq 100; FMP only for cache-miss dynamic tickers
  - TTL: 72h (Wednesday cache valid through Friday scan ~46h gap)

Endpoints used per ticker (4 calls — unchanged from v3):
  1. stable/key-metrics-ttm?symbol=   — PE, PEG, P/B, ROIC, EV/EBITDA, net debt ratio, FCF yield
  2. stable/income-statement?symbol=  — revenue/earnings growth (3 years), shares outstanding (buyback trend)
  3. stable/ratios-ttm?symbol=        — forward PE proxy, profit margins TTM
  4. stable/analyst-estimates?symbol= — EPS revision trend (are estimates rising or falling?)
"""
import os
import time
import requests
from datetime import datetime, date, timezone

FMP_BASE = "https://financialmodelingprep.com/stable"
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
        curr = estimates[0].get("epsAvg") or estimates[0].get("epsHigh")
        prev = estimates[1].get("epsAvg") or estimates[1].get("epsHigh")
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


def _derive_share_buyback_trend(income: list) -> str | None:
    """
    Detects whether shares outstanding are shrinking (buyback) or growing (dilution).
    Uses weightedAverageShsOut from 3 years of income-statement — no extra API call.
    Returns: "BUYBACK" | "DILUTING" | "STABLE" | None
    """
    if not income or len(income) < 2:
        return None
    try:
        shares = [yr.get("weightedAverageShsOut") for yr in income]
        shares = [s for s in shares if s and s > 0]
        if len(shares) < 2:
            return None
        # Compare most recent vs oldest available
        pct_change = (shares[0] - shares[-1]) / shares[-1] * 100
        if pct_change <= -2:
            return "BUYBACK"    # shrinking ≥2% — management returning capital
        elif pct_change >= 3:
            return "DILUTING"   # growing ≥3% — options/secondaries headwind
        return "STABLE"
    except Exception:
        return None


def get_fundamentals(ticker: str) -> dict:
    """
    Fetches fundamentals from FMP for one ticker.
    Returns dict matching the schema expected by scorers and Claude prompts.
    Uses 4 API calls per ticker (stable endpoints).
    Falls back gracefully — any field that FMP can't provide remains None.
    """
    result = {
        "ticker":                    ticker,
        "revenue_growth_pct":        None,
        "earnings_growth_pct":       None,
        "operating_margin_pct":      None,
        "gross_margin_pct":          None,
        "profit_margin_pct":         None,
        "free_cashflow":             None,
        "trailing_pe":               None,
        "forward_pe":                None,
        "peg_ratio":                 None,
        "price_to_book":             None,
        "analyst_mean_target":       None,
        "analyst_upside_pct":        None,
        "analyst_count":             None,
        "eps_revision_trend":        None,   # "RISING" | "FALLING" | "STABLE" | None
        "revenue_declining_years":   None,   # int: consecutive years of decline
        "gross_margin_prev_pct":     None,   # prior year gross margin for compression check
        "operating_margin_prev_pct": None,   # prior year op margin for compression check
        "profit_margin_prev_pct":    None,   # prior year net margin for trend detection
        "debt_to_equity":            None,   # D/E ratio
        "price_to_sales":            None,   # P/S TTM — valuation for unprofitable growth names
        "revenue_growth_pct_prev":   None,   # yr1→yr2 revenue growth (for deceleration signal)
        "revenue_growth_decel":      None,   # yr0_growth minus yr1_growth — positive = decelerating
        # New signals (zero extra calls)
        "roic":                      None,   # Return on Invested Capital TTM — moat quality signal
        "ev_to_ebitda":              None,   # EV/EBITDA TTM — cross-sector valuation
        "net_debt_to_ebitda":        None,   # Net debt / EBITDA — negative = net cash
        "fcf_yield":                 None,   # FCF yield TTM — cash return to market cap
        "share_buyback_trend":       None,   # "BUYBACK" | "DILUTING" | "STABLE" | None
        "fetched_at":                datetime.now(timezone.utc).isoformat(),
        "source":                    "fmp",
    }

    # ── Call 1: key-metrics-ttm — ROIC, EV/EBITDA, net debt, FCF ───────────────
    # Note: PE, PEG, D/E, P/S live in ratios-ttm on the stable API (extracted in Call 3)
    time.sleep(_REQUEST_DELAY)
    metrics = _get("/key-metrics-ttm", {"symbol": ticker})
    if metrics and isinstance(metrics, list) and metrics:
        m = metrics[0]

        roic  = m.get("returnOnInvestedCapitalTTM")
        ev_eb = m.get("evToEBITDATTM") or m.get("enterpriseValueOverEBITDATTM")
        nd_eb = m.get("netDebtToEBITDATTM")
        fcfy  = m.get("freeCashFlowYieldTTM")
        fcf   = m.get("freeCashFlowToFirmTTM") or m.get("freeCashFlowToEquityTTM")

        result["roic"]               = round(float(roic) * 100, 1) if roic is not None else None
        result["ev_to_ebitda"]       = round(float(ev_eb), 1) if ev_eb and float(ev_eb) > 0 else None
        result["net_debt_to_ebitda"] = round(float(nd_eb), 2) if nd_eb is not None else None
        result["fcf_yield"]          = round(float(fcfy) * 100, 2) if fcfy is not None else None
        result["free_cashflow"]      = int(fcf) if fcf else None

    # ── Call 2: income-statement — 3 years for growth + buyback trend ──────────
    time.sleep(_REQUEST_DELAY)
    income = _get("/income-statement", {"symbol": ticker, "limit": 3, "period": "annual"})
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
            prev_rev  = prev.get("revenue", 0) or 0
            prev_earn = prev.get("netIncome", 0) or 0
            prev_op   = prev.get("operatingIncome", 0) or 0
            prev_gp   = prev.get("grossProfit", 0) or 0

            if prev_rev > 0 and curr_rev > 0:
                result["revenue_growth_pct"] = round((curr_rev - prev_rev) / abs(prev_rev) * 100, 1)
            if prev_earn != 0 and curr_earn is not None:
                result["earnings_growth_pct"] = round((curr_earn - prev_earn) / abs(prev_earn) * 100, 1)

            if prev_rev > 0:
                result["gross_margin_prev_pct"]     = round(prev_gp / prev_rev * 100, 1)
                result["operating_margin_prev_pct"] = round(prev_op / prev_rev * 100, 1)
                result["profit_margin_prev_pct"]    = round(prev_earn / prev_rev * 100, 1)

            if yr2 is not None:
                yr2_rev = yr2.get("revenue", 0) or 0
                if yr2_rev > 0 and prev_rev > 0:
                    prev_growth = (prev_rev - yr2_rev) / abs(yr2_rev) * 100
                    result["revenue_growth_pct_prev"] = round(prev_growth, 1)
                    if result["revenue_growth_pct"] is not None:
                        result["revenue_growth_decel"] = round(prev_growth - result["revenue_growth_pct"], 1)

        result["revenue_declining_years"] = _derive_revenue_declining_years(income)
        result["share_buyback_trend"]     = _derive_share_buyback_trend(income)

    # ── Call 3: ratios-ttm — PE, PEG, P/B, D/E, P/S, forward PE, margins ───────
    # In stable API all valuation ratios live here (not in key-metrics-ttm)
    time.sleep(_REQUEST_DELAY)
    ratios = _get("/ratios-ttm", {"symbol": ticker})
    if ratios and isinstance(ratios, list) and ratios:
        r = ratios[0]

        pe_ttm = r.get("priceToEarningsRatioTTM")
        peg    = r.get("priceToEarningsGrowthRatioTTM")
        fwd_peg = r.get("forwardPriceToEarningsGrowthRatioTTM")  # forward PEG (not forward PE)
        pb     = r.get("priceToBookRatioTTM")
        dte    = r.get("debtToEquityRatioTTM")
        ps     = r.get("priceToSalesRatioTTM")

        result["trailing_pe"]    = round(float(pe_ttm), 1) if pe_ttm and float(pe_ttm) > 0 else None
        result["forward_pe"]     = result["trailing_pe"]  # stable API has no separate fwd PE; use TTM as proxy
        result["peg_ratio"]      = round(float(peg), 2) if peg and float(peg) > 0 else None
        result["price_to_book"]  = round(float(pb), 2) if pb else None
        result["debt_to_equity"] = round(float(dte), 2) if dte else None
        result["price_to_sales"] = round(float(ps), 1) if ps and float(ps) > 0 else None

        # Profit margin TTM from ratios (confirmation / fallback for income-statement calc)
        if result["profit_margin_pct"] is None:
            npm = r.get("netProfitMarginTTM") or r.get("continuousOperationsProfitMarginTTM")
            if npm is not None:
                result["profit_margin_pct"] = round(float(npm) * 100, 1)

    # Fallback: use trailing PE as forward PE proxy if still None
    if result["forward_pe"] is None and result["trailing_pe"] is not None:
        result["forward_pe"] = result["trailing_pe"]

    # ── Call 4: analyst-estimates — EPS revision trend ────────────────────────
    time.sleep(_REQUEST_DELAY)
    estimates = _get("/analyst-estimates", {"symbol": ticker, "limit": 4, "period": "annual"})
    if estimates and isinstance(estimates, list):
        result["eps_revision_trend"] = _derive_eps_revision_trend(estimates)

    return result


def get_sector_pe() -> dict[str, float]:
    """
    Fetches average P/E ratio for all sectors from FMP stable sector-pe-snapshot.
    Uses 1 API call total (returns all sectors in one response) — cached weekly.
    Returns: {"Technology": 28.5, "Healthcare": 22.1, ...}
    """
    try:
        from database.db import get_cache, set_cache
        cached = get_cache("sector_pe_ratios")
        if cached:
            return cached
    except Exception:
        set_cache = lambda *a, **kw: None

    today = date.today().isoformat()
    result = {}
    data = _get("/sector-pe-snapshot", {"date": today, "exchange": "NASDAQ"})
    if data and isinstance(data, list):
        for row in data:
            sector = row.get("sector")
            pe     = row.get("pe")
            if sector and pe:
                try:
                    val = float(pe)
                    if val > 0:
                        result[sector] = round(val, 1)
                except Exception:
                    pass

    if result:
        try:
            set_cache("sector_pe_ratios", result, ttl_hours=168)  # 1-week TTL
        except Exception:
            pass
        print(f"  Sector PE fetched for {len(result)} sectors")
    else:
        print("  WARNING: sector PE snapshot returned empty — sector comparison scoring disabled this run")
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
