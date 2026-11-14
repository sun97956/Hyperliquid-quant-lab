"""Shared long-only portfolio backtest engine used by the index and tactical strategy scripts."""
import base64
import math
import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data import fetch_candles

WARMUP_BARS = 60
BARS_PER_YEAR = 365
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_ROOT = os.path.join(PROJECT_ROOT, "results")


def strategy_slug(name):
    slug = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")
    return slug or "strategy"


def strategy_output_dir(name):
    path = os.path.join(RESULTS_ROOT, strategy_slug(name))
    os.makedirs(path, exist_ok=True)
    return path


def as_utc_day(index):
    idx = pd.DatetimeIndex(index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")
    return idx.normalize()


def load_universe(start, symbols):
    symbols = list(symbols)
    data = {}
    for symbol in symbols:
        try:
            frame = fetch_candles(symbol, "futures", "1d", start, verbose=True)
        except Exception as exc:
            print(f"UNAVAILABLE: {symbol} ({exc})")
            sys.exit(1)
        if frame is None or frame.empty or not {"open", "close"}.issubset(frame.columns):
            print(f"UNAVAILABLE: {symbol}")
            sys.exit(1)
        frame = frame.copy()
        frame.index = as_utc_day(frame.index)
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
        data[symbol] = frame
    return data


def prepare_market_data(start, symbols):
    symbols = list(symbols)
    data = load_universe(start, symbols)
    fetched_rows = {symbol: int(len(frame)) for symbol, frame in data.items()}

    common = None
    for frame in data.values():
        common = frame.index if common is None else common.intersection(frame.index)
    common = pd.DatetimeIndex(common).sort_values()

    opens = pd.DataFrame({symbol: data[symbol].open.reindex(common) for symbol in symbols})
    closes = pd.DataFrame({symbol: data[symbol].close.reindex(common) for symbol in symbols})
    valid = opens.notna().all(axis=1) & closes.notna().all(axis=1)
    common = common[valid]
    opens = opens.loc[common]
    closes = closes.loc[common]
    return opens, closes, common, fetched_rows


def rescale_bounded(weights, target_sum, lower, upper):
    w = dict(weights)
    for _ in range(1000):
        total = sum(w.values())
        if abs(total - target_sum) <= 1e-10:
            break
        factor = target_sum / total
        w = {k: min(upper, max(lower, v * factor)) for k, v in w.items()}
    return w


def allocate_bounded(weights, target_sum, lower=0.0, upper=0.20):
    w = {k: max(lower, float(v)) for k, v in weights.items()}
    total = sum(w.values())
    if total <= 0:
        w = {k: 1.0 for k in w}
        total = float(len(w))
    w = {k: v / total * target_sum for k, v in w.items()}
    for _ in range(1000):
        w = {k: min(upper, max(lower, v)) for k, v in w.items()}
        total = sum(w.values())
        gap = target_sum - total
        if abs(gap) <= 1e-12:
            break
        free = [
            k for k, v in w.items()
            if (gap > 0 and v < upper - 1e-12) or (gap < 0 and v > lower + 1e-12)
        ]
        if not free:
            break
        room = sum((upper - w[k]) if gap > 0 else (w[k] - lower) for k in free)
        if room <= 1e-15:
            break
        for k in free:
            cap = (upper - w[k]) if gap > 0 else (w[k] - lower)
            w[k] += gap * (cap / room)
    return w


def build_schedule(index, start_date):
    schedule = []
    seen_decisions = set()
    seen_executions = set()
    k = 0
    while True:
        target = start_date + pd.Timedelta(days=7 * k)
        if target > index[-1]:
            break
        position = int(index.searchsorted(target, side="left"))
        if position >= len(index) - 1:
            break
        decision = index[position]
        execution = index[position + 1]
        if decision not in seen_decisions and execution not in seen_executions:
            schedule.append((decision, execution))
            seen_decisions.add(decision)
            seen_executions.add(execution)
        k += 1
        if k > 10000:
            break
    return schedule


def simulate(opens, closes, exec_targets, start_date, capital, fee, no_trade_threshold):
    index = closes.index
    start_pos = int(index.get_loc(start_date))
    names = list(closes.columns)
    shares = np.zeros(len(names))
    cash = float(capital)
    curve = pd.Series(index=index[start_pos:], dtype=float)
    curve.iloc[0] = cash
    exposure = pd.Series(index=index[start_pos:], dtype=float)
    exposure.iloc[0] = 0.0
    trades = []

    for i in range(start_pos + 1, len(index)):
        day = index[i]
        if day in exec_targets:
            decision_day, target = exec_targets[day]
            target_vector = np.array([target[symbol] for symbol in names], dtype=float)
            target_sum = float(target_vector.sum())
            assert -1e-9 <= target_sum <= 1.0 + 1e-6, "target weights must be long-only and at most 100%"
            decision_prices = closes.loc[decision_day, names].to_numpy(dtype=float)
            equity_decision = cash + float(shares @ decision_prices)
            current_decision = shares * decision_prices / equity_decision if equity_decision > 0 else np.zeros(len(names))
            differences = np.abs(target_vector - current_decision)
            skip = no_trade_threshold is not None and float(differences.max()) < no_trade_threshold

            record = {
                "decision_date": decision_day,
                "exec_date": day,
                "action": "skip" if skip else "rebalance",
                "turnover": 0.0,
                "traded_notional": 0.0,
                "fee": 0.0,
                "equity_open": float(equity_decision),
            }
            for j, symbol in enumerate(names):
                record[f"target_{symbol}"] = float(target[symbol])
                record[f"current_{symbol}"] = float(current_decision[j])

            if not skip:
                open_prices = opens.loc[day, names].to_numpy(dtype=float)
                equity_open = cash + float(shares @ open_prices)
                current_open = shares * open_prices / equity_open if equity_open > 0 else np.zeros(len(names))
                fee_paid = fee * float(np.abs(target_vector * equity_open - shares * open_prices).sum())
                for _ in range(5):
                    investable = equity_open - fee_paid
                    target_value = target_vector * investable
                    traded = float(np.abs(target_value - shares * open_prices).sum())
                    new_fee = fee * traded
                    if abs(new_fee - fee_paid) <= 1e-12:
                        fee_paid = new_fee
                        break
                    fee_paid = new_fee
                investable = equity_open - fee_paid
                target_value = target_vector * investable
                traded = float(np.abs(target_value - shares * open_prices).sum())
                fee_paid = fee * traded
                shares = target_value / open_prices
                cash = investable - float(target_value.sum())
                post_weights = shares * open_prices / investable
                assert abs(float(post_weights.sum()) - target_sum) <= 1e-6, "post-trade weights do not match target"
                assert float(post_weights.sum()) <= 1.0 + 1e-6, "post-trade weights exceed 100%"
                assert cash >= -1e-9, "post-trade cash is negative"
                assert abs(cash - investable * (1.0 - target_sum)) <= 1e-9, "cash accounting mismatch"
                record["turnover"] = traded / equity_open if equity_open > 0 else 0.0
                record["traded_notional"] = traded
                record["fee"] = fee_paid
                record["equity_open"] = equity_open
                for j, symbol in enumerate(names):
                    record[f"current_{symbol}"] = float(current_open[j])

            trades.append(record)

        close_prices = closes.loc[day, names].to_numpy(dtype=float)
        equity = cash + float(shares @ close_prices)
        curve.loc[day] = equity
        exposure.loc[day] = float(shares @ close_prices / equity) if equity > 0 else 0.0

    return curve, pd.DataFrame(trades), exposure


def metrics(curve, trades, capital, exposure=None):
    returns = curve.pct_change().dropna()
    sd = float(returns.std())
    if not np.isfinite(sd) or sd == 0:
        sd = 1e-12
    rebalances = int((trades["action"] == "rebalance").sum()) if len(trades) else 0
    skipped = int((trades["action"] == "skip").sum()) if len(trades) else 0
    fees = float(trades["fee"].sum()) if len(trades) else 0.0
    turnover = float(trades["traded_notional"].sum()) if len(trades) else 0.0
    if exposure is not None and len(exposure):
        avg_exposure = round(float(exposure.mean()) * 100, 2)
        min_exposure = round(float(exposure.min()) * 100, 2)
        max_exposure = round(float(exposure.max()) * 100, 2)
    else:
        avg_exposure = min_exposure = max_exposure = None
    return {
        "start": str(curve.index[0].date()),
        "end": str(curve.index[-1].date()),
        "bars": int(len(curve)),
        "total_return_pct": round((float(curve.iloc[-1]) / float(curve.iloc[0]) - 1) * 100, 2),
        "annual_vol_pct": round(sd * math.sqrt(BARS_PER_YEAR) * 100, 2),
        "sharpe": round(float(returns.mean()) / sd * math.sqrt(BARS_PER_YEAR), 2),
        "max_drawdown_pct": round(float((curve / curve.cummax() - 1).min()) * 100, 2),
        "avg_exposure_pct": avg_exposure,
        "min_exposure_pct": min_exposure,
        "max_exposure_pct": max_exposure,
        "rebalances": rebalances,
        "skipped_rebalances": skipped,
        "total_fees": round(fees, 4),
        "total_turnover": round(turnover, 2),
        "fee_pct_of_initial": round(fees / float(capital) * 100, 3),
    }


def metric_table(results):
    columns = [
        ("total_return_pct", "Total return %"),
        ("annual_vol_pct", "Annual vol %"),
        ("sharpe", "Sharpe"),
        ("max_drawdown_pct", "Max drawdown %"),
        ("avg_exposure_pct", "Avg exposure %"),
        ("rebalances", "Rebalances"),
        ("skipped_rebalances", "Skipped"),
        ("total_fees", "Fees"),
        ("total_turnover", "Turnover"),
    ]
    table = pd.DataFrame(results).T
    return table[[key for key, _ in columns]], [label for _, label in columns]


def weights_frame(weights, assets=None):
    asset_list = list(assets) if assets is not None else list(weights.keys())
    labels = asset_list + ["Cash"]
    values = [round(weights[s] * 100, 2) for s in asset_list]
    values.append(round((1.0 - sum(weights.values())) * 100, 2))
    return pd.DataFrame({"Asset": labels, "Weight %": values})


def save_equity_chart(path, series, title, strategy_curve):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    for label, curve in series:
        axes[0].plot(curve.index, curve / curve.iloc[0] * 100, label=label, linewidth=1.4)
    axes[0].set_title(title)
    axes[0].set_ylabel("Equity (start = 100)")
    axes[0].legend(fontsize=9)
    drawdown = strategy_curve / strategy_curve.cummax() - 1
    axes[1].fill_between(drawdown.index, drawdown * 100, 0, color="tab:red", alpha=0.4)
    axes[1].set_ylabel("Strategy drawdown %")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    with open(path, "rb") as handle:
        return base64.b64encode(handle.read()).decode("ascii")


def run_benchmarks(
    opens,
    closes,
    schedule,
    start_date,
    capital,
    fee,
    single_asset=None,
    fixed_weights=None,
    fixed_label="Fixed weights weekly",
):
    """Build benchmarks from the actual universe instead of a fixed symbol list.

    single_asset: asset held in the buy-and-hold benchmark (defaults to BTC when present).
    fixed_weights: explicit weights for the fixed-weight benchmark; when omitted the
        single asset gets 50% and the remaining universe shares the other 50%.
    """
    symbols = list(closes.columns)
    if not symbols:
        return {}
    single_asset = single_asset or ("BTC" if "BTC" in symbols else symbols[0])
    single_weights = {symbol: 1.0 if symbol == single_asset else 0.0 for symbol in symbols}
    equal_weights = {symbol: 1.0 / len(symbols) for symbol in symbols}
    if fixed_weights is None:
        rest = [symbol for symbol in symbols if symbol != single_asset]
        fixed_weights = {single_asset: 0.5 if rest else 1.0}
        for symbol in rest:
            fixed_weights[symbol] = 0.5 / len(rest)
    else:
        fixed_weights = {symbol: float(fixed_weights.get(symbol, 0.0)) for symbol in symbols}

    first_execution = schedule[0][1]
    single_targets = {first_execution: (schedule[0][0], single_weights)}
    equal_targets = {execution: (decision, equal_weights) for decision, execution in schedule}
    fixed_targets = {execution: (decision, fixed_weights) for decision, execution in schedule}

    return {
        f"{single_asset} buy & hold": simulate(opens, closes, single_targets, start_date, capital, fee, None),
        "Equal weight weekly": simulate(opens, closes, equal_targets, start_date, capital, fee, None),
        fixed_label: simulate(opens, closes, fixed_targets, start_date, capital, fee, None),
    }


def build_results(strategy, benchmarks, capital, strategy_label="Strategy"):
    curve, trades, exposure = strategy
    results = {strategy_label: metrics(curve, trades, capital, exposure)}
    for name, (bench_curve, bench_trades, bench_exposure) in benchmarks.items():
        results[name] = metrics(bench_curve, bench_trades, capital, bench_exposure)
    return results
