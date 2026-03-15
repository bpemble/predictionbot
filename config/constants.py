"""
Fixed numeric constants.  Do not change these without understanding the
statistical implications.  All dollar amounts are in USD.
"""

# ─── Kelly / Position sizing ─────────────────────────────────────────────────
KELLY_FRACTION: float = 0.25        # fractional Kelly multiplier
MAX_PER_TRADE_PCT: float = 0.05     # max 5 % of bankroll per single trade
MAX_TOTAL_EXPOSURE: float = 0.60    # max 60 % of bankroll in open positions at once

# ─── Edge / Signal thresholds ────────────────────────────────────────────────
MIN_EDGE: float = 0.05              # minimum |our_prob - market_price| to trade
MIN_SIGNALS_REQUIRED: int = 2       # need at least N signal sources to place a trade
MIN_SIGNAL_CONFIDENCE: float = 0.20 # discard signals below this self-reported confidence

# ─── Market filtering ─────────────────────────────────────────────────────────
MIN_LIQUIDITY_USD: float = 5_000    # skip markets with less available liquidity
MIN_VOLUME_USD: float = 5_000       # skip markets with low total volume
MIN_HOURS_TO_CLOSE: float = 12      # skip markets closing in < 12 hours (need time for edge to play)
MAX_DAYS_TO_CLOSE: float = 45       # skip markets closing > 45 days out
PRICE_FLOOR: float = 0.05           # skip if yes_price < 5 %
PRICE_CEIL: float = 0.95            # skip if yes_price > 95 %
MAX_MARKETS_PER_SCAN: int = 15      # fewer markets, much deeper analysis

# Info-asymmetry market efficiency filter:
# Avoid the most liquid markets — they are priced by sophisticated players.
# The sweet spot is mid-tier volume where crowd wisdom is incomplete.
MAX_VOLUME_USD_EFFICIENCY: float = 800_000  # skip hyper-liquid markets above this
# Prefer markets where price is NOT near extremes (those are usually "known" outcomes)
ALPHA_PRICE_FLOOR: float = 0.10     # extra scoring preference for markets above this
ALPHA_PRICE_CEIL: float = 0.90      # extra scoring preference for markets below this

# ─── Signal aggregation ──────────────────────────────────────────────────────
# Bayesian shrinkage toward market price.
# confidence_weight_split: how much self-reported confidence modulates base weight.
CONFIDENCE_WEIGHT_ALPHA: float = 0.30   # 0.7 base + 0.3 * confidence
DEFAULT_SIGNAL_CONFIDENCE: float = 0.50

# ─── Self-learning ────────────────────────────────────────────────────────────
EMA_ALPHA: float = 0.20             # EMA speed for signal weight updates
BRIER_WINDOW: int = 50              # rolling window of resolved trades for Brier

# ─── Scheduling (seconds) ─────────────────────────────────────────────────────
SCAN_INTERVAL_SECONDS: int = 3600       # 1 hour — info asymmetry, not latency
REEVAL_INTERVAL_SECONDS: int = 3600     # 1 hour
OUTCOME_CHECK_INTERVAL_SECONDS: int = 14_400  # 4 hours
CALIBRATE_HOUR_UTC: int = 2             # 02:00 UTC daily
SNAPSHOT_HOUR_UTC: int = 0              # 00:05 UTC daily
HEALTH_CHECK_INTERVAL_SECONDS: int = 300  # 5 min

# ─── HTTP ────────────────────────────────────────────────────────────────────
HTTP_TIMEOUT: int = 30
HTTP_MAX_RETRIES: int = 3
HTTP_BACKOFF_BASE: float = 1.0
HTTP_BACKOFF_MAX: float = 30.0

# ─── Misc ────────────────────────────────────────────────────────────────────
POLY_CHAIN_ID_MAINNET: int = 137
POLY_CHAIN_ID_TESTNET: int = 80001
