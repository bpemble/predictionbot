"""
Autonomous position monitor — runs every 30 minutes alongside the scan cycle.

Manages exits across three triggers:

  1. EDGE REALIZED   — market price has converged toward our estimate;
                       residual edge < MIN_EDGE.  Lock in the gain and redeploy.

  2. LOW IRR         — some edge remains but the annualised return on that
                       residual is below MIN_IRR_TO_HOLD.  Capital is better
                       deployed elsewhere.

  3. ADVERSE SIGNAL  — market has moved sharply against our position (≥ 25 pp).
                       Re-runs lightweight signals; if the model now agrees with
                       the market, exit to cut the loss.

  4. KELLY TRIM LOG  — when optimal stake has shrunk to < 50% of current
                       position value, logs a trim alert (full trim in v2).

For paper trades: P&L is mark-to-market at current price.
For live trades:  sells shares on the CLOB at current bid.
"""
from __future__ import annotations

import json
import math
import requests
from datetime import datetime, timezone
from typing import Optional

from config import constants
from config.settings import get_settings
from db import repository
from utils.logging import get_logger

log = get_logger(__name__)

# ── Exit thresholds ───────────────────────────────────────────────────────────
MIN_IRR_TO_HOLD: float = 0.30          # exit if annualised residual return < 30 %
ADVERSE_MOVE_REEVAL: float = 0.20      # re-run signals if price moved 20 pp against us
ADVERSE_MOVE_HARD_EXIT: float = 0.35   # hard exit if price moved 35 pp against us
KELLY_TRIM_THRESHOLD: float = 0.50     # log trim alert if new Kelly < 50 % of current


def monitor_and_exit() -> dict:
    """
    Main entry point called by the scheduler.
    Returns a summary dict with counts of each action taken.
    """
    settings = get_settings()
    open_trades = repository.get_open_trades()

    if not open_trades:
        log.debug("Position monitor: no open trades.")
        return {"checked": 0, "exited": 0, "trimmed": 0, "held": 0}

    log.info(f"Position monitor: reviewing {len(open_trades)} open positions")

    stats = {"checked": 0, "exited": 0, "trimmed": 0, "held": 0}

    for trade in open_trades:
        stats["checked"] += 1

        market_id  = trade["market_id"]
        platform   = trade["platform"]
        side       = trade["side"]           # 'yes' or 'no'
        entry_price = trade["price"]         # price paid per share at entry
        shares      = trade["shares"]
        cost_usd    = trade["cost_usd"]

        # ── Fetch fresh market price ──────────────────────────────────────────
        market = _fetch_market(platform, market_id)
        if market is None:
            log.debug(f"Could not refresh market {market_id[:20]} — skipping")
            continue

        if market.status == "resolved":
            # Outcome tracker will handle this; don't double-close
            continue

        # Current market price for our side
        if side == "yes":
            current_price = market.yes_price
        else:
            current_price = 1.0 - market.yes_price

        # ── Our stored probability estimate ───────────────────────────────────
        our_prob_yes = _get_our_prob(trade.get("evaluation_id"))
        if our_prob_yes is None:
            log.debug(f"No evaluation for trade {trade['id']} — skipping")
            continue

        our_prob = our_prob_yes if side == "yes" else (1.0 - our_prob_yes)

        # ── Residual edge and metrics ─────────────────────────────────────────
        residual_edge = our_prob - current_price      # positive = still in our favour
        adverse_move  = current_price - entry_price   # positive = market moved against NO
        if side == "yes":
            adverse_move = entry_price - current_price  # positive = market fell vs entry

        days_remaining = _days_to_resolution(market.resolution_date)

        log.debug(
            f"  Trade {trade['id']} {side.upper()} | entry={entry_price:.3f} "
            f"current={current_price:.3f} our_p={our_prob:.3f} "
            f"residual_edge={residual_edge:+.3f} days={days_remaining}"
        )

        # ── Exit trigger 1: edge realised ────────────────────────────────────
        if residual_edge < constants.MIN_EDGE:
            reason = (
                f"edge_realized (residual={residual_edge:+.3f} < "
                f"min={constants.MIN_EDGE:.2f})"
            )
            _exit_trade(trade, current_price, reason, settings.paper_trade)
            stats["exited"] += 1
            continue

        # ── Exit trigger 2: IRR too low ──────────────────────────────────────
        if days_remaining and days_remaining > 0 and current_price > 0:
            expected_return = residual_edge / current_price
            years = days_remaining / 365.0
            try:
                irr = (1.0 + expected_return) ** (1.0 / years) - 1.0
            except (ValueError, ZeroDivisionError):
                irr = 0.0

            if irr < MIN_IRR_TO_HOLD:
                reason = (
                    f"low_irr ({irr:.0%} annualised < {MIN_IRR_TO_HOLD:.0%} threshold, "
                    f"{days_remaining:.0f} days remaining)"
                )
                _exit_trade(trade, current_price, reason, settings.paper_trade)
                stats["exited"] += 1
                continue

        # ── Exit trigger 3: adverse price move ───────────────────────────────
        if adverse_move >= ADVERSE_MOVE_HARD_EXIT:
            reason = (
                f"adverse_move_hard_exit ({adverse_move:.1%} against position, "
                f"exceeds {ADVERSE_MOVE_HARD_EXIT:.0%} threshold)"
            )
            _exit_trade(trade, current_price, reason, settings.paper_trade)
            stats["exited"] += 1
            continue

        if adverse_move >= ADVERSE_MOVE_REEVAL:
            # Market has moved materially against us — re-run lightweight signals
            signal_flip = _reeval_signal(market, our_prob_yes, side)
            if signal_flip:
                reason = (
                    f"signal_flip ({adverse_move:.1%} adverse move + "
                    f"signals now agree with market)"
                )
                _exit_trade(trade, current_price, reason, settings.paper_trade)
                stats["exited"] += 1
                continue
            else:
                log.info(
                    f"  Trade {trade['id']}: {adverse_move:.1%} adverse move but "
                    f"signals still support position — holding"
                )

        # ── Kelly trim check (log only — full trim in v2) ─────────────────────
        current_position_value = shares * current_price
        new_kelly = _kelly_optimal(our_prob, current_price, platform, settings)
        if new_kelly < current_position_value * KELLY_TRIM_THRESHOLD:
            log.info(
                f"  TRIM ALERT trade {trade['id']}: optimal ${new_kelly:.2f} < "
                f"50% of current ${current_position_value:.2f}. "
                f"Consider trimming to lock partial gains."
            )
            stats["trimmed"] += 1

        stats["held"] += 1

    log.info(
        f"Position monitor complete: checked={stats['checked']} "
        f"exited={stats['exited']} trim_alerts={stats['trimmed']} held={stats['held']}"
    )
    return stats


# ── Helpers ───────────────────────────────────────────────────────────────────

def _exit_trade(trade: dict, current_price: float, reason: str, paper: bool) -> None:
    """Mark-to-market exit: close the trade at current price."""
    side   = trade["side"]
    shares = trade["shares"]
    cost   = trade["cost_usd"]

    proceeds = shares * current_price
    pnl      = proceeds - cost

    log.info(
        f"EXIT trade {trade['id']} ({trade['platform']} {side.upper()}) | "
        f"reason={reason} | "
        f"pnl=${pnl:+.2f} ({pnl/cost*100:+.1f}%) | "
        f"exit_price={current_price:.3f}"
    )

    if not paper:
        _sell_live(trade, current_price)

    # Record as closed with mark-to-market P&L; no Brier (market didn't resolve)
    repository.close_trade_early(trade["id"], round(pnl, 4), reason)


def _sell_live(trade: dict, current_price: float) -> None:
    """Place a sell order on the live exchange."""
    try:
        if trade["platform"] == "polymarket":
            from clients.polymarket import PolymarketClient
            # For a YES position, sell YES tokens; for NO, sell NO tokens.
            # We need to look up token_id from the market record.
            market_rec = repository.get_market(trade["market_id"])
            if not market_rec:
                log.error(f"Cannot sell live: no market record for {trade['market_id']}")
                return
            raw = requests.get(
                f"https://gamma-api.polymarket.com/markets/{trade['market_id']}",
                timeout=10
            ).json()
            clob_ids = raw.get("clobTokenIds") or []
            if isinstance(clob_ids, str):
                clob_ids = json.loads(clob_ids)
            token_id = clob_ids[0] if trade["side"] == "yes" else (clob_ids[1] if len(clob_ids) > 1 else None)
            if not token_id:
                log.error(f"Cannot sell live: missing token_id for {trade['market_id']}")
                return
            proceeds = trade["shares"] * current_price
            PolymarketClient().place_market_order(token_id, "SELL", proceeds)

        elif trade["platform"] == "kalshi":
            from clients.kalshi import KalshiClient
            KalshiClient().place_market_order(
                ticker=trade["market_id"],
                side="yes" if trade["side"] == "yes" else "no",
                cost_usd=trade["shares"] * current_price,
                yes_price=current_price,
            )
    except Exception as exc:
        log.error(f"Live sell failed for trade {trade['id']}: {exc}")


def _reeval_signal(market, our_prob_yes: float, side: str) -> bool:
    """
    Quick re-evaluation using only Tavily + LLM (no Exa/cross-market).
    Returns True if the new estimate has FLIPPED to agree with the market
    (i.e., we no longer have edge in our original direction).
    """
    try:
        from signals import llm_signal
        result = llm_signal.run(market)
        new_prob = result.probability

        if side == "yes":
            had_edge   = our_prob_yes > market.yes_price
            still_edge = new_prob > market.yes_price + constants.MIN_EDGE
        else:
            had_edge   = (1 - our_prob_yes) > (1 - market.yes_price)
            still_edge = (1 - new_prob) > (1 - market.yes_price) + constants.MIN_EDGE

        flipped = had_edge and not still_edge
        log.debug(
            f"Re-eval: new_prob={new_prob:.3f} side={side} "
            f"had_edge={had_edge} still_edge={still_edge} flipped={flipped}"
        )
        return flipped
    except Exception as exc:
        log.warning(f"Re-eval signal failed: {exc} — holding position")
        return False


def _kelly_optimal(our_prob: float, current_price: float, platform: str, settings) -> float:
    """Current Kelly-optimal stake in USD given refreshed price."""
    from risk.kelly import kelly_stake
    bankroll = settings.bankroll(platform)
    # Pass a synthetic side='yes' since our_prob and current_price are already
    # normalised for the correct side in the caller.
    return kelly_stake(
        our_prob=our_prob,
        market_price=current_price,
        side="yes",
        bankroll=bankroll,
    )


def _fetch_market(platform: str, market_id: str):
    try:
        if platform == "polymarket":
            from clients.polymarket import PolymarketClient
            return PolymarketClient().get_market(market_id)
        elif platform == "kalshi":
            from clients.kalshi import KalshiClient
            return KalshiClient().get_market(market_id)
    except Exception as exc:
        log.warning(f"Could not fetch {platform} {market_id[:20]}: {exc}")
    return None


def _get_our_prob(evaluation_id: Optional[int]) -> Optional[float]:
    if evaluation_id is None:
        return None
    try:
        row = repository._get_conn().execute(
            "SELECT aggregated_prob FROM evaluations WHERE id=?", (evaluation_id,)
        ).fetchone()
        return float(row["aggregated_prob"]) if row else None
    except Exception:
        return None


def _days_to_resolution(resolution_date: Optional[str]) -> Optional[float]:
    if not resolution_date:
        return None
    try:
        dt = datetime.fromisoformat(resolution_date.replace("Z", "+00:00"))
        delta = dt - datetime.now(timezone.utc)
        return max(0.0, delta.total_seconds() / 86400)
    except Exception:
        return None
