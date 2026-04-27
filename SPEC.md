# Personal Stock Analysis Web App — Full Project Spec

## What This App Does

A personal cloud-based stock screener, trade planner, and prediction tracker that:
- Runs a full deep scan nightly at 8:00 PM PT on a deduplicated universe of 100–150 stocks
- Surfaces the strongest 5–10 trade setups per timeframe (short / medium / long term)
- Generates detailed trade plans with buy windows, buy/sell price ranges, and stop losses
- Logs every prediction as a paper trade automatically
- Verifies outcomes after each timeframe expires
- Tracks prediction accuracy over time so you can see which signals actually work
- Sends Telegram alerts directly to your phone
- Automatically learns from past prediction outcomes and recalibrates Claude's analysis
- Tracks rejected stocks in shadow portfolio to detect signals we are missing
- Self-improves its own scoring formula over time, with every change logged and visible
- Lets you forensically analyze any stock to understand past moves and what signals could have caught them
- Tracks credibility scores for analysts and article writers who influenced predictions
- Runs entirely in the cloud — nothing installed on your machine

---

## Tech Stack

| Layer | Technology | Cost |
|---|---|---|
| Language | Python | Free |
| UI Dashboard | Streamlit (Streamlit Cloud) | Free |
| Price Data | yfinance (Yahoo Finance) | Free, no API key |
| News + Sentiment + Analyst Ratings | Finnhub API | Free tier |
| Technical Indicators | pandas + pandas-ta | Free |
| AI Analysis | Claude Haiku 4.5 (Anthropic API) | ~$0.05/month |
| Database | Supabase (PostgreSQL, cloud-hosted) | Free (500MB) |
| Notifications | Telegram Bot API | Free |
| Code Editor | GitHub Codespaces (browser-based VS Code) | Free (60hrs/month) |
| Automation | GitHub Actions (cron jobs) | Free |

**Total estimated monthly cost: ~$0.05**

---

## Infrastructure Architecture

```
GitHub (code + secrets + cron)
    │
    ├── GitHub Codespaces       ← where you write/edit code (browser)
    │
    ├── Streamlit Cloud         ← where the app runs (any browser, phone or Mac)
    │
    └── GitHub Actions          ← background automation
            ├── Nightly Deep Scan (8:00 PM PT)     → full scan, predictions, Telegram
            ├── Price Watcher (every 5 min, market hours 6:30–1:00 PM PT)
            │                                        → open trades only, stop loss + target
            ├── Mid-Session Check (9:45 AM PT)      → re-scan top 20, check news shifts
            ├── Market Close Snapshot (1:00 PM PT)  → position summary, feeds nightly scan
            ├── Verifier (8:30 PM PT)              → labels outcomes WIN/LOSS
            ├── Feedback Engine (8:45 PM PT)       → updates Claude prompt context
            ├── Opportunity Analyzer (weekly Sun)   → finds what we missed, suggests fixes
            └── Health Monitor (6:00 AM PT)         → checks all components
                        │
                        └── Telegram Bot → alerts to phone
```

---

## Accounts You Need to Set Up

| Service | Purpose | URL |
|---|---|---|
| GitHub | Code + Codespaces + Actions | github.com |
| Streamlit Cloud | Host the dashboard | streamlit.io |
| Supabase | Cloud PostgreSQL database | supabase.com |
| Anthropic Console | Claude API key | console.anthropic.com |
| Finnhub | Stock news + sentiment API | finnhub.io |
| Telegram | Create bot via BotFather | t.me/BotFather |

---

## API Keys — Security Model

Keys are never stored in code or committed to GitHub.

| Environment | How Keys Are Stored |
|---|---|
| Local dev | .env file (gitignored), loaded via python-dotenv |
| Streamlit Cloud | Streamlit Secrets UI |
| GitHub Actions | GitHub Secrets UI |
| GitHub repo | Nothing — ever |

Keys needed:
- `ANTHROPIC_API_KEY`
- `FINNHUB_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `SUPABASE_URL`
- `SUPABASE_KEY`

---

## Stock Universe

### Layer 1 — Static Base: Nasdaq 100
Always scanned. Large cap, liquid, well-covered stocks.

### Layer 2 — Dynamic Hot Stocks (Top 50, refreshed nightly as part of 8:00 PM PT scan)
Compiled using a Hot Score formula:

```
Hot Score =
  News Volume Score      (how many articles in last 48 hours — Finnhub)
+ Analyst Rating Score   (buy/sell/hold consensus — Finnhub)
+ Social Sentiment Score (Reddit + StockTwits mentions — Finnhub)
+ Price Momentum Score   (unusual % move last 1–3 days — yfinance)
```

**Total universe: 100–150 stocks per scan (varies nightly based on overlap).**

### Deduplication Rules

The app always deduplicates before scanning. The same stock is never analyzed twice in one scan regardless of how many lists it appears in.

| Scenario | Nasdaq 100 | Hot 50 | Deduplicated Total |
|---|---|---|---|
| Full overlap (all hot stocks already in Nasdaq) | 100 | 50 | 100 |
| Zero overlap (all hot stocks outside Nasdaq) | 100 | 50 | 150 |
| Typical day (partial overlap) | 100 | ~30 new | ~130 |

### Source Tagging

Every stock is tagged with where it came from. Stored on every prediction row in Supabase:

```
NVDA  → source: nasdaq100 + hot_stock  ← priority bonus applied
SMCI  → source: hot_stock only
AAPL  → source: nasdaq100 only
```

Stocks appearing in **both** lists receive a +3 point priority bonus on their signal score — appearing on both a stable index AND trending hot list is itself a meaningful signal.

### Actual Count Always Reported

The app never assumes a fixed universe size. Every scan reports the real number:

```
Telegram 8:00 PM PT:
"Universe tonight: 127 stocks
 (100 Nasdaq + 50 hot → 23 overlap, deduplicated)"

Dashboard shows same number live.
Scan logs in Supabase store exact count every run.
```

---

## Screening Funnel

```
Nasdaq 100 + Hot 50
    │
    ▼
Deduplicate → actual universe: 100–150 stocks (varies nightly)
Source-tagged: nasdaq100 / hot_stock / both
    │
    ▼
Signal Scorer runs on full deduplicated universe
(+3 bonus for stocks tagged "both")
    │
    ▼
Top 20 by score pass threshold
    │
    ▼
Claude deep analysis on top 20 only
    │
    ▼
Ranked into timeframe buckets
    │
    ▼
5–10 best shown per timeframe on dashboard
```

### Signal Score Breakdown v1.0 (0–100)

See full indicator definitions in the Indicators section below.

| Group | Max Points | Signals Included |
|---|---|---|
| Momentum | 25 | RSI, MACD crossover, Rate of Change |
| Trend | 20 | MA20/50/200 alignment, price vs MA |
| Volatility | 15 | Bollinger Band squeeze/breakout, ATR |
| Volume | 20 | Volume surge, OBV trend, VWAP position |
| Sentiment | 10 | News sentiment, social mentions |
| External | 10 | Analyst rating, earnings momentum |
| **Total** | **100** | |

### Score Thresholds
- Below 60 → dropped silently, never shown
- 60–74 → watchlist only (expandable section)
- 75–84 → qualifies for top 10 per timeframe
- 85+ → highlighted on dashboard, pushed to Telegram

### Formula Versioning
Every prediction stores the formula version that generated it (e.g. `score_formula_v1`). When the formula changes based on shadow portfolio findings, it gets a new version number. This keeps historical accuracy comparisons meaningful across formula changes.

---

## Timeframes

| Label | Horizon | Primary Signals |
|---|---|---|
| Short-term | 2–5 days | Momentum, news catalyst, MACD, RSI |
| Medium-term | 1–4 weeks | Trend, MA20/50, analyst ratings |
| Long-term | 1–6 months | MA50/200, earnings, sector trend |

Note: Intraday (hours) excluded from v1 — can be added later.

Each stock gets up to 3 separate Claude predictions, one per timeframe.

**"All Timeframes Agree" section** — highlights stocks where all 3 timeframes point the same direction. Highest conviction signal in the entire app.

---

## Dashboard Layout

### Main Page — Today's Best Setups

```
📊 Today's Best Setups — [Date]
Last scanned: 9:45 AM PT | Universe: 148 stocks

⚡ Short-Term (2–5 days)
─────────────────────────────────────────
1. NVDA   Bullish  Confidence 84%   92/100
2. META   Bullish  Confidence 79%   87/100
3. TSLA   Bearish  Confidence 76%   83/100
...

📈 Medium-Term (1–4 weeks)
─────────────────────────────────────────
1. AAPL   Bullish  Confidence 82%   89/100
2. MSFT   Bullish  Confidence 77%   85/100
...

🌱 Long-Term (1–6 months)
─────────────────────────────────────────
1. NVDA   Bullish  Confidence 88%   94/100
...

🔥 All Timeframes Agree (Highest Conviction)
─────────────────────────────────────────
NVDA — Bullish across all timeframes | Avg Confidence 84%
```

### Individual Stock Page (click any stock)

- Candlestick chart with RSI, MACD, MA20, MA50 overlays
- Full trade plan (see below)
- Claude's reasoning
- News headlines + sentiment
- Signal score breakdown

---

## Trade Plan Format (per prediction)

Every prediction includes a complete trade plan:

```
NVDA — Short-term Setup (2–5 days)
Score: 92/100 | Confidence: 84% | Direction: BULLISH LONG
──────────────────────────────────────────────────────────
Position:     LONG

Buy Window:   Apr 27, 2026  7:15 AM – 8:30 AM PT
Buy Range:    $888.00 – $895.00
              (current: $891.50 — within range ✅)

Target Sell:  $912.00 – $918.00  (+2.4% to +3.0%)
Stop Loss:    $876.00            (-1.7% max risk)
Risk/Reward:  1 : 1.8

Why this stock:
"NVDA broke above 20-day MA with RSI at 58 (room to run,
not overbought). MACD just crossed bullish. 4 analyst
upgrades this week. Social sentiment up 34% vs last week."
──────────────────────────────────────────────────────────
All times in Pacific Time (Seattle)
```

### Buy Window Logic (Pacific Time)

US market hours in PT: 6:30 AM – 1:00 PM PT (9:30 AM – 4:00 PM ET)

| Time (PT) | Recommendation |
|---|---|
| 6:30 – 7:15 AM | Opening volatility — avoid unless clear breakout |
| 7:15 – 8:30 AM | Best short-term entry window |
| 8:30 – 10:30 AM | Good for momentum continuation |
| 10:30 AM – 11:00 AM | Mid-session check fires at 9:45 AM — reassess |
| 11:00 AM – 12:30 PM | Good for medium-term entries |
| 12:30 – 1:00 PM | Power hour — use for exits, not entries |
| 1:00 PM | Market closes (ET 4:00 PM) |

### Long vs Short
- BULLISH signal → LONG (buy expecting price rise)
- BEARISH signal → SHORT (flagged as requiring margin account — app always notes this)
- NEUTRAL → HOLD / WAIT, no trade suggested

---

## Paper Trade Auto-Logging

Every prediction is automatically logged in Supabase as an open paper trade.
Entry price = midpoint of buy range at time of prediction.
No manual input needed from the user.

### Supabase Predictions Table

| Column | Description |
|---|---|
| ticker | e.g. NVDA |
| predicted_on | datetime Pacific Time |
| timeframe | short / medium / long |
| direction | BULLISH / BEARISH / NEUTRAL |
| position | LONG / SHORT / HOLD |
| confidence | 0–100 |
| score | 0–100 |
| price_at_prediction | entry price used |
| buy_range_low / high | price range bounds |
| target_low / high | target range |
| stop_loss | stop price |
| reasoning | Claude's full reasoning |
| verified_on | filled by nightly verifier |
| price_at_close | actual price at verification |
| outcome | WIN / LOSS / PENDING |
| return_pct | actual % return |
| closed_reason | TARGET_HIT / STOP_LOSS / EXPIRED |

### Verification Logic (runs nightly at 8:30 PM PT after market close data is settled)
- Price enters target range → CLOSED WIN
- Price hits stop loss → immediate Telegram alert (caught by 5-min price watcher during market hours)
- Timeframe expires → CLOSED at 1:00 PM PT close price, WIN if ≥+2% in predicted direction

---

## History Page — Prediction Ledger

Separate page in the Streamlit app. Green = WIN, Red = LOSS.

Each row shows:
- Ticker, timeframe, direction, date range
- Entry details: price, time, buy range, was entry within range?
- Exit details: price, time, how it closed
- Return %
- Claude's original reasoning
- Signal score breakdown at time of prediction

### Filters
By timeframe / outcome (WIN, LOSS, PENDING) / ticker / date range / confidence band

### Accuracy Summary (top of page)
```
Overall win rate
Avg return on wins / avg loss on losses
Expectancy per trade (positive = system works)

By timeframe:     Short / Medium / Long
By signal type:   All-timeframes-aligned vs single timeframe
By position:      Long trades vs short trades
Best combos:      Which indicator combinations win most often
```

---

## Telegram Notifications

### Alert Types

**Priority 1 — Immediate (fires any time, no cooldown)**

Stop Loss Hit:
```
⚠️ NVDA stop loss triggered
Entry: $891.50 → Current: $875.80
Loss: -1.76% | Trade closed as LOSS
Time: Apr 29, 11:42 AM PT
```

Target Hit:
```
✅ NVDA hit target
Entry: $891.50 → Current: $914.20
Return: +2.55% | Consider taking profit
Time: May 2, 1:15 PM PT
```

**Priority 2 — Market hours only (6:30 AM – 1:00 PM PT), 4hr cooldown per ticker**

New Prediction (score 85+):
```
📊 NVDA — Short-term LONG
Confidence: 84% | Score: 92/100
Buy: $888 – $895 (now: $891.50 ✅)
Target: $912 – $918
Stop Loss: $876
Best entry window: 7:15 – 8:30 AM PT
All timeframes aligned 🎯
```

RSI Alert:
```
🚨 TSLA RSI Alert
RSI hit 71 — Overbought territory
Current: $248.30 | Watch for reversal
Time: Apr 27, 9:55 AM PT
```

Sentiment Spike:
```
📰 META Sentiment Alert
Negative sentiment spike detected
5 negative articles in last 2 hours
Current: $512.40 | Monitor closely
```

**Priority 3 — Scheduled**

Nightly Predictions (8:00 PM PT) — MAIN ALERT:
```
📊 Tonight's Top Picks — Apr 27, 2026  8:00 PM PT

⚡ Short-term: NVDA, META, TSLA
📈 Medium-term: AAPL, MSFT
🌱 Long-term: NVDA, AAPL

Top pick:
NVDA — Short-term LONG
Confidence: 84% · historically 71% correct
Buy window: 7:15–8:30 AM PT tomorrow
Buy range: $888–$895 | Target: $912–$918 | Stop: $876
All timeframes aligned 🎯

Open trades: 8 | Winning: 5 | Losing: 2 | Neutral: 1
```

Morning Reminder (6:20 AM PT — 10 min before open):
```
📅 Market opens in 10 minutes — Apr 28, 2026
Active predictions from last night: 8
Top entry today: NVDA  Buy: $888–$895  Window: 7:15–8:30 AM PT
System: All components healthy ✅
```

Mid-Session Update (9:45 AM PT — only if something changed):
```
🔄 Mid-session update — NVDA
Sentiment shifted positive since last night
Confidence upgraded: 84% → 88%
Still within buy range ✅
```

Market Close Summary (1:00 PM PT):
```
🔔 Market closed — Apr 28, 2026  1:00 PM PT
Open trades: 7 | 4 winning | 2 losing | 1 neutral
Best performer: NVDA +2.1%
Next scan: tonight 8:00 PM PT
```

---

## Complete Indicators Reference (v1.0)

All indicators computed via pandas-ta on yfinance OHLCV data. Every indicator below contributes to the signal score. Each is stored per-stock per-scan in Supabase so the feedback engine and shadow tracker can reference exact values when analyzing missed opportunities.

---

### Group 1 — Momentum Indicators (25 pts max)

#### RSI — Relative Strength Index (up to 12 pts)
Measures speed and magnitude of price changes. Most reliable single indicator in the set.

| Condition | Points | Meaning |
|---|---|---|
| RSI < 30 (oversold, recovering) | 10–12 | Strong buy signal |
| RSI 30–40 (recovering zone) | 6–9 | Moderate buy signal |
| RSI 60–70 (strong momentum, not overbought) | 5–7 | Bullish continuation |
| RSI > 70 (overbought) | 0–2 | Caution — bearish signal |
| RSI divergence (price down, RSI up) | +3 bonus | Hidden bullish signal |

Settings: Period 14 (standard). Computed on daily candles.

#### MACD — Moving Average Convergence Divergence (up to 8 pts)
Trend-following momentum indicator. Best for confirming direction, not timing.

| Condition | Points | Meaning |
|---|---|---|
| Bullish crossover (MACD crosses above signal) | 7–8 | Strong buy |
| MACD above signal line + rising | 4–6 | Bullish momentum |
| MACD histogram growing positive | 3–4 | Early momentum |
| Bearish crossover | 0 | Bearish signal |

Settings: Fast 12, Slow 26, Signal 9 (standard).

#### Rate of Change — ROC (up to 5 pts)
Measures % price change over N periods. Catches acceleration before it shows in RSI/MACD.

| Condition | Points |
|---|---|
| ROC > +5% over 5 days | 4–5 |
| ROC +2% to +5% | 2–3 |
| ROC flat or negative | 0–1 |

Settings: Period 5 for short-term, Period 20 for medium/long.

---

### Group 2 — Trend Indicators (20 pts max)

#### Moving Averages — MA20, MA50, MA200 (up to 12 pts)
Direction and strength of the underlying trend.

| Condition | Points | Meaning |
|---|---|---|
| Price above MA20 + MA20 above MA50 | 10–12 | Strong uptrend |
| Price just crossed above MA20 | 8–10 | Early trend entry |
| Price above MA50, below MA20 | 5–7 | Moderate trend |
| MA20 crossing above MA50 (golden cross) | +3 bonus | Medium-term bullish |
| MA50 crossing above MA200 | +4 bonus | Long-term bullish |
| Price below all MAs | 0–2 | Downtrend |

Settings: Simple MA (SMA). 20-day, 50-day, 200-day.

#### ADX — Average Directional Index (up to 8 pts)
Measures trend strength regardless of direction. Filters out choppy, sideways markets.

| Condition | Points | Meaning |
|---|---|---|
| ADX > 30 (strong trend) | 6–8 | Trend is real and strong |
| ADX 20–30 (developing trend) | 3–5 | Trend forming |
| ADX < 20 (weak/no trend) | 0–2 | Choppy — reduce confidence |

Settings: Period 14. Used as a confidence multiplier — a high RSI+MACD signal in a low-ADX environment gets penalized.

---

### Group 3 — Volatility Indicators (15 pts max)

#### Bollinger Bands (up to 10 pts)
Shows price volatility and potential breakout zones.

| Condition | Points | Meaning |
|---|---|---|
| Bollinger Squeeze (bands narrowing) | 8–10 | Breakout imminent — high potential |
| Price breaking above upper band on volume | 6–8 | Bullish breakout confirmed |
| Price at lower band + RSI oversold | 7–9 | Bounce setup |
| Price in middle of bands | 2–3 | Neutral |
| Bands widening (post-squeeze) | +2 bonus | Breakout in progress |

Settings: Period 20, 2 standard deviations. Squeeze defined as band width in bottom 20% of 52-week range.

#### ATR — Average True Range (up to 5 pts)
Measures daily price range / volatility. Used to set stop loss distances and filter low-liquidity stocks.

| Condition | Points | Use |
|---|---|---|
| ATR rising (increasing volatility) | 3–5 | Confirms breakout energy |
| ATR stable | 2–3 | Normal trading |
| ATR falling (compression) | 4–5 | Pre-breakout squeeze signal |

Settings: Period 14. Also used directly in stop loss calculation: stop = entry − (1.5 × ATR).

---

### Group 4 — Volume Indicators (20 pts max)

Volume is the most underused free signal. A price move without volume is weak. A price move with high volume is significant. This group was identified as the biggest gap in v1 scoring.

#### Volume Surge (up to 10 pts)
Unusual volume = institutional or informed buying/selling.

| Condition | Points | Meaning |
|---|---|---|
| Volume > 300% of 20-day avg | 9–10 | Very strong institutional activity |
| Volume > 200% of 20-day avg | 6–8 | Strong unusual activity |
| Volume > 150% of 20-day avg | 3–5 | Moderate elevated volume |
| Volume at or below avg | 0–2 | Weak conviction |

Settings: Compared against 20-day average daily volume. Computed from yfinance.

#### OBV — On-Balance Volume (up to 6 pts)
Cumulative volume indicator. Rising OBV with flat/falling price = accumulation (bullish divergence).

| Condition | Points | Meaning |
|---|---|---|
| OBV rising + price rising | 5–6 | Volume confirming uptrend |
| OBV rising + price flat/falling | 4–5 | Bullish divergence — hidden accumulation |
| OBV flat | 2–3 | Neutral |
| OBV falling + price rising | 0–1 | Warning — weak move |

Settings: Standard OBV, computed on daily closes.

#### VWAP — Volume Weighted Average Price (up to 4 pts)
The "fair value" price based on volume. Used by institutional traders as reference.

| Condition | Points | Meaning |
|---|---|---|
| Price above VWAP + rising | 3–4 | Bullish institutional bias |
| Price at VWAP (support) | 2–3 | Good entry zone |
| Price below VWAP | 0–1 | Bearish institutional bias |

Settings: Daily VWAP, computed from yfinance intraday where available, otherwise skipped.

---

### Group 5 — Sentiment Indicators (10 pts max)

#### News Sentiment Score (up to 6 pts)
From Finnhub. Aggregated sentiment across last 48 hours of news.

| Condition | Points |
|---|---|
| Strong positive sentiment (> 0.6) | 5–6 |
| Moderate positive (0.3–0.6) | 3–4 |
| Neutral (-0.3 to 0.3) | 1–2 |
| Negative sentiment | 0 |

#### Social Sentiment — Reddit + StockTwits (up to 4 pts)
From Finnhub. Volume and direction of social media mentions.

| Condition | Points |
|---|---|
| Mention volume spike + positive | 3–4 |
| Rising mentions + neutral tone | 2–3 |
| Flat or declining mentions | 0–1 |

Note: Social sentiment alone scores ≤4 pts by design. It is context, not a primary signal — this directly addresses the "sentiment only → 48% win rate" historical finding.

---

### Group 6 — External Signals (10 pts max)

#### Analyst Rating Consensus (up to 6 pts)
From Finnhub `/stock/recommendation`. Aggregate of all analyst buy/sell/hold ratings.

| Condition | Points |
|---|---|
| Strong Buy consensus | 5–6 |
| Buy consensus | 3–4 |
| Hold | 1–2 |
| Sell / Strong Sell | 0 |

#### Earnings Momentum (up to 4 pts)
Whether the stock has beaten earnings estimates in recent quarters. A stock beating estimates 3 quarters in a row has built-in institutional confidence.

| Condition | Points |
|---|---|
| Beat estimates 3+ consecutive quarters | 3–4 |
| Beat estimates 1–2 quarters | 1–2 |
| Missed or no data | 0 |

Note: Earnings data from Finnhub `/stock/earnings`. Only available for stocks with recent earnings history.

---

### Bonus Signals (added to base score, no cap individually but total score capped at 100)

| Signal | Bonus | Condition |
|---|---|---|
| All timeframes agree | +5 | Short + medium + long all bullish/bearish |
| RSI divergence | +3 | Price falling but RSI rising |
| Golden cross | +3 | MA20 just crossed above MA50 |
| Bollinger post-squeeze | +2 | Bands just started expanding after squeeze |
| 52-week high breakout | +4 | Price just broke above 52-week high on volume |
| Dual-list appearance | +3 | Stock in both Nasdaq 100 AND hot stock list |
| Sector strength | +2 | Stock's sector ETF also bullish (future addition) |

---

### What Each Timeframe Weights Differently

Same indicators, different emphasis per timeframe:

| Indicator Group | Short-term weight | Medium-term weight | Long-term weight |
|---|---|---|---|
| Momentum (RSI, MACD, ROC) | High | Medium | Low |
| Trend (MA, ADX) | Medium | High | High |
| Volatility (Bollinger, ATR) | High | Medium | Low |
| Volume | High | Medium | Low |
| Sentiment | Medium | Low | Very Low |
| External (analyst, earnings) | Low | Medium | High |

Short-term relies on momentum and volume. Long-term relies on trend and fundamentals. This is reflected in the timeframe-specific scoring weights applied in `scoring.py`.

---

## Shadow Portfolio — Catching What We Miss

Tracks all 130 rejected stocks silently to detect scoring gaps and missing indicators.

### How It Works

```
Every scan:
  Top 20 → full Claude analysis → predictions logged  (normal flow)
  Remaining 130 → price + all indicator values logged silently
                  NO prediction made, NO Claude call

Nightly verifier:
  Checks: did any rejected stock move ≥3% in 3 days?
  Flags as "missed opportunity"
  Logs: score at rejection time, which indicators fired

Weekly (Sunday 8:00 PM PT):
  Opportunity Analyzer runs
  Sends Claude the list of high-movers that were rejected
  Claude identifies the pattern gap
  Writes finding + formula suggestion to Supabase
  You review on the System Evolution page
  You approve or reject the suggested change
```

### Missed Opportunity Threshold

A rejected stock is flagged as missed if:
- It moves ≥3% in either direction within 3 trading days of being rejected
- Its score was 55–74 (close to threshold — not a fundamentally bad stock)
- The move was in the direction our formula would have predicted

Stocks scoring below 55 are not tracked — too weak to be meaningful.

### What the Weekly Analysis Asks Claude

```
MISSED OPPORTUNITY ANALYSIS — Week of Apr 27, 2026

Stocks REJECTED by scorer (score 55–74) that moved ≥3%:

SMCI: Score 62, moved +4.8% in 3 days
  Had: RSI 38 recovering, volume +340% vs avg
  Missing in score: volume surge not in formula

CRWD: Score 58, moved +3.9% in 2 days
  Had: Bollinger squeeze + analyst upgrade
  Missing in score: Bollinger squeeze not in formula (added in v1.1?)

PLTR: Score 61, moved +5.2% in 4 days
  Had: 52-week high breakout + OBV rising
  Missing in score: 52-week breakout bonus not triggering

Question: What signals are present in missed stocks
that the current formula underweights or misses?
What specific change would have captured these?
```

### New Supabase Tables

**shadow_prices** — price snapshots for rejected stocks
```
ticker, scan_timestamp, score_at_rejection,
price, volume, rsi, macd_signal, bb_squeeze,
volume_surge_ratio, obv_trend, formula_version
```

**missed_opportunities** — flagged high-movers
```
ticker, rejection_date, score_at_rejection,
move_pct, move_direction, days_to_move,
signals_present, formula_version
```

**formula_suggestions** — Claude's weekly findings
```
suggestion_date, suggested_by (always "claude"),
indicator_to_add, current_weight, suggested_weight,
evidence (list of missed tickers),
projected_improvement_pct,
status (PENDING / APPROVED / REJECTED),
reviewed_on, reviewed_by (always "user")
```

**formula_history** — log of every approved change
```
version, applied_on, change_description,
changed_by (always "user-approved"),
win_rate_before, win_rate_after (filled retrospectively)
```

---

## System Evolution Page

A dedicated page in the Streamlit app that shows the full history of every improvement the system has made — in plain English alongside the technical details. This is your audit trail of how the app has grown smarter over time.

### What It Shows

**Header — Summary Stats**
```
System started:      Apr 27, 2026
Formula version:     v1.4  (4 improvements made)
Overall improvement: Win rate 54% → 71% since launch
Last improvement:    May 18, 2026
Pending suggestions: 1 awaiting your review
```

**Timeline — Every Improvement (most recent first)**

Each card shows both a human-readable explanation and the technical details:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ IMPROVEMENT #4  —  May 18, 2026  9:12 AM PT
   Formula: v1.3 → v1.4
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WHAT CHANGED (plain English)
The system noticed it was missing stocks that were about
to break out to new 52-week highs. These breakouts kept
happening in stocks that were rejected because their RSI
wasn't in an "extreme" zone. Added a bonus for stocks
approaching or breaking 52-week highs on high volume.

WHAT CHANGED (technical)
  Added: 52_week_high_breakout bonus signal (+4 pts)
  Condition: price within 2% of or above 52-week high
             AND volume > 150% of 20-day avg
  Affected formula group: Bonus Signals

EVIDENCE THAT TRIGGERED THIS
  Missed opportunities that prompted change:
    PLTR: score 61, moved +5.2% (had 52-wk breakout)
    HOOD: score 58, moved +4.1% (had 52-wk breakout)
    SOFI: score 63, moved +3.8% (had 52-wk breakout)
  Pattern found in: 3/5 missed stocks that week

IMPACT (filled in 4 weeks after change)
  Predictions using this signal: 12
  Win rate with signal: 75%  vs  61% baseline
  Stocks captured that would have been missed: 8
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ IMPROVEMENT #3  —  May 11, 2026  9:08 AM PT
   Formula: v1.2 → v1.3
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WHAT CHANGED (plain English)
Sentiment score was being weighted too heavily. When news
was positive, stocks were scoring high enough to pass even
with weak technical setups. Historical data showed
"sentiment only" predictions win only 48% of the time —
barely better than a coin flip. Reduced sentiment max
from 30 pts to 10 pts and moved those 20 pts to volume.

WHAT CHANGED (technical)
  Sentiment group: 30 pts → 10 pts
  Volume group:    0 pts  → 20 pts  (new group added)
  Specifically added: volume_surge_score, obv_score, vwap_score
  Win rate before change: 54%  (last 30 days)
  Projected improvement: +8–12% win rate

EVIDENCE THAT TRIGGERED THIS
  Accuracy data: sentiment_only combo → 48% win (25 trades)
  Vs RSI+MACD combo → 79% win (31 trades)
  Feedback engine flagged this after 25 sentiment-only trades

IMPACT
  Win rate 4 weeks after: 67%  (+13% improvement)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⏳ PENDING SUGGESTION  —  May 24, 2026
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SUGGESTED CHANGE (plain English)
The system noticed TSLA predictions are 40% accurate
despite TSLA having strong technical setups. TSLA tends
to move on macro/news events more than technicals.
Suggesting to add a TSLA-specific confidence penalty
of -10 pts when news sentiment is neutral or negative.

SUGGESTED CHANGE (technical)
  Add ticker_specific_penalty for TSLA
  Condition: TSLA + news_sentiment < 0.3
  Penalty: -10 pts from total score
  Based on: 12 TSLA trades, 40% win rate

[ APPROVE ]   [ REJECT ]   [ REMIND ME LATER ]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Rejected Suggestions Log**

Shows suggestions you rejected and why (you can add a note):
```
❌ Rejected — May 14, 2026
   Suggested: Add sector ETF comparison signal
   Reason rejected: "Data not available on free tier yet"
```

**Formula Diff View**

For any improvement, a "show diff" expands to show the exact before/after scoring weights as a table so you can see precisely what changed numerically.

**Impact Over Time Chart**

A line chart showing win rate over time with vertical markers at each formula version change. Visually shows whether each improvement helped or not.

---

### Key Design Decisions for This Page

1. **You approve every change** — Claude suggests, you decide. The system never modifies its own scoring formula without your explicit approval on this page.

2. **Impact is measured retrospectively** — 4 weeks after each approved change, the system goes back and fills in the actual win rate improvement. This makes the evidence real, not projected.

3. **Rejections are logged** — so you can see what the system suggested that you said no to. Useful for reviewing later if circumstances change.

4. **Formula version is stored on every prediction** — so accuracy stats are always compared within the same formula version. A change in formula doesn't corrupt your historical data.

---

The app automatically learns from its own prediction history and feeds that knowledge back into Claude's analysis prompt. No manual intervention. No machine learning model needed — Claude does the reasoning.

### How It Works

```
Nightly Verifier labels outcomes (WIN / LOSS)
            │
            ▼
Feedback Engine runs (after verifier)
Computes accuracy stats and writes to Supabase
            │
            ▼
Every scan: ai_service.py pulls latest accuracy context
Injects it into Claude's prompt
            │
            ▼
Claude reads its own track record and self-adjusts
confidence scores and signal weighting
```

### Three Layers of Context Fed to Claude

**Layer 1 — Signal Combination Accuracy**
Which indicator combos actually produce wins:
```
RSI recovery + MACD crossover:  79% win rate  (31 trades)
All timeframes aligned:         82% win rate  (17 trades)
Sentiment spike alone:          48% win rate  (25 trades)
Analyst upgrade only:           61% win rate  (19 trades)
```

**Layer 2 — Ticker-Specific History**
How the app has performed on each individual stock:
```
NVDA short-term: 8 wins / 2 losses  (80%)  → trust these
TSLA short-term: 4 wins / 6 losses  (40%)  → be cautious
AAPL medium-term: 7 wins / 3 losses (70%)  → reliable
```

**Layer 3 — Timeframe Calibration**
Which timeframes are most reliable:
```
Short-term:   61% win  (most trades, noisier)
Medium-term:  68% win
Long-term:    74% win  (fewer trades, more reliable)
```

### What Claude Is Told

Every analysis prompt includes this context block:

```
HISTORICAL PERFORMANCE CONTEXT:
Your past accuracy for reference — weight your analysis accordingly.

Signal accuracy (last 60 days):
  RSI + MACD crossover → 79% win (31 trades) — high weight
  All timeframes aligned → 82% win (17 trades) — highest weight
  Sentiment only → 48% win (25 trades) — low weight

This ticker (TSLA) history:
  Short-term: 4/10 wins — be conservative with confidence score
  Medium-term: 6/9 wins — moderate confidence acceptable

Timeframe bias:
  Your short-term calls run 61% accurate overall
  Adjust confidence scores down by ~5-8% for short-term

Instruction: If the primary signal is low-accuracy type,
lower your confidence score. If multiple high-accuracy
signals align, you may raise it. Always explain which
signals you are weighting most heavily and why.
```

### Confidence Calibration Display

Raw confidence is shown alongside historical accuracy so you always see both:

```
NVDA — Short-term
Claude confidence:    85%
Historical accuracy at this confidence band: 71%
Displayed as: "Confidence 85% · historically 71% correct"
```

This is more honest than a raw number. You see what Claude thinks AND what reality has been.

### When Does It Start Working

```
Week 1–2:   No history yet — Claude uses base logic only
Week 3+:    First labeled outcomes feed back in
Month 1:    Signal-level patterns emerge
Month 2+:   Ticker-specific patterns become reliable
Month 3+:   Confidence calibration becomes meaningful
```

The system gets better automatically over time. The longer it runs, the more accurate the feedback context.

### What It Cannot Fix

Being honest about limits:
- Unpredictable events: earnings surprises, macro shocks, breaking news
- Structural market regime changes (bull to bear market)
- Fundamental data quality ceiling from free APIs
- The base rate of stock prediction being inherently difficult

The feedback engine improves **signal weighting and confidence calibration** — it does not manufacture new information that was never in the data.

### New Supabase Table — accuracy_stats

Written by the feedback engine nightly:
```
signal_combo        e.g. "RSI+MACD+allTimeframes"
ticker              e.g. "NVDA" (null = global stat)
timeframe           short / medium / long / all
total_trades        count
wins                count
win_rate            0.0–1.0
avg_return_pct      float
last_updated        datetime
sample_reliable     bool  (true if total_trades >= 15)
```

Only stats with `sample_reliable = true` are injected into Claude's prompt. Below 15 trades, the sample is too small to trust.

---

## System Health Dashboard Page

A dedicated page inside the Streamlit app that shows real-time and historical stats for every component. All data is written to Supabase by the scanner as it runs — this page just reads and displays it. No extra API calls needed.

### What It Shows

**Component Status (live)**
```
Claude API      Online   Budget: $1.23 / $10.00 (12%)
Supabase        Online   Storage: 187MB / 500MB (37%)
Finnhub         Online   Calls today: 312
yfinance        Online   Last fetch: 9:44 AM PT
GitHub Actions  Running  Last scan: 9:30 AM PT
```

**API Traffic Today**
```
yfinance        1,847 price rows fetched     150 stocks
Finnhub         142 news articles parsed     89 stocks
Finnhub         298 sentiment scores         150 stocks
Finnhub         150 analyst ratings          150 stocks
Claude          18 deep analyses run         18 stocks
Supabase        94 reads / 22 writes
```

**Scan History (today)**
```
9:30 AM   150 scanned → 20 analyzed → 8 predictions logged
9:00 AM   150 scanned → 20 analyzed → 5 predictions logged
8:30 AM   150 scanned → 17 analyzed → 3 predictions logged
```

**All-Time Totals**
```
Total stocks scanned:      12,450
Total Claude calls made:   1,840
Total predictions logged:  634
Total API errors caught:   12  (all recovered)
```

**Error Log (last 7 days)**
- Timestamp, component, error type, how it was handled (recovered / skipped / fallback)

### What Gets Logged to Supabase by the Scanner

Every scan run writes a row to a `scan_logs` table:
- timestamp, nasdaq100_count, hot_stock_count, overlap_count, universe_total
- stocks_analyzed, predictions_created
- yfinance_rows_fetched, finnhub_news_fetched, finnhub_sentiment_fetched
- claude_calls_made, claude_cost_usd
- supabase_reads, supabase_writes
- errors_encountered, errors_recovered

This is what powers both the health dashboard and the Telegram health alerts.

---

## File Structure

```
/app.py                          ← Streamlit entrypoint
/services/
    finnhub_service.py           ← news, sentiment, analyst ratings
    yfinance_service.py          ← price history (all timeframes)
    ai_service.py                ← Claude Haiku analysis + prompts
    telegram_service.py          ← send all alert types
    screener_service.py          ← hot score + signal score + ranking
/indicators/
    technicals.py                ← RSI, MACD, MA via pandas-ta
    scoring.py                   ← signal score calculator
/database/
    db.py                        ← Supabase read/write operations
    schema.sql                   ← full table definitions
/scripts/
    nightly_scanner.py           ← runs 8:00 PM PT (full deep scan + predictions)
    price_watcher.py             ← runs every 5 min, market hours 6:30 AM–1:00 PM PT
    midsession_check.py          ← runs 9:45 AM PT (re-scan top 20 + news check)
    prediction_verifier.py       ← runs 8:30 PM PT nightly
    feedback_engine.py           ← runs 8:45 PM PT nightly
    shadow_tracker.py            ← runs with nightly scanner, logs rejected stocks
    opportunity_analyzer.py      ← runs weekly Sunday 8:00 PM PT
    health_monitor.py            ← runs daily 6:00 AM PT
/pages/
    health_dashboard.py          ← Streamlit health dashboard page
    system_evolution.py          ← formula change history + pending suggestions
    deep_dive.py                 ← forensic analysis for any stock (any ticker, any date)
    analysts.py                  ← analyst/writer credibility leaderboard and profiles
/config/
    watchlist.json               ← stock universe config
/.github/workflows/
    nightly_scan.yml             ← cron daily 8:00 PM PT
    price_watcher.yml            ← cron every 5 min Mon–Fri 6:30 AM–1:00 PM PT
    midsession.yml               ← cron daily Mon–Fri 9:45 AM PT
    verifier.yml                 ← cron daily 8:30 PM PT
    feedback.yml                 ← cron daily 8:45 PM PT
    opportunity_analyzer.yml     ← cron weekly Sunday 8:00 PM PT
    health_check.yml             ← cron daily 6:00 AM PT
/requirements.txt
/README.md
```

---

## System Health Monitoring

A separate GitHub Actions job runs every morning at 6:00 AM PT and checks the health of every technical component. If anything needs attention, you get a Telegram alert before the market opens — so you're never caught off guard mid-day.

### What Gets Monitored

#### Anthropic API (Claude)
| Trigger | Threshold | Alert |
|---|---|---|
| Monthly spend approaching limit | > $8 of $10 budget used | Warning |
| Monthly spend at limit | > $9.50 used | Critical — scanner pauses Claude calls |
| API key invalid / rejected | Any 401 error | Immediate alert |
| Repeated API failures | 3 failures in one scan | Alert + fallback to score-only mode |

Fallback behavior: if Claude API is unavailable, the app still runs the signal scorer and shows technical scores — just without AI reasoning. You'll be told this is happening.

#### Supabase Database
| Trigger | Threshold | Alert |
|---|---|---|
| Storage usage high | > 400MB (80% of 500MB) | Warning |
| Storage critical | > 475MB (95% of 500MB) | Critical — cache cleanup triggered automatically |
| Database unreachable | Connection timeout | Immediate alert |
| Slow queries | Response > 5 seconds | Warning |

Automatic action: when storage hits 80%, the health monitor deletes price cache entries older than 30 days. You're notified when this happens.

#### Finnhub API
| Trigger | Threshold | Alert |
|---|---|---|
| Rate limit hit | 429 response | Warning + auto-backoff |
| API key invalid | 401 response | Immediate alert |
| Repeated failures | 3 failures in one scan | Alert + skip news/sentiment for that run |

#### GitHub Actions
| Trigger | Alert |
|---|---|
| Nightly scan job failed | Immediate Telegram alert with error summary |
| Price watcher job failed during market hours | Immediate Telegram alert |
| Verifier or feedback job failed | Telegram alert |
| Nightly scan hasn't run by 8:30 PM PT | Alert — something is stuck |

#### yfinance (Yahoo Finance)
| Trigger | Alert |
|---|---|
| Data fetch fails for > 10 stocks in one scan | Warning — Yahoo may be rate limiting |
| Returns empty data for a stock | Logged silently, stock skipped that run |

---

### What the Health Alert Looks Like on Telegram

```
🔧 SYSTEM HEALTH ALERT — Apr 27, 2026  6:00 AM PT

⚠️ Anthropic API Budget Warning
  Spent: $8.42 of $10.00 this month
  At current usage: limit hit in ~4 days
  Action needed: top up credits at console.anthropic.com

✅ Supabase: OK (187MB / 500MB used)
✅ Finnhub: OK
✅ GitHub Actions: OK (last run 27 min ago)
✅ yfinance: OK
```

If everything is healthy, no message is sent — you only hear from the system when something needs attention.

---

### Health Summary in Morning Reminder

The 6:20 AM morning reminder sent to Telegram includes a one-line system status:

```
System: All components healthy ✅
```

or if there's an issue:

```
System: ⚠️ Claude budget at 84% — check console.anthropic.com
```

---

### Spend Controls (Set Once, Forget)

Set these up during account setup — they're your safety net:

| Service | Control | Where to Set |
|---|---|---|
| Anthropic | Hard monthly spend limit ($10) | console.anthropic.com → Limits |
| Supabase | Email alert at 80% storage | Supabase dashboard → Advisors |
| GitHub Actions | Spending limit $0 (free tier only) | GitHub → Billing settings |

These controls exist independently of the app — even if the health monitor fails, the platforms themselves will stop charging you at the limits you set.

---

## Important Notes

- App is a **decision support tool**, not a crystal ball. It identifies patterns, not certainties.
- All paper trades are logged automatically — no manual input required.
- Short selling alerts always include a note that a margin account is required.
- Supabase free tier pauses after 7 days of inactivity — the nightly 8:00 PM scan connects daily, preventing this automatically.
- All times displayed in Pacific Time (Seattle).
- The accuracy feedback loop (which signals work, which don't) is the most valuable long-term feature.

---

---

## Deep Dive Page — Forensic Stock Analysis

A dedicated page for investigating any stock — in or out of the nightly universe — to understand why a rally or crash happened and what signals could have caught it earlier.

### How It Works

User selects a stock from a **search field with autocomplete** (any valid US ticker, not limited to the 100–150 nightly universe). Multiple stocks can be selected for comparison. Optionally selects a date range or uses "recent event" (auto-detects the largest move in the last 90 days).

Clicking **"Analyze"** runs a full forensic analysis on that stock and generates three outputs:

**1. Event Timeline**
- Annotated candlestick price chart with volume
- Vertical markers for: indicator crossovers, news publication dates, analyst rating changes, volume spikes
- Each marker links to the source (news article or indicator event)

**2. Signal Autopsy**
- For each indicator in the current formula: did it fire before the move? How many days in advance?
- Table comparing "signals present" vs "signals caught by formula"
- Highlights missed signals — indicators that fired but weren't scored highly enough to surface the stock

**3. Formula Improvement Suggestions**
- Claude analyzes the full picture and writes specific suggestions
- e.g., "Adding volume-RSI divergence would have flagged NVDA 2 days earlier than the MACD crossover alone"
- Each suggestion is sent to the System Evolution page approval queue as `PENDING`
- Suggestions never auto-apply — you review and approve or reject them, same as shadow portfolio findings

### Any Stock, Unrestricted

The Deep Dive search covers any valid US ticker. You can analyze:
- A biotech that crashed on FDA news last month
- A meme stock that went viral this week
- A competitor to a stock already in your universe
- Any historical event (e.g., NVDA's run-up in 2023)

Analysis works on historical data — yfinance provides full OHLCV history, Finnhub provides news going back ~1 year (note: news depth degrades beyond 12 months, but indicator analysis is unlimited).

### Persistent Approval Queue

Every suggestion generated by Deep Dive analysis (or shadow portfolio weekly analysis, or nightly feedback engine) goes into the same `formula_suggestions` table in Supabase with status `PENDING`. 

Key behaviors:
- **No expiry** — suggestions sit as `PENDING` indefinitely until you act
- **No auto-apply** — the formula never changes without your explicit approval
- **Approve a month later, a year later** — no issues; the evidence and context are stored with the suggestion
- The System Evolution page sorts `PENDING` items at the top with oldest first so nothing gets buried

### Forensic Session Logging

Every forensic analysis is logged to a `forensic_sessions` Supabase table:
```
ticker, analyzed_on, date_range_start, date_range_end,
move_detected_pct, move_direction,
signals_that_fired (JSON), signals_missed (JSON),
suggestions_generated (count),
session_source (deep_dive / shadow_portfolio / feedback_engine)
```

This creates an audit trail of every investigation — useful for seeing patterns across multiple forensic sessions over time.

### New File: `/pages/deep_dive.py`

Added to the Streamlit app as a separate page.

---

## Analyst Credibility Tracker

Tracks the reliability of every analyst and article writer whose work influences a prediction. Over time this builds a leaderboard of which voices are actually predictive vs. noise.

### How It Works

When a prediction closes as WIN or LOSS, the system goes back to the Finnhub articles that were cited in Claude's analysis, extracts the author name and publication, and updates their credibility record in Supabase.

This is fully automatic — no manual tagging needed.

### Analyst Score — Both Binary and Weighted

Each analyst has both scores displayed:

**Binary score** — simple +1 per WIN, -1 per LOSS:
```
Sarah Chen  Binary: +6  (8 wins, 2 losses)
Mike Ross   Binary: +2  (6 wins, 4 losses)
```

**Weighted score** — scaled by return magnitude:
```
Formula: return_pct / 5, rounded to nearest 0.5, capped at ±5 per prediction

Sarah Chen  Weighted: +18  (her wins avg +12%, losses avg -2%)
Mike Ross   Weighted: -3   (his wins avg +1%, losses avg -15%)
```

The same binary record (+2) can hide a highly profitable analyst or a genuinely dangerous one. Weighted score reveals the difference.

### Analyst Profile Card (expandable)

Each analyst entry expands to show:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Sarah Chen  |  Seeking Alpha
Binary: +6  |  Weighted: +18.5
Win Rate: 80%  |  10 predictions tracked
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WINS (8)
  NVDA +15.2%  Apr 12  "NVDA AI Infrastructure..."  [link]
  AAPL +8.7%   Mar 28  "Apple Services Margin..."   [link]
  ...

LOSSES (2)
  TSLA -9.1%   Mar 5   "Tesla FSD Timeline..."      [link]
  ...

SECTOR BREAKDOWN
  Semiconductors: 5/5 wins  (+22 weighted)   ✅ Strong
  EVs:            1/3 wins  (-6 weighted)    ⚠️ Weak

TIMEFRAME FIT
  Short-term:   3/6 wins  (50%)   — less reliable
  Medium-term:  5/5 wins  (100%)  — very reliable

RECENCY
  All-time: +18.5 weighted
  Last 90 days: +12.0  (still consistent ✅)

LEAD TIME
  Avg days before move: 2.4 days  ← predictive, not recapping
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Lead Time — Predictors vs. Recappers

Days between article publication and the start of the price move. Tracked per analyst and shown in their profile.

- **Lead time > 0 days** = article came before the move — predictive
- **Lead time ≈ 0 days** = same-day — could be either
- **Lead time < 0 days** = article came after the move started — recapping, not predicting

An analyst with avg lead time of +3 days and high win rate is the most valuable signal. An analyst with avg lead time of -1 day has zero predictive value regardless of win rate.

### Sector Reliability

Analysts are often experts in specific sectors. The profile shows win rate and weighted score broken out by sector — so you know to trust Sarah Chen on semiconductors but not on EVs, even if her overall score is strong.

### Recency Trend

All-time score vs. last 90 days displayed side by side. An analyst who was great in 2024 but has been wrong recently is flagged with a ⚠️ recency warning. Past glory doesn't override recent performance.

### Timeframe Fit

Some analysts write macro thesis pieces (better for long-term holds), others write momentum pieces (better for short-term entries). Win rate is shown separately for short / medium / long timeframe predictions.

### Publication-Level Score

Alongside individual analyst scores, each publication (Seeking Alpha, Reuters, Benzinga, etc.) gets an aggregate score from all its writers. Useful when bylines are inconsistent or an article is published without a clear author name.

### Cited-but-Ignored

Tracks analysts who wrote bullish/bearish articles on stocks that Claude *rejected* (scored below threshold), where the stock subsequently moved significantly. These are "missed signals we ignored." A high score in this column is a flag to weight this analyst's future articles more heavily in the scoring formula.

### Supabase Tables

**analysts**
```
analyst_id (UUID), name, publication,
binary_score, weighted_score,
total_predictions, wins, losses,
avg_lead_time_days,
created_at, last_updated
```

**analyst_predictions** — one row per analyst-prediction link
```
analyst_id, prediction_id (FK to predictions table),
article_title, article_url, article_published_at,
lead_time_days (article pub → move start),
outcome (WIN / LOSS / PENDING),
return_pct, weighted_contribution,
timeframe, sector
```

**publication_scores** — aggregate by outlet
```
publication_name, binary_score, weighted_score,
total_predictions, win_rate, last_updated
```

### New File: `/pages/analysts.py`

Added to the Streamlit app as a dedicated Analysts page, showing the full leaderboard and expandable profiles.

---

## Status

**Spec finalized. Ready to build.**

Next steps:
1. Set up all accounts (GitHub, Streamlit Cloud, Supabase, Anthropic Console, Finnhub, Telegram BotFather)
2. Create GitHub repo
3. Begin implementation starting with database schema and core services
