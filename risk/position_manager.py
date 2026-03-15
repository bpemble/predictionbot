"""
Enforces portfolio-level risk constraints before any trade is placed.
"""
from __future__ import annotations

from config import constants
from config.settings import get_settings
from db import repository
from risk.kelly import kelly_stake, min_stake_usd
from signals.aggregator import AggregationResult
from utils.logging import get_logger
from utils.normalizer import MarketSchema

log = get_logger(__name__)


class PositionManager:
    def __init__(self) -> None:
        self.settings = get_settings()

    def compute_stake(
        self,
        market: MarketSchema,
        agg: AggregationResult,
    ) -> tuple[float, str]:
        """
        Returns (stake_usd, reason).
        stake_usd = 0 if the trade should not be placed.
        reason = human-readable explanation.
        """
        paper = self.settings.paper_trade
        bankroll = self.settings.bankroll(market.platform)

        # ── Edge check ────────────────────────────────────────────────────────
        if agg.abs_edge < constants.MIN_EDGE:
            return 0.0, f"Edge {agg.abs_edge:.1%} < min {constants.MIN_EDGE:.1%}"

        # ── Already have a position ────────────────────────────────────────────
        if repository.has_open_trade_for_market(market.id):
            return 0.0, "Already have an open position in this market"

        # ── Kelly stake ────────────────────────────────────────────────────────
        raw_stake = kelly_stake(
            our_prob=agg.aggregated_prob,
            market_price=agg.market_implied_prob,
            side=agg.side,
            bankroll=bankroll,
        )

        if raw_stake <= 0:
            return 0.0, "Kelly formula returned zero or negative stake"

        # ── Total exposure cap ─────────────────────────────────────────────────
        open_exposure = repository.get_open_exposure_usd(market.platform, paper)
        remaining_budget = bankroll * constants.MAX_TOTAL_EXPOSURE - open_exposure
        if remaining_budget <= 0:
            return 0.0, f"Total exposure cap reached (${open_exposure:.0f} / ${bankroll * constants.MAX_TOTAL_EXPOSURE:.0f})"

        stake = min(raw_stake, remaining_budget)

        # ── Minimum order size ────────────────────────────────────────────────
        min_stake = min_stake_usd(market.platform)
        if stake < min_stake:
            return 0.0, f"Stake ${stake:.2f} < min ${min_stake:.2f} for {market.platform}"

        reason = (
            f"edge={agg.abs_edge:.1%} side={agg.side} "
            f"stake=${stake:.2f} exposure=${open_exposure:.0f}/${bankroll * constants.MAX_TOTAL_EXPOSURE:.0f}"
        )
        return stake, reason
