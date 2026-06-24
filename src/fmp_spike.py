#!/usr/bin/env python3
"""
FMP data spike -- single-company sanity check.

Goal: pull NVIDIA's financials from Financial Modeling Prep and compute
EV/EBITDA BY HAND from the underlying components, then compare it to the
multiple FMP reports. If the hand-built number is sane (and roughly matches
what NVDA actually trades at), the pipeline is trustworthy and the other 13
names are just repetition. If it's garbage, we found out on one ticker
instead of fourteen.

This is throwaway-ish scaffolding. The real engine generalizes it. For now
it does ONE thing and prints every input so you can verify each piece.

Setup:
    pip install requests          # (or use the stdlib version below, no install)
    export FMP_API_KEY="your_key_here"
    python fmp_spike.py

Free tier: 250 requests/day. This script uses 3 requests per run and caches
the raw JSON locally so re-runs cost zero requests.
"""

import os
import sys
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import HTTPError, URLError

BASE = "https://financialmodelingprep.com/stable"
SYMBOL = "NVDA"
CACHE_DIR = Path("data_cache")


# --------------------------------------------------------------------------
# Plumbing
# --------------------------------------------------------------------------

def get_key():
    key = os.environ.get("FMP_API_KEY")
    if not key:
        sys.exit(
            "ERROR: no API key found.\n"
            "  Run:  export FMP_API_KEY=\"your_key_here\"\n"
            "  Get a free key at https://site.financialmodelingprep.com/developer/docs/dashboard"
        )
    return key


def fetch(endpoint, key, use_cache=True, **params):
    """
    Hit a /stable/ endpoint and return parsed JSON.
    Caches raw responses to data_cache/ so repeated runs don't burn requests.
    Stable endpoints take the ticker as a ?symbol= query param (not in the path).
    """
    CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CACHE_DIR / f"{params.get('symbol', 'x')}_{endpoint.replace('/', '_')}.json"

    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text())

    params["apikey"] = key
    url = f"{BASE}/{endpoint}?{urlencode(params)}"

    try:
        with urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except HTTPError as e:
        if e.code == 403:
            sys.exit(f"ERROR 403 on '{endpoint}': this endpoint isn't on the free tier. "
                     f"Swap it for a free equivalent or check the docs.")
        sys.exit(f"ERROR {e.code} on '{endpoint}': {e.reason}")
    except URLError as e:
        sys.exit(f"NETWORK ERROR on '{endpoint}': {e.reason}")

    # FMP returns {} or {"Error Message": ...} when something's off
    if isinstance(data, dict) and ("Error Message" in data or not data):
        sys.exit(f"FMP returned an error for '{endpoint}': {data}")
    if not data:
        sys.exit(f"FMP returned empty data for '{endpoint}'. Check the ticker/endpoint.")

    cache_file.write_text(json.dumps(data, indent=2))
    return data


def pick(d, *names, required=True):
    """
    Grab the first field that exists out of several possible names.
    FMP occasionally renames fields between API versions, so this keeps the
    script from exploding on a single rename -- it tells you what's missing.
    """
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    if required:
        sys.exit(f"MISSING FIELD: none of {names} found.\n"
                 f"Available keys: {sorted(d.keys())}")
    return 0


def money(x):
    """Format a big number as $X.XXB for readability."""
    return f"${x/1e9:,.2f}B"


# --------------------------------------------------------------------------
# The actual spike
# --------------------------------------------------------------------------

def main():
    key = get_key()

    # 3 endpoints: income statement, balance sheet, company profile (for mkt cap).
    # period=annual, limit=1 -> just the most recent fiscal year.
    income_list = fetch("income-statement", key, symbol=SYMBOL, period="annual", limit=1)
    balance_list = fetch("balance-sheet-statement", key, symbol=SYMBOL, period="annual", limit=1)
    profile_list = fetch("profile", key, symbol=SYMBOL)

    income = income_list[0]
    balance = balance_list[0]
    profile = profile_list[0]

    fiscal_date = pick(income, "date")
    print(f"\n{'='*60}")
    print(f"  {SYMBOL}  --  most recent annual filing: {fiscal_date}")
    print(f"{'='*60}\n")

    # ---- EBITDA, computed three ways so you can cross-check ----
    # NVIDIA's fiscal year ends in late January, so the "most recent annual"
    # is the FY that closed ~Jan of this year. Worth confirming the date above
    # matches what you expect.

    operating_income = pick(income, "operatingIncome")
    da = pick(income, "depreciationAndAmortization")
    net_income = pick(income, "netIncome")
    interest_exp = pick(income, "interestExpense", required=False)
    tax_exp = pick(income, "incomeTaxExpense", required=False)
    reported_ebitda = pick(income, "ebitda", required=False)

    ebitda_topdown = operating_income + da            # EBIT + D&A
    ebitda_bottomup = net_income + interest_exp + tax_exp + da  # NI + I + T + D&A

    print("EBITDA build:")
    print(f"  Operating income (EBIT) : {money(operating_income)}")
    print(f"  + D&A                    : {money(da)}")
    print(f"  = EBITDA (top-down)      : {money(ebitda_topdown)}")
    print(f"  EBITDA (bottom-up, NI+I+T+D&A): {money(ebitda_bottomup)}")
    print(f"  EBITDA (FMP reported)    : {money(reported_ebitda)}")
    print("  -> these three should be close. Big gaps = a field means something")
    print("     other than you think (the #1 reason a comp table lies).\n")

    ebitda = ebitda_topdown  # use the top-down figure as our number

    # ---- Enterprise Value = Market Cap + Total Debt - Cash ----
    market_cap = pick(profile, "marketCap", "mktCap")
    total_debt = pick(balance, "totalDebt", required=False)
    if total_debt == 0:
        # fall back to summing short + long term debt if totalDebt isn't given
        total_debt = (pick(balance, "shortTermDebt", required=False)
                      + pick(balance, "longTermDebt", required=False))
    cash = pick(balance, "cashAndCashEquivalents",
                "cashAndShortTermInvestments", required=False)

    enterprise_value = market_cap + total_debt - cash

    print("Enterprise Value build:")
    print(f"  Market cap (current)     : {money(market_cap)}")
    print(f"  + Total debt             : {money(total_debt)}")
    print(f"  - Cash & equivalents     : {money(cash)}")
    print(f"  = Enterprise Value       : {money(enterprise_value)}")
    print("  note: mkt cap is live, debt/cash are as of the last filing. Standard")
    print("        convention for a trailing multiple. Rigorous EV also adds")
    print("        minority interest + preferred -- negligible for NVDA, matters elsewhere.\n")

    # ---- The multiple ----
    ev_ebitda = enterprise_value / ebitda
    print(f"{'='*60}")
    print(f"  EV / EBITDA  =  {money(enterprise_value)} / {money(ebitda)}  =  {ev_ebitda:.1f}x")
    print(f"{'='*60}\n")
    print("SANITY CHECK: pull up NVDA's EV/EBITDA on any finance site and compare.")
    print("Within a point or two -> pipeline is trustworthy, go scale to 14 names.")
    print("Way off -> something's mislabeled. Debug here, not after wiring all 14.\n")


if __name__ == "__main__":
    main()