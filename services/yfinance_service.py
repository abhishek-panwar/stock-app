import time
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import pytz

PT = pytz.timezone("America/Los_Angeles")


def get_price_history(ticker: str, period: str = "6mo") -> pd.DataFrame:
    """Returns OHLCV daily data. period: 1mo, 3mo, 6mo, 1y, 2y"""
    for attempt in range(3):
        try:
            df = yf.download(ticker, period=period, interval="1d", progress=False, auto_adjust=True)
            if df.empty:
                return pd.DataFrame()
            # Flatten MultiIndex columns from newer yfinance versions
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0].lower() for col in df.columns]
            else:
                df.columns = [c.lower() for c in df.columns]
            df.index = pd.to_datetime(df.index)
            return df
        except Exception as e:
            if "401" in str(e) or "Unauthorized" in str(e) or "Invalid Crumb" in str(e):
                time.sleep(2 ** attempt)  # 1s, 2s, 4s
                continue
            return pd.DataFrame()
    return pd.DataFrame()


def get_current_price(ticker: str) -> float | None:
    try:
        t = yf.Ticker(ticker)
        info = t.fast_info
        return float(info.last_price)
    except Exception:
        return None


def get_multiple_prices(tickers: list[str]) -> dict[str, float]:
    results = {}
    try:
        data = yf.download(tickers, period="2d", interval="1d", progress=False, auto_adjust=True)
        if data.empty:
            return results
        # Handle both MultiIndex and flat columns
        if isinstance(data.columns, pd.MultiIndex):
            close = data.xs("Close", axis=1, level=0)
        else:
            close = data["Close"] if "Close" in data.columns else data["close"]
        for t in tickers:
            try:
                val = close[t].dropna().iloc[-1]
                results[t] = float(val)
            except Exception:
                pass
    except Exception:
        pass
    return results


def get_ticker_info(ticker: str, run_date: str = "", log_api: bool = False) -> dict:
    try:
        t = yf.Ticker(ticker)
        info = t.info
        if log_api and run_date:
            from database.db import log_api_call
            log_api_call(run_date, "yfinance_info", ticker, True)
        short_pct = info.get("shortPercentOfFloat")
        return {
            "name": info.get("longName", ticker),
            "sector": info.get("sector", "Unknown"),
            "industry": info.get("industry", "Unknown"),
            "market_cap": info.get("marketCap"),
            "avg_volume": info.get("averageVolume") or info.get("averageDailyVolume10Day"),
            "52w_high": info.get("fiftyTwoWeekHigh"),
            "52w_low": info.get("fiftyTwoWeekLow"),
            "short_interest_pct": round(short_pct * 100, 1) if short_pct is not None else None,
        }
    except Exception as e:
        if log_api and run_date:
            from database.db import log_api_call
            log_api_call(run_date, "yfinance_info", ticker, False, str(e))
        return {"name": ticker, "sector": "Unknown", "industry": "Unknown"}


def is_market_open() -> bool:
    now_pt = datetime.now(PT)
    if now_pt.weekday() >= 5:
        return False
    market_open = now_pt.replace(hour=6, minute=30, second=0, microsecond=0)
    market_close = now_pt.replace(hour=13, minute=0, second=0, microsecond=0)
    return market_open <= now_pt <= market_close


def get_fundamentals(ticker: str, run_date: str = "", log_api: bool = False) -> dict:
    """
    Returns cached fundamentals — FMP cache preferred, then main cache, then live yfinance fallback.
    FMP cache is populated by Thursday pre-fetch (thursday_prefetch.py) and weekend fundamentals_fetcher.
    """
    from database.db import get_cache, set_cache
    # Check FMP-specific cache first — higher quality data
    fmp_cached = get_cache(f"fundamentals_fmp_{ticker}")
    if fmp_cached:
        return fmp_cached
    cached = get_cache(f"fundamentals_{ticker}")
    if cached:
        return cached

    # Live fallback — only hits yfinance, no AV call
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        price = info.get("currentPrice") or info.get("regularMarketPrice") or 0
        rev_growth = info.get("revenueGrowth")
        earn_growth = info.get("earningsGrowth")
        mean_target = info.get("targetMeanPrice")
        result = {
            "ticker": ticker,
            "price": price,
            "revenue_growth_pct": round(rev_growth * 100, 1) if rev_growth is not None else None,
            "earnings_growth_pct": round(earn_growth * 100, 1) if earn_growth is not None else None,
            "gross_margin_pct": round(info.get("grossMargins", 0) * 100, 1) if info.get("grossMargins") else None,
            "operating_margin_pct": round(info.get("operatingMargins", 0) * 100, 1) if info.get("operatingMargins") else None,
            "profit_margin_pct": round(info.get("profitMargins", 0) * 100, 1) if info.get("profitMargins") else None,
            "free_cashflow": info.get("freeCashflow"),
            "trailing_pe": round(info.get("trailingPE"), 1) if info.get("trailingPE") else None,
            "forward_pe": round(info.get("forwardPE"), 1) if info.get("forwardPE") else None,
            "peg_ratio": round(info.get("pegRatio"), 2) if info.get("pegRatio") else None,
            "price_to_book": round(info.get("priceToBook"), 2) if info.get("priceToBook") else None,
            "analyst_mean_target": mean_target,
            "analyst_upside_pct": round((mean_target - price) / price * 100, 1) if mean_target and price else None,
            "analyst_count": info.get("numberOfAnalystOpinions"),
            "source": "yfinance_live",
        }
        set_cache(f"fundamentals_{ticker}", result, ttl_hours=48)
        if log_api and run_date:
            from database.db import log_api_call
            log_api_call(run_date, "yfinance_fundamentals", ticker, True)
        return result
    except Exception as e:
        if log_api and run_date:
            from database.db import log_api_call
            log_api_call(run_date, "yfinance_fundamentals", ticker, False, str(e))
        return {}


# Sector ETF map: GICS sector name → ETF ticker
_SECTOR_ETF_MAP = {
    "Technology":             "XLK",
    "Communication Services": "XLC",
    "Consumer Cyclical":      "XLY",
    "Consumer Defensive":     "XLP",
    "Healthcare":             "XLV",
    "Financials":             "XLF",
    "Industrials":            "XLI",
    "Energy":                 "XLE",
    "Utilities":              "XLU",
    "Real Estate":            "XLRE",
    "Basic Materials":        "XLB",
}
_ALL_ETFS = ["SPY"] + list(_SECTOR_ETF_MAP.values())


def get_market_context() -> dict:
    """
    Fetches 5-day returns for SPY and all 11 sector ETFs in one download call.
    Cached 4h — called once per scanner run, shared across all tickers.
    Returns: {"SPY": 1.2, "XLK": 0.8, ...}
    """
    from database.db import get_cache, set_cache
    cached = get_cache("market_context")
    if cached:
        return cached
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            df = yf.download(_ALL_ETFS, period="10d", interval="1d", progress=False, auto_adjust=True)
        close = df["Close"] if isinstance(df.columns, pd.MultiIndex) else df
        result = {}
        for etf in _ALL_ETFS:
            try:
                series = close[etf].dropna()
                if len(series) >= 5:
                    result[etf] = round((float(series.iloc[-1]) - float(series.iloc[-5])) / float(series.iloc[-5]) * 100, 2)
            except Exception:
                pass
        set_cache("market_context", result, ttl_hours=4)
        return result
    except Exception:
        return {}


def get_sector_etf(sector: str) -> str | None:
    """Returns the ETF ticker for a given sector name, or None."""
    return _SECTOR_ETF_MAP.get(sector)


def get_analyst_upgrade_momentum(ticker: str, days_back: int = 30) -> dict:
    """
    Derives upgrade/downgrade momentum from yfinance upgrades_downgrades.
    Counts raises vs cuts in the last days_back days — a cluster of raises = upgrade cycle.
    Returns: {"raises": int, "cuts": int, "net": int, "momentum": "UPGRADING"|"DOWNGRADING"|"NEUTRAL"|None}
    Cached 24h — changes daily but rarely intraday.
    """
    from database.db import get_cache, set_cache
    cache_key = f"analyst_upgrades_{ticker}"
    cached = get_cache(cache_key)
    if cached is not None:
        return cached

    empty = {"raises": 0, "cuts": 0, "net": 0, "momentum": None}
    try:
        from datetime import timezone
        import pandas as pd
        t = yf.Ticker(ticker)
        ud = t.upgrades_downgrades
        if ud is None or ud.empty:
            set_cache(cache_key, empty, ttl_hours=24)
            return empty

        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        # Index is a DatetimeTZDynamic — normalise to UTC
        if ud.index.tz is None:
            ud.index = ud.index.tz_localize("UTC")
        recent = ud[ud.index >= cutoff]
        if recent.empty:
            set_cache(cache_key, empty, ttl_hours=24)
            return empty

        raises = int((recent.get("priceTargetAction", pd.Series(dtype=str)) == "Raises").sum())
        cuts   = int((recent.get("priceTargetAction", pd.Series(dtype=str)) == "Lowers").sum())
        net    = raises - cuts

        if net >= 3:
            momentum = "UPGRADING"
        elif net <= -3:
            momentum = "DOWNGRADING"
        else:
            momentum = "NEUTRAL"

        result = {"raises": raises, "cuts": cuts, "net": net, "momentum": momentum}
        set_cache(cache_key, result, ttl_hours=24)
        return result
    except Exception:
        return empty


def get_institutional_ownership_delta(ticker: str) -> dict:
    """
    Derives net institutional buying/selling from yfinance institutional_holders (13F quarterly).
    pctChange > 0 per institution = increasing position; < 0 = reducing.
    Returns: {"net_buying": int, "net_selling": int, "bias": "ACCUMULATING"|"DISTRIBUTING"|"NEUTRAL"|None}
    Cached 48h — 13F data is quarterly, no need to refresh more often.
    """
    from database.db import get_cache, set_cache
    cache_key = f"inst_ownership_{ticker}"
    cached = get_cache(cache_key)
    if cached is not None:
        return cached

    empty = {"net_buying": 0, "net_selling": 0, "bias": None}
    try:
        t = yf.Ticker(ticker)
        ih = t.institutional_holders
        if ih is None or ih.empty:
            set_cache(cache_key, empty, ttl_hours=48)
            return empty

        changes = ih["pctChange"].dropna() if "pctChange" in ih.columns else None
        if changes is None or changes.empty:
            set_cache(cache_key, empty, ttl_hours=48)
            return empty

        buying  = int((changes > 0.01).sum())   # >1% position increase
        selling = int((changes < -0.01).sum())   # >1% position decrease

        if buying >= selling + 3:
            bias = "ACCUMULATING"
        elif selling >= buying + 3:
            bias = "DISTRIBUTING"
        else:
            bias = "NEUTRAL"

        result = {"net_buying": buying, "net_selling": selling, "bias": bias}
        set_cache(cache_key, result, ttl_hours=48)
        return result
    except Exception:
        return empty


def get_earnings_surprise_magnitude(ticker: str) -> dict:
    """
    Derives earnings beat magnitude from yfinance earnings_history.
    Consistent large beats = Wall Street systematically under-estimating → re-rating catalyst.
    Returns: {"avg_surprise_pct": float, "last_surprise_pct": float, "beat_quality": "STRONG"|"MODERATE"|"WEAK"|None}
    Cached 7 days — earnings history changes once per quarter.
    """
    from database.db import get_cache, set_cache
    cache_key = f"earnings_surprise_{ticker}"
    cached = get_cache(cache_key)
    if cached is not None:
        return cached

    empty = {"avg_surprise_pct": None, "last_surprise_pct": None, "beat_quality": None}
    try:
        t = yf.Ticker(ticker)
        eh = t.earnings_history
        if eh is None or eh.empty:
            set_cache(cache_key, empty, ttl_hours=168)
            return empty

        eh = eh.dropna(subset=["surprisePercent"])
        if eh.empty:
            set_cache(cache_key, empty, ttl_hours=168)
            return empty

        surprises = eh["surprisePercent"].tolist()  # most recent last
        avg_surprise   = round(sum(surprises) / len(surprises) * 100, 1)
        last_surprise  = round(surprises[-1] * 100, 1) if surprises else None

        if avg_surprise >= 8 and last_surprise is not None and last_surprise >= 3:
            beat_quality = "STRONG"    # consistently beating by wide margin = re-rating fuel
        elif avg_surprise >= 3:
            beat_quality = "MODERATE"
        elif avg_surprise < 0:
            beat_quality = "WEAK"      # consistently missing
        else:
            beat_quality = "NEUTRAL"

        result = {"avg_surprise_pct": avg_surprise, "last_surprise_pct": last_surprise, "beat_quality": beat_quality}
        set_cache(cache_key, result, ttl_hours=168)
        return result
    except Exception:
        return empty


def get_price_momentum(ticker: str, days: int = 3) -> float | None:
    """Returns % price change over last N days."""
    df = get_price_history(ticker, period="1mo")
    if df.empty or len(df) < days + 1:
        return None
    try:
        recent = df["close"].iloc[-1]
        past = df["close"].iloc[-(days + 1)]
        return round(((recent - past) / past) * 100, 2)
    except Exception:
        return None
