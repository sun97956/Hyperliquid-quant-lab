"""Relay strategy weights into the on-chain index fund (HyperEVM testnet).

Reads the latest target weights from a strategy weights.csv (the
target_<COIN> columns written by the portfolio strategy scripts), decides
whether a rebalance is due, and — with --send — writes the weights into
the IndexFund contract and reports the trading account's total value so
the fund's NAV stays current.

Rebalance decision (volatility-aware, professor feedback #2):
  - Realized BTC volatility is estimated from the data.py candle cache
    (data/hl_*_BTC_1d.csv) when present.
  - High-volatility regime (annualized vol above --vol-threshold):
    always push the new weights and raise the fund's redemption fee to
    --fee-high-bps (the slippage guardrail).
  - Normal regime: push only if the largest single-coin weight change
    versus the on-chain weights is at least --min-change (avoids churn),
    and set the redemption fee back to --fee-base-bps.

Keys: the same ~/.hyperliquid/env as trade.py. The IndexFund contract's
`agent` address must be the address of HL_AGENT_KEY, because setWeights /
reportTotalAssets / setRedemptionFee are agent-only. The key is loaded
from the env file and never printed.

Default is a dry run: the plan is printed, nothing is sent. --send
transmits the transactions (no extra prompt — review the dry run first).

Examples:
  python scripts/relay_weights.py --weights results/index_v2/weights.csv --fund 0xFUND
  python scripts/relay_weights.py --weights results/index_v2/weights.csv --fund 0xFUND --send
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys

import pandas as pd

ENV_PATH = os.path.expanduser("~/.hyperliquid/env")
DEFAULT_RPC = "https://rpc.hyperliquid-testnet.xyz/evm"

FUND_ABI = json.loads("""
[
  {"type":"function","name":"setWeights","stateMutability":"nonpayable",
   "inputs":[{"name":"coins","type":"string[]"},{"name":"weightsBps","type":"uint256[]"}],"outputs":[]},
  {"type":"function","name":"getWeights","stateMutability":"view",
   "inputs":[],"outputs":[{"name":"coins","type":"string[]"},{"name":"weightsBps","type":"uint256[]"},{"name":"","type":"uint256"}]},
  {"type":"function","name":"reportTotalAssets","stateMutability":"nonpayable",
   "inputs":[{"name":"totalValue","type":"uint256"}],"outputs":[]},
  {"type":"function","name":"setRedemptionFee","stateMutability":"nonpayable",
   "inputs":[{"name":"newFeeBps","type":"uint256"}],"outputs":[]},
  {"type":"function","name":"redemptionFeeBps","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"uint256"}]},
  {"type":"function","name":"breakerTripped","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"bool"}]},
  {"type":"function","name":"navPerShare","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"uint256"}]}
]
""")


def load_env(path: str = ENV_PATH) -> None:
    """Load ~/.hyperliquid/env into os.environ (same pattern as trade.py)."""
    if not os.path.exists(path):
        return
    for line in open(path):
        m = re.match(r"\s*(?:export\s+)?(HL_[A-Z0-9_]+)=(.*)", line.strip())
        if m:
            os.environ.setdefault(m.group(1), m.group(2).strip().strip('"').strip("'"))


def read_target_weights(weights_csv: str) -> dict[str, float]:
    """Latest target weights from a strategy weights.csv.

    Uses the target_<COIN> columns of the last row; target_CASH and
    target_sum are not coins and are skipped. Zero weights are dropped
    (any remainder below 100% is cash, which the contract allows).
    """
    df = pd.read_csv(weights_csv)
    target_cols = [c for c in df.columns
                   if c.startswith("target_") and c not in ("target_CASH", "target_sum")]
    if not target_cols:
        raise SystemExit(f"No target_<COIN> columns in {weights_csv}")
    last = df.iloc[-1]
    weights: dict[str, float] = {}
    for col in target_cols:
        w = float(last[col])
        if w > 0:
            weights[col[len("target_"):]] = w
    total = sum(weights.values())
    if total > 1.0 + 1e-6:
        raise SystemExit(f"Target weights sum to {total:.4f} > 1 — refusing to relay")
    return weights


def realized_vol_btc(data_dir: str, days: int = 7) -> float | None:
    """Annualized realized volatility of BTC from the data.py candle cache.

    Returns None when no cache file is available (caller treats that as
    the normal regime and says so).
    """
    candidates = sorted(glob.glob(os.path.join(data_dir, "hl_*_BTC_1d.csv")))
    if not candidates:
        return None
    df = pd.read_csv(candidates[-1])
    closes = df["close"].astype(float).tail(days + 1)
    if len(closes) < days + 1:
        return None
    rets = [math.log(b / a) for a, b in zip(closes.iloc[:-1], closes.iloc[1:])]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(365)


def trading_account_value_usdc() -> float | None:
    """Total value of the Hyperliquid trading account (perps), in USDC.

    This is the number the fund's NAV should reflect: the assets the agent
    actually trades sit in this account (HyperCore side).
    """
    addr = os.environ.get("HL_ACCOUNT_ADDRESS", "").strip()
    if not addr:
        return None
    try:
        from hyperliquid.info import Info
        from hyperliquid.utils import constants
        network = os.environ.get("HL_NETWORK", "testnet").lower()
        base = constants.MAINNET_API_URL if network == "mainnet" else constants.TESTNET_API_URL
        info = Info(base, skip_ws=True)
        state = info.user_state(addr)
        return float(state["marginSummary"]["accountValue"])
    except Exception as exc:  # network hiccup etc. — report, don't crash the relay
        print(f"warning: could not read trading account value ({exc})")
        return None


def to_bps(weights: dict[str, float]) -> tuple[list[str], list[int]]:
    coins = sorted(weights)
    return coins, [int(round(weights[c] * 10000)) for c in coins]


def main() -> int:
    ap = argparse.ArgumentParser(description="Relay strategy weights into the IndexFund contract")
    ap.add_argument("--weights", required=True, help="strategy weights.csv (target_<COIN> columns)")
    ap.add_argument("--fund", help="IndexFund contract address on HyperEVM")
    ap.add_argument("--rpc", default=DEFAULT_RPC, help="HyperEVM RPC URL")
    ap.add_argument("--data-dir", default="data", help="data.py cache folder (for the volatility gate)")
    ap.add_argument("--vol-threshold", type=float, default=0.80, help="annualized vol above this = high-vol regime")
    ap.add_argument("--min-change", type=float, default=0.01, help="min largest weight change to push in normal regime")
    ap.add_argument("--fee-base-bps", type=int, default=0, help="redemption fee in normal regime")
    ap.add_argument("--fee-high-bps", type=int, default=100, help="redemption fee in high-vol regime")
    ap.add_argument("--send", action="store_true", help="actually send the transactions (default: dry run)")
    args = ap.parse_args()

    load_env()

    # 1. Target weights from the strategy output.
    weights = read_target_weights(args.weights)
    coins, bps = to_bps(weights)
    print(f"Target weights from {args.weights}:")
    for c, b in zip(coins, bps):
        print(f"  {c:<8} {b / 100:5.2f}%")
    cash = 10000 - sum(bps)
    if cash > 0:
        print(f"  {'CASH':<8} {cash / 100:5.2f}%")

    # 2. Volatility gate.
    vol = realized_vol_btc(args.data_dir)
    if vol is None:
        print("Volatility: no BTC candle cache found — treating as normal regime")
        high_vol = False
    else:
        high_vol = vol > args.vol_threshold
        print(f"Volatility: BTC realized {vol:.0%} annualized "
              f"(threshold {args.vol_threshold:.0%}) -> {'HIGH-VOL' if high_vol else 'normal'} regime")
    fee_bps = args.fee_high_bps if high_vol else args.fee_base_bps

    if not args.fund:
        print("\nNo --fund given: plan only. Pass --fund <address> to compare on-chain and relay.")
        return 0

    # 3. Compare with the on-chain state.
    from web3 import Web3
    w3 = Web3(Web3.HTTPProvider(args.rpc))
    if not w3.is_connected():
        raise SystemExit(f"Cannot connect to RPC {args.rpc}")
    fund = w3.eth.contract(address=Web3.to_checksum_address(args.fund), abi=FUND_ABI)

    if fund.functions.breakerTripped().call():
        print("\nCircuit breaker is TRIPPED on-chain — relay aborted. "
              "The owner must review and call resetBreaker first.")
        return 1

    cur_coins, cur_bps, _ = fund.functions.getWeights().call()
    current = {c: b / 10000 for c, b in zip(cur_coins, cur_bps)}
    max_change = max([abs(weights.get(c, 0.0) - current.get(c, 0.0))
                      for c in set(weights) | set(current)] or [0.0])
    print(f"On-chain weights: {current if current else '(none yet)'}")
    print(f"Largest weight change: {max_change:.2%}")

    push = high_vol or max_change >= args.min_change
    account_value = trading_account_value_usdc()
    print(f"Trading account value: {account_value if account_value is not None else '(unavailable)'} USDC")
    print(f"Plan: {'PUSH weights' if push else 'skip weights (change below threshold, normal regime)'}; "
          f"set redemption fee to {fee_bps} bps; "
          f"{'report total assets' if account_value is not None else 'skip valuation report'}")

    if not args.send:
        print("\nDry run — nothing sent. Re-run with --send to relay.")
        return 0

    # 4. Send: weights (if due), fee (if changed), valuation (if available).
    key = os.environ.get("HL_AGENT_KEY", "").strip()
    if not key:
        raise SystemExit("Missing HL_AGENT_KEY in ~/.hyperliquid/env — run setup_keys.py")
    from eth_account import Account
    acct = Account.from_key(key)
    print(f"Sending as agent {acct.address}")

    def send_tx(fn):
        tx = fn.build_transaction({
            "from": acct.address,
            "nonce": w3.eth.get_transaction_count(acct.address),
            "chainId": w3.eth.chain_id,
            "gas": fn.estimate_gas({"from": acct.address}),
            "gasPrice": w3.eth.gas_price,
        })
        signed = acct.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        if receipt.status != 1:
            raise SystemExit(f"Transaction failed: {tx_hash.hex()}")
        return tx_hash.hex()

    if push:
        print("setWeights ->", send_tx(fund.functions.setWeights(coins, bps)))
    if fund.functions.redemptionFeeBps().call() != fee_bps:
        print("setRedemptionFee ->", send_tx(fund.functions.setRedemptionFee(fee_bps)))
    if account_value is not None:
        print("reportTotalAssets ->",
              send_tx(fund.functions.reportTotalAssets(int(round(account_value * 1_000_000)))))

    new_coins, new_bps, _ = fund.functions.getWeights().call()
    print(f"On-chain weights now: {dict(zip(new_coins, [b / 100 for b in new_bps]))} (%); "
          f"NAV per share: {fund.functions.navPerShare().call() / 1e6}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
