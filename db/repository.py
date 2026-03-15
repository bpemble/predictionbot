"""
All SQLite I/O lives here.  No other module should run raw SQL.
Thread-safe via check_same_thread=False + a module-level lock.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from utils.logging import get_logger

log = get_logger(__name__)

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        raise RuntimeError("Database not initialised — call init_db() first.")
    return _conn


def init_db(db_path: str) -> None:
    global _conn
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    _conn = sqlite3.connect(db_path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    _schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(_schema_path) as f:
        _conn.executescript(f.read())
    _conn.commit()
    log.info(f"Database ready at {db_path}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── Markets ──────────────────────────────────────────────────────────────────

def upsert_market(market: dict) -> None:
    now = _now()
    market.setdefault("first_seen_at", now)
    market["last_updated_at"] = now
    sql = """
        INSERT INTO markets
            (id, platform, title, category, resolution_date,
             yes_price, no_price, liquidity_usd, volume_usd,
             status, outcome, first_seen_at, last_updated_at)
        VALUES
            (:id, :platform, :title, :category, :resolution_date,
             :yes_price, :no_price, :liquidity_usd, :volume_usd,
             :status, :outcome, :first_seen_at, :last_updated_at)
        ON CONFLICT(id) DO UPDATE SET
            yes_price       = excluded.yes_price,
            no_price        = excluded.no_price,
            liquidity_usd   = excluded.liquidity_usd,
            volume_usd      = excluded.volume_usd,
            status          = excluded.status,
            outcome         = excluded.outcome,
            last_updated_at = excluded.last_updated_at
    """
    with _lock:
        _get_conn().execute(sql, market)
        _get_conn().commit()


def get_market(market_id: str) -> Optional[dict]:
    row = _get_conn().execute(
        "SELECT * FROM markets WHERE id = ?", (market_id,)
    ).fetchone()
    return dict(row) if row else None


def get_open_markets(platform: Optional[str] = None) -> list[dict]:
    if platform:
        rows = _get_conn().execute(
            "SELECT * FROM markets WHERE status='open' AND platform=?", (platform,)
        ).fetchall()
    else:
        rows = _get_conn().execute(
            "SELECT * FROM markets WHERE status='open'"
        ).fetchall()
    return [dict(r) for r in rows]


def mark_market_resolved(market_id: str, outcome: str) -> None:
    with _lock:
        _get_conn().execute(
            "UPDATE markets SET status='resolved', outcome=?, last_updated_at=? WHERE id=?",
            (outcome, _now(), market_id),
        )
        _get_conn().commit()


# ─── Signal runs ──────────────────────────────────────────────────────────────

def insert_signal_run(run: dict) -> int:
    run["run_at"] = _now()
    if "metadata" in run and isinstance(run["metadata"], dict):
        run["metadata"] = json.dumps(run["metadata"])
    sql = """
        INSERT INTO signal_runs
            (market_id, run_at, signal_source, raw_probability,
             confidence, weight_used, metadata)
        VALUES
            (:market_id, :run_at, :signal_source, :raw_probability,
             :confidence, :weight_used, :metadata)
    """
    with _lock:
        cur = _get_conn().execute(sql, run)
        _get_conn().commit()
        return cur.lastrowid


# ─── Evaluations ──────────────────────────────────────────────────────────────

def insert_evaluation(ev: dict) -> int:
    ev["evaluated_at"] = _now()
    if "signal_run_ids" in ev and isinstance(ev["signal_run_ids"], list):
        ev["signal_run_ids"] = json.dumps(ev["signal_run_ids"])
    sql = """
        INSERT INTO evaluations
            (market_id, evaluated_at, aggregated_prob, market_implied_prob,
             edge, decision, kelly_stake_pct, actual_stake_pct, signal_run_ids)
        VALUES
            (:market_id, :evaluated_at, :aggregated_prob, :market_implied_prob,
             :edge, :decision, :kelly_stake_pct, :actual_stake_pct, :signal_run_ids)
    """
    with _lock:
        cur = _get_conn().execute(sql, ev)
        _get_conn().commit()
        return cur.lastrowid


# ─── Trades ───────────────────────────────────────────────────────────────────

def insert_trade(trade: dict) -> int:
    trade["opened_at"] = _now()
    sql = """
        INSERT INTO trades
            (evaluation_id, market_id, platform, side, order_type,
             price, shares, cost_usd, paper, platform_order_id, status, opened_at)
        VALUES
            (:evaluation_id, :market_id, :platform, :side, :order_type,
             :price, :shares, :cost_usd, :paper, :platform_order_id, :status, :opened_at)
    """
    with _lock:
        cur = _get_conn().execute(sql, trade)
        _get_conn().commit()
        return cur.lastrowid


def get_open_trades(platform: Optional[str] = None) -> list[dict]:
    if platform:
        rows = _get_conn().execute(
            "SELECT * FROM trades WHERE status='open' AND platform=?", (platform,)
        ).fetchall()
    else:
        rows = _get_conn().execute("SELECT * FROM trades WHERE status='open'").fetchall()
    return [dict(r) for r in rows]


def close_trade(trade_id: int, pnl_usd: float, brier: float) -> None:
    with _lock:
        _get_conn().execute(
            """UPDATE trades SET status='closed', closed_at=?, pnl_usd=?,
               brier_contribution=? WHERE id=?""",
            (_now(), pnl_usd, brier, trade_id),
        )
        _get_conn().commit()


def get_open_exposure_usd(platform: str, paper: bool) -> float:
    row = _get_conn().execute(
        "SELECT COALESCE(SUM(cost_usd),0) FROM trades WHERE status='open' AND platform=? AND paper=?",
        (platform, int(paper)),
    ).fetchone()
    return float(row[0])


def has_open_trade_for_market(market_id: str) -> bool:
    row = _get_conn().execute(
        "SELECT 1 FROM trades WHERE market_id=? AND status='open' LIMIT 1", (market_id,)
    ).fetchone()
    return row is not None


# ─── Signal weights ───────────────────────────────────────────────────────────

def get_signal_weights() -> dict[str, float]:
    rows = _get_conn().execute("SELECT signal_source, weight FROM signal_weights").fetchall()
    return {r["signal_source"]: r["weight"] for r in rows}


def update_signal_weight(source: str, weight: float, avg_brier: float, n: int) -> None:
    with _lock:
        _get_conn().execute(
            """INSERT INTO signal_weights (signal_source, weight, avg_brier_score, n_resolved, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(signal_source) DO UPDATE SET
                   weight=excluded.weight,
                   avg_brier_score=excluded.avg_brier_score,
                   n_resolved=excluded.n_resolved,
                   updated_at=excluded.updated_at""",
            (source, weight, avg_brier, n, _now()),
        )
        _get_conn().commit()


def get_resolved_signal_runs_for_calibration(source: str, window: int) -> list[dict]:
    """Returns the most recent `window` resolved signal runs for a source."""
    rows = _get_conn().execute(
        """
        SELECT sr.raw_probability, m.outcome
        FROM signal_runs sr
        JOIN markets m ON sr.market_id = m.id
        WHERE sr.signal_source = ?
          AND m.status = 'resolved'
          AND m.outcome IS NOT NULL
        ORDER BY sr.run_at DESC
        LIMIT ?
        """,
        (source, window),
    ).fetchall()
    return [dict(r) for r in rows]


# ─── Bankroll snapshots ───────────────────────────────────────────────────────

def insert_bankroll_snapshot(platform: str, balance: float, exposure: float, paper: bool) -> None:
    with _lock:
        _get_conn().execute(
            """INSERT INTO bankroll_snapshots
               (snapshotted_at, platform, balance_usd, open_exposure_usd, paper)
               VALUES (?, ?, ?, ?, ?)""",
            (_now(), platform, balance, exposure, int(paper)),
        )
        _get_conn().commit()
