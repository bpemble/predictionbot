"""
Fractional Kelly criterion for prediction market position sizing.

For a binary market:
  Buying YES at market price p with our estimate q:
    Kelly fraction f* = (q - p) / (1 - p)

  Buying NO at implied price (1-p) with our estimate of NO = (1-q):
    Kelly fraction f* = ((1-q) - (1-p)) / p = (p - q) / p

We apply KELLY_FRACTION multiplier and cap at MAX_PER_TRADE_PCT.
"""
from __future__ import annotations

from config import constants
from utils.logging import get_logger

log = get_logger(__name__)


def kelly_stake(
    our_prob: float,
    market_price: float,
    side: str,
    bankroll: float,
) -> float:
    """
    Returns the recommended stake in USD.

    our_prob:    aggregated probability of YES resolution (0-1)
    market_price: current market implied YES probability (0-1)
    side:         'yes' or 'no' — which side we're betting
    bankroll:     current available capital in USD
    """
    if side == "yes":
        # Buying YES: cost = market_price per share, payout = 1 per share
        if market_price >= 0.99:
            return 0.0
        f_star = (our_prob - market_price) / (1.0 - market_price)
    else:
        # Buying NO: cost = (1-market_price) per share, payout = 1 per share
        no_price = 1.0 - market_price
        no_prob = 1.0 - our_prob
        if no_price >= 0.99:
            return 0.0
        f_star = (no_prob - no_price) / (1.0 - no_price)

    if f_star <= 0:
        return 0.0

    # Apply fractional Kelly
    f_applied = f_star * constants.KELLY_FRACTION

    # Cap at max per-trade percentage
    f_capped = min(f_applied, constants.MAX_PER_TRADE_PCT)

    stake = f_capped * bankroll
    log.debug(
        f"Kelly: f*={f_star:.3f} applied={f_applied:.3f} capped={f_capped:.3f} "
        f"stake=${stake:.2f} (bankroll=${bankroll:.0f})"
    )
    return stake


def min_stake_usd(platform: str) -> float:
    """Minimum order size per platform."""
    return 5.0 if platform == "polymarket" else 1.0
