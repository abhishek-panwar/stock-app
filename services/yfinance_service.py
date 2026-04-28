import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import pytz

PT = pytz.timezone("America/Los_Angeles")


def get_price_history(ticker: str, period: str = "6mo") -> pd.DataFrame:
    """Returns OHLCV daily data. period: 1mo, 3mo, 6mo, 1y, 2y"""
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
    except Exception:
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


def get_ticker_info(ticker: str) -> dict:
    try:
        t = yf.Ticker(ticker)
        info = t.info
        return {
            "name": info.get("longName", ticker),
            "sector": info.get("sector", "Unknown"),
            "industry": info.get("industry", "Unknown"),
            "market_cap": info.get("marketCap"),
            "52w_high": info.get("fiftyTwoWeekHigh"),
            "52w_low": info.get("fiftyTwoWeekLow"),
        }
    except Exception:
        return {"name": ticker, "sector": "Unknown", "industry": "Unknown"}


def is_market_open() -> bool:
    now_pt = datetime.now(PT)
    if now_pt.weekday() >= 5:
        return False
    market_open = now_pt.replace(hour=6, minute=30, second=0, microsecond=0)
    market_close = now_pt.replace(hour=13, minute=0, second=0, microsecond=0)
    return market_open <= now_pt <= market_close


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
