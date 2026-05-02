"""
Short-term bullish universe builder.

get_bullish_hot_tickers()  — HTTP only, returns raw candidate ticker list
get_bullish_candidates()   — returns Nasdaq earnings candidates (no API calls)
filter_bullish_universe()  — pure computation from pre-fetched ticker_data
                             (no live API calls — reads ind/df from the shared fetch store)

Selection criteria (evaluated from pre-fetched data):
  - Market cap >= $2B
  - Price >= MA20 (basic uptrend posture)
  - 5-day return > -5% (not in active selloff)
  - Up-day volume >= down-day volume over last 10 sessions (accumulation bias)
  - Nasdaq earnings stocks additionally require price >= MA50
"""

import os

MIN_MARKET_CAP = 2_000_000_000  # $2B


def fetch_alpha_vantage_gainers() -> set[str]:
    """
    Fetches top_gainers + most_actively_traded from Alpha Vantage.
    Called ONCE per scanner run and shared between bullish and bearish builders.
    Returns empty set if AV key not configured or request fails.
    """
    import requests
    av_key = os.environ.get("ALPHA_VANTAGE_KEY", "")
    if not av_key:
        print("  Alpha Vantage key not set — skipping AV tickers")
        return set()
    try:
        r = requests.get(
            f"https://www.alphavantage.co/query?function=TOP_GAINERS_LOSERS&apikey={av_key}",
            timeout=10,
        )
        data = r.json()
        raw: set[str] = set()
        for category in ("top_gainers", "most_actively_traded"):
            for item in data.get(category, []):
                sym = item.get("ticker", "")
                if sym and "=" not in sym and "/" not in sym:
                    raw.add(sym.upper())
        print(f"  Alpha Vantage: {len(raw)} tickers (gainers + actives)")
        return raw
    except Exception as e:
        print(f"  Alpha Vantage fetch failed: {e}")
        return set()


def get_bullish_hot_tickers(av_gainers: set[str] | None = None) -> list[str]:
    """
    Returns raw bullish candidate tickers: gainers + actives + trending.
    Intentionally excludes day_losers — those feed the bearish universe.
    HTTP only — no yfinance, no Finnhub.

    av_gainers: pass result of fetch_alpha_vantage_gainers() to avoid double-calling AV.
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

    if av_gainers:
        raw |= av_gainers

    # Always include crypto/commodities in bullish universe
    raw.update(["BTC-USD", "ETH-USD", "SOL-USD", "GLD", "USO"])

    tickers = sorted(raw)
    print(f"  Bullish hot tickers: {len(tickers)} symbols (gainers + actives + trending)")
    return tickers


def get_bullish_candidates(earnings_tickers: set[str]) -> tuple[set[str], set[str]]:
    """
    Returns (nasdaq_earnings_candidates, nasdaq100_set) — no API calls.
    Caller passes earnings_tickers from the already-loaded DB.
    """
    from services.screener_service import load_watchlist
    data = load_watchlist()
    nasdaq = set(data["nasdaq100"])
    return nasdaq & earnings_tickers, nasdaq


def filter_bullish_universe(
    hot_tickers: list[str],
    nasdaq_earnings_candidates: set[str],
    nasdaq100: set[str],
    ticker_data: dict,
    bearish_tickers: set[str],
) -> tuple[list[dict], int, int, int]:
    """
    Filters the bullish universe using pre-fetched data — zero live API calls.

    hot_tickers            : raw candidates from get_bullish_hot_tickers()
    nasdaq_earnings_candidates : Nasdaq tickers with upcoming earnings
    nasdaq100              : full Nasdaq 100 set
    ticker_data            : {ticker: data_dict} from market_data_fetcher.fetch_all()
    bearish_tickers        : tickers already assigned to bearish pipeline — excluded here

    Returns (universe, nasdaq_earnings_count, hot_count, overlap_count).
    universe entries: {"ticker": str, "source": str}
    """
    hot = set(hot_tickers)
    nasdaq_with_earnings: set[str] = set()

    # MA50 check for Nasdaq earnings stocks — from pre-fetched ind
    for t in nasdaq_earnings_candidates:
        if t not in ticker_data:
            continue  # no price data fetched — skip
        ind = ticker_data[t]["ind"]
        price = ind.get("price", 0)
        ma50  = ind.get("ma50")
        if ma50 is None or price >= ma50:
            nasdaq_with_earnings.add(t)
        else:
            print(f"  {t} excluded from bullish — below MA50 going into earnings")

    overlap = nasdaq_with_earnings & hot

    universe = []
    seen = set()
    filtered_mcap      = 0
    filtered_momentum  = 0
    filtered_bearish   = 0

    all_candidates = list(hot) + [t for t in nasdaq_with_earnings if t not in hot]

    for t in all_candidates:
        if t in seen:
            continue

        # Exclude tickers already in bearish pipeline
        if t in bearish_tickers:
            filtered_bearish += 1
            print(f"  {t} excluded from bullish — assigned to bearish pipeline")
            continue

        if t not in ticker_data:
            continue  # no data fetched — skip silently (was already logged during fetch)

        data = ticker_data[t]
        ind  = data["ind"]
        df   = data["df"]

        # Market cap check
        mcap = data.get("market_cap") or 0
        if mcap > 0 and mcap < MIN_MARKET_CAP:
            filtered_mcap += 1
            print(f"  {t} filtered — market cap ${mcap/1e6:.0f}M (below $2B floor)")
            continue

        # Momentum pre-filter — pure computation from pre-fetched data
        if not _passes_momentum_prefilter_from_data(ind, df):
            filtered_momentum += 1
            print(f"  {t} excluded from bullish — failed momentum pre-filter")
            continue

        source = "both" if t in overlap else ("nasdaq_earnings" if t in nasdaq_with_earnings else "hot_stock")
        universe.append({"ticker": t, "source": source})
        seen.add(t)

    print(
        f"  Bullish universe: {len(universe)} stocks "
        f"(filtered: {filtered_mcap} mcap, {filtered_momentum} momentum, {filtered_bearish} bearish-overlap)"
    )
    return universe, len(nasdaq_with_earnings), len(hot), len(overlap)


def _passes_momentum_prefilter_from_data(ind: dict, df) -> bool:
    """
    Momentum check using already-fetched indicators and price DataFrame.
    No live API calls.
    Returns True (passes / include in bullish) if data is insufficient.
    """
    try:
        price = ind.get("price", 0)
        ma20  = ind.get("ma20")

        # Price >= MA20
        if ma20 is not None and price < ma20:
            return False

        # 5-day return > -5%
        close = df["close"] if "close" in df.columns else df["Close"]
        if len(close) >= 5:
            ret_5d = (float(close.iloc[-1]) - float(close.iloc[-5])) / float(close.iloc[-5]) * 100
            if ret_5d < -5.0:
                return False

        # Up-day volume >= down-day volume over last 10 sessions
        if "volume" in df.columns and len(df) >= 10:
            last10    = df.iloc[-10:]
            open_col  = "open"  if "open"  in df.columns else "Open"
            close_col = "close" if "close" in df.columns else "Close"
            vol_col   = "volume"
            up_vol   = float(last10[last10[close_col] >= last10[open_col]][vol_col].sum())
            down_vol = float(last10[last10[close_col] <  last10[open_col]][vol_col].sum())
            if down_vol > 0 and up_vol < down_vol:
                return False

        return True
    except Exception:
        return True  # on any error, include — don't drop on missing data
