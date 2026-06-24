#!/usr/bin/env python3
"""
Data layer -- pull the full universe and build the base dataset.

For every ticker in the universe this:
  1. pulls income statement, balance sheet, cash flow, and profile from FMP
  2. caches each raw response (re-runs cost zero requests)
  3. computes EBITDA the canonical way we settled on in the spike:
        EBITDA = operating income + D&A
     deliberately NOT FMP's reported `ebitda` field, which folds in
     non-operating income (interest on cash, investment gains) and would
     make cash-rich names look artificially cheap.
  4. computes enterprise value = market cap + total debt - cash
  5. lays it all out in a segment-sorted table and writes a CSV

D&A is taken from the CASH FLOW statement, not the income statement -- it's the
more complete figure (picks up amortization the income statement can bury).

Usage:
    set FMP_API_KEY=your_key        (cmd)   or   $env:FMP_API_KEY="your_key"  (PowerShell)
    python src\\build_dataset.py

Requests: 4 per name x 14 names = 56. Free tier is 250/day, and caching means
re-runs are free.
"""

import os
import sys
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import HTTPError, URLError

import pandas as pd

# make `data.universe` importable whether you run from root or src/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data.universe import UNIVERSE, segment_of  # noqa: E402

BASE = "https://financialmodelingprep.com/stable"
CACHE_DIR = Path("data/raw")
OUTPUT_CSV = Path("data/dataset.csv")


# --------------------------------------------------------------------------
# Plumbing (same fetch/pick helpers as the spike, lightly generalized)
# --------------------------------------------------------------------------

def get_key():
    key = os.environ.get("FMP_API_KEY")
    if not key:
        sys.exit("ERROR: set FMP_API_KEY first. Get a free key at "
                 "https://site.financialmodelingprep.com/developer/docs/dashboard")
    return key


def fetch(endpoint, key, use_cache=True, **params):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    sym = params.get("symbol", "x")
    cache_file = CACHE_DIR / f"{sym}_{endpoint.replace('/', '_')}.json"

    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text())

    params["apikey"] = key
    url = f"{BASE}/{endpoint}?{urlencode(params)}"
    try:
        with urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except HTTPError as e:
        if e.code == 403:
            sys.exit(f"ERROR 403 on '{endpoint}' for {sym}: not on free tier.")
        sys.exit(f"ERROR {e.code} on '{endpoint}' for {sym}: {e.reason}")
    except URLError as e:
        sys.exit(f"NETWORK ERROR on '{endpoint}' for {sym}: {e.reason}")

    if isinstance(data, dict) and ("Error Message" in data or not data):
        sys.exit(f"FMP error for {sym}/{endpoint}: {data}")
    if not data:
        sys.exit(f"Empty data for {sym}/{endpoint}.")

    cache_file.write_text(json.dumps(data, indent=2))
    return data


def pick(d, *names, required=True, label=""):
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    if required:
        sys.exit(f"MISSING FIELD {label}: none of {names}.\nKeys: {sorted(d.keys())}")
    return 0


# --------------------------------------------------------------------------
# Per-company metric build
# --------------------------------------------------------------------------

def build_row(ticker, name, key):
    """Pull one company and return a dict of computed metrics."""
    income = fetch("income-statement", key, symbol=ticker, period="annual", limit=1)[0]
    balance = fetch("balance-sheet-statement", key, symbol=ticker, period="annual", limit=1)[0]
    cashflow = fetch("cash-flow-statement", key, symbol=ticker, period="annual", limit=1)[0]
    profile = fetch("profile", key, symbol=ticker)[0]

    # --- canonical operating EBITDA: EBIT + D&A (D&A from cash flow stmt) ---
    operating_income = pick(income, "operatingIncome", label=f"{ticker} EBIT")
    da = pick(cashflow, "depreciationAndAmortization", label=f"{ticker} D&A")
    ebitda = operating_income + da

    # --- enterprise value ---
    market_cap = pick(profile, "marketCap", "mktCap", label=f"{ticker} mktcap")
    total_debt = pick(balance, "totalDebt", required=False)
    if total_debt == 0:
        total_debt = (pick(balance, "shortTermDebt", required=False)
                      + pick(balance, "longTermDebt", required=False))
    cash = pick(balance, "cashAndCashEquivalents",
                "cashAndShortTermInvestments", required=False)
    ev = market_cap + total_debt - cash

    revenue = pick(income, "revenue", required=False)

    return {
        "ticker": ticker,
        "company": name,
        "segment": segment_of(ticker),
        "fiscal_date": pick(income, "date", required=False),
        "revenue": revenue,
        "operating_income": operating_income,
        "da": da,
        "ebitda": ebitda,
        "ebitda_margin": ebitda / revenue if revenue else None,
        "market_cap": market_cap,
        "total_debt": total_debt,
        "cash": cash,
        "enterprise_value": ev,
        "ev_ebitda": ev / ebitda if ebitda else None,
    }


def main():
    key = get_key()
    rows = []

    for segment, members in UNIVERSE.items():
        for ticker, name in members.items():
            print(f"  pulling {ticker:6} ({segment})...")
            rows.append(build_row(ticker, name, key))

    df = pd.DataFrame(rows)
    # sort by segment, then by the multiple within each segment
    df = df.sort_values(["segment", "ev_ebitda"]).reset_index(drop=True)

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_CSV, index=False)

    # readable console view, $B where it helps
    show = df.copy()
    for col in ["revenue", "ebitda", "market_cap", "enterprise_value"]:
        show[col] = (show[col] / 1e9).round(1)
    show["ebitda_margin"] = (show["ebitda_margin"] * 100).round(1)
    show["ev_ebitda"] = show["ev_ebitda"].round(1)

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 200)
    print("\n" + "=" * 70)
    print("  BASE DATASET  (revenue / ebitda / mkt cap / EV in $B)")
    print("=" * 70)
    for seg in df["segment"].unique():
        print(f"\n{seg.upper()}")
        cols = ["ticker", "company", "revenue", "ebitda", "ebitda_margin",
                "enterprise_value", "ev_ebitda"]
        print(show[show["segment"] == seg][cols].to_string(index=False))

    print(f"\nSaved {len(df)} rows to {OUTPUT_CSV}")
    print("\nEyeball check: do the EV/EBITDA multiples rank sensibly within each")
    print("segment? Any name that looks absurd is the next thing to dig into.")


if __name__ == "__main__":
    main()