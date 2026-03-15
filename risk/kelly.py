"""
Sharpe-optimal position sizing for prediction markets.

Kelly criterion maximizes E[log(wealth)] — it is NOT Sharpe-optimal.
For a portfolio of binary bets the Sharpe-optimal fraction is:

    f* = edge / (λ · σ²)

where:
    edge = |p_our - p_market|        (expected edge per dollar of exposure)
    σ²   = p_market · (1 - p_market) (Bernoulli variance of the binary outcome)
    λ    = risk aversion parameter    (calibrated via SHARPE_SCALE below)

Compared to Kelly:
    f_kelly  = edge / (1 - p_market)
    f_sharpe = edge / (p_market · (1 - p_market)) = f_kelly / p_market

The key difference: Sharpe explicitly penalises high-variance markets.
A 30pp edge on a 0.50-priced market (σ²=0.25) sizes smaller than the same
edge on a 0.25-priced market (σ²=0.1875), because the 50/50 market has
higher outcome variance for the same informational edge.

SHARPE_SCALE is calibrated so the average position size matches
1/4-Kelly at the median market price (≈0.35), producing equivalent
total bankroll exposure while improving the risk-adjusted distribution.
"""
from __future__ import annotations

import math

from config import constants
from utils.logging import get_logger

log = get_logger(__name__)

# Risk-aversion scalar.  Calibrated so that at p_market=0.35 (median in our
# filtered universe), Sharpe sizing ≈ 1/4-Kelly sizing.
# p*(1-p) at 0.35 = 0.2275 vs (1-p) = 0.65 → ratio = 0.35
# So SHARPE_SCALE ≈ KELLY_FRACTION * 0.35 to preserve expected exposure.
SHARPE_SCALE: float = constants.KELLY_FRACTION * 0.35   # ≈ 0.0875


def kelly_stake(
    our_prob: float,
    market_price: float,
    side: str,
    bankroll: float,
) -> float:
    """
    Returns the Sharpe-optimal stake in USD.

    our_prob:     aggregated YES probability estimate (0–1)
    market_price: market implied YES probability (0–1)
    side:         'yes' or 'no'
    bankroll:     current available capital in USD
    """
    if side == "yes":
        if market_price >= 0.99:
            return 0.0
        edge = our_prob - market_price
        variance = market_price * (1.0 - market_price)
    else:
        no_price = 1.0 - market_price
        no_prob = 1.0 - our_prob
        if no_price >= 0.99:
            return 0.0
        edge = no_prob - no_price
        variance = no_price * (1.0 - no_price)

    if edge <= 0 or variance <= 0:
        return 0.0

    # Sharpe-optimal fraction: f* = edge / (λ · σ²)
    f_star = edge / variance

    # Apply risk-aversion scalar (calibrated to match 1/4-Kelly at median price)
    f_applied = f_star * SHARPE_SCALE

    # Hard cap: never risk more than MAX_PER_TRADE_PCT of bankroll on one position
    f_capped = min(f_applied, constants.MAX_PER_TRADE_PCT)

    stake = f_capped * bankroll

    log.debug(
        f"Sharpe sizing: edge={edge:.3f} σ²={variance:.3f} "
        f"f*={f_star:.3f} applied={f_applied:.3f} capped={f_capped:.3f} "
        f"stake=${stake:.2f}"
    )
    return stake


def sharpe_contribution(edge: float, variance: float, stake_pct: float) -> float:
    """
    Expected Sharpe contribution of a single position.
    Returns (expected_return / std_dev) scaled by position size.
    Useful for ranking positions and portfolio reporting.
    """
    if variance <= 0 or stake_pct <= 0:
        return 0.0
    std_dev = math.sqrt(variance)
    return (edge * stake_pct) / (std_dev * stake_pct)   # simplifies to edge/std_dev


def min_stake_usd(platform: str) -> float:
    """Minimum order size per platform."""
    return 5.0 if platform == "polymarket" else 1.0
