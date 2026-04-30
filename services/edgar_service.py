"""
SEC EDGAR insider buying signal (Phase 2).
Uses the free public EDGAR API — no key required.
SEC requires a descriptive User-Agent header.
"""
import requests
from datetime import datetime, timedelta

_USER_AGENT = "stock-app-abhi research@example.com"
_HEADERS = {"User-Agent": _USER_AGENT}
_SESSION = None

CIK_MAP_TTL_H       = 720      # 30 days — SEC CIK map rarely changes
INSIDER_LOOKBACK_DAYS   = 14
INSIDER_MIN_BUY_USD     = 10_000   # ignore token/trivial purchases
INSIDER_STRONG_USD      = 500_000  # threshold for STRONG signal
INSIDER_STRONG_COUNT    = 3        # or this many distinct insiders
INSIDER_MODERATE_USD    = 100_000  # threshold for MODERATE signal

# In-memory CIK cache to avoid repeated bulk downloads per scanner run
_cik_cache: dict[str, str] = {}
_cik_cache_loaded = False


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


def get_insider_buying(ticker: str, days_back: int = INSIDER_LOOKBACK_DAYS) -> dict:
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
        return empty

    try:
        resp = _session().get(
            f"https://data.sec.gov/submissions/CIK{cik}.json", timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
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
        return empty

    # Parse each Form 4 XML to extract transaction type and value
    total_usd = 0.0
    insiders = set()
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
            purchase_usd = _parse_form4_purchase(r.text)
            if purchase_usd and purchase_usd >= INSIDER_MIN_BUY_USD:
                total_usd += purchase_usd
                largest = max(largest, purchase_usd)
                insiders.add(filing["accession"])  # one accession = one insider filing
                if not latest_date or filing["date"] > latest_date:
                    latest_date = filing["date"]
        except Exception:
            continue

    if total_usd == 0:
        return empty

    # Signal strength thresholds
    if total_usd >= INSIDER_STRONG_USD or len(insiders) >= INSIDER_STRONG_COUNT:
        strength = "STRONG"
    elif total_usd >= INSIDER_MODERATE_USD:
        strength = "MODERATE"
    else:
        strength = "NONE"

    return {
        "has_insider_buying": strength != "NONE",
        "total_purchased_usd": round(total_usd, 2),
        "num_insiders": len(insiders),
        "largest_buy_usd": round(largest, 2),
        "latest_filing_date": latest_date,
        "signal_strength": strength,
    }


def _parse_form4_purchase(xml_text: str) -> float:
    """
    Extract total purchase value from Form 4 XML.
    Only counts transactionCode 'P' (open-market purchase), not 'A' (award/grant).
    """
    import re
    total = 0.0

    # Find all nonDerivativeTransaction blocks
    blocks = re.findall(
        r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>",
        xml_text, re.DOTALL
    )
    for block in blocks:
        code_match = re.search(r"<transactionCode>(\w+)</transactionCode>", block)
        if not code_match or code_match.group(1) != "P":
            continue
        shares_match = re.search(
            r"<transactionShares>.*?<value>([\d.]+)</value>", block, re.DOTALL
        )
        price_match = re.search(
            r"<transactionPricePerShare>.*?<value>([\d.]+)</value>", block, re.DOTALL
        )
        if shares_match and price_match:
            try:
                total += float(shares_match.group(1)) * float(price_match.group(1))
            except Exception:
                pass
    return total
