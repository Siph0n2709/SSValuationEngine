"""
Balance-sheet spike (v2) -- pinned to the 10-K fiscal-year-end so it lines up with my annual
EBITDA, instead of grabbing the latest 10-Q quarter. I reconcile against my FMP oracle
(total debt $11.41B, cash & equivalents $10.61B) and, more importantly, work out the EV
input definitions I actually want to use across all 14 names.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
doc = json.loads((ROOT / "data" / "raw" / "NVDA_companyfacts.json").read_text())
GAAP = doc.get("facts", {}).get("us-gaap", {})
DEI = doc.get("facts", {}).get("dei", {})

ORACLE_DEBT = 11.41e9
ORACLE_CASH = 10.61e9


def instant_10k(node, unit="USD"):
    """Latest year-END instant value: I keep only facts reported on a 10-K (the annual
    balance sheet) so I get the fiscal-year-end, not a 10-Q quarter. Instant facts carry
    'end' but no 'start', which is how I tell them from duration flows."""
    if not node:
        return None, None
    rows = [r for r in node.get("units", {}).get(unit, [])
            if r.get("end") and "start" not in r
            and str(r.get("form", "")).startswith("10-K")]
    if not rows:
        return None, None
    r = max(rows, key=lambda x: (x["end"], x.get("filed", "")))
    return r["val"], r["end"]


def show(tag, unit="USD", width=44):
    v, end = instant_10k(GAAP.get(tag), unit)
    print(f"  {tag:{width}} {'--' if v is None else f'${v/1e9:8.2f}B'}  {end or ''}")
    return v


print("DEBT (year-end 10-K):")
lt_total = show("LongTermDebt")
op_lease_nc = show("OperatingLeaseLiabilityNoncurrent")
op_lease_c = show("OperatingLeaseLiabilityCurrent")
fin_lease_nc = show("FinanceLeaseLiabilityNoncurrent")
fin_lease_c = show("FinanceLeaseLiabilityCurrent")

print("\nCASH / LIQUIDITY (year-end 10-K):")
cash = show("CashAndCashEquivalentsAtCarryingValue")
st_inv = show("ShortTermInvestments")
mkt_sec = show("MarketableSecuritiesCurrent")

sh_val, sh_end = instant_10k(DEI.get("EntityCommonStockSharesOutstanding"), "shares")
print(f"\nSHARES outstanding: {'--' if sh_val is None else f'{sh_val/1e9:.3f}B'}  {sh_end or ''}")

fin_leases = (fin_lease_nc or 0) + (fin_lease_c or 0)
op_leases = (op_lease_nc or 0) + (op_lease_c or 0)
liquid = (st_inv or 0) + (mkt_sec or 0)

print("\n--- reconciliation ---")
print(f"  FMP-style debt (LT + operating leases) : ${((lt_total or 0)+op_leases)/1e9:7.2f}B   "
      f"oracle ${ORACLE_DEBT/1e9:.2f}B  <- confirms FMP bundled op-leases")
print(f"  cash & equivalents only                : ${(cash or 0)/1e9:7.2f}B   "
      f"oracle ${ORACLE_CASH/1e9:.2f}B  <- should match if date/tag are right")

print("\n--- my proposed EV inputs ---")
clean_debt = (lt_total or 0) + fin_leases
print(f"  total debt  (LT + finance leases, NO op-leases) : ${clean_debt/1e9:7.2f}B")
print(f"  cash  (cash & equiv only)                       : ${(cash or 0)/1e9:7.2f}B")
print(f"  cash  (cash & equiv + short-term liquid)        : ${((cash or 0)+liquid)/1e9:7.2f}B")
print(f"  net debt  (cash-only basis)   = debt - cash     : ${(clean_debt-(cash or 0))/1e9:7.2f}B")
print(f"  net debt  (liquid basis)      = debt - liquid   : ${(clean_debt-(cash or 0)-liquid)/1e9:7.2f}B")