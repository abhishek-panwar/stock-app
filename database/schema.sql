-- Run this once in Supabase SQL Editor to create all tables

-- Enable UUID generation
create extension if not exists "pgcrypto";

-- ── Predictions ───────────────────────────────────────────────────────────────
create table if not exists predictions (
  id                  uuid primary key default gen_random_uuid(),
  ticker              text not null,
  predicted_on        timestamptz not null,
  timeframe           text not null check (timeframe in ('short', 'medium', 'long')),
  direction           text not null check (direction in ('BULLISH', 'BEARISH', 'NEUTRAL')),
  position            text not null check (position in ('LONG', 'SHORT', 'HOLD')),
  confidence          integer not null check (confidence between 0 and 100),
  score               integer not null check (score between 0 and 100),
  price_at_prediction numeric(10,2),
  buy_range_low       numeric(10,2),
  buy_range_high      numeric(10,2),
  target_low          numeric(10,2),
  target_high         numeric(10,2),
  stop_loss           numeric(10,2),
  reasoning           text,
  source              text,  -- nasdaq100 / hot_stock / both
  formula_version     text default 'v1.0',
  verified_on         timestamptz,
  price_at_close      numeric(10,2),
  outcome             text default 'PENDING' check (outcome in ('WIN', 'LOSS', 'PENDING')),
  return_pct          numeric(6,2),
  closed_reason       text check (closed_reason in ('TARGET_HIT', 'STOP_LOSS', 'EXPIRED', null)),
  created_at          timestamptz default now()
);

-- ── Scan Logs ─────────────────────────────────────────────────────────────────
create table if not exists scan_logs (
  id                      uuid primary key default gen_random_uuid(),
  timestamp               timestamptz not null default now(),
  nasdaq100_count         integer,
  hot_stock_count         integer,
  overlap_count           integer,
  universe_total          integer,
  stocks_analyzed         integer,
  predictions_created     integer,
  yfinance_rows_fetched   integer,
  finnhub_news_fetched    integer,
  finnhub_sentiment_fetched integer,
  claude_calls_made       integer,
  claude_cost_usd         numeric(8,4),
  supabase_reads          integer,
  supabase_writes         integer,
  errors_encountered      integer default 0,
  errors_recovered        integer default 0,
  scan_type               text default 'nightly'
);

-- ── Shadow Portfolio ──────────────────────────────────────────────────────────
create table if not exists shadow_prices (
  id                  uuid primary key default gen_random_uuid(),
  ticker              text not null,
  scan_timestamp      timestamptz not null,
  score_at_rejection  integer,
  price               numeric(10,2),
  volume              bigint,
  rsi                 numeric(6,2),
  macd_signal         numeric(8,4),
  bb_squeeze          boolean,
  volume_surge_ratio  numeric(6,2),
  obv_trend           text,
  formula_version     text default 'v1.0',
  created_at          timestamptz default now()
);

create table if not exists missed_opportunities (
  id                  uuid primary key default gen_random_uuid(),
  ticker              text not null,
  rejection_date      timestamptz not null,
  score_at_rejection  integer,
  move_pct            numeric(6,2),
  move_direction      text,
  days_to_move        integer,
  signals_present     jsonb,
  formula_version     text default 'v1.0',
  created_at          timestamptz default now()
);

-- ── Formula Evolution ─────────────────────────────────────────────────────────
create table if not exists formula_suggestions (
  id                      uuid primary key default gen_random_uuid(),
  suggestion_date         timestamptz not null default now(),
  suggested_by            text default 'claude',
  source                  text,  -- shadow_portfolio / deep_dive / feedback_engine
  plain_english           text,
  technical_detail        text,
  indicator_to_add        text,
  current_weight          numeric(6,2),
  suggested_weight        numeric(6,2),
  evidence                jsonb,
  projected_improvement   numeric(5,2),
  status                  text default 'PENDING' check (status in ('PENDING', 'APPROVED', 'REJECTED', 'REMIND_LATER')),
  reviewed_on             timestamptz,
  reviewed_by             text
);

create table if not exists formula_history (
  id                  uuid primary key default gen_random_uuid(),
  version             text not null,
  applied_on          timestamptz not null default now(),
  change_description  text,
  plain_english       text,
  technical_detail    text,
  evidence            jsonb,
  changed_by          text default 'user-approved',
  win_rate_before     numeric(5,2),
  win_rate_after      numeric(5,2)
);

-- ── Accuracy Stats ────────────────────────────────────────────────────────────
create table if not exists accuracy_stats (
  id              uuid primary key default gen_random_uuid(),
  signal_combo    text not null,
  ticker          text,
  timeframe       text,
  total_trades    integer default 0,
  wins            integer default 0,
  win_rate        numeric(5,4),
  avg_return_pct  numeric(6,2),
  last_updated    timestamptz default now(),
  sample_reliable boolean default false,
  unique (signal_combo, ticker, timeframe)
);

-- ── Analysts ──────────────────────────────────────────────────────────────────
create table if not exists analysts (
  id                  uuid primary key default gen_random_uuid(),
  name                text not null,
  publication         text,
  binary_score        integer default 0,
  weighted_score      numeric(8,2) default 0,
  total_predictions   integer default 0,
  wins                integer default 0,
  losses              integer default 0,
  avg_lead_time_days  numeric(6,2),
  created_at          timestamptz default now(),
  last_updated        timestamptz default now(),
  unique (name, publication)
);

create table if not exists analyst_predictions (
  id                    uuid primary key default gen_random_uuid(),
  analyst_id            uuid references analysts(id),
  prediction_id         uuid references predictions(id),
  article_title         text,
  article_url           text,
  article_published_at  timestamptz,
  lead_time_days        numeric(6,2),
  outcome               text default 'PENDING',
  return_pct            numeric(6,2),
  weighted_contribution numeric(6,2),
  timeframe             text,
  sector                text,
  created_at            timestamptz default now()
);

create table if not exists publication_scores (
  id                  uuid primary key default gen_random_uuid(),
  publication_name    text not null unique,
  binary_score        integer default 0,
  weighted_score      numeric(8,2) default 0,
  total_predictions   integer default 0,
  win_rate            numeric(5,4),
  last_updated        timestamptz default now()
);

-- ── Forensic Sessions ─────────────────────────────────────────────────────────
create table if not exists forensic_sessions (
  id                      uuid primary key default gen_random_uuid(),
  ticker                  text not null,
  analyzed_on             timestamptz not null default now(),
  date_range_start        date,
  date_range_end          date,
  move_detected_pct       numeric(6,2),
  move_direction          text,
  signals_that_fired      jsonb,
  signals_missed          jsonb,
  suggestions_generated   integer default 0,
  session_source          text default 'deep_dive'
);
