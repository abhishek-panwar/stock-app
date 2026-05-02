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
