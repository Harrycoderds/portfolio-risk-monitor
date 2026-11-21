# Portfolio Risk Monitor

A small Python + SQLite tool that measures the daily market risk of a real Canadian multi-asset portfolio: the holdings of **iShares Core Balanced ETF Portfolio (XBAL)**, scaled to $100M CAD.

## Problem

A risk manager needs to answer three questions every day:
1. How much could we lose tomorrow? (**VaR**, **Expected Shortfall**)
2. Was yesterday's model any good? (**backtesting**: Kupiec test, Basel traffic light)
3. What happens if a past crisis repeats? (**stress testing**)

This project answers all three for XBAL using free data, and puts the results in one Excel report.

## What it does

`python run_all.py` runs these steps in order:

1. Downloads XBAL's holdings from iShares Canada, daily prices from Yahoo Finance, and USD/CAD, all cached in `data/raw/`.
2. Loads everything into SQLite (`data/risk.db`) and reads it back with SQL joins for the calculations.
3. Computes 1-day historical VaR and ES at 95% and 99% on a rolling 250-day window.
4. Backtests VaR from 2008 to today: exception counts, the Kupiec POF test, and the Basel traffic light.
5. Runs three historical stress tests: 2008 GFC, the 2020 COVID crash and the 2022 rate shock.
6. Compares against the Big Six banks' disclosed trading VaR, once you have filled in `data/raw/big6_var.csv`.
7. Writes `reports/risk_report.xlsx` (8 sheets, breaches in red) and PNG charts.

## Data

| Data | Source |
|---|---|
| XBAL holdings (as of 2026-11-19) | iShares Canada holdings CSV (downloaded automatically) |
| ETF prices | Yahoo Finance via `yfinance`, adjusted close (dividends reinvested) |
| USD/CAD | Yahoo Finance `CAD=X` |
| Bond yields (optional, context only) | FRED, if `FRED_API_KEY` is set in `.env` |
| Big Six trading VaR | You fill it in by hand from the banks' quarterly reports (`data/raw/big6_var.csv`) |

XBAL holds 8 ETFs, 99.81% of the fund. The remaining 0.19% is cash and FX forwards, which are dropped; weights are renormalised to 100%.

| ETF | What it is | Weight | Trades in |
|---|---|---|---|
| XBB | Canadian universe bonds | 26.65% | CAD |
| ITOT | US total stock market | 26.51% | USD |
| XIC | S&P/TSX Capped Composite | 14.79% | CAD |
| XEF | MSCI EAFE IMI (developed ex-N. America) | 14.78% | CAD |
| XSH | Canadian short-term corporate bonds | 5.85% | CAD |
| GOVT | US Treasuries | 4.22% | USD |
| USIG | US investment-grade corporates | 4.17% | USD |
| XEC | MSCI Emerging Markets | 3.03% | CAD |

### Proxies

Four ETFs launched after 2007. Before each one's first trading day, the return of a long-history proxy is used instead. From that day on, the ETF's own returns are used.

| Holding | Own history starts | Proxy before that | Why |
|---|---|---|---|
| XEF | 2013-04-15 | EFA (USD, converted to CAD) | Same MSCI EAFE exposure |
| XEC | 2013-04-15 | EEM (USD, converted to CAD) | Same MSCI EM exposure |
| XSH | 2011-09-21 | XSB.TO | Canadian short-term bonds (includes governments, not only corporates) |
| GOVT | 2012-02-24 | IEF (USD, converted to CAD) | US Treasuries (7–10y, a bit longer duration than GOVT) |

The candidate proxy map is in `data/raw/proxies.csv`. The code uses a proxy only when a holding's history doesn't reach back to 2007.

## Methods and assumptions

- **Portfolio:** $100M CAD, today's XBAL weights held static over the whole history.
- **Returns:** daily simple returns in **CAD**. USD-listed ETFs are converted with USD/CAD, so their return is (1 + r_USD)(1 + r_FX) − 1. Currency risk is included, as it would be for a Canadian investor.
- **Holidays:** TSX and US holidays differ. A closed market carries its last price forward, so that holding shows a 0% return that day.
- **Historical VaR:** the loss exceeded on only 1% (or 5%) of the last 250 days, i.e. the negative of the 1st (5th) percentile. It uses the same interpolation as Excel's `PERCENTILE.INC`.
- **Expected Shortfall:** the average of the returns at or beyond that percentile.
- **Backtest:** each day's VaR uses only the **previous** 250 days (no look-ahead) and is compared with that day's actual return. An exception is a loss bigger than the VaR forecast.
- **Kupiec POF test:** a likelihood-ratio test of whether the exception rate equals 1% (or 5%). χ²(1), 5% significance.
- **Basel traffic light:** 99% exceptions in the latest 250 days. Green 0–4, yellow 5–9, red 10+.
- **Stress tests:** the close-to-close return of each holding (or its proxy) over the window, applied to today's weights. GFC uses 2008-08-29 → 2008-11-28, COVID 2020-02-19 → 2020-03-23 and the rate shock 2021-12-31 → 2022-10-31. The fund's actual XBAL.TO return over the same window is shown for reference.

## Key results (run of 2026-11-20)

**Current 1-day risk** (window 2025-12-01 to 2026-11-20):

| | VaR | ES |
|---|---|---|
| 95% | 0.93% ($928,740) | 1.25% ($1,253,018) |
| 99% | 1.58% ($1,575,512) | 1.75% ($1,751,393) |

**Backtest:**

| Sample | Level | Exceptions | Rate | Kupiec (5%) |
|---|---|---|---|---|
| 2008-01-03 to 2026-11-20 (4,838 days) | 95% | 280 | 5.79% | **Reject** (p = 0.014) |
| 2008-01-03 to 2026-11-20 (4,838 days) | 99% | 77 | 1.59% | **Reject** (p = 0.0001) |
| Last 250 days | 95% | 15 | 6.00% | Accept (p = 0.48) |
| Last 250 days | 99% | 3 | 1.20% | Accept (p = 0.76) |

Basel zone for the last 250 days: **GREEN** (3 exceptions).

**Why does the full-history test fail?** The 99% VaR had 77 exceptions against 48 expected, and they come in clusters: 12 in 2008, 10 in 2011, 9 in 2022, 8 in 2020. Two reasons:

1. **Volatility clustering.** A 250-day equal-weighted window is slow to react. When a calm year turns volatile (Feb–Mar 2020), VaR is still based on calm data, so the first weeks of a crisis produce a burst of exceptions. In the 2020 chart, 99% VaR was 0.84% on 2020-02-21 and only reached its peak of 4.81% on 2020-03-19, *after* the worst days. The worst day was −7.89% on 2020-03-16.
2. **Fat tails.** 250 days hold only about 2.5 observations beyond the 1st percentile, so the 99% estimate is noisy.

The model was not tuned to pass. This is a known weakness of plain historical-simulation VaR, and the reason banks also use stressed VaR and ES.

**Stress tests** ($100M, today's weights):

| Scenario | Our portfolio | Actual XBAL.TO (reference) |
|---|---|---|
| 2008 GFC (Sep–Nov 2008) | −13.38% (−$13.38M) | −17.69% |
| COVID crash (Feb 19–Mar 23 2020) | −19.74% (−$19.74M) | −20.93% |
| 2022 rate shock (Jan–Oct 2022) | −12.47% (−$12.47M) | −12.88% |

- **GFC.** USD/CAD rose 15.7% in the window. That turned US Treasuries (IEF proxy) into a +23.4% CAD gain and cushioned US equities (ITOT −19.5% in CAD, against XIC −32.9%). The limitations section explains why the actual fund lost more.
- **2022.** Bonds and stocks fell together (XBB −12.8%, ITOT −13.3%), so the usual bond hedge didn't work.

## Limitations

- **Static weights, and the GFC gap:** today's weights are applied to the past. That is why the GFC result (−13.4%) differs from XBAL's actual 2008 move (−17.7%) while COVID and 2022 match within about 1 point. XBAL changed its mandate in December 2018. Regressing its weekly returns shows that before then it behaved like a mostly Canadian fund: beta 0.33 to XIC and only 0.09 to US and international equity combined, against 0.19 and 0.39 since 2019, matching today's weights with R² 0.95. So in 2008 the real fund took the full TSX drop (−32.9%) without the USD cushion our portfolio gets. The 2008 fit is loose (R² 0.61), and XBAL.TO traded thinly then (33 of 63 days in the window with an unchanged price), so its −17.7% is only a rough benchmark. To reproduce: `python -m src.gfc_gap`.
- **Proxies:** proxies are not exact. XSB includes government bonds as well as the corporates XSH holds.
- **GOVT → IEF duration mismatch:** before 2012, GOVT (modified duration 5.5 years per the holdings file) is proxied by IEF, which holds only 7–10 year Treasuries and so has a longer duration. IEF therefore overstates how much GOVT would have gained when yields fell: in the GFC, IEF's +23.4% CAD return added +0.99% to the portfolio. The effect is small at a 4.2% weight. Even if GOVT had gained only half as much, the GFC loss would be about 0.5 points larger.
- **Market prices, not NAV:** bond ETFs traded at discounts to NAV in March 2020 (e.g. XSH −9.6%), and that shows up in the returns.
- **Model choices:** historical VaR on 250 equal-weighted days reacts slowly to volatility changes (see the backtest), and VaR is 1-day only, with no √t scaling or liquidity horizon.
- **Holidays:** carrying prices over holidays creates some 0% days and shifts a move to the next day.
- **Yahoo data:** adjusted prices can be revised slightly, so re-downloading can change results by a few dollars.
- **Big Six:** the comparison is context only. Banks report VaR on whole trading books with their own models.

## How to run

```bash
cd ~/Desktop/"Portfolio Risk Monitor"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run_all.py              # uses cached data in data/raw/ if present
python run_all.py --refresh    # re-download holdings and prices
python -m unittest tests/test_risk.py
python -m src.gfc_gap          # optional: GFC gap check (see Limitations)
```

After setup, the daily one-liner is:

```bash
cd ~/Desktop/"Portfolio Risk Monitor" && source .venv/bin/activate && python run_all.py
```

Optional: copy `.env.example` to `.env` and add a free FRED API key to load Canada and US 10Y yields.

**Big Six comparison:** fill in `data/raw/big6_var.csv`, one row per bank per quarter, from each bank's quarterly report (market risk / trading VaR table), and fill the `source_document` and `page` columns. Then re-run. While it's empty, the report says "pending data".

## Outputs

- `reports/risk_report.xlsx` has 8 sheets: Summary, Charts, Holdings, VaR_History (exception days in red), Backtest, Stress, Big6, Data_Sources.
- Charts: `reports/var_backtest.png`, `reports/return_distribution.png`, `reports/stress_tests.png` and `reports/big6_var.png` (once the Big Six data is filled in).
- Database: `data/risk.db`, which you can open with any SQLite viewer.
- Screenshots for GitHub/LinkedIn go in `docs/screenshots/`.

## Project structure

```
data/raw/           cached downloads + proxies.csv + big6_var.csv (manual)
data/risk.db        SQLite database (rebuilt each run)
src/load_data.py    downloads -> SQLite
src/risk.py         returns, VaR, ES, backtest, stress tests
src/banks.py        Big Six comparison
src/report.py       Excel report + charts
src/gfc_gap.py      one-off check: XBAL's pre-2018 exposures vs today's
tests/test_risk.py  hand-checked tests (VaR/ES, Kupiec, Basel, no look-ahead)
run_all.py          runs everything
```
