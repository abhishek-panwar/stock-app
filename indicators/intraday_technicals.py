"""
Live tracking signals for tracked predictions.

Short/medium-term: 15-minute bars — RSI, MACD, OBV.
Long-term: daily bars — price vs MA50, consecutive closes below MA50, weekly stop logic.
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


def compute_longterm_signals(ticker: str, entry: float, stop_loss: float,
                              target_low: float, target_high: float,
                              direction: str) -> dict:
    """
    Long-term tracking signal using daily bars.
    SELL only on meaningful structural breaks — not intraday noise.

    Rules (BULLISH):
      - Price closed below MA50 for 2+ consecutive daily closes → SELL
      - Price closed below stop_loss on a daily close → SELL
      - Price is above target zone → SELL (thesis complete, take profit)

    Rules (BEARISH): mirror logic — price closed above MA50 for 2+ days,
      or above stop_loss on daily close.

    Returns empty dict on failure.
    """
    try:
        df = yf.download(ticker, period="3mo", interval="1d",
                         progress=False, auto_adjust=True)
        if df.empty or len(df) < 10:
            return {}
        df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
    except Exception:
        return {}

    close  = df["close"]
    price  = float(close.iloc[-1])

    # ── MA50 ─────────────────────────────────────────────────────────────
    ma50_series = ta.trend.SMAIndicator(close, window=50).sma_indicator()
    ma50 = float(ma50_series.iloc[-1]) if not ma50_series.empty and not ma50_series.iloc[-1] != ma50_series.iloc[-1] else None

    # Count consecutive daily closes below MA50 (bullish) or above (bearish)
    ma50_break_days = 0
    if ma50 is not None and len(ma50_series) >= 3:
        for i in range(-1, -4, -1):
            val = ma50_series.iloc[i]
            c_val = float(close.iloc[i])
            if val != val:  # NaN
                break
            if direction == "BULLISH" and c_val < float(val):
                ma50_break_days += 1
            elif direction == "BEARISH" and c_val > float(val):
                ma50_break_days += 1
            else:
                break

    # ── MA200 for context ─────────────────────────────────────────────────
    ma200_series = ta.trend.SMAIndicator(close, window=200).sma_indicator()
    ma200 = float(ma200_series.iloc[-1]) if len(ma200_series) > 0 and ma200_series.iloc[-1] == ma200_series.iloc[-1] else None

    # ── Return so far ─────────────────────────────────────────────────────
    ret_pct = None
    if entry > 0:
        if direction == "BEARISH":
            ret_pct = round((entry - price) / entry * 100, 2)
        else:
            ret_pct = round((price - entry) / entry * 100, 2)

    # ── Signal logic ─────────────────────────────────────────────────────
    sell_reasons = []
    hold_parts   = []

    # Stop loss: daily close breach (not intraday)
    stop_breached = (
        (direction == "BULLISH" and stop_loss > 0 and price < stop_loss) or
        (direction == "BEARISH" and stop_loss > 0 and price > stop_loss)
    )
    if stop_breached:
        sell_reasons.append(f"Daily close ${price:.2f} breached stop ${stop_loss:.2f}")

    # Target reached
    tgt_mid = (target_low + target_high) / 2 if target_low > 0 and target_high > 0 else 0
    target_reached = (
        (direction == "BULLISH" and target_low > 0 and price >= target_low) or
        (direction == "BEARISH" and target_high > 0 and price <= target_high)
    )
    if target_reached:
        sell_reasons.append(f"Price ${price:.2f} reached target zone — consider taking profit")

    # MA50 structural break: 2+ consecutive closes
    if ma50_break_days >= 2:
        side = "below" if direction == "BULLISH" else "above"
        sell_reasons.append(f"Price closed {side} MA50 (${ma50:.2f}) for {ma50_break_days} consecutive days — trend break")
    elif ma50 is not None:
        side = "above" if direction == "BULLISH" else "below"
        hold_parts.append(f"Price {side} MA50 ${ma50:.2f} — trend intact")

    # Build hold context
    if ret_pct is not None:
        sign = "+" if ret_pct >= 0 else ""
        hold_parts.append(f"Return so far: {sign}{ret_pct:.1f}%")
    if ma200 is not None:
        side = "above" if price > ma200 else "below"
        hold_parts.append(f"Price {side} MA200 ${ma200:.2f}")

    signal = "SELL" if sell_reasons else "HOLD"
    reason = " · ".join(sell_reasons) if sell_reasons else " · ".join(hold_parts) if hold_parts else "Thesis intact — monitoring daily closes"

    return {
        "price":           price,
        "ma50":            round(ma50, 2) if ma50 else None,
        "ma200":           round(ma200, 2) if ma200 else None,
        "ma50_break_days": ma50_break_days,
        "stop_breached":   stop_breached,
        "target_reached":  target_reached,
        "ret_pct":         ret_pct,
        "signal":          signal,
        "reason":          reason,
    }


def compute_tracking_signal(ticker: str, timeframe: str, entry: float,
                             stop_loss: float, target_low: float,
                             target_high: float, direction: str) -> dict:
    """Dispatcher: routes to intraday (short/medium) or daily (long) logic."""
    if timeframe == "long":
        return compute_longterm_signals(ticker, entry, stop_loss, target_low, target_high, direction)
    return compute_intraday_signals(ticker)
