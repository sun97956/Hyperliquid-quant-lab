"""Index Strategy v2: BTC trend regime, alt trend filter, inverse-vol sleeve,
portfolio volatility target, and cash.

Weekly rebalance, long-only, no leverage, fee 0.00035. Outputs index_v2_* files.
"""
import argparse
import json
import math
import os
import sys

import numpy as np
import pandas as pd

from portfolio_engine import (
    BARS_PER_YEAR,
    WARMUP_BARS,
    allocate_bounded,
    build_results,
    build_schedule,
    metric_table,
    prepare_market_data,
    run_benchmarks,
    save_equity_chart,
    simulate,
    strategy_output_dir,
    weights_frame,
)
from portfolio_report import write_portfolio_report

UNIVERSE = ["BTC", "ETH", "SOL", "BNB", "HYPE"]
ALTS = ["ETH", "SOL", "BNB", "HYPE"]


def target_weights_v2(day, closes, ma_fast, ma_slow, alt_ma, ret60, vol30, alt_score, daily_returns, target_vol):
    btc_close = float(closes.at[day, "BTC"])
    btc_fast = float(ma_fast.at[day, "BTC"])
    btc_slow = float(ma_slow.at[day, "BTC"])
    if btc_close > btc_slow and btc_fast > btc_slow:
        btc_weight = 0.50
    elif btc_close > btc_slow:
        btc_weight = 0.25
    else:
        btc_weight = 0.00

    eligible = []
    for symbol in ALTS:
        alt_close = float(closes.at[day, symbol])
        alt_ma_value = float(alt_ma.at[day, symbol])
        alt_ret60 = float(ret60.at[day, symbol])
        if np.isnan(alt_ma_value) or np.isnan(alt_ret60):
            continue
        if alt_close > alt_ma_value and alt_ret60 > 0:
            eligible.append(symbol)

    alt_budget = 0.0 if not eligible else 0.50
    if eligible and btc_weight < 0.50 and len(eligible) >= 3:
        avg_ret60 = float(np.mean([float(ret60.at[day, symbol]) for symbol in eligible]))
        if avg_ret60 > 0:
            alt_budget = min(0.70, 0.50 + (0.50 - btc_weight))

    alt_weights = {}
    if eligible and alt_budget > 0:
        inverse_vol = {symbol: 1.0 / max(float(vol30.at[day, symbol]), 1e-9) for symbol in eligible}
        alt_weights = allocate_bounded(inverse_vol, alt_budget, 0.0, 0.20)
        if len(eligible) == 4:
            scores = alt_score.loc[day, ALTS]
            if not scores.isna().any():
                ranked = sorted(ALTS, key=lambda symbol: (-float(scores[symbol]), symbol))
                tilts = {ranked[0]: 0.05, ranked[1]: 0.025, ranked[2]: -0.025, ranked[3]: -0.05}
                scale = alt_budget / 0.50
                for symbol in ALTS:
                    alt_weights[symbol] = alt_weights.get(symbol, 0.0) + tilts[symbol] * scale
                alt_weights = allocate_bounded(alt_weights, alt_budget, 0.0, 0.20)

    raw = {"BTC": btc_weight}
    for symbol in ALTS:
        raw[symbol] = float(alt_weights.get(symbol, 0.0))
    raw_sum = sum(raw.values())
    assert raw_sum <= 1.0 + 1e-9, f"raw weights exceed 100%: {raw_sum}"

    raw_vector = np.array([raw[symbol] for symbol in UNIVERSE], dtype=float)
    covariance = daily_returns.loc[:day].tail(20).cov().reindex(index=UNIVERSE, columns=UNIVERSE).to_numpy(dtype=float)
    covariance = np.nan_to_num(covariance)
    portfolio_vol = math.sqrt(max(float(raw_vector @ covariance @ raw_vector), 0.0) * BARS_PER_YEAR)
    scale = 1.0 if portfolio_vol <= 0 else min(1.0, target_vol / portfolio_vol)

    weights = {symbol: float(raw[symbol] * scale) for symbol in UNIVERSE}
    total = sum(weights.values())
    assert -1e-12 <= total <= 1.0 + 1e-6, f"weights exceed 100%: {total}"
    return weights


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--fee", type=float, default=0.00035)
    parser.add_argument("--capital", type=float, default=100.0)
    parser.add_argument("--btc-ma", type=int, default=100)
    parser.add_argument("--alt-ma", type=int, default=100)
    parser.add_argument("--target-vol", type=float, default=0.30)
    args = parser.parse_args()

    opens, closes, common, fetched_rows = prepare_market_data(args.start, symbols=UNIVERSE)
    warmup = max(WARMUP_BARS, args.btc_ma, args.alt_ma)
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

    print("Strategy: Index Strategy v2")
    print(f"Requested start: {args.start}")
    print(f"Chosen backtest start: {start_date.date()}")
    print(f"End date: {end_date.date()}")
    print(f"Aligned dates: {len(common)}")
    for symbol in UNIVERSE:
        print(f"  {symbol}: fetched {fetched_rows[symbol]}, aligned {len(common)}, used {used_bars}")

    daily_returns = closes.pct_change()
    volatility_30 = daily_returns.rolling(30).std().replace(0, np.nan)
    score_30 = closes.pct_change(30) / volatility_30
    ma_fast = closes.rolling(50).mean()
    ma_slow = closes.rolling(args.btc_ma).mean()
    alt_ma = closes.rolling(args.alt_ma).mean()
    ret_60 = closes.pct_change(60)
    volatility_60 = daily_returns.rolling(60).std().replace(0, np.nan)
    alt_score = 0.5 * score_30[ALTS] + 0.5 * (ret_60[ALTS] / volatility_60[ALTS])

    schedule = build_schedule(common, start_date)
    if not schedule:
        print("No rebalance dates available")
        sys.exit(1)

    strategy_targets = {}
    for decision, execution in schedule:
        weights = target_weights_v2(
            decision, closes, ma_fast, ma_slow, alt_ma, ret_60,
            volatility_30, alt_score, daily_returns, args.target_vol,
        )
        assert decision < execution, "decision date must precede execution"
        strategy_targets[execution] = (decision, weights)

    first_execution = schedule[0][1]
    last_execution = schedule[-1][1]
    first_weights = strategy_targets[first_execution][1]
    last_weights = strategy_targets[last_execution][1]

    strategy = simulate(
        opens, closes, strategy_targets, start_date, args.capital, args.fee, 0.015
    )
    benchmarks = run_benchmarks(
        opens, closes, schedule, start_date, args.capital, args.fee,
        single_asset="BTC", fixed_label="Fixed 50/12.5 weekly",
    )
    label = "Index Strategy v2"
    results = build_results(strategy, benchmarks, args.capital, label)

    outdir = strategy_output_dir("Index Strategy v2")
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
        "version": "v2",
        "UNIVERSE": UNIVERSE,
        "alts": ALTS,
        "start_requested": args.start,
        "fee": args.fee,
        "capital": args.capital,
        "warmup_bars": warmup,
        "rebalance_days": 7,
        "no_trade_threshold": 0.015,
        "btc_ma": args.btc_ma,
        "alt_ma": args.alt_ma,
        "target_vol": args.target_vol,
        "btc_regime_weights": [0.50, 0.25, 0.0],
        "alt_base": "inverse_vol",
        "alt_budget_base": 0.50,
        "alt_budget_max": 0.70,
        "alt_cap": 0.20,
        "alt_score": "0.5*30d + 0.5*60d risk-adjusted",
        "cash_allowed": True,
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
        ("Index Strategy v2", strategy_curve),
        ("BTC buy & hold", benchmarks["BTC buy & hold"][0]),
        ("Equal weight weekly", benchmarks["Equal weight weekly"][0]),
        ("Fixed 50/12.5 weekly", benchmarks["Fixed 50/12.5 weekly"][0]),
    ]
    save_equity_chart(
        os.path.join(outdir, "equity.png"),
        series,
        "Index Strategy v2 vs benchmarks (start = 100)",
        strategy_curve,
    )

    title = "Index Strategy v2"
    meta = (
        f"Index Strategy v2 · BTC regime 50/25/0 · alt trend filter · "
        f"{args.target_vol * 100:.0f}% vol target · fee {args.fee}"
    )
    config_items = f"""<li>Universe: {", ".join(UNIVERSE)} (daily Hyperliquid mainnet candles)</li>
<li>BTC regime: 50% if close &gt; MA{args.btc_ma} and MA50 &gt; MA{args.btc_ma}; 25% if only close &gt; MA{args.btc_ma}; 0% below.</li>
<li>Alt eligibility: close &gt; MA{args.alt_ma} and 60-day return &gt; 0; otherwise excluded.</li>
<li>Alt score: 0.5 &times; 30-day return/vol + 0.5 &times; 60-day return/vol.</li>
<li>Alt base: inverse-vol; rank tilts &plusmn;5/2.5pp; 20% per-alt cap; sleeve 50-70%, remainder cash.</li>
<li>Portfolio vol target: {args.target_vol * 100:.0f}% annualized; risky exposure scaled down when needed.</li>
<li>Rebalance: every 7 calendar days, scored at close and executed next open.</li>
<li>No-trade rule: skip only if every asset differs by less than 1.5 percentage points.</li>
<li>Long-only, no leverage; cash is allowed.</li>"""
    method_note = (
        "All portfolios are charged 0.00035 on traded notional at each execution. "
        "Index Strategy v2 may hold cash and scales exposure to its volatility target. "
        "Benchmarks use the same dates and fees; no funding or slippage is included."
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
