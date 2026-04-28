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
    """Computes hot scores for a candidate pool and returns top N."""
    from services.finnhub_service import compute_hot_score
    from services.yfinance_service import get_price_momentum

    # Candidate pool: well-known trending tickers beyond Nasdaq 100
    candidates = [
        "SMCI", "PLTR", "HOOD", "SOFI", "UPST", "AFRM",
        "COIN", "MSTR", "GME", "AMC", "SPCE", "JOBY",
        "RBLX", "SNAP", "PINS", "UBER", "LYFT", "DASH",
        "ROKU", "SHOP", "SE", "GRAB", "BABA",
        "JD", "NIO", "XPEV", "LI", "F", "GM", "STLA",
        "MVIS", "OUST",
        "NET", "SNOW", "OKTA", "S", "TENB",
        "VRNS", "QLYS",
        "AI", "BBAI", "SOUN", "IREN", "CORZ", "RIOT", "MARA", "HUT",
        "BTBT", "CIFR", "WULF", "CLSK",
    ]
    # Remove duplicates
    seen = set()
    unique = [t for t in candidates if t not in seen and not seen.add(t)]

    scored = []
    for ticker in unique[:80]:
        try:
            score = compute_hot_score(ticker)
            scored.append((ticker, score))
        except Exception:
            pass

    scored.sort(key=lambda x: x[1], reverse=True)
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
