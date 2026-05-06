"""
Intraday technicals for live tracking signals.
Runs on 15-minute bars (yfinance, ~15-min delayed).
Only computes what's needed for HOLD/SELL: RSI, MACD, OBV.
"""
import pandas as pd
import ta
import yfinance as yf


def fetch_15m_bars(ticker: str) -> pd.DataFrame:
    try:
        df = yf.download(ticker, period="5d", interval="15m",
                         progress=False, auto_adjust=True)
        if df.empty or len(df) < 10:
            return pd.DataFrame()
        df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
        return df
    except Exception:
        return pd.DataFrame()


def compute_intraday_signals(ticker: str) -> dict:
    """
    Returns a dict with:
      rsi, macd_bullish, macd_hist_shrinking, obv_bearish_bars,
      price, signal (HOLD/SELL), reason
    Returns empty dict on failure.
    """
    df = fetch_15m_bars(ticker)
    if df.empty:
        return {}

    close  = df["close"]
    volume = df["volume"]
    price  = float(close.iloc[-1])

    # ── RSI ──────────────────────────────────────────────────────────────
    rsi_series = ta.momentum.RSIIndicator(close, window=14).rsi()
    rsi = float(rsi_series.iloc[-1]) if not rsi_series.empty else 50.0

    # ── MACD ─────────────────────────────────────────────────────────────
    macd_ind  = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
    macd_line = float(macd_ind.macd().iloc[-1])
    macd_sig  = float(macd_ind.macd_signal().iloc[-1])
    macd_hist = float(macd_ind.macd_diff().iloc[-1])
    macd_hist_prev = float(macd_ind.macd_diff().iloc[-2]) if len(macd_ind.macd_diff()) > 1 else macd_hist
    macd_bullish         = macd_line > macd_sig
    macd_hist_shrinking  = macd_hist < macd_hist_prev and macd_hist > 0

    # ── OBV — count consecutive bearish bars ─────────────────────────────
    obv_series = ta.volume.OnBalanceVolumeIndicator(close, volume).on_balance_volume()
    obv_bearish_bars = 0
    for i in range(-1, -5, -1):
        if len(obv_series) >= abs(i) + 1:
            if float(obv_series.iloc[i]) < float(obv_series.iloc[i - 1]):
                obv_bearish_bars += 1
            else:
                break

    # ── Signal logic ─────────────────────────────────────────────────────
    sell_reasons = []
    if rsi > 75:
        sell_reasons.append(f"RSI {rsi:.0f} — overbought on 15m")
    if not macd_bullish:
        sell_reasons.append("MACD bearish on 15m")
    elif macd_hist_shrinking:
        sell_reasons.append("MACD histogram shrinking — momentum fading")
    if obv_bearish_bars >= 2:
        sell_reasons.append(f"OBV declining {obv_bearish_bars} consecutive bars")

    # Need at least 2 sell signals to trigger SELL (avoid single-bar noise)
    signal = "SELL" if len(sell_reasons) >= 2 else "HOLD"
    if signal == "HOLD":
        hold_parts = []
        if rsi <= 70:
            hold_parts.append(f"RSI {rsi:.0f} — healthy")
        if macd_bullish and not macd_hist_shrinking:
            hold_parts.append("MACD bullish, expanding")
        if obv_bearish_bars == 0:
            hold_parts.append("OBV confirming")
        reason = " · ".join(hold_parts) if hold_parts else "No strong sell signal"
    else:
        reason = " · ".join(sell_reasons)

    return {
        "price":               price,
        "rsi":                 round(rsi, 1),
        "macd_bullish":        macd_bullish,
        "macd_hist_shrinking": macd_hist_shrinking,
        "obv_bearish_bars":    obv_bearish_bars,
        "signal":              signal,
        "reason":              reason,
    }
