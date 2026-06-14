# Deploy runbook — rule-based sizing, vol-aware targets, forced realization

**Branch:** `feat/rule-based-sizing-targets-exits` (audited & APPROVED by Kimi 2026-06-14)
**Target:** EC2 `cascade-ai.service` (`/home/ec2-user/cascade-ai`, runs `python -m src.main --live`)
**Status before activation:** code is **inert** — defaults reproduce current behavior until the `.env` block below is set.

---

## 1. Pre-deploy checks (local, py3.12 venv)

The Linux sandbox used for development is py3.10 and cannot import `src.main`
(needs `bnb-chain-agentkit`, py3.12). Run the **full** suite on the real env:

```bash
cd ~/Documents/BNBHacks/cascade-ai
source .venv/bin/activate            # py3.12 venv
git checkout feat/rule-based-sizing-targets-exits
python -m pytest tests/test_rule_based_exits.py tests/test_position_manager_v2.py -q
# Expect: 10 passed (incl. the 2 window-flatten tests) + 9 passed.
python -m pytest -q                  # full suite; expect ~330 passed, 2 known ML fails.
```

Confirmed already in dev sandbox: 8/8 pure-logic + 9/9 regression pass; `py_compile`
clean on all edited files. Only the 2 `src.main`-importing window tests remain to
confirm here.

---

## 2. Activate — `.env` on EC2

The vol-aware **targets** are automatic the moment this branch is live (the entry
path always passes `regime`). To also turn on **forced realization** and the
intended **competition sizing**, append/adjust these in `/home/ec2-user/cascade-ai/.env`:

```bash
# --- Forced realization (NEW) ---
# Trading window is June 22-28, 2026 (DoraHacks). Exact end-of-day clock time is
# not published to the minute -- CONFIRM in the hackathon Telegram, then set below.
# Scoring is hourly marked-to-market, so this flatten is a DEFENSIVE lock-in of the
# final score (and a guard against a last-hour drop tripping the ~30% drawdown DQ),
# not a scoring requirement.
MAX_HOLD_HOURS=18                          # time-stop: close positions stale > 18h
COMPETITION_END_UTC=2026-06-28T23:59:00Z   # <-- verify the exact June 28 end time
FLATTEN_BEFORE_END_MINUTES=30              # liquidate the whole book 30m before close

# --- Risk-based sizing (recommended for the competition profile) ---
BASE_RISK_PER_TRADE_PCT=0.02               # 2% risk budget per trade
MAX_POSITION_PCT=0.20                      # 20% conviction cap

# --- Optional: the flat take-profit no longer drives NEW entries (vol-aware
#     path overrides it), but it is still read for reconstructed/legacy rows.
#     Lower it so any legacy path is also realistic instead of +15%.
TAKE_PROFIT_PCT=0.10
```

Notes:
- `MAX_HOLD_HOURS=0` (or unset) fully disables the time-stop. 18h gives ~1–2 turns/day
  over a 1-week window; tune 12–24h to taste.
- `COMPETITION_END_UTC` empty/unset = window-flatten disabled. **Set the exact
  competition end time** — once `now >= end − FLATTEN_BEFORE_END_MINUTES` the bot
  liquidates everything to USDC and stops opening new positions.
- Tighten `FLATTEN_BEFORE_END_MINUTES` only if swaps are fast; 30m is a safe buffer
  for liquidity/slippage on the full book.

---

## 3. Deploy (on EC2 as ec2-user)

```bash
# from your machine: copy the changed source + tests up
scp -r src tests docs ec2-user@34.226.247.39:/home/ec2-user/cascade-ai/

# on EC2: edit .env per section 2, then restart the service
sudo systemctl restart cascade-ai
systemctl status cascade-ai --no-pager
tail -f /home/ec2-user/cascade-ai/bot_live.log
```

The service `WorkingDirectory` is load-bearing (`positions.json`, `price_cache.json`,
`guardrail_state.json` are cwd-relative) — do not run the bot from a different dir.
Restarts are cheap; the snapshot cache persists.

---

## 4. Validation — confirm the changes are live (first few cycles)

Watch `bot_live.log` and `logs/decision_live.jsonl`:

1. **Vol-aware targets:** new entries should NOT all show a `+15%` corridor. On a
   cold cache they get an 8% target; once ATR warms, targets vary by asset. Cross-check
   the dashboard "Risk corridor" line — it should no longer read `+15.0%` for every row.
2. **Risk-based sizing:** position notionals should reflect `BASE_RISK_PER_TRADE_PCT /
   stop-distance`, capped at `MAX_POSITION_PCT` — not uniform dust.
3. **Time-stop:** after `MAX_HOLD_HOURS`, a stale flat position should exit with
   `exit_reason: "time_stop"` in the decision/execution logs.
4. **Window flatten (dry sanity):** temporarily set `COMPETITION_END_UTC` to ~40 min in
   the future on a throwaway run → confirm it logs `Competition window flatten:
   liquidating N open positions` and blocks entries (`entries_blocked_reason:
   competition_window_flatten`). **Reset to the real deadline afterward.**

If anything looks wrong, the kill path is unchanged: `python -m src.main
--emergency-liquidate` flattens to USDC immediately.

---

## 5. What was applied since the audit (post-approval cleanups)

Both Kimi minor notes resolved (tests still green):
- `_open_local_position_v25` now selects the call form via
  `inspect.signature(...)` instead of a broad `except TypeError`, so it can no
  longer mask a genuine `TypeError` from inside `open_position`.
- Cold-ATR sizing stop clamp aligned to `min(0.08, ...)` (was `0.10`) to match the
  warm-ATR upper bound.

## 6. Explicitly deferred (post-validation, per audit)

- **Partial scale-out at +1R** — bank a fraction of winners early. Highest PnL upside
  but touches the live partial-fill swap path; do it only after the above is validated
  in production.
- **Selection bias toward higher-vol names** — the audit confirmed vol-aware targets
  fix *realizability*, not large-cap *expectancy* (ETH 8% target is still ~−0.37% EV on
  a pure random walk). The remaining edge is entry quality + concentrating size where a
  +R target is reachable. Tune via the breakout score / candidate filter, with a replay
  pass before changing thresholds.
- **Compliance dust swaps** (`COMPLIANCE_TRADE_USDC=0.5`) — still create junk $0.50 rows;
  gate off once real rule-based entries are flowing.
