"""Verified x402 TTL/refresh optimization framework.

Implements the Lagrangian-derived bang-bang solution for optimal CMC x402
call scheduling.  All parameter values were verified by a 6-agent swarm
against empirical research (CoinQuant backtests, CCi30 concentration data,
Citi/Schroders institutional benchmarks).

Key results:
  - β = 0.12 (NOT 0.7) → n* = max(1, β/(1-β)) = 1 theoretically; use 3-5
    in practice for diversification
  - COST_PER_CALL = $0.015 all-in (API + gas + facilitator)
  - T2_MIN_PRACTICAL = 300s (x402 has 2-5s latency per request)
  - AUM_MIN_VIABLE ≈ $5K-$10K for live profitability

The $15 competition AUM configuration is for scoring, not live trading.
"""

import math

# ---------------------------------------------------------------------------
# Verified baseline parameters (Agents 1-3, 6-agent swarm)
# ---------------------------------------------------------------------------

ALPHA: list[float] = [0.22, 0.50, 4.50]
"""Expected alpha per cycle at $2K position size: [flat, trending, breakout]."""

ALPHA_SCALING_FACTOR: float = 2000.0
"""AUM at which ALPHA was calibrated (USD)."""

BETA: float = 0.12
"""Empirical coverage exponent from market-cap concentration analysis."""

BETA_CI: tuple[float, float] = (0.10, 0.15)
"""90% confidence interval for BETA."""

LAMBDA: list[float] = [0.42, 0.35, 0.35]
"""Per-hour signal decay rates (BSC-adjusted, 1.5× large-cap estimate)."""

T_REF: int = 3600
"""Reference timeframe for decay calculation (1 hour, in seconds)."""

P_STATES: list[float] = [0.85, 0.10, 0.05]
"""Stationary distribution over market states (fraction of time)."""

T_MIN: int = 60
"""Minimum TTL in seconds."""

T_MAX: int = 86400
"""Maximum TTL (1 day) — noise floor."""

T2_MIN_PRACTICAL: int = 300
"""Minimum viable hot-candidate refresh interval without prepaid bundles."""

N_MAX: int = 50
"""Universe size (CMC top-N candidates)."""

COST_PER_CALL: float = 0.015
"""All-in cost per x402 call: API + gas + CDP facilitator (USD)."""

COST_PER_CALL_BUNDLED: float = 0.008
"""All-in cost with prepaid bundles (100-1000 calls/tx)."""

T0_MIN_EMPIRICAL: int = 3600
"""Minimum scan interval from CoinQuant backtests (1H Sharpe +0.38; 15M Sharpe -1.00)."""

TRADE_COST_PER_ROUND_TRIP: float = 0.005
"""PancakeSwap 0.25% pool fee + BNB gas per round-trip (≈0.5% of position)."""

GAMMA: dict[str, float] = {
    "competition": 10.0,
    "micro_retail": 5.0,
    "small_retail": 1.0,
    "medium_retail": 0.5,
    "large_retail": 0.2,
    "institutional": 0.01,
}
"""Risk-aversion coefficient by AUM tier."""

AUM_MIN_VIABLE: float = 5_000.0
"""Minimum AUM for live profitability with practical config (USD)."""

COMPETITION_CONFIG: dict = {
    "AUM": 20.0,
    "data_budget_total": 5.0,       # 25% of AUM
    "data_budget_daily": 5.0 / 7,   # ≈ $0.714/day
    "T0": 7200,                      # 2h scanning — slower to conserve budget
    "T1": 7200,                      # 2h monitoring
    "T2": 360,                       # 6 min hot-candidate refresh
    "n": 1,                          # only affordable option at $0.714/day
    "cost_per_call": COST_PER_CALL,
    "status": "COMPETITION_ONLY",    # unprofitable at $20 AUM — scoring use only
}
"""
Competition parameters (25% data allocation on $20 AUM).

B* ≈ $0.714/day constrains n=1, T2=360s. Structurally unprofitable vs
expected daily alpha ~$0.49 at $20 AUM position size. Live deployment
requires AUM ≥ $5K–$10K.
"""


# ---------------------------------------------------------------------------
# Optimization functions
# ---------------------------------------------------------------------------


def compute_optimal_n(beta: float = BETA, n_min: int = 1, n_max: int = N_MAX) -> int:
    """Optimal symbol count with box constraints applied.

    The theoretical formula n* = β/(1-β) is only valid when 0.5 ≤ β < N/(N+1).
    For the empirical β ≈ 0.12, the n ≥ 1 constraint is binding, so n* = 1.
    Use n = 3-5 in practice for diversification.
    """
    n_star = beta / (1.0 - beta)
    return int(min(n_max, max(n_min, math.ceil(n_star))))


def objective_ratio(
    T: list[float],
    n: int,
    alpha: list[float] = ALPHA,
    lam: list[float] = LAMBDA,
    p: list[float] = P_STATES,
    beta: float = BETA,
    N: int = N_MAX,
    cost_per_call: float = COST_PER_CALL,
) -> float:
    """Expected alpha per dollar spent (ratio objective).

    Clean vector form — equivalent to the verified matrix formulation but
    without the 3×3 diagonal overhead.
    """
    if any(t <= 0 for t in T):
        raise ValueError("All TTL values must be positive")
    f = [86400.0 / t for t in T]
    q = [math.exp(-lam[i] * T[i] / T_REF) for i in range(3)]
    w = (n / N) ** beta
    numerator = w * sum(p[i] * f[i] * alpha[i] * q[i] for i in range(3))
    denominator = cost_per_call * (1 + n) * sum(p[i] * f[i] for i in range(3))
    if denominator == 0.0:
        return 0.0
    return numerator / denominator


def objective_profit(
    T: list[float],
    n: int,
    gamma: float,
    alpha: list[float] = ALPHA,
    lam: list[float] = LAMBDA,
    p: list[float] = P_STATES,
    beta: float = BETA,
    N: int = N_MAX,
    cost_per_call: float = COST_PER_CALL,
) -> float:
    """Daily alpha minus cost (profit objective)."""
    if any(t <= 0 for t in T):
        raise ValueError("All TTL values must be positive")
    f = [86400.0 / t for t in T]
    q = [math.exp(-lam[i] * T[i] / T_REF) for i in range(3)]
    w = (n / N) ** beta
    alpha_daily = w * sum(p[i] * f[i] * alpha[i] * q[i] for i in range(3))
    cost_daily = gamma * cost_per_call * (1 + n) * sum(p[i] * f[i] for i in range(3))
    return alpha_daily - cost_daily


def compute_budget(
    T: list[float],
    n: int,
    p: list[float] = P_STATES,
    cost_per_call: float = COST_PER_CALL,
) -> float:
    """Daily x402 budget required for the given TTL/symbol configuration (USD)."""
    freq_weighted = sum(p[i] * 86400.0 / T[i] for i in range(3))
    return cost_per_call * (1 + n) * freq_weighted


def scale_alpha(
    alpha_baseline: list[float] = ALPHA,
    target_aum: float = 2000.0,
    baseline_position: float = ALPHA_SCALING_FACTOR,
) -> list[float]:
    """Scale alpha values to target AUM.

    Alpha scales linearly with position size for a fixed risk fraction.
    """
    scale = target_aum / baseline_position
    return [a * scale for a in alpha_baseline]


def subscription_breakpoint(
    calls_per_day: int,
    subscription_cost_monthly: float = 35.0,
    cost_per_call: float = COST_PER_CALL,
) -> float:
    """AUM at which a monthly subscription becomes cheaper than pay-per-call.

    Returns inf when x402 is always cheaper at the given call volume.
    """
    x402_monthly = calls_per_day * cost_per_call * 30
    if x402_monthly <= subscription_cost_monthly:
        return float("inf")
    # Breakeven AUM: assuming ~0.1% daily return on AUM
    return subscription_cost_monthly / (0.001 * 30)
