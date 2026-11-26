---
name: crypto-quant-lab
description: Use for crypto quant research in this project — Hyperliquid public data, strategy backtests, portfolio simulation, risk, reports, and optional testnet paper trading.
license: MIT
compatibility: opencode
---

# Crypto Quant Lab

Local research skill for this project. Guide the user in English, one step at a time, and keep every command reproducible.

## Operating rules

1. **One step at a time.** Explain → command → output → confirm before moving on.
2. **Never ask for seed phrases or master private keys.** Only an API/agent key belongs in `~/.hyperliquid/env` via `setup_keys.py`. If a secret is pasted in chat, tell the user to revoke that agent and create a new one.
3. **Testnet-first.** `trade.py` defaults to dry-run; `--send` still requires typing `YES`. Do not use mainnet unless the user explicitly intends real funds.
4. **Hard caps.** Per-order notional default 25 USDC; `KILL_SWITCH` freezes sends.
5. Work inside the project folder. Keep strategy logic, engine code, and generated outputs separate.

## Environment

```bash
python3 --version   # >= 3.9
pip install hyperliquid-python-sdk eth-account numpy pandas matplotlib
```

Public data check (no keys):

```bash
python scripts/data.py candles <SYMBOL> --granularity <TIMEFRAME> --start <YYYY-MM-DD>
```

## Data

- `scripts/data.py` fetches Hyperliquid **mainnet public** candles and funding.
- The API returns at most ~5000 bars per coin and interval.
- Use larger intervals (`4h`, `1d`) for long windows and smaller intervals for short studies.
- Local CSV cache lives in `data/`.
- Align symbols by date before computing cross-sectional signals.

## Backtest framework

The project has two reusable layers.

### Signal backtests

`scripts/backtest.py` runs strategy functions over one or more symbols with fees, risk rules, train/test split, and optional HTML output. Signal functions live in `scripts/strategies.py` and return target positions in `[-1, 1]`.

```bash
python scripts/backtest.py --symbols <SYMBOLS> --granularity <TIMEFRAME> --start <YYYY-MM-DD> \
  --strategy <STRATEGY> --params '<JSON>' \
  --fee 0.00035
```

By default, outputs go to `results/<strategy>/`. Override with `--outdir <path>`.

### Portfolio backtests

`scripts/portfolio_engine.py` is for weight-based, multi-asset portfolios:

1. Define the universe in the strategy script, then load and align data with `prepare_market_data(start, symbols=UNIVERSE)`.
2. Build a rebalance schedule with `build_schedule`.
3. Define target weights at each decision date.
4. Execute targets at the next open with `simulate`.
5. Compare against benchmarks with `run_benchmarks`; its benchmark set is derived from `closes.columns`, not a fixed symbol list. Customize with `single_asset`, `fixed_weights`, and `fixed_label`.
6. Export metrics, weights, trades, equity chart, and HTML report with the shared helpers: `save_equity_chart` and `write_portfolio_report`.

The engine is universe-agnostic: no symbol list is hardcoded. `prepare_market_data` / `load_universe` require an explicit `symbols` argument, `weights_frame` derives assets from the weights dict, and `write_portfolio_report(..., assets=UNIVERSE)` renders any universe (when `assets` is omitted it infers them from the trades/weights). The engine supports long-only targets with cash, per-asset caps, transaction costs, and no-trade bands.

## Adding a strategy

### Signal strategy

1. Add `def my_strategy(data, params)` to `scripts/strategies.py`. Return a target-position table with the same index as the candles, columns = symbols, values in `[-1, 1]`.
2. Register the function in `STRATEGIES` (add a `STRATEGY_META` entry if it should show params in the report).
3. Do not reimplement data loading, fees, risk, or metrics. `backtest.py` already calls `data.py`, `risk.py`, and `report_html.py`.
4. Run:
   ```bash
   python scripts/backtest.py --symbols <SYMBOLS> --granularity <TF> --start <DATE> \
     --strategy my_strategy --params '<JSON>' --fee 0.00035
   ```
5. Check `results/<strategy>/metrics.json`, `equity.png`, `report.html`.

### Portfolio strategy

1. Create a new entry script `scripts/<name>.py`. Start from `scripts/index_strategy_v1.py` or `scripts/equal_weight_index.py`; do not copy engine code.
2. Define `UNIVERSE` at the top and pass `symbols=UNIVERSE` to `prepare_market_data`.
3. Build the schedule with `build_schedule`, then define targets as `{execution_date: (decision_date, weights_dict)}`.
4. Call `simulate(...)`, then `run_benchmarks(...)` with `single_asset` / `fixed_weights` / `fixed_label` if the default benchmarks do not fit.
5. Write outputs with `strategy_output_dir(name)`, `save_equity_chart(...)`, and `write_portfolio_report(..., assets=UNIVERSE)`.
6. Run `python scripts/<name>.py`. Entry scripts are ordinary Python files and are **not auto-discovered**; always invoke them explicitly. List `scripts/` instead of assuming a fixed strategy name.

### Edit boundaries

| Path | Agent may edit? | Rule |
|---|---|---|
| `scripts/strategies.py` | Yes | Add/register signal functions; leave existing entries intact |
| `scripts/<name>.py` | Yes | New portfolio strategy: strategy logic, `UNIVERSE`, CLI args, report wiring only |
| `scripts/data.py`, `portfolio_engine.py`, `portfolio_report.py`, `report_html.py`, `risk.py`, `backtest.py` | No, unless the user explicitly asks for a framework change | Change the universe/params in the strategy, never hardcode symbols or fork engine code |
| `results/` | Generated | Never hand-edit; rerun the script to regenerate |
| `~/.hyperliquid/env` | No | Written only by `setup_keys.py`; never print, commit, or copy secrets |
| `KILL_SWITCH` | Create to freeze | Delete only with user confirmation |

- Do not place order-sending code in a strategy script; execution stays manual through `trade.py`.
- If a task truly needs a shared-module change, say so, make the smallest change, and update this skill.
- These boundaries are mandatory conventions for this skill. Runtime guards live in `trade.py` (dry-run, typed `YES`) and `risk.py` (caps, `KILL_SWITCH`); actual file-write permission is enforced by opencode's tool configuration, not by this document.

### Correctness and reproducibility

- **No look-ahead:** compute signals at day `t` close and execute at day `t+1` open.
- **Explicit costs:** state the fee and turnover assumptions in the report.
- **Reproducibility:** keep parameters as CLI arguments or a config dict, not hidden constants.
- **Verify before finishing:** `python -m py_compile scripts/<file>.py`, run the script, then confirm the `results/<name>/` files exist and weights/metrics are sensible.

## Risk

`scripts/risk.py` defines `RiskConfig` and `RiskManager`:

| Rule | Default |
|---|---|
| Max single-symbol position | 50% |
| Stop loss / take profit | 2% / 4% |
| Max daily loss | 5% |
| Max leverage | 2x |
| Min order notional | 5 USDC |
| Kill switch | `KILL_SWITCH` file |

Use the same risk checks for backtests and paper trading where applicable.

## Outputs and reports

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

Use `strategy_output_dir()` from `scripts/portfolio_engine.py` in portfolio strategy scripts. The generic signal backtest creates the folder automatically from `--strategy`.

Reports are self-contained: metrics table, equity curve, drawdown, and configuration. Open the HTML file directly in a browser; no external CDN is required.

The generic report page is rendered by `scripts/report_html.py`; portfolio strategies render through `scripts/portfolio_report.py`.

## Optional testnet trading

How the wallet works in this project (for the agent):

- The user's master wallet stays in their wallet app; scripts never touch it. Trading uses a separate API/agent key that can trade but cannot withdraw.
- Keys live in `~/.hyperliquid/env` (`HL_ACCOUNT_ADDRESS`, `HL_AGENT_KEY`, `HL_NETWORK`). `trade.py` loads this file itself — never read, print, or copy the secret, and never ask the user to paste any key into the chat.
- Setup is done by the user, not the agent: if the env file is missing, or `trade.py balance` reports missing keys, tell the user to run `python scripts/setup_keys.py` in their own terminal (it stores the master address and the agent key they create on the testnet API page), then stop and wait. After setup the user restarts opencode once so new sessions pick up the env file.
- Verify the connection before any trading: `python scripts/trade.py balance` should print the account address and its testnet `accountValue`. If the agent address or balance does not match what the user expects, stop and report it.

```bash
python scripts/setup_keys.py
python scripts/trade.py balance
python scripts/trade.py limit --coin <COIN> --side buy --size <SIZE> --price <PRICE>
python scripts/trade.py limit --coin <COIN> --side buy --size <SIZE> --price <PRICE> --send
python scripts/trade.py orders
python scripts/trade.py cancel --coin <COIN> --oid <OID> --send
python scripts/trade.py positions
python scripts/trade.py close --coin <COIN> --send
```

Safety:

- Never share seed phrases or master private keys.
- Only the API/agent key goes into `~/.hyperliquid/env`.
- Agent keys can trade but cannot withdraw.
- Use testnet unless real funds are explicitly intended.
- Keep per-order notional small.
- The agent must run the dry-run form first and only add `--send` after the user explicitly confirms; never send orders automatically or from a strategy script.
- Mainnet requires `--mainnet` plus a typed `YES`; do not use it for this lab.

## Relaying weights into the fund contract

`scripts/relay_weights.py` connects the strategy output to the IndexFund contract (`contracts/IndexFund.sol`, HyperEVM testnet):

```bash
python scripts/relay_weights.py --weights results/<strategy>/weights.csv --fund <FUND_ADDRESS>
python scripts/relay_weights.py --weights results/<strategy>/weights.csv --fund <FUND_ADDRESS> --send
```

- It reads the latest `target_<COIN>` row, compares it with the on-chain weights, and checks BTC realized volatility from the `data.py` cache: high-volatility regime always relays and raises the fund's redemption fee; normal regime relays only when the largest weight change is at least 1%.
- It also reports the trading account's total value (`reportTotalAssets`) so the fund NAV tracks the HyperCore account.
- The contract's `agent` must be the address of `HL_AGENT_KEY` — the same env key as `trade.py`. Default is a dry run; `--send` transmits. If the contract's circuit breaker is tripped, the relay aborts and the owner must reset it first.

## Deployed fund (HyperEVM testnet, chain 998)

Deployed 2026-11-26; explorer: https://testnet.purrsec.com

| Contract | Address |
| --- | --- |
| IndexFund (AIDX) | `0xB29aDaB6f2ea9Bf9EE084E22006a739802E0Fbe4` |
| MockUSD (base asset) | `0x1f2DDA28Dbd9800df76DcC3582E1102CFfe24944` |

- Owner: the main wallet `0x8d30B151Df52b12226A8840971bd6C696e36270e`; agent: `0xd296ecc079ef15589cd8dd33853e08979a95828f` (the `HL_AGENT_KEY` address).
- Whitelist: BTC, ETH, SOL, BNB, HYPE. First live relay (2026-11-26): Index Strategy v1 weights on-chain (BNB 15% / BTC 50% / ETH 10% / HYPE 17.5% / SOL 7.5%), reported valuation 982.8 USDC.
- Deployment compile settings: Solidity 0.8, optimizer runs 200, EVM version paris (unoptimized bytecode exceeds HyperEVM's 3M small-block gas limit).

## Troubleshooting

| Symptom | Likely fix |
|---|---|
| `Missing HL_ACCOUNT_ADDRESS` | Run `setup_keys.py`; load `~/.hyperliquid/env` |
| Wallet does not exist / no balance | Fund the testnet wallet; confirm the testnet URL |
| Candle history short | Use a larger interval or a later start |
| Agent cannot trade | Re-approve the agent on the Hyperliquid testnet UI |
| Want to freeze sends | `python scripts/trade.py kill` or create `KILL_SWITCH` |
| Report styling broken | Open the HTML locally and confirm the file is complete |
| Weights do not sum to 1 | Check target construction and cash handling before simulating |

## Project layout

| Path | Role |
|---|---|
| `scripts/data.py` | Public candles and funding |
| `scripts/backtest.py` | Generic signal backtest engine |
| `scripts/strategies.py` | Signal strategy interface and registry |
| `scripts/portfolio_engine.py` | Portfolio/weight backtest engine (universe-agnostic) |
| `scripts/portfolio_report.py` | Self-contained portfolio HTML report |
| `scripts/risk.py` | Risk configuration and checks |
| `scripts/report_html.py` | Single-file HTML report page |
| `scripts/setup_keys.py` | Optional key storage |
| `scripts/trade.py` | Optional testnet orders |
| `scripts/relay_weights.py` | Relay strategy weights + valuation into the IndexFund contract |
| `contracts/` | IndexFund contract, reusable interface, local rehearsal |
| `results/` | Per-strategy backtest outputs |
| `templates/` | Report templates |
