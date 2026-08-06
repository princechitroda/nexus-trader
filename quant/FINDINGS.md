# XAUUSD Research Findings — Round 1

**Date:** 2026-08-06
**Data:** 10 years real XAUUSD, 2012-05-15 → 2022-03-04 (230,400 M15 bars / 57,600 H1 bars)
**Costs assumed:** 0.30 spread, $7/lot round turn, 0.03 entry slip, 0.12 stop slip

---

## Headline

**No fundable edge was found. Do not buy a challenge yet.**

Six strategies were tested — five conventional, one purpose-built from measured
structure. After realistic costs, every one of them either loses money or has an
edge statistically indistinguishable from zero. The best out-of-sample result was
**+1.96% over 7.5 years** with a t-statistic of **0.31**.

This is a useful result, not a failed project. It cost nothing and it has probably
saved several challenge fees. What follows is what the data actually says, which
is considerably more interesting than the headline.

---

## 1. The data is real and it checks out

Sourced from a public MT5-derived dataset. Every anchor matches known gold history:

| Event | Data says | Reality |
|---|---|---|
| Dec 2015 bear low | **1046.23** | 1046.20 |
| Aug 2020 all-time high | **2074.87** | 2075.14 |
| 15 Apr 2013 crash | H 1495.57 → L 1337.09 | the ~$140 one-day crash |
| Brexit, 24 Jun 2016 | L 1250.34 → H 1358.25 | ✓ |
| COVID, 16 Mar 2020 | H 1562.94 → L 1451.13 | ✓ |

**Timezone was determined empirically, not assumed.** Peak 15-minute volatility sits
at exactly `15:30` file-time. US data releases at 8:30 ET = 13:30 UTC in winter and
12:30 UTC in summer — both map to 15:30 only on an EET/EEST (UTC+2/+3) server clock.
The daily broker break at 00:00–01:00 file-time (= 5pm ET) confirms it. So the data
is loaded with `--server-tz 2`, which yields a DST-free clock where London and New
York sit at fixed hours all year.

---

## 2. What the market actually does (this is the valuable part)

Before fitting anything, `research/edge_scan.py` measured conditional forward
returns directly, in ATR units, with significance from a **daily block bootstrap**
(a naive t-test on overlapping forward windows is inflated by roughly √h and will
manufacture edges that aren't there).

### Gold is a momentum market. Fading it is systematically wrong.

| Effect | Horizon | Mean move | 95% CI | p |
|---|---|---|---|---|
| **Asian-range break continuation** | 1h | +0.00 ATR | [−0.04, +0.04] | 0.94 |
| | 4h | +0.23 ATR | [+0.07, +0.39] | 0.005 |
| | 8h | **+0.52 ATR** | [+0.19, +0.83] | 0.001 |
| | 24h | **+0.70 ATR** | [+0.24, +1.18] | 0.007 |
| Momentum (4h lookback) | 24h | +0.25 ATR | [+0.09, +0.41] | 0.003 |
| Momentum in expanding vol | 8h | +0.14 ATR | [+0.02, +0.25] | 0.027 |
| **Prior-day sweep *reversal*** | 8h | **−0.12 ATR** | [−0.34, +0.11] | 0.31 |
| Asian-session drift | 8h | −0.08 ATR | [−0.22, +0.05] | 0.24 |

Three findings worth internalising as a trader, independent of any algo:

1. **The Asian-range break is real — but the edge is exactly zero in the first
   hour and accrues over 8–24 hours.** This single fact explains why the standard
   retail ORB loses: it is *correct about direction* and then exits at the London
   close, before the move it correctly predicted has happened, having paid the
   spread for the privilege.

2. **The liquidity-sweep-reversal premise is backwards.** Fading a prior-day
   high/low sweep has *negative* expectancy. Sweeps continue more often than they
   reverse. Anyone teaching the reverse is teaching a losing trade.

3. **Fading gold in the Asian session is a losing trade** (`asian_fade` gross
   expectancy −0.05R, t = −3.16 *before costs*). The inverse has the edge.

---

## 3. Why the strategies still fail: the cost arithmetic

Gross vs net expectancy per trade, M15, 10 years:

| Strategy | Gross expR | Cost drag | Net expR |
|---|---|---|---|
| london_orb | −0.002 | 0.253 | **−0.255** |
| asian_fade | −0.051 | 0.224 | −0.274 |
| trend_pullback | +0.067 | 0.179 | −0.112 |
| donchian | +0.035 | 0.141 | −0.106 |
| sweep_reversal | +0.008 | 0.173 | −0.165 |

**Cost drag of 0.14–0.25R against gross edges of 0.03–0.07R.** Costs are three to
five times larger than any edge present. This is the whole story of retail gold
algo trading in one table.

Drag scales with 1/stop-distance, so moving the same idea to H1 with a wider stop
cut it from 0.25R to **0.068R** and flipped net expectancy positive. That fix is
real and worth knowing — it just wasn't enough.

---

## 4. The purpose-built strategy, and its honest result

`AsiaBreakMomentum` was built *from* the scan above: H1 execution, wide stop, hold
8–48 hours, no breakeven and no partial (both truncate the right tail the edge
lives in), volatility-regime gate.

In-sample it looks respectable — PF 1.20, return/drawdown 2.93, max DD 3.7%. Then:

**Year by year (this is what killed it):**

| Year | expR | P&L |
|---|---|---|
| 2012 | −0.042 | −2,171 |
| **2013** | **+0.163** | **+12,911** |
| 2014 | −0.024 | −2,485 |
| 2015 | +0.057 | +4,383 |
| 2016 | −0.108 | −8,305 |
| 2017 | +0.066 | +4,920 |
| 2018 | −0.065 | −6,150 |
| 2019 | −0.078 | −6,545 |
| **2020** | **+0.270** | **+21,381** |
| 2021 | +0.049 | +4,007 |

Five of eleven years negative. **2013 and 2020 together contribute more than the
entire ten-year profit** — the gold crash year and the COVID year, both violent
high-volatility trending regimes. Remove them and the system loses money.

**Walk-forward (24m train / 6m test, 15 folds):**

```
trades 527   win 48.0%   PF 1.03   expectancy +0.0083R   t = 0.31
return +1.96%   maxDD 9.59%   return/DD 0.20   Sharpe 0.10
fold efficiency 60%
```

That is zero. The in-sample profile was parameter selection, not edge.

---

## 5. What it would actually take

Independent of any price history — pure arithmetic of the rules plus sequencing
risk. **P(pass phase 1: +10% target, 5% daily loss, 10% max loss), 45% win rate:**

**At 1 trade/day:**

| | risk 0.50% | 0.75% | 1.00% | 1.50% | 2.00% |
|---|---|---|---|---|---|
| exp +0.02R | 13% | 28% | 41% | 49% | 41% |
| exp +0.10R | 34% | 57% | 67% | 69% | 54% |
| exp +0.20R | 69% | 84% | **87%** | 82% | 65% |

**At 3 trades/day:**

| | risk 0.50% | 0.75% | 1.00% | 1.50% | 2.00% |
|---|---|---|---|---|---|
| exp +0.02R | 52% | 61% | 53% | 40% | 31% |
| exp +0.10R | **90%** | 88% | 71% | 54% | 39% |
| exp +0.20R | **99%** | 96% | 83% | 64% | 50% |

Two things fall out of this that matter more than any strategy:

- **Risking 1.5–2% is worse than risking 1%, almost everywhere on the grid.** The
  drawdown tripwire bites harder than the extra size helps. Every cell in the 2%
  column is worse than the 1% column at the same edge. If you take one number from
  this whole project, take that one.
- **Frequency substitutes for edge.** At 3 trades/day, a feeble +0.05R passes 69%
  of the time at 0.5% risk. At 1 trade/day the same edge passes 20%.

**The target: ≥ +0.10R expectancy at ~3 trades/day, risking 0.5–0.75%.**
Our best out-of-sample result was **+0.008R at 0.3 trades/day** — between 12× and
25× short. That gap is not closeable by parameter tuning.

---

## 6. The most important caveat: the data stops in March 2022

This dataset ends **2022-03-04**. It therefore misses the entire 2022–2026 period,
including gold's largest sustained bull trend in decades.

That matters more than usual here, because the one thing this research established
about the edge is that **it is regime-dependent — it pays in high-volatility
trending years (2013, 2020) and bleeds in quiet ones.** 2023–2025 was exactly the
former. It is genuinely possible that `AsiaBreakMomentum` performed well over the
missing four years.

I want to be precise about the epistemics: that is a *hypothesis consistent with
the measured regime-dependence*, not a result. It cannot be confirmed without the
data, and "my strategy would have worked in the period I can't test" is the oldest
self-deception in trading. It is, however, the single highest-value thing to check
next, and it is cheap to check.

---

## 7. What to do next

1. **Export XAUUSD H1 + M15 from your MT5, 2021→today**, and drop it in
   `quant/data/raw/`. Note your broker's server offset. Then:
   ```bash
   python -m quant.run_research wfo --data <file> --server-tz <2 or 3> \
       --strategy asia_break_momentum --train 24 --test 6
   ```
   This directly tests the regime hypothesis above. It is the one experiment that
   could change the conclusion.

2. **Re-run the edge scan on the recent regime.** If the Asian-break effect is
   larger post-2022, that is a real finding; if it has decayed, that is also a real
   finding and settles the question.

3. **Raise frequency, not risk.** The requirements grid says frequency is the
   cheapest lever available. A +0.10R edge taken 3×/day passes 90%; the same edge
   taken once a day passes 34%. Widening the entry window, trading both the London
   and NY session breaks, or running several uncorrelated symbols are all better
   uses of effort than tuning stop multiples.

4. **Do not fund anything that has not cleared this bar:** t ≥ 2.0 out of sample,
   ≥ 60% of walk-forward folds profitable, top trade < 30% of total profit, and
   Monte Carlo pass rate ≥ 70% at ≤ 1% risk. Then demo-forward-test for 4–6 weeks
   before paying a fee.

---

## Reproducing

```bash
pip install -r quant/requirements.txt
python -m quant.run_research selftest                     # validate the engine
python -m quant.run_research screen --data quant/data/raw/XAUUSDm15.csv \
    --server-tz 2 --price-scale 100
python -m quant.run_research wfo --data quant/data/raw/XAUUSDh1.csv \
    --server-tz 2 --price-scale 100 --strategy asia_break_momentum
```

The engine self-validates before any of this: a causality test (signals must be
bit-identical when future bars are deleted), a null test (structureless data must
lose ~the cost of trading), and a positive control (a known injected edge must be
found). All three pass.
