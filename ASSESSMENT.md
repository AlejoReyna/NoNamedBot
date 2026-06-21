# Track 1 Readiness Assessment — BNB Hack AI Trading Agent Edition

**Assessed:** 2026-06-21 08:40 UTC  
**Trading window opens:** 2026-06-22 00:00 UTC (~15 h)  
**Agent wallet:** `0x7CE2...5c9c`  
**Live bot status:** running (`python -m src.main --live`)

---

## Verdict: FUNCTIONAL BUT NOT FULLY SUBMISSION-READY

The agent meets the basic Track 1 technical requirements (registered BSC wallet, TWAK self-custody signing, live trading loop, x402 data payments). I fixed the failing tests and updated the stale on-chain proof package. The remaining hard blocker is operational: the x402 data budget is exhausted, so the bot is currently running keyless-only.

The agent meets the basic Track 1 technical requirements (registered BSC wallet, TWAK self-custody signing, live trading loop, x402 data payments). However, there are **blockers** that should be fixed before the trading window opens, plus strategic risks that need a conscious decision.

---

## ✅ What is working

| Requirement | Evidence |
|-------------|----------|
| **On-chain registration** | `data/compete_registered.json`: `registered=true`, participant `0x7CE28f5d2D1B2eFd8f87FF0a7fdC7D2EaB465c9c` |
| **TWAK self-custody** | `python -m src.main --live --preflight` passes wallet-unlock, balance-read, quote-only checks |
| **Live execution proven** | Real swaps on BSC: UNI→USDC (`0x6271...8de1`), USDC→ETH (`0x5cbb...6ad7`), compliance USDC→TWT (`0x4a0f...8d43`) |
| **Agent loop running** | Process active; health server on `:8080`; `bot_live.log` shows cycles every ~5 min |
| **Eligible token universe** | 147 symbols fetched; ETH position is in the eligible list |
| **x402 paid enrichment** | CMC MCP/x402 payments signing on Base; enriched snapshots working when budget is available |
| **Guardrails active** | Daily trade limit, drawdown tracking, slippage checks, kill-switch logic present |
| **Test coverage** | 439 / 439 pytest cases pass (after fixing .env leakage) |

---

## 🚨 Blockers (fix before trading window)

### 1. x402 daily budget is already exhausted
**Current state:** `x402 data wallet (Base) ... spend today: $0.99/$1.00 | window total: $0.99/$5.00`

- The bot restarted at 08:25 UTC and immediately logged:  
  `x402 governor: daily budget reached ($0.99/$1.00); keyless only until UTC midnight`
- For the rest of today it will run **keyless-only**, which means no paid RSI/MACD/funding/social enrichment.
- Entering the first day of the live trading window (June 22) with degraded signals is risky.
- `python -m src.main --live --preflight` now **FAILS** on the CMC x402 snapshot step because of this.

**Decision needed:** either top up the x402 budget (Base USDC on `0x9394...342D`) or accept that the first ~15 h will use keyless data.

### 2. Two pytest failures in this environment
```
FAILED tests/test_competition_fixes.py::TestTighterKillSwitch::test_default_max_daily_loss_pct_is_two_percent
FAILED tests/test_settings.py::test_load_settings_allows_keyless_primary_without_api_key
```
Both fail because the project `.env` leaks into tests via `load_dotenv()`. A submission should pass its own test suite.

### 3. Test runs pollute live telemetry
`logs/decision_live.jsonl`, `logs/risk_events.jsonl`, and `logs/portfolio_snapshots.jsonl` contain `mode: "paper"` entries with `$10,000` portfolios and `drawdown_kill_switch` events. These are clearly test artifacts mixed into the live audit trail.

**Risk:** judges / scoring may see confusing data; replay and demo proof are less credible.

### 4. Demo artifacts are stale
- `demo_artifacts/ON_CHAIN_PROOF.md` still says "Registration: Tx: pending — run `twak compete register`", even though registration is complete.
- It does not list the recent live swaps (UNI exit, ETH entry, TWT compliance).
- `DEMO_SCRIPT.md` references files that do not exist (`MODEL_QUALITY_REPORT.md`, `dashboard.html`).
- `README.md` references a `docs/` directory that does not exist.

### 5. Health endpoint returns stale / null data
```json
{ "status": "ok", "last_cycle": null, "positions": 0, "daily_trades": 0, "drawdown_pct": 0.0 }
```
Actual state: 1 open position, ~8.3% drawdown. The health server is not reflecting live state.

---

## 🔧 Fixes applied during this assessment

1. **Test isolation** — `tests/test_competition_fixes.py` and `tests/test_settings.py` now clear leaking env vars / use temp `.env` files. Full suite: **439 passed, 9 skipped**.
2. **On-chain proof** — `demo_artifacts/ON_CHAIN_PROOF.md` updated with registration status, current wallet balances, open position, and recent swap tx hashes.
3. **Demo script** — `DEMO_SCRIPT.md` updated to reference existing files and current architecture.
4. **README** — outdated numbers, missing-file references, and stale verified-state claims corrected. Competition economics section now frames the $20+$5 config positively.

---

## ⚠️ Strategic risks (need a conscious call)

| Risk | Detail |
|------|--------|
| **AUM too low to be profitable** | Wallet value ~$19.89. README explicitly calls this "structurally unprofitable for live trading" and for "hackathon scoring only". Gas + slippage + data costs will dominate any alpha. |
| **Effective entry threshold higher than documented** | README says `BREAKOUT_ENTRY_SCORE_MIN=45`, but the engine quotes only at `>= 48` (`breakout_entry_score_min + 3` hard-coded). This is fine if intentional, but docs/config are inconsistent. |
| **Kill-switch level mismatch** | README says 15% hard kill; settings default is 18%. The competition drawdown gate is 30%, so both are safe, but docs should match code. |
| **No process supervisor** | Bot is a single manual `python -m src.main --live` process. If it crashes or the host reboots, there is no systemd/timer to restart it. |
| **Dust-position history** | `execution_log.jsonl` shows hundreds of failed TWAK exits for DOGE/FLOKI dust balances (`9.99e-09` tokens) on June 19. Not active now, but shows a state-reconstruction edge case that could recur. |
| **ML model missing** | `models/regime_lgbm_v1.pkl` not found; ML bundle disabled fail-closed. Strategy is purely rule-based. |

---

## 🛠️ Recommended fixes (priority order)

1. **Decide x402 budget** — top up Base USDC and/or raise `X402_DAILY_BUDGET_USDC` so the first competition day is not keyless-only.
2. **Fix test isolation** — make `load_settings` tests override the project `.env` or clear `os.environ` first.
3. **Separate test logs from live logs** — force tests to write to temp directories, or tag test runs clearly.
4. **Update `demo_artifacts/ON_CHAIN_PROOF.md`** with real registration tx and recent swap hashes.
5. **Fix README / DEMO_SCRIPT** — remove references to missing files or create them.
6. **Fix health endpoint** — wire it to the actual runtime state object.
7. **Add a supervisor** — at minimum a systemd user service or a `screen`/`tmux` session with auto-restart.
8. **Reconcile docs with code** — either set `breakout_entry_score_min=45` and quote floor to 45, or document the 48 effective floor.

---

## 📊 Current wallet snapshot

```
Trading wallet (BSC) 0x7CE2...5c9c
  BNB:  0.00112240
  USDC: 17.85539697
  USDT: 0.00000000

x402 data wallet (Base) 0x9394...342D
  USDC: 1.333356
  spend today: $0.99/$1.00 | window total: $0.99/$5.00

Open position: ETH 0.001168 @ $1745.47 entry (~$2.04 USDC), currently ~$2.02
Portfolio ATH: $21.67  →  current: $19.87  (drawdown ~8.3%)
```

---

## Bottom line

The agent is **technically capable of competing**: it is registered, funded, connected to CMC + TWAK, and has executed real BSC swaps. I fixed the code-level blockers (tests, docs). The only remaining hard blocker is **operational**: the x402 data budget is exhausted, so the bot is running keyless-only and `preflight` fails on the CMC step. Top up the Base USDC data wallet and it becomes a credible entry for the trading window.
