# Crypto Quant Lab

A local research workspace for crypto strategy backtests on Hyperliquid public candles, with optional testnet paper trading.

## Quick start

Requirements: Python 3.9+.

```bash
pip install hyperliquid-python-sdk eth-account numpy pandas matplotlib
```

Public candle check (no keys required):

```bash
python scripts/data.py candles ETH --granularity 1d --start 2024-01-01
```

## Strategies

| Script | Description | Outputs |
|---|---|---|
| `scripts/index_strategy_v1.py` | Index Strategy v1: 50% BTC + dynamic 50% alt sleeve, fully invested, weekly rebalance | `results/index_strategy_v1/` |
| `scripts/index_strategy_v2.py` | Index Strategy v2: BTC trend regime, alt trend filter, inverse-vol sleeve, volatility target, cash | `results/index_strategy_v2/` |
| `scripts/equal_weight_index.py` | Equal Weight Index: BTC, ETH, BNB, HYPE, AAVE at 20% each, weekly rebalance | `results/equal_weight_index/` |

Run:

```bash
python scripts/index_strategy_v1.py
python scripts/index_strategy_v2.py
python scripts/equal_weight_index.py
```

All strategies use fee `0.00035` and write results to their own folder under `results/`.

## Generic backtests

`scripts/backtest.py` runs single- or multi-asset signal backtests with train/test splits, fees, risk rules, and optional HTML output.

```bash
python scripts/backtest.py --symbols ETH --granularity 1h --start 2026-04-01 \
  --strategy rsi --params '{"n":14,"lo":30,"hi":70}' \
  --fee 0.00035 --split 0.7
```

By default, outputs go to `results/<strategy>/`. Use `--outdir <path>` to override.

Strategy templates live in `scripts/strategies.py`; `scripts/report_html.py` builds the single-file result page.

## Results layout

Each strategy writes to its own folder:

```
results/
  <strategy>/
    metrics.json
    weights.csv      # portfolio strategies
    trades.csv       # portfolio strategies
    equity.png
    report.html
```

The generic backtest uses the strategy name for the folder. Portfolio strategy scripts use `strategy_output_dir()` from `scripts/portfolio_engine.py`.

## Risk

`scripts/risk.py` contains `RiskConfig` and `RiskManager`:

- max single-symbol position: 50%
- stop loss / take profit: 2% / 4%
- max daily loss: 5%
- max leverage: 2x
- min order notional: 5 USDC
- `KILL_SWITCH` file freezes new sends

## Optional testnet trading

`scripts/setup_keys.py` stores a master address and an API/agent key in `~/.hyperliquid/env` (mode 600). The agent key can trade but cannot withdraw.

`scripts/trade.py` defaults to dry-run. `--send` still requires typing `YES`.

Safety:

- Never share seed phrases or master private keys.
- Keep only the API/agent key in the environment file.
- Use testnet unless you intentionally intend to use real funds.
- Keep per-order notional small and create `KILL_SWITCH` to freeze sends.

## Project layout

| File | Role |
|---|---|
| `scripts/portfolio_engine.py` | Shared data loading, portfolio simulation, metrics, and report rendering |
| `scripts/index_strategy_v1.py` | Index Strategy v1 |
| `scripts/index_strategy_v2.py` | Index Strategy v2 |
| `scripts/data.py` | Hyperliquid public candles / funding |
| `scripts/strategies.py` | Signal strategy templates |
| `scripts/backtest.py` | Generic signal backtest engine |
| `scripts/risk.py` | Risk configuration and checks |
| `scripts/report_html.py` | Single-file HTML result page |
| `scripts/setup_keys.py` | Save master address + agent key |
| `scripts/trade.py` | Optional testnet orders via agent wallet |
| `results/` | Per-strategy backtest outputs |
