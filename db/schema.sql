-- Canonical DDL — all tables.
-- Run via db/repository.py on startup (CREATE IF NOT EXISTS is idempotent).

-- ─────────────────────────────────────────────────────────────────────────────
-- MARKETS
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS markets (
    id                  TEXT PRIMARY KEY,
    platform            TEXT NOT NULL,
    title               TEXT NOT NULL,
    category            TEXT,
    resolution_date     TEXT,
    yes_price           REAL,
    no_price            REAL,
    liquidity_usd       REAL,
    volume_usd          REAL,
    status              TEXT DEFAULT 'open',
    outcome             TEXT,
    first_seen_at       TEXT NOT NULL,
    last_updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_markets_platform_status ON markets(platform, status);

-- ─────────────────────────────────────────────────────────────────────────────
-- SIGNAL RUNS
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS signal_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id           TEXT NOT NULL REFERENCES markets(id),
    run_at              TEXT NOT NULL,
    signal_source       TEXT NOT NULL,
    raw_probability     REAL NOT NULL,
    confidence          REAL,
    weight_used         REAL NOT NULL,
    metadata            TEXT
);

CREATE INDEX IF NOT EXISTS idx_signal_runs_market ON signal_runs(market_id, run_at);

-- ─────────────────────────────────────────────────────────────────────────────
-- EVALUATIONS
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS evaluations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id           TEXT NOT NULL REFERENCES markets(id),
    evaluated_at        TEXT NOT NULL,
    aggregated_prob     REAL NOT NULL,
    market_implied_prob REAL NOT NULL,
    edge                REAL NOT NULL,
    decision            TEXT NOT NULL,
    kelly_stake_pct     REAL,
    actual_stake_pct    REAL,
    signal_run_ids      TEXT
);

-- ─────────────────────────────────────────────────────────────────────────────
-- TRADES
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trades (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id       INTEGER REFERENCES evaluations(id),
    market_id           TEXT NOT NULL REFERENCES markets(id),
    platform            TEXT NOT NULL,
    side                TEXT NOT NULL,
    order_type          TEXT NOT NULL DEFAULT 'market',
    price               REAL NOT NULL,
    shares              REAL NOT NULL,
    cost_usd            REAL NOT NULL,
    paper               INTEGER NOT NULL DEFAULT 1,
    platform_order_id   TEXT,
    status              TEXT DEFAULT 'open',
    opened_at           TEXT NOT NULL,
    closed_at           TEXT,
    pnl_usd             REAL,
    brier_contribution  REAL
);

CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_market ON trades(market_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- SIGNAL WEIGHTS
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS signal_weights (
    signal_source       TEXT PRIMARY KEY,
    weight              REAL NOT NULL DEFAULT 1.0,
    avg_brier_score     REAL,
    n_resolved          INTEGER NOT NULL DEFAULT 0,
    updated_at          TEXT NOT NULL
);

INSERT OR IGNORE INTO signal_weights (signal_source, weight, updated_at)
VALUES
    ('llm',        0.30, datetime('now')),
    ('news',       0.15, datetime('now')),
    ('research',   0.25, datetime('now')),
    ('metaculus',  0.20, datetime('now')),
    ('gdelt',      0.10, datetime('now'));

-- ─────────────────────────────────────────────────────────────────────────────
-- BANKROLL SNAPSHOTS
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bankroll_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshotted_at      TEXT NOT NULL,
    platform            TEXT NOT NULL,
    balance_usd         REAL NOT NULL,
    open_exposure_usd   REAL NOT NULL,
    paper               INTEGER NOT NULL DEFAULT 1
);
