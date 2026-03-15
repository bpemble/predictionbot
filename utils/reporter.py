"""
Position reporter: open position analytics + closed P&L dashboard.

--report  shows open positions with expected P&L, IRR, Sharpe
--pnl     shows closed trade history, win rate, Brier score, realized P&L
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


def build_position_report(top_n: Optional[int] = None) -> list[dict]:
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
    return positions if top_n is None else positions[:top_n]


def print_position_report(top_n: Optional[int] = None) -> None:
    positions = build_position_report(top_n)

    if not positions:
        print("\nNo open positions.")
        return

    mode = "PAPER" if positions[0]["paper"] else "LIVE"
    print(f"\n{'='*80}")
    print(f"  OPEN POSITIONS — {len(positions)} total, sorted by Expected P&L  [{mode} MODE]")
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
    print(f"  PORTFOLIO SUMMARY ({len(positions)} positions)")
    print(f"  Total deployed   : ${total_cost:.2f}")
    print(f"  Current value    : ${total_current:.2f}   "
          f"Unrealized P&L: {'+' if total_unrealized >= 0 else ''}${total_unrealized:.2f}")
    print(f"  Expected P&L     : {'+' if total_expected >= 0 else ''}${total_expected:.2f}   "
          f"({'+' if total_expected/total_cost*100 >= 0 else ''}"
          f"{total_expected/total_cost*100:.1f}% on deployed capital)")
    print(f"{'='*80}\n")


# ── P&L dashboard ─────────────────────────────────────────────────────────────

def _get_closed_trades() -> list[dict]:
    """Fetch all closed/exited trades with their market titles."""
    rows = _get_conn().execute(
        """
        SELECT t.*, m.title, m.outcome, m.category
        FROM trades t
        LEFT JOIN markets m ON t.market_id = m.id
        WHERE t.status IN ('closed', 'exited')
        ORDER BY t.closed_at DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def _get_signal_weights() -> list[dict]:
    rows = _get_conn().execute(
        "SELECT * FROM signal_weights ORDER BY weight DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def print_pnl_report() -> None:
    """
    Full P&L dashboard: closed trade history, win rate, Brier score,
    realized P&L, and signal weight calibration status.
    """
    closed = _get_closed_trades()
    open_trades = get_open_trades()

    mode = "PAPER" if _is_paper_mode() else "LIVE"
    print(f"\n{'='*80}")
    print(f"  P&L DASHBOARD  [{mode} MODE]")
    print(f"{'='*80}")

    # ── Open positions summary ────────────────────────────────────────────────
    open_exposure = sum(t["cost_usd"] for t in open_trades)
    print(f"\n  OPEN POSITIONS: {len(open_trades)}  (${open_exposure:.2f} deployed)")
    print(f"  Run  --report  for full open position analytics.\n")

    # ── Closed trades ─────────────────────────────────────────────────────────
    if not closed:
        print("  No closed trades yet.")
        print(f"\n{'='*80}\n")
        return

    resolved = [t for t in closed if t["status"] == "closed"]   # market resolved
    exited   = [t for t in closed if t["status"] == "exited"]   # early exit

    print(f"  CLOSED TRADES: {len(closed)} total  "
          f"({len(resolved)} resolved · {len(exited)} early exits)\n")

    print(f"  {'#':>3}  {'Market':<42} {'Side':4} {'Entry':6} "
          f"{'Exit':6} {'Cost':7} {'P&L':8} {'Ret%':7} {'Type'}")
    print(f"  {'─'*3}  {'─'*42} {'─'*4} {'─'*6} {'─'*6} {'─'*7} {'─'*8} {'─'*7} {'─'*10}")

    for i, t in enumerate(closed[:30], 1):   # cap display at 30 rows
        title   = (t["title"] or "Unknown")[:41]
        side    = (t["side"] or "?").upper()
        cost    = t["cost_usd"] or 0
        pnl     = t["pnl_usd"] or 0
        ret_pct = (pnl / cost * 100) if cost > 0 else 0
        pnl_str = f"{'+' if pnl >= 0 else ''}${pnl:.2f}"
        ret_str = f"{'+' if ret_pct >= 0 else ''}{ret_pct:.1f}%"

        # Entry price and exit price
        entry = t["price"] or 0
        # Exit price: back-calculate from pnl and shares
        shares = t["shares"] or 0
        if shares > 0 and cost > 0:
            exit_price = (cost + pnl) / shares
        else:
            exit_price = 0

        trade_type = "resolved" if t["status"] == "closed" else "early-exit"
        outcome_marker = ""
        if t["status"] == "closed" and t["outcome"]:
            won = (t["side"] == t["outcome"])
            outcome_marker = " ✓" if won else " ✗"

        print(f"  {i:>3}  {title:<42} {side:4} {entry:6.3f} "
              f"{exit_price:6.3f} ${cost:6.2f} {pnl_str:>8} {ret_str:>7} "
              f"{trade_type}{outcome_marker}")

    if len(closed) > 30:
        print(f"  ... and {len(closed) - 30} more trades")

    # ── Aggregate stats ───────────────────────────────────────────────────────
    total_cost    = sum(t["cost_usd"] or 0 for t in closed)
    total_pnl     = sum(t["pnl_usd"] or 0 for t in closed)
    total_ret_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0

    winners = [t for t in closed if (t["pnl_usd"] or 0) > 0]
    losers  = [t for t in closed if (t["pnl_usd"] or 0) < 0]
    win_rate = len(winners) / len(closed) * 100 if closed else 0

    avg_win  = sum(t["pnl_usd"] for t in winners) / len(winners) if winners else 0
    avg_loss = sum(t["pnl_usd"] for t in losers)  / len(losers)  if losers  else 0
    profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")

    brier_trades = [t for t in resolved if t["brier_contribution"] is not None]
    avg_brier = (sum(t["brier_contribution"] for t in brier_trades) / len(brier_trades)
                 if brier_trades else None)

    print(f"\n  {'─'*78}")
    print(f"  REALIZED P&L SUMMARY")
    print(f"  Total wagered    : ${total_cost:.2f}")
    print(f"  Total realized   : {'+' if total_pnl >= 0 else ''}${total_pnl:.2f}  "
          f"({'+' if total_ret_pct >= 0 else ''}{total_ret_pct:.1f}% on deployed capital)")
    print(f"  Win rate         : {win_rate:.1f}%  "
          f"({len(winners)} wins · {len(losers)} losses · "
          f"{len(closed) - len(winners) - len(losers)} scratch)")
    print(f"  Avg win / loss   : +${avg_win:.2f} / -${abs(avg_loss):.2f}  "
          f"(profit factor: {profit_factor:.2f}x)")

    if avg_brier is not None:
        # Brier score: 0 = perfect, 0.25 = random (50/50), lower = better
        calibration = "excellent" if avg_brier < 0.10 else \
                      "good"      if avg_brier < 0.18 else \
                      "fair"      if avg_brier < 0.22 else "poor"
        print(f"  Avg Brier score  : {avg_brier:.4f}  [{calibration}]  "
              f"(0=perfect · 0.25=random · {len(brier_trades)} resolved trades)")
    else:
        print(f"  Avg Brier score  : n/a  (need resolved trades for calibration)")

    # ── Signal weight status ──────────────────────────────────────────────────
    weights = _get_signal_weights()
    if weights:
        print(f"\n  SIGNAL WEIGHTS  (self-calibrating via Brier score)")
        for w in weights:
            bar_len = int(w["weight"] * 100)
            bar = "█" * bar_len + "░" * (30 - bar_len)
            brier_str = f"brier={w['avg_brier_score']:.4f}" if w.get("avg_brier_score") else "not yet calibrated"
            n = w.get("n_resolved", 0)
            print(f"  {w['signal_source']:<12} {w['weight']:.3f}  {bar}  {brier_str}  n={n}")

    print(f"\n{'='*80}\n")


def _is_paper_mode() -> bool:
    try:
        from config.settings import get_settings
        return get_settings().paper_trade
    except Exception:
        return True
