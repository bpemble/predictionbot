"""
APScheduler job definitions.
All jobs acquire a named lock to prevent overlapping runs.
"""
from __future__ import annotations

import threading

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler

from config import constants
from config.settings import get_settings
from utils.logging import get_logger

log = get_logger(__name__)

_scan_lock = threading.Lock()
_outcome_lock = threading.Lock()

_scheduler: BackgroundScheduler | None = None


# ─── Job functions ────────────────────────────────────────────────────────────

def job_scan_and_trade():
    if not _scan_lock.acquire(blocking=False):
        log.debug("scan_and_trade already running, skipping.")
        return
    try:
        from core.market_scanner import scan_all_markets
        from core.market_filter import filter_markets
        from core.pipeline import run_pipeline

        log.info("=== Scan & Trade cycle starting ===")
        all_markets = scan_all_markets()
        candidates = filter_markets(all_markets)
        log.info(f"{len(candidates)} markets qualify for signal generation")
        stats = run_pipeline(candidates)
        log.info(f"=== Scan & Trade complete: {stats} ===")
    except Exception as exc:
        log.error(f"scan_and_trade job error: {exc}", exc_info=True)
    finally:
        _scan_lock.release()


def job_check_outcomes():
    if not _outcome_lock.acquire(blocking=False):
        log.debug("check_outcomes already running, skipping.")
        return
    try:
        from learning.outcome_tracker import check_and_close_trades
        n = check_and_close_trades()
        log.info(f"Outcome check: closed {n} trades")
    except Exception as exc:
        log.error(f"check_outcomes job error: {exc}", exc_info=True)
    finally:
        _outcome_lock.release()


def job_calibrate_weights():
    try:
        from learning.weight_calibrator import calibrate
        calibrate()
    except Exception as exc:
        log.error(f"calibrate_weights job error: {exc}", exc_info=True)


def job_snapshot_bankroll():
    try:
        from clients.kalshi import KalshiClient
        from clients.polymarket import PolymarketClient
        from db import repository

        settings = get_settings()
        paper = settings.paper_trade

        if settings.polymarket_enabled():
            balance = PolymarketClient().get_balance_usdc()
            exposure = repository.get_open_exposure_usd("polymarket", paper)
            repository.insert_bankroll_snapshot("polymarket", balance, exposure, paper)

        if settings.kalshi_enabled():
            balance = KalshiClient().get_balance_usd()
            exposure = repository.get_open_exposure_usd("kalshi", paper)
            repository.insert_bankroll_snapshot("kalshi", balance, exposure, paper)

        log.info("Bankroll snapshot saved.")
    except Exception as exc:
        log.error(f"snapshot_bankroll job error: {exc}", exc_info=True)


def job_health_check():
    try:
        from db import repository
        open_trades = repository.get_open_trades()
        log.info(f"Health check: {len(open_trades)} open trades")
    except Exception as exc:
        log.error(f"health_check job error: {exc}", exc_info=True)


# ─── Scheduler setup ─────────────────────────────────────────────────────────

def build_scheduler() -> BackgroundScheduler:
    global _scheduler
    settings = get_settings()

    jobstores = {"default": SQLAlchemyJobStore(url=settings.db_url)}
    executors = {"default": ThreadPoolExecutor(max_workers=4)}
    job_defaults = {
        "coalesce": True,
        "max_instances": 1,
        "misfire_grace_time": 300,
    }

    _scheduler = BackgroundScheduler(
        jobstores=jobstores,
        executors=executors,
        job_defaults=job_defaults,
        timezone="UTC",
    )

    _scheduler.add_job(
        job_scan_and_trade,
        "interval",
        seconds=constants.SCAN_INTERVAL_SECONDS,
        id="scan_and_trade",
        replace_existing=True,
    )
    _scheduler.add_job(
        job_check_outcomes,
        "interval",
        seconds=constants.OUTCOME_CHECK_INTERVAL_SECONDS,
        id="check_outcomes",
        replace_existing=True,
    )
    _scheduler.add_job(
        job_calibrate_weights,
        "cron",
        hour=constants.CALIBRATE_HOUR_UTC,
        minute=0,
        id="calibrate_weights",
        replace_existing=True,
    )
    _scheduler.add_job(
        job_snapshot_bankroll,
        "cron",
        hour=constants.SNAPSHOT_HOUR_UTC,
        minute=5,
        id="snapshot_bankroll",
        replace_existing=True,
    )
    _scheduler.add_job(
        job_health_check,
        "interval",
        seconds=constants.HEALTH_CHECK_INTERVAL_SECONDS,
        id="health_check",
        replace_existing=True,
    )

    return _scheduler
