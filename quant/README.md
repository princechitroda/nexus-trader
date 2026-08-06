# XAUUSD Strategy Research Lab

A backtesting and validation harness built to answer one question honestly:

> **Which XAUUSD strategy is profitable enough, and stable enough, to pass a funded
> account challenge?**

**Round 1 is done and the answer was "none of them" — see [FINDINGS.md](FINDINGS.md).**
Six strategies were tested on ten years of real XAUUSD; the best out-of-sample result
was +1.96% over 7.5 years at t = 0.31. That write-up also contains the genuinely
useful part: what the edge scan measured about how gold actually behaves.

---

## Read this first: what this package will and will not tell you

Anyone who says "strategy X is profitable on gold" without showing you walk-forward
results, costs and a trade-level distribution is selling something. Gold's edges are
real but thin, regime-dependent, and mostly eaten by spread. The honest position is:

* **Candidate strategies are hypotheses, not answers.** Six are implemented here,
  each with a structural reason to exist (see below). Which ones survive on *your*
  broker's data, at *your* costs, is an empirical question.
* **In-sample results mean nothing.** A profitable backtest over a fixed history is
  the default outcome of trying enough parameters, not evidence of edge.
* **The pass/fail bar is drawdown, not profit.** A funded challenge is not graded on
  return. It is graded on whether your equity touches a tripwire before it touches a
  target. A 1.5 profit factor system can have a 40% pass rate.

---

## Is the backtester itself trustworthy?

Before trusting any result, the engine validates itself. Run:

```bash
python -m quant.run_research selftest
```

Three layers, all of which currently pass:

| Test | What it proves |
|---|---|
| **Causality** | Signals are recomputed on truncated history and must be bit-identical. Catches look-ahead: unshifted higher-timeframe series, centred windows, whole-frame groupbys. |
| **Null** | On synthetic data with realistic volatility clustering and session seasonality but *no directional structure*, every strategy must lose roughly the cost of trading. A profit here means a bug. |
| **Positive control** | A known Asian-range-continuation effect is injected into synthetic data; `LondonORB` must find it (it does: +0.95R, t=27.9). Proves the engine isn't so pessimistic it would miss a real edge. |

A backtester that has never been tested this way is a random number generator with a
progress bar.

---

## Getting real data — the one thing you have to do

Round 1 used a public 2012–2022 dataset (see the bottom of this file). **It ends in
March 2022**, so for anything recent you need to supply data — and you should want to:
the right data is **your own broker's**, because their spread and their candles are
what you will actually trade.

### Export from MT5 (free, 5 minutes, best option)

1. MT5 → **View → Symbols** (Ctrl+U) → find `XAUUSD` → **Bars** tab.
2. Set the timeframe to **M15** and a date range of **at least 3 years** (5 is better
   — you want a rate-hiking regime, a chopping regime and a trending regime in there).
3. **Request** → then **Export Bars** → save as CSV.
4. Drop it in `quant/data/` and run the commands below.

Alternatively: MT5 → **Tools → Options → Charts** → set "Max bars in chart" to
unlimited, open an M15 gold chart, scroll back to load history, then
**File → Save As** the chart data.

### The timezone trap

MT5 timestamps are in **broker server time**, not UTC — usually UTC+2 or UTC+3 with
DST. Every session strategy here keys off UTC hours, so feeding server-time bars
silently moves the London open by two or three hours and quietly destroys the result.
Pass your broker's offset:

```bash
python -m quant.run_research screen --data quant/data/XAUUSD_M15.csv --server-tz 3
```

Check `Market Watch → Specification` or just look at what hour the daily candle opens.

---

## Usage

```bash
pip install pandas numpy

# 1. validate the engine (no data needed)
python -m quant.run_research selftest

# 2. rank all six strategies on your data
python -m quant.run_research screen \
    --data quant/data/XAUUSD_M15.csv --server-tz 3 \
    --risk 0.5 --spread 0.30 --commission 7

# 3. the test that actually matters — walk-forward the survivor
python -m quant.run_research wfo \
    --data quant/data/XAUUSD_M15.csv --server-tz 3 \
    --strategy london_orb --train 12 --test 3
```

Match `--spread` and `--commission` to your prop firm's gold pricing. Many firms
quote 25-35 cents on gold plus $6-8 per lot round turn; some are far worse. This
single number decides several of these strategies.

---

## The six candidates

Each exists for a structural reason, not because it backtested well.

| Strategy | Thesis | Expected profile |
|---|---|---|
| `london_orb` | Asian session compresses on thin volume; London arrives with real volume and runs the stops parked either side. A liquidity mechanism, not a chart pattern. | ~40-45% win rate, 2R targets, 1 trade/day |
| `asian_fade` | The deliberate opposite. Thin Asian book, no scheduled news, no macro flow — the one window where fading gold is defensible. Gated to compressed volatility regimes. | High win rate, small wins, ugly tails |
| `trend_pullback` | Gold's real money is in multi-day macro runs (real yields, DXY). H4 trend filter, M15 pullback entry, no target — ATR trailing only. | Low win rate (~35%), fat right tail, painful drawdowns |
| `donchian` | 40-year-old textbook breakout as a **control**. If this beats the clever systems, the clever systems are fitting. | Benchmark |
| `sweep_reversal` | Mechanical stop-hunt: price runs the prior day's high/low, fails, closes back inside. Stop sits past proven failure, so losses are tight. | Tight loss distribution — structurally good for a drawdown-limited account |
| `asia_break_momentum` | Built *from* the edge scan, not from intuition: the Asian-range break edge is zero intraday and accrues over 8–24h, so this holds for a day on H1 with a wide stop. | The best of the six — and still not fundable; see FINDINGS.md |

`asian_fade` and `london_orb` are near-opposites by design. If both look profitable on
the same data, at least one is fitting noise, and you have learned something.

---

## Reading the output

The screen prints these; the thresholds are where to be ruthless.

| Metric | Reject if |
|---|---|
| `t` (t-stat of mean R) | **< 2.0** — indistinguishable from luck, whatever the return |
| `top_trade` | **> 0.3** — most of the profit is one trade; that's a lottery ticket |
| fold efficiency (WFO) | **< 60%** of folds profitable — the edge isn't stable |
| `MC_pass` | **< 50%** — you'd be paying a challenge fee on a coin flip |
| parameter stability (WFO) | best params jump around between folds — no real edge, just fitting |

### The risk sizing curve

The most practically useful output. Pass probability as a function of risk per trade
is almost always **humped**: too small and you time out, too large and the daily-loss
rule takes you out before the edge compounds. The peak is usually *lower* than people
put it — frequently 0.25-0.5% per trade, not the 1-2% most retail traders use.

### Monte Carlo

Your backtest shows one ordering of your trades. You will not get that ordering. The
simulator resamples whole trading days — the pair (close-to-close return, worst
intraday floor) — with a **stationary block bootstrap**, because losing days cluster
and an i.i.d. resample understates the odds of the consecutive-loss run that actually
breaches you.

Daily loss is measured on **equity including floating positions**, against the balance
at the daily reset. A trade that is 5.1% underwater at 14:00 and recovers by 17:00 is
still a breach. This is the rule that catches people whose backtest "never had a
losing day".

---

## Cost realism

Defaults are deliberately pessimistic:

* Signals are generated on bar close and filled at the **next bar's open**. Never the same bar.
* If a bar's range contains both stop and target, **the stop filled first**.
* Spread is paid once, on the correct side. A short's stop is triggered by the **ask**,
  which fires `spread` earlier than a bid chart shows — gold spreads widen to 40-60
  cents around rollover, and ignoring this is worth several percent a year of
  imaginary profit.
* Stops slip more than market orders (12 cents vs 3 by default).
* Commission counts against the risk budget when sizing.
* Positions flatten before the weekend — gold gaps.

`ZeroCosts` exists to measure how much of an edge is real: if profit factor collapses
from 1.8 to 0.9 when you switch it off, it was never a strategy.

---

## Layout

```
quant/
├── data/
│   ├── loader.py      MT5 / Dukascopy / generic CSV → normalised UTC OHLC
│   ├── sessions.py    session windows, session ranges, prior-day levels
│   └── synth.py       synthetic generator for the null + positive control tests
├── engine/
│   ├── backtest.py    event-driven engine, sizing, stop management
│   ├── costs.py       instrument spec, spread/commission/slippage
│   ├── metrics.py     performance stats incl. overfit detectors
│   ├── propfirm.py    FTMO-style rule simulator
│   ├── montecarlo.py  block-bootstrap pass probability + risk sizing curve
│   └── walkforward.py anchored/rolling WFO
├── strategies/        the five candidates + indicators
└── run_research.py    CLI: selftest | screen | wfo
```

---

## Honest expectations

Two years of discretionary XAUUSD experience is a genuine asset here — you already
know how gold behaves around the London open, how it treats prior-day levels, and how
violently it moves on CPI. That intuition is what generates good hypotheses. This
harness exists to stop that same intuition from talking you into a system that only
worked on the past.

Realistically, expect most of these to fail walk-forward. That is the normal and
correct outcome, and finding out for free is the entire point of building this before
paying a challenge fee. If one survives, the next step is **not** to fund it — it is
to forward-test on demo for 4-6 weeks and confirm the live fill quality matches the
assumptions above.

---

## Getting the 10-year dataset used in FINDINGS.md

The research in `FINDINGS.md` used a public MT5-derived dataset (2012–2022). It is
not committed here — fetch it yourself:

```bash
git clone --depth 1 https://github.com/ejtraderLabs/historical-data /tmp/hist
mkdir -p quant/data/raw && cp /tmp/hist/XAUUSD/XAUUSD{m15,h1}.csv quant/data/raw/
```

Prices ship as integers scaled ×100 (155408 = 1554.08) on an EET/EEST server clock,
so pass `--price-scale 100 --server-tz 2`. Validation of this data against known
gold history, and how the timezone was determined empirically, are in `FINDINGS.md`.

**It ends in March 2022.** Replace it with your own MT5 export for anything recent.
