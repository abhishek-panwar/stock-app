"""
Long-term bearish universe builder — Friday scan.

get_long_bearish_hot_tickers()  — HTTP only, returns raw candidate ticker list
filter_long_bearish_universe()  — pure computation from pre-fetched ticker_data

Selection criteria (from pre-fetched data):
  - Market cap >= $2B (only liquid, shortable names)
  - Price below MA50 OR below MA200 (intermediate-term downtrend)
  - At least one fundamental red flag: negative revenue/earnings growth, negative FCF,
    or PEG > 3 (significantly overvalued with declining fundamentals)
  - 5-day return < 5% (not currently bouncing sharply — don't short into oversold bounces)
  - Crypto and commodities excluded (no fundamental basis for long-term bearish thesis)

Universe sources:
  - Yahoo day_losers (stocks in sustained downtrends)
  - Yahoo 52-week lows screener (structural breakdown candidates)
  - Yahoo most_actives that failed fundamental filters from bullish side
"""

MIN_MARKET_CAP = 2_000_000_000  # $2B

_EXCLUDE_TICKERS = {
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "BNB-USD",
    "ADA-USD", "AVAX-USD", "DOGE-USD", "LINK-USD", "DOT-USD",
    "GLD", "IAU", "GDX", "GDXJ", "GOLD", "SLV", "PPLT", "USO", "UNG",
    "TLT", "IEF", "SHY",  # bonds — directional but not fundamentals-driven shorts
}


def get_long_bearish_hot_tickers() -> list[str]:
    """
    Returns raw long-term bearish candidate tickers.
    Targets liquid names showing sustained price weakness.
    HTTP only — no yfinance, no Finnhub.
    """
    import requests

    raw: set[str] = set()
    headers = {"User-Agent": "Mozilla/5.0"}

    yahoo_sources = [
        # Day losers = stocks failing today, often part of a longer downtrend
        "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?scrIds=day_losers&count=25",
        # Underperformers over 52 weeks = structural breakdown
        "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?scrIds=52wk_low&count=20",
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

    raw -= _EXCLUDE_TICKERS
    tickers = sorted(raw)
    print(f"  Long bearish candidate pool: {len(tickers)} raw tickers")
    return tickers


def filter_long_bearish_universe(
    raw_tickers: list[str],
    ticker_data: dict,
) -> tuple[list[dict], set[str]]:
    """
    Filters raw losers/52wk-low names down to genuine long-term bearish setups.
    Requires fundamental deterioration — not just price weakness.
    Uses pre-fetched data — zero live API calls.

    Returns:
      (universe, long_bearish_ticker_set)
      universe entries: {"ticker": str, "source": "long_bearish_candidate"}
    """
    universe = []
    bearish_tickers: set[str] = set()
    filtered = {"mcap": 0, "trend": 0, "fundamentals": 0, "bounce": 0, "no_data": 0}

    for ticker in raw_tickers:
        if ticker in _EXCLUDE_TICKERS:
            continue

        if ticker not in ticker_data:
            filtered["no_data"] += 1
            continue

        data = ticker_data[ticker]
        ind  = data["ind"]

        fail_reason, detail = _check_long_bearish_setup(
            ind,
            data.get("fundamentals") or {},
            data.get("df"),
            data.get("market_cap"),
        )
        if fail_reason:
            filtered[fail_reason] = filtered.get(fail_reason, 0) + 1
            print(f"  {ticker} excluded from long bearish — {fail_reason}: {detail}")
            continue

        universe.append({"ticker": ticker, "source": "long_bearish_candidate"})
        bearish_tickers.add(ticker)

    print(
        f"  Long bearish universe: {len(universe)} stocks "
        f"(filtered: {filtered['mcap']} mcap, {filtered['trend']} trend, "
        f"{filtered['fundamentals']} fundamentals, {filtered['bounce']} bounce, "
        f"{filtered['no_data']} no data)"
    )
    return universe, bearish_tickers


def _check_long_bearish_setup(
    ind: dict, fundamentals: dict, df, market_cap
) -> tuple[str | None, str]:
    """
    Returns (fail_reason, detail). fail_reason is None if passes all checks.
    Pure computation — no API calls.
    """
    try:
        price = ind.get("price", 0)

        # 1. Market cap check
        mcap = market_cap or 0
        if mcap > 0 and mcap < MIN_MARKET_CAP:
            return "mcap", f"${mcap/1e6:.0f}M < $2B"

        # 2. Trend check — must be in a downtrend (below MA50 or MA200)
        ma50  = ind.get("ma50")
        ma200 = ind.get("ma200")
        below_ma50  = ma50  is not None and ma50  > 0 and price < ma50
        below_ma200 = ma200 is not None and ma200 > 0 and price < ma200
        if not below_ma50 and not below_ma200:
            return "trend", f"price ${price:.2f} above both MA50 ${ma50:.2f} and MA200 ${ma200:.2f} — no structural downtrend"

        # 3. Not in a sharp bounce — don't short stocks already -5%+ on the day
        if df is not None:
            close = df["close"] if "close" in df.columns else df.get("Close")
            if close is not None and len(close) >= 5:
                ret_5d = (float(close.iloc[-1]) - float(close.iloc[-5])) / float(close.iloc[-5]) * 100
                if ret_5d > 5.0:
                    return "bounce", f"5d return {ret_5d:.1f}% — bouncing, not breaking down"

        # 4. Fundamental deterioration required — not just a cheap stock
        if fundamentals is not None:
            rev_growth  = fundamentals.get("revenue_growth_pct")
            earn_growth = fundamentals.get("earnings_growth_pct")
            fcf         = fundamentals.get("free_cashflow")
            peg         = fundamentals.get("peg_ratio")

            has_red_flag = (
                (rev_growth  is not None and rev_growth  < 0) or
                (earn_growth is not None and earn_growth < 0) or
                (fcf         is not None and fcf         < 0) or
                (peg         is not None and peg         > 3)
            )
            if not has_red_flag:
                return "fundamentals", "no fundamental red flags (negative growth/FCF or PEG>3)"
        else:
            return "fundamentals", "no fundamentals data available"

        return None, "passes all checks"
    except Exception:
        return None, "error in check — passing through"
