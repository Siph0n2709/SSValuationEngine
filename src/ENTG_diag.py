"""
Two ENTG diagnostics I need before I can set assumptions.

1. Split D&A into depreciation and intangible amortization, by year.

   ENTG's capex/D&A ratio fell to 0.77x, which read naively says they're underinvesting and
   I can't extend that into perpetuity. But D&A climbed from 8.5% to 12.2% of revenue over the
   same stretch, and that's probably CMC Materials purchase accounting rather than a heavier
   asset base. Acquired intangible amortization doesn't need replacement capex the way PP&E
   depreciation does, so the ratio that actually matters for terminal value is capex over
   DEPRECIATION, not capex over total D&A. If depreciation alone is comfortably below capex
   then ENTG is reinvesting above maintenance and the concern goes away.

2. Dump every interest tag at each year end.

   My extract pulled InterestExpense and got 2021 to 2024, one year behind everything else.
   annual_series picks a tag once and stops, so if the filer switched tags partway through I
   silently get the wrong window. Same shape as the D&A bug: the candidate list resolves at the
   tag level instead of per year. I need FY2025 interest to back out cost of debt, so I need to
   see which tag actually carries it.

Run:  python src/entg_diagnostics.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import edgar

TICKER = "ENTG"
YEARS_BACK = 4

INTEREST_NEEDLES = ["interest"]


def fiscal_year_ends(facts, n=YEARS_BACK):
    """Fiscal year ends off the revenue series, so these line up with my extract."""
    ends = set()
    for tag in ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"]:
        node = facts.get("us-gaap", {}).get(tag)
        if not node:
            continue
        for r in node.get("units", {}).get("USD", []):
            if edgar._is_annual(r):
                ends.add(r["end"])
        if ends:
            break
    return sorted(ends)[-n:]


def main():
    cik, doc = edgar.company_facts(TICKER)
    if not doc:
        sys.exit(f"couldn't resolve {TICKER}")
    facts = doc["facts"]

    ends = fiscal_year_ends(facts)
    print(f"{TICKER}  CIK {cik}")
    print(f"fiscal year ends: {', '.join(ends)}\n")

    # ---- 1. D&A split -------------------------------------------------------------
    print("D&A SPLIT")
    print(f"  {'year':6}{'deprec':>10}{'amort':>10}{'total':>10}{'capex':>10}"
          f"{'capex/dep':>12}{'capex/D&A':>12}")

    for e in ends:
        dep = 0.0
        for tag in edgar.DEPRECIATION_TAGS:
            v = edgar.annual_value_for_end(facts, tag, e)
            if v is not None:
                dep += v

        amort = None
        for tag in edgar.AMORT_TAGS:
            v = edgar.annual_value_for_end(facts, tag, e)
            if v is not None:
                amort = v
                break
        amort = amort or 0.0

        total = dep + amort
        capex = (edgar.annual_value_for_end(facts, "PaymentsToAcquirePropertyPlantAndEquipment", e)
                 or edgar.annual_value_for_end(facts, "PaymentsToAcquireProductiveAssets", e)
                 or 0.0)

        r_dep = capex / dep if dep else 0.0
        r_tot = capex / total if total else 0.0
        print(f"  {e[:4]:6}{dep/1e9:>10.2f}{amort/1e9:>10.2f}{total/1e9:>10.2f}"
              f"{capex/1e9:>10.2f}{r_dep:>11.2f}x{r_tot:>11.2f}x")

    print("\n  capex/deprec is the ratio that matters for terminal value. Amortization of")
    print("  acquired intangibles doesn't need replacement capex, depreciation of PP&E does.")

    # ---- 2. interest tags ---------------------------------------------------------
    print("\nINTEREST TAGS (annual values at each year end)")
    tags = edgar.available_tags(facts, *INTEREST_NEEDLES)

    header = f"  {'tag':<58}" + "".join(f"{e[:4]:>10}" for e in ends)
    print(header)
    print("  " + "." * (len(header) - 2))

    for tag in tags:
        vals = [edgar.annual_value_for_end(facts, tag, e) for e in ends]
        if all(v is None for v in vals):
            continue
        cells = "".join(f"{edgar._bil(v):>10}" for v in vals)
        print(f"  {tag:<58}{cells}")

    print("\n  I want whichever tag carries a real interest expense in the LATEST year, so I")
    print("  can back out cost of debt as interest over average total debt.")

    # ---- 3. debt, for the cost of debt calc ----------------------------------------
    print("\nTOTAL DEBT (for averaging)")
    for e in ends:
        debt, detail = edgar.total_debt_for(facts, e)
        print(f"  {e[:4]}  {debt/1e9:>8.2f}   {detail}")

    print("\nvalues in $B.")


if __name__ == "__main__":
    main()