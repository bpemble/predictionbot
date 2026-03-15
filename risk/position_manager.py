"""
Portfolio-level risk controls for Sharpe-optimal position management.

Three layers beyond per-trade sizing:

  1. TOTAL EXPOSURE CAP      — never deploy more than 60% of bankroll at once
  2. CATEGORY CONCENTRATION  — max 20% of bankroll in any single topic cluster
                               (prevents correlated election/macro blowups)
  3. DRAWDOWN SCALING        — linearly reduce position sizes when in drawdown,
                               reaching 50% reduction at MAX_DRAWDOWN threshold
                               (cuts variance precisely when you can least afford it)
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

# Category concentration: no more than this fraction of bankroll in one topic
MAX_CATEGORY_EXPOSURE_PCT: float = 0.20

# Drawdown scaling: at this drawdown level, positions are halved
MAX_DRAWDOWN_SCALE_PCT: float = 0.15   # 15% drawdown → 50% position reduction

# Correlation: if N or more open trades share category keywords, apply penalty
CORRELATION_PENALTY_THRESHOLD: int = 2
CORRELATION_PENALTY_FACTOR: float = 0.70   # reduce stake by 30% for correlated cluster


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
        """
        paper = self.settings.paper_trade
        bankroll = self.settings.bankroll(market.platform)

        # ── 1. Edge check ─────────────────────────────────────────────────────
        if agg.abs_edge < constants.MIN_EDGE:
            return 0.0, f"Edge {agg.abs_edge:.1%} < min {constants.MIN_EDGE:.1%}"

        # ── 2. Duplicate position guard ───────────────────────────────────────
        if repository.has_open_trade_for_market(market.id):
            return 0.0, "Already have an open position in this market"

        # ── 3. Sharpe-optimal Kelly stake ─────────────────────────────────────
        raw_stake = kelly_stake(
            our_prob=agg.aggregated_prob,
            market_price=agg.market_implied_prob,
            side=agg.side,
            bankroll=bankroll,
        )
        if raw_stake <= 0:
            return 0.0, "Sharpe-Kelly returned zero or negative stake"

        stake = raw_stake

        # ── 4. Total exposure cap ─────────────────────────────────────────────
        open_exposure = repository.get_open_exposure_usd(market.platform, paper)
        max_exposure = bankroll * constants.MAX_TOTAL_EXPOSURE
        remaining_budget = max_exposure - open_exposure
        if remaining_budget <= 0:
            return 0.0, f"Total exposure cap reached (${open_exposure:.0f}/${max_exposure:.0f})"
        stake = min(stake, remaining_budget)

        # ── 5. Category concentration limit ───────────────────────────────────
        category_exposure = self._get_category_exposure(market, paper)
        max_category = bankroll * MAX_CATEGORY_EXPOSURE_PCT
        if category_exposure >= max_category:
            return 0.0, (
                f"Category concentration cap: ${category_exposure:.0f} already "
                f"in '{_market_category(market)}' (max ${max_category:.0f})"
            )
        stake = min(stake, max_category - category_exposure)

        # ── 6. Correlation penalty ─────────────────────────────────────────────
        correlated_count = self._count_correlated_open(market)
        if correlated_count >= CORRELATION_PENALTY_THRESHOLD:
            stake *= CORRELATION_PENALTY_FACTOR
            log.debug(
                f"Correlation penalty: {correlated_count} related open trades → "
                f"stake reduced by {(1-CORRELATION_PENALTY_FACTOR):.0%}"
            )

        # ── 7. Drawdown scaling ────────────────────────────────────────────────
        drawdown_scalar = self._drawdown_scalar(market.platform, bankroll, paper)
        if drawdown_scalar < 1.0:
            stake *= drawdown_scalar
            log.info(f"Drawdown scaling: position reduced to {drawdown_scalar:.0%}")

        # ── 8. Minimum order size ─────────────────────────────────────────────
        min_stake = min_stake_usd(market.platform)
        if stake < min_stake:
            return 0.0, f"Post-adjustment stake ${stake:.2f} < min ${min_stake:.2f}"

        reason = (
            f"edge={agg.abs_edge:.1%} side={agg.side} stake=${stake:.2f} "
            f"exposure=${open_exposure:.0f}/${max_exposure:.0f} "
            f"drawdown_scalar={drawdown_scalar:.2f}"
        )
        return round(stake, 2), reason

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_category_exposure(self, market: MarketSchema, paper: bool) -> float:
        """Sum of open exposure in markets that share the same category cluster."""
        open_trades = repository.get_open_trades(market.platform)
        category = _market_category(market)
        total = 0.0
        for trade in open_trades:
            if trade.get("paper", 1) != int(paper):
                continue
            m = repository.get_market(trade["market_id"])
            if m and _market_category_from_dict(m) == category:
                total += trade["cost_usd"]
        return total

    def _count_correlated_open(self, market: MarketSchema) -> int:
        """Count open trades in topically related markets."""
        open_trades = repository.get_open_trades(market.platform)
        keywords = _title_keywords(market.title)
        count = 0
        for trade in open_trades:
            m = repository.get_market(trade["market_id"])
            if m and _title_keywords(m["title"]) & keywords:
                count += 1
        return count

    def _drawdown_scalar(self, platform: str, bankroll: float, paper: bool) -> float:
        """
        Returns a [0.5, 1.0] scalar based on current drawdown.
        At 0% drawdown → 1.0 (no reduction).
        At MAX_DRAWDOWN_SCALE_PCT drawdown → 0.5 (half size).
        """
        try:
            snapshots = _get_recent_snapshots(platform, paper)
            if len(snapshots) < 2:
                return 1.0
            peak = max(s["balance_usd"] for s in snapshots)
            current = snapshots[-1]["balance_usd"]
            if peak <= 0:
                return 1.0
            drawdown = (peak - current) / peak
            if drawdown <= 0:
                return 1.0
            # Linear scale: 0% DD → 1.0, MAX_DRAWDOWN_SCALE_PCT → 0.5
            scalar = 1.0 - (drawdown / MAX_DRAWDOWN_SCALE_PCT) * 0.5
            return max(0.5, min(1.0, scalar))
        except Exception:
            return 1.0


def _market_category(market: MarketSchema) -> str:
    """Derive a coarse category label for concentration tracking."""
    return _market_category_from_title(market.title, market.category)


def _market_category_from_dict(m: dict) -> str:
    return _market_category_from_title(m.get("title", ""), m.get("category"))


def _market_category_from_title(title: str, category: str | None = None) -> str:
    if category:
        return category.lower().split("/")[0].strip()
    t = title.lower()
    if any(k in t for k in ["election", "president", "minister", "parliament", "senate", "congress"]):
        return "politics"
    if any(k in t for k in ["fed", "rate", "gdp", "inflation", "recession", "treasury"]):
        return "macro"
    if any(k in t for k in ["bitcoin", "crypto", "ethereum", "solana"]):
        return "crypto"
    if any(k in t for k in ["nba", "nfl", "nhl", "mlb", "soccer", "tennis", "golf", "f1"]):
        return "sports"
    if any(k in t for k in ["war", "ceasefire", "invasion", "sanctions", "nuclear"]):
        return "geopolitics"
    return "other"


def _title_keywords(title: str) -> set[str]:
    """Extract meaningful keywords from a title for correlation detection."""
    stop = {"will", "the", "a", "an", "in", "on", "at", "to", "of", "be", "is",
            "are", "by", "for", "win", "won", "does", "do", "get", "have"}
    return {w.lower().strip("?.,!") for w in title.split()
            if len(w) > 3 and w.lower() not in stop}


def _get_recent_snapshots(platform: str, paper: bool) -> list[dict]:
    """Pull the last 30 bankroll snapshots for drawdown calculation."""
    try:
        from db.repository import _get_conn
        rows = _get_conn().execute(
            """SELECT balance_usd, snapshotted_at FROM bankroll_snapshots
               WHERE platform=? AND paper=?
               ORDER BY snapshotted_at DESC LIMIT 30""",
            (platform, int(paper)),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]
    except Exception:
        return []
