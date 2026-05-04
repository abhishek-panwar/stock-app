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
        return {
            "name": info.get("longName", ticker),
            "sector": info.get("sector", "Unknown"),
            "industry": info.get("industry", "Unknown"),
            "market_cap": info.get("marketCap"),
            "avg_volume": info.get("averageVolume") or info.get("averageDailyVolume10Day"),
            "52w_high": info.get("fiftyTwoWeekHigh"),
            "52w_low": info.get("fiftyTwoWeekLow"),
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
    Returns cached fundamentals (fetched Saturday by fundamentals_fetcher).
    Falls back to a live yfinance fetch if cache is empty (e.g. new ticker).
    """
    from database.db import get_cache, set_cache
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
