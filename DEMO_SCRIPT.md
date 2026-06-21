# Demo Video Script — NoNamedYet_Bot (3 minutes)

## 0:00–0:30 — Architecture overview

- Show repo structure: `src/main.py`, `src/execution/twak_interface.py`, `src/data/`
- Emphasize: **no private key in Python** — all signing via TWAK CLI subprocess
- CMC data: Keyless primary + x402 premium enrichment (`src/data/cmc_mcp_client.py`)
- Strategy: rule-based six-factor breakout engine with regime-aware guardrails

## 0:30–1:00 — TWAK self-custody + live preflight

- Run `python -m src.main --live --preflight`
- Show wallet unlock, balance read, TWAK quote-only, and CMC snapshot all passing
- Open `demo_artifacts/ON_CHAIN_PROOF.md` and point to real BSC swap hashes

## 1:00–1:30 — Live loop telemetry

- `tail logs/decision_live.jsonl` — show live `mode: "live"` decisions
- `curl localhost:8080/health` — status, positions, drawdown
- Filter out any `mode: "paper"` rows as test artifacts

## 1:30–2:00 — Guardrails demo

- Decision log entry with `action: WAIT` and `reasons: ["No candidate passed gates"]`
- Slippage block from TWAK quote-only (`slippage_quote_state`)
- Risk events in `logs/risk_events.jsonl`

## 2:00–2:30 — x402 paid data flow

- Show `src/data/x402_client.py` signing USDC on Base
- Point to `bot_live.log` lines: `Built enriched x402 snapshot for N symbols`
- Note the x402 data wallet is isolated from the TWAK trading wallet

## 2:30–3:00 — Competition readiness

- `data/compete_registered.json`: `registered: true`
- Agent wallet holds non-zero USDC + BNB + in-scope asset (ETH)
- Compliance trade already proven: USDC → TWT tx `0x4a0f...8d43`
