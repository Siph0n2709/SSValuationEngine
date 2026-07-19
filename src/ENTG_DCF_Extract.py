"""
Pull ENTG's DCF historicals straight from EDGAR, same as I did for QCOM.

Separate file from QCOM_DCF_Extract.py on purpose. The line item tags are not the same across
the two names, and I would rather keep each config next to the company it belongs to than
build a lookup that hides the differences. If I add a third DCF I should generalize this
instead of copying it again.

What's different about ENTG versus QCOM:

  Receivables come through AccountsNotesAndLoansReceivableNetCurrent, which is the subtotal.
  AccountsReceivableNetCurrent only shows up in 2025 and AccountsReceivableGross is the same
  figure, so picking either of those would either miss years or double count.

  Accrued income taxes are a SEPARATE current liability line here. For QCOM they were footnote
  detail sitting inside OtherLiabilitiesCurrent, so I had to back them out. For ENTG I just
  don't add them. Same policy either way, taxes stay out of operating NWC, but the mechanic is
  different and hardcoding QCOM's would subtract them twice.

  No deferred revenue tag. ENTG bills on shipment as an equipment supplier, it isn't a licensor
  collecting cash upfront, so there's no unearned revenue line to include.

Reuses my validated edgar.py client. Run with SEC_USER_AGENT set, same as the screener.
"""

import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "src", _ROOT / "data", _ROOT):
    sys.path.insert(0, str(_p))

import edgar

TICKER = "ENTG"
YEARS_BACK = 4

# Candidate tags in priority order. D&A is deliberately not here: it needs da_for_period, not a
# first tag match, or I risk dropping intangible amortization the way I did on AMD and MRVL.
LINE_ITEMS = {
    "revenue": (["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"], "flow"),
    "operating_income": (["OperatingIncomeLoss"], "flow"),
    "capex": (["PaymentsToAcquirePropertyPlantAndEquipment",
               "PaymentsToAcquireProductiveAssets"], "flow"),
    "income_tax": (["IncomeTaxExpenseBenefit"], "flow"),
    "pretax_income": (["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                       "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"], "flow"),
    "interest_expense": (["InterestExpense", "InterestExpenseDebt",
                          "InterestExpenseNonoperating"], "flow"),
}

# Operating net working capital = non cash current assets minus non debt current liabilities.
# Cash is out because it isn't operating, it's what I'm valuing, and it shows up in the equity
# bridge. Short term debt is out because it's financing and this is unlevered FCF. Income taxes
# are out because the projection already taxes EBIT at my normalized rate.
#
# I use the receivables and inventory SUBTOTALS. The component tags sit inside them and adding
# both would double count.
NWC_ASSETS = ["AccountsNotesAndLoansReceivableNetCurrent", "InventoryNet", "OtherAssetsCurrent"]
NWC_LIABS = ["AccountsPayableCurrent", "EmployeeRelatedLiabilitiesCurrent",
             "OtherLiabilitiesCurrent"]

# Liquidity for the equity bridge. At the screen level I use cash and equivalents only because
# marketable securities tags are inconsistent across filers. At the DCF stage I'm valuing one
# company so I take the full liquid balance. If the securities tags come back empty for ENTG
# that's a real answer, not a miss: they may simply hold cash.
LIQUIDITY = ["CashAndCashEquivalentsAtCarryingValue", "MarketableSecuritiesCurrent",
             "ShortTermInvestments", "OtherShortTermInvestments"]


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
    """D&A per fiscal year through da_for_period, so I get the full add back every year."""
    out, tags = {}, {}
    for e in ends:
        res = edgar.da_for_period(facts, e)
        if res:
            out[e] = res["value"]
            tags[e] = f"{res['label']} ({res['method']})"
    return out, tags


def nwc_series(facts, ends):
    """Operating NWC per fiscal year, built from components. Returns totals and the parts."""
    totals, parts = {}, {}
    for e in ends:
        row = {}
        for tag in NWC_ASSETS + NWC_LIABS:
            row[tag] = edgar.instant_value_for_end(facts, tag, e) or 0.0
        assets = sum(row[t] for t in NWC_ASSETS)
        liabs = sum(row[t] for t in NWC_LIABS)
        totals[e] = assets - liabs
        parts[e] = row
    return totals, parts


def liquidity_series(facts, ends):
    """Cash and equivalents plus any liquid securities, for the equity bridge."""
    out, detail = {}, {}
    for e in ends:
        row = {t: edgar.instant_value_for_end(facts, t, e) or 0.0 for t in LIQUIDITY}
        out[e] = sum(row.values())
        detail[e] = row
    return out, detail


def total_debt_series(facts, ends):
    """Total debt per year. ENTG is levered so I want the trend, not just the latest snapshot."""
    return {e: edgar.total_debt_for(facts, e)[0] for e in ends}


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

    # Everything anchors to the fiscal year ends revenue reports, so my rows line up.
    ends = sorted(collected["revenue"])

    collected["da"], da_tags = da_series(facts, ends)
    collected["nwc"], nwc_parts = nwc_series(facts, ends)
    collected["liquidity"], liq_parts = liquidity_series(facts, ends)
    collected["total_debt"] = total_debt_series(facts, ends)
    tag_used["da"] = "da_for_period"
    tag_used["nwc"] = "computed from components"
    tag_used["liquidity"] = "cash + liquid securities"
    tag_used["total_debt"] = "total_debt_for"

    order = ["revenue", "operating_income", "da", "capex", "income_tax", "pretax_income",
             "interest_expense", "nwc", "liquidity", "total_debt"]

    for name in order:
        series = collected.get(name, {})
        shown = "  ".join(f"{e[:4]}:{v/1e9:6.2f}B" for e, v in sorted(series.items())) \
            or "-- NOT FOUND --"
        print(f"{name:18} [{tag_used.get(name)}]")
        print(f"    {shown}")

    print("\nD&A method by year (watch for 'combined', that's where amort can be dropped)")
    for e in ends:
        print(f"    {e[:4]}: {da_tags.get(e, 'none')}")

    rev = collected["revenue"]
    last = ends[-1]

    print("\nratios by year")
    print(f"    {'year':6}{'op margin':>12}{'D&A %':>10}{'capex %':>10}{'NWC %':>10}"
          f"{'capex/D&A':>12}")
    for e in ends:
        r = rev.get(e)
        if not r:
            continue
        om = collected["operating_income"].get(e, 0) / r
        da = collected["da"].get(e, 0) / r
        cx = collected["capex"].get(e, 0) / r
        nw = collected["nwc"].get(e, 0) / r
        ratio = (collected["capex"].get(e, 0) / collected["da"][e]) if collected["da"].get(e) else 0
        print(f"    {e[:4]:6}{om:>11.1%}{da:>10.1%}{cx:>10.1%}{nw:>10.1%}{ratio:>11.2f}x")

    # ENTG is capital intensive in a way QCOM isn't. D&A is nearly as large as EBIT here, so the
    # capex and D&A assumptions drive FCF far more than they did for QCOM. Capex below D&A for
    # long means a shrinking asset base, so if that ratio sits under 1.0 I need a reason.
    print(f"\n    capex/D&A over the period: "
          f"{sum(collected['capex'].get(e, 0) for e in ends) / sum(collected['da'].get(e, 0) for e in ends):.2f}x")

    print("\nliquidity detail (which tags actually carried a balance)")
    for tag in LIQUIDITY:
        vals = [liq_parts[e].get(tag, 0.0) for e in ends]
        if any(v for v in vals):
            print(f"    {tag:<44}" + "".join(f"{v/1e9:>9.2f}" for v in vals))

    all_years = sorted({e for s in collected.values() for e in s})
    table = pd.DataFrame(index=order, columns=all_years, dtype=float)
    for name in order:
        for e, v in collected.get(name, {}).items():
            table.at[name, e] = v

    out = _ROOT / "data" / "processed" / f"{TICKER}_dcf_inputs.csv"
    table.to_csv(out)
    print(f"\nSaved -> {out.relative_to(_ROOT)}")

    parts_out = _ROOT / "data" / "processed" / f"{TICKER}_nwc_components.csv"
    pd.DataFrame(nwc_parts).to_csv(parts_out)
    print(f"Saved -> {parts_out.relative_to(_ROOT)}")

    print("\nAny '-- NOT FOUND --' above means I need to add the tag ENTG actually uses.")
    print("ENTG divested businesses in 2022 (DisposalGroup tags on the balance sheet), so I")
    print("should check whether 2022 is comparable before averaging it with the later years.")


if __name__ == "__main__":
    main()