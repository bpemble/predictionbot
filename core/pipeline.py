"""
Main pipeline: info-asymmetry optimised.

Two-pass strategy:
  Pass 1 (batch): Run cross_market analysis across ALL candidate markets at once.
                  This is free (no API calls) and finds logical inconsistencies.

  Pass 2 (per market, parallel): Run LLM + resolution_analyzer + news + research
                  + metaculus + gdelt signals concurrently for each market.
                  Inject the cross_market result for that market if one exists.

Markets are executed in order of computed edge (highest first).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from config import constants
from config.settings import get_settings
from db import repository
from execution.trade_engine import execute_trade
from signals import (
    SignalResult, aggregate,
    llm_signal, news_signal, research_signal,
    metaculus_signal, gdelt_signal,
    resolution_analyzer, cross_market,
)
from utils.logging import get_logger
from utils.normalizer import MarketSchema

log = get_logger(__name__)


def _run_signals_for_market(
    market: MarketSchema,
    cross_market_signal: SignalResult | None = None,
) -> list[SignalResult]:
    """Run all per-market signal providers in parallel."""
    signal_fns = [
        ("llm",        lambda: llm_signal.run(market)),
        ("news",       lambda: news_signal.run(market)),
        ("research",   lambda: research_signal.run(market)),
        ("metaculus",  lambda: metaculus_signal.run(market)),
        ("gdelt",      lambda: gdelt_signal.run(market)),
        ("resolution", lambda: resolution_analyzer.run(market)),
    ]

    results: list[SignalResult] = []

    # Inject cross-market signal if available (already computed)
    if cross_market_signal is not None:
        results.append(cross_market_signal)

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fn): name for name, fn in signal_fns}
        for future in as_completed(futures, timeout=90):
            name = futures[future]
            try:
                sig = future.result()
                results.append(sig)
                log.debug(f"Signal {name} [{market.id[:12]}]: p={sig.probability:.3f} conf={sig.confidence:.2f}")
            except Exception as exc:
                log.warning(f"Signal {name} failed for {market.id[:20]}: {exc}")

    return results


def process_market(
    market: MarketSchema,
    cross_market_signal: SignalResult | None = None,
) -> tuple[MarketSchema, float, int] | None:
    """
    Full pipeline for one market.
    Returns (market, abs_edge, eval_id) if the market has tradeable edge, else None.
    """
    log.debug(f"Processing: {market.platform} | {market.title[:60]}")

    signals = _run_signals_for_market(market, cross_market_signal)
    agg = aggregate(signals, market.yes_price)

    if agg is None:
        log.debug(f"Insufficient signals for {market.id[:20]}")
        return None

    # Persist signal runs
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

    # Compute Kelly stake for logging
    decision = "insufficient_edge"
    kelly_pct = None

    if agg.abs_edge >= constants.MIN_EDGE:
        from config.settings import get_settings as _gs
        from risk.kelly import kelly_stake
        bankroll = _gs().bankroll(market.platform)
        raw_stake = kelly_stake(agg.aggregated_prob, agg.market_implied_prob, agg.side, bankroll)
        kelly_pct = round(raw_stake / bankroll, 4) if bankroll > 0 else 0
        decision = f"trade_{agg.side}"

    eval_id = repository.insert_evaluation({
        "market_id": market.id,
        "aggregated_prob": agg.aggregated_prob,
        "market_implied_prob": agg.market_implied_prob,
        "edge": agg.edge,
        "decision": decision,
        "kelly_stake_pct": kelly_pct,
        "actual_stake_pct": None,
        "signal_run_ids": signal_run_ids,
    })

    if decision.startswith("trade_"):
        return (market, agg.abs_edge, eval_id, agg, signal_run_ids)

    return None


def run_pipeline(markets: list[MarketSchema]) -> dict:
    """
    Info-asymmetry optimised pipeline:
    1. Run cross-market analysis across the full batch (free, no API calls)
    2. Run per-market signals in parallel
    3. Execute trades in order of edge (highest edge first)
    """
    if not markets:
        return {"processed": 0, "traded": 0, "errors": 0}

    # ── Pass 1: Cross-market analysis (free, batch) ───────────────────────────
    log.info(f"Running cross-market analysis on {len(markets)} markets...")
    cross_signals = cross_market.run_all(markets)
    if cross_signals:
        log.info(f"Cross-market: {len(cross_signals)} inconsistency signals found")

    # ── Pass 2: Per-market signals (parallel) ────────────────────────────────
    processed = 0
    errors = 0
    tradeable: list[tuple] = []  # (market, abs_edge, eval_id, agg, signal_run_ids)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(
                process_market,
                m,
                cross_signals.get(m.id),
            ): m
            for m in markets
        }
        for future in as_completed(futures, timeout=300):
            m = futures[future]
            try:
                result = future.result()
                processed += 1
                if result is not None:
                    tradeable.append(result)
            except Exception as exc:
                log.error(f"Pipeline error for {m.id[:20]}: {exc}")
                errors += 1

    # ── Pass 3: Execute in order of edge (largest edge first) ────────────────
    tradeable.sort(key=lambda x: x[1], reverse=True)
    traded = 0
    for market, abs_edge, eval_id, agg, signal_run_ids in tradeable:
        if execute_trade(market, agg, eval_id, signal_run_ids):
            traded += 1

    log.info(f"Pipeline complete: processed={processed} tradeable={len(tradeable)} traded={traded} errors={errors}")
    return {"processed": processed, "traded": traded, "errors": errors}
