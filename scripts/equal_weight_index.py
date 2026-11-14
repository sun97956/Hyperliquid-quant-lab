"""Equal Weight Index: BTC, ETH, BNB, HYPE, AAVE at 20% each, weekly rebalance.

Long-only, fully invested, fee 0.00035. Outputs to results/equal_weight_index/.
"""
import argparse
import json
import os
import sys

import pandas as pd

from portfolio_engine import (
    build_results,
    build_schedule,
    metric_table,
    prepare_market_data,
    save_equity_chart,
    simulate,
    strategy_output_dir,
    weights_frame,
)
from portfolio_report import write_portfolio_report

UNIVERSE = ["BTC", "ETH", "BNB", "HYPE", "AAVE"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--fee", type=float, default=0.00035)
    parser.add_argument("--capital", type=float, default=100.0)
    args = parser.parse_args()

    opens, closes, common, fetched_rows = prepare_market_data(args.start, symbols=UNIVERSE)
    if len(common) < 2:
        print(f"Not enough aligned history: {len(common)} rows")
        sys.exit(1)

    start_date = common[0]
    end_date = common[-1]
    used_bars = int(len(common))
    rows_per_symbol = {
        symbol: {"fetched": fetched_rows[symbol], "aligned": int(len(common)), "used": used_bars}
        for symbol in UNIVERSE
    }

    print("Strategy: Equal Weight Index")
    print(f"Universe: {', '.join(UNIVERSE)}")
    print(f"Requested start: {args.start}")
    print(f"Chosen backtest start: {start_date.date()}")
    print(f"End date: {end_date.date()}")
    print(f"Aligned dates: {len(common)}")
    for symbol in UNIVERSE:
        print(f"  {symbol}: fetched {fetched_rows[symbol]}, aligned {len(common)}, used {used_bars}")

    schedule = build_schedule(common, start_date)
    if not schedule:
        print("No rebalance dates available")
        sys.exit(1)

    equal_weights = {symbol: 1.0 / len(UNIVERSE) for symbol in UNIVERSE}
    strategy_targets = {
        execution: (decision, dict(equal_weights))
        for decision, execution in schedule
    }

    first_execution = schedule[0][1]
    first_weights = strategy_targets[first_execution][1]
    last_execution = schedule[-1][1]
    last_weights = strategy_targets[last_execution][1]

    strategy = simulate(
        opens, closes, strategy_targets, start_date, args.capital, args.fee, None
    )

    btc_only = {symbol: (1.0 if symbol == "BTC" else 0.0) for symbol in UNIVERSE}
    btc_targets = {first_execution: (schedule[0][0], btc_only)}
    buy_hold_targets = {first_execution: (schedule[0][0], dict(equal_weights))}
    benchmarks = {
        "BTC buy & hold": simulate(opens, closes, btc_targets, start_date, args.capital, args.fee, None),
        "Equal weight buy & hold": simulate(opens, closes, buy_hold_targets, start_date, args.capital, args.fee, None),
    }

    label = "Equal Weight Index"
    results = build_results(strategy, benchmarks, args.capital, label)
    outdir = strategy_output_dir(label)

    strategy_curve, strategy_trades, strategy_exposure = strategy
    weights_df = strategy_trades.copy()
    weights_df["target_sum"] = weights_df[[f"target_{s}" for s in UNIVERSE]].sum(axis=1)
    weights_df["target_CASH"] = 1.0 - weights_df["target_sum"]
    current_sum = weights_df[[f"current_{s}" for s in UNIVERSE]].sum(axis=1)
    weights_df["current_CASH"] = 1.0 - current_sum
    weights_columns = (
        ["decision_date", "exec_date", "action"]
        + [f"target_{s}" for s in UNIVERSE]
        + ["target_CASH", "target_sum"]
        + [f"current_{s}" for s in UNIVERSE]
        + ["current_CASH"]
    )
    weights_df = weights_df[weights_columns]
    for column in ("decision_date", "exec_date"):
        weights_df[column] = pd.to_datetime(weights_df[column]).dt.strftime("%Y-%m-%d")
    weights_df.to_csv(os.path.join(outdir, "weights.csv"), index=False)

    trades_df = strategy_trades[
        ["decision_date", "exec_date", "action", "turnover", "traded_notional", "fee", "equity_open"]
        + [f"target_{s}" for s in UNIVERSE]
        + [f"current_{s}" for s in UNIVERSE]
    ].copy()
    trades_df["target_CASH"] = 1.0 - trades_df[[f"target_{s}" for s in UNIVERSE]].sum(axis=1)
    for column in ("decision_date", "exec_date"):
        trades_df[column] = pd.to_datetime(trades_df[column]).dt.strftime("%Y-%m-%d")
    trades_df.to_csv(os.path.join(outdir, "trades.csv"), index=False)

    config = {
        "strategy": label,
        "universe": UNIVERSE,
        "weights": equal_weights,
        "rebalance_days": 7,
        "fee": args.fee,
        "capital": args.capital,
        "cash_allowed": False,
    }
    payload = {
        "generated_at": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M UTC"),
        "config": config,
        "data": {
            "start": str(start_date.date()),
            "end": str(end_date.date()),
            "common_bars": int(len(common)),
            "warmup_bars": 0,
            "rows_per_symbol": rows_per_symbol,
        },
        "strategy": results[label],
        "benchmarks": {
            "btc_buy_hold": results["BTC buy & hold"],
            "equal_weight_buy_hold": results["Equal weight buy & hold"],
        },
        "first_rebalance": {
            "date": str(first_execution.date()),
            "weights": first_weights,
            "cash": round(1.0 - sum(first_weights.values()), 6),
        },
        "last_rebalance": {
            "date": str(last_execution.date()),
            "weights": last_weights,
            "cash": round(1.0 - sum(last_weights.values()), 6),
        },
    }
    with open(os.path.join(outdir, "metrics.json"), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    series = [
        (label, strategy_curve),
        ("BTC buy & hold", benchmarks["BTC buy & hold"][0]),
        ("Equal weight buy & hold", benchmarks["Equal weight buy & hold"][0]),
    ]
    save_equity_chart(
        os.path.join(outdir, "equity.png"),
        series,
        f"{label} vs benchmarks (start = 100)",
        strategy_curve,
    )

    title = label
    meta = (
        f"{label} · BTC/ETH/BNB/HYPE/AAVE equal weight · "
        f"weekly rebalance · fee {args.fee}"
    )
    config_items = f"""<li>Universe: {", ".join(UNIVERSE)} (daily Hyperliquid mainnet candles)</li>
<li>Weights: 20% each at every rebalance.</li>
<li>Rebalance: every 7 calendar days, decided at close and executed next open.</li>
<li>Long-only, fully invested, no leverage, no shorting.</li>
<li>Fees: {args.fee} on traded notional at each execution.</li>"""
    method_note = (
        "The equal-weight strategy rebalances weekly back to 20% per asset. "
        "The equal-weight buy & hold benchmark buys once and never rebalances. "
        "The BTC benchmark holds 100% BTC. All portfolios use the same dates and fee; "
        "metrics annualize daily returns with 365 days."
    )
    write_portfolio_report(
        os.path.join(outdir, "report.html"),
        title=title,
        meta=meta,
        strategy_label=label,
        results=results,
        strategy_curve=strategy_curve,
        benchmarks={name: value[0] for name, value in benchmarks.items()},
        exposure=strategy_exposure,
        trades=trades_df,
        first_weights=first_weights,
        last_weights=last_weights,
        rows_per_symbol=rows_per_symbol,
        config_items=config_items,
        method_note=method_note,
        data_start=str(start_date.date()),
        data_end=str(end_date.date()),
        warmup_bars=0,
        assets=UNIVERSE,
        config=config,
    )

    print("\nFirst rebalance:", schedule[0][0].date(), "decision ->", first_execution.date(), "execution")
    print(weights_frame(first_weights, assets=UNIVERSE).to_string(index=False, float_format=lambda value: f"{value:.2f}"))
    print("\nLast rebalance:", schedule[-1][0].date(), "decision ->", last_execution.date(), "execution")
    print(weights_frame(last_weights, assets=UNIVERSE).to_string(index=False, float_format=lambda value: f"{value:.2f}"))

    table, labels = metric_table(results)
    table.columns = labels
    print("\nFinal metrics")
    print(table.to_string())
    print(f"\nResults written to: {outdir}")


if __name__ == "__main__":
    main()
