"""
Trace exactly how my EBITDA gets built for one ticker.

LSCC came out of the screen with operating income of 11.2M and D&A of 22.1M, which gives a
559x multiple. Lattice does a few hundred million in revenue so that operating income is
almost certainly wrong. I don't want to guess which part broke, so this prints the path
my code actually took: which operating income method it landed on, which D&A tag it chose,
and then the full income statement tag dump at the same period end so I can see what was
available to it.

Same pattern I used on KLA and Broadcom. Look first, then fix.

Run:  python src/income_trace.py LSCC
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import edgar

NEEDLES = [
    "revenue", "revenues", "cost", "operating", "grossprofit", "researchand",
    "sellinggeneral", "depreciation", "amortization", "expense",
]


def main():
    ticker = sys.argv[1] if len(sys.argv) > 1 else "LSCC"

    cik, doc = edgar.company_facts(ticker)
    if not doc:
        sys.exit(f"couldn't resolve {ticker}")
    facts = doc["facts"]

    oi, end, method = edgar.operating_income_for(facts)
    print(f"{ticker}  CIK {cik}")
    print(f"period end:        {end}")
    print(f"operating income:  {edgar._bil(oi)}B   via {method}\n")

    da = edgar.da_for_period(facts, end)
    if da:
        print("D&A")
        print(f"  chosen:   {edgar._bil(da['value'])}B  ({da['label']}, {da['method']})")
        print(f"  combined: {edgar._bil(da['combined'])}B")
        print(f"  summed:   {edgar._bil(da['summed'])}B\n")
    else:
        print("D&A: nothing matched\n")

    res = edgar.ebitda_for(facts)
    if res.get("ok"):
        print(f"EBITDA: {edgar._bil(res['ebitda'])}B\n")

    # Now the raw material. Everything income statement shaped that has an annual value at
    # this same period end, so I can see what my reconstruction had to work with.
    print(f"annual tags at {end}")
    print("." * 78)
    for tag in edgar.available_tags(facts, *NEEDLES):
        v = edgar.annual_value_for_end(facts, tag, end)
        if v is None:
            continue
        print(f"  {tag:<62}{edgar._bil(v):>10}")

    print("\nvalues in $B. I'm checking whether the direct OperatingIncomeLoss tag exists")
    print("at this period end, and if my reconstruction picked the right components.")


if __name__ == "__main__":
    main()