"""
Pull QCOM's DCF historicals straight from EDGAR, the raw material my valuation model needs.

Same primary source discipline as the rest of the project: I don't hand type historicals into
Excel, I source them from filings so the whole model stays reproducible. This grabs the annual
line items a DCF is built from (revenue, operating income, D&A, capex, working capital),
prints them for me to eyeball, and writes a tidy CSV that feeds the Excel model.

Two things I fixed after the first version:

1. D&A now goes through edgar.da_for_period instead of grabbing the combined tag directly.
   The combined tag can silently exclude intangible amortization, which is the Broadcom trap.
   For QCOM the combined tag says 1.60B while the pieces sum to 1.62B, so my screener and my
   DCF were carrying two different D&A figures for the same company. One source of truth now.

2. NWC is computed here from its components instead of being worked out by hand off a tag dump
   and typed into Excel. If it's in the model it should come out of a filing.

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

# The line items a DCF is built from. For each I list candidate tags in priority order, and
# whether it's a duration flow (income statement, cash flow) or an instant balance sheet item.
# D&A is deliberately NOT in here: it needs da_for_period, not a first tag match.
LINE_ITEMS = {
    "revenue": (["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"], "flow"),
    "operating_income": (["OperatingIncomeLoss"], "flow"),
    "capex": (["PaymentsToAcquirePropertyPlantAndEquipment",
               "PaymentsToAcquireProductiveAssets"], "flow"),
    "income_tax": (["IncomeTaxExpenseBenefit"], "flow"),
    "pretax_income": (["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                       "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"], "flow"),
}

# Operating net working capital = non cash current assets minus non debt current liabilities.
#
# Cash is out because it isn't an operating asset, it's what I'm valuing, and it shows up in
# the equity bridge. Short term debt is out because it's financing and this is unlevered FCF.
# Income taxes are out on both sides because the projection already taxes EBIT at my
# normalized rate, so leaving them in would tax the same thing twice. Stripping taxes also
# makes the ratio far more stable year to year, which is the tell that it's the right call.
#
# I use the receivables and inventory SUBTOTALS. QCOM tags the components alongside them
# (AccountsReceivableNetCurrent, OtherReceivablesNetCurrent, UnbilledContractsReceivable all
# sit inside AccountsAndOtherReceivablesNetCurrent) and adding both would double count.
#
# AccruedIncomeTaxesCurrent is a footnote detail INSIDE OtherLiabilitiesCurrent, not a
# separate line, so I subtract it back out of that total rather than treating it separately.
# The liability side reconciles: AP + employee related + deferred revenue + other, plus short
# term debt, ties to LiabilitiesCurrent.
NWC_ASSETS = ["AccountsAndOtherReceivablesNetCurrent", "InventoryNet", "OtherAssetsCurrent"]
NWC_LIABS = ["AccountsPayableCurrent", "EmployeeRelatedLiabilitiesCurrent",
             "DeferredRevenueCurrent", "OtherLiabilitiesCurrent"]
NWC_LIAB_EXCLUDE = ["AccruedIncomeTaxesCurrent"]  # inside OtherLiabilitiesCurrent, back it out

# Liquidity for the equity bridge. At the screen level I use cash and equivalents only, because
# marketable securities tags are inconsistent across filers. At the DCF stage I'm valuing one
# company and precision matters, so I take the full liquid balance. I leave out restricted cash
# (QCOM's $2.32B was escrowed for the Alphawave deal, so it wasn't spendable at year end) and
# strategic equity stakes in private companies, which aren't liquid.
LIQUIDITY = ["CashAndCashEquivalentsAtCarryingValue", "MarketableSecuritiesCurrent"]


def annual_series(facts, tags, kind, n=YEARS_BACK):
    """Return {fiscal_year_end: value} for the most recent n years, trying tags in order."""
    node_tags = facts.get("us-gaap", {})
    for tag in tags:
        if tag not in node_tags:
            continue
        rows = node_tags[tag].get("units", {}).get("USD", [])
        if kind == "flow":
            picked = [r for r in rows if edgar._is_annual(r)]
        else:  # instant: no 'start', take fiscal year end 10-K values
            picked = [r for r in rows if r.get("end") and "start" not in r
                      and str(r.get("form", "")).startswith("10-K")]
        if not picked:
            continue
        by_end = {}
        for r in sorted(picked, key=lambda x: (x["end"], x.get("filed", ""))):
            by_end[r["end"]] = r["val"]  # later filing overwrites, so latest restatement wins
        if by_end:
            ends = sorted(by_end)[-n:]
            return {e: by_end[e] for e in ends}, tag
    return {}, None


def da_series(facts, ends):
    """
    D&A per fiscal year, through da_for_period so I get the full add back.

    This is the whole point of the fix. da_for_period takes max(combined tag, summed pieces),
    so it can't silently drop intangible amortization the way a first tag match does.
    """
    out, tags = {}, {}
    for e in ends:
        res = edgar.da_for_period(facts, e)
        if res:
            out[e] = res["value"]
            tags[e] = f"{res['label']} ({res['method']})"
    return out, tags


def nwc_series(facts, ends):
    """Operating NWC per fiscal year, built from components. Returns the total and the parts."""
    totals, parts = {}, {}
    for e in ends:
        row = {}
        for tag in NWC_ASSETS + NWC_LIABS + NWC_LIAB_EXCLUDE:
            row[tag] = edgar.instant_value_for_end(facts, tag, e) or 0.0

        assets = sum(row[t] for t in NWC_ASSETS)
        liabs = sum(row[t] for t in NWC_LIABS) - sum(row[t] for t in NWC_LIAB_EXCLUDE)

        totals[e] = assets - liabs
        parts[e] = row
    return totals, parts


def liquidity_series(facts, ends):
    """Cash and equivalents plus current marketable securities, for the equity bridge."""
    out = {}
    for e in ends:
        out[e] = sum(edgar.instant_value_for_end(facts, tag, e) or 0.0 for tag in LIQUIDITY)
    return out


def main():
    cik, doc = edgar.company_facts(TICKER)
    if not doc:
        sys.exit(f"couldn't fetch {TICKER}")
    facts = doc.get("facts", {})
    print(f"{doc.get('entityName')} (CIK {cik})\n")

    collected, tag_used = {}, {}
    for name, (tags, kind) in LINE_ITEMS.items():
        series, tag = annual_series(facts, tags, kind)
        collected[name] = series
        tag_used[name] = tag

    # Everything else anchors to the fiscal year ends revenue reports, so my rows line up.
    ends = sorted(collected["revenue"])

    collected["da"], da_tags = da_series(facts, ends)
    collected["nwc"], nwc_parts = nwc_series(facts, ends)
    collected["liquidity"] = liquidity_series(facts, ends)
    tag_used["da"] = "da_for_period"
    tag_used["nwc"] = "computed from components"
    tag_used["liquidity"] = "cash + marketable securities"

    order = ["revenue", "operating_income", "da", "capex", "income_tax", "pretax_income",
             "nwc", "liquidity"]

    for name in order:
        series = collected.get(name, {})
        shown = "  ".join(f"{e[:4]}:{v/1e9:6.2f}B" for e, v in sorted(series.items())) \
            or "-- NOT FOUND --"
        print(f"{name:18} [{tag_used.get(name)}]")
        print(f"    {shown}")

    # Which D&A path fired each year. If it says 'combined' anywhere, I want to know, because
    # that's the case where intangible amortization can go missing.
    print("\nD&A method by year (watch for 'combined', that's where amort can be dropped)")
    for e in ends:
        print(f"    {e[:4]}: {da_tags.get(e, 'none')}")

    # The ratios the model actually consumes. I set D&A and NWC off the most recent year rather
    # than a 4 year average because both are structurally trending, and because D&A has to be
    # consistent with the operating margin assumption it's being added back on top of.
    rev = collected["revenue"]
    print("\nratios (latest year, what the model uses)")
    last = ends[-1]
    print(f"    D&A % of revenue    {collected['da'][last] / rev[last]:.4%}")
    print(f"    NWC % of revenue    {collected['nwc'][last] / rev[last]:.4%}")
    print(f"    capex % of revenue  {collected['capex'][last] / rev[last]:.4%}  "
          f"(I use a normalized 3%, capex is cyclical)")

    # Assemble into a year indexed table (fiscal years as columns).
    all_years = sorted({e for s in collected.values() for e in s})
    table = pd.DataFrame(index=order, columns=all_years, dtype=float)
    for name in order:
        for e, v in collected.get(name, {}).items():
            table.at[name, e] = v

    out = _ROOT / "data" / "processed" / f"{TICKER}_dcf_inputs.csv"
    table.to_csv(out)
    print(f"\nSaved -> {out.relative_to(_ROOT)}")

    # The NWC component detail, so I can audit the total and defend it in an interview.
    parts_table = pd.DataFrame(nwc_parts)
    parts_out = _ROOT / "data" / "processed" / f"{TICKER}_nwc_components.csv"
    parts_table.to_csv(parts_out)
    print(f"Saved -> {parts_out.relative_to(_ROOT)}")

    print("\nAny '-- NOT FOUND --' above means I need to add the tag QCOM actually uses.")


if __name__ == "__main__":
    main()