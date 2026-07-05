"""
My thin EDGAR client -- the shared plumbing the whole screener pulls financials through.

I keep the fetch / cache / tag logic here so both the spike and the dataset builder
import one source of truth instead of duplicating it. Everything comes straight from
SEC companyfacts, which is the primary source: the exact numbers in the 10-K.

Name this file exactly edgar.py (lowercase) -- build_dataset.py imports it by that name.
"""

import os
import sys
import json
import time
import datetime
from pathlib import Path

import requests


# SEC's fair-access policy makes me identify myself in the User-Agent or they 403 me.
# I read my contact from an env var so it stays off the public repo -- same pattern as
# the old API key:  $env:SEC_USER_AGENT="Your Name your_email@example.com"
USER_AGENT = os.environ.get("SEC_USER_AGENT")
if not USER_AGENT:
    sys.exit(
        'ERROR: no SEC contact set.\n'
        '  SEC needs a User-Agent identifying me or it blocks the request.\n'
        '  Run:  $env:SEC_USER_AGENT="Agnivesh Kaundinya akaundinya06@gmail.com"'
    )

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})

# Paths resolve off this file, not the working directory, so it runs from anywhere.
ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
RAW.mkdir(parents=True, exist_ok=True)

# For EBITDA I want the FULL D&A add-back: depreciation AND amortization of intangibles.
# Some filers report it as one combined cash-flow line...
COMBINED_DA_TAGS = [
    "DepreciationDepletionAndAmortization",
    "DepreciationAmortizationAndAccretionNet",
    "DepreciationAndAmortization",
]
# ...others split it (Broadcom, AMD, Marvell), where bare Depreciation is only the property
# piece and drops billions of intangible amortization. So I also build depreciation +
# intangible amortization and take whichever is more complete (see da_for_period).
AMORT_TAGS = [
    "AmortizationOfIntangibleAssets",
    "AmortizationOfIntangibleAssetsAndOtherAmortization",
    "AmortizationOfAcquiredIntangibleAssets",
    "FiniteLivedIntangibleAssetsAmortizationExpense",
]

# A full fiscal year is ~365 days. I select annual figures by actual period length, not by
# fp/form flags -- those are coded inconsistently across filers (that's what sent KLAC to a
# 2014 filing). 52/53-week years land at 364/371, so this window catches them all.
ANNUAL_MIN_DAYS = 340
ANNUAL_MAX_DAYS = 380

_COURTESY_DELAY = 0.15  # brief pause between live SEC calls to stay well under their 10/sec


def get_json(url, cache_name=None):
    """Fetch JSON, caching to data/raw/ so repeat runs don't re-hit SEC."""
    if cache_name:
        cached = RAW / cache_name
        if cached.exists():
            return json.loads(cached.read_text())
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    data = r.json()
    if cache_name:
        (RAW / cache_name).write_text(json.dumps(data))
    time.sleep(_COURTESY_DELAY)  # only sleeps on a real network hit, not a cache read
    return data


def resolve_cik(ticker):
    """Map a ticker to its zero-padded 10-digit CIK -- EDGAR keys everything on CIK."""
    data = get_json(
        "https://www.sec.gov/files/company_tickers.json",
        cache_name="sec_company_tickers.json",
    )
    for row in data.values():
        if row["ticker"].upper() == ticker.upper():
            return str(row["cik_str"]).zfill(10)
    return None


def company_facts(ticker):
    """Resolve the ticker and pull its full companyfacts doc (cached per ticker)."""
    cik = resolve_cik(ticker)
    if not cik:
        return None, None
    doc = get_json(
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
        cache_name=f"{ticker}_companyfacts.json",
    )
    return cik, doc


def _is_annual(row):
    """True if this fact covers a ~full fiscal year, judged by its start->end length."""
    start, end = row.get("start"), row.get("end")
    if not start or not end:
        return False
    days = (datetime.date.fromisoformat(end) - datetime.date.fromisoformat(start)).days
    return ANNUAL_MIN_DAYS <= days <= ANNUAL_MAX_DAYS


def latest_annual(facts, tag, unit="USD"):
    """Most recent full-year row for a us-gaap tag, selected by period length not flags."""
    node = facts.get("us-gaap", {}).get(tag)
    if not node:
        return None
    annual = [r for r in node.get("units", {}).get(unit, []) if _is_annual(r)]
    if not annual:
        return None
    # Latest fiscal-year end wins; a period restated in a later filing breaks the tie.
    return max(annual, key=lambda r: (r["end"], r.get("filed", "")))


def annual_value_for_end(facts, tag, end_date, unit="USD"):
    """A tag's full-year value for one specific period-end, so my pieces all line up."""
    node = facts.get("us-gaap", {}).get(tag)
    if not node:
        return None
    matches = [
        r for r in node.get("units", {}).get(unit, [])
        if r.get("end") == end_date and _is_annual(r)
    ]
    if not matches:
        return None
    return max(matches, key=lambda r: r.get("filed", ""))["val"]


def da_for_period(facts, end):
    """
    Total D&A for EBITDA at one period-end. I compute two candidates and take the more
    complete one, so I never silently drop intangible amortization:
      A) the filer's single combined D&A line, if they report one
      B) Depreciation + amortization of intangibles, summed from separate lines
    max(A, B) is safe -- I never add A and B, so no double-count -- and it beats the
    Broadcom-style trap where a "combined" tag actually excludes intangible amort.
    Returns a dict with the chosen value plus both candidates for cross-checking.
    """
    a_val, a_tag = None, None
    for tag in COMBINED_DA_TAGS:
        v = annual_value_for_end(facts, tag, end)
        if v is not None:
            a_val, a_tag = v, tag
            break

    dep = annual_value_for_end(facts, "Depreciation", end)
    amort, amort_tag = None, None
    for tag in AMORT_TAGS:
        x = annual_value_for_end(facts, tag, end)
        if x is not None:
            amort, amort_tag = x, tag
            break
    b_val, b_label = None, None
    if dep is not None or amort is not None:
        b_val = (dep or 0) + (amort or 0)
        b_label = "+".join(p for p in [
            "Depreciation" if dep is not None else None, amort_tag] if p)

    if a_val is None and b_val is None:
        return None

    # Pick the larger (more complete) candidate.
    if b_val is not None and (a_val is None or b_val > a_val):
        value, label, method = b_val, b_label, "summed"
    else:
        value, label, method = a_val, a_tag, "combined"

    return {
        "value": value, "label": label, "method": method,
        "combined": a_val, "summed": b_val,
    }


def ebitda_for(facts):
    """
    My EBITDA = operating income + D&A, both from the latest 10-K.
    Anchors on OperatingIncomeLoss, guards against stale filings, builds a full D&A
    add-back. Returns the pieces, or a reason it couldn't build one.
    """
    oi_row = latest_annual(facts, "OperatingIncomeLoss")
    if not oi_row:
        return {"ok": False, "reason": "no annual OperatingIncomeLoss"}
    end = oi_row["end"]
    oi = oi_row["val"]

    # Staleness guard -- if even the newest annual figure is old, something's wrong.
    age_days = (datetime.date.today() - datetime.date.fromisoformat(end)).days
    if age_days > 500:
        return {"ok": False, "reason": f"stale period {end} ({age_days}d old)",
                "period_end": end, "operating_income": oi}

    da = da_for_period(facts, end)
    if da is None:
        return {"ok": False, "reason": "no D&A tag matched",
                "period_end": end, "operating_income": oi}

    return {
        "ok": True,
        "period_end": end,
        "operating_income": oi,
        "da": da["value"],
        "da_tag": da["label"],
        "da_info": da,
        "ebitda": oi + da["value"],
    }


def available_tags(facts, *needles):
    """List us-gaap tags whose name contains any of these substrings -- for isolate-then-fix."""
    needles = [n.lower() for n in needles]
    keys = facts.get("us-gaap", {}).keys()
    return sorted(k for k in keys if any(n in k.lower() for n in needles))