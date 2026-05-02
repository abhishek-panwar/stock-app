"""
Short-term bearish universe builder — Type 1: overbought reversal setups.

Target: stocks that have had a sustained multi-day run and are showing
exhaustion signals. NOT stocks already in a downtrend.

Selection criteria:
  - Yahoo day_gainers (today's top gainers)
  - Alpha Vantage top_gainers
  - Stocks that have gained 8%+ over the last 5 trading days (recent run filter)
  - RSI pre-filter: RSI >= 65 (approaching or in overbought territory)
  - Price extended: price > MA20 by >= 4% (stretched from mean)
  - Market cap floor: $2B
  - Explicitly excludes crypto and commodities (these trend, not revert)
"""

import os
MIN_MARKET_CAP = 2_000_000_000  # $2B

_EXCLUDE_TICKERS = {
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "BNB-USD",
    "ADA-USD", "AVAX-USD", "DOGE-USD", "LINK-USD", "DOT-USD",
    "GLD", "IAU", "GDX", "GDXJ", "GOLD", "SLV", "PPLT", "USO", "UNG",
}


def get_bearish_hot_tickers() -> list[str]:
    """
    Returns recent gainers — the raw pool of overbought reversal candidates.
    Includes today's day_gainers + Alpha Vantage top_gainers.
    Crypto and commodities excluded — they trend, mean reversion is unreliable.
    """
    import requests

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

    av_key = os.environ.get("ALPHA_VANTAGE_KEY", "")
    if av_key:
        try:
            r = requests.get(
                f"https://www.alphavantage.co/query?function=TOP_GAINERS_LOSERS&apikey={av_key}",
                timeout=10,
            )
            for item in r.json().get("top_gainers", []):
                sym = item.get("ticker", "")
                if sym and "=" not in sym and "/" not in sym:
                    raw.add(sym.upper())
        except Exception:
            pass

    # Remove excluded asset classes
    raw -= _EXCLUDE_TICKERS
    tickers = sorted(raw)
    print(f"  Bearish candidate pool: {len(tickers)} recent gainers")
    return tickers


def build_bearish_universe(raw_tickers: list[str]) -> list[dict]:
    """
    Filters raw gainers down to genuine overbought reversal setups.
    Returns list of {"ticker": str, "source": "bearish_candidate"}.

    Passes if ALL of:
      1. Market cap >= $2B
      2. 5-day return >= 8% (has had a real run)
      3. RSI >= 65 (approaching or in overbought)
      4. Price > MA20 by >= 4% (extended from mean, prime for reversion)
    """
    universe = []
    filtered = {"mcap": 0, "run": 0, "rsi": 0, "extension": 0}

    for ticker in raw_tickers:
        if ticker in _EXCLUDE_TICKERS:
            continue

        result = _check_reversal_setup(ticker)
        if result["fail_reason"]:
            filtered[result["fail_reason"]] = filtered.get(result["fail_reason"], 0) + 1
            print(f"  {ticker} excluded from bearish — {result['fail_reason']}: {result['detail']}")
            continue

        universe.append({"ticker": ticker, "source": "bearish_candidate"})

    print(
        f"  Bearish universe: {len(universe)} stocks "
        f"(filtered: {filtered['mcap']} mcap, {filtered['run']} run<8%, "
        f"{filtered['rsi']} rsi<65, {filtered['extension']} extension<4%)"
    )
    return universe


def _check_reversal_setup(ticker: str) -> dict:
    """
    Returns {"fail_reason": str|None, "detail": str}.
    fail_reason is None if the ticker passes all checks.
    """
    try:
        import yfinance as yf
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            t = yf.Ticker(ticker)
            info = t.info
            df   = t.history(period="3mo")

        if df.empty or len(df) < 20:
            return {"fail_reason": None, "detail": "insufficient data — passing through"}

        # 1. Market cap check
        mcap = int(info.get("marketCap") or 0)
        if mcap > 0 and mcap < MIN_MARKET_CAP:
            return {"fail_reason": "mcap", "detail": f"${mcap/1e6:.0f}M < $2B"}

        close = df["Close"]
        price = float(close.iloc[-1])

        # 2. 5-day run check — must have gained >= 8%
        if len(close) >= 5:
            ret_5d = (price - float(close.iloc[-5])) / float(close.iloc[-5]) * 100
            if ret_5d < 8.0:
                return {"fail_reason": "run", "detail": f"5d return {ret_5d:.1f}% < 8%"}

        # 3. RSI check — must be >= 65
        try:
            import ta
            rsi = float(ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1])
        except Exception:
            rsi = 50.0
        if rsi < 65:
            return {"fail_reason": "rsi", "detail": f"RSI {rsi:.1f} < 65"}

        # 4. Extension from MA20 — must be >= 4% above MA20
        if len(close) >= 20:
            ma20 = float(close.rolling(20).mean().iloc[-1])
            extension_pct = (price - ma20) / ma20 * 100
            if extension_pct < 4.0:
                return {"fail_reason": "extension", "detail": f"only {extension_pct:.1f}% above MA20"}

        return {"fail_reason": None, "detail": "passes all checks"}
    except Exception:
        return {"fail_reason": None, "detail": "error in check — passing through"}
