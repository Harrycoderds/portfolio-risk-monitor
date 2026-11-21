"""
report.py - the Excel risk report and PNG charts.

Everything written here comes from the results of risk.py / banks.py; this file
only formats. Breaches (VaR exceptions, Kupiec rejections, Basel zone) are red.
"""
import os
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from openpyxl.drawing.image import Image  # noqa: E402
from openpyxl.styles import Alignment, Font, PatternFill  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402

from src.banks import _style  # noqa: E402
from src.risk import PORTFOLIO_VALUE, WINDOW  # noqa: E402

BLUE, ORANGE, RED, INK, MUTED = "#2a78d6", "#eb6834", "#d03b3b", "#0b0b0b", "#52514e"

RED_FILL = PatternFill("solid", fgColor="F8C9C9")        # breach rows
DARK_RED_FILL = PatternFill("solid", fgColor="D03B3B")   # REJECT / RED zone
ZONE_FILLS = {"GREEN": PatternFill("solid", fgColor="9BD39B"),
              "YELLOW": PatternFill("solid", fgColor="FAD67A"),
              "RED": DARK_RED_FILL}
HEADER_FILL = PatternFill("solid", fgColor="1C5CAB")
HEADER_FONT = Font(bold=True, color="FFFFFF")
TITLE_FONT = Font(bold=True, size=14)
SECTION_FONT = Font(bold=True, size=12, color="1C5CAB")

PCT, PCT4, CAD, NUM = "0.00%", "0.0000%", '"$"#,##0', "0.00"


# ======================================================================
# Charts
# ======================================================================
def make_charts(results, chart_dir):
    os.makedirs(chart_dir, exist_ok=True)
    paths = {}
    hist = results["history"]

    # 1. Daily P&L vs the 99% VaR forecast, exceptions marked.
    fig, ax = plt.subplots(figsize=(11, 4.8))
    ax.plot(hist.index, hist["return"] * 100, color="#9ec5f4", linewidth=0.6,
            label="Daily portfolio return")
    ax.plot(hist.index, -hist["var99"] * 100, color=BLUE, linewidth=1.4,
            label="-99% VaR (prior 250 days)")
    exc = hist[hist["exception99"] == 1]
    ax.scatter(exc.index, exc["return"] * 100, s=14, color=RED, zorder=3,
               label=f"99% VaR exception ({len(exc)} of {len(hist)} days)")
    ax.axhline(0, color="#999999", linewidth=0.6)
    ax.set_ylabel("Daily return (%)")
    ax.set_title("VaR backtest: XBAL portfolio daily returns vs 99% historical VaR", loc="left")
    ax.legend(loc="lower left", frameon=False, fontsize=9)
    _style(ax)
    ax.grid(axis="x", visible=False)
    ax.grid(axis="y", color="#e5e5e3", linewidth=0.8)
    fig.tight_layout()
    paths["backtest"] = os.path.join(chart_dir, "var_backtest.png")
    fig.savefig(paths["backtest"], dpi=150)
    plt.close(fig)

    # 2. Distribution of the last 250 returns with VaR / ES cut-offs.
    cur = results["current"].set_index("confidence")
    last = results["portfolio_returns"].iloc[-WINDOW:] * 100
    fig, ax = plt.subplots(figsize=(9, 4.8))
    ax.hist(last, bins=40, color="#86b6ef", edgecolor="#fcfcfb", linewidth=0.8)
    for a, style in [(0.95, ":"), (0.99, "--")]:
        ax.axvline(-cur.loc[a, "var_pct"] * 100, color=BLUE, linestyle=style, linewidth=1.6,
                   label=f"{a:.0%} VaR = {cur.loc[a, 'var_pct']:.2%}")
    ax.axvline(-cur.loc[0.99, "es_pct"] * 100, color=ORANGE, linewidth=1.6,
               label=f"99% ES = {cur.loc[0.99, 'es_pct']:.2%}")
    ax.set_xlabel("Daily return (%)")
    ax.set_ylabel("Number of days")
    ax.set_title(f"Last {WINDOW} daily returns ({last.index[0]:%Y-%m-%d} to "
                 f"{last.index[-1]:%Y-%m-%d})", loc="left")
    ax.legend(frameon=False, fontsize=9, loc="upper right")
    ax.yaxis.get_major_locator().set_params(integer=True)
    _style(ax)
    ax.grid(axis="x", visible=False)
    ax.grid(axis="y", color="#e5e5e3", linewidth=0.8)
    fig.tight_layout()
    paths["histogram"] = os.path.join(chart_dir, "return_distribution.png")
    fig.savefig(paths["histogram"], dpi=150)
    plt.close(fig)

    # 3. Stress scenarios ($ P&L on $100M).
    s = results["stress_summary"]
    fig, ax = plt.subplots(figsize=(9, 3.8))
    vals = s["pnl_cad"] / 1e6
    ax.barh(s["scenario"], vals, color=[RED if v < 0 else BLUE for v in vals], height=0.55)
    for y, (v, r) in enumerate(zip(vals, s["portfolio_return"])):
        ax.text(v, y, f" {'-' if v < 0 else ''}${abs(v):,.1f}M ({r:.1%}) ", va="center",
                ha="right" if v < 0 else "left", fontsize=9, color=INK)
    ax.axvline(0, color="#999999", linewidth=0.8)
    ax.invert_yaxis()
    ax.set_xlim(min(vals.min() * 1.45, 0), max(vals.max() * 1.45, 0) + 0.5)
    ax.set_xlabel("P&L on $100M portfolio (CAD millions)")
    ax.set_title("Historical stress tests (today's weights)", loc="left")
    _style(ax)
    fig.tight_layout()
    paths["stress"] = os.path.join(chart_dir, "stress_tests.png")
    fig.savefig(paths["stress"], dpi=150)
    plt.close(fig)
    return paths


# ======================================================================
# Excel helpers
# ======================================================================
def _write_table(ws, df, start_row, formats=None, start_col=1):
    """Write a DataFrame with a styled header. Returns the next free row."""
    formats = formats or {}
    for j, col in enumerate(df.columns):
        c = ws.cell(row=start_row, column=start_col + j, value=col)
        c.fill, c.font = HEADER_FILL, HEADER_FONT
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for i, row in enumerate(df.itertuples(index=False), start=1):
        for j, val in enumerate(row):
            if isinstance(val, float) and pd.isna(val):
                val = None
            elif hasattr(val, "item"):
                val = val.item()                       # numpy -> python
            c = ws.cell(row=start_row + i, column=start_col + j, value=val)
            fmt = formats.get(df.columns[j])
            if hasattr(val, "year") and not fmt:
                fmt = "yyyy-mm-dd"
            if fmt:
                c.number_format = fmt
    return start_row + len(df) + 2


def _autowidth(ws, min_w=9, max_w=60):
    for col in ws.columns:
        width = max((len(str(c.value)) for c in col if c.value is not None), default=0)
        ws.column_dimensions[get_column_letter(col[0].column)].width = max(min_w, min(max_w, width + 2))


def _section(ws, row, text):
    ws.cell(row=row, column=1, value=text).font = SECTION_FONT
    return row + 1


# ======================================================================
# The workbook
# ======================================================================
def write_report(results, bank, warnings, data_sources, path, chart_paths):
    wb = Workbook()
    holdings = results["holdings"]
    hist = results["history"]
    bt = results["backtest"]
    port = results["portfolio_returns"]

    # ------------------------------------------------------------ Summary
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "Portfolio Risk Monitor - XBAL (iShares Core Balanced ETF Portfolio)"
    ws["A1"].font = TITLE_FONT
    info = [
        ("Portfolio value", PORTFOLIO_VALUE, CAD),
        ("Holdings as of", holdings["as_of_date"].iloc[0], None),
        ("Return history", f"{port.index[0]:%Y-%m-%d} to {port.index[-1]:%Y-%m-%d} "
                           f"({len(port):,} trading days)", None),
        ("Method", f"1-day historical simulation, rolling {WINDOW}-day window, "
                   f"static weights, daily simple returns in CAD", None),
        ("Report generated", datetime.now().strftime("%Y-%m-%d %H:%M"), None),
    ]
    r = 3
    for label, val, fmt in info:
        ws.cell(row=r, column=1, value=label).font = Font(bold=True)
        c = ws.cell(row=r, column=2, value=val)
        if fmt:
            c.number_format = fmt
            c.alignment = Alignment(horizontal="left")
        r += 1

    r = _section(ws, r + 1, f"1. Current 1-day VaR and Expected Shortfall (last {WINDOW} days)")
    cur = results["current"].rename(columns={
        "confidence": "Confidence", "var_pct": "VaR %", "es_pct": "ES %",
        "var_cad": "VaR $", "es_cad": "ES $", "window_start": "Window start",
        "window_end": "Window end"})
    r = _write_table(ws, cur, r, {"Confidence": "0%", "VaR %": PCT, "ES %": PCT,
                                  "VaR $": CAD, "ES $": CAD})

    r = _section(ws, r, "2. VaR backtest (forecast from prior 250 days vs next-day return)")
    btv = bt.rename(columns={
        "sample": "Sample", "confidence": "Confidence", "start": "Start", "end": "End",
        "days": "Days", "exceptions": "Exceptions", "expected": "Expected",
        "exception_rate": "Exception rate", "kupiec_lr": "Kupiec LR",
        "kupiec_p_value": "Kupiec p-value", "kupiec_result": "Kupiec (5%)",
        "basel_zone": "Basel zone"})
    top = r
    r = _write_table(ws, btv, r, {"Confidence": "0%", "Expected": "0.0",
                                  "Exception rate": PCT, "Kupiec LR": NUM,
                                  "Kupiec p-value": "0.0000"})
    _colour_backtest(ws, top, len(btv), list(btv.columns))
    ws.cell(row=r - 1, column=1,
            value="Kupiec H0: true exception rate = 1 - confidence. REJECT = exception count "
                  "statistically inconsistent with the VaR level. Basel zone uses the last 250 "
                  "days at 99% (green 0-4, yellow 5-9, red 10+).").font = Font(italic=True, color="52514E")
    r += 1

    r = _section(ws, r, "3. Historical stress tests (scenario returns applied to today's weights)")
    sv = results["stress_summary"].rename(columns={
        "scenario": "Scenario", "start_close": "From close", "end_close": "To close",
        "trading_days": "Trading days", "portfolio_return": "Portfolio return",
        "pnl_cad": "P&L $", "actual_xbal_return": "Actual XBAL.TO return (reference)"})
    top = r
    r = _write_table(ws, sv, r, {"Portfolio return": PCT, "P&L $": CAD,
                                 "Actual XBAL.TO return (reference)": PCT})
    for i in range(len(sv)):
        if sv["P&L $"].iloc[i] < 0:
            ws.cell(row=top + 1 + i, column=list(sv.columns).index("P&L $") + 1).font = Font(color="C00000", bold=True)

    r = _section(ws, r, "4. Big Six Canadian banks - disclosed trading VaR")
    ws.cell(row=r, column=1, value=f"Status: {bank['status']}").font = Font(bold=True)
    ws.cell(row=r + 1, column=1, value=bank["summary_line"])
    r += 3

    r = _section(ws, r, "5. Warnings and data notes")
    for w in warnings:
        ws.cell(row=r, column=1, value=f"- {w}")
        r += 1
    _autowidth(ws, max_w=22)
    ws.column_dimensions["A"].width = 34

    # ------------------------------------------------------------ Charts
    wc = wb.create_sheet("Charts")
    row = 1
    for key in ["backtest", "histogram", "stress"]:
        img = Image(chart_paths[key])
        img.width, img.height = img.width * 0.55, img.height * 0.55
        wc.add_image(img, f"A{row}")
        row += int(img.height / 20) + 2
    if bank.get("chart"):
        img = Image(bank["chart"])
        img.width, img.height = img.width * 0.55, img.height * 0.55
        wc.add_image(img, f"A{row}")

    # ------------------------------------------------------------ Holdings
    wh = wb.create_sheet("Holdings")
    hv = holdings[["ticker", "yf_ticker", "name", "asset_class", "market_currency", "weight",
                   "proxy_ticker", "proxy_currency", "holding_first_date"]].rename(columns={
        "ticker": "Ticker", "yf_ticker": "Yahoo ticker", "name": "Name",
        "asset_class": "Asset class", "market_currency": "Trading currency",
        "weight": "Weight (renormalised)", "proxy_ticker": "Proxy (pre-inception)",
        "proxy_currency": "Proxy currency", "holding_first_date": "Holding's own history starts"})
    hv["Position $"] = hv["Weight (renormalised)"] * PORTFOLIO_VALUE
    _write_table(wh, hv, 1, {"Weight (renormalised)": PCT, "Position $": CAD})
    _autowidth(wh)

    # ------------------------------------------------------------ VaR history
    wv = wb.create_sheet("VaR_History")
    hv = hist.reset_index().rename(columns={"index": "date"})
    hv["date"] = hv["date"].dt.date
    hv = hv[["date", "return", "pnl_cad", "var95", "es95", "var99", "es99",
             "exception95", "exception99"]]
    _write_table(wv, hv, 1, {"return": PCT4, "pnl_cad": CAD, "var95": PCT4, "es95": PCT4,
                             "var99": PCT4, "es99": PCT4})
    # Red rows = the loss was larger than the VaR forecast for that day.
    for i, (e95, e99) in enumerate(zip(hv["exception95"], hv["exception99"]), start=2):
        if e95 or e99:
            for j in range(1, len(hv.columns) + 1):
                wv.cell(row=i, column=j).fill = DARK_RED_FILL if e99 else RED_FILL
    wv.freeze_panes = "B2"
    _autowidth(wv, min_w=12)

    # ------------------------------------------------------------ Backtest exceptions
    wb_ = wb.create_sheet("Backtest")
    top = 1
    nxt = _write_table(wb_, btv, top, {"Confidence": "0%", "Expected": "0.0",
                                       "Exception rate": PCT, "Kupiec LR": NUM,
                                       "Kupiec p-value": "0.0000"})
    _colour_backtest(wb_, top, len(btv), list(btv.columns))
    wb_.cell(row=nxt, column=1, value="Every VaR exception day (light red = 95% only, "
                                      "dark red = also a 99% exception)").font = SECTION_FONT
    ex = hv[(hv["exception95"] == 1) | (hv["exception99"] == 1)]
    start = nxt + 1
    _write_table(wb_, ex, start, {"return": PCT4, "pnl_cad": CAD, "var95": PCT4, "es95": PCT4,
                                  "var99": PCT4, "es99": PCT4})
    for i, e99 in enumerate(ex["exception99"], start=start + 1):
        for j in range(1, len(ex.columns) + 1):
            wb_.cell(row=i, column=j).fill = DARK_RED_FILL if e99 else RED_FILL
    _autowidth(wb_, min_w=12, max_w=18)

    # ------------------------------------------------------------ Stress
    wsx = wb.create_sheet("Stress")
    nxt = _write_table(wsx, sv, 1, {"Portfolio return": PCT, "P&L $": CAD,
                                    "Actual XBAL.TO return (reference)": PCT})
    det = results["stress_detail"].rename(columns={
        "scenario": "Scenario", "start_close": "From close", "end_close": "To close",
        "trading_days": "Trading days", "ticker": "Holding", "weight": "Weight",
        "source": "Return source", "holding_return": "Holding return",
        "contribution": "Contribution", "pnl_cad": "P&L $"})
    _write_table(wsx, det, nxt, {"Weight": PCT, "Holding return": PCT,
                                 "Contribution": PCT, "P&L $": CAD})
    _autowidth(wsx)

    # ------------------------------------------------------------ Big Six
    wbk = wb.create_sheet("Big6")
    wbk["A1"] = f"Status: {bank['status']}"
    wbk["A1"].font = Font(bold=True)
    wbk["A2"] = bank["summary_line"]
    _write_table(wbk, bank["table"], 4, {"avg_trading_var_cad_mm": "#,##0.0",
                                         "period_end_var_cad_mm": "#,##0.0"})
    _autowidth(wbk)

    # ------------------------------------------------------------ Data sources
    wd = wb.create_sheet("Data_Sources")
    _write_table(wd, pd.DataFrame(data_sources, columns=["Data", "Source", "Detail"]), 1)
    _autowidth(wd, max_w=90)

    wb.save(path)
    return path


def _colour_backtest(ws, header_row, n, cols):
    """REJECT -> dark red; Basel zone -> green/yellow/red."""
    k_col, z_col = cols.index("Kupiec (5%)") + 1, cols.index("Basel zone") + 1
    for i in range(1, n + 1):
        k = ws.cell(row=header_row + i, column=k_col)
        if k.value == "REJECT":
            k.fill, k.font = DARK_RED_FILL, Font(bold=True, color="FFFFFF")
        z = ws.cell(row=header_row + i, column=z_col)
        if z.value in ZONE_FILLS:
            z.fill, z.font = ZONE_FILLS[z.value], Font(bold=True)
