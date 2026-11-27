# Hyperliquid Quant Lab

A local research workspace for crypto strategy backtests on Hyperliquid public candles, with optional testnet paper trading. It also carries an on-chain index fund on HyperEVM testnet: strategy weights are relayed into a smart contract that issues tokenized fund shares at NAV.

It can be driven two ways: run the Python scripts directly, or let an opencode + DeepSeek agent drive them through the `SKILL.md` playbook in this repo. Either way, all computation stays in the scripts — the agent only fills in parameters, runs commands, and explains results.

## Quick start (scripts only, no agent)

Requirements: Python 3.9+.

```bash
pip install -r requirements.txt
```

Public candle check (no keys required):

```bash
python scripts/data.py candles ETH --granularity 1d --start 2024-01-01
```

## Install with opencode (recommended)

First make sure opencode itself works: install it with `npm install -g opencode-ai` (or run `npx opencode` from inside a folder — this repo carries a `package.json` for that local setup), start it, run `/connect` to connect DeepSeek, and pick the model with `/models`.

After opencode works with DeepSeek, paste this whole prompt:

```
You are installing the Hyperliquid Quant Lab skill.

Do the following, then print a checklist table (OK / FAIL):
1) Ensure git and python3 (>=3.9) exist.
2) Clone or update the skill:
   mkdir -p ~/.claude/skills
   if [ -d ~/.claude/skills/Hyperliquid-quant-lab/.git ]; then
     git -C ~/.claude/skills/Hyperliquid-quant-lab fetch origin && git -C ~/.claude/skills/Hyperliquid-quant-lab pull
   else
     git clone https://github.com/sun97956/Hyperliquid-quant-lab.git ~/.claude/skills/Hyperliquid-quant-lab
   fi
3) mkdir -p ~/lab-hl && cp -R ~/.claude/skills/Hyperliquid-quant-lab/{scripts,templates,contracts} ~/lab-hl/ && cp ~/.claude/skills/Hyperliquid-quant-lab/requirements.txt ~/lab-hl/
4) python3 -m venv ~/lab-hl/.venv && source ~/lab-hl/.venv/bin/activate
   pip install -U pip
   pip install -r requirements.txt
5) Quick checks (public data only — no keys):
   cd ~/lab-hl && source .venv/bin/activate
   python scripts/data.py candles ETH --granularity 1h --start 2026-09-01 | tail -5
6) NEVER ask for seed phrases, master private keys, or agent keys. Tell the user to run
   `python scripts/setup_keys.py` themselves in their own terminal.

Print versions and pass/fail for each step. Stop after the checklist.
```

How the skill gets imported: there is no import command — opencode scans `~/.claude/skills/` at startup, so step 2 above (cloning this repo into that folder, with `SKILL.md` at its root) is the import. If opencode was already running, restart it, then ask it to `List your available skills` to verify the skill was picked up.

Manual fallback:

```bash
git clone https://github.com/sun97956/Hyperliquid-quant-lab.git ~/.claude/skills/Hyperliquid-quant-lab
mkdir -p ~/lab-hl && cp -R ~/.claude/skills/Hyperliquid-quant-lab/{scripts,templates,contracts} ~/lab-hl/ && cp ~/.claude/skills/Hyperliquid-quant-lab/requirements.txt ~/lab-hl/
cd ~/lab-hl && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
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

## Wallet setup and testnet trading

Trading in this project uses an API/agent wallet — a sub-key that can trade but cannot withdraw — never your main wallet key:

1. Create an API/agent wallet on the Hyperliquid testnet site (API page) and copy its private key once.
2. Run `python scripts/setup_keys.py` in your own terminal and paste your master address + the agent key. It writes `~/.hyperliquid/env` (mode 600) with `HL_ACCOUNT_ADDRESS`, `HL_AGENT_KEY`, and `HL_NETWORK`.
3. Restart opencode once after setup. `scripts/trade.py` loads the env file itself; the agent never sees the key.
4. Verify the setup: `python scripts/trade.py balance` prints the account address and its testnet balance.

`scripts/trade.py` defaults to dry-run. `--send` still requires typing `YES`.

Safety:

- Never share seed phrases or master private keys.
- Keep only the API/agent key in the environment file.
- Use testnet unless you intentionally intend to use real funds.
- Keep per-order notional small and create `KILL_SWITCH` to freeze sends.

## On-chain index fund (HyperEVM testnet)

`contracts/` holds the tokenized fund: `IndexFund.sol` (fund shares issued and redeemed at NAV, on-chain target weights, guardrails: coin whitelist, 50% single-asset cap, 20% drawdown circuit breaker, volatility-linked redemption fee), `IIndexFund.sol` (the reusable interface third-party developers plug into), `MockUSD.sol` (test base asset with a public faucet), and `local_test.js` (local end-to-end rehearsal, 20 checks).

`scripts/relay_weights.py` bridges a strategy's `weights.csv` into the contract: it reads the latest target weights, checks BTC realized volatility (high volatility pushes immediately and raises the redemption fee to 100 bps; otherwise a push needs at least a 1% weight change), and — signed by the agent key — writes the weights and reports the trading account's total value as the fund's valuation. Default is a dry run; `--send` transmits.

```bash
python scripts/relay_weights.py --weights results/index_strategy_v1/weights.csv --fund <FUND_ADDRESS> --send
```

Deployed 2026-11-26 on HyperEVM testnet (chain 998; explorer: https://testnet.purrsec.com):

| Contract | Address |
|---|---|
| IndexFund (AIDX) | `0xB29aDaB6f2ea9Bf9EE084E22006a739802E0Fbe4` |
| MockUSD | `0x1f2DDA28Dbd9800df76DcC3582E1102CFfe24944` |

The owner is the main wallet; the contract's agent is the same agent key as `trade.py`. Compile with optimizer runs 200 and EVM version paris — unoptimized bytecode exceeds HyperEVM's 3M small-block gas limit.

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
| `scripts/relay_weights.py` | Relay strategy weights + valuation into the fund contract |
| `scripts/equal_weight_index.py` | Equal Weight Index strategy |
| `contracts/` | IndexFund contracts (fund, interface, MockUSD) and local rehearsal |
| `results/` | Per-strategy backtest outputs |
