# Crypto Quant Lab

A local research workspace for crypto strategy backtests on Hyperliquid public candles, with optional testnet paper trading.

It can be driven two ways: run the Python scripts directly, or let an opencode + DeepSeek agent drive them through the `SKILL.md` playbook in this repo. Either way, all computation stays in the scripts — the agent only fills in parameters, runs commands, and explains results.

## Quick start (scripts only, no agent)

Requirements: Python 3.9+.

```bash
pip install hyperliquid-python-sdk eth-account numpy pandas matplotlib
```

Public candle check (no keys required):

```bash
python scripts/data.py candles ETH --granularity 1d --start 2024-01-01
```

## Run it with opencode (agent workflow)

### Step 1: Install opencode

Either install it globally:

```bash
npm install -g opencode-ai
```

or run it without installing, from inside this folder (this repo carries a `package.json` for the local setup):

```bash
npx opencode
```

Start opencode **from this repo's folder** — it works with the files and skills it can see from where it is launched.

### Step 2: Connect DeepSeek

Inside opencode:

1. Run `/connect`, choose **DeepSeek**, and paste your DeepSeek API key.
2. Run `/models` and select the DeepSeek model you want to use.
3. Ask it a trivial question (e.g. "say hi") to confirm the model actually replies before you continue.

### Step 3: Import the skill

opencode has no "import skill" command. At startup it automatically scans a fixed set of folders for skill folders — one of them is `~/.claude/skills/`. A skill is imported simply by **cloning this repo into that folder** (the `SKILL.md` at the repo root is the playbook):

```bash
git clone https://github.com/sun97956/Hyperliquid-quant-lab.git ~/.claude/skills/Hyperliquid-quant-lab
```

Two things to know:

- If you clone or update the skill while opencode is already running, **restart opencode** — it only scans at startup.
- To verify the skill was picked up, start opencode and ask it to `List your available skills`. If the skill is missing, check that `SKILL.md` sits directly inside the cloned folder.

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
| `SKILL.md` | Agent playbook: how opencode drives the scripts, stage by stage |
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
