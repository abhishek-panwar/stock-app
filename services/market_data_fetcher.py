"""
Single concurrent data fetch pass over the full ticker superset.

Both bullish and bearish pipelines read from this shared in-memory store —
no ticker is fetched more than once per scanner run.

Failure policy:
  - Empty price history or failed compute_all → ticker excluded (cannot score)
  - All other failures (Finnhub, EDGAR, social) → safe empty defaults returned,
    ticker still included with partial data. Never silently dropped.
"""
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from indicators.technicals import compute_all
from services.yfinance_service import get_market_context, get_sector_etf


_REGULATORY_KEYWORDS = {
    "antitrust", "investigation", "fine", "penalty", "lawsuit", "litigation",
    "regulatory", "probe", "doj", "ftc", "sec", "subpoena", "class action",
    "settlement", "ban", "sanction",
}


def _derive_narrative_risk(fundamentals: dict, sentiment: dict) -> dict:
    """
    Derives narrative_risk signals from already-fetched FMP fundamentals + news sentiment.
    Zero API calls — pure computation from data we already have.

    Fields mapped to long_term_bearish_scorer Group 4:
      competitive_disruption — revenue declining + margin declining simultaneously
      secular_decline        — 2+ consecutive years of revenue decline
      regulatory_risk        — regulatory keywords in recent news headlines
      pricing_compression    — gross margin declining YoY by ≥2 pts
      business_model_risk    — negative FCF + declining revenue (cash burning while shrinking)
    """
    if not fundamentals:
        return {}

    rev_growth   = fundamentals.get("revenue_growth_pct")
    earn_growth  = fundamentals.get("earnings_growth_pct")
    op_margin    = fundamentals.get("operating_margin_pct")
    op_margin_p  = fundamentals.get("operating_margin_prev_pct")
    gm           = fundamentals.get("gross_margin_pct")
    gm_prev      = fundamentals.get("gross_margin_prev_pct")
    fcf          = fundamentals.get("free_cashflow")
    rev_yrs      = fundamentals.get("revenue_declining_years") or 0

    risk = {}

    # competitive_disruption: revenue + margin declining simultaneously,
    # OR revenue still positive but decelerating sharply (Netflix/Meta/Shopify early warning)
    rev_decel = fundamentals.get("revenue_growth_decel")
    if (rev_growth is not None and rev_growth < 0 and
            op_margin is not None and op_margin_p is not None and
            op_margin < op_margin_p):
        risk["competitive_disruption"] = True
    elif rev_decel is not None and rev_decel >= 15 and rev_growth is not None and rev_growth > 0:
        risk["competitive_disruption"] = True

    # secular_decline: 2+ consecutive years of revenue decline
    if rev_yrs >= 2:
        risk["secular_decline"] = True

    # regulatory_risk: keyword match in recent news headlines
    articles = (sentiment or {}).get("articles", [])
    if articles:
        text = " ".join(
            (a.get("headline", "") + " " + a.get("summary", "")).lower()
            for a in articles
        )
        if any(kw in text for kw in _REGULATORY_KEYWORDS):
            risk["regulatory_risk"] = True

    # pricing_compression: gross margin declining ≥2 pts YoY
    if gm is not None and gm_prev is not None and (gm_prev - gm) >= 2:
        risk["pricing_compression"] = True

    # business_model_risk: burning cash while revenue is shrinking
    if fcf is not None and fcf < 0 and rev_growth is not None and rev_growth < 0:
        risk["business_model_risk"] = True

    return risk


def fetch_all(
    tickers: list[str],
    run_date: str,
    earnings_universe: dict,
    log_api: bool = True,
) -> tuple[dict, dict]:
    """
    Fetches all required market data for each ticker concurrently (10 workers).

    Returns:
      ticker_data : {ticker: data_dict}  — only tickers with valid price data + indicators
      stats       : {"rows_fetched": int, "news_fetched": int, "errors": int}
    """
    ticker_data: dict = {}
    stats = {"rows_fetched": 0, "news_fetched": 0, "errors": 0}
    _lock = threading.Lock()

    # Fetch market context once — SPY + sector ETFs, shared across all tickers
    market_ctx = get_market_context()

    def _fetch_one(ticker: str) -> None:
        try:
            import warnings
            from services.yfinance_service import get_price_history, get_ticker_info, get_fundamentals
            from services.social_service import get_social_velocity
            from services.finnhub_service import (
                get_news_sentiment, get_analyst_recommendation,
                get_earnings_history, get_analyst_price_target,
            )
            from services.edgar_service import get_insider_buying

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                df = get_price_history(ticker, period="1y")

            if df.empty:
                return  # no price data — cannot score, exclude ticker

            ind = compute_all(df)
            if not ind:
                return  # indicator computation failed — exclude ticker

            # All subsequent fetches return safe defaults on failure — ticker stays in
            sentiment      = get_news_sentiment(ticker, hours=48, run_date=run_date, log_api=log_api)
            analyst        = get_analyst_recommendation(ticker, run_date=run_date, log_api=log_api)
            earnings       = get_earnings_history(ticker, run_date=run_date, log_api=log_api)
            analyst_target = get_analyst_price_target(ticker, run_date=run_date, log_api=log_api)
            insider_buying = get_insider_buying(ticker, days_back=14, run_date=run_date, log_api=log_api)
            fundamentals   = get_fundamentals(ticker, run_date=run_date, log_api=log_api)
            social_vel     = get_social_velocity(ticker)
            info           = get_ticker_info(ticker, run_date=run_date, log_api=log_api)

            # Derive narrative_risk from fundamentals — no API calls, pure computation
            narrative_risk = _derive_narrative_risk(fundamentals, sentiment)

            ec_data = earnings_universe.get(ticker.upper())
            earnings_calendar = (
                {"has_upcoming": True,
                 "days_to_earnings": ec_data["days_to_earnings"],
                 "earnings_date":    ec_data["earnings_date"]}
                if ec_data
                else {"has_upcoming": False, "days_to_earnings": None, "earnings_date": None}
            )

            # Compute relative strength vs SPY and sector ETF
            sector      = info.get("sector", "Unknown")
            sector_etf  = get_sector_etf(sector)
            spy_return  = market_ctx.get("SPY")
            sector_return = market_ctx.get(sector_etf) if sector_etf else None

            # Ticker's own 5d return for relative strength calc
            try:
                close_series = df["close"] if "close" in df.columns else df["Close"]
                ticker_5d = (float(close_series.iloc[-1]) - float(close_series.iloc[-5])) / float(close_series.iloc[-5]) * 100 if len(close_series) >= 5 else None
            except Exception:
                ticker_5d = None

            rel_strength_vs_spy = round(ticker_5d - spy_return, 1) if ticker_5d is not None and spy_return is not None else None

            with _lock:
                ticker_data[ticker] = {
                    "df":               df,
                    "ind":              ind,
                    "sentiment":        sentiment,
                    "analyst":          analyst,
                    "earnings":         earnings,
                    "analyst_target":   analyst_target,
                    "insider_buying":   insider_buying,
                    "fundamentals":     fundamentals,
                    "social_velocity":  social_vel,
                    "info":             info,
                    "earnings_calendar": earnings_calendar,
                    "company_name":     info.get("name", ticker),
                    "market_cap":       info.get("market_cap"),
                    "avg_volume":       info.get("avg_volume"),
                    "sector":           sector,
                    "sector_etf":       sector_etf,
                    "spy_return_5d":    spy_return,
                    "sector_return_5d": sector_return,
                    "ticker_return_5d": ticker_5d,
                    "rel_strength_vs_spy": rel_strength_vs_spy,
                    "short_interest_pct": info.get("short_interest_pct"),
                "narrative_risk":     narrative_risk,
                }
                stats["rows_fetched"] += len(df)
                stats["news_fetched"] += sentiment.get("volume", 0)

        except Exception as e:
            with _lock:
                stats["errors"] += 1
            from database.db import log_error
            log_error("scanner", f"Data fetch error {ticker}: {e}", detail=str(e), ticker=ticker)
            print(f"  Fetch error on {ticker}: {e}")

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_fetch_one, t): t for t in tickers}
        for future in as_completed(futures):
            pass  # errors handled inside _fetch_one

    print(
        f"  Data fetch complete: {len(ticker_data)}/{len(tickers)} tickers "
        f"({stats['errors']} errors, {stats['rows_fetched']} price rows)"
    )
    return ticker_data, stats
