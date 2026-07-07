"""
Three names flagged 'no debt tags': TER, MPWR, LSCC. Before I record them as debt-free, I
want to be sure there's no debt hiding under a tag my total_debt_for doesn't check
(convertibles, senior notes, secured debt, lines of credit). I dump every debt-ish tag they
report with its latest year-end value, straight from cache.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEBT_KEYWORDS = ["debt", "borrow", "notespayable", "seniornotes",
                 "convertible", "securednote", "lineofcredit", "linesofcredit",
                 "finlease", "financelease", "capitallease"]


def latest_instant(node, unit="USD"):
    if not node:
        return None, None
    rows = [r for r in node.get("units", {}).get(unit, [])
            if r.get("end") and "start" not in r]
    if not rows:
        return None, None
    r = max(rows, key=lambda x: (x["end"], x.get("filed", "")))
    return r["val"], r["end"]


for tkr in ["TER", "MPWR", "LSCC"]:
    doc = json.loads((ROOT / "data" / "raw" / f"{tkr}_companyfacts.json").read_text())
    gaap = doc.get("facts", {}).get("us-gaap", {})
    print(f"\n=== {tkr}: debt-related tags carrying a balance ===")
    found = False
    for t in sorted(gaap):
        if any(k in t.lower() for k in DEBT_KEYWORDS):
            v, e = latest_instant(gaap.get(t))
            if v is not None and abs(v) > 0:
                print(f"  {t:58} ${v/1e9:8.3f}B  {e}")
                found = True
    if not found:
        print("  (nothing with a balance -> genuinely debt-free)")