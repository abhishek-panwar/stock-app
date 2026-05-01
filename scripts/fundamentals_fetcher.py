"""
Fundamentals fetcher — runs Fri/Sat/Sun 8 AM PT via Modal cron.
Reads hot_tickers from DB, fetches fundamentals from yfinance + Alpha Vantage,
persists to api_cache with 2-week TTL, refreshes weekly (overwrites if >7 days old).

AV call budget per run:
  Friday  8 AM PT: 24 calls (Thursday scanner used 1 from this budget)
  Saturday 8 AM PT: 25 calls (full budget — no scanner ran Friday post-reset)
  Sunday  8 AM PT: 25 calls (full budget — no scanner ran Saturday)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import time
from datetime import datetime, timezone, timedelta
import pytz

from database.db import get_hot_tickers_from_db, get_cache, set_cache

PT = pytz.timezone("America/Los_Angeles")

FUNDAMENTALS_TTL_H   = 336  # 2-week TTL — safety net if fetcher misses a weekend
REFRESH_AFTER_DAYS   = 7    # always refresh data older than 7 days regardless of TTL

# AV call budget per day of week (weekday() → 4=Fri, 5=Sat, 6=Sun)
AV_BUDGET = {4: 24, 5: 25, 6: 25}


def get_fundamentals_from_yfinance(ticker: str) -> dict:
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        price = info.get("currentPrice") or info.get("regularMarketPrice") or 0

        rev_growth       = info.get("revenueGrowth")
        earn_growth      = info.get("earningsGrowth")
        gross_margin     = info.get("grossMargins")
        operating_margin = info.get("operatingMargins")
        profit_margin    = info.get("profitMargins")
        fcf              = info.get("freeCashflow")
        trailing_pe      = info.get("trailingPE")
        forward_pe       = info.get("forwardPE")
        peg              = info.get("pegRatio")
        pb               = info.get("priceToBook")
        mean_target      = info.get("targetMeanPrice")
        analyst_count    = info.get("numberOfAnalystOpinions")

        analyst_upside = None
        if mean_target and price and price > 0:
            analyst_upside = round((mean_target - price) / price * 100, 1)

        return {
            "ticker":               ticker,
            "price":                price,
            "revenue_growth_pct":   round(rev_growth * 100, 1) if rev_growth is not None else None,
            "earnings_growth_pct":  round(earn_growth * 100, 1) if earn_growth is not None else None,
            "gross_margin_pct":     round(gross_margin * 100, 1) if gross_margin is not None else None,
            "operating_margin_pct": round(operating_margin * 100, 1) if operating_margin is not None else None,
            "profit_margin_pct":    round(profit_margin * 100, 1) if profit_margin is not None else None,
            "free_cashflow":        fcf,
            "trailing_pe":          round(trailing_pe, 1) if trailing_pe else None,
            "forward_pe":           round(forward_pe, 1) if forward_pe else None,
            "peg_ratio":            round(peg, 2) if peg else None,
            "price_to_book":        round(pb, 2) if pb else None,
            "analyst_mean_target":  mean_target,
            "analyst_upside_pct":   analyst_upside,
            "analyst_count":        analyst_count,
            "fetched_at":           datetime.now(timezone.utc).isoformat(),
            "source":               "yfinance",
        }
    except Exception as e:
        print(f"  yfinance error {ticker}: {e}")
        return {"ticker": ticker, "source": "yfinance", "error": str(e)}


def get_fundamentals_from_av(ticker: str, av_key: str) -> dict:
    try:
        import requests
        r = requests.get(
            "https://www.alphavantage.co/query",
            params={"function": "COMPANY_OVERVIEW", "symbol": ticker, "apikey": av_key},
            timeout=15,
        )
        d = r.json()
        if "Symbol" not in d:
            return {}

        def _f(key):
            v = d.get(key)
            try:
                return float(v) if v and v != "None" else None
            except Exception:
                return None

        price_str   = d.get("AnalystTargetPrice", "")
        mean_target = float(price_str) if price_str and price_str != "None" else None
        profit_raw  = _f("ProfitMargin")

        return {
            "trailing_pe":       _f("PERatio"),
            "forward_pe":        _f("ForwardPE"),
            "peg_ratio":         _f("PEGRatio"),
            "price_to_book":     _f("PriceToBookRatio"),
            "profit_margin_pct": round(profit_raw * 100, 1) if profit_raw else None,
            "analyst_mean_target": mean_target,
            "analyst_count":     _f("AnalystRatingCount") or _f("NumberOfAnalystOpinions"),
            "revenue_ttm":       _f("RevenueTTM"),
            "eps_ttm":           _f("EPS"),
            "source":            "alpha_vantage",
        }
    except Exception as e:
        print(f"  Alpha Vantage error {ticker}: {e}")
        return {}


def _needs_refresh(cached: dict) -> bool:
    """Returns True if cached data is older than REFRESH_AFTER_DAYS."""
    if not cached:
        return True
    fetched_at = cached.get("fetched_at", "")
    if not fetched_at:
        return True
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(fetched_at)
        return age.days >= REFRESH_AFTER_DAYS
    except Exception:
        return True


def run():
    now_pt   = datetime.now(PT)
    weekday  = now_pt.weekday()  # 4=Fri, 5=Sat, 6=Sun
    av_limit = AV_BUDGET.get(weekday, 0)

    print(f"Fundamentals fetcher — {now_pt.strftime('%A %b %d %Y %I:%M %p PT')}")
    print(f"  AV budget today: {av_limit} calls  |  Refresh threshold: {REFRESH_AFTER_DAYS} days")

    rows = get_hot_tickers_from_db()
    if not rows:
        print("  No hot tickers in DB — nothing to fetch.")
        return

    tickers = [r["ticker"] for r in rows]
    print(f"  {len(tickers)} tickers from last scanner run")

    av_key   = os.environ.get("ALPHA_VANTAGE_KEY", "")
    av_calls = 0
    fetched  = 0
    skipped  = 0
    refreshed = 0

    for ticker in tickers:
        cached = get_cache(f"fundamentals_{ticker}")

        if cached and not _needs_refresh(cached):
            skipped += 1
            continue

        is_refresh = cached is not None  # True = update, False = first fetch

        # yfinance — no limit, fetch for all tickers
        data = get_fundamentals_from_yfinance(ticker)

        # AV — overlay if budget remains, fills any gaps yfinance left
        if av_key and av_calls < av_limit:
            av_data = get_fundamentals_from_av(ticker, av_key)
            if av_data:
                for k, v in av_data.items():
                    if v is not None and data.get(k) is None:
                        data[k] = v
                av_calls += 1
            time.sleep(0.5)  # AV rate limit safety

        set_cache(f"fundamentals_{ticker}", data, ttl_hours=FUNDAMENTALS_TTL_H)
        fetched += 1
        if is_refresh:
            refreshed += 1

        if fetched % 10 == 0:
            print(f"  Progress: {fetched}/{len(tickers)} — {av_calls} AV calls used")

        time.sleep(0.1)  # gentle yfinance pacing

    print(f"  Done — {fetched} fetched ({refreshed} refreshed, {fetched - refreshed} new), "
          f"{skipped} already fresh, {av_calls}/{av_limit} AV calls used")


if __name__ == "__main__":
    run()
