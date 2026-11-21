"""
gfc_gap.py - why does the GFC stress result differ from XBAL's actual 2008 move?

A one-off check, not part of run_all.py. Run after run_all.py has built the database:
    python -m src.gfc_gap

It regresses XBAL.TO's WEEKLY returns on four CAD-denominated building blocks
(Canadian equity, Canadian bonds, US equity, international equity), separately
before and after the fund's December 2018 mandate change. The coefficients show
roughly what XBAL behaved like in each era. Weekly data is used because XBAL.TO
traded thinly in 2008 (many days with an unchanged price).
"""
import sqlite3

import numpy as np
import pandas as pd

from src.load_data import DB_PATH
from src.risk import STRESS_SCENARIOS

ERAS = [("2007-07-01", "2010-12-31"), ("2011-01-01", "2018-11-30"), ("2019-01-01", None)]


def main():
    conn = sqlite3.connect(DB_PATH)
    raw = (pd.read_sql("SELECT date, ticker, close FROM prices", conn, parse_dates=["date"])
           .pivot(index="date", columns="ticker", values="close"))
    px = raw.ffill()
    fx = (pd.read_sql("SELECT date, usdcad FROM fx", conn, parse_dates=["date"])
          .set_index("date")["usdcad"].reindex(px.index).ffill())

    # Building blocks in CAD (US-listed prices x USD/CAD)
    blocks = pd.DataFrame({
        "Canadian equity (XIC)": px["XIC.TO"],
        "Canadian bonds (XBB)": px["XBB.TO"],
        "US equity in CAD (ITOT)": px["ITOT"] * fx,
        "Intl equity in CAD (EFA)": px["EFA"] * fx,
    })
    fund = px["XBAL.TO"]

    # 1. Stale prices in the GFC window
    _, start, end = STRESS_SCENARIOS[0]
    d = raw["XBAL.TO"].dropna()[start:end].pct_change().dropna()   # TSX trading days only
    print(f"XBAL.TO in GFC window: {(d == 0).sum()} of {len(d)} trading days with an unchanged price")

    # 2. Exposure regression by era
    weekly = pd.concat([blocks, fund.rename("XBAL")], axis=1).resample("W-FRI").last().pct_change().dropna()
    for a, b in ERAS:
        w = weekly[a:b]
        X = np.column_stack([np.ones(len(w))] + [w[c] for c in blocks.columns])
        beta, *_ = np.linalg.lstsq(X, w["XBAL"], rcond=None)
        r2 = 1 - (w["XBAL"] - X @ beta).var() / w["XBAL"].var()
        print(f"\n{w.index[0]:%Y-%m-%d} to {w.index[-1]:%Y-%m-%d}  ({len(w)} weeks, R^2 = {r2:.2f})")
        for name, bt in zip(blocks.columns, beta[1:]):
            print(f"  {name:<26} beta {bt:5.2f}")

    # 3. Building-block returns over the GFC window (CAD)
    print(f"\nGFC window {start} -> {end}, CAD returns:")
    for name in blocks.columns:
        s = blocks[name].dropna()
        print(f"  {name:<26} {s[:end].iloc[-1] / s[:start].iloc[-1] - 1:7.2%}")
    print(f"  {'USD/CAD':<26} {fx[:end].iloc[-1] / fx[:start].iloc[-1] - 1:7.2%}")


if __name__ == "__main__":
    main()
