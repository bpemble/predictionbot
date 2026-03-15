#!/usr/bin/env python3
"""
Prediction Market Bot — entrypoint.

Usage:
  python main.py                    # Start the bot (uses PAPER_TRADE from .env)
  python main.py --derive-poly-creds  # One-time: derive Polymarket API creds from wallet
  python main.py --run-once          # Run one scan cycle and exit (useful for testing)
  python main.py --report            # Print top 5 open position analytics and exit
"""
from __future__ import annotations

import argparse
import signal
import sys
import time

from dotenv import load_dotenv

load_dotenv()

from config.settings import get_settings
from db.repository import init_db
from scheduler.jobs import build_scheduler, job_scan_and_trade
from utils.logging import setup_logging, get_logger


def main():
    parser = argparse.ArgumentParser(description="Prediction Market Bot")
    parser.add_argument("--derive-poly-creds", action="store_true",
                        help="Derive Polymarket API credentials from private key and exit")
    parser.add_argument("--run-once", action="store_true",
                        help="Run one scan cycle and exit (for testing)")
    parser.add_argument("--report", action="store_true",
                        help="Print top 5 open position analytics and exit")
    parser.add_argument("--pnl", action="store_true",
                        help="Print full P&L dashboard (closed trades, win rate, Brier) and exit")
    args = parser.parse_args()

    settings = get_settings()
    setup_logging(settings.log_level)
    log = get_logger("main")

    log.info("=" * 60)
    log.info("Prediction Market Bot starting")
    log.info(f"Mode: {'PAPER TRADING' if settings.paper_trade else '🔴 LIVE TRADING'}")
    log.info(f"Polymarket: {'enabled' if settings.polymarket_enabled() else 'disabled (no key)'}")
    log.info(f"Kalshi:     {'enabled' if settings.kalshi_enabled() else 'disabled (no key)'}")
    log.info("=" * 60)

    # ── Report modes ──────────────────────────────────────────────────────────
    if args.report:
        init_db(settings.db_path)
        from utils.reporter import print_position_report
        print_position_report(top_n=None)
        return

    if args.pnl:
        init_db(settings.db_path)
        from utils.reporter import print_pnl_report
        print_pnl_report()
        return

    # ── One-time setup ────────────────────────────────────────────────────────
    if args.derive_poly_creds:
        from clients.polymarket import PolymarketClient
        PolymarketClient().derive_api_creds()
        return

    # ── Validate at least one platform is configured ──────────────────────────
    if not settings.polymarket_enabled() and not settings.kalshi_enabled():
        log.warning(
            "No platforms configured. Set POLY_PRIVATE_KEY or KALSHI_API_KEY_ID in .env.\n"
            "The bot will run in demo mode using only public API reads."
        )

    if not settings.anthropic_api_key:
        log.error("ANTHROPIC_API_KEY not set — LLM signal will be unavailable. Set it in .env.")
        sys.exit(1)

    if not settings.tavily_api_key:
        log.warning("TAVILY_API_KEY not set — news context for LLM signal will be degraded.")

    # ── Init database ─────────────────────────────────────────────────────────
    init_db(settings.db_path)

    # ── Run once mode (testing) ───────────────────────────────────────────────
    if args.run_once:
        log.info("Running one scan cycle...")
        job_scan_and_trade()
        log.info("Done.")
        return

    # ── Start scheduler ───────────────────────────────────────────────────────
    scheduler = build_scheduler()
    scheduler.start()

    log.info("Scheduler started. Running first scan immediately...")
    job_scan_and_trade()

    # ── Graceful shutdown ─────────────────────────────────────────────────────
    def _shutdown(signum, frame):
        log.info("Shutting down...")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    log.info("Bot running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        _shutdown(None, None)


if __name__ == "__main__":
    main()
