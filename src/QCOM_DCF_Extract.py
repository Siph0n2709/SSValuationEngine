"""
Pull QCOM's DCF historicals straight from EDGAR -- the raw material my valuation model needs.

Same primary-source discipline as the rest of the project: I don't hand-type historicals into
Excel, I source them from filings so the whole model stays reproducible. This grabs the annual
line items a DCF is built from (revenue, operating income, D&A, capex, working-capital pieces),
prints them for me to eyeball, and writes a tidy CSV that feeds the Excel model.

Reuses my validated edgar.py client. Run with SEC_USER_AGENT set, same as the screener.
"""

import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "src", _ROOT / "data", _ROOT):
    sys.path.insert(0, str(_p))

import edgar

TICKER = "QCOM"
YEARS_BACK = 4  # how many recent fiscal years of history I want as the base for forecasting

# The line items a DCF is built from. For each I list candidate tags in priority order,
# and whether it's a duration flow (income statement / cash flow) or an instant balance item.
LINE_ITEMS = {
    "revenue": (["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"], "flow"),
    "operating_income": (["OperatingIncomeLoss"], "flow"),
    "da": (["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
            "DepreciationAndAmortization"], "flow"),
    "capex": (["PaymentsToAcquirePropertyPlantAndEquipment",
               "PaymentsToAcquireProductiveAssets"], "flow"),
    "income_tax": (["IncomeTaxExpenseBenefit"], "flow"),
    "pretax_income": (["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                       "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"], "flow"),
    # working-capital pieces (instant balance-sheet items)
    "receivables": (["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"], "instant"),
    "inventory": (["InventoryNet"], "instant"),
    "payables": (["AccountsPayableCurrent", "AccountsPayableTradeCurrent"], "instant"),
}


def annual_series(facts, tags, kind, n=YEARS_BACK):
    """Return {fiscal_year_end: value} for the most recent n years, trying tags in order."""
    node_tags = facts.get("us-gaap", {})
    for tag in tags:
        if tag not in node_tags:
            continue
        rows = node_tags[tag].get("units", {}).get("USD", [])
        if kind == "flow":
            picked = [r for r in rows if edgar._is_annual(r)]
        else:  # instant: no 'start', take fiscal-year-end 10-K values
            picked = [r for r in rows if r.get("end") and "start" not in r
                      and str(r.get("form", "")).startswith("10-K")]
        if not picked:
            continue
        by_end = {}
        for r in sorted(picked, key=lambda x: (x["end"], x.get("filed", ""))):
            by_end[r["end"]] = r["val"]  # later filing overwrites -> latest restatement wins
        if by_end:
            ends = sorted(by_end)[-n:]
            return {e: by_end[e] for e in ends}, tag
    return {}, None


def main():
    cik, doc = edgar.company_facts(TICKER)
    if not doc:
        sys.exit(f"couldn't fetch {TICKER}")
    facts = doc.get("facts", {})
    print(f"{doc.get('entityName')} (CIK {cik})\n")

    collected = {}
    tag_used = {}
    for name, (tags, kind) in LINE_ITEMS.items():
        series, tag = annual_series(facts, tags, kind)
        collected[name] = series
        tag_used[name] = tag
        shown = "  ".join(f"{e[:4]}:{v/1e9:6.2f}B" for e, v in series.items()) or "-- NOT FOUND --"
        print(f"{name:18} [{tag}]")
        print(f"    {shown}")

    # Assemble into a year-indexed table (fiscal years as columns).
    all_years = sorted({e for s in collected.values() for e in s})
    table = pd.DataFrame(index=list(LINE_ITEMS.keys()), columns=all_years, dtype=float)
    for name, series in collected.items():
        for e, v in series.items():
            table.at[name, e] = v

    out = _ROOT / "data" / "processed" / f"{TICKER}_dcf_inputs.csv"
    table.to_csv(out)
    print(f"\nSaved -> {out.relative_to(_ROOT)}")
    print("\nAny '-- NOT FOUND --' above means I need to add the tag QCOM actually uses.")


if __name__ == "__main__":
    main()