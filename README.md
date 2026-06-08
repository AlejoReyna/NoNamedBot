# Plan B+ — BSC Momentum Breakout Scalper

Plan B+ is a runnable Python skeleton for the BNB Hack: AI Trading Agent Edition, Track 1. It is an autonomous, rule-based BSC momentum breakout scalper for high-volume BNB Chain tokens with strict guardrails, local TWAK signing, and x402 access to CoinMarketCap premium data.

It intentionally does not include ML, external inference servers, invented trading APIs, cloud workers, VPS setup, or fake PancakeSwap SDKs.

Read `context.md` first if you are onboarding. It is the detailed technical
handoff with current proof, known gaps, and next engineering tasks.

## Current Verified State

As of 2026-06-04:

- TWAK CLI version verified locally: `0.17.0`.
- BSC agent wallet verified by TWAK:
  `0x7CE28f5d2D1B2eFd8f87FF0a7fdC7D2EaB465c9c`.
- `.env` points both `AGENT_WALLET_ADDRESS` and `WALLET_ADDRESS` at that same
  wallet.
- `python -m src.main --live --balance` reads real BSC balances through Web3.
- A real BSC approval and swap have been executed through TWAK/LiquidMesh.
- TWAK swap parsing handles pure JSON and mixed stdout with approval/swap tx
  lines before the final JSON.
- Autonomous entry, exit, and emergency liquidation paths persist execution
  records to `execution_log.jsonl`.
- Autonomous strategy decisions persist to `decision_log.jsonl`, including
  WAIT, BLOCKED, ENTER, and HALT cycle decisions.
- CMC Keyless trial API is the primary market-data path by default
  (`USE_KEYLESS_PRIMARY=true`).
- CMC Agent Hub MCP/x402 is also available as an isolated optional adapter in
  `src/data/cmc_mcp_client.py`, `src/data/x402_client.py`, and
  `src/data/market_data_router.py`. It delegates paid requests to
  `twak x402 request`, so the user's TWAK wallet signs locally. Trading
  execution also uses TWAK self-custody signing exclusively.
- Current tests: `117 passed, 1 warning`.

Real execution proof:

```text
demo_artifacts/real_twak_swap_2026-06-04.md
```

Approval tx:

```text
0x5863c33ba5fbfd7016fae9dfe062d853213b198376862fd76ce81336a20fe7d0
```

Swap tx:

```text
0x2b5db498c97d6c69af6718872feb749457e7e6434c17569a34a2f78ff64eda94
```

## Architecture

- `src/data`: CoinMarketCap Keyless market data, MCP JSON-RPC envelopes, and
  x402 payment retry handling.
- `src/execution`: TWAK subprocess commands, bnb-chain-agentkit balance/transfer wrapper, PancakeSwap V3 conceptual routing, and append-only audit logs.
- `src/strategy`: four-core-factor breakout gating, two optional score factors, JSON-backed position tracking, and executable guardrails.
- `src/config`: settings, token allowlists, and environment-only secret access.
- `src/main.py`: CLI entrypoint and 5-minute trading loop.

## Verified Tools

CMC MCP tool names used exactly:

- `get_crypto_quotes_latest`
- `get_crypto_technical_analysis`
- `get_global_crypto_derivatives_metrics`
- `get_crypto_market_metrics`

Official x402 endpoint:

```text
https://mcp.coinmarketcap.com/x402/mcp
```

Verified execution imports:

```python
from bnb_chain_agentkit.agent_toolkits import BnbChainToolkit
from bnb_chain_agentkit.utils import BnbChainAPIWrapper
```

Verified TWAK commands:

```bash
twak wallet create
twak compete register
twak wallet sign-message --chain base --message <text> --json
twak x402 request <endpoint> --method POST --body <json> --max-payment <atomic> --prefer-network base --prefer-method eip3009 --yes --json
twak swap <amount> <from-token> <to-token> --slippage <pct> --chain bsc --json
twak start
```

Use `twak x402 request` for native x402. TWAK owns the paid HTTP request and
signs the EIP-3009 authorization locally from the user's TWAK wallet. Because
TWAK 0.17.0 does not expose custom request-header flags for x402, the agent
keeps CMC Keyless as a fail-open market-data fallback if CMC rejects the generic
TWAK request shape.

TWAK 0.17.0 does not accept the old `twak swap --from ... --to ... --amount ...`
form. `twak swap --help` shows positional arguments:

```bash
twak swap [options] <amountOrFrom> <fromOrTo> [to]
```

For BSC swaps use:

```bash
twak swap 0.5 USDC BNB --slippage 1 --chain bsc --quote-only --json
twak swap 0.5 USDC BNB --slippage 1 --chain bsc --json
```

`--slippage` is a percent value at the TWAK CLI. The codebase stores slippage as
a fraction (`0.01` = 1%) and `TWAKInterface._fraction_to_cli_percent()` converts
it to TWAK CLI percent format (`--slippage 1`).

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Configure `.env` with real CMC access, RPC URLs, wallet address, token addresses, and TWAK setup before live trading.
The current `bnb-chain-agentkit` package requires Python 3.12+ and
`BSC_PROVIDER_URL`/`BSC_RPC_URL` for live balance and transfer operations.

Required live environment variables:

```text
BSC_PROVIDER_URL=...
OPBNB_PROVIDER_URL=...
AGENT_WALLET_ADDRESS=...
WALLET_ADDRESS=...
PAPER_TRADE=false
```

Optional/credential variables:

```text
CMC_API_KEY=...
USE_KEYLESS_PRIMARY=true
CMC_MCP_ENABLED=false
CMC_MCP_SHADOW_MODE=true
CMC_MCP_URL=https://mcp.coinmarketcap.com/x402/mcp
CMC_X402_AMOUNT=0.01
CMC_X402_ASSET=0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
TWAK_WALLET_PASSWORD=...
ACCESS_ID=...
ACCESS_SECRET=...
```

Do not commit `.env`. It is ignored by `.gitignore`.

## Optional CMC MCP/x402 Adapter

The CMC Agent Hub MCP/x402 path is a client-side adapter for
`https://mcp.coinmarketcap.com/x402/mcp`. It posts MCP `initialize`,
`tools/list`, and `tools/call` requests over Streamable HTTP, saves unknown 402
responses to `artifacts/x402_402_response.json`, and routes paid request
attempts through TWAK-native x402.

x402 payments use `twak x402 request` for CMC data access. TWAK local signing
controls x402 authorizations, trading execution, swaps, approvals, transfers,
and liquidation. No x402 private key is needed in `.env` for the submitted
path.

Default trading behavior does not depend on it:

```text
CMC_MCP_ENABLED=false
CMC_MCP_SHADOW_MODE=true
USE_KEYLESS_PRIMARY=true
```

Run local checks:

```bash
pip install -r requirements.txt
pytest
python scripts/smoke_cmc_mcp.py
python scripts/smoke_cmc_x402_paid_quote.py
```

TODO: confirm the exact live CMC MCP behavior through `twak x402 request`.
Current code keeps Keyless fallback because TWAK owns x402 HTTP headers.

## TWAK Setup

```bash
twak wallet create
twak compete register
```

For unattended execution, TWAK must be able to unlock the agent wallet without
an interactive prompt. Prefer storing the password in the OS keychain:

```bash
twak wallet keychain save --password '<wallet-password>'
twak wallet keychain check
```

For a hackathon runner, `TWAK_WALLET_PASSWORD` can also be set in the local
`.env` file. Keep `.env` ignored and never commit it. The project uses internal
fractional slippage values (`0.01` = 1%) and converts them to TWAK's percent CLI
format (`--slippage 1`).

Manual shell commands do not automatically load `.env`. Either export
`TWAK_WALLET_PASSWORD` in the shell or use the keychain. Python commands call
`load_dotenv()`, so TWAK subprocesses spawned by Python inherit `.env` values.

Password/unlock smoke test:

```bash
twak wallet address --chain bsc --json
```

The result must match `AGENT_WALLET_ADDRESS`.

## Run

Paper mode:

```bash
python -m src.main --paper-trade
```

Live mode:

```bash
python -m src.main --live
```

Emergency liquidation:

```bash
python -m src.main --emergency-liquidate
```

Emergency liquidation defaults to live execution and loads `POSITION_STATE_PATH`
before selling open positions back to USDC. Use `--paper-trade` with the
emergency command only for a dry run.

Tests:

```bash
pytest
```

If your shell does not have the virtualenv activated, use:

```bash
./.venv/bin/python -m pytest -q
```

## Logs

The agent writes two append-only JSONL audit files by default:

- `decision_log.jsonl` records one strategy decision per cycle: ENTER, WAIT,
  BLOCKED, or HALT, with the selected symbol, factor scores, slippage estimate,
  portfolio value, position count, and reason.
- `execution_log.jsonl` records swap execution attempts and results: entries,
  exits, emergency liquidations, tx hashes, approvals, router output, and errors.

Use `DECISION_LOG_PATH` and `EXECUTION_LOG_PATH` to move these files without
changing code.

## Strategy

The agent evaluates the focused 20-token target universe every 5 minutes. USDT and USDC remain configured for routing and balance checks, but are excluded from directional entries. The engine scores six factors, but entries are gated by the four core factors below. The default `MIN_ENTRY_FACTORS=4` requires all four core factors to pass, and slippage is always mandatory and fail-closed.

Core entry factors:

- 1h volume is greater than 2x rolling 24h hourly average when CMC hourly fields are available, with local 24h cache fallback.
- Price breaks the CMC 6h high when available, with local six-hour price cache fallback.
- BNB 1h trend is not sharply risk-off.
- Estimated slippage is non-negative and under 1% through TWAK quote-only.

Optional score/ranking factors:

- RSI is between 55 and 75, inclusive.
- Funding and open interest are not flashing broad liquidation risk.

`MIN_ENTRY_FACTORS=3` can be used to permit one missing core factor, but slippage still must pass and all executable guardrails remain enforced.

After entry, the position manager persists the open position, sets a trailing stop 3.5% below entry, and sets a fixed take-profit at +8%. In live mode, the agent requires a swap tx hash before opening or closing local position state; paper mode uses the fake paper tx hash.

## Risk Guardrails

- Strict trading allowlist: only `TRADABLE_TARGET_SYMBOLS` for directional entries; stables are base/settlement tokens only.
- Max position size: 5% of portfolio per trade.
- Max daily trades: 3.
- Max daily realized loss: 3% of portfolio, then pause entries for 24 hours.
- Max swap slippage: 1%.
- Drawdown kill switch: 20% from all-time high triggers liquidation to USDC and terminates the loop.
- Manual emergency command loads persisted positions or reconstructs target-token wallet balances, then sells non-USDC positions to USDC without undocumented TWAK commands.

The June 22-28 live-window trade target is implemented only as a log warning. Guardrails are never overridden to force a trade.

## Live Trading Notes

Real trading requires funded wallets, correct BSC and opBNB RPC configuration, token addresses, TWAK setup, CMC/x402 access, and installed dependencies. This skeleton persists positions to `positions.json`, guardrails to `guardrail_state.json`, strategy decisions to `decision_log.jsonl`, and execution logs to `execution_log.jsonl` by default; production deployments should still harden state recovery and reconciliation before running unattended capital.

## Known Gaps

- Real TWAK signing/broadcast has been proven manually, but the autonomous
  strategy loop has not yet produced and persisted a real trade.
- Real CMC/x402 paid data has not yet been proven end-to-end against the live
  paid endpoint. Current live-safe data path is CMC Keyless trial; x402 remains
  implemented for native-payment demo and TWAK-special-prize proof.
- Router output-floor validation happens after TWAK returns. Real pre-broadcast
  slippage protection depends on TWAK/LiquidMesh honoring `--slippage`.
