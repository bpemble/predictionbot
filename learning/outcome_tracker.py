"""
Polls resolved markets, closes open trades, computes PnL and Brier scores.
"""
from __future__ import annotations

from clients.kalshi import KalshiClient
from clients.polymarket import PolymarketClient
from db import repository
from utils.logging import get_logger

log = get_logger(__name__)

_poly = PolymarketClient()
_kalshi = KalshiClient()


def check_and_close_trades() -> int:
    """
    Check all open trades. Close any whose market has resolved.
    Returns the number of trades closed.
    """
    open_trades = repository.get_open_trades()
    if not open_trades:
        log.debug("No open trades to check.")
        return 0

    closed = 0
    for trade in open_trades:
        market_id = trade["market_id"]
        platform = trade["platform"]

        # Fetch current market status
        market = _fetch_market(platform, market_id)
        if market is None:
            continue

        if market.status != "resolved" or market.outcome is None:
            # Update market price in DB even if not resolved
            repository.upsert_market(market.to_db_dict())
            continue

        # Market resolved — close the trade
        repository.mark_market_resolved(market_id, market.outcome)
        outcome_binary = 1.0 if market.outcome == "yes" else 0.0

        # PnL calculation
        side = trade["side"]
        price = trade["price"]
        shares = trade["shares"]

        if side == "yes":
            pnl = (outcome_binary - price) * shares
        else:
            pnl = ((1 - outcome_binary) - price) * shares

        # Brier score: (our_forecast - outcome)^2
        # We need the original aggregated_prob from the evaluation
        agg_prob = _get_aggregated_prob(trade.get("evaluation_id"))
        brier = (agg_prob - outcome_binary) ** 2 if agg_prob is not None else None

        repository.close_trade(trade["id"], round(pnl, 4), brier)

        log.info(
            f"Closed trade {trade['id']}: {platform} {market_id[:20]} "
            f"outcome={market.outcome} side={side} pnl=${pnl:.2f} brier={brier:.4f if brier else 'N/A'}"
        )
        closed += 1

    return closed


def _fetch_market(platform: str, market_id: str):
    try:
        if platform == "polymarket":
            return _poly.get_market(market_id)
        elif platform == "kalshi":
            return _kalshi.get_market(market_id)
    except Exception as exc:
        log.warning(f"Could not fetch {platform} market {market_id}: {exc}")
    return None


def _get_aggregated_prob(evaluation_id: int | None) -> float | None:
    if evaluation_id is None:
        return None
    try:
        from db.repository import _get_conn
        row = _get_conn().execute(
            "SELECT aggregated_prob FROM evaluations WHERE id=?", (evaluation_id,)
        ).fetchone()
        return float(row["aggregated_prob"]) if row else None
    except Exception:
        return None
