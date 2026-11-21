"""
banks.py - Big Six Canadian banks' disclosed trading VaR, for context.

Data comes ONLY from data/raw/big6_var.csv, filled in by hand from the banks'
quarterly reports (via SQLite table big6_var). If it is empty the section is
marked "pending data" and the rest of the pipeline carries on.
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

NUMERIC = ["avg_trading_var_cad_mm", "period_end_var_cad_mm", "confidence_level",
           "backtesting_exceptions", "page"]


def load_big6(conn):
    df = pd.read_sql("SELECT * FROM big6_var", conn)
    for c in NUMERIC:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def compare_banks(conn, current_var, chart_dir):
    """
    Returns {"status", "table", "summary_line", "chart"}.
    The chart shows the banks only; our portfolio is a separate summary line
    because a $100M fund and a bank's trading book are very different scales.
    """
    df = load_big6(conn)
    filled = df.dropna(subset=["avg_trading_var_cad_mm"])
    if filled.empty:
        return {"status": "pending data", "table": df, "chart": None,
                "missing": df["bank"].tolist(),
                "summary_line": "Big Six comparison: pending data "
                                "(fill in data/raw/big6_var.csv and re-run)."}

    # Banks usually disclose 99% 1-day VaR; quote ours at the same confidence level.
    levels = filled["confidence_level"].dropna().unique()
    level = levels[0] if len(levels) == 1 else 0.99
    level = level / 100 if level > 1 else level           # accept 99 or 0.99
    ours = current_var.loc[(current_var["confidence"] - level).abs() < 1e-9, "var_cad"]
    ours_mm = ours.iloc[0] / 1e6 if len(ours) else float("nan")
    missing = df.loc[df["avg_trading_var_cad_mm"].isna(), "bank"].tolist()
    summary = (f"For reference: this $100M XBAL portfolio's {level:.0%} 1-day historical VaR "
               f"is ${ours_mm:,.2f}M CAD. Bank figures are for whole trading books, so compare "
               f"the method and backtesting results, not the size.")

    # Bar chart: banks only, average trading VaR, latest quarter per bank.
    latest = (filled.sort_values("fiscal_quarter")
              .groupby("bank", as_index=False).last()
              .sort_values("avg_trading_var_cad_mm"))
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.barh(latest["bank"] + " (" + latest["fiscal_quarter"].astype(str) + ")",
            latest["avg_trading_var_cad_mm"], color="#2a78d6", height=0.6)
    for y, v in enumerate(latest["avg_trading_var_cad_mm"]):
        ax.text(v, y, f"  {v:,.1f}", va="center", fontsize=9, color="#0b0b0b")
    ax.set_xlabel("Average trading VaR (CAD millions)")
    ax.set_title("Big Six banks: disclosed average trading VaR", loc="left")
    _style(ax)
    fig.tight_layout()
    path = os.path.join(chart_dir, "big6_var.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    status = "ok" if not missing else f"partial - no data yet for: {', '.join(missing)}"
    return {"status": status, "table": df, "chart": path, "summary_line": summary,
            "missing": missing}


def _style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.spines["left"].set_color("#999999")
    ax.spines["bottom"].set_color("#999999")
    ax.tick_params(colors="#52514e", labelsize=9)
    ax.grid(axis="x", color="#e5e5e3", linewidth=0.8)
    ax.set_axisbelow(True)
