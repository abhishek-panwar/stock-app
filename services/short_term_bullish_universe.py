"""
Short-term bullish universe builder.

Selection criteria:
  - Hot tickers: Yahoo day_gainers + most_actives + trending + Alpha Vantage gainers/actives
    (day_losers intentionally excluded — losers feed the bearish universe)
  - Nasdaq 100 stocks with upcoming earnings (within 14 days) AND price above MA50
    (below MA50 going into earnings = higher miss risk → bearish candidate instead)
  - Market cap floor: $2B
  - Momentum pre-filter: price > MA20, 5-day return > -5%, up-day volume bias
"""

import os
from datetime import datetime
import pytz

PT = pytz.timezone("America/Los_Angeles")
MIN_MARKET_CAP = 2_000_000_000  # $2B


def build_bullish_universe(hot_tickers: list[str]) -> tuple[list[dict], int, int, int]:
    """
    Returns (universe, nasdaq_earnings_count, hot_count, overlap_count).
    universe entries: {"ticker": str, "source": str}
    source: "nasdaq_earnings" | "hot_stock" | "both"
    """
    from services.screener_service import load_watchlist
    data = load_watchlist()
    nasdaq = set(data["nasdaq100"])
    hot = set(hot_tickers)

    try:
        from database.db import get_earnings_calendar_from_db
        earnings_tickers = {row["ticker"] for row in get_earnings_calendar_from_db()}
    except Exception:
        earnings_tickers = set()

    nasdaq_earnings_candidates = nasdaq & earnings_tickers

    # Filter Nasdaq earnings stocks: must be above MA50 (uptrend going into earnings)
    nasdaq_with_earnings = set()
    for t in nasdaq_earnings_candidates:
        if _passes_ma50_check(t):
            nasdaq_with_earnings.add(t)
        else:
            print(f"  {t} excluded from bullish — below MA50 going into earnings")

    overlap = nasdaq_with_earnings & hot

    universe = []
    seen = set()
    filtered_mcap = 0

    for t in list(hot) + [t for t in nasdaq_with_earnings if t not in hot]:
        source = "both" if t in overlap else ("nasdaq_earnings" if t in nasdaq_with_earnings else "hot_stock")
        mcap = _get_market_cap(t)
        if mcap > 0 and mcap < MIN_MARKET_CAP:
            filtered_mcap += 1
            print(f"  {t} filtered — market cap ${mcap/1e6:.0f}M (below $2B floor)")
            continue
        if not _passes_momentum_prefilter(t):
            print(f"  {t} excluded from bullish — failed momentum pre-filter")
            continue
        if t not in seen:
            universe.append({"ticker": t, "source": source})
            seen.add(t)

    if filtered_mcap:
        print(f"  Filtered {filtered_mcap} tickers below $2B market cap")

    return universe, len(nasdaq_with_earnings), len(hot), len(overlap)


def get_bullish_hot_tickers() -> list[str]:
    """
    Returns tickers likely in uptrends: gainers, actives, trending.
    Intentionally excludes day_losers (those go to bearish universe).
    """
    import requests

    raw: set[str] = set()
    headers = {"User-Agent": "Mozilla/5.0"}

    yahoo_sources = [
        "https://query1.finance.yahoo.com/v1/finance/trending/US",
        "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?scrIds=most_actives&count=20",
        "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?scrIds=day_gainers&count=20",
    ]
    for url in yahoo_sources:
        try:
            r = requests.get(url, headers=headers, timeout=10)
            quotes = r.json()["finance"]["result"][0]["quotes"]
            for q in quotes:
                sym = q.get("symbol", "")
                if sym and "=" not in sym and "/" not in sym:
                    raw.add(sym.upper())
        except Exception:
            pass

    av_key = os.environ.get("ALPHA_VANTAGE_KEY", "")
    if av_key:
        try:
            r = requests.get(
                f"https://www.alphavantage.co/query?function=TOP_GAINERS_LOSERS&apikey={av_key}",
                timeout=10,
            )
            data = r.json()
            for category in ("top_gainers", "most_actively_traded"):
                for item in data.get(category, []):
                    sym = item.get("ticker", "")
                    if sym and "=" not in sym and "/" not in sym:
                        raw.add(sym.upper())
        except Exception:
            pass

    # Always include crypto/commodities in bullish universe
    raw.update(["BTC-USD", "ETH-USD", "SOL-USD", "GLD", "USO"])

    tickers = sorted(raw)
    print(f"  Bullish hot tickers: {len(tickers)} symbols (gainers + actives + trending)")
    return tickers


# ── Pre-filters ────────────────────────────────────────────────────────────────

def _get_market_cap(ticker: str) -> int:
    try:
        import yfinance as yf
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return int(yf.Ticker(ticker).info.get("marketCap") or 0)
    except Exception:
        return 0


def _passes_ma50_check(ticker: str) -> bool:
    """Returns True if price is above MA50. Used to qualify Nasdaq earnings stocks."""
    try:
        import yfinance as yf
        import warnings
        import pandas as pd
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            df = yf.Ticker(ticker).history(period="3mo")
        if df.empty or len(df) < 50:
            return True  # not enough data — don't exclude
        close = df["Close"]
        ma50 = close.rolling(50).mean().iloc[-1]
        return float(close.iloc[-1]) >= float(ma50)
    except Exception:
        return True


def _passes_momentum_prefilter(ticker: str) -> bool:
    """
    Lightweight momentum check before full scoring.
    Rejects stocks that are in active selloffs or have no upward bias.
    Criteria:
      - Price >= MA20 (basic uptrend posture)
      - 5-day return > -5% (not in active selloff)
      - Up-day volume >= down-day volume over last 10 days (accumulation bias)
    Returns True (passes) if data is unavailable — don't exclude on missing data.
    """
    try:
        import yfinance as yf
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            df = yf.Ticker(ticker).history(period="1mo")
        if df.empty or len(df) < 10:
            return True

        close  = df["Close"]
        volume = df["Volume"]
        price  = float(close.iloc[-1])

        # MA20 check
        if len(close) >= 20:
            ma20 = float(close.rolling(20).mean().iloc[-1])
            if price < ma20:
                return False

        # 5-day return check
        if len(close) >= 5:
            ret_5d = (price - float(close.iloc[-5])) / float(close.iloc[-5]) * 100
            if ret_5d < -5.0:
                return False

        # Volume bias: up-day vol vs down-day vol over last 10 sessions
        if len(df) >= 10:
            last10 = df.iloc[-10:]
            up_days   = last10[last10["Close"] >= last10["Open"]]
            down_days = last10[last10["Close"] <  last10["Open"]]
            up_vol   = float(up_days["Volume"].sum())
            down_vol = float(down_days["Volume"].sum())
            if down_vol > 0 and up_vol < down_vol:
                return False

        return True
    except Exception:
        return True
