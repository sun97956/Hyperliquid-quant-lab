"""Index Strategy v1: fixed 50% BTC plus a dynamic 50% alt sleeve, fully invested.

Weekly rebalance, long-only, no leverage, fee 0.00035. Outputs index_v1_* files.
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

from portfolio_engine import (
    WARMUP_BARS,
    build_results,
    build_schedule,
    metric_table,
    prepare_market_data,
    rescale_bounded,
    run_benchmarks,
    save_equity_chart,
    simulate,
    strategy_output_dir,
    weights_frame,
)
from portfolio_report import write_portfolio_report

UNIVERSE = ["BTC", "ETH", "SOL", "BNB", "HYPE"]
ALTS = ["ETH", "SOL", "BNB", "HYPE"]


def target_weights(score_row):
    scores = score_row.reindex(ALTS)
    if scores.isna().any():
        raise ValueError(f"missing alt score: {scores.to_dict()}")
    ranked = sorted(ALTS, key=lambda symbol: (-float(scores[symbol]), symbol))
    tilts = {ranked[0]: 0.05, ranked[1]: 0.025, ranked[2]: -0.025, ranked[3]: -0.05}
    alts = {symbol: 0.125 + tilts[symbol] for symbol in ALTS}
    alts = {symbol: min(0.20, max(0.05, weight)) for symbol, weight in alts.items()}
    alts = rescale_bounded(alts, 0.50, 0.05, 0.20)

    weights = {"BTC": 0.50}
    weights.update(alts)
    total = sum(weights.values())
    assert abs(total - 1.0) <= 1e-6, f"weights sum to {total}"
    assert abs(weights["BTC"] - 0.50) <= 1e-9, "BTC weight changed"
    assert abs(sum(weights[s] for s in ALTS) - 0.50) <= 1e-6, "alt sleeve does not sum to 50%"
    for symbol in ALTS:
        assert 0.05 - 1e-9 <= weights[symbol] <= 0.20 + 1e-9, f"{symbol} outside alt bounds"
    for symbol in UNIVERSE:
        assert -1e-12 <= weights[symbol] <= 1.0 + 1e-12, f"{symbol} outside [0, 1]"
    return weights


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--fee", type=float, default=0.00035)
    parser.add_argument("--capital", type=float, default=100.0)
    args = parser.parse_args()

    opens, closes, common, fetched_rows = prepare_market_data(args.start, symbols=UNIVERSE)
    warmup = WARMUP_BARS
    if len(common) <= warmup + 1:
        print(f"Not enough aligned history: {len(common)} rows")
        sys.exit(1)

    start_date = common[warmup]
    end_date = common[-1]
    used_bars = int(len(common) - warmup)
    rows_per_symbol = {
        symbol: {"fetched": fetched_rows[symbol], "aligned": int(len(common)), "used": used_bars}
        for symbol in UNIVERSE
    }

    print("Strategy: Index Strategy v1")
    print(f"Requested start: {args.start}")
    print(f"Chosen backtest start: {start_date.date()}")
    print(f"End date: {end_date.date()}")
    print(f"Aligned dates: {len(common)}")
    for symbol in UNIVERSE:
        print(f"  {symbol}: fetched {fetched_rows[symbol]}, aligned {len(common)}, used {used_bars}")

    daily_returns = closes.pct_change()
    volatility_30 = daily_returns.rolling(30).std().replace(0, np.nan)
    score = (closes.pct_change(30) / volatility_30)[ALTS]

    schedule = build_schedule(common, start_date)
    if not schedule:
        print("No rebalance dates available")
        sys.exit(1)

    strategy_targets = {}
    for decision, execution in schedule:
        weights = target_weights(score.loc[decision])
        assert decision < execution, "decision date must precede execution"
        strategy_targets[execution] = (decision, weights)

    first_execution = schedule[0][1]
    last_execution = schedule[-1][1]
    first_weights = strategy_targets[first_execution][1]
    last_weights = strategy_targets[last_execution][1]

    strategy = simulate(
        opens, closes, strategy_targets, start_date, args.capital, args.fee, 0.03
    )
    benchmarks = run_benchmarks(
        opens, closes, schedule, start_date, args.capital, args.fee,
        single_asset="BTC", fixed_label="Fixed 50/12.5 weekly",
    )
    label = "Index Strategy v1"
    results = build_results(strategy, benchmarks, args.capital, label)

    outdir = strategy_output_dir("Index Strategy v1")
    strategy_curve, strategy_trades, _ = strategy
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
        "strategy": "Index Strategy",
        "version": "v1",
        "UNIVERSE": UNIVERSE,
        "alts": ALTS,
        "start_requested": args.start,
        "fee": args.fee,
        "capital": args.capital,
        "warmup_bars": warmup,
        "rebalance_days": 7,
        "no_trade_threshold": 0.03,
        "btc_weight": 0.50,
        "alt_base_weight": 0.125,
        "alt_tilts": [0.05, 0.025, -0.025, -0.05],
        "alt_bounds": [0.05, 0.20],
        "score_window_days": 30,
    }
    payload = {
        "generated_at": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M UTC"),
        "config": config,
        "data": {
            "start": str(start_date.date()),
            "end": str(end_date.date()),
            "common_bars": int(len(common)),
            "warmup_bars": warmup,
            "rows_per_symbol": rows_per_symbol,
        },
        "strategy": results[label],
        "benchmarks": {
            "btc_buy_hold": results["BTC buy & hold"],
            "equal_weight_weekly": results["Equal weight weekly"],
            "fixed_50_12_5_weekly": results["Fixed 50/12.5 weekly"],
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
        ("Index Strategy v1", strategy_curve),
        ("BTC buy & hold", benchmarks["BTC buy & hold"][0]),
        ("Equal weight weekly", benchmarks["Equal weight weekly"][0]),
        ("Fixed 50/12.5 weekly", benchmarks["Fixed 50/12.5 weekly"][0]),
    ]
    save_equity_chart(
        os.path.join(outdir, "equity.png"),
        series,
        "Index Strategy v1 vs benchmarks (start = 100)",
        strategy_curve,
    )

    title = "Index Strategy v1"
    meta = (
        f"Index Strategy v1 · BTC 50% fixed + dynamic 50% alt sleeve · "
        f"weekly rebalance · fee {args.fee}"
    )
    config_items = f"""<li>Universe: {", ".join(UNIVERSE)} (daily Hyperliquid mainnet candles)</li>
<li>BTC target: 50% fixed. Alt sleeve: 50%, base 12.5% each.</li>
<li>Alt score: 30-day return / 30-day volatility. Tilts: +5.0, +2.5, -2.5, -5.0 percentage points.</li>
<li>Alt clamp: 5% to 20%; rescale the sleeve to exactly 50%.</li>
<li>Rebalance: every 7 calendar days, scored at close and executed next open.</li>
<li>No-trade rule: skip if every asset differs from target by less than 3 percentage points.</li>
<li>No leverage, no shorting, fully invested after the first rebalance.</li>"""
    method_note = (
        "All portfolios are charged 0.00035 on traded notional at each execution. Benchmarks use the same date range and execution schedule. "
        "The 100% BTC benchmark buys once; the equal-weight and fixed-weight benchmarks rebalance weekly without the no-trade rule. "
        "Metrics use daily returns annualized with 365 days."
    )
    write_portfolio_report(
        os.path.join(outdir, "report.html"),
        title=title,
        meta=meta,
        strategy_label=label,
        results=results,
        strategy_curve=strategy_curve,
        benchmarks={name: value[0] for name, value in benchmarks.items()},
        exposure=strategy[2],
        trades=trades_df,
        first_weights=first_weights,
        last_weights=last_weights,
        rows_per_symbol=rows_per_symbol,
        config_items=config_items,
        method_note=method_note,
        data_start=str(start_date.date()),
        data_end=str(end_date.date()),
        warmup_bars=warmup,
        assets=UNIVERSE,
        config=config,
    )

    print("\nFirst rebalance:", schedule[0][0].date(), "decision ->", first_execution.date(), "execution")
    print(weights_frame(first_weights).to_string(index=False, float_format=lambda value: f"{value:.2f}"))
    print("\nLast rebalance:", schedule[-1][0].date(), "decision ->", last_execution.date(), "execution")
    print(weights_frame(last_weights).to_string(index=False, float_format=lambda value: f"{value:.2f}"))

    table, labels = metric_table(results)
    table.columns = labels
    print("\nFinal metrics")
    print(table.to_string())
    print(f"\nResults written to: {outdir}")


if __name__ == "__main__":
    main()
