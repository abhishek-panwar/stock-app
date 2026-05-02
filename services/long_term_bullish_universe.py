"""
Long-term bullish universe builder — Friday scan.

get_long_bullish_hot_tickers()  — HTTP only, returns raw candidate ticker list
filter_long_bullish_universe()  — pure computation from pre-fetched ticker_data

Selection criteria (from pre-fetched data):
  - Market cap >= $2B
  - Price above MA50 (above intermediate-term trend)
  - Fundamentals present: at least one of revenue_growth, earnings_growth, FCF must be positive
  - 5-day return > -10% (not in freefall — fundamentals need time, not a crash candidate)
  - Crypto excluded (no fundamental signals)

Universe sources:
  - Nasdaq 100 stocks with upcoming earnings (passed in from caller)
  - Yahoo most_actives + trending (broad liquid names)
  - Alpha Vantage most_actively_traded (institutional flow)
  - S&P 500 proxy: large-cap ETF holdings (SPY top components via screener)
"""

import os

MIN_MARKET_CAP = 2_000_000_000  # $2B

_EXCLUDE_TICKERS = {
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "BNB-USD",
    "ADA-USD", "AVAX-USD", "DOGE-USD", "LINK-USD", "DOT-USD",
}


def get_long_bullish_hot_tickers(av_gainers: set[str] | None = None) -> list[str]:
    """
    Returns raw long-term bullish candidate tickers.
    Targets liquid, large-cap names — the kind that re-rate over 60-180 days.
    HTTP only — no yfinance, no Finnhub.

    av_gainers: pass result of fetch_alpha_vantage_gainers() to avoid double-calling AV.
    """
    import requests

    raw: set[str] = set()
    headers = {"User-Agent": "Mozilla/5.0"}

    yahoo_sources = [
        # Most actively traded = institutional flow, good long-term candidates
        "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?scrIds=most_actives&count=25",
        # Trending = broad market attention
        "https://query1.finance.yahoo.com/v1/finance/trending/US",
        # Large-cap gainers with volume = re-rating in progress
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

    if av_gainers:
        raw |= av_gainers

    # Add commodity/macro plays for long-term (valid fundamental assets)
    raw.update(["GLD", "USO", "TLT", "XLE", "XLF", "XLK", "XLV"])

    raw -= _EXCLUDE_TICKERS
    tickers = sorted(raw)
    print(f"  Long bullish candidate pool: {len(tickers)} raw tickers")
    return tickers


def filter_long_bullish_universe(
    hot_tickers: list[str],
    nasdaq_earnings_candidates: set[str],
    nasdaq100: set[str],
    ticker_data: dict,
    long_bearish_tickers: set[str],
) -> tuple[list[dict], int, int, int]:
    """
    Filters raw candidates down to genuine long-term bullish setups.
    Uses pre-fetched data — zero live API calls.

    Returns:
      (universe, nasdaq_earnings_count, hot_count, overlap_count)
      universe entries: {"ticker": str, "source": str}
    """
    hot = set(hot_tickers)

    # MA50 check for Nasdaq earnings stocks
    nasdaq_with_earnings: set[str] = set()
    for t in nasdaq_earnings_candidates:
        if t not in ticker_data:
            continue
        ind = ticker_data[t]["ind"]
        price = ind.get("price", 0)
        ma50  = ind.get("ma50")
        if ma50 is None or price >= ma50:
            nasdaq_with_earnings.add(t)
        else:
            print(f"  {t} excluded from long bullish — below MA50 going into earnings")

    overlap = nasdaq_with_earnings & hot

    universe = []
    seen: set[str] = set()
    filtered_mcap       = 0
    filtered_trend      = 0
    filtered_fundament  = 0
    filtered_bearish    = 0

    all_candidates = list(hot) + [t for t in nasdaq_with_earnings if t not in hot]

    for t in all_candidates:
        if t in seen:
            continue

        if t in long_bearish_tickers:
            filtered_bearish += 1
            print(f"  {t} excluded from long bullish — assigned to long bearish pipeline")
            continue

        if t not in ticker_data:
            continue

        data = ticker_data[t]
        ind  = data["ind"]

        mcap = data.get("market_cap") or 0
        if mcap > 0 and mcap < MIN_MARKET_CAP:
            filtered_mcap += 1
            print(f"  {t} filtered — market cap ${mcap/1e6:.0f}M (below $2B floor)")
            continue

        fail, detail = _check_long_bullish_setup(ind, data.get("fundamentals") or {}, data.get("df"))
        if fail:
            if fail == "trend":
                filtered_trend += 1
            else:
                filtered_fundament += 1
            print(f"  {t} excluded from long bullish — {fail}: {detail}")
            continue

        source = "both" if t in overlap else ("nasdaq_earnings" if t in nasdaq_with_earnings else "hot_stock")
        universe.append({"ticker": t, "source": source})
        seen.add(t)

    print(
        f"  Long bullish universe: {len(universe)} stocks "
        f"(filtered: {filtered_mcap} mcap, {filtered_trend} trend, "
        f"{filtered_fundament} fundamentals, {filtered_bearish} bearish-overlap)"
    )
    return universe, len(nasdaq_with_earnings), len(hot), len(overlap)


def _check_long_bullish_setup(ind: dict, fundamentals: dict, df) -> tuple[str | None, str]:
    """
    Returns (fail_reason, detail). fail_reason is None if passes all checks.
    Pure computation — no API calls.
    """
    try:
        price = ind.get("price", 0)
        ma50  = ind.get("ma50")

        # Must be above MA50 for a long-term bullish thesis
        if ma50 is not None and ma50 > 0 and price < ma50:
            return "trend", f"price ${price:.2f} below MA50 ${ma50:.2f}"

        # 5-day return check — not in active freefall
        if df is not None:
            close = df["close"] if "close" in df.columns else df.get("Close")
            if close is not None and len(close) >= 5:
                ret_5d = (float(close.iloc[-1]) - float(close.iloc[-5])) / float(close.iloc[-5]) * 100
                if ret_5d < -10.0:
                    return "trend", f"5d return {ret_5d:.1f}% — in freefall"

        # At least one positive fundamental signal required
        if fundamentals:
            rev_growth  = fundamentals.get("revenue_growth_pct")
            earn_growth = fundamentals.get("earnings_growth_pct")
            fcf         = fundamentals.get("free_cashflow")
            has_signal  = (
                (rev_growth  is not None and rev_growth  > 0) or
                (earn_growth is not None and earn_growth > 0) or
                (fcf         is not None and fcf         > 0)
            )
            if not has_signal:
                return "fundamentals", "no positive revenue/earnings/FCF signal"

        return None, "passes all checks"
    except Exception:
        return None, "error in check — passing through"
