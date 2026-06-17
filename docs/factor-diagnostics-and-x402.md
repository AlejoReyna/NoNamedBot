# Factor diagnostics, the "1/6 factors" display, and the x402 data path

This note explains why the bot/dashboard can appear stuck on "1/6 factors",
what each missing input means, how to populate RSI and derivatives data via paid
x402 enrichment, and how to safely recover a stale paper guardrail.

## The six entry factors

The breakout engine counts how many of these booleans are true:

| Factor | True when | Needs |
| --- | --- | --- |
| `volume_breakout` | 1h volume surges over the rolling average | keyless OK |
| `six_hour_high_break` | price clears the buffered 6h reference high | keyless OK |
| `regime_not_risk_off` | BNB + token 1h/24h trends are risk-on | keyless OK |
| `slippage_under_cap` | a TWAK quote came back under the cap | TWAK quote |
| `rsi_in_range` | RSI is in the 55–75 band | **x402 technicals** |
| `derivatives_risk_clear` | funding/OI are not stressed | **x402 derivatives** |

Two of the six (`rsi_in_range`, `derivatives_risk_clear`) require paid x402
data. The free/keyless CoinMarketCap trial REST API has **no technicals and no
derivatives endpoint**, so when the bot runs keyless-only those two factors
**fail closed on every token** — and `slippage_under_cap` only turns true once a
TWAK quote is actually obtained for a candidate. That is how a healthy keyless
run can sit at a low factor count even when the market looks fine.

## Why "1/6 factors" was misleading

There were two separate effects:

1. **Compliance trades.** The end-of-day "daily minimum" compliance swap is not
   scored against the six factors. The engine tags it with
   `source="daily_minimum"` and `factor_scores={"daily_minimum": true}`,
   `true_factor_count=1`. The dashboard used to render that `1` as "1/6
   factors". It now shows **"compliance trade" / "Compliance trade — not
   scored"** instead (see `factor-scoring.ts: isComplianceDecision`).

2. **Missing paid inputs.** When RSI and derivatives data are absent, two
   factors are legitimately false. That is real, but it was indistinguishable
   from "the data path is broken". The engine now surfaces the *reason* in
   `factor_metrics`:
   - RSI missing → `rsi_in_range: "RSI n/a · band 55–75"`
   - funding/OI missing → `derivatives_risk_clear: "funding/OI data missing"`
   - slippage never quoted → `slippage_under_cap: "not quoted · cap X%"`
   - slippage quoted but failed → `slippage_under_cap: "quote failed · cap X%"`
   - slippage quoted → `slippage_under_cap: "0.42% · cap 1.00%"`

   The decision `reason` and `BreakoutDecision.slippage_quote_state`
   (`not_quoted` | `failed` | `quoted`) carry the same distinction, and the
   per-symbol `factor_matrix.jsonl` log records `missing.rsi`,
   `missing.funding_rate`, `missing.open_interest_change_pct` when
   `FACTOR_MATRIX_LOG_ENABLED=true`.

## Enabling paid x402 enrichment (RSI + derivatives)

RSI/funding/OI are only populated on the **x402 enriched** snapshot
(`cmc_mcp_client.py` `_build_enriched_snapshot`). The keyless snapshot
(`_snapshot_from_quotes`) does not contain them. To get those fields populated:

| Env var | Set to | Effect |
| --- | --- | --- |
| `CMC_X402_EPHEMERAL_KEY` (or `EVM_PRIVATE_KEY`) | a funded Base-mainnet signer key | Enables x402 micropayments. Without a signer, dual/x402 mode is off and the run is keyless-only. |
| `USE_KEYLESS_PRIMARY` | `false` | Keep keyless from being the sole source. |
| `USE_DUAL_MARKET_DATA` | `true` (auto-on when a signer is present and keyless is not primary) | Refresh keyless every `LOOP_SECONDS` and pay for x402 enrichment every `CMC_SNAPSHOT_TTL_SECONDS`. |
| `X402_FETCH_TECHNICALS` | `true` (default) | Fetch x402 technical-analysis (RSI/MACD) for enriched symbols. **Without this, `rsi_in_range` fails closed even with a signer.** |
| `CMC_MCP_ENABLED` / `CMC_MCP_SHADOW_MODE` | `true` / `false` | Use the live x402 MCP adapter rather than shadow-only. |
| `X402_ENRICH_TOP_N` | e.g. `50` | Scope of paid enrichment per cycle (0 = all targets). |
| `X402_DAILY_BUDGET_USDC` / `X402_TOTAL_BUDGET_USDC` | budget caps | Governor limits on paid calls. |

Budget note: derivatives/funding/OI come from the enriched snapshot the same
way RSI does. `CMC_COLLECTOR_FETCH_TECHNICALS` stays `false` on purpose — the
background collector enriches the whole universe and would double paid calls;
the live loop still gets RSI via `X402_FETCH_TECHNICALS`.

If you intend to run keyless-only (no signer), accept that `rsi_in_range` and
`derivatives_risk_clear` will be false and rely on the weighted `entry_score`
(breakout + volume + momentum + macro) rather than the raw factor count — the
dashboard already prefers `score N/100` for breakout decisions.

## Stale paper guardrail (drawdown kill switch)

`guardrail_state.json` keeps `portfolio_ath` as a monotonic high-water mark.
If it was seeded at a notional bankroll (e.g. `10000`) that the paper balance
never actually reached, drawdown is measured against a peak that never
happened:

```
drawdown = (10000 - 8000) / 10000 = 20%  >=  drawdown_kill_switch_pct (18%)
```

That latches `kill_switch=true`, and the bot emits `action=HALT` /
`risk_state=kill_switch` every cycle even though no real capital was lost.

Safe recovery (paper only):

```bash
# Dry run first
PAPER_TRADE=true python scripts/recalibrate_guardrails.py --value 8000
# Then commit
PAPER_TRADE=true python scripts/recalibrate_guardrails.py --value 8000 --confirm
```

`recalibrate_guardrails.py` / `Guardrails.recalibrate_paper_state` re-anchor the
ATH to the current paper value and clear the latched kill switch. It **refuses
to run when `PAPER_TRADE` is false**, so it can never paper over a genuine live
drawdown — live recovery must be a deliberate, audited manual step.
