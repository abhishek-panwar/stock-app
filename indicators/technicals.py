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
    rsi_bearish_divergence = False
    if len(close) >= 10:
        price_5 = close.iloc[-5:]
        rsi_5   = rsi_series.iloc[-5:]
        price_net_down = float(price_5.iloc[-1]) < float(price_5.iloc[0])
        price_net_up   = float(price_5.iloc[-1]) > float(price_5.iloc[0])
        rsi_net_up     = float(rsi_5.iloc[-1])   > float(rsi_5.iloc[0])
        rsi_net_down   = float(rsi_5.iloc[-1])   < float(rsi_5.iloc[0])
        # Bullish divergence: price net lower but RSI net higher (hidden demand)
        rsi_divergence = bool(price_net_down and rsi_net_up)

    # Bearish divergence over 10 bars: price made a higher high but RSI made a lower high
    # 10-bar window matches the run detection window and reduces noise vs 5 bars
    if len(close) >= 10:
        price_10 = close.iloc[-10:]
        rsi_10   = rsi_series.iloc[-10:]
        price_net_up_10 = float(price_10.iloc[-1]) > float(price_10.iloc[0])
        rsi_net_down_10 = float(rsi_10.iloc[-1])   < float(rsi_10.iloc[0])
        rsi_bearish_divergence = bool(price_net_up_10 and rsi_net_down_10)

    # ── MACD ─────────────────────────────────────────────────────────────────
    macd_ind = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
    macd_line = float(macd_ind.macd().iloc[-1]) if not macd_ind.macd().empty else 0.0
    macd_signal_val = float(macd_ind.macd_signal().iloc[-1]) if not macd_ind.macd_signal().empty else 0.0
    macd_hist = float(macd_ind.macd_diff().iloc[-1]) if not macd_ind.macd_diff().empty else 0.0
    macd_hist_prev = float(macd_ind.macd_diff().iloc[-2]) if len(macd_ind.macd_diff()) > 1 else macd_hist
    macd_prev = float(macd_ind.macd().iloc[-2]) if len(macd_ind.macd()) > 1 else macd_line
    macd_signal_prev = float(macd_ind.macd_signal().iloc[-2]) if len(macd_ind.macd_signal()) > 1 else macd_signal_val
    macd_crossover = (macd_prev < macd_signal_prev) and (macd_line > macd_signal_val)
    macd_crossover_bearish = (macd_prev > macd_signal_prev) and (macd_line < macd_signal_val)

    # Bullish crossover within last 3 bars
    macd_crossover_recent = False
    _ml = macd_ind.macd()
    _ms = macd_ind.macd_signal()
    if len(_ml) >= 4:
        for _i in range(2, 4):
            if (float(_ml.iloc[-_i - 1]) < float(_ms.iloc[-_i - 1]) and
                    float(_ml.iloc[-_i]) > float(_ms.iloc[-_i])):
                macd_crossover_recent = True
                break

    # ── Rate of Change ────────────────────────────────────────────────────────
    roc_5_val  = float(close.pct_change(5).iloc[-1]  * 100) if len(close) > 5  else 0.0
    roc_10_val = float(close.pct_change(10).iloc[-1] * 100) if len(close) > 10 else 0.0
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
    # True if price touched or exceeded BB upper in any of the last 5 bars
    bb_upper_series = bb_ind.bollinger_hband()
    bb_touched_upper = bool((high.iloc[-5:].values >= bb_upper_series.iloc[-5:].values).any()) if len(high) >= 5 else False

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

    # ── Distribution days (bearish) ───────────────────────────────────────────
    # A distribution day = price closed DOWN on volume > 20-day avg
    # Count over last 10 bars — 3+ = institutional selling pattern
    distribution_days = 0
    if len(close) >= 10 and avg_vol > 0:
        for i in range(-10, 0):
            if float(close.iloc[i]) < float(close.iloc[i - 1]) and float(volume.iloc[i]) > avg_vol:
                distribution_days += 1

    # ── MA50 slope ────────────────────────────────────────────────────────────
    # Rising MA50 = strong uptrend — mean reversion thesis is weaker
    ma50_slope_rising = False
    if ma50_series is not None and len(ma50_series) >= 10:
        ma50_10ago = ma50_series.iloc[-10]
        if not pd.isna(ma50_10ago) and ma50_val is not None:
            ma50_slope_rising = float(ma50_val) > float(ma50_10ago)

    # ── Candlestick signals ───────────────────────────────────────────────────
    bearish_engulfing    = False
    shooting_star        = False
    upper_wick_rejection = False
    bullish_engulfing    = False

    if len(df) >= 2:
        o1, h1, l1, c1 = float(df["open"].iloc[-2]), float(high.iloc[-2]), float(low.iloc[-2]), float(close.iloc[-2])
        o0, h0, l0, c0 = float(df["open"].iloc[-1]), float(high.iloc[-1]), float(low.iloc[-1]), float(close.iloc[-1])
        body0 = abs(c0 - o0)
        candle_range0 = h0 - l0 if h0 > l0 else 1e-9

        # Bearish engulfing: yesterday bullish, today bearish body fully covers yesterday's body
        if c1 > o1 and c0 < o0 and o0 >= c1 and c0 <= o1:
            bearish_engulfing = True

        # Bullish engulfing: yesterday bearish, today bullish body fully covers yesterday's body
        if c1 < o1 and c0 > o0 and c0 >= o1 and o0 <= c1:
            bullish_engulfing = True

        # Shooting star: long upper wick ≥ 2× body, tiny lower wick
        upper_wick0 = h0 - max(o0, c0)
        lower_wick0 = min(o0, c0) - l0
        if body0 > 0 and upper_wick0 >= 2 * body0 and lower_wick0 <= body0 * 0.3:
            shooting_star = True

        # Upper wick rejection: upper wick > 40% of candle range
        if upper_wick0 / candle_range0 > 0.40:
            upper_wick_rejection = True

    # ── Volatility compression (NR7) ─────────────────────────────────────────
    # NR7: today's high-low range is the narrowest of the last 7 bars
    nr7 = False
    if len(df) >= 7:
        ranges = [float(high.iloc[i]) - float(low.iloc[i]) for i in range(-7, 0)]
        nr7 = ranges[-1] == min(ranges) and ranges[-1] > 0

    # ── Pullback quality signals ──────────────────────────────────────────────
    # higher_low must be computed before near_ma20_bounce which depends on it
    higher_low = False
    if len(low) >= 6:
        higher_low = float(low.iloc[-1]) > float(low.iloc[-6])

    # near_ma20_bounce: tight pullback to MA20 (within 1.5% above it) + candle confirmation
    # 3% was too loose — weak drift qualified. Require structure confirmation.
    _ma20_ext = (price - ma20_val) / ma20_val * 100 if ma20_val else 999
    near_ma20_bounce = (
        ma20_val is not None and
        0 <= _ma20_ext <= 1.5 and
        (bullish_engulfing or higher_low)
    )

    # ── Blow-off top ──────────────────────────────────────────────────────────
    # 3 consecutive candles with accelerating bodies + volume spike + upper wick on last bar
    blowoff_top = False
    if len(df) >= 4 and "open" in df.columns:
        bodies = [abs(float(close.iloc[i]) - float(df["open"].iloc[i])) for i in range(-4, 0)]
        bodies_accelerating = bodies[1] > bodies[0] and bodies[2] > bodies[1] and bodies[3] > bodies[2]
        vol_spike = float(volume.iloc[-1]) > avg_vol * 1.5
        last_upper_wick = float(high.iloc[-1]) - max(float(close.iloc[-1]), float(df["open"].iloc[-1]))
        last_body = bodies[-1]
        wick_rejection = last_body > 0 and last_upper_wick >= last_body * 0.5
        blowoff_top = bool(bodies_accelerating and vol_spike and wick_rejection)

    # ── Gap up + holds ────────────────────────────────────────────────────────
    # Gap up: today's open > yesterday's close by >1%, and price held above open
    gap_up_holds = False
    if len(df) >= 2 and "open" in df.columns:
        prev_close = float(close.iloc[-2])
        today_open = float(df["open"].iloc[-1])
        if prev_close > 0 and today_open > prev_close * 1.01 and price >= today_open:
            gap_up_holds = True

    # ── Dollar volume (liquidity) ─────────────────────────────────────────────
    avg_dollar_volume = avg_vol * price if avg_vol > 0 and price > 0 else 0

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
        "rsi_bearish_divergence": rsi_bearish_divergence,
        "macd_line": macd_line,
        "macd_signal": macd_signal_val,
        "macd_hist": macd_hist,
        "macd_hist_prev": macd_hist_prev,
        "macd_crossover": macd_crossover,
        "macd_crossover_recent": macd_crossover_recent,
        "macd_crossover_bearish": macd_crossover_bearish,
        "roc_5": roc_5_val,
        "roc_10": roc_10_val,
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
        "bb_touched_upper": bb_touched_upper,
        "bb_width_pct": bb_width_pct,
        "atr": atr_val,
        "atr_rising": atr_rising,
        "volume_surge_ratio": volume_surge_ratio,
        "obv_trend": obv_trend,
        "distribution_days": distribution_days,
        "ma50_slope_rising": ma50_slope_rising,
        "bearish_engulfing": bearish_engulfing,
        "bullish_engulfing": bullish_engulfing,
        "shooting_star": shooting_star,
        "upper_wick_rejection": upper_wick_rejection,
        "nr7": nr7,
        "near_ma20_bounce": near_ma20_bounce,
        "higher_low": higher_low,
        "blowoff_top": blowoff_top,
        "gap_up_holds": gap_up_holds,
        "avg_dollar_volume": avg_dollar_volume,
        "vwap": vwap_val,
        "price_above_vwap": price_above_vwap,
        "high_52w": high_52w,
        "near_52w_high": near_52w_high,
        "broke_52w_high": broke_52w_high,
    }


def get_ma_series(close: pd.Series, window: int) -> pd.Series:
    """Helper for chart overlays."""
    return ta.trend.SMAIndicator(close, window=window).sma_indicator()
