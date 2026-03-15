"""
Main pipeline: for each filtered market, run all signals in parallel,
aggregate, check risk, and execute if warranted.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from typing import Optional

from config import constants
from db import repository
from execution.trade_engine import execute_trade
from signals import (
    SignalResult, aggregate,
    llm_signal, news_signal, research_signal, metaculus_signal, gdelt_signal,
)
from utils.logging import get_logger
from utils.normalizer import MarketSchema

log = get_logger(__name__)


def _run_signals_for_market(market: MarketSchema) -> list[SignalResult]:
    """Run all signal providers in parallel for a single market."""
    signal_fns = [
        ("llm",       lambda: llm_signal.run(market)),
        ("news",      lambda: news_signal.run(market)),
        ("research",  lambda: research_signal.run(market)),
        ("metaculus", lambda: metaculus_signal.run(market)),
        ("gdelt",     lambda: gdelt_signal.run(market)),
    ]

    results: list[SignalResult] = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(fn): name for name, fn in signal_fns}
        for future in as_completed(futures, timeout=90):
            name = futures[future]
            try:
                sig = future.result()
                results.append(sig)
                log.debug(f"Signal {name}: p={sig.probability:.3f} conf={sig.confidence:.2f}")
            except Exception as exc:
                log.warning(f"Signal {name} failed for {market.id[:20]}: {exc}")

    return results


def process_market(market: MarketSchema) -> bool:
    """
    Full pipeline for one market.
    Returns True if a trade was placed.
    """
    log.debug(f"Processing: {market.platform} | {market.title[:60]}")

    # 1. Run signals
    signals = _run_signals_for_market(market)

    # 2. Aggregate
    agg = aggregate(signals, market.yes_price)
    if agg is None:
        log.debug(f"Insufficient signals for {market.id[:20]}")
        return False

    # 3. Persist signal runs
    weights = repository.get_signal_weights()
    signal_run_ids = []
    for sig in signals:
        run_id = repository.insert_signal_run({
            "market_id": market.id,
            "signal_source": sig.source,
            "raw_probability": sig.probability,
            "confidence": sig.confidence,
            "weight_used": weights.get(sig.source, 0.1),
            "metadata": sig.metadata,
        })
        signal_run_ids.append(run_id)

    # 4. Determine trade decision
    decision = "pass"
    kelly_pct = None
    actual_pct = None

    if agg.abs_edge >= constants.MIN_EDGE:
        from config.settings import get_settings as _gs
        from risk.kelly import kelly_stake
        bankroll = _gs().bankroll(market.platform)
        raw_stake = kelly_stake(agg.aggregated_prob, agg.market_implied_prob, agg.side, bankroll)
        kelly_pct = round(raw_stake / bankroll, 4) if bankroll > 0 else 0
        decision = f"trade_{agg.side}"
    else:
        decision = "insufficient_edge"

    # 5. Persist evaluation
    eval_id = repository.insert_evaluation({
        "market_id": market.id,
        "aggregated_prob": agg.aggregated_prob,
        "market_implied_prob": agg.market_implied_prob,
        "edge": agg.edge,
        "decision": decision,
        "kelly_stake_pct": kelly_pct,
        "actual_stake_pct": actual_pct,
        "signal_run_ids": signal_run_ids,
    })

    # 6. Execute if warranted
    if decision.startswith("trade_"):
        traded = execute_trade(market, agg, eval_id, signal_run_ids)
        return traded

    return False


def run_pipeline(markets: list[MarketSchema]) -> dict:
    """
    Run the pipeline for all markets concurrently.
    Returns summary stats.
    """
    if not markets:
        return {"processed": 0, "traded": 0, "errors": 0}

    processed = 0
    traded = 0
    errors = 0

    # Process markets in parallel batches (max 4 at a time to manage API rate limits)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(process_market, m): m for m in markets}
        for future in as_completed(futures, timeout=300):
            m = futures[future]
            try:
                result = future.result()
                processed += 1
                if result:
                    traded += 1
            except Exception as exc:
                log.error(f"Pipeline error for {m.id[:20]}: {exc}")
                errors += 1

    log.info(f"Pipeline complete: processed={processed} traded={traded} errors={errors}")
    return {"processed": processed, "traded": traded, "errors": errors}
