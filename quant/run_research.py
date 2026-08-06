#!/usr/bin/env python3
"""XAUUSD strategy research CLI.

    python -m quant.run_research selftest
    python -m quant.run_research screen --data data/XAUUSD_M15.csv --server-tz 3
    python -m quant.run_research wfo    --data data/XAUUSD_M15.csv --strategy london_orb

`selftest` needs no data and validates the backtester itself. `screen` and `wfo`
need real gold history — see quant/README.md for how to export it from MT5.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from .data import loader, synth
from .engine import metrics as metrics_mod
from .engine import montecarlo, propfirm
from .engine.backtest import Backtester
from .engine.costs import CostModel, Instrument, ZeroCosts
from .engine.walkforward import walk_forward
from .strategies import PARAM_GRIDS, REGISTRY

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 50)


def _rule(title: str = "") -> None:
    print("\n" + "─" * 92)
    if title:
        print(title)
        print("─" * 92)


def _load(args) -> pd.DataFrame:
    if args.data:
        df = loader.load_csv(args.data, server_tz_offset=args.server_tz,
                             timeframe=args.timeframe, price_scale=args.price_scale)
        print(f"Loaded {len(df):,} bars from {Path(args.data).name}: {df.index[0]} → {df.index[-1]}")
        loader.validate(df, verbose=True)
        return df
    print("No --data given; falling back to SYNTHETIC data.")
    print("!! Synthetic results say nothing about real profitability. See quant/README.md.")
    return synth.generate(structured=args.structured)


# ────────────────────────────────────────────────────────────── self test ────
def _causality_check(df: pd.DataFrame, margin: int = 400) -> list[str]:
    """Prove no strategy can see the future.

    Run `prepare()` on the full history, then again on the history truncated to
    70% of its length. Every signal and stop level on the overlapping region must
    be *bit-identical*. If a strategy peeks ahead — an unshifted higher-timeframe
    series, a centred rolling window, a groupby that spans the whole frame — the
    truncated run produces different values and this catches it.

    This is a far stronger guarantee than "the null test lost money", because a
    small leak can hide under trading costs. Bars near the cut are excluded: the
    final higher-timeframe candle is legitimately incomplete there.
    """
    problems = []
    cut = int(len(df) * 0.7)
    for key, cls in REGISTRY.items():
        strat = cls()
        full = strat.prepare(df)
        trunc = strat.prepare(df.iloc[:cut])
        lo, hi = strat.warmup(), cut - margin
        if hi <= lo:
            continue
        for col in ["long_signal", "short_signal", "stop_long", "stop_short",
                    "target_long", "target_short"]:
            a = full[col].iloc[lo:hi]
            b = trunc[col].iloc[lo:hi]
            if a.dtype == bool:
                bad = int((a.to_numpy() != b.to_numpy()).sum())
            else:
                x, y = a.to_numpy(float), b.to_numpy(float)
                bad = int((~(np.isclose(x, y, rtol=1e-9, atol=1e-9) |
                             (np.isnan(x) & np.isnan(y)))).sum())
            if bad:
                problems.append(f"{key}.{col}: {bad} bars differ when the future is removed")
    return problems


def cmd_selftest(args) -> int:
    """Causality + null test + positive control. Non-zero exit = untrustworthy engine."""
    bt = Backtester(initial_balance=100_000.0)
    failures: list[str] = []

    _rule("CAUSALITY TEST — signals must not change when future bars are deleted")
    causal_df = synth.generate(start="2022-01-01", end="2024-06-30", seed=5)
    problems = _causality_check(causal_df)
    if problems:
        for p in problems:
            print(f"  LEAK  {p}")
        failures.extend(problems)
    else:
        print(f"  clean — all {len(REGISTRY)} strategies reproduce identical signals "
              f"on truncated history")

    _rule("NULL TEST — no structure in the data; every strategy must LOSE after costs")
    null_df = synth.generate(structured=False, seed=11)
    print(f"{len(null_df):,} synthetic bars, {null_df.index[0].date()} → {null_df.index[-1].date()}\n")
    rows = []
    for key, cls in REGISTRY.items():
        res = bt.run(null_df, cls())
        m = metrics_mod.compute(res)
        rows.append({
            "strategy": key, "trades": m.n_trades, "return_%": round(m.total_return_pct, 2),
            "PF": round(m.profit_factor, 2), "exp_R": round(m.expectancy_r, 4),
            "t_stat": round(m.t_stat, 2),
        })
        # A t-stat above 3 on structureless data means look-ahead, not skill.
        if m.n_trades >= 30 and m.t_stat > 3.0:
            failures.append(f"{key}: t={m.t_stat:.2f} on structureless data — suspect look-ahead")
    print(pd.DataFrame(rows).to_string(index=False))

    _rule("COST SENSITIVITY — same data, zero costs. Gap = the cost drag, and it should be large")
    rows = []
    bt_free = Backtester(initial_balance=100_000.0, costs=ZeroCosts())
    for key, cls in REGISTRY.items():
        m_free = metrics_mod.compute(bt_free.run(null_df, cls()))
        rows.append({"strategy": key, "zero_cost_exp_R": round(m_free.expectancy_r, 4),
                     "zero_cost_return_%": round(m_free.total_return_pct, 2)})
    print(pd.DataFrame(rows).to_string(index=False))
    print("\nExpectancy near 0 with zero costs = the engine is not manufacturing edge. Good.")

    _rule("POSITIVE CONTROL — a real Asian-break continuation effect is injected")
    pos_df = synth.generate(structured=True, structure_strength=0.5, seed=11)
    res = bt.run(pos_df, REGISTRY["london_orb"]())
    m = metrics_mod.compute(res)
    print(f"LondonORB on structured data: {m.n_trades} trades, "
          f"return {m.total_return_pct:+.2f}%, PF {m.profit_factor:.2f}, "
          f"expectancy {m.expectancy_r:+.3f}R, t={m.t_stat:.2f}")
    if m.n_trades < 30:
        failures.append("positive control produced too few trades to judge")
    elif m.expectancy_r <= 0:
        failures.append("positive control: engine failed to detect a known injected edge")

    _rule("RESULT")
    if failures:
        for f in failures:
            print(f"  FAIL  {f}")
        return 1
    print("  PASS  engine finds a known edge and finds nothing where there is none.")
    print("        Safe to point at real data.")
    return 0


# ───────────────────────────────────────────────────────────────── screen ────
def cmd_screen(args) -> int:
    df = _load(args)
    costs = CostModel(spread=args.spread, commission_per_lot=args.commission)
    bt = Backtester(instrument=Instrument(), costs=costs,
                    initial_balance=args.balance, max_risk_pct=0.02)
    rules = propfirm.PropFirmRules(
        initial_balance=args.balance,
        profit_target_pct=args.target / 100.0,
        daily_loss_pct=args.daily_loss / 100.0,
        max_loss_pct=args.max_loss / 100.0,
        trailing_max_loss=args.trailing_dd,
    )

    _rule("IN-SAMPLE SCREEN — ranking is indicative only; trust the walk-forward, not this")
    rows, results = [], {}
    for key, cls in REGISTRY.items():
        strat = cls(risk_pct=args.risk / 100.0)
        res = bt.run(df, strat)
        results[key] = res
        m = metrics_mod.compute(res)
        pf_res = propfirm.evaluate(res, rules)
        daily = propfirm.daily_returns_table(res, rules.reset_hour_utc)
        mc = montecarlo.simulate_challenge(daily, rules, n_sims=args.sims)
        rows.append({
            "strategy": key,
            "trades": m.n_trades,
            "tr/mo": round(m.trades_per_month, 1),
            "win%": round(m.win_rate, 1),
            "PF": round(m.profit_factor, 2),
            "exp_R": round(m.expectancy_r, 3),
            "t": round(m.t_stat, 2),
            "ret%": round(m.total_return_pct, 1),
            "maxDD%": round(m.max_dd_pct, 2),
            "ret/DD": round(m.return_over_maxdd, 2),
            "Sharpe": round(m.sharpe, 2),
            "top_trade": round(m.best_trade_share, 2),
            "challenge": pf_res.outcome.value,
            "MC_pass": mc.pass_rate,
        })
    table = pd.DataFrame(rows).sort_values("MC_pass", ascending=False).reset_index(drop=True)
    display = table.copy()
    display["MC_pass"] = display["MC_pass"].map(lambda v: f"{v:.0%}")
    print(display.to_string(index=False))

    print("\nHow to read this:")
    print("  t < 2.0        → result is indistinguishable from luck, whatever the return")
    print("  top_trade>0.3  → most of the profit is one trade; not a system")
    print("  MC_pass        → P(reach target before breaching), over resampled orderings")

    best = table.iloc[0]["strategy"]
    _rule(f"RISK SIZING CURVE — {best} (best MC pass rate)")
    daily = propfirm.daily_returns_table(results[best], rules.reset_hour_utc)
    curve = montecarlo.risk_curve(daily, rules, n_sims=max(args.sims // 2, 500))
    curve["risk_per_trade_%"] = curve["risk_multiplier"] * args.risk
    print(curve[["risk_per_trade_%", "pass_rate", "breach_daily", "breach_max",
                 "timeout", "median_days_to_pass"]].to_string(index=False))
    peak = curve.loc[curve["pass_rate"].idxmax()]
    print(f"\nPeak pass probability {peak['pass_rate']:.0%} at "
          f"{peak['risk_per_trade_%']:.2f}% risk per trade.")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        table.to_csv(args.out, index=False)
        print(f"\nWrote {args.out}")
    return 0


# ──────────────────────────────────────────────────────────── walk-forward ────
def cmd_wfo(args) -> int:
    df = _load(args)
    key = args.strategy
    if key not in REGISTRY:
        print(f"unknown strategy {key!r}; choose from {list(REGISTRY)}")
        return 2

    costs = CostModel(spread=args.spread, commission_per_lot=args.commission)
    bt = Backtester(costs=costs, initial_balance=args.balance, max_risk_pct=0.02)
    proto = REGISTRY[key](risk_pct=args.risk / 100.0)
    grid = PARAM_GRIDS.get(key, {})

    _rule(f"WALK-FORWARD — {key} | train {args.train}m / test {args.test}m | grid {grid}")
    wf = walk_forward(df, proto, grid, backtester=bt,
                      train_months=args.train, test_months=args.test,
                      anchored=args.anchored)

    if not wf.folds:
        print("Not enough history for even one fold. Need at least "
              f"{args.train + args.test} months; got "
              f"{(df.index[-1] - df.index[0]).days / 30.4:.1f}.")
        return 1

    _rule("FOLD DETAIL")
    print(wf.fold_table.to_string(index=False))

    m = metrics_mod.compute(wf.oos)
    rules = propfirm.PropFirmRules(
        initial_balance=args.balance,
        profit_target_pct=args.target / 100.0,
        daily_loss_pct=args.daily_loss / 100.0,
        max_loss_pct=args.max_loss / 100.0,
        trailing_max_loss=args.trailing_dd,
    )
    pf_res = propfirm.evaluate(wf.oos, rules)
    daily = propfirm.daily_returns_table(wf.oos, rules.reset_hour_utc)
    mc = montecarlo.simulate_challenge(daily, rules, n_sims=args.sims)

    _rule("OUT-OF-SAMPLE AGGREGATE — this is the only number that means anything")
    print(f"  trades           {m.n_trades} ({m.trades_per_month:.1f}/month)")
    print(f"  win rate         {m.win_rate:.1f}%")
    print(f"  profit factor    {m.profit_factor:.2f}")
    print(f"  expectancy       {m.expectancy_r:+.3f}R   (t = {m.t_stat:.2f})")
    print(f"  return           {m.total_return_pct:+.2f}%")
    print(f"  max drawdown     {m.max_dd_pct:.2f}%  over {m.max_dd_days:.0f} days")
    print(f"  return / maxDD   {m.return_over_maxdd:.2f}")
    print(f"  Sharpe           {m.sharpe:.2f}")
    print(f"  top-trade share  {m.best_trade_share:.2f}")
    print(f"  fold efficiency  {wf.efficiency:.0%} of folds profitable OOS")
    print(f"\n  single-run challenge outcome: {pf_res.outcome.value} "
          f"(worst day {pf_res.worst_daily_loss_pct:.2f}%, worst total {pf_res.max_total_dd_pct:.2f}%)")
    print(f"  monte carlo: {mc.report()}")

    verdict = []
    if m.t_stat < 2.0:
        verdict.append("t-stat below 2 — not statistically distinguishable from luck")
    if wf.efficiency < 0.6:
        verdict.append(f"only {wf.efficiency:.0%} of folds profitable — unstable edge")
    if m.best_trade_share > 0.3:
        verdict.append("profit concentrated in one trade")
    if mc.pass_rate < 0.5:
        verdict.append(f"MC pass rate {mc.pass_rate:.0%} — coin-flip or worse on a challenge")
    _rule("VERDICT")
    if verdict:
        print("  NOT FUNDABLE as configured:")
        for v in verdict:
            print(f"    - {v}")
    else:
        print("  Survives walk-forward with a usable challenge pass rate.")
        print("  Next step: forward-test on demo for 4-6 weeks before risking a fee.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--data", help="path to OHLC CSV (MT5 export, Dukascopy, or generic)")
        sp.add_argument("--server-tz", type=float, default=0.0,
                        help="broker server UTC offset in hours (MT5 exports are NOT UTC)")
        sp.add_argument("--timeframe", default=None, help="resample, e.g. 15min")
        sp.add_argument("--price-scale", type=float, default=1.0,
                        help="divide raw prices by this (some public datasets ship 155408 for 1554.08)")
        sp.add_argument("--structured", action="store_true",
                        help="synthetic fallback: inject a known edge (control only)")
        sp.add_argument("--balance", type=float, default=100_000.0)
        sp.add_argument("--risk", type=float, default=0.5, help="%% of equity risked per trade")
        sp.add_argument("--spread", type=float, default=0.25, help="gold spread in price units")
        sp.add_argument("--commission", type=float, default=7.0, help="USD per lot round turn")
        sp.add_argument("--target", type=float, default=10.0, help="profit target %%")
        sp.add_argument("--daily-loss", type=float, default=5.0)
        sp.add_argument("--max-loss", type=float, default=10.0)
        sp.add_argument("--trailing-dd", action="store_true", help="max loss trails the equity high")
        sp.add_argument("--sims", type=int, default=3000)

    sp = sub.add_parser("selftest", help="validate the backtester (no data needed)")
    sp.set_defaults(func=cmd_selftest)

    sp = sub.add_parser("screen", help="run every strategy and rank them")
    common(sp)
    sp.add_argument("--out", default=None, help="write the ranking table to CSV")
    sp.set_defaults(func=cmd_screen)

    sp = sub.add_parser("wfo", help="walk-forward one strategy")
    common(sp)
    sp.add_argument("--strategy", required=True, choices=list(REGISTRY))
    sp.add_argument("--train", type=int, default=12, help="training window, months")
    sp.add_argument("--test", type=int, default=3, help="test window, months")
    sp.add_argument("--anchored", action="store_true", help="expanding rather than rolling window")
    sp.set_defaults(func=cmd_wfo)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
