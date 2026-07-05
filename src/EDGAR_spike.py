"""
EDGAR spike -- my one-company sanity check before I wire up all 14 names.

FMP's free tier turned out to be symbol-gated: it served NVDA but paywalled AMAT
and (almost certainly) most of my universe, so I'm moving the financials layer to
SEC EDGAR. EDGAR is the primary source anyway -- the numbers come straight out of
the 10-K -- and it's free and reproducible for anyone who clones this repo.

Before I trust EDGAR across the board, I re-pull NVDA and check it reproduces the
figures I already validated through FMP:

    operating income = $130.39B
    D&A              = $2.84B
    EBITDA (my defn) = $133.23B   (operating income + D&A)

If EDGAR lands on those, my tag mapping is right and I can scale. If it doesn't,
I'd rather find out on one name than after wiring all fourteen.
"""

import sys
import json
from pathlib import Path

import requests


# SEC's fair-access policy makes me identify myself in the User-Agent header or they
# just 403 every request. I keep my contact out of the committed code by reading it
# from an env var -- same pattern I used for the API key, and it keeps my email off
# a public repo. Set it once in PowerShell before running:
#   $env:SEC_USER_AGENT="Agnivesh Kaundinya myemail@example.com"
USER_AGENT = None
try:
    import os
    USER_AGENT = os.environ.get("SEC_USER_AGENT")
except Exception:
    pass

if not USER_AGENT:
    sys.exit(
        'ERROR: no SEC contact set.\n'
        '  SEC needs a User-Agent identifying me or it blocks the request.\n'
        '  Run:  $env:SEC_USER_AGENT="Your Name your_email@example.com"'
    )

# One session so the User-Agent rides on every call automatically.
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})

# I cache raw EDGAR responses to data/raw/ (gitignored) so repeat runs don't keep
# hammering SEC and I stay well inside their 10-req/sec courtesy limit. Paths are
# resolved off this file, not the working directory, so it runs from anywhere.
ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
RAW.mkdir(parents=True, exist_ok=True)

# The numbers I'm checking against -- straight from my validated FMP spike.
TARGET_OPERATING_INCOME = 130.39e9
TARGET_DA = 2.84e9
TARGET_EBITDA = 133.23e9

# D&A is the tag that varies most from filer to filer, so I try these in order and
# take the first that resolves. This is exactly the "mapping work" I signed up for
# by going to raw XBRL instead of FMP's pre-standardized fields.
DA_TAGS = [
    "DepreciationDepletionAndAmortization",
    "DepreciationAmortizationAndAccretionNet",
    "DepreciationAndAmortization",
    "Depreciation",
]


def get_json(url, cache_name=None):
    """Fetch JSON from EDGAR, caching to data/raw/ so I don't re-request on every run."""
    if cache_name:
        cached = RAW / cache_name
        if cached.exists():
            return json.loads(cached.read_text())
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    data = r.json()
    if cache_name:
        (RAW / cache_name).write_text(json.dumps(data))
    return data


def resolve_cik(ticker):
    """Map a ticker to its zero-padded 10-digit CIK -- EDGAR keys everything on CIK, not ticker."""
    data = get_json(
        "https://www.sec.gov/files/company_tickers.json",
        cache_name="sec_company_tickers.json",
    )
    # The file is a dict keyed by row number, so I walk the values looking for my ticker.
    for row in data.values():
        if row["ticker"].upper() == ticker.upper():
            return str(row["cik_str"]).zfill(10)
    return None


def latest_annual(facts, tag, unit="USD"):
    """Grab the most recent full-year (10-K) row for a us-gaap tag, or None if it's missing."""
    node = facts.get("us-gaap", {}).get(tag)
    if not node:
        return None
    rows = node.get("units", {}).get(unit, [])
    # I only want annual numbers reported on a 10-K, not quarterly slices, so I filter
    # to fp == "FY" and the 10-K form, then take the latest fiscal-year end. Ties on the
    # same period-end (a figure restated in a later filing) break to the latest filing.
    annual = [
        r for r in rows
        if r.get("fp") == "FY" and str(r.get("form", "")).startswith("10-K")
    ]
    if not annual:
        return None
    return max(annual, key=lambda r: (r["end"], r.get("filed", "")))


def annual_value_for_end(facts, tag, end_date, unit="USD"):
    """Pull a tag's full-year value for one specific period-end, so my D&A lines up with my EBIT."""
    node = facts.get("us-gaap", {}).get(tag)
    if not node:
        return None
    rows = node.get("units", {}).get(unit, [])
    matches = [r for r in rows if r.get("end") == end_date and r.get("fp") == "FY"]
    if not matches:
        return None
    return max(matches, key=lambda r: r.get("filed", ""))["val"]


def pct_delta(got, target):
    """How far off I am from the FMP number, as a percentage -- my pass/fail signal."""
    if target == 0:
        return float("inf")
    return abs(got - target) / abs(target) * 100.0


def run(ticker="NVDA"):
    cik = resolve_cik(ticker)
    if not cik:
        sys.exit(f"ERROR: couldn't find a CIK for {ticker}.")

    facts_doc = get_json(
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
        cache_name=f"{ticker}_companyfacts.json",
    )
    facts = facts_doc.get("facts", {})
    name = facts_doc.get("entityName", ticker)

    # Operating income is my EBIT and it's a stable tag across filers, so I anchor on it.
    oi_row = latest_annual(facts, "OperatingIncomeLoss")
    if not oi_row:
        sys.exit("ERROR: no OperatingIncomeLoss found -- I need a different tag for this filer.")
    period_end = oi_row["end"]
    operating_income = oi_row["val"]

    # Now I find D&A for that same fiscal year, walking my tag priority list.
    da_value = None
    da_tag_used = None
    for tag in DA_TAGS:
        v = annual_value_for_end(facts, tag, period_end)
        if v is not None:
            da_value = v
            da_tag_used = tag
            break

    print("=" * 60)
    print(f"  {name} ({ticker})  --  most recent 10-K period end: {period_end}")
    print("=" * 60)
    print()

    if da_value is None:
        print("  Couldn't resolve D&A for this period from my tag list:")
        for t in DA_TAGS:
            print(f"    - {t}")
        print("  -> I need to add whatever tag this filer actually uses.")
        return

    ebitda = operating_income + da_value

    print("EBITDA build (my definition = operating income + D&A):")
    print(f"  Operating income (EBIT)   : ${operating_income/1e9:>8.2f}B   "
          f"[FMP: ${TARGET_OPERATING_INCOME/1e9:.2f}B, off {pct_delta(operating_income, TARGET_OPERATING_INCOME):.2f}%]")
    print(f"  + D&A ({da_tag_used})")
    print(f"      = D&A                 : ${da_value/1e9:>8.2f}B   "
          f"[FMP: ${TARGET_DA/1e9:.2f}B, off {pct_delta(da_value, TARGET_DA):.2f}%]")
    print(f"  = EBITDA                  : ${ebitda/1e9:>8.2f}B   "
          f"[FMP: ${TARGET_EBITDA/1e9:.2f}B, off {pct_delta(ebitda, TARGET_EBITDA):.2f}%]")
    print()

    # I call it a pass if EBITDA is within 1% of my validated FMP number. Both pull from
    # the same 10-K, so they should basically match; a bigger gap means a tag is mislabeled
    # and I debug it here, on one name, before scaling.
    if pct_delta(ebitda, TARGET_EBITDA) <= 1.0:
        print("PASS: EDGAR reproduces my validated EBITDA. Tag mapping is sound -> scale to 14.")
    else:
        print("CHECK: EBITDA is off by more than 1%. A tag is probably mislabeled --")
        print("       most likely D&A, since that's the one that varies by filer.")
    print("=" * 60)


if __name__ == "__main__":
    run("NVDA")