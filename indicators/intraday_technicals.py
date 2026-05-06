"""
Live tracking signals for tracked predictions.

Short/medium-term: 1-hour bars — RSI, MACD, OBV.
  Using 1h instead of 15m: for 3-8 day swing holds, hourly closes filter out
  intraday noise that causes excessive signal flipping on 15m bars.
Long-term: daily bars — price vs MA50, consecutive closes below MA50.

Conviction levels:
  STRONG_SELL — all 3 indicators bearish AND RSI overbought (>75) or OBV declining 4+ bars
  SELL        — all 3 indicators bearish (RSI >70 or MACD bearish or OBV declining 4+ bars)
  HOLD        — fewer than 3 sell signals firing
  STRONG_HOLD — all 3 indicators bullish AND RSI in healthy zone (40-65) AND MACD expanding
"""
import pandas as pd
import ta
import yfinance as yf


def fetch_1h_bars(ticker: str) -> pd.DataFrame:
    try:
        df = yf.download(ticker, period="10d", interval="1h",
                         progress=False, auto_adjust=True)
        if df.empty or len(df) < 20:
            return pd.DataFrame()
        df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
        return df
    except Exception:
        return pd.DataFrame()


def compute_intraday_signals(ticker: str, macro: dict | None = None) -> dict:
    """
    Returns a dict with:
      rsi, macd_bullish, macd_hist_shrinking, obv_bearish_bars,
      price, signal (HOLD/SELL), conviction (STRONG_SELL/SELL/HOLD/STRONG_HOLD), reason
    Returns empty dict on failure.
    macro: optional dict with spy_return_pct, vix, spy_ok, vix_ok
    """
    df = fetch_1h_bars(ticker)
    if df.empty:
        return {}
    macro = macro or {}

    close  = df["close"]
    volume = df["volume"]
    price  = float(close.iloc[-1])

    # ── RSI ──────────────────────────────────────────────────────────────
    rsi_series = ta.momentum.RSIIndicator(close, window=14).rsi()
    rsi = float(rsi_series.iloc[-1]) if not rsi_series.empty else 50.0

    # ── MACD ─────────────────────────────────────────────────────────────
    macd_ind       = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
    macd_line      = float(macd_ind.macd().iloc[-1])
    macd_sig_val   = float(macd_ind.macd_signal().iloc[-1])
    macd_hist      = float(macd_ind.macd_diff().iloc[-1])
    macd_hist_prev = float(macd_ind.macd_diff().iloc[-2]) if len(macd_ind.macd_diff()) > 1 else macd_hist
    macd_bullish        = macd_line > macd_sig_val
    macd_hist_expanding = macd_hist > macd_hist_prev and macd_hist > 0
    macd_hist_shrinking = macd_hist < macd_hist_prev and macd_hist > 0

    # ── OBV — count consecutive bearish/bullish bars ──────────────────────
    obv_series = ta.volume.OnBalanceVolumeIndicator(close, volume).on_balance_volume()
    obv_bearish_bars = 0
    for i in range(-1, -6, -1):
        if len(obv_series) >= abs(i) + 1:
            if float(obv_series.iloc[i]) < float(obv_series.iloc[i - 1]):
                obv_bearish_bars += 1
            else:
                break
    obv_bullish_bars = 0
    for i in range(-1, -6, -1):
        if len(obv_series) >= abs(i) + 1:
            if float(obv_series.iloc[i]) > float(obv_series.iloc[i - 1]):
                obv_bullish_bars += 1
            else:
                break

    # ── Sell signals — all 3 must fire (OBV threshold: 4 consecutive bars) ─
    rsi_sell        = rsi > 70
    macd_sell       = not macd_bullish
    obv_sell        = obv_bearish_bars >= 4

    sell_count = sum([rsi_sell, macd_sell, obv_sell])

    # ── Hold / buy signals ────────────────────────────────────────────────
    rsi_healthy     = 40 <= rsi <= 65
    obv_confirming  = obv_bearish_bars == 0

    # ── Macro context ─────────────────────────────────────────────────────
    spy_ret = macro.get("spy_return_pct", 0.0)
    vix     = macro.get("vix", 20.0)
    spy_ok  = macro.get("spy_ok", False)
    vix_ok  = macro.get("vix_ok", False)

    macro_bearish = (spy_ok and spy_ret <= -1.5) or (vix_ok and vix > 25)
    macro_bullish = (spy_ok and spy_ret > 0) and (vix_ok and vix < 20)

    # ── Signal + conviction ───────────────────────────────────────────────
    if sell_count == 3:
        signal = "SELL"
        conviction = "STRONG_SELL" if macro_bearish else "SELL"
    else:
        signal = "HOLD"
        if rsi_healthy and macd_bullish and macd_hist_expanding and obv_confirming and macro_bullish:
            conviction = "STRONG_HOLD"
        else:
            conviction = "HOLD"

    # ── Reason text ──────────────────────────────────────────────────────
    if signal == "SELL":
        parts = []
        if rsi_sell:
            parts.append(f"RSI {rsi:.0f} — overbought on 1h")
        if macd_sell:
            parts.append("MACD bearish on 1h")
        if obv_sell:
            parts.append(f"OBV declining {obv_bearish_bars} consecutive bars")
        reason = " · ".join(parts)
    else:
        parts = []
        if rsi_healthy:
            parts.append(f"RSI {rsi:.0f} — healthy zone")
        elif rsi <= 40:
            parts.append(f"RSI {rsi:.0f} — oversold, potential bounce")
        else:
            parts.append(f"RSI {rsi:.0f}")
        if macd_bullish and macd_hist_expanding:
            parts.append("MACD bullish, momentum building")
        elif macd_bullish:
            parts.append("MACD bullish")
        else:
            parts.append("MACD weak — only 1h signal, not enough to exit")
        if obv_confirming:
            parts.append("OBV confirming")
        elif obv_bearish_bars > 0:
            parts.append(f"OBV soft ({obv_bearish_bars} bars) — not enough to exit")
        reason = " · ".join(parts)

    return {
        "price":               price,
        "rsi":                 round(rsi, 1),
        "macd_bullish":        macd_bullish,
        "macd_hist_shrinking": macd_hist_shrinking,
        "obv_bearish_bars":    obv_bearish_bars,
        "signal":              signal,
        "conviction":          conviction,
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
                             target_high: float, direction: str,
                             macro: dict | None = None) -> dict:
    """Dispatcher: routes to intraday (short/medium) or daily (long) logic."""
    if timeframe == "long":
        return compute_longterm_signals(ticker, entry, stop_loss, target_low, target_high, direction)
    return compute_intraday_signals(ticker, macro=macro)
