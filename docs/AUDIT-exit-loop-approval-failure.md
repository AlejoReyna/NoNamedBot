# Audit handoff — Exit swaps revert in a loop (`0xf4059071`), positions cannot close

**Date:** 2026-06-14
**Author:** initial diagnosis by Claude (Cowork). Handoff for a second model (Codex) to
audit independently, then reconcile both opinions before any code change.
**Severity:** HIGH — live competition wallet. The bot cannot exit any position;
all sell-side execution has been failing for hours.

---

## 0. TL;DR for the auditor

Over the last ~10h on the live `cascade-ai` instance (EC2 `34.226.247.39`,
`WorkingDirectory=/home/ec2-user/cascade-ai`, service `cascade-ai`), the bot
attempted **1,222 exit swaps; 3 succeeded, 1,219 reverted.** Every failure is the
same on-chain revert: `execution reverted: 0xf4059071` (`errorCode: TX_FAILED`),
swapping each held token → USDC via the LiquidMesh route.

`0xf4059071` is an **ERC-20 allowance / "check allowance" error** — the router is
not approved to spend the token, so the swap reverts before executing.

The code already knows this selector. `TWAKInterface._is_approval_race_failure`
(in `src/execution/twak_interface.py`) only treats it as retryable when the error
text **also** contains the string `"approval was sent"`. The live failures do
**not** contain that string (they are bare `TX_FAILED` reverts), so the guard
returns `False`, no approval/retry is triggered, the exit is logged as failed, and
the next cycle repeats the identical un-approved swap. Result: an infinite
fail-retry loop and trapped positions.

**What we want from the auditor:** confirm/refute the allowance hypothesis,
sanity-check the proposed fix, and flag anything we've missed (e.g. whether the
TWAK CLI is supposed to auto-approve and is silently not, decimals/dust, or route
liquidity). Operator has no contract ABI for the LiquidMesh router, so the
selector cannot be decoded from source — treat the allowance reading as
*strong inference*, not proven.

---

## 1. Environment / where the data came from

- Host: `[ec2-user@ip-172-31-80-189 cascade-ai]$` — i.e. the EC2 box itself.
- Live logs (cwd-relative, per `systemd/cascade-ai.service`):
  - `logs/decision_live.jsonl`
  - `logs/portfolio_snapshots.jsonl`
  - `logs/risk_events.jsonl`
  - `execution_log.jsonl` (root) — actual swap attempts + results
- There are sibling dirs on the box (`cascade-ai-repo`, `cascade-ai.old`,
  `cascade-ai-old`, `cascade-ai-backup`, `nnyb`, …) and a separate `nnyb` bot
  running different symbols. **The failing tokens below (DOGE/ATOM/FIL/ETH/DOT/
  BONK) match the live dashboard positions, so `cascade-ai` is the instance under
  audit.** Auditor should still confirm the running unit's cwd:
  `systemctl status cascade-ai --no-pager | grep -E 'CGroup|python'`.

---

## 2. Evidence

### 2.1 10-hour window summary
```
decisions:   Counter({'WAIT': 99, 'ENTER': 2})
executions:  Counter({'exit': 1215, 'entry': 2})    # 1222 exits in the exit-only pass
risk events: Counter({'reduced_risk': 1})
portfolio:   10.38246944497524 -> 10.354308774521717   # flat / slightly down
last decision ts: 2026-06-14T17:25:59.625418+00:00     # bot is live & current
```

### 2.2 Exit breakdown (10h)
```
total exits: 1222
exits by token:
  DOGE: 204   ATOM: 203   FIL: 203   ETH: 203   DOT: 203   BONK: 203
  UNI: 1      TON: 1      TWT: 1
succeeded (have tx_hash): 3
errored: 1219
top error messages (truncated to 80 chars):
  [204] twak swap 5.59594069 failed with exit code 1: stderr: Swapping 5.59594069 DOGE -
  [203] twak swap 0.05281430348447367 failed ...
  [203] twak swap 0.13779037815710085 failed ...
  [203] twak swap 0.000062303634196258 failed ...   # ETH dust, ~0.0000623
  [203] twak swap 0.10629792971091118 failed ...
```
Each of the 6 open positions is retried ~203 times (≈ once per decision cycle for
10h). This is uniform across tokens and amounts — **not** token-specific, which
argues against a per-token liquidity/decimals problem and toward a systemic
allowance/approval problem.

### 2.3 Full error of the most recent failure
```
ts: 2026-06-14T17:31:55.421734+00:00
token: DOGE -> USDC
amount_in: 5.59594069
FULL ERROR:
 twak swap 5.59594069 failed with exit code 1: stderr:
   Swapping 5.59594069 DOGE -> 0.480096390485040875 USDC via LiquidMesh
 | stdout: {
     "error": "execution reverted: 0xf4059071",
     "errorCode": "TX_FAILED"
   }
```
Key: the CLI *quotes successfully* (it prints a USDC out-amount), then the
broadcast reverts. So routing/liquidity exists; the tx itself reverts at execution
— consistent with a missing spend allowance, not a no-route condition.

---

## 3. Root cause (hypothesis, ranked)

### H1 (primary) — Missing ERC-20 allowance, and the approval-retry guard is too narrow
`src/execution/twak_interface.py`:

The swap path issues a single `twak swap` and routes failures through
`_run_swap_with_approval_retry`:
```python
def swap(self, from_symbol, to_symbol, amount, slippage_pct) -> dict[str, Any]:
    ...
    command = ["twak", "swap", _format_amount_for_cli(amount),
               resolve_twak_token(from_symbol), resolve_twak_token(to_symbol),
               "--slippage", self._fraction_to_cli_percent(slippage_pct),
               "--chain", "bsc", "--json"]
    result = self._run_swap_with_approval_retry(command)
    return self._swap_payload_from_result(result)
```
```python
def _run_swap_with_approval_retry(self, command):
    retries = 0
    while True:
        try:
            return self._run(command)
        except TWAKCommandError as exc:
            if not self._is_approval_race_failure(exc.result):
                raise                      # <-- live failures hit this branch
            ...
```
```python
@staticmethod
def _is_approval_race_failure(result) -> bool:
    decoded, _ = TWAKInterface._decode_swap_stdout(result.stdout)
    if isinstance(decoded, dict) and decoded.get("errorCode") == "APPROVAL_SENT_SWAP_FAILED":
        return True
    combined = f"{result.stdout}\n{result.stderr}".lower()
    if "approval_sent_swap_failed" in combined:
        return True
    return "approval was sent" in combined and (
        "0xf4059071" in combined or "check allowance" in combined
    )       # <-- requires BOTH; live error has 0xf4059071 but NOT "approval was sent"
```
The observed stdout is `{"error":"execution reverted: 0xf4059071","errorCode":"TX_FAILED"}`.
It contains `0xf4059071` but **not** `"approval was sent"` and **not**
`APPROVAL_SENT_SWAP_FAILED`. So `_is_approval_race_failure` → `False`, the error
re-raises, the exit is recorded as failed, and the loop repeats next cycle with the
same un-approved token. The allowance is never established because **nothing in
this path ever issues a standalone `approve`** — it depends on the TWAK CLI to
approve inline, and for these reverts that inline approval is evidently not
happening (or not landing before the swap).

Why 3 succeeded: likely the entries (USDC→token) and/or a token that already had a
residual allowance. Auditor should confirm by inspecting the 3 success rows.

### H2 (secondary) — TWAK CLI approval flow not actually sending the approve tx
Possible the CLI is *meant* to approve-then-swap in one call but, for already-held
positions opened in a prior run/binary, it skips the approve and assumes allowance.
Auditor: check TWAK CLI version/flags for an explicit approve step or
`--approve`/`--max-approval` option; the wrapper passes none.

### H3 (low) — dust / min-output
ETH amount is ~0.0000623 (≈$0.10). Tiny. But the CLI returns a valid quote and the
revert selector is allowance-shaped, not slippage/min-out shaped, so this is
unlikely to be the primary cause. Worth ruling out for the smallest positions only.

---

## 4. Impact

- **Positions are trapped.** The bot cannot realize any exit; stops/targets are
  effectively inert because the sell reverts. The dashboard "Execution Rate 0.0%"
  reflects this.
- **No capital lost to fees** (reverts cost gas only; wallet still holds BNB), but
  **risk management is non-functional** — a real drawdown could not be cut.
- Portfolio "movement" the operator noticed is unrealized price drift, not the bot
  working. Net over the window: `10.38 → 10.35`.

---

## 5. Proposed fixes (for both models to weigh in on)

### Fix A — Broaden the retry guard to treat bare allowance reverts as approvable
Trigger the approve+retry path when the error is an allowance revert *regardless*
of the `"approval was sent"` substring:
```python
# treat a bare allowance revert as "needs approval", not just the race case
if isinstance(decoded, dict) and decoded.get("errorCode") in (
    "APPROVAL_SENT_SWAP_FAILED", "TX_FAILED"
) and "0xf4059071" in combined:
    return True
```
Risk: `TX_FAILED` is generic; gating on `0xf4059071` keeps it allowance-specific.
But retrying the *same un-approved swap* will loop unless an actual approve is sent
first (see Fix B). So Fix A alone is insufficient.

### Fix B (preferred) — Issue an explicit ERC-20 approve before the swap on allowance revert
On an allowance revert, run a `twak approve` (or equivalent) for `from_symbol` →
router, wait for confirmation, then retry the swap once. Cap retries
(`approval_retry_max`, default 3) and back off (`approval_retry_delay_seconds`,
default 7s) — both already exist. This is the durable fix; Fix A only makes the
loop *recognize* the condition.

### Immediate operator mitigation (no deploy)
Manually grant allowance / manually sell the 6 trapped tokens via the TWAK CLI with
an explicit approval, so positions can close while the code fix is reviewed. Do NOT
restart the loop expecting it to self-heal — it will not, the allowance is the
blocker.

---

## 6. Open questions for Codex

1. Decode `0xf4059071` against the LiquidMesh router ABI if obtainable — confirm it
   is the allowance/`InsufficientAllowance`-class error vs. something else.
2. Inspect the 3 successful exit rows: which tokens, and did they have pre-existing
   allowance? That tests H1.
3. Does the installed TWAK CLI support an explicit approve step/flag? If yes, Fix B
   should use it; if not, we need an on-chain approve via the BNB toolkit
   (`src/execution/bnb_toolkit_wrapper.py`).
4. Is approval per-token-per-spender persistent? If the spender (router) address
   rotated between the binary that opened these positions and the current one, that
   alone explains the missing allowance.
5. Any reason exits should be rate-limited regardless? 203 retries/token/10h is
   wasteful even once fixed — consider a per-position failed-exit backoff /
   circuit-breaker so a persistent revert doesn't hammer every cycle.

---

## 7. Commands to reproduce (run in the live cwd on EC2)

```bash
# 10h window summary
python3 - <<'PY'
import json,collections,datetime as d
t=d.datetime.now(d.timezone.utc)-d.timedelta(hours=10)
def w(p):
    out=[]
    try:
        for l in open(p):
            l=l.strip()
            if not l: continue
            r=json.loads(l); ts=r.get("timestamp")
            if ts and d.datetime.fromisoformat(ts.replace("Z","+00:00"))>=t: out.append(r)
    except FileNotFoundError: pass
    return out
dec=w("logs/decision_live.jsonl"); ex=w("execution_log.jsonl")
sn=w("logs/portfolio_snapshots.jsonl"); rk=w("logs/risk_events.jsonl")
print("decisions:",collections.Counter(r.get('action') for r in dec))
print("executions:",collections.Counter(r.get('action') for r in ex))
print("risk events:",collections.Counter(r.get('event_type') for r in rk))
if sn: print("portfolio:",sn[0].get('portfolio_value_usdc'),"->",sn[-1].get('portfolio_value_usdc'))
PY

# exit error breakdown + full latest error
# (see scripts/audit_window.py for a reusable version)
```

A reusable read-only auditor lives at `scripts/audit_window.py`
(`python scripts/audit_window.py --hours 10`). It never touches the wallet.
