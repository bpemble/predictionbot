"""
Trade engine: routes a sizing decision to either paper_trader or live exchange clients.
"""
from __future__ import annotations

from clients.kalshi import KalshiClient
from clients.polymarket import PolymarketClient
from config.settings import get_settings
from db import repository
from risk.position_manager import PositionManager
from signals.aggregator import AggregationResult
from utils.logging import get_logger
from utils.normalizer import MarketSchema

log = get_logger(__name__)

_poly = PolymarketClient()
_kalshi = KalshiClient()
_pm = PositionManager()


def execute_trade(
    market: MarketSchema,
    agg: AggregationResult,
    evaluation_id: int,
    signal_run_ids: list[int],
) -> bool:
    """
    Attempts to place a trade for the given market + aggregation result.
    Returns True if a trade was placed (paper or live), False otherwise.
    """
    settings = get_settings()

    stake_usd, reason = _pm.compute_stake(market, agg)

    if stake_usd <= 0:
        log.info(f"No trade for {market.id[:20]}: {reason}")
        return False

    log.info(
        f"{'[PAPER] ' if settings.paper_trade else ''}Trade: "
        f"{market.platform} {market.title[:50]} | {agg.side.upper()} "
        f"${stake_usd:.2f} @ {agg.market_implied_prob:.2f} "
        f"(our_p={agg.aggregated_prob:.2f} edge={agg.abs_edge:.1%})"
    )

    entry_price = agg.market_implied_prob if agg.side == "yes" else (1.0 - agg.market_implied_prob)

    if settings.paper_trade:
        order_id = _place_paper(market, agg.side, stake_usd, entry_price)
    else:
        order_id = _place_live(market, agg.side, stake_usd, entry_price)

    if order_id is None:
        log.error(f"Order placement failed for {market.id}")
        return False

    shares = stake_usd / entry_price if entry_price > 0 else 0

    repository.insert_trade({
        "evaluation_id": evaluation_id,
        "market_id": market.id,
        "platform": market.platform,
        "side": agg.side,
        "order_type": "market",
        "price": entry_price,
        "shares": round(shares, 4),
        "cost_usd": round(stake_usd, 2),
        "paper": int(settings.paper_trade),
        "platform_order_id": order_id,
        "status": "open",
    })

    return True


def _place_paper(market: MarketSchema, side: str, stake_usd: float, price: float) -> str:
    log.info(f"[PAPER FILL] {market.platform} {market.id[:20]} {side} ${stake_usd:.2f} @ {price:.3f}")
    return f"paper-{market.id[:12]}-{side}"


def _place_live(
    market: MarketSchema, side: str, stake_usd: float, price: float
) -> str | None:
    if market.platform == "polymarket":
        token_id = market.yes_token_id if side == "yes" else market.no_token_id
        if not token_id:
            log.error(f"Missing token_id for {market.id} side={side}")
            return None
        return _poly.place_market_order(token_id, side.upper(), stake_usd)

    elif market.platform == "kalshi":
        return _kalshi.place_market_order(
            ticker=market.ticker or market.id,
            side=side,
            cost_usd=stake_usd,
            yes_price=market.yes_price,
        )
    else:
        log.error(f"Unknown platform: {market.platform}")
        return None
