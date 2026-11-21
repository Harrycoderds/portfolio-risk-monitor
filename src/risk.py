"""
risk.py - returns, VaR, Expected Shortfall, backtesting and stress tests.

All inputs are read from SQLite (data/risk.db). The pure functions at the top
(hist_var, expected_shortfall, kupiec_pof, basel_zone) take plain arrays so they
are easy to test against hand-calculated answers.
"""
import numpy as np
import pandas as pd
from scipy.special import xlogy
from scipy.stats import chi2

PORTFOLIO_VALUE = 100_000_000   # $100M CAD, current XBAL weights held static
WINDOW = 250                    # ~1 trading year of history for each VaR estimate
LEVELS = [0.95, 0.99]

# Close-to-close windows: P&L = price at end_close / price at start_close - 1
STRESS_SCENARIOS = [
    # name,                         start close,  end close
    ("2008 GFC (Sep-Nov 2008)",      "2008-08-29", "2008-11-28"),
    ("COVID crash (Feb 19-Mar 23 2020)", "2020-02-19", "2020-03-23"),
    ("2022 rate shock (Jan-Oct 2022)", "2021-12-31", "2022-10-31"),
]


# ======================================================================
# Pure finance functions
# ======================================================================
def hist_var(returns, alpha):
    """
    Historical VaR at confidence alpha, as a positive loss fraction.
    It is the loss that is exceeded on only (1 - alpha) of days in the sample:
    VaR = -(the (1-alpha) percentile of returns). Uses linear interpolation,
    the same as Excel's PERCENTILE.INC.
    """
    r = np.asarray(returns, dtype=float)
    return -np.quantile(r, 1 - alpha)


def expected_shortfall(returns, alpha):
    """
    Expected Shortfall (a.k.a. CVaR): the average loss on the days that are
    at or beyond the VaR cut-off. It answers "when it is bad, how bad on average?"
    """
    r = np.asarray(returns, dtype=float)
    cutoff = np.quantile(r, 1 - alpha)
    return -r[r <= cutoff].mean()


def kupiec_pof(n, x, p):
    """
    Kupiec proportion-of-failures test.
    n = days tested, x = VaR exceptions observed, p = expected exception rate (1 - alpha).
    H0: the true exception rate is p. LR compares the likelihood of the data under p
    with the likelihood under the observed rate x/n. LR ~ chi-squared(1 d.o.f.).
    Reject H0 (model badly calibrated) if p-value < 5%.
    """
    phat = x / n
    log_l0 = xlogy(n - x, 1 - p) + xlogy(x, p)          # likelihood if model is right
    log_l1 = xlogy(n - x, 1 - phat) + xlogy(x, phat)    # likelihood at observed rate
    lr = -2 * (log_l0 - log_l1)
    p_value = 1 - chi2.cdf(lr, df=1)
    return {"lr_stat": lr, "p_value": p_value, "reject_5pct": bool(p_value < 0.05)}


def basel_zone(exceptions):
    """Basel traffic light for 250 days of 99% VaR: green 0-4, yellow 5-9, red 10+."""
    if exceptions <= 4:
        return "GREEN"
    if exceptions <= 9:
        return "YELLOW"
    return "RED"


def rolling_var_es(returns, alpha, window=WINDOW):
    """
    VaR and ES for each day t estimated ONLY from the `window` returns before t
    (days t-window ... t-1). Day t's own return is never used to forecast day t,
    so the backtest has no look-ahead.
    """
    r = np.asarray(returns, dtype=float)
    var = np.full(len(r), np.nan)
    es = np.full(len(r), np.nan)
    for t in range(window, len(r)):
        past = r[t - window:t]
        var[t] = hist_var(past, alpha)
        es[t] = expected_shortfall(past, alpha)
    return pd.Series(var, index=getattr(returns, "index", None)), \
        pd.Series(es, index=getattr(returns, "index", None))


# ======================================================================
# Reading the data back out of SQLite
# ======================================================================
def load_holdings(conn):
    """Holdings joined to their proxy (if any)."""
    sql = """
        SELECT h.ticker, h.yf_ticker, h.name, h.asset_class, h.market_currency,
               h.weight, h.as_of_date,
               x.proxy_ticker, x.proxy_currency, x.holding_first_date
        FROM holdings h
        LEFT JOIN proxies x ON x.holding_ticker = h.ticker
        ORDER BY h.weight DESC
    """
    return pd.read_sql(sql, conn)


def load_holding_returns(conn):
    """
    Daily simple returns in CAD for each holding, with proxy history spliced in
    before the holding's own first price. Returns a DataFrame (dates x tickers).
    """
    holdings = load_holdings(conn)

    # Every price row, with the USD/CAD rate for the same date attached.
    sql = """
        SELECT p.date, p.ticker, p.close, f.usdcad
        FROM prices p
        LEFT JOIN fx f ON f.date = p.date
        WHERE p.ticker IN (SELECT yf_ticker FROM holdings
                           UNION
                           SELECT proxy_ticker FROM proxies)
        ORDER BY p.date
    """
    px = pd.read_sql(sql, conn, parse_dates=["date"])
    close = px.pivot(index="date", columns="ticker", values="close")
    fx = pd.read_sql("SELECT date, usdcad FROM fx ORDER BY date", conn,
                     parse_dates=["date"]).set_index("date")["usdcad"]

    # TSX and US exchanges have different holidays: carry the last price forward
    # (so a market that was closed shows a 0% return that day).
    close = close.ffill(limit=5)
    fx = fx.reindex(close.index.union(fx.index)).ffill().reindex(close.index)

    def cad_returns(ticker, currency):
        # A USD-listed ETF held by a Canadian investor: convert price to CAD first,
        # so the return includes the USD/CAD move: (1 + r_usd)(1 + r_fx) - 1.
        p = close[ticker]
        if currency == "USD":
            p = p * fx
        return p.pct_change(fill_method=None)

    out = {}
    for _, h in holdings.iterrows():
        own = cad_returns(h["yf_ticker"], h["market_currency"])
        if pd.notna(h["proxy_ticker"]):
            proxy = cad_returns(h["proxy_ticker"], h["proxy_currency"])
            # use the proxy only on dates before the holding has its own return
            own = own.where(own.notna(), proxy.where(own.index <= own.first_valid_index()))
        out[h["ticker"]] = own
    rets = pd.DataFrame(out).dropna()     # keep days where every holding has a return
    return rets, holdings


def portfolio_returns(holding_returns, holdings):
    """Static weights: r_portfolio = sum(weight_i * r_i) every day."""
    w = holdings.set_index("ticker")["weight"].reindex(holding_returns.columns)
    return (holding_returns * w).sum(axis=1).rename("portfolio")


# ======================================================================
# Analyses
# ======================================================================
def current_var_es(port):
    """VaR/ES for tomorrow, from the most recent 250 days."""
    last = port.iloc[-WINDOW:]
    rows = []
    for a in LEVELS:
        v, e = hist_var(last, a), expected_shortfall(last, a)
        rows.append({"confidence": a, "var_pct": v, "es_pct": e,
                     "var_cad": v * PORTFOLIO_VALUE, "es_cad": e * PORTFOLIO_VALUE,
                     "window_start": last.index[0].date(), "window_end": last.index[-1].date()})
    return pd.DataFrame(rows)


def var_history(port):
    """Daily table: return, the VaR/ES that was forecast for that day, and exceptions."""
    df = pd.DataFrame({"return": port})
    for a in LEVELS:
        tag = int(a * 100)
        df[f"var{tag}"], df[f"es{tag}"] = rolling_var_es(port, a)
        # Exception: the realised loss was bigger than the VaR forecast for that day.
        df[f"exception{tag}"] = (-df["return"] > df[f"var{tag}"]).astype(int)
    df = df.dropna(subset=["var95"])      # first 250 days have no forecast
    df["pnl_cad"] = df["return"] * PORTFOLIO_VALUE
    return df


def backtest(hist):
    """Exception counts, Kupiec test (full sample + last 250 days), Basel zone."""
    rows = []
    for sample, data in [("Full history", hist), ("Last 250 days", hist.iloc[-WINDOW:])]:
        for a in LEVELS:
            tag = int(a * 100)
            n = len(data)
            x = int(data[f"exception{tag}"].sum())
            k = kupiec_pof(n, x, 1 - a)
            rows.append({
                "sample": sample, "confidence": a,
                "start": data.index[0].date(), "end": data.index[-1].date(),
                "days": n, "exceptions": x, "expected": n * (1 - a),
                "exception_rate": x / n, "kupiec_lr": k["lr_stat"],
                "kupiec_p_value": k["p_value"],
                "kupiec_result": "REJECT" if k["reject_5pct"] else "Accept",
                "basel_zone": basel_zone(x) if (a == 0.99 and sample == "Last 250 days") else "",
            })
    return pd.DataFrame(rows)


def stress_test(holding_returns, holdings):
    """
    Historical stress: each holding's (or proxy's) cumulative return over the
    scenario window, applied to TODAY's weights on $100M.
    """
    w = holdings.set_index("ticker")["weight"]
    rows = []
    for name, start, end in STRESS_SCENARIOS:
        window = holding_returns[(holding_returns.index > start) & (holding_returns.index <= end)]
        cum = (1 + window).prod() - 1                     # compounded return per holding
        for t in holding_returns.columns:
            proxy = holdings.set_index("ticker").loc[t, "proxy_ticker"]
            first = holdings.set_index("ticker").loc[t, "holding_first_date"]
            used_proxy = pd.notna(proxy) and pd.Timestamp(first) > pd.Timestamp(start)
            rows.append({
                "scenario": name, "start_close": start, "end_close": end,
                "trading_days": len(window), "ticker": t, "weight": w[t],
                "source": proxy if used_proxy else t,
                "holding_return": cum[t], "contribution": w[t] * cum[t],
                "pnl_cad": w[t] * cum[t] * PORTFOLIO_VALUE,
            })
    detail = pd.DataFrame(rows)
    summary = (detail.groupby(["scenario", "start_close", "end_close", "trading_days"], sort=False)
               .agg(portfolio_return=("contribution", "sum"), pnl_cad=("pnl_cad", "sum"))
               .reset_index())
    return summary, detail


def actual_fund_returns(conn):
    """
    Reference only: what XBAL.TO itself actually returned over each scenario.
    It differs from our stress result because the fund's weights back then were
    not today's weights (and its price includes fees / trading at a premium or discount).
    """
    px = pd.read_sql("SELECT date, close FROM prices WHERE ticker = 'XBAL.TO' ORDER BY date",
                     conn, parse_dates=["date"]).set_index("date")["close"]
    out = []
    for _, start, end in STRESS_SCENARIOS:
        p0, p1 = px[:start], px[:end]
        out.append(p1.iloc[-1] / p0.iloc[-1] - 1 if len(p0) and len(p1) else np.nan)
    return out


def run_risk(conn):
    """Run every analysis, save the results back into SQLite, return them as a dict."""
    rets, holdings = load_holding_returns(conn)
    port = portfolio_returns(rets, holdings)
    hist = var_history(port)
    results = {
        "holdings": holdings,
        "holding_returns": rets,
        "portfolio_returns": port,
        "current": current_var_es(port),
        "history": hist,
        "backtest": backtest(hist),
    }
    results["stress_summary"], results["stress_detail"] = stress_test(rets, holdings)
    results["stress_summary"]["actual_xbal_return"] = actual_fund_returns(conn)

    out = hist.copy()
    out.index = out.index.strftime("%Y-%m-%d")
    out.rename_axis("date").reset_index().to_sql("var_history", conn, if_exists="replace", index=False)
    results["backtest"].astype({"start": str, "end": str}).to_sql(
        "backtest", conn, if_exists="replace", index=False)
    results["stress_summary"].to_sql("stress_results", conn, if_exists="replace", index=False)
    results["stress_detail"].to_sql("stress_detail", conn, if_exists="replace", index=False)
    conn.commit()
    return results
