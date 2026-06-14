# Pre-competition checklist — Cascade AI (BNB Hack Track 1)

**Trading window:** June 22 → June 28, 2026 (live PnL, hourly marked-to-market)
**Build/submission deadline:** June 21, 2026, 06:00 UTC
**DQ gate:** ~30% max drawdown. Min 1 trade/day to qualify.

---

## ✅ Done & verified (June 14)

The engineering is complete, deployed (`main`, commit `05b5b54`), and running
(`cascade-ai.service`, healthy):

- Rule-based ATR position sizing (moderate profile: `MAX_POSITION_PCT=0.20`, `BASE_RISK_PER_TRADE_PCT=0.02`)
- Volatility-aware exit targets (replaced the flat +15% miracle target)
- 18h time-stop (`MAX_HOLD_HOURS=18`) — forces turnover
- End-of-window flatten (needs `COMPETITION_END_UTC`, see below)
- Crash-safe exits (a failed swap can no longer crash the agent)
- **Sell path fixed and proven on-chain** — approval-race retry + plain-decimal
  amount formatting. Confirmed with real sells (ADA/AAVE/LTC/SHIB tx hashes).
- Test suite green (358 passed); Kimi audit approved.

**Pre-competition dust** (ATOM/FIL/ETH/DOT/BONK/DOGE/UNI/TON, ~$3) is genuinely
unsellable on-chain (sub-$1, route reverts). It's harmless: counts at its tiny
marked-to-market value, fails at gas-estimation (no gas burned), and becomes a
rounding error once the wallet is funded. **Do not spend more time on it.**

---

## ⬜ Remaining — all non-code, before the window

### 1. Fund the agent wallet  ← do this first; everything depends on it
- The ~$4 test balance is why every position is dust and can't sell. Fund the
  agent wallet with real working capital so positions are properly sized and
  sellable.
- Must hold a **non-zero balance of in-scope assets at competition start** to be
  ranked. USDC/USDT count.
- After funding, watch one real position open and sell to confirm the full loop:
  `grep -i "Swap executed" bot_live.log | tail`

### 2. Register the agent on-chain  ← hard deadline: before June 22
- Registration closes when the trading window opens. Late entries are rejected.
- Run: `twak compete register`  (or the `competition_register` MCP action)
- Competition contract (BSC): `0x212c61b9b72c95d95bf29cf032f5e5635629aed5`

### 3. Set the end-of-window flatten time
- Confirm the exact June 28 end time in the hackathon Telegram, then on the box:
  ```bash
  cd /home/ec2-user/nnyb
  upsert() { grep -q "^$1=" .env && sed -i "s|^$1=.*|$1=$2|" .env || echo "$1=$2" >> .env; }
  upsert COMPETITION_END_UTC 2026-06-28T23:59:00Z   # <-- confirmed time
  upsert FLATTEN_BEFORE_END_MINUTES 30
  sudo systemctl restart cascade-ai
  ```

### 4. Submit on DoraHacks  ← hard deadline: June 21, 06:00 UTC
- Public repo link + agent wallet address + a short strategy write-up explaining
  how the results were achieved (sizing/targets/realization + TWAK self-custody +
  x402 in the trade loop — the judged criteria).

---

## Operational notes
- **Don't rapid-restart.** systemd allows 10 starts/hour (`StartLimitBurst=10`).
  If you hit `start-limit-hit`, clear it with:
  `sudo systemctl reset-failed cascade-ai && sudo systemctl start cascade-ai`
- Deploy flow: commit+push on laptop → `git pull --ff-only` on box → restart.
- Health check: `curl -s "http://34.226.247.39:8787/health?fresh=$(date +%s)"`
- Emergency flatten (manual): `python -m src.main --emergency-liquidate`

## Optional polish (not blocking)
- **Dust min-notional guard:** skip exit attempts on sub-$1 positions so the
  log stops showing the harmless dust reverts each cycle. ~10 lines + a test.
- **Partial scale-out at +1R:** bank a fraction of winners early. Higher realized
  PnL; do it only after the funded sell loop is validated live.
