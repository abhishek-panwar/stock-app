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
    Deduplicates Nasdaq 100 + S&P 500 additions + hot stocks.
    Returns: (universe_with_source, nasdaq_count, hot_count, overlap_count)
    """
    data = load_watchlist()
    nasdaq = set(data["nasdaq100"])
    sp500_extra = set(data.get("sp500_additions", []))
    commodities = set(data.get("commodities_and_alts", []))
    core = nasdaq | sp500_extra | commodities
    hot = set(hot_tickers)
    overlap = core & hot
    universe = []
    for t in core:
        universe.append({"ticker": t, "source": "both" if t in overlap else "nasdaq100"})
    for t in hot:
        if t not in core:
            universe.append({"ticker": t, "source": "hot_stock"})
    return universe, len(core), len(hot), len(overlap)


def get_hot_tickers(top_n: int = 50) -> list[str]:
    """
    Dynamically discovers trending tickers from Finnhub market news,
    then ranks them by hot score (analyst rating + news volume + momentum).
    """
    from services.finnhub_service import compute_hot_score, get_client as _fc
    import re

    # Pull general market news from last 48h
    raw_tickers: set[str] = set()
    try:
        news_items = _fc().general_news("general", min_id=0)
        ticker_pattern = re.compile(r'\b([A-Z]{2,5})\b')
        # Common words to exclude that look like tickers
        exclude = {
            "A", "I", "IT", "AT", "BE", "ON", "OR", "BY", "IF", "IN", "IS",
            "NO", "OF", "TO", "UP", "US", "AI", "AN", "AS", "DO", "GO",
            "HE", "MY", "SO", "WE", "CEO", "CFO", "COO", "IPO", "SEC",
            "FDA", "FED", "GDP", "ETF", "NYSE", "NYSE", "NASDAQ", "S&P",
            "USA", "EUR", "USD", "GBP", "JPY", "API", "APP", "THE", "AND",
            "FOR", "ARE", "BUT", "NOT", "ALL", "NEW", "CAN", "HAS", "ITS",
            "WAS", "HAD", "ONE", "TWO", "YOU", "HIM", "HER", "OUR", "OUT",
            "WHO", "HOW", "GET", "SET", "PUT", "MAY", "NOW", "YET", "TOO",
            "BOT", "TAX", "LAW", "ACT", "WAR", "OIL", "GAS", "ESG",
        }
        for item in news_items[:100]:
            headline = item.get("headline", "") + " " + item.get("summary", "")
            found = ticker_pattern.findall(headline)
            for t in found:
                if t not in exclude and len(t) >= 2:
                    raw_tickers.add(t)
    except Exception:
        pass

    # Also pull crypto/commodity tickers that are always relevant
    raw_tickers.update(["BTC-USD", "ETH-USD", "SOL-USD", "GLD", "USO"])

    # Score each candidate by hot score and filter out duds
    scored = []
    for ticker in list(raw_tickers)[:150]:
        try:
            score = compute_hot_score(ticker)
            if score > 5:  # ignore tickers with no real data
                scored.append((ticker, score))
        except Exception:
            pass

    scored.sort(key=lambda x: x[1], reverse=True)
    print(f"  Hot tickers: {len(raw_tickers)} extracted from news, {len(scored)} scored above threshold, returning top {min(top_n, len(scored))}")
    return [t for t, _ in scored[:top_n]]


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
