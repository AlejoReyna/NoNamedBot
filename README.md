<div align="center">

# NoNamedYet_Bot

**Autonomous BSC trading agent for the BNB Hack — AI Trading Agent Edition**

*Rule-based momentum breakout · TWAK self-custody execution · CMC market data · strict guardrails*

<br/>

| | |
|:---:|:---:|
| **Chain** | BNB Smart Chain (BSC) |
| **Agent schema** | `2.6.0` |
| **Default strategy** | `breakout` |
| **Loop interval** | `300s` (5 min) |
| **Tests** | `273+` pytest cases |

<br/>

</div>

[Quick Start](#quick-start) · [Run Modes](#run-modes) · [Strategy](#strategy-modes) · [Architecture](#architecture) · [Logs](#logs--telemetry)

---

## Overview

**NoNamedYet_Bot** is a production-minded Python trading agent that evaluates a focused universe of high-liquidity BNB Chain tokens, applies regime-aware guardrails, and executes swaps through **TWAK** — so Python never holds a trading private key.

| Design principle | What it means |
| --- | --- |
| **Deterministic strategy core** | Rule-based breakout scoring drives entries; runtime behavior does not depend on ML training |
| **No custom execution server** | All writes go through verified TWAK CLI subprocesses |
| **Fail-closed risk** | Slippage, drawdown, and daily limits block entries before capital moves |
| **Append-only audit trail** | Every cycle and swap is logged to JSONL for replay and demo proof |

> **Onboarding tip:** `context.md` is the engineer handoff — verified proofs, env quirks, and open blockers live there.

---

## What it does today

Each trading cycle (every `LOOP_SECONDS`, default **5 minutes**):

```
┌─────────────┐    ┌──────────────┐    ┌─────────────┐    ┌──────────────┐
│ Fetch CMC   │───▶│ Detect regime│───▶│ Score tokens│───▶│ Guardrails   │
│ snapshot    │    │ + sentiment  │    │ (strategy)  │    │ check        │
└─────────────┘    └──────────────┘    └─────────────┘    └──────┬───────┘
                                                                  │
                    ┌──────────────┐    ┌──────────────┐            ▼
                    │ Reconcile tx │◀───│ TWAK swap    │◀─── ENTER or WAIT
                    │ on-chain     │    │ (live only)  │
                    └──────────────┘    └──────────────┘
```

1. **Market data** — CoinMarketCap trial REST quotes (and optional x402-paid enrichment when a micropayment signer is configured).
2. **Regime & sentiment** — BNB trend, Fear & Greed, funding, and gas inform risk posture.
3. **Strategy evaluation** — `breakout` scores momentum, volume, market context, and quote safety before entry.
4. **Position management** — Monitor open trades every `POSITION_MONITOR_SECONDS`; exit on TP, SL, trailing stop, or time stop.
5. **Live execution** — TWAK quote-only slippage checks → swap → on-chain reconciliation before persisting local state.
6. **Telemetry** — Structured logs under `logs/` plus legacy `decision_log.jsonl` / `execution_log.jsonl`.

---

## Verified state

> Last audited: **2026-06-08**

| Capability | Status |
| --- | --- |
| TWAK CLI `0.17.0` swap + approval on BSC | ✅ Proven ([artifact](demo_artifacts/real_twak_swap_2026-06-04.md)) |
| Live balance reads via `bnb-chain-agentkit` | ✅ |
| Autonomous decision + execution JSONL logging | ✅ |
| On-chain execution reconciliation | ✅ |
| Dual CMC data path (REST + x402 enrichment) | ✅ Implemented |
| Autonomous loop producing a funded live swap | ⏳ Manual TWAK swap proven; loop micro-live pending |
| End-to-end paid CMC x402 in production | ⏳ Dual mode ready; full live proof pending |

**On-chain proof (manual TWAK run):**

| Action | Tx hash |
| --- | --- |
| Approval | `0x5863c33ba5fbfd7016fae9dfe062d853213b198376862fd76ce81336a20fe7d0` |
| Swap | `0x2b5db498c97d6c69af6718872feb749457e7e6434c17569a34a2f78ff64eda94` |

---

<a id="architecture"></a>

## Architecture

```mermaid
flowchart TB
    subgraph Data["src/data"]
        CMC[CMCMCPClient]
        X402[x402_client]
        Cache[market_snapshot_cache]
        Router[market_data_router]
    end

    subgraph Strategy["src/strategy"]
        Factory[factory]
        Breakout[6falgorithm / breakout]
        Regime[regime_detector]
        Guard[guardrails]
        Pos[positions]
    end

    subgraph Execution["src/execution"]
        TWAK[twak_interface]
        Swap[swap_router]
        Recon[execution_reconciler]
        Logs[decision_log + execution_log]
    end

    Main[src/main.py] --> Data
    Main --> Strategy
    Main --> Execution
    Factory --> Breakout
    CMC --> Cache
    X402 --> CMC
    TWAK --> Swap
    Swap --> Recon
    Main --> Logs
```

| Module | Responsibility |
| --- | --- |
| `src/main.py` | CLI, 5-minute agent loop, preflight, emergency liquidation |
| `src/data/` | CMC Keyless quotes, x402 micropayments, snapshot caching |
| `src/strategy/` | Breakout engine, regime, sentiment, guardrails |
| `src/execution/` | TWAK subprocess wrapper, swap routing, on-chain reconciliation |
| `src/config/` | Settings, token allowlists, env-only secrets |
| `scripts/` | Smoke tests, shadow replay, emergency shell helpers |

---

<a id="strategy-modes"></a>

## Strategy

Set `STRATEGY_MODE=breakout` in `.env`. The production bot uses the momentum breakout algorithm.

### Momentum breakout

The engine scans the tradable BNB Chain universe, scores candidates from **0–100**, quotes only candidates near the entry threshold, and enters only when quote safety and anti-chase rules pass.

| Signal | Intent |
| --- | --- |
| Volume breakout | 1h volume expands versus the rolling 24h hourly average |
| Price breakout | Price clears recent reference highs with a configurable buffer |
| Market context | BNB trend, sentiment, gas, and macro checks avoid risk-off entries |
| Quote safety | TWAK quote-only slippage must stay below the configured cap |
| Ranking context | RSI, derivatives, and momentum improve candidate ordering |

| Parameter | Default |
| --- | --- |
| Position size | up to 5% of portfolio |
| Trailing stop | 3.5% below peak |
| Take profit | +8% |
| Max daily trades | 3 |
| Max daily loss | 3% → 24h pause |
| Entry score | `BREAKOUT_ENTRY_SCORE_MIN=45` |
| Max slippage | 1% |

---

## Risk guardrails

> Guardrails are **never** bypassed for demo or competition windows.

| Control | Behavior |
| --- | --- |
| Tradable allowlist | `TRADABLE_TARGET_SYMBOLS` only |
| Drawdown soft stop | 10% |
| Drawdown kill switch | 15% → liquidate & halt |
| Max swap slippage | 1% |
| Daily trade limit | 3 entries |
| Daily loss cap | 3% → 24h pause |
| Emergency liquidation | `python -m src.main --emergency-liquidate` |

Stables (USDC / USDT) are settlement tokens — not directional entry targets.

---

<a id="quick-start"></a>

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with RPC URLs, wallet address, and TWAK unlock before live trading.

**Required for live mode:**

```env
BSC_PROVIDER_URL=...
AGENT_WALLET_ADDRESS=...
WALLET_ADDRESS=...
PAPER_TRADE=false
```

**TWAK wallet unlock** (pick one):

```bash
# Preferred — OS keychain
twak wallet keychain save --password '<wallet-password>'
twak wallet keychain check

# Or local-only (never commit)
TWAK_WALLET_PASSWORD=...
```

```bash
pytest
```

---

<a id="run-modes"></a>

## Run modes

| Command | Description |
| --- | --- |
| `python -m src.main --paper-trade` | Deterministic paper execution *(default when neither flag is set)* |
| `python -m src.main --live` | Live TWAK swaps on BSC |
| `python -m src.main --live --preflight` | Readiness checks — no broadcasts |
| `python -m src.main --live --once` | Single cycle then exit |
| `python -m src.main --live --demo-mode` | Compact per-cycle stdout summary |
| `python -m src.main --live --balance` | Print wallet balances and exit |
| `python -m src.main --emergency-liquidate` | Sell open positions → USDC |
| `python -m src.main --live --withdraw SYMBOL --to 0x… --amount N` | Transfer tokens out |

**Examples:**

```bash
# Paper loop (safe default)
python -m src.main --paper-trade

# Live with preflight first
python -m src.main --live --preflight
python -m src.main --live

# Dry-run emergency exit
python -m src.main --emergency-liquidate --paper-trade
```

---

## Market data

Three paths — configured via `.env`:

| Mode | Env | Behavior |
| --- | --- | --- |
| **Dual** *(default live)* | `USE_DUAL_MARKET_DATA=true` | Trial REST every loop; x402 enrichment on `CMC_SNAPSHOT_TTL_SECONDS` cadence |
| **Keyless only** | `USE_KEYLESS_PRIMARY=true` | Trial REST snapshots only |
| **MCP shadow** | `CMC_MCP_ENABLED=true` + `CMC_MCP_SHADOW_MODE=true` | Exercise x402 MCP without affecting trading data |

x402 micropayments use `CMC_X402_EPHEMERAL_KEY` (isolated in `src/data/` — **not** the TWAK trading wallet).

**Verified TWAK commands:**

```bash
twak wallet create
twak compete register
twak wallet address --chain bsc --json
twak swap <amount> <from> <to> --slippage <pct> --chain bsc --quote-only --json
twak swap <amount> <from> <to> --slippage <pct> --chain bsc --json
twak x402 pay --url <endpoint> --amount <amount> --asset <token> --chain base --json
```

> Internal slippage is stored as a fraction (`0.01` = 1%). TWAK CLI expects percent (`--slippage 1`).

**Smoke scripts:**

```bash
python scripts/smoke_cmc_mcp.py
python scripts/smoke_cmc_x402_paid_quote.py
python scripts/replay_shadow.py
```

---

<a id="logs--telemetry"></a>

## Logs & telemetry

### Structured (`logs/`)

| File | Contents |
| --- | --- |
| `decision_live.jsonl` | Per-cycle ENTER / WAIT / BLOCKED / HALT with factor scores |
| `portfolio_snapshots.jsonl` | Portfolio value, drawdown, open positions |
| `risk_events.jsonl` | Kill switch, pause, and limit breaches |
| `sentiment_live.jsonl` | FGI, funding, gas snapshots |
| `decision_shadow.jsonl` | Parallel strategy variants for research |

### Legacy (root)

| File | Contents |
| --- | --- |
| `decision_log.jsonl` | Compact strategy decisions |
| `execution_log.jsonl` | Swap attempts, tx hashes, errors |

Override paths with `DECISION_LOG_PATH`, `EXECUTION_LOG_PATH`, `POSITION_STATE_PATH`, and `GUARDRAIL_STATE_PATH`.

---

## Key environment variables

<details>
<summary><strong>Click to expand full env reference</strong></summary>

```env
# Strategy
STRATEGY_MODE=breakout
LOOP_SECONDS=300
POSITION_MONITOR_SECONDS=60

# Market data
USE_DUAL_MARKET_DATA=true
CMC_SNAPSHOT_TTL_SECONDS=14400
CMC_KEYLESS_SNAPSHOT_TTL_SECONDS=300
USE_KEYLESS_PRIMARY=false
CMC_MCP_ENABLED=false
CMC_MCP_SHADOW_MODE=false

# x402 (Base micropayments for CMC enrichment)
CMC_X402_EPHEMERAL_KEY=
CMC_X402_AMOUNT=0.01
CMC_X402_ASSET=0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
CMC_X402_CHAIN_ID=8453

# Risk
MAX_POSITION_PCT=0.05
MAX_DAILY_TRADES=3
MAX_DAILY_LOSS_PCT=0.03
DRAWDOWN_KILL_SWITCH_PCT=0.15
BREAKOUT_ENTRY_SCORE_MIN=45
```

See `src/config/settings.py` and `.env.example` for the complete list.

</details>

---

## Known gaps

- [ ] Autonomous loop has not yet persisted a **funded micro-live** swap end-to-end (manual TWAK swap is proven).
- [ ] Paid CMC x402 enrichment is wired but awaits full production proof.
- [ ] Unattended production should harden state recovery beyond JSON file persistence.

---

## Project layout

```
NoNamedYet_Bot/
├── src/
│   ├── main.py              # Agent loop & CLI
│   ├── config/              # Settings, tokens, secrets
│   ├── data/                # CMC + x402 clients
│   ├── execution/           # TWAK, swaps, reconciliation
│   └── strategy/            # Breakout, regime, guardrails
├── tests/                   # Pytest suite
├── scripts/                 # Smoke & ops helpers
├── logs/                    # Structured telemetry (gitignored contents)
├── demo_artifacts/          # On-chain proof writeups
├── .env.example
└── context.md               # Technical handoff for AI/engineering sessions
```

---

<div align="center">

**Built for BNB Hack Track 1** · TWAK self-custody · CoinMarketCap data · No private keys in Python. Some cool guy I worked with told me updating the README sometimes fixed CI/CD pipelines 

</div>
