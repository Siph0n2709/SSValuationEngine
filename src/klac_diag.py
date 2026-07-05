"""
One-off: figure out why KLAC keeps resolving to a 2014 filing. I read the cached
companyfacts straight off disk (no network) and dump every OperatingIncomeLoss row so I
can SEE whether recent years are simply missing, or the entity/tag itself is wrong.
"""

import json
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
raw = ROOT / "data" / "raw" / "KLAC_companyfacts.json"
if not raw.exists():
    raise SystemExit(f"No cached file at {raw} -- run build_dataset once to populate it.")

doc = json.loads(raw.read_text())

# First thing I want: did I even pull the right company?
print(f"entityName: {doc.get('entityName')}")
print(f"cik:        {doc.get('cik')}")

rows = (doc.get("facts", {}).get("us-gaap", {})
        .get("OperatingIncomeLoss", {}).get("units", {}).get("USD", []))
print(f"\nOperatingIncomeLoss: {len(rows)} USD rows total")


def duration_days(r):
    if r.get("start") and r.get("end"):
        return (datetime.date.fromisoformat(r["end"])
                - datetime.date.fromisoformat(r["start"])).days
    return None


# Show the newest dozen so I can eyeball the end dates, durations, fp and form codes.
print("\nnewest rows by end date  (start -> end | days | $B | fp | form):")
for r in sorted(rows, key=lambda r: r.get("end", ""))[-12:]:
    d = duration_days(r)
    print(f"  {r.get('start')} -> {r.get('end')} | {str(d):>4}d | "
          f"${(r.get('val') or 0)/1e9:7.2f}B | fp={r.get('fp')} | form={r.get('form')}")