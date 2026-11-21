"""
load_data.py - everything that touches the outside world.

Downloads (or reads from cache) the XBAL holdings, daily prices, USD/CAD FX,
optional FRED yields and the manual Big Six VaR file, then writes it all into
SQLite (data/risk.db). The risk calculations read from SQLite, not from here.
"""
import io
import os
import sqlite3
from datetime import datetime

import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
DB_PATH = os.path.join(ROOT, "data", "risk.db")

START_DATE = "2007-01-01"   # gives one year of history before the 2008 GFC window
FX_TICKER = "CAD=X"         # Yahoo's USD/CAD rate (CAD per 1 USD)
FUND_TICKER = "XBAL.TO"     # the fund itself - only used as a reference in stress tests

XBAL_PAGE = ("https://www.blackrock.com/ca/investors/en/products/239449/"
             "ishares-balanced-income-coreportfoliotm-fund")
XBAL_CSV_URL = XBAL_PAGE + "/1464253357814.ajax?fileType=csv&fileName=XBAL_holdings&dataType=fund"
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}

BIG6_COLUMNS = ["bank", "fiscal_quarter", "avg_trading_var_cad_mm", "period_end_var_cad_mm",
                "confidence_level", "backtesting_exceptions", "source_document", "page"]
BIG6_BANKS = ["RBC", "TD", "BMO", "Scotiabank", "CIBC", "National Bank"]

WARNINGS = []   # collected and printed at the end of run_all.py


def warn(msg):
    print(f"  WARNING: {msg}")
    WARNINGS.append(msg)


# ---------------------------------------------------------------- holdings
def get_holdings(refresh=False):
    """Download XBAL's holdings CSV from iShares Canada (cached) and keep the ETF rows."""
    path = os.path.join(RAW, "xbal_holdings.csv")
    if refresh or not os.path.exists(path):
        print("  Downloading XBAL holdings from iShares Canada...")
        try:
            r = requests.get(XBAL_CSV_URL, headers=HTTP_HEADERS, timeout=60)
            r.raise_for_status()
            if "Ticker" not in r.text[:2000]:
                raise ValueError("response does not look like a holdings CSV")
            with open(path, "w", encoding="utf-8") as f:
                f.write(r.text)
        except Exception as e:
            if os.path.exists(path):
                warn(f"Holdings download failed ({e}); using cached {path}")
            else:
                raise SystemExit(
                    f"\nCould not download XBAL holdings ({e}).\n"
                    f"Please download it manually:\n"
                    f"  1. Open {XBAL_PAGE}\n"
                    f"  2. Holdings tab -> 'Detailed Holdings and Analytics' (CSV)\n"
                    f"  3. Save it as: {path}\n"
                    f"Then re-run: python run_all.py")

    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    # Line 1 is 'Fund Holdings as of,"Nov 19, 2026"'; the table header is line 3.
    # The file holds two tables: XBAL's own ETF holdings first, then a look-through of
    # every security inside those ETFs. We only want the first table (ends at a blank line).
    lines = text.splitlines()
    as_of = lines[0].split(",", 1)[1].strip().strip('"')
    as_of = datetime.strptime(as_of, "%b %d, %Y").strftime("%Y-%m-%d")
    table = []
    for line in lines[2:]:
        if not line.strip() or line.startswith("Fund Holdings as of"):
            break
        table.append(line)
    df = pd.read_csv(io.StringIO("\n".join(table)), dtype=str)
    df = df.dropna(subset=["Ticker", "Asset Class"])

    # XBAL is a fund of ETFs. Keep the ETF positions (Equity / Fixed Income);
    # drop cash and the (near-zero weight) FX forwards.
    keep = df["Asset Class"].isin(["Equity", "Fixed Income"])
    dropped = df.loc[~keep, "Weight (%)"].str.replace(",", "").astype(float).sum()
    df = df[keep].copy()
    df["weight"] = df["Weight (%)"].str.replace(",", "").astype(float)
    print(f"  Holdings as of {as_of}: {len(df)} ETFs, {df['weight'].sum():.2f}% of fund "
          f"(dropped {dropped:.2f}% cash/forwards, weights renormalised to 100%)")
    df["weight"] = df["weight"] / df["weight"].sum()

    # Yahoo tickers: TSX listings end in .TO
    df["yf_ticker"] = df.apply(
        lambda r: r["Ticker"] + ".TO" if r["Exchange"] == "Toronto Stock Exchange" else r["Ticker"],
        axis=1)
    out = pd.DataFrame({
        "ticker": df["Ticker"], "yf_ticker": df["yf_ticker"], "name": df["Name"],
        "asset_class": df["Asset Class"], "location": df["Location"],
        "market_currency": df["Market Currency"], "weight": df["weight"],
        "as_of_date": as_of,
    })
    return out.reset_index(drop=True)


# ---------------------------------------------------------------- prices
def _cache_path(ticker):
    return os.path.join(RAW, f"prices_{ticker.replace('=', '_').replace('^', '')}.csv")


def get_price_history(ticker, refresh=False):
    """Daily adjusted close (dividends reinvested) from Yahoo Finance, cached to CSV."""
    path = _cache_path(ticker)
    if not refresh and os.path.exists(path):
        return pd.read_csv(path, index_col=0, parse_dates=True)["close"]
    print(f"  Downloading prices: {ticker}")
    data = yf.download(ticker, start=START_DATE, auto_adjust=True, progress=False)
    if data is None or data.empty:
        warn(f"No price data returned for {ticker}")
        return pd.Series(dtype=float)
    close = data["Close"].squeeze().dropna()
    close.index = pd.to_datetime(close.index).tz_localize(None)
    close.name = "close"
    close.index.name = "date"
    close.to_csv(path)
    return close


def choose_proxies(holdings, prices):
    """
    A holding needs a proxy if its own price history starts after START_DATE
    (+ a 30-day grace period). The candidate map lives in data/raw/proxies.csv.
    Only proxies that are actually needed are returned.
    """
    candidates = pd.read_csv(os.path.join(RAW, "proxies.csv"))
    cutoff = pd.Timestamp(START_DATE) + pd.Timedelta(days=30)
    rows = []
    for _, h in holdings.iterrows():
        first = prices[h["yf_ticker"]].index.min()
        if pd.isna(first) or first > cutoff:
            match = candidates[candidates["holding_ticker"] == h["ticker"]]
            if match.empty:
                warn(f"{h['ticker']} starts {first} but has no proxy in proxies.csv; "
                     f"its early history will be missing")
                continue
            m = match.iloc[0]
            rows.append({"holding_ticker": h["ticker"], "proxy_ticker": m["proxy_ticker"],
                         "proxy_currency": m["proxy_currency"], "reason": m["reason"],
                         "holding_first_date": first.strftime("%Y-%m-%d")})
    return pd.DataFrame(rows, columns=["holding_ticker", "proxy_ticker", "proxy_currency",
                                       "reason", "holding_first_date"])


# ---------------------------------------------------------------- FRED (optional)
def get_fred_yields():
    """Canada and US 10Y government yields from FRED. Context only; skipped without a key."""
    load_dotenv(os.path.join(ROOT, ".env"))
    key = os.getenv("FRED_API_KEY", "").strip()
    if not key:
        warn("FRED_API_KEY not set in .env - skipping FRED yields (optional context only)")
        return pd.DataFrame(columns=["date", "series", "value"])
    frames = []
    for series in ["IRLTLT01CAM156N", "DGS10"]:   # Canada 10Y (monthly), US 10Y (daily)
        try:
            r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                             params={"series_id": series, "api_key": key, "file_type": "json",
                                     "observation_start": START_DATE}, timeout=60)
            r.raise_for_status()
            obs = pd.DataFrame(r.json()["observations"])[["date", "value"]]
            obs["value"] = pd.to_numeric(obs["value"], errors="coerce")  # '.' = missing
            obs["series"] = series
            frames.append(obs.dropna())
        except Exception as e:
            warn(f"FRED download failed for {series}: {e}")
    if not frames:
        return pd.DataFrame(columns=["date", "series", "value"])
    out = pd.concat(frames)
    out.to_csv(os.path.join(RAW, "fred_yields.csv"), index=False)
    return out


# ---------------------------------------------------------------- Big Six (manual)
def get_big6():
    """Manual file filled in by hand from the banks' quarterly reports. Never overwritten."""
    path = os.path.join(RAW, "big6_var.csv")
    if not os.path.exists(path):
        pd.DataFrame({"bank": BIG6_BANKS}, columns=BIG6_COLUMNS).to_csv(path, index=False)
        print(f"  Created empty Big Six template: {path}")
    return pd.read_csv(path)


# ---------------------------------------------------------------- main
def load_all(refresh=False):
    """Fetch everything and (re)build data/risk.db. Returns the open connection."""
    os.makedirs(RAW, exist_ok=True)

    holdings = get_holdings(refresh)

    prices = {t: get_price_history(t, refresh) for t in holdings["yf_ticker"]}
    proxies = choose_proxies(holdings, prices)
    for t in proxies["proxy_ticker"]:
        prices[t] = get_price_history(t, refresh)
    prices[FUND_TICKER] = get_price_history(FUND_TICKER, refresh)
    fx = get_price_history(FX_TICKER, refresh)
    for _, p in proxies.iterrows():
        warn(f"Proxy used: {p['holding_ticker']} (history starts {p['holding_first_date']}) "
             f"-> {p['proxy_ticker']} before that date")

    prices_long = pd.concat(
        [s.rename("close").rename_axis("date").reset_index().assign(ticker=t)
         for t, s in prices.items() if not s.empty])
    prices_long["date"] = prices_long["date"].dt.strftime("%Y-%m-%d")
    fx_df = fx.rename("usdcad").rename_axis("date").reset_index()
    fx_df["date"] = fx_df["date"].dt.strftime("%Y-%m-%d")

    yields = get_fred_yields()
    big6 = get_big6()

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)          # rebuild from the cached CSVs every run
    conn = sqlite3.connect(DB_PATH)
    holdings.to_sql("holdings", conn, index=False)
    proxies.to_sql("proxies", conn, index=False)
    prices_long[["date", "ticker", "close"]].to_sql("prices", conn, index=False)
    fx_df.to_sql("fx", conn, index=False)
    yields.to_sql("yields", conn, index=False)
    big6.to_sql("big6_var", conn, index=False)
    conn.execute("CREATE INDEX idx_prices ON prices(ticker, date)")
    conn.execute("CREATE INDEX idx_fx ON fx(date)")
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    print(f"  SQLite: {DB_PATH} ({n:,} price rows, {len(holdings)} holdings, {len(proxies)} proxies)")
    return conn


if __name__ == "__main__":
    load_all()
