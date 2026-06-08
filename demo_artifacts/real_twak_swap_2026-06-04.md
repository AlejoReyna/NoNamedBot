# Real TWAK Swap Evidence - 2026-06-04

## Context

- Network: BSC / BNB Smart Chain
- Wallet: `0x7CE28f5d2D1B2eFd8f87FF0a7fdC7D2EaB465c9c`
- Command shape verified against TWAK 0.17.0 positional swap syntax.

## Pre-Trade Balance

```text
BNB: 0.00159000
USDC: 8.40520501
USDT: 0.00000000
```

## Quote

```bash
twak swap 0.5 USDC BNB --slippage 1 --chain bsc --quote-only --json
```

```json
{
  "input": "0.5 USDC",
  "output": "0.000828627437793792 BNB",
  "minReceived": "0.000820341163415854 BNB",
  "provider": "LiquidMesh",
  "priceImpact": "0"
}
```

## Executed Swap

```bash
twak swap 0.5 USDC BNB --slippage 1 --chain bsc --json
```

```text
Swapping 0.5 USDC -> 0.000828456748071545 BNB via LiquidMesh
Sending token approval...
Approval tx: https://bscscan.com/tx/0x5863c33ba5fbfd7016fae9dfe062d853213b198376862fd76ce81336a20fe7d0
Swap tx: https://bscscan.com/tx/0x2b5db498c97d6c69af6718872feb749457e7e6434c17569a34a2f78ff64eda94
Swap executed!
```

```json
{
  "input": "0.5 USDC",
  "output": "0.000828458273533057 BNB",
  "minReceived": "0.000820173690797726 BNB",
  "provider": "LiquidMesh",
  "priceImpact": "0",
  "hash": "0x2b5db498c97d6c69af6718872feb749457e7e6434c17569a34a2f78ff64eda94",
  "fromChain": "bsc",
  "toChain": "bsc",
  "explorer": "https://bscscan.com/tx/0x2b5db498c97d6c69af6718872feb749457e7e6434c17569a34a2f78ff64eda94"
}
```

## Post-Trade Balance

```text
BNB: 0.00241804
USDC: 7.90520501
USDT: 0.00000000
```

## Result

- Real token approval transaction: `0x5863c33ba5fbfd7016fae9dfe062d853213b198376862fd76ce81336a20fe7d0`
- Real swap transaction: `0x2b5db498c97d6c69af6718872feb749457e7e6434c17569a34a2f78ff64eda94`
- Explorer: https://bscscan.com/tx/0x2b5db498c97d6c69af6718872feb749457e7e6434c17569a34a2f78ff64eda94
