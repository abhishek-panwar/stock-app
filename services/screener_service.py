import json
import os
from datetime import datetime
import pytz

PT = pytz.timezone("America/Los_Angeles")


def load_watchlist() -> dict:
    path = os.path.join(os.path.dirname(__file__), "..", "config", "watchlist.json")
    with open(path) as f:
        return json.load(f)


def load_nasdaq100() -> list[str]:
    return load_watchlist()["nasdaq100"]


def _is_tradable(ticker: str) -> bool:
    """Returns False if yfinance can't find any price data (delisted/invalid)."""
    try:
        import yfinance as yf
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            hist = yf.Ticker(ticker).fast_info
            return (hist.get("last_price") or 0) > 0
    except Exception:
        return False


def build_universe(hot_tickers: list[str]) -> tuple[list[dict], int, int, int]:
    """
    Universe = hot tickers + any Nasdaq 100 stocks with upcoming earnings.
    Pure Nasdaq stocks with no catalyst are excluded to reduce API call volume.
    Returns: (universe_with_source, nasdaq_earnings_count, hot_count, overlap_count)
    """
    data = load_watchlist()
    nasdaq = set(data["nasdaq100"])
    hot = set(hot_tickers)
    overlap = nasdaq & hot

    # Pull earnings universe from DB (already fetched and persisted by scanner)
    try:
        from database.db import get_earnings_calendar_from_db
        earnings_tickers = {row["ticker"] for row in get_earnings_calendar_from_db()}
    except Exception:
        earnings_tickers = set()

    nasdaq_with_earnings = nasdaq & earnings_tickers

    universe = []
    seen = set()
    for t in hot:
        universe.append({"ticker": t, "source": "both" if t in overlap else "hot_stock"})
        seen.add(t)
    for t in nasdaq_with_earnings:
        if t not in seen:
            universe.append({"ticker": t, "source": "nasdaq_earnings"})
            seen.add(t)

    return universe, len(nasdaq_with_earnings), len(hot), len(overlap)


def get_hot_tickers() -> list[str]:
    """
    Fetches trending tickers from Yahoo Finance (validated symbols),
    then ranks them by hot score (analyst rating + news volume + momentum).
    Returns all tickers scoring >5 — final selection to Claude happens in the scanner.
    """
    from services.finnhub_service import compute_hot_score
    import requests

    raw_tickers: set[str] = set()

    headers = {"User-Agent": "Mozilla/5.0"}
    yahoo_sources = [
        "https://query1.finance.yahoo.com/v1/finance/trending/US",
        "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?scrIds=most_actives&count=20",
        "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?scrIds=day_gainers&count=20",
        "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?scrIds=day_losers&count=20",
    ]
    for url in yahoo_sources:
        try:
            r = requests.get(url, headers=headers, timeout=10)
            quotes = r.json()["finance"]["result"][0]["quotes"]
            for q in quotes:
                symbol = q.get("symbol", "")
                if symbol and "=" not in symbol and "/" not in symbol:
                    raw_tickers.add(symbol.upper())
        except Exception:
            pass

    # Always include crypto/commodities
    raw_tickers.update(["BTC-USD", "ETH-USD", "SOL-USD", "GLD", "USO"])

    scored = []
    for ticker in raw_tickers:
        try:
            score = compute_hot_score(ticker)
            if score > 5:
                scored.append((ticker, score))
        except Exception:
            pass

    scored.sort(key=lambda x: x[1], reverse=True)
    print(f"  Hot tickers: {len(raw_tickers)} from Yahoo, {len(scored)} scored above threshold")
    return [t for t, _ in scored]


_CRYPTO_TICKERS = {
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "BNB-USD",
    "ADA-USD", "AVAX-USD", "DOGE-USD", "LINK-USD", "DOT-USD",
}
_COMMODITY_TICKERS = {
    "GLD", "IAU", "GDX", "GDXJ", "GOLD", "SLV", "PPLT", "USO", "UNG",
}

def get_asset_class(ticker: str) -> str:
    """Returns 'crypto', 'commodity', or 'stock'."""
    if ticker in _CRYPTO_TICKERS or ticker.endswith("-USD"):
        return "crypto"
    if ticker in _COMMODITY_TICKERS:
        return "commodity"
    return "stock"


def rank_predictions(predictions: list[dict]) -> dict:
    """
    Groups predictions into timeframe buckets and returns top picks.
    predictions: list of dicts with timeframe, score, ticker, direction, confidence
    """
    buckets = {"short": [], "medium": [], "long": []}
    for p in predictions:
        tf = p.get("timeframe", "short")
        if tf in buckets:
            buckets[tf].append(p)

    for tf in buckets:
        buckets[tf].sort(key=lambda x: x.get("score", 0), reverse=True)
        buckets[tf] = buckets[tf][:10]

    # All-timeframes agree
    tickers_per_tf = {tf: {p["ticker"] for p in buckets[tf]} for tf in buckets}
    all_agree = tickers_per_tf["short"] & tickers_per_tf["medium"] & tickers_per_tf["long"]

    # Check direction alignment for "agree" tickers
    agree_list = []
    for ticker in all_agree:
        directions = []
        for tf in ["short", "medium", "long"]:
            for p in buckets[tf]:
                if p["ticker"] == ticker:
                    directions.append(p.get("direction", "NEUTRAL"))
        if len(set(directions)) == 1 and directions[0] != "NEUTRAL":
            avg_conf = sum(
                p["confidence"] for tf in ["short", "medium", "long"]
                for p in buckets[tf] if p["ticker"] == ticker
            ) / 3
            agree_list.append({"ticker": ticker, "direction": directions[0], "avg_confidence": round(avg_conf)})

    # Top pick = highest score across all timeframes
    all_preds = [p for tf in buckets.values() for p in tf]
    top_pick = max(all_preds, key=lambda x: x.get("score", 0)) if all_preds else None

    return {
        "short": buckets["short"],
        "medium": buckets["medium"],
        "long": buckets["long"],
        "all_timeframes_agree": agree_list,
        "top_pick": top_pick,
    }


def compute_buy_window(timeframe: str, score: int) -> str:
    """Returns recommended buy window in PT."""
    if timeframe == "short":
        return "7:15 AM – 8:30 AM PT"
    elif timeframe == "medium":
        return "11:00 AM – 12:30 PM PT"
    else:
        return "11:00 AM – 12:30 PM PT"
