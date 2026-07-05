"""
KLA stopped tagging OperatingIncomeLoss after ~2015, so I need to rebuild operating income
from components. This reads the cached file and prints the latest annual value of each
income-statement building block, then shows a few ways to reconstruct operating income --
so I can see which one lands on KLA's real ~$4B before I wire it into edgar.py.
"""

import json
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
doc = json.loads((ROOT / "data" / "raw" / "KLAC_companyfacts.json").read_text())
gaap = doc.get("facts", {}).get("us-gaap", {})


def is_annual(r):
    s, e = r.get("start"), r.get("end")
    if not s or not e:
        return False
    d = (datetime.date.fromisoformat(e) - datetime.date.fromisoformat(s)).days
    return 340 <= d <= 380


def latest_annual(tag):
    node = gaap.get(tag)
    if not node:
        return None
    rows = [r for r in node.get("units", {}).get("USD", []) if is_annual(r)]
    if not rows:
        return None
    return max(rows, key=lambda r: (r["end"], r.get("filed", "")))


candidates = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "CostOfRevenue",
    "CostOfGoodsAndServicesSold",
    "GrossProfit",
    "OperatingExpenses",
    "ResearchAndDevelopmentExpense",
    "SellingGeneralAndAdministrativeExpense",
    "CostsAndExpenses",
    "OperatingIncomeLoss",
]

print("latest annual value per candidate tag (end | $B):")
vals = {}
for t in candidates:
    r = latest_annual(t)
    if r:
        vals[t] = r["val"]
        print(f"  {t:60} {r['end']}  ${r['val']/1e9:8.2f}B")
    else:
        print(f"  {t:60} --")


def g(t):
    return vals.get(t)


print("\nreconstructed operating income candidates (target ~ $4B):")
if g("GrossProfit") is not None and g("OperatingExpenses") is not None:
    v = g("GrossProfit") - g("OperatingExpenses")
    print(f"  GrossProfit - OperatingExpenses            = ${v/1e9:.2f}B")
rev = g("Revenues") or g("RevenueFromContractWithCustomerExcludingAssessedTax")
if rev is not None and g("CostsAndExpenses") is not None:
    v = rev - g("CostsAndExpenses")
    print(f"  Revenue - CostsAndExpenses                 = ${v/1e9:.2f}B")
cor = g("CostOfRevenue") or g("CostOfGoodsAndServicesSold")
if rev is not None and cor is not None and g("OperatingExpenses") is not None:
    v = rev - cor - g("OperatingExpenses")
    print(f"  Revenue - CostOfRevenue - OperatingExpenses = ${v/1e9:.2f}B")