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
        '  Run:  $env:SEC_USER_AGENT="Your Name your_email@example.com"'
    )

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})

# Paths resolve off this file, not the working directory, so it runs from anywhere.
ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
RAW.mkdir(parents=True, exist_ok=True)

# For EBITDA I want the FULL D&A add-back: depreciation AND amortization of intangibles.
COMBINED_DA_TAGS = [
    "DepreciationDepletionAndAmortization",
    "DepreciationAmortizationAndAccretionNet",
    "DepreciationAndAmortization",
]
# ...if a filer splits it, bare Depreciation drops intangible amort, so I sum the pieces.
AMORT_TAGS = [
    "AmortizationOfIntangibleAssets",
    "AmortizationOfIntangibleAssetsAndOtherAmortization",
    "AmortizationOfAcquiredIntangibleAssets",
    "FiniteLivedIntangibleAssetsAmortizationExpense",
]

# Revenue / cost tags I use to reconstruct operating income when the direct tag is gone.
REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
]
COST_OF_REVENUE_TAGS = [
    "CostOfRevenue",
    "CostOfGoodsAndServicesSold",
]
SGA_TAGS = [
    "SellingGeneralAndAdministrativeExpense",
    "GeneralAndAdministrativeExpense",
]

# A full fiscal year is ~365 days. I select annual figures by actual period length, not by
# fp/form flags -- those are coded inconsistently across filers (that's what sent KLAC to a
# 2014 filing). 52/53-week years land at 364/371, so this window catches them all.
ANNUAL_MIN_DAYS = 340
ANNUAL_MAX_DAYS = 380
STALE_AFTER_DAYS = 500  # if even the newest annual figure is older than this, don't trust it

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


def _first_annual_for_end(facts, tags, end):
    """First of several candidate tags that has an annual value at this period-end."""
    for t in tags:
        v = annual_value_for_end(facts, t, end)
        if v is not None:
            return v
    return None


def operating_income_for(facts):
    """
    Operating income for the latest fiscal year.
    I use the tagged OperatingIncomeLoss when it's current. When a filer stops tagging it
    (KLA did after 2015), I reconstruct it from income-statement components at the same
    period-end, in priority order. Returns (value, end, method).
    """
    # 1. Direct tag, if it isn't stale.
    row = latest_annual(facts, "OperatingIncomeLoss")
    if row:
        age = (datetime.date.today() - datetime.date.fromisoformat(row["end"])).days
        if age <= STALE_AFTER_DAYS:
            return row["val"], row["end"], "OperatingIncomeLoss"

    # Reconstruct -- anchor everything on the latest annual revenue period.
    rev_row = None
    for t in REVENUE_TAGS:
        rev_row = latest_annual(facts, t)
        if rev_row:
            break
    if not rev_row:
        return None, None, None
    end, rev = rev_row["end"], rev_row["val"]

    # 2. Gross profit minus operating expenses (cleanest when both subtotals are tagged).
    gp = annual_value_for_end(facts, "GrossProfit", end)
    opex = annual_value_for_end(facts, "OperatingExpenses", end)
    if gp is not None and opex is not None:
        return gp - opex, end, "GrossProfit-OpEx"

    # 3. Revenue minus total costs-and-expenses (this subtotal already includes opex).
    cae = annual_value_for_end(facts, "CostsAndExpenses", end)
    if cae is not None:
        return rev - cae, end, "Rev-CostsAndExpenses"

    # 4. Revenue minus cost of revenue minus R&D minus SG&A (component build -- KLA lands here).
    cor = _first_annual_for_end(facts, COST_OF_REVENUE_TAGS, end)
    rd = annual_value_for_end(facts, "ResearchAndDevelopmentExpense", end)
    sga = _first_annual_for_end(facts, SGA_TAGS, end)
    if cor is not None and (rd is not None or sga is not None):
        return rev - cor - (rd or 0) - (sga or 0), end, "Rev-CoR-RD-SGA"

    return None, None, None


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

    if b_val is not None and (a_val is None or b_val > a_val):
        value, label, method = b_val, b_label, "summed"
    else:
        value, label, method = a_val, a_tag, "combined"

    return {"value": value, "label": label, "method": method,
            "combined": a_val, "summed": b_val}


def ebitda_for(facts):
    """
    My EBITDA = operating income + D&A, both from the latest fiscal year.
    Operating income comes from operating_income_for (direct tag or reconstruction);
    D&A from da_for_period (full add-back). Returns the pieces, or a reason it failed.
    """
    oi, end, oi_method = operating_income_for(facts)
    if oi is None:
        return {"ok": False, "reason": "no operating income (direct or reconstructed)"}

    da = da_for_period(facts, end)
    if da is None:
        return {"ok": False, "reason": "no D&A tag matched",
                "period_end": end, "operating_income": oi}

    return {
        "ok": True,
        "period_end": end,
        "operating_income": oi,
        "oi_method": oi_method,
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


# --- Balance-sheet layer (instant facts) --------------------------------------------
# Balance-sheet items are point-in-time (instant) facts: they carry 'end' but no 'start'.
# I anchor everything to the latest 10-K year-end so net debt lines up with my annual
# EBITDA, one consistent snapshot per name.

BALANCE_ANCHOR_TAGS = [
    "Assets",
    "LiabilitiesAndStockholdersEquity",
    "CashAndCashEquivalentsAtCarryingValue",
]


def _bil(x):
    return "--" if x is None else f"{x/1e9:.2f}"


def _instant_rows(facts, tag, unit="USD"):
    node = facts.get("us-gaap", {}).get(tag)
    if not node:
        return []
    return [r for r in node.get("units", {}).get(unit, [])
            if r.get("end") and "start" not in r]


def balance_sheet_date(facts):
    """Latest fiscal-year-end balance-sheet date, anchored on a universal 10-K line."""
    for tag in BALANCE_ANCHOR_TAGS:
        rows = [r for r in _instant_rows(facts, tag)
                if str(r.get("form", "")).startswith("10-K")]
        if rows:
            return max(rows, key=lambda r: (r["end"], r.get("filed", "")))["end"]
    return None


def instant_value_for_end(facts, tag, end, unit="USD"):
    """A balance-sheet tag's value at one specific year-end (newest filing on ties)."""
    rows = [r for r in _instant_rows(facts, tag, unit) if r.get("end") == end]
    if not rows:
        return None
    return max(rows, key=lambda r: r.get("filed", ""))["val"]


def total_debt_for(facts, end):
    """
    Total debt = long-term debt (incl. current portion) + finance leases.
    Operating leases are EXCLUDED on purpose: my EBITDA is struck after operating-lease
    expense, so adding the operating-lease liability to EV would double-count it (that's
    the ~$2.9B FMP bundled into NVDA's 'total debt'). Returns (value, detail).
    """
    lt = instant_value_for_end(facts, "LongTermDebt", end)  # total line when reported
    if lt is None:
        nc = instant_value_for_end(facts, "LongTermDebtNoncurrent", end)
        cur = (instant_value_for_end(facts, "LongTermDebtCurrent", end)
               or instant_value_for_end(facts, "DebtCurrent", end))
        if nc is not None or cur is not None:
            lt = (nc or 0) + (cur or 0)

    fl = instant_value_for_end(facts, "FinanceLeaseLiability", end)
    if fl is None:
        flnc = instant_value_for_end(facts, "FinanceLeaseLiabilityNoncurrent", end)
        flc = instant_value_for_end(facts, "FinanceLeaseLiabilityCurrent", end)
        if flnc is not None or flc is not None:
            fl = (flnc or 0) + (flc or 0)

    if lt is None and fl is None:
        # No funded-debt tags at the year-end. For my universe I confirmed this means
        # genuinely debt-free (TER, MPWR, LSCC each repaid what they once had) rather than
        # debt hiding under an exotic tag -- companies that carry debt report LongTermDebt.
        # So I record $0, not a miss.
        return 0.0, "debt-free (no debt tags at year-end)"
    return (lt or 0) + (fl or 0), f"LT={_bil(lt)} finLease={_bil(fl)}"


def cash_for(facts, end):
    """
    Cash for net debt = cash & equivalents only.
    I keep marketable securities OUT at the screen level: their tags are inconsistent
    across filers and even across years for one filer (NVDA's changed year to year), while
    cash & equivalents is universal and ties to my oracle exactly. I refine liquidity by
    hand for the 2-3 names that reach the DCF stage, where precision actually matters.
    """
    return instant_value_for_end(facts, "CashAndCashEquivalentsAtCarryingValue", end)


def shares_outstanding(facts):
    """Most current shares outstanding -- the dei cover-page count, newer than the balance date."""
    for ns, tag in [("dei", "EntityCommonStockSharesOutstanding"),
                    ("us-gaap", "CommonStockSharesOutstanding")]:
        node = facts.get(ns, {}).get(tag)
        if not node:
            continue
        rows = [r for r in node.get("units", {}).get("shares", [])
                if r.get("end") and "start" not in r]
        if rows:
            return max(rows, key=lambda r: (r["end"], r.get("filed", "")))["val"]
    return None


def ev_inputs(facts):
    """The balance-sheet inputs for EV: total debt, cash, net debt, shares, at the year-end."""
    end = balance_sheet_date(facts)
    if end is None:
        return {"ok": False, "reason": "no balance-sheet date"}
    debt, detail = total_debt_for(facts, end)
    cash = cash_for(facts, end)
    return {
        "ok": True,
        "bs_date": end,
        "total_debt": debt,
        "debt_detail": detail,
        "cash": cash,
        "net_debt": (debt or 0) - (cash or 0),
        "shares": shares_outstanding(facts),
    }