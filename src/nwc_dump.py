"""
Diagnostic: dump every current asset and current liability tag QCOM reports, by fiscal year.

I'm not guessing at working capital tags. Filers split accrued liabilities across several
lines (AccruedLiabilitiesCurrent, OtherLiabilitiesCurrent, EmployeeRelatedLiabilitiesCurrent),
and if I miss one my NWC base is understated. Same failure mode as the Broadcom intangible
amortization problem. So I look before I build. This prints what QCOM actually tags at each
year end and I pick the real lines off the output by eye.

Run:  python src/nwc_dump.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import edgar

TICKER = "QCOM"
YEARS_BACK = 4

# Substrings that catch anything working capital related. I keep this wide on purpose.
# This is the dump, not the extract, so I'd rather see too much than miss a line.
NEEDLES = [
    "receivable", "inventory", "prepaid", "payable", "accrued",
    "AssetsCurrent", "LiabilitiesCurrent", "unearned", "deferredrevenue",
]


def fiscal_year_ends(facts, n=YEARS_BACK):
    """
    The last n fiscal year ends, taken off 10-K instant rows.

    A 10-K carries the prior year balance sheet as a comparative, so the year ends I need
    are already in the filings I've cached and I don't have to make extra SEC calls. I
    anchor on universal balance sheet lines and filter to form 10-K so I never pick up a
    10-Q date. That's the bug that pulled KLA's balance sheet from a quarterly filing.
    """
    ends = set()
    for tag in edgar.BALANCE_ANCHOR_TAGS:
        for r in edgar._instant_rows(facts, tag):
            if str(r.get("form", "")).startswith("10-K"):
                ends.add(r["end"])
    return sorted(ends)[-n:]


def main():
    cik, doc = edgar.company_facts(TICKER)
    if not doc:
        sys.exit(f"couldn't resolve {TICKER}")
    facts = doc["facts"]

    ends = fiscal_year_ends(facts)
    print(f"{TICKER}  CIK {cik}")
    print(f"fiscal year ends: {', '.join(ends)}\n")

    tags = edgar.available_tags(facts, *NEEDLES)

    # I only show tags that carry a value at one of my year ends. A tag can exist in
    # companyfacts but be stale or quarterly only, and those are just noise here.
    header = f"{'tag':<58}" + "".join(f"{e:>14}" for e in ends)
    print(header)
    print("." * len(header))

    for tag in tags:
        vals = [edgar.instant_value_for_end(facts, tag, e) for e in ends]
        if all(v is None for v in vals):
            continue
        cells = "".join(f"{edgar._bil(v):>14}" for v in vals)
        print(f"{tag:<58}{cells}")

    print("\nvalues in $B. I want receivables, inventory, other current assets, payables,")
    print("and accrued liabilities. Cash and short term debt stay out of NWC.")


if __name__ == "__main__":
    main()