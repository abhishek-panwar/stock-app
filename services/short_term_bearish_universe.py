"""
Short-term bearish universe builder — Type 1: overbought reversal setups.

get_bearish_hot_tickers()  — HTTP only, returns raw candidate ticker list
filter_bearish_universe()  — pure computation from pre-fetched ticker_data
                             (no live API calls — reads ind from the shared fetch store)

Selection criteria (evaluated from pre-fetched data):
  - Market cap >= $2B
  - 5-day OR 10-day return >= 8% (catches multi-week runs, not just single-day gaps)
  - RSI >= 70 (genuinely overbought — required for reversal thesis)
  - Crypto and commodities excluded (trend, not revert)
  - Nasdaq 100 tickers with RSI >= 70 added as additional overlay
"""

import os

MIN_MARKET_CAP = 2_000_000_000  # $2B

_EXCLUDE_TICKERS = {
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "BNB-USD",
    "ADA-USD", "AVAX-USD", "DOGE-USD", "LINK-USD", "DOT-USD",
    "GLD", "IAU", "GDX", "GDXJ", "GOLD", "SLV", "PPLT", "USO", "UNG",
}


def get_bearish_hot_tickers(av_gainers: set[str] | None = None) -> list[str]:
    """
    Returns raw bearish candidate tickers: today's top gainers + Nasdaq 100.
    HTTP only — no yfinance, no Finnhub.

    av_gainers: pass result of fetch_alpha_vantage_gainers() to avoid double-calling AV.
    Nasdaq 100 tickers are included so multi-week extended setups aren't missed.
    """
    import requests
    from services.screener_service import load_nasdaq100

    raw: set[str] = set()
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?scrIds=day_gainers&count=25",
            headers=headers, timeout=10,
        )
        quotes = r.json()["finance"]["result"][0]["quotes"]
        for q in quotes:
            sym = q.get("symbol", "")
            if sym and "=" not in sym and "/" not in sym:
                raw.add(sym.upper())
    except Exception:
        pass

    # AV top_gainers are also bearish candidates
    if av_gainers:
        raw |= av_gainers

    # Nasdaq 100 overlay — catches stocks extended over weeks, not just today's gappers
    try:
        raw |= set(load_nasdaq100())
    except Exception:
        pass

    raw -= _EXCLUDE_TICKERS
    tickers = sorted(raw)
    print(f"  Bearish candidate pool: {len(tickers)} raw candidates (gainers + Nasdaq 100)")
    return tickers


def filter_bearish_universe(
    raw_tickers: list[str],
    ticker_data: dict,
) -> tuple[list[dict], set[str]]:
    """
    Filters raw gainers down to genuine overbought reversal setups.
    Uses pre-fetched data — zero live API calls.

    Returns:
      (universe, bearish_ticker_set)
      universe entries: {"ticker": str, "source": "bearish_candidate"}
      bearish_ticker_set: set of tickers assigned to bearish, for bullish exclusion
    """
    universe = []
    bearish_tickers: set[str] = set()
    filtered = {"mcap": 0, "run": 0, "rsi": 0, "no_data": 0}

    for ticker in raw_tickers:
        if ticker in _EXCLUDE_TICKERS:
            continue

        if ticker not in ticker_data:
            filtered["no_data"] += 1
            # Not silently dropped — logged during fetch phase
            continue

        data = ticker_data[ticker]
        ind  = data["ind"]
        df   = data["df"]

        fail_reason, detail = _check_reversal_setup_from_data(data, ind, df)
        if fail_reason:
            filtered[fail_reason] = filtered.get(fail_reason, 0) + 1
            print(f"  {ticker} excluded from bearish — {fail_reason}: {detail}")
            continue

        universe.append({"ticker": ticker, "source": "bearish_candidate"})
        bearish_tickers.add(ticker)

    print(
        f"  Bearish universe: {len(universe)} stocks "
        f"(filtered: {filtered['mcap']} mcap, {filtered['run']} run<8%, "
        f"{filtered['rsi']} rsi<70, {filtered['no_data']} no data)"
    )
    return universe, bearish_tickers


def _check_reversal_setup_from_data(data: dict, ind: dict, df) -> tuple[str | None, str]:
    """
    Returns (fail_reason, detail). fail_reason is None if the ticker passes all checks.
    Pure computation from pre-fetched data — no API calls.
    """
    try:
        price = ind.get("price", 0)

        # 1. Market cap check
        mcap = data.get("market_cap") or 0
        if mcap > 0 and mcap < MIN_MARKET_CAP:
            return "mcap", f"${mcap/1e6:.0f}M < $2B"

        # 2. Run check — 5d OR 10d return >= 8%
        # 10d catches multi-week extended setups that aren't gapping today
        close = df["close"] if "close" in df.columns else df["Close"]
        ret_5d  = None
        ret_10d = None
        if len(close) >= 5:
            ret_5d  = (float(close.iloc[-1]) - float(close.iloc[-5]))  / float(close.iloc[-5])  * 100
        if len(close) >= 10:
            ret_10d = (float(close.iloc[-1]) - float(close.iloc[-10])) / float(close.iloc[-10]) * 100
        best_run = max(r for r in [ret_5d, ret_10d] if r is not None) if (ret_5d is not None or ret_10d is not None) else 0
        if best_run < 8.0:
            return "run", f"5d={ret_5d:.1f}% 10d={ret_10d:.1f}% both < 8%"

        # 3. RSI check — must be >= 70 (genuinely overbought)
        rsi = ind.get("rsi", 50)
        if rsi < 70:
            return "rsi", f"RSI {rsi:.1f} < 70"

        return None, "passes all checks"
    except Exception:
        return None, "error in check — passing through"
