import pandas as pd
import ta
import numpy as np


def compute_all(df: pd.DataFrame) -> dict:
    """
    Takes a yfinance OHLCV DataFrame and returns a flat dict of all indicator values.
    Returns the most recent value for each indicator.
    """
    if df.empty or len(df) < 30:
        return {}

    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # ── RSI ──────────────────────────────────────────────────────────────────
    rsi_series = ta.momentum.RSIIndicator(close, window=14).rsi()
    rsi_val = float(rsi_series.iloc[-1]) if not rsi_series.empty else 50.0

    rsi_divergence = False
    if len(close) >= 10:
        price_trend = close.iloc[-5:].is_monotonic_decreasing
        rsi_trend = rsi_series.iloc[-5:].is_monotonic_increasing
        rsi_divergence = bool(price_trend and rsi_trend)

    # ── MACD ─────────────────────────────────────────────────────────────────
    macd_ind = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
    macd_line = float(macd_ind.macd().iloc[-1]) if not macd_ind.macd().empty else 0.0
    macd_signal_val = float(macd_ind.macd_signal().iloc[-1]) if not macd_ind.macd_signal().empty else 0.0
    macd_hist = float(macd_ind.macd_diff().iloc[-1]) if not macd_ind.macd_diff().empty else 0.0
    macd_prev = float(macd_ind.macd().iloc[-2]) if len(macd_ind.macd()) > 1 else macd_line
    macd_signal_prev = float(macd_ind.macd_signal().iloc[-2]) if len(macd_ind.macd_signal()) > 1 else macd_signal_val
    macd_crossover = (macd_prev < macd_signal_prev) and (macd_line > macd_signal_val)

    # ── Rate of Change ────────────────────────────────────────────────────────
    roc_5_val = float(close.pct_change(5).iloc[-1] * 100) if len(close) > 5 else 0.0
    roc_20_val = float(close.pct_change(20).iloc[-1] * 100) if len(close) > 20 else 0.0

    # ── Moving Averages ───────────────────────────────────────────────────────
    ma20_series = ta.trend.SMAIndicator(close, window=20).sma_indicator()
    ma50_series = ta.trend.SMAIndicator(close, window=50).sma_indicator()
    ma200_series = ta.trend.SMAIndicator(close, window=200).sma_indicator()

    ma20_val = float(ma20_series.iloc[-1]) if not ma20_series.empty and not pd.isna(ma20_series.iloc[-1]) else None
    ma50_val = float(ma50_series.iloc[-1]) if not ma50_series.empty and not pd.isna(ma50_series.iloc[-1]) else None
    ma200_val = float(ma200_series.iloc[-1]) if not ma200_series.empty and not pd.isna(ma200_series.iloc[-1]) else None
    price = float(close.iloc[-1])

    golden_cross = False
    if ma20_val and ma50_val and len(ma20_series) > 1 and len(ma50_series) > 1:
        ma20_prev = float(ma20_series.iloc[-2])
        ma50_prev = float(ma50_series.iloc[-2])
        if not pd.isna(ma20_prev) and not pd.isna(ma50_prev):
            golden_cross = (ma20_prev < ma50_prev) and (ma20_val > ma50_val)

    ma50_cross_ma200 = False
    if ma50_val and ma200_val and len(ma50_series) > 1 and len(ma200_series) > 1:
        ma50_prev = float(ma50_series.iloc[-2])
        ma200_prev = float(ma200_series.iloc[-2])
        if not pd.isna(ma50_prev) and not pd.isna(ma200_prev):
            ma50_cross_ma200 = (ma50_prev < ma200_prev) and (ma50_val > ma200_val)

    # ── ADX ───────────────────────────────────────────────────────────────────
    adx_ind = ta.trend.ADXIndicator(high, low, close, window=14)
    adx_series = adx_ind.adx()
    adx_val = float(adx_series.iloc[-1]) if not adx_series.empty and not pd.isna(adx_series.iloc[-1]) else 20.0

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    bb_ind = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    bb_lower = float(bb_ind.bollinger_lband().iloc[-1])
    bb_mid = float(bb_ind.bollinger_mavg().iloc[-1])
    bb_upper = float(bb_ind.bollinger_hband().iloc[-1])
    bb_widths = bb_ind.bollinger_hband() - bb_ind.bollinger_lband()
    bb_width_pct = float(bb_widths.rank(pct=True).iloc[-1]) if not bb_widths.empty else 0.5
    bb_squeeze = bb_width_pct <= 0.20
    bb_breakout_up = price > bb_upper
    bb_breakout_down = price < bb_lower

    # ── ATR ───────────────────────────────────────────────────────────────────
    atr_series = ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range()
    atr_val = float(atr_series.iloc[-1]) if not atr_series.empty and not pd.isna(atr_series.iloc[-1]) else price * 0.02
    atr_prev = float(atr_series.iloc[-5]) if len(atr_series) >= 5 and not pd.isna(atr_series.iloc[-5]) else atr_val
    atr_rising = atr_val > atr_prev

    # ── Volume ───────────────────────────────────────────────────────────────
    avg_vol = float(volume.iloc[-20:].mean()) if len(volume) >= 20 else float(volume.mean())
    current_vol = float(volume.iloc[-1])
    volume_surge_ratio = current_vol / avg_vol if avg_vol > 0 else 1.0

    # ── OBV ───────────────────────────────────────────────────────────────────
    obv_series = ta.volume.OnBalanceVolumeIndicator(close, volume).on_balance_volume()
    obv_trend = "NEUTRAL"
    if len(obv_series) >= 5:
        obv_slope = float(obv_series.iloc[-1]) - float(obv_series.iloc[-5])
        price_slope = price - float(close.iloc[-5])
        if obv_slope > 0 and price_slope > 0:
            obv_trend = "CONFIRMING"
        elif obv_slope > 0 and price_slope <= 0:
            obv_trend = "DIVERGING_BULLISH"
        elif obv_slope < 0 and price_slope >= 0:
            obv_trend = "DIVERGING_BEARISH"
        else:
            obv_trend = "DECLINING"

    # ── VWAP (approximate) ────────────────────────────────────────────────────
    typical_price = (high + low + close) / 3
    vwap_val = float((typical_price * volume).iloc[-20:].sum() / volume.iloc[-20:].sum()) if len(df) >= 20 else price
    price_above_vwap = price > vwap_val

    # ── 52-week high ─────────────────────────────────────────────────────────
    high_52w = float(high.iloc[-252:].max()) if len(df) >= 252 else float(high.max())
    near_52w_high = price >= high_52w * 0.98
    broke_52w_high = price >= high_52w

    return {
        "price": price,
        "rsi": rsi_val,
        "rsi_divergence": rsi_divergence,
        "macd_line": macd_line,
        "macd_signal": macd_signal_val,
        "macd_hist": macd_hist,
        "macd_crossover": macd_crossover,
        "roc_5": roc_5_val,
        "roc_20": roc_20_val,
        "ma20": ma20_val,
        "ma50": ma50_val,
        "ma200": ma200_val,
        "golden_cross": golden_cross,
        "ma50_cross_ma200": ma50_cross_ma200,
        "adx": adx_val,
        "bb_lower": bb_lower,
        "bb_mid": bb_mid,
        "bb_upper": bb_upper,
        "bb_squeeze": bb_squeeze,
        "bb_breakout_up": bb_breakout_up,
        "bb_breakout_down": bb_breakout_down,
        "bb_width_pct": bb_width_pct,
        "atr": atr_val,
        "atr_rising": atr_rising,
        "volume_surge_ratio": volume_surge_ratio,
        "obv_trend": obv_trend,
        "vwap": vwap_val,
        "price_above_vwap": price_above_vwap,
        "high_52w": high_52w,
        "near_52w_high": near_52w_high,
        "broke_52w_high": broke_52w_high,
    }


def get_ma_series(close: pd.Series, window: int) -> pd.Series:
    """Helper for chart overlays."""
    return ta.trend.SMAIndicator(close, window=window).sma_indicator()
