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


MIN_MARKET_CAP = 2_000_000_000  # $2B — mid-cap floor; excludes small/micro/nano-cap

def _get_market_cap(ticker: str) -> int:
    """Returns market cap from yfinance, or 0 if unavailable."""
    try:
        import yfinance as yf
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            info = yf.Ticker(ticker).info
            return int(info.get("marketCap") or 0)
    except Exception:
        return 0


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
    filtered = 0
    for t in list(hot) + [t for t in nasdaq_with_earnings if t not in hot]:
        source = "both" if t in overlap else ("nasdaq_earnings" if t in nasdaq_with_earnings else "hot_stock")
        mcap = _get_market_cap(t)
        if mcap > 0 and mcap < MIN_MARKET_CAP:
            filtered += 1
            print(f"  {t} filtered — market cap ${mcap/1e6:.0f}M (below $2B mid-cap floor)")
            continue
        if t not in seen:
            universe.append({"ticker": t, "source": source})
            seen.add(t)

    if filtered:
        print(f"  Filtered {filtered} tickers below ${MIN_MARKET_CAP/1e9:.0f}B market cap")

    return universe, len(nasdaq_with_earnings), len(hot), len(overlap)


def get_hot_tickers() -> list[str]:
    """
    Fetches trending tickers from Yahoo Finance + Alpha Vantage (validated symbols).
    No Finnhub scoring here — sources already validate these as real active tickers.
    Final selection to Claude happens in the scanner after full indicator scoring.
    """
    import requests

    raw_tickers: set[str] = set()

    # Yahoo Finance — trending + most active + gainers + losers
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

    # Alpha Vantage — gainers + losers + most active in 1 call
    av_key = os.environ.get("ALPHA_VANTAGE_KEY", "")
    if av_key:
        try:
            r = requests.get(
                f"https://www.alphavantage.co/query?function=TOP_GAINERS_LOSERS&apikey={av_key}",
                timeout=10,
            )
            data = r.json()
            for category in ("top_gainers", "top_losers", "most_actively_traded"):
                for item in data.get(category, []):
                    symbol = item.get("ticker", "")
                    if symbol and "=" not in symbol and "/" not in symbol:
                        raw_tickers.add(symbol.upper())
        except Exception:
            pass
    else:
        print("  Alpha Vantage key not set — skipping AV hot tickers")

    # Always include crypto/commodities
    raw_tickers.update(["BTC-USD", "ETH-USD", "SOL-USD", "GLD", "USO"])

    tickers = sorted(raw_tickers)
    print(f"  Hot tickers: {len(tickers)} validated symbols (Yahoo + Alpha Vantage)")
    return tickers


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

    Within each bucket: BULLISH predictions sort before BEARISH at the same score level,
    so the Telegram summary leads with actionable long entries.
    """
    buckets = {"short": [], "medium": [], "long": []}
    for p in predictions:
        tf = p.get("timeframe", "short")
        if tf in buckets:
            buckets[tf].append(p)

    def _sort_key(p):
        # Primary: score desc; secondary: BULLISH before BEARISH for equal scores
        dir_order = 0 if p.get("direction") == "BULLISH" else 1
        return (-p.get("score", 0), dir_order)

    for tf in buckets:
        buckets[tf].sort(key=_sort_key)
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

    # Top pick = highest score across all timeframes (direction shown in pred dict)
    all_preds = [p for tf in buckets.values() for p in tf]
    top_pick = max(all_preds, key=lambda x: x.get("score", 0)) if all_preds else None

    # Direction counts per bucket for summary display
    direction_counts = {}
    for tf in buckets:
        bullish = sum(1 for p in buckets[tf] if p.get("direction") == "BULLISH")
        bearish = sum(1 for p in buckets[tf] if p.get("direction") == "BEARISH")
        direction_counts[tf] = {"bullish": bullish, "bearish": bearish}

    return {
        "short": buckets["short"],
        "medium": buckets["medium"],
        "long": buckets["long"],
        "all_timeframes_agree": agree_list,
        "top_pick": top_pick,
        "direction_counts": direction_counts,
    }


def compute_buy_window(timeframe: str, score: int) -> str:
    """Returns recommended buy window in PT."""
    if timeframe == "short":
        return "7:15 AM – 8:30 AM PT"
    elif timeframe == "medium":
        return "11:00 AM – 12:30 PM PT"
    else:
        return "11:00 AM – 12:30 PM PT"
