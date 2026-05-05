"""
SEC EDGAR insider buying signal (Phase 2).
Uses the free public EDGAR API — no key required.
SEC requires a descriptive User-Agent header.
"""
import requests
import threading
from datetime import datetime, timedelta

_USER_AGENT = "stock-app-abhi research@example.com"
_HEADERS = {"User-Agent": _USER_AGENT}
_SESSION = None

CIK_MAP_TTL_H       = 720      # 30 days — SEC CIK map rarely changes
INSIDER_BUYING_TTL_H    = 24
INSIDER_LOOKBACK_DAYS   = 14
INSIDER_MIN_BUY_USD     = 10_000   # ignore token/trivial purchases
INSIDER_STRONG_USD      = 500_000  # threshold for STRONG signal
INSIDER_STRONG_COUNT    = 3        # or this many distinct insiders
INSIDER_MODERATE_USD    = 100_000  # threshold for MODERATE signal

# In-memory CIK cache to avoid repeated bulk downloads per scanner run
_cik_cache: dict[str, str] = {}
_cik_cache_loaded = False
_cik_load_lock = threading.Lock()


def _session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        _SESSION.headers.update(_HEADERS)
    return _SESSION


def _load_cik_map() -> None:
    """
    Load ticker→CIK mapping. Checks Supabase cache first (30-day TTL),
    falls back to downloading from SEC if not cached.
    """
    global _cik_cache_loaded
    if _cik_cache_loaded:
        return
    with _cik_load_lock:
        if _cik_cache_loaded:  # re-check after acquiring lock — another thread may have loaded it
            return
        try:
            from database.db import get_cache, set_cache
            cached = get_cache("sec_cik_map")
            if cached:
                _cik_cache.update(cached)
                _cik_cache_loaded = True
                print(f"  SEC CIK map: loaded from cache ({len(_cik_cache)} tickers)")
                return
        except Exception:
            pass

        try:
            resp = _session().get(
                "https://www.sec.gov/files/company_tickers.json", timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
            for entry in data.values():
                ticker = entry.get("ticker", "").upper()
                cik = str(entry.get("cik_str", "")).zfill(10)
                if ticker:
                    _cik_cache[ticker] = cik
            _cik_cache_loaded = True
            try:
                from database.db import set_cache
                set_cache("sec_cik_map", dict(_cik_cache), ttl_hours=CIK_MAP_TTL_H)
                print(f"  SEC CIK map: downloaded and cached ({len(_cik_cache)} tickers)")
            except Exception:
                pass
        except Exception:
            pass


def _ticker_to_cik(ticker: str) -> str | None:
    _load_cik_map()
    # Strip exchange suffix for yfinance tickers like BRK-B
    clean = ticker.replace("-", ".").split(".")[0].upper()
    return _cik_cache.get(clean) or _cik_cache.get(ticker.upper())


def get_insider_buying(ticker: str, days_back: int = INSIDER_LOOKBACK_DAYS, run_date: str = "", log_api: bool = False) -> dict:
    """
    Checks SEC EDGAR Form 4 filings for insider purchases in the last days_back days.

    Returns:
      {
        "has_insider_buying": bool,
        "total_purchased_usd": float,   # sum of all insider buys
        "num_insiders":        int,     # distinct insiders who bought
        "largest_buy_usd":     float,   # single largest purchase
        "latest_filing_date":  str,     # most recent Form 4 date
        "signal_strength":     str,     # "STRONG" / "MODERATE" / "NONE"
      }
    """
    from database.db import get_cache, set_cache
    cache_key = f"insider_buying_{ticker}"
    cached = get_cache(cache_key)
    if cached is not None:
        return cached

    empty = {
        "has_insider_buying": False,
        "total_purchased_usd": 0.0,
        "num_insiders": 0,
        "largest_buy_usd": 0.0,
        "latest_filing_date": None,
        "signal_strength": "NONE",
    }

    cik = _ticker_to_cik(ticker)
    if not cik:
        set_cache(cache_key, empty, ttl_hours=INSIDER_BUYING_TTL_H)
        return empty

    try:
        resp = _session().get(
            f"https://data.sec.gov/submissions/CIK{cik}.json", timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        if log_api and run_date:
            from database.db import log_api_call
            log_api_call(run_date, "sec_edgar", ticker, False, str(e))
        return empty

    recent = data.get("filings", {}).get("recent", {})
    forms       = recent.get("form", [])
    dates       = recent.get("filingDate", [])
    accessions  = recent.get("accessionNumber", [])

    cutoff = (datetime.utcnow() - timedelta(days=days_back)).date()

    # Find Form 4 filings within the window
    form4_filings = []
    for i, form in enumerate(forms):
        if form != "4":
            continue
        try:
            filing_date = datetime.strptime(dates[i], "%Y-%m-%d").date()
        except Exception:
            continue
        if filing_date >= cutoff:
            form4_filings.append({"date": dates[i], "accession": accessions[i]})

    if not form4_filings:
        set_cache(cache_key, empty, ttl_hours=INSIDER_BUYING_TTL_H)
        return empty

    # Parse each Form 4 XML to extract buy and sell transactions
    total_buy_usd = 0.0
    total_sell_usd = 0.0
    buy_insiders = set()
    sell_insiders = set()
    largest = 0.0
    latest_date = None

    for filing in form4_filings[:10]:  # cap at 10 to avoid rate limits
        acc = filing["accession"].replace("-", "")
        xml_url = (
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{acc}/{filing['accession']}.xml"
        )
        try:
            r = _session().get(xml_url, timeout=10)
            if r.status_code != 200:
                continue
            buy_usd, sell_usd = _parse_form4_transactions(r.text)
            if buy_usd >= INSIDER_MIN_BUY_USD:
                total_buy_usd += buy_usd
                largest = max(largest, buy_usd)
                buy_insiders.add(filing["accession"])
                if not latest_date or filing["date"] > latest_date:
                    latest_date = filing["date"]
            if sell_usd >= INSIDER_MIN_BUY_USD:
                total_sell_usd += sell_usd
                sell_insiders.add(filing["accession"])
        except Exception:
            continue

    if total_buy_usd == 0 and total_sell_usd == 0:
        set_cache(cache_key, empty, ttl_hours=INSIDER_BUYING_TTL_H)
        return empty

    # Buy signal strength
    if total_buy_usd >= INSIDER_STRONG_USD or len(buy_insiders) >= INSIDER_STRONG_COUNT:
        strength = "STRONG"
    elif total_buy_usd >= INSIDER_MODERATE_USD:
        strength = "MODERATE"
    else:
        strength = "NONE"

    if log_api and run_date:
        from database.db import log_api_call
        log_api_call(run_date, "sec_edgar", ticker, True)
    result = {
        "has_insider_buying": strength != "NONE",
        "total_purchased_usd": round(total_buy_usd, 2),
        "num_insiders": len(buy_insiders),
        "largest_buy_usd": round(largest, 2),
        "latest_filing_date": latest_date,
        "signal_strength": strength,
        # Selling signals — open-market sales by insiders into strength
        "has_insider_selling": total_sell_usd >= INSIDER_MIN_BUY_USD,
        "total_sold_usd": round(total_sell_usd, 2),
        "num_sellers": len(sell_insiders),
    }
    set_cache(cache_key, result, ttl_hours=INSIDER_BUYING_TTL_H)
    return result


def _parse_form4_transactions(xml_text: str) -> tuple[float, float]:
    """
    Extract buy and sell values from Form 4 XML.
    'P' = open-market purchase, 'S' = open-market sale.
    Returns (total_buy_usd, total_sell_usd).
    """
    import re
    buy_total = 0.0
    sell_total = 0.0

    blocks = re.findall(
        r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>",
        xml_text, re.DOTALL
    )
    for block in blocks:
        code_match = re.search(r"<transactionCode>(\w+)</transactionCode>", block)
        if not code_match:
            continue
        code = code_match.group(1)
        if code not in ("P", "S"):
            continue
        shares_match = re.search(
            r"<transactionShares>.*?<value>([\d.]+)</value>", block, re.DOTALL
        )
        price_match = re.search(
            r"<transactionPricePerShare>.*?<value>([\d.]+)</value>", block, re.DOTALL
        )
        if shares_match and price_match:
            try:
                usd = float(shares_match.group(1)) * float(price_match.group(1))
                if code == "P":
                    buy_total += usd
                else:
                    sell_total += usd
            except Exception:
                pass
    return buy_total, sell_total
