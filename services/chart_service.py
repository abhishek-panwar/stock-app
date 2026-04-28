"""
TradingView Lightweight Charts helper.
Builds chart configs for renderLightweightCharts().
"""
import pandas as pd
import ta
from indicators.technicals import get_ma_series


def build_stock_chart(df: pd.DataFrame, prediction: dict = None,
                      ticker: str = "", height: int = 460) -> list:
    """
    Returns a list of chart dicts for renderLightweightCharts().
    Layout: candlestick + MAs + target/stop markers (top),
            volume histogram (middle),
            RSI line (bottom).
    """
    if df.empty or len(df) < 15:
        return []

    df = df.copy()
    df.index = pd.to_datetime(df.index)

    # ── Compute indicators ────────────────────────────────────────────────────
    ma20  = get_ma_series(df["close"], 20)
    ma50  = get_ma_series(df["close"], 50)
    rsi   = ta.momentum.RSIIndicator(df["close"], window=14).rsi()
    bb    = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)

    def to_ts(dt):
        """Convert pandas Timestamp to Unix epoch int (seconds)."""
        return int(pd.Timestamp(dt).timestamp())

    # ── Candlestick data ──────────────────────────────────────────────────────
    candles = [
        {
            "time": to_ts(idx),
            "open":  round(float(row["open"]),  4),
            "high":  round(float(row["high"]),  4),
            "low":   round(float(row["low"]),   4),
            "close": round(float(row["close"]), 4),
        }
        for idx, row in df.iterrows()
        if not any(pd.isna([row["open"], row["high"], row["low"], row["close"]]))
    ]

    # ── MA lines ──────────────────────────────────────────────────────────────
    ma20_data = [
        {"time": to_ts(idx), "value": round(float(v), 4)}
        for idx, v in ma20.items() if not pd.isna(v)
    ]
    ma50_data = [
        {"time": to_ts(idx), "value": round(float(v), 4)}
        for idx, v in ma50.items() if not pd.isna(v)
    ]

    # ── Bollinger Band lines ──────────────────────────────────────────────────
    bb_upper_data = [
        {"time": to_ts(idx), "value": round(float(v), 4)}
        for idx, v in bb.bollinger_hband().items() if not pd.isna(v)
    ]
    bb_lower_data = [
        {"time": to_ts(idx), "value": round(float(v), 4)}
        for idx, v in bb.bollinger_lband().items() if not pd.isna(v)
    ]

    # ── Target / stop markers on last candle ──────────────────────────────────
    markers = []
    if prediction:
        last_ts = to_ts(df.index[-1])
        direction = prediction.get("direction", "NEUTRAL")
        target_low = prediction.get("target_low")
        stop_loss  = prediction.get("stop_loss")
        entry      = prediction.get("price_at_prediction")

        if direction == "BULLISH":
            if entry:
                markers.append({"time": last_ts, "position": "belowBar",
                                 "color": "#f0b429", "shape": "arrowUp",
                                 "text": f"Entry ${entry:.2f}"})
            if target_low:
                markers.append({"time": last_ts, "position": "aboveBar",
                                 "color": "#26a641", "shape": "arrowUp",
                                 "text": f"Target ${target_low:.2f}"})
            if stop_loss:
                markers.append({"time": last_ts, "position": "belowBar",
                                 "color": "#e74c3c", "shape": "arrowDown",
                                 "text": f"Stop ${stop_loss:.2f}"})
        elif direction == "BEARISH":
            if entry:
                markers.append({"time": last_ts, "position": "aboveBar",
                                 "color": "#f0b429", "shape": "arrowDown",
                                 "text": f"Entry ${entry:.2f}"})
            if target_low:
                markers.append({"time": last_ts, "position": "belowBar",
                                 "color": "#26a641", "shape": "arrowDown",
                                 "text": f"Target ${target_low:.2f}"})
            if stop_loss:
                markers.append({"time": last_ts, "position": "aboveBar",
                                 "color": "#e74c3c", "shape": "arrowUp",
                                 "text": f"Stop ${stop_loss:.2f}"})

    # ── Volume data ───────────────────────────────────────────────────────────
    volume_data = [
        {
            "time":  to_ts(idx),
            "value": float(row["volume"]),
            "color": "#26a64180" if float(row["close"]) >= float(row["open"]) else "#e74c3c80",
        }
        for idx, row in df.iterrows()
        if not pd.isna(row["volume"])
    ]

    # ── RSI data ──────────────────────────────────────────────────────────────
    rsi_data = [
        {"time": to_ts(idx), "value": round(float(v), 2)}
        for idx, v in rsi.items() if not pd.isna(v)
    ]

    # ── Common chart options ──────────────────────────────────────────────────
    common_layout = {
        "layout": {
            "background": {"type": "solid", "color": "#0e1117"},
            "textColor": "#d1d4dc",
        },
        "grid": {
            "vertLines": {"color": "rgba(255,255,255,0.05)"},
            "horzLines": {"color": "rgba(255,255,255,0.05)"},
        },
        "crosshair": {"mode": 1},
        "timeScale": {
            "borderColor": "rgba(255,255,255,0.1)",
            "timeVisible": True,
            "secondsVisible": False,
        },
        "rightPriceScale": {"borderColor": "rgba(255,255,255,0.1)"},
    }

    # ── Chart 1: Price ────────────────────────────────────────────────────────
    price_series = [
        {
            "type": "Candlestick",
            "data": candles,
            "options": {
                "upColor":        "#26a641",
                "downColor":      "#e74c3c",
                "borderUpColor":  "#26a641",
                "borderDownColor":"#e74c3c",
                "wickUpColor":    "#26a641",
                "wickDownColor":  "#e74c3c",
            },
            "markers": markers,
        },
        {
            "type": "Line",
            "data": ma20_data,
            "options": {"color": "#f39c12", "lineWidth": 1, "title": "MA20"},
        },
        {
            "type": "Line",
            "data": ma50_data,
            "options": {"color": "#3498db", "lineWidth": 1, "title": "MA50"},
        },
        {
            "type": "Line",
            "data": bb_upper_data,
            "options": {"color": "rgba(150,150,255,0.45)", "lineWidth": 1,
                        "lineStyle": 2, "title": "BB Upper"},
        },
        {
            "type": "Line",
            "data": bb_lower_data,
            "options": {"color": "rgba(150,150,255,0.45)", "lineWidth": 1,
                        "lineStyle": 2, "title": "BB Lower"},
        },
    ]

    price_chart = {
        "chart": {**common_layout, "height": int(height * 0.55)},
        "series": price_series,
    }

    # ── Chart 2: Volume ───────────────────────────────────────────────────────
    volume_chart = {
        "chart": {**common_layout, "height": int(height * 0.20)},
        "series": [
            {
                "type": "Histogram",
                "data": volume_data,
                "options": {"priceFormat": {"type": "volume"}, "color": "#26a641"},
            }
        ],
    }

    # ── Chart 3: RSI ──────────────────────────────────────────────────────────
    rsi_chart = {
        "chart": {**common_layout, "height": int(height * 0.25)},
        "series": [
            {
                "type": "Line",
                "data": rsi_data,
                "options": {
                    "color": "#9b59b6",
                    "lineWidth": 1,
                    "title": "RSI(14)",
                    "priceFormat": {"type": "price", "precision": 1},
                },
                "priceScale": {
                    "scaleMargins": {"top": 0.1, "bottom": 0.1},
                    "autoScale": False,
                    "minValue": 0,
                    "maxValue": 100,
                },
            }
        ],
    }

    return [price_chart, volume_chart, rsi_chart]


def build_forensic_chart(df: pd.DataFrame, news_dates: list[int] = None,
                          ticker: str = "", height: int = 500) -> list:
    """
    Chart for Deep Dive forensic analysis.
    Adds news article markers and a wider date range.
    """
    charts = build_stock_chart(df, prediction=None, ticker=ticker, height=height)
    if not charts or not news_dates:
        return charts

    # Add news markers to the price chart
    news_markers = [
        {
            "time": ts,
            "position": "aboveBar",
            "color": "#f0b429",
            "shape": "circle",
            "text": "📰",
        }
        for ts in news_dates
    ]

    if charts and charts[0].get("series"):
        existing = charts[0]["series"][0].get("markers", [])
        charts[0]["series"][0]["markers"] = existing + news_markers

    return charts
