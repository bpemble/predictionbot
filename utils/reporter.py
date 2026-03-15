"""
Position reporter: computes and displays open position analytics.

Metrics per position:
  - Entry price / current price / our probability
  - Unrealized P&L (mark-to-market at current price)
  - Expected P&L (at our probability, assuming we're right)
  - Expected return %
  - IRR (annualized expected return given days to resolution)
  - Sharpe contribution (edge / σ per position)
  - Information ratio (our edge as a multiple of market uncertainty)
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

from db.repository import _get_conn, get_open_trades, get_market
from utils.logging import get_logger

log = get_logger(__name__)


def _days_to_resolution(resolution_date: Optional[str]) -> Optional[float]:
    if not resolution_date:
        return None
    try:
        dt = datetime.fromisoformat(resolution_date.replace("Z", "+00:00"))
        delta = dt - datetime.now(timezone.utc)
        return max(0.01, delta.total_seconds() / 86400)
    except Exception:
        return None


def _get_evaluation(evaluation_id: Optional[int]) -> Optional[dict]:
    if evaluation_id is None:
        return None
    try:
        row = _get_conn().execute(
            "SELECT aggregated_prob, market_implied_prob, edge FROM evaluations WHERE id=?",
            (evaluation_id,)
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def build_position_report(top_n: int = 5) -> list[dict]:
    """
    Build analytics for the top N open positions by expected P&L.
    Returns a list of position dicts sorted by expected_pnl descending.
    """
    open_trades = get_open_trades()
    positions = []

    for trade in open_trades:
        market = get_market(trade["market_id"])
        if not market:
            continue

        evaluation = _get_evaluation(trade.get("evaluation_id"))
        our_prob = evaluation["aggregated_prob"] if evaluation else None

        side = trade["side"]
        entry_price = trade["price"]
        shares = trade["shares"]
        cost_usd = trade["cost_usd"]

        # Current market-implied price for our side
        if side == "yes":
            current_price = market.get("yes_price", entry_price)
        else:
            yes_price = market.get("yes_price", 0.5)
            current_price = 1.0 - yes_price

        # ── Mark-to-market (unrealized) P&L ──────────────────────────────────
        current_value = shares * current_price
        unrealized_pnl = current_value - cost_usd
        unrealized_pct = unrealized_pnl / cost_usd if cost_usd > 0 else 0

        # ── Expected P&L (at our probability) ────────────────────────────────
        if our_prob is not None:
            resolution_prob = our_prob if side == "yes" else (1.0 - our_prob)
            expected_payout = shares * resolution_prob
            expected_pnl = expected_payout - cost_usd
            expected_return_pct = expected_pnl / cost_usd if cost_usd > 0 else 0
        else:
            expected_pnl = None
            expected_return_pct = None

        # ── IRR (annualized expected return) ─────────────────────────────────
        days = _days_to_resolution(market.get("resolution_date"))
        if days and expected_return_pct is not None and days > 0:
            years = days / 365.0
            irr = (1 + expected_return_pct) ** (1 / years) - 1
        else:
            irr = None

        # ── Sharpe contribution (edge / σ) ────────────────────────────────────
        if evaluation and our_prob is not None:
            edge = abs(evaluation.get("edge", 0))
            market_p = evaluation.get("market_implied_prob", current_price)
            variance = market_p * (1.0 - market_p)
            sharpe_contrib = edge / math.sqrt(variance) if variance > 0 else 0
        else:
            sharpe_contrib = None

        positions.append({
            "trade_id": trade["id"],
            "platform": trade["platform"],
            "title": market.get("title", ""),
            "side": side.upper(),
            "paper": bool(trade.get("paper", 1)),
            "entry_price": round(entry_price, 4),
            "current_price": round(current_price, 4),
            "our_probability": round(our_prob, 4) if our_prob else None,
            "shares": round(shares, 2),
            "cost_usd": round(cost_usd, 2),
            "current_value_usd": round(current_value, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "unrealized_pct": round(unrealized_pct * 100, 2),
            "expected_pnl": round(expected_pnl, 2) if expected_pnl is not None else None,
            "expected_return_pct": round(expected_return_pct * 100, 2) if expected_return_pct is not None else None,
            "days_to_resolution": round(days, 1) if days else None,
            "irr_annualized_pct": round(irr * 100, 1) if irr is not None else None,
            "sharpe_contribution": round(sharpe_contrib, 3) if sharpe_contrib is not None else None,
            "opened_at": trade.get("opened_at", ""),
            "resolution_date": market.get("resolution_date", ""),
        })

    # Sort by expected P&L, then unrealized P&L as fallback
    positions.sort(
        key=lambda x: (x["expected_pnl"] or 0, x["unrealized_pnl"]),
        reverse=True,
    )
    return positions[:top_n]


def print_position_report(top_n: int = 5) -> None:
    positions = build_position_report(top_n)

    if not positions:
        print("\nNo open positions.")
        return

    mode = "PAPER" if positions[0]["paper"] else "LIVE"
    print(f"\n{'='*80}")
    print(f"  OPEN POSITIONS — TOP {len(positions)} by Expected P&L  [{mode} MODE]")
    print(f"{'='*80}")

    for i, p in enumerate(positions, 1):
        title_display = p["title"][:65] + "…" if len(p["title"]) > 65 else p["title"]
        unreal_sign = "+" if p["unrealized_pnl"] >= 0 else ""
        exp_sign = "+" if (p["expected_pnl"] or 0) >= 0 else ""

        print(f"\n  #{i}  {title_display}")
        print(f"       Platform : {p['platform'].capitalize()}   Side: {p['side']}   "
              f"Opened: {p['opened_at'][:10]}")
        print(f"       Closes   : {(p['resolution_date'] or 'Unknown')[:10]}   "
              f"Days left: {p['days_to_resolution'] or '?'}")
        print(f"  {'─'*74}")
        print(f"       Entry price      : {p['entry_price']:.3f}   "
              f"Current price: {p['current_price']:.3f}   "
              f"Our estimate: {p['our_probability']:.3f}" if p['our_probability'] else
              f"       Entry price      : {p['entry_price']:.3f}   "
              f"Current price: {p['current_price']:.3f}")
        print(f"       Position size    : {p['shares']:.1f} shares @ ${p['cost_usd']:.2f} cost")
        print(f"       Current value    : ${p['current_value_usd']:.2f}   "
              f"Unrealized P&L: {unreal_sign}${p['unrealized_pnl']:.2f} "
              f"({unreal_sign}{p['unrealized_pct']:.1f}%)")

        if p["expected_pnl"] is not None:
            print(f"       Expected P&L     : {exp_sign}${p['expected_pnl']:.2f}   "
                  f"Expected return: {exp_sign}{p['expected_return_pct']:.1f}%")

        if p["irr_annualized_pct"] is not None:
            print(f"       IRR (annualized) : {p['irr_annualized_pct']:+.1f}%")

        if p["sharpe_contribution"] is not None:
            print(f"       Sharpe contrib   : {p['sharpe_contribution']:.3f}  "
                  f"(edge/σ per position)")

    # Portfolio summary
    total_cost = sum(p["cost_usd"] for p in positions)
    total_current = sum(p["current_value_usd"] for p in positions)
    total_unrealized = sum(p["unrealized_pnl"] for p in positions)
    total_expected = sum(p["expected_pnl"] or 0 for p in positions)

    print(f"\n{'─'*80}")
    print(f"  PORTFOLIO SUMMARY (top {len(positions)} positions)")
    print(f"  Total deployed   : ${total_cost:.2f}")
    print(f"  Current value    : ${total_current:.2f}   "
          f"Unrealized P&L: {'+' if total_unrealized >= 0 else ''}${total_unrealized:.2f}")
    print(f"  Expected P&L     : {'+' if total_expected >= 0 else ''}${total_expected:.2f}   "
          f"({'+' if total_expected/total_cost*100 >= 0 else ''}"
          f"{total_expected/total_cost*100:.1f}% on deployed capital)")
    print(f"{'='*80}\n")
