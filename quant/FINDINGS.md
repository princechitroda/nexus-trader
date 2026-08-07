# XAUUSD Research Findings

**Last updated:** 2026-08-06
**Data:** 21 years real XAUUSD H1, 2004-06-11 → 2025-06-06 (122,028 bars), plus a
second independent 2012–2022 M15 dataset used for cross-checking.
**Costs assumed:** 0.30 spread, $7/lot round turn, 0.03 entry slip, 0.12 stop slip

---

## Bottom line

**No generalizable market inefficiency was found.** What survives is a gold
long-bias with an unusually good drawdown profile — which can pass an untimed
challenge, but is a bet on gold continuing to rise, not a discovered edge.

The gold strategy clears every statistical gate below. It then fails the one test
that distinguishes a real mechanism from a well-dressed directional bet: applied to
eleven other symbols, the same signal has **negative** expectancy (§7). Read the
bottom line as "this works on gold, for reasons that are probably gold's 21-year
uptrend", not as "we found an edge".

| | Result |
|---|---|
| Out-of-sample expectancy | **+0.049R** per trade |
| Out-of-sample t-statistic | **2.61** (clears the ≥2.0 bar) |
| Walk-forward folds profitable | **71%** (12 of 17) |
| OOS return / max drawdown | **6.91** (+46.0% vs 6.66% DD, 17 years) |
| Profit from single best trade | 1% (not a lottery ticket) |
| **P(pass a 120-day challenge)** | **7%** at 1% risk — do not buy one |
| **P(pass a no-time-limit challenge)** | **79%** at 1% risk, median **320 trading days** |

So: slow, and probably beta rather than alpha. It will not pass a timed challenge.
It would probably pass an untimed one over about fifteen months — while you carry
gold-direction risk that no backtest statistic on this page prices for you.

---

## 1. Two independent datasets, both validated against known gold history

| Event | Data | Reality |
|---|---|---|
| 2011 all-time high | **1920.61** | 1920.70 |
| Dec 2015 bear low | **1046.23** | 1046.20 |
| Aug 2020 high | **2074.87** | 2075.14 |
| Oct 2024 | **2790.05** | ✓ |
| Apr 2025 peak | **3499.94** | ~3500 |
| 15 Apr 2013 crash | H 1495.57 → L 1337.09 | the ~$140 one-day crash |

**Timezone was measured, not assumed.** Peak volatility sits at `15:xx` file-time
in both datasets. US data releases at 8:30 ET = 13:30 UTC winter / 12:30 UTC
summer — both land on 15:30 only on an EET/EEST (UTC+2/+3) server clock. The daily
broker break at hour 0 (= 5pm ET) confirms it. Hence `--server-tz 2`, which gives a
DST-free clock where London and New York sit at fixed hours year-round.

### A data bug worth knowing about

The H1 file stamps dates as `2004.06.11` (yyyy.mm.dd). A dayfirst parse turns that
into 6 November — **but only on rows where the day is ≤ 12**; rows like `2024.10.30`
parse correctly because 30 cannot be a month. The result is an index that is
partly scrambled, still spans the right years, and still looks sorted after a
`sort_index()`. It showed up as gold trading at $1,817 on its all-time-high day.

`loader.py` now detects year-first formats explicitly and, as a backstop, refuses
to load any file whose parsed timestamps run backwards relative to file order.
Sorting over this class of corruption hides it rather than fixing it.

---

## 2. What gold actually does

Measured with `research/edge_scan.py`: conditional forward returns in ATR units,
significance from a **daily block bootstrap** (overlapping forward windows inflate
a naive t-stat by roughly √h).

### The confound that matters more than any single result

Gold went from $380 to $3,400 across this sample. **The unconditional drift is
+0.40 ATR per 48 hours** — so *any* long-biased measurement looks significant.
This showed up unmistakably: every single day of the week tested "significant"
and positive (+0.71 to +0.99 ATR). That is not a weekday effect. That is buy-and-hold
wearing a disguise.

Every number below is therefore reported against its own directional baseline.

### Results after controlling for drift

| Signal | Raw | Baseline | **Excess** | Verdict |
|---|---|---|---|---|
| Asia-range break, **long**, 48h | +0.79 | +0.40 | **+0.39** | real |
| Asia-range break, **short**, 48h | −0.04 | −0.40 | **+0.36** | real |
| Momentum (96h lookback), long, 48h | +0.53 | +0.40 | +0.13 | weak |
| Momentum (96h lookback), short, 48h | −0.21 | −0.40 | +0.19 | weak |
| Prior-day sweep **reversal** | −0.05 | 0 | **negative** | backwards |

Three things worth knowing as a discretionary trader, independent of any algo:

1. **The Asian-range break carries genuine, symmetric information** — roughly
   +0.37 ATR of excess move on both sides. But the edge is **near zero in the
   first hour and accrues over 24–48 hours** (+0.09 at 4h → +0.30 at 24h → +0.39
   at 48h). This is precisely why the conventional intraday ORB loses money: it is
   *correct about direction* and then exits at the London close, before the move
   it correctly predicted has happened, having paid the spread for the privilege.

2. **The liquidity-sweep-reversal premise is backwards.** Fading a prior-day
   high/low sweep has negative expectancy at every horizon tested. Sweeps continue
   more often than they reverse.

3. **Fading gold in the Asian session is a losing trade** (`asian_fade` gross
   expectancy −0.05R, t = −3.16 *before costs*).

---

## 3. Why the five conventional strategies all failed

Gross vs net expectancy per trade, M15, 10 years:

| Strategy | Gross expR | Cost drag | Net expR |
|---|---|---|---|
| london_orb | −0.002 | 0.253 | **−0.255** |
| asian_fade | −0.051 | 0.224 | −0.274 |
| trend_pullback | +0.067 | 0.179 | −0.112 |
| donchian | +0.035 | 0.141 | −0.106 |
| sweep_reversal | +0.008 | 0.173 | −0.165 |

**Cost drag of 0.14–0.25R against gross edges of 0.03–0.07R.** Costs three to five
times larger than the edge. That is the entire story of retail gold algo trading in
one table.

Drag scales with 1/stop-distance, so moving the same idea to **H1 with a wide stop
cut drag from 0.25R to 0.068R** — a 4× reduction, achieved by changing nothing about
the signal. Trade less often, hold longer, stop wider.

---

## 4. A hypothesis I formed, then falsified

Round 1 (2012–2022 data only) found that 2013 and 2020 — both violent trending
years — contributed more than the entire ten-year profit. The natural hypothesis:
*the edge is regime-dependent and pays in high-volatility trending markets.*

The 21-year data killed it. **2023 (−0.003R) and 2024 (−0.006R) — the strongest
sustained gold trend in modern history — were flat to slightly negative.** A
volatility-percentile gate built on the hypothesis made results *worse*
out-of-sample. The 2013/2020 concentration was noise being read as structure.

Recording this because it is the failure mode this whole harness exists to catch,
and it caught it in me.

---

## 5. The strategy that works: `AsiaBreakMomentum`

Built *from* the measurements above rather than from intuition:

- **H1 execution**, not M15 — cuts cost drag 4× for the same signal
- **Asian-range break** during London *and* New York hours (07:00–18:00)
- **Wide stop** (5–8 ATR), **hold up to 48 hours**, time stop not a session bell
- **No breakeven, no partial** — both truncate exactly the right tail the 24–48h
  horizon exists to capture
- **Long-biased.** The signal is symmetric in *excess* terms, but a short
  additionally pays the secular drift it stands in front of. The walk-forward
  optimiser independently chose long-only in 13 of 17 folds, using training data
  only.

### Walk-forward result (36m train / 12m test, 17 folds, 2007–2024)

```
trades 1601 (7.8/month)   win 51.7%   PF 1.18   expectancy +0.0487R   t = 2.61
return +46.00%   maxDD 6.66%   return/DD 6.91   folds profitable 71%
top-trade share 0.01
```

Every quality gate passes: t ≥ 2.0 ✓, folds ≥ 60% ✓, top trade < 30% ✓.

**The honest caveat:** long-biased gold over 2004–2025 is partly a bet that gold
keeps rising. Buy-and-hold returned +778% over the same span — far more in absolute
terms — but with a **45% drawdown** that breaches a funded account many times over.
The strategy's contribution is not out-performing gold; it is extracting a fraction
of gold's move at a 6.7% drawdown instead of 45%. For a drawdown-limited account
that trade-off is the whole point.

---

## 6. Can it pass a funded challenge?

P(reach +10% before breaching 5% daily / 10% max), from the out-of-sample trade
stream, block-bootstrapped:

| Horizon | 0.5% risk | 1.0% risk | 1.5% risk |
|---|---|---|---|
| 120 days | 0% | **7%** | 24% |
| 250 days | 2% | 27% | 50% |
| 500 days | 18% | **57%** | 70% |
| 1000 days | 51% | **79%** | 76% |

Trailing-drawdown variant (harder, many newer firms): 57% at 1% risk / 500 days.

**Conclusions:**

- **Do not buy a timed challenge.** 7% in 120 days is paying a fee for a lottery ticket.
- **An untimed challenge is genuinely winnable** — 79% at 1% risk — but the median
  time to pass is **320 trading days**, about fifteen months. Budget accordingly.
- **1.5% risk is not better than 1% at long horizons** (76% vs 79%). The drawdown
  tripwire starts costing more than the extra size earns. This matches the general
  requirements grid: every 2% column is worse than the 1% column at equal edge.

---

## 7. The test that changed the conclusion: eleven other symbols

The plan was to fix the frequency problem by running the same signal across many
symbols. The Asian-range break is supposed to be a *liquidity mechanism* — a thin
overnight session accumulating orders, a busy London session running them — and
that structure exists in every major pair, not just gold. Ten symbols should mean
ten times the trades.

It is also, incidentally, the sharpest available out-of-sample test of whether the
mechanism is real at all. It is not.

**Same strategy, same parameters, 12 symbols, H1, 2012–2022, equal-weighted:**

| | Portfolio result |
|---|---|
| Long-biased (the gold config) | expR **−0.006**, t = −0.59, −2.7% |
| Both sides (the pure signal) | expR **−0.016**, t = −1.67, −10.1% |
| Short only | expR −0.005, t = −0.33, −2.3% |

Per-symbol, pure signal — only 4 of 12 positive, **none significant**:

| Symbol | expR | t | | Symbol | expR | t |
|---|---|---|---|---|---|---|
| USDJPY | +0.044 | 1.17 | | GBPUSD | −0.031 | −2.00 |
| AUDJPY | +0.035 | 0.84 | | USDCAD | −0.040 | −2.55 |
| XAUUSD | +0.009 | 0.50 | | EURCHF | −0.037 | −2.66 |
| EURJPY | +0.020 | 0.42 | | EURGBP | −0.066 | −4.37 |

Mean off-diagonal daily correlation was **0.09** — diversification was excellent, so
the portfolio machinery worked exactly as intended. It faithfully diversified a
collection of nothing.

**What this means.** A mechanism that appears in one instrument, only on one side,
only during that instrument's 21-year bull market, is far more likely to be drift
plus survivorship in my own hypothesis search than a structural inefficiency. The
gold result in §5 is best read as: *a long-gold position, entered on a filter that
happens to keep drawdowns near 6%.* That is a genuinely useful property for a
drawdown-limited account. It is not an edge, and it will stop working the moment
gold stops rising.

Note also that the long-biased gold config *loses* on FX (−2.7%), which is what you
would expect if its contribution were directional rather than structural.

### The frequency arithmetic still stands

From `research/requirements.py`, P(pass phase 1, 120 days):

| | risk 0.5% | 0.75% | 1.0% | 1.5% | 2.0% |
|---|---|---|---|---|---|
| **1 trade/day**, exp +0.10R | 34% | 57% | 67% | 69% | 54% |
| **3 trades/day**, exp +0.05R | **69%** | 74% | 61% | 45% | 35% |
| **3 trades/day**, exp +0.10R | **90%** | 88% | 71% | 54% | 39% |

A *feeble* +0.05R edge taken three times a day beats a strong +0.10R edge taken
once. That arithmetic is still correct and still the right target — the portfolio
attempt above reached **128 trades/month**, comfortably past the frequency needed.

It just had nothing to multiply. Frequency is a multiplier on edge, and 128 × 0 = 0.
The search has to go back to finding an edge that generalizes.

---

## 8. What to do next, in order

1. **Do not fund anything yet.** Nothing here has been forward-tested, and §7 says
   the one candidate is probably directional beta.
2. **Decide what you actually want to bet on.** If you believe gold keeps rising,
   the §5 strategy is a *reasonable, drawdown-controlled way to express that view*
   on an untimed challenge — just hold it in your head as a gold bet, not an edge.
   If you want an edge that does not depend on that, keep searching.
3. **Export XAUUSD H1 from your own MT5, 2021→today**, and re-run §5 on your
   broker's actual spread and candles. The public data ends June 2025.
4. **Search where the mechanism is more likely to be structural.** The edge scan
   found the strongest raw effects at 24–48h horizons; the obvious unexplored
   ground is scheduled-event structure (NFP/CPI/FOMC), cross-asset conditioning
   (DXY, real yields, SPX), and intraday seasonality that is *symmetric* rather
   than drift-contaminated. `research/edge_scan.py` measures any new hypothesis in
   about a minute — use it before writing another strategy.
5. **Whatever you find, apply the §7 test to it**: if it does not work on symbols
   other than the one it was discovered on, treat it as drift until proven
   otherwise.
6. **Then** demo-forward-test for 4–6 weeks before paying any fee. If you buy a
   challenge, buy an untimed one and risk 1% — not 2%.

---

## Reproducing

```bash
pip install -r quant/requirements.txt
python -m quant.run_research selftest      # causality + null + positive control

# the headline walk-forward
python -m quant.run_research wfo --data quant/data/raw/XAU_1h_data.csv \
    --server-tz 2 --strategy asia_break_momentum --train 36 --test 12
```

Engine self-validation — a causality test (signals must be bit-identical when future
bars are deleted), a null test (structureless data must lose ≈ the cost of trading),
and a positive control (a known injected edge must be found) — all pass.

---

# Appendix: FundingPips $5,000 account

**Rules below are compiled from public third-party documentation in August 2026.
fundingpips.com and their help centre are both blocked by this environment's egress
proxy, so I could not read the primary source. Verify every number on their live
rulebook for the exact plan you buy before paying.** Prop firm terms change often
and differ by plan.

## The plans

| Plan | Target | Daily loss | Max loss | Type | Min days | Time limit |
|---|---|---|---|---|---|---|
| **2-Step Standard** | 8% then 5% | 5% | 10% | **static** | 3/phase | **unlimited** |
| 1-Step Flex | 10% | 4% | 6% | static | 3 | unlimited |
| 2-Step Pro | 6% then 6% | 3% | ~8% | trailing | 3/phase | unlimited |
| Zero (instant) | none | 3% | 5% | **trailing** | — | — |

$5,000 entry is around $29–40 depending on plan. Two mechanics worth internalising:

- **Daily loss is measured against the higher of the day's opening balance or
  opening equity, and it counts floating P&L.** A trade 5.1% underwater at 14:00
  that recovers by 17:00 is still a breach. Resets 00:00 platform time = 21:00 UTC.
- **Each phase starts with a fresh 10% drawdown budget.** Two phases at 8%+5% is
  materially easier than one 13% run, which is why the two-step beats the one-step
  below despite the higher total target.

## Automation is allowed — and you already have the proof they ask for

Full automation is permitted **on an EA you wrote yourself**, provided you can show
ownership: source code, version-control history, development environment, or
explaining the logic on a call. A compiled `.ex5` on its own is not accepted.

This git repository — commit history, research that motivated each rule, the
walk-forward that selected the parameters — is a stronger ownership record than
most people can produce. Keep it.

Banned: third-party/off-the-shelf EAs (allowed only as a trade/risk *manager*),
copy trading inbound from any source, and anyone else trading your account.

## The constraint nobody mentions: lot granularity

Gold is ~$3,369 and median H1 ATR(14) in 2025 is **$8.64**. Minimum lot is 0.01,
which on gold is $1 per $1 move. So:

| Stop | Stop distance | Risk of the **smallest possible position** on $5,000 |
|---|---|---|
| 5 × ATR | $43.20 | **0.86%** |
| 8 × ATR | $69.12 | **1.38%** |

**You cannot risk 0.5% per trade on a $5,000 gold account with this strategy.** The
smallest trade you are able to place already risks 0.86–1.38%, and sizing is
quantised — 0.86%, 1.73%, 2.59%… There is no fine control. Every "risk 0.5%" piece
of advice on the internet is unimplementable at this account size on this
instrument, and the constraint disappears entirely by $25,000.

## What that means for pass probability

Using the out-of-sample trade stream from §5, FundingPips 2-Step Standard, $5,000:

| Config | Risk/trade | P(phase 1) | P(phase 2) | **P(both)** | Breach risk | Median days |
|---|---|---|---|---|---|---|
| 0.01 lot, 5×ATR | 0.86% | 73.9% | 84.1% | **62.1%** | 7.5% | 438 |
| 0.01 lot, 8×ATR | 1.38% | 77.5% | 83.0% | **64.3%** | 20.3% | 235 |
| 0.02 lot, 5×ATR | 1.73% | 75.2% | 80.4% | 60.5% | 24.4% | 162 |
| 0.02 lot, 8×ATR | 2.76% | 64.8% | 71.9% | 46.6% | 35.2% | 69 |

1-Step Flex is worse at every risk level (63% at 1% risk, 30% breach risk) — the 6%
max loss is too tight for a 48-hour hold.

**Zero is the wrong plan for this strategy.** Its 5% *trailing* drawdown at 1% risk
gives a **10.6%** chance of surviving 500 days. At 0.5% risk it is 73% — but 0.5%
is not achievable on a $5,000 gold account, per above. Do not buy Zero for this.

## Recommendation

**2-Step Standard, $5,000, 0.01 lot with the 5×ATR stop (0.86% per trade).**
62% to pass both phases, only 7.5% chance of blowing phase 1, median ~438 trading
days. The 8×ATR variant passes marginally more often (64%) and nearly three times
faster, but at 2.7× the breach risk — on a paid challenge the slower, safer version
is the better buy, since a breach costs the fee and a slow pass costs only time.

**Everything above still rests on §7.** The edge is probably gold's uptrend rather
than an inefficiency. A 62% pass probability is real arithmetic applied to a
strategy whose edge may not persist. Forward-test on their free trial or a demo
before paying, and re-run §5 on your own broker's data first.
