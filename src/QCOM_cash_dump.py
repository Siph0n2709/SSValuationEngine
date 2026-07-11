"""
Dump QCOM's cash and investment tags so I can pick the right liquidity number for the
DCF equity bridge.

For the screen I used cash and equivalents only, on purpose: marketable securities tags
are inconsistent across filers and even across years for one filer, and cash and
equivalents is the one line that's universal and ties to my NVDA oracle. That was the
right call for ranking 14 names on a consistent basis.

The DCF bridge is different. Here I'm valuing one company and precision matters, so I
want the full liquid balance, not just the narrowest definition. This prints everything
cash or investment shaped at each year end and I choose from the output.

Run:  python src/cash_dump.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import edgar

TICKER = "QCOM"
YEARS_BACK = 2

NEEDLES = [
    "cash", "marketable", "securities", "investment", "shortterm", "longterm",
]

# Tags I don't care about. Cash flow statement lines and equity method holdings clutter
# the output and none of them belong in an equity bridge.
SKIP = [
    "CashCashEquivalentsRestricted", "ProceedsFrom", "PaymentsTo", "PaymentsFor",
    "EquityMethod", "IncreaseDecrease", "Realized", "Unrealized", "Impairment",
]


def fiscal_year_ends(facts, n=YEARS_BACK):
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

    header = f"{'tag':<62}" + "".join(f"{e:>14}" for e in ends)
    print(header)
    print("." * len(header))

    for tag in tags:
        if any(s.lower() in tag.lower() for s in SKIP):
            continue
        vals = [edgar.instant_value_for_end(facts, tag, e) for e in ends]
        if all(v is None for v in vals):
            continue
        cells = "".join(f"{edgar._bil(v):>14}" for v in vals)
        print(f"{tag:<62}{cells}")

    print("\nvalues in $B. I want cash and equivalents plus whatever liquid marketable")
    print("securities they hold, current and noncurrent. I leave out restricted cash and")
    print("strategic equity stakes in private companies since those aren't spendable.")


if __name__ == "__main__":
    main()