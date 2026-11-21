"""
run_all.py - runs the whole pipeline end to end.

    python run_all.py            # uses cached downloads in data/raw/ where available
    python run_all.py --refresh  # re-downloads holdings and prices
"""
import os
import sys

from src import load_data
from src.banks import compare_banks
from src.load_data import ROOT, XBAL_CSV_URL, FX_TICKER
from src.report import make_charts, write_report
from src.risk import run_risk

REPORT_DIR = os.path.join(ROOT, "reports")


def sanity_checks(res):
    """Hard checks must hold; exception rates are reported as warnings only."""
    errors, notes = [], []
    cur = res["current"].set_index("confidence")
    if not cur.loc[0.99, "var_pct"] > cur.loc[0.95, "var_pct"]:
        errors.append("99% VaR is not greater than 95% VaR")
    for a in (0.95, 0.99):
        if cur.loc[a, "es_pct"] < cur.loc[a, "var_pct"]:
            errors.append(f"ES < VaR at {a:.0%}")
    stress = res["stress_summary"].set_index("scenario")["portfolio_return"]
    for name in stress.index:
        if ("GFC" in name or "COVID" in name) and stress[name] >= 0:
            errors.append(f"Stress scenario '{name}' is not a loss")

    # Exception rates: report honestly, never tune the model to hit a target.
    for _, b in res["backtest"].iterrows():
        exp = 1 - b["confidence"]
        if b["kupiec_result"] == "REJECT":
            notes.append(f"Backtest {b['sample']} {b['confidence']:.0%}: {b['exceptions']} exceptions "
                         f"in {b['days']} days ({b['exception_rate']:.2%} vs {exp:.0%} expected) - "
                         f"Kupiec REJECTS at 5% (p={b['kupiec_p_value']:.4f}). Likely cause: fat tails "
                         f"and volatility clustering that a 250-day equal-weighted window reacts to slowly.")
    return errors, notes


def main():
    refresh = "--refresh" in sys.argv
    os.makedirs(REPORT_DIR, exist_ok=True)

    print("\n[1/4] Loading data into SQLite")
    conn = load_data.load_all(refresh=refresh)

    print("\n[2/4] Calculating returns, VaR/ES, backtest and stress tests")
    res = run_risk(conn)
    errors, notes = sanity_checks(res)
    for n in notes:
        load_data.warn(n)

    print("\n[3/4] Big Six bank comparison")
    bank = compare_banks(conn, res["current"], REPORT_DIR)
    print(f"  {bank['summary_line']}")
    if bank["status"] == "pending data":
        load_data.warn(bank["summary_line"])
    elif bank["missing"]:
        load_data.warn(f"Big Six: no figures yet for {', '.join(bank['missing'])} "
                       f"(left blank in data/raw/big6_var.csv)")

    print("\n[4/4] Writing report")
    charts = make_charts(res, REPORT_DIR)
    holdings = res["holdings"]
    proxies = holdings.dropna(subset=["proxy_ticker"])
    sources = [
        ("XBAL holdings", "iShares Canada (BlackRock) holdings CSV",
         f"As of {holdings['as_of_date'].iloc[0]}; {XBAL_CSV_URL}"),
        ("ETF prices", "Yahoo Finance via yfinance (auto-adjusted close)",
         ", ".join(holdings["yf_ticker"])),
        ("Proxy prices", "Yahoo Finance via yfinance (auto-adjusted close)",
         "; ".join(f"{p.ticker} <- {p.proxy_ticker} before {p.holding_first_date}"
                   for p in proxies.itertuples()) or "none"),
        ("XBAL.TO fund price (reference)", "Yahoo Finance via yfinance",
         "Only used to compare stress results with what the fund actually did"),
        ("USD/CAD", "Yahoo Finance via yfinance", f"{FX_TICKER} (CAD per USD)"),
        ("Bond yields (context)", "FRED", "IRLTLT01CAM156N, DGS10 - only if FRED_API_KEY is set"),
        ("Big Six trading VaR", "Manual: banks' quarterly reports",
         "data/raw/big6_var.csv (source_document and page per row)"),
        ("Database", "SQLite", "data/risk.db (tables: holdings, proxies, prices, fx, yields, "
                               "big6_var, var_history, backtest, stress_results, stress_detail)"),
    ]
    xlsx = write_report(res, bank, load_data.WARNINGS, sources,
                        os.path.join(REPORT_DIR, "risk_report.xlsx"), charts)
    conn.close()

    # ------------------------------------------------------------ console summary
    cur = res["current"]
    print("\n" + "=" * 70)
    print(f"RESULTS - $100M XBAL portfolio, holdings as of {holdings['as_of_date'].iloc[0]}")
    print("=" * 70)
    for _, c in cur.iterrows():
        print(f"  {c['confidence']:.0%} 1-day VaR: {c['var_pct']:.2%} (${c['var_cad']:,.0f})   "
              f"ES: {c['es_pct']:.2%} (${c['es_cad']:,.0f})")
    print("\n  Backtest:")
    for _, b in res["backtest"].iterrows():
        zone = f"  Basel zone: {b['basel_zone']}" if b["basel_zone"] else ""
        print(f"    {b['sample']:<14} {b['confidence']:.0%}: {b['exceptions']:>3} exceptions / "
              f"{b['days']} days ({b['exception_rate']:.2%}), Kupiec {b['kupiec_result']} "
              f"(p={b['kupiec_p_value']:.4f}){zone}")
    print("\n  Stress tests:")
    for _, s in res["stress_summary"].iterrows():
        print(f"    {s['scenario']:<36} {s['portfolio_return']:>7.2%}  ${s['pnl_cad']:>14,.0f}   "
              f"(actual XBAL.TO: {s['actual_xbal_return']:.2%})")
    print(f"\n  Report: {xlsx}")
    print(f"  Charts: {', '.join(os.path.basename(p) for p in charts.values())}"
          + (", big6_var.png" if bank.get("chart") else ""))
    if load_data.WARNINGS:
        print(f"\n  {len(load_data.WARNINGS)} warning(s) - see the Summary sheet.")
    if errors:
        print("\nSANITY CHECK FAILED:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print("\n  Sanity checks passed: VaR99 > VaR95, ES >= VaR, GFC/COVID stress are losses.")


if __name__ == "__main__":
    main()
