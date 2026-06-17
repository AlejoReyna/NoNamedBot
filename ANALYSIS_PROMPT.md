# Data Analysis Prompt — Cascade AI

## What this codebase does

Cascade AI is a live BEP-20 momentum scalping bot competing in the BNB Hack hackathon (149 eligible tokens). It runs on an EC2 instance and uses a "6-factor breakout engine" to decide which token to enter, how large, and when. It executes trades via TWAK (a BNB chain wallet toolkit) and tracks everything in a Vercel dashboard.

---

## The trading algorithm (6-factor breakout engine)

File: `src/strategy/6falgorithm/breakout_engine.py`

The engine scores every token on up to 6 boolean factors and picks the best candidate each cycle:

1. **Volume breakout** — 24h volume > 5% of market cap, OR current volume > rolling 24h hourly average × threshold
2. **6h high break** — current price > highest price in last 3–6h reference windows (stored in `price_cache.json`)
3. **Regime not risk-off** — BNB 1h % change > `bnb_regime_threshold` AND token 1h % > `token_regime_1h_min` AND token 24h % > `token_regime_24h_min`
4. **RSI in range** — RSI between 55 and 75 (momentum zone, not overbought)
5. **Derivatives risk clear** — funding rate < 0.15% AND open interest 24h change > -10%
6. **Slippage acceptable** — estimated slippage < threshold (evaluated only for top candidates to conserve TWAK quotes)

Macro context (BTC dominance trend, stablecoin dominance trend, total market cap trend) is computed from `get_global_crypto_derivatives_metrics` and used to scale position size.

The final `entry_score` weights: volume breakout, momentum z-score (cross-symbol relative), RSI, breakout strength, volume surge score, and macro score.

---

## Data sources and what is currently collected

### x402 paid calls (0.01 USDC each, via CMC MCP)
Called by `src/data/cmc_mcp_client.py` and collected every ~30 min by `scripts/cmc_feature_collector.py`.

Fields assembled per symbol in `_build_enriched_snapshot()`:
- `price`, `market_cap`, `volume_1h`, `volume_24h`
- `percent_change_1h`, `percent_change_6h`, `percent_change_24h`
- `rolling_24h_hourly_volume_avg`
- `high_3h`, `high_6h`, `high_24h`, `low_24h`
- `rsi` (RSI-14 from technicals)
- `macd`
- `bnb_1h_trend_pct`
- `estimated_slippage_pct`
- `funding_rate`, `open_interest_change_pct`
- `macro_btc_dominance`, `macro_stablecoin_dominance`, `macro_total_market_cap`

### Persisted to disk
- `price_cache.json` — time-series `{symbol: [{timestamp, value}]}` used by breakout engine for 3/6/24h high detection
- `volume_cache.json` — same format for rolling volume average
- `data/cmc_premium.db` — SQLite with tables: `quotes` (per-symbol per-run), `funding_rates`, `fear_greed`, `market_metrics`
- `logs/market_snapshot_cache.json` — latest full enriched snapshot for bot warm-restart
- `data/cmc_premium/YYYY-MM-DD_HH-MM.json` — raw snapshot archive

### Free (keyless) calls
`fetch_keyless_quotes_snapshot()` — used only to harvest CMC `id` fields needed by the paid tool. Price data from this call is currently discarded.

---

## Token universe

- **149 eligible** (hackathon rule): `ELIGIBLE_149_SYMBOLS` in `src/config/tokens.py`
- **132 tracked** (non-stablecoins): `TRADABLE_TARGET_SYMBOLS` — all eligible minus the 17 stablecoins below
- **17 excluded** (stablecoins, intentional): `DAI, DUSD, EURI, FDUSD, FRAX, FRXUSD, TUSD, USD1, USDC, USDD, USDF, USDT, USDe, USDf, XUSD, lisUSD`
- **Dashboard shows 100** of 132 due to a `DEFAULT_LIMIT=100` cap in the agent-exporter `/status` response

---

## Infrastructure

- **EC2** — runs the Python bot (`bot_live.log`) and `cmc_feature_collector.py` (30-min systemd timer pending install)
- **agent-exporter** — Node.js service on EC2 (`/etc/systemd/system/cascade-ai-exporter.service`) serving `/status` at port 8787; reads `price_cache.json`, `volume_cache.json`, `logs/x402_calls.jsonl`, `decision_log.jsonl`, `execution_log.jsonl`
- **Vercel** — Next.js dashboard at `https://bot.alexisreyna.dev` reading from agent-exporter

---

## Your task

Analyze two things:

### 1. How can the already-collected data better improve the algorithm?

Look at what is gathered (listed above) vs. what the engine actually uses per evaluation cycle. Consider:
- Are all collected fields being consumed by the 6-factor scoring? (`macd`, `high_3h`, `low_24h`, `percent_change_6h`, `rolling_24h_hourly_volume_avg` — are these used or silently ignored?)
- The SQLite `quotes` table accumulates historical rows every 30 min. Could this history improve factor scoring? E.g. multi-period momentum signals, volume trend slope, RSI trend direction?
- `price_cache.json` feeds the 6h-high-break factor. With 30-min resolution from the collector, is the lookback window dense enough, or is the bot relying only on TWAK live quotes between runs?
- The macro context (BTC dominance delta, stablecoin dominance delta, total market cap delta) is used for position size multiplier. Is it being populated reliably from `get_global_crypto_derivatives_metrics`, or does it frequently fall back to the no-data default of `(0.0, 1.0)`?
- `momentum_z_score` is computed cross-symbol but currently hardcoded to `0.0` in some paths — is this feature live or dead?
- The free keyless snapshot returns price/volume data that is currently thrown away after id extraction. Could it serve as a lightweight inter-run update between 30-min x402 calls?

### 2. What data is NOT being collected that could improve decisions?

Consider gaps across these dimensions:

**Per-symbol missing:**
- `percent_change_7d` / `percent_change_30d` — longer-term trend context for regime detection
- Bid/ask spread or order book depth — better slippage estimation than the volume-cap heuristic
- Circulating supply / fully diluted valuation — needed to distinguish small-cap vs large-cap breakouts
- Number of exchanges listing the token — proxy for liquidity breadth
- `high_7d` / `low_7d` — wider breakout reference window

**Macro missing:**
- Fear & Greed Index — collected in `fear_greed` DB table but never passed into `token_data` for the engine to read
- BNB price and 1h change as a standalone field (currently inferred from BNB's entry in the quotes batch)
- Total crypto market cap trend at finer granularity (currently only a 24h delta)

**Derivatives / on-chain missing:**
- Long/short ratio — directional positioning of traders
- Open interest absolute value (not just % change) — size of leveraged bets on the asset
- Liquidation volume in last 1h/4h — leading indicator of volatility

**Operational missing:**
- Per-decision outcome tracking (entry price → exit price → PnL) stored in DB — needed to backtest which factor combinations actually win
- Factor miss log — which factor failed for which symbol each run (the engine logs this to `bot_live.log` as warnings but doesn't persist it)
- Collector run latency and success/failure rate — currently no alerting if the 30-min timer silently fails

Read the relevant source files before answering:
- `src/strategy/6falgorithm/breakout_engine.py` — full scoring logic
- `src/data/cmc_mcp_client.py` — what fields CMC actually returns vs what `_build_enriched_snapshot` maps
- `src/strategy/6falgorithm/evaluator.py` — how the engine is called from the main loop
- `scripts/cmc_feature_collector.py` — what gets persisted
- `data/cmc_premium.db` — query the `quotes` table to see actual field coverage: `SELECT symbol, COUNT(*) as rows, MIN(timestamp), MAX(timestamp) FROM quotes GROUP BY symbol LIMIT 10;`

Then produce:
1. A table of every field the engine reads from `token_data`, whether it's currently populated by the collector, and what impact it has on scoring when missing
2. A prioritized list of data gaps to close, ordered by expected impact on trade quality
3. Concrete code suggestions for the highest-impact gaps (e.g. passing `fear_greed` into `token_data`, using the DB history for momentum slope, recycling keyless data between x402 calls)
