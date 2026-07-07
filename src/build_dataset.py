"""
Build the screener dataset across my full 14-name universe, straight from EDGAR.

Two layers, both validated against NVDA as my oracle:
  - EBITDA  = operating income + full D&A (intangible amort included)
  - EV base = total debt (excl. operating leases) - cash & equivalents, plus shares
The only input still missing after this is a live price for market cap.
"""

import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "src", _ROOT / "data", _ROOT):
    sys.path.insert(0, str(_p))

import edgar
from universe import UNIVERSE, all_tickers, segment_of

# NVDA oracle -- EBITDA from the spike; debt/cash/shares from the balance-sheet spike.
NVDA_EBITDA_TARGET = 133.23e9
NVDA_DEBT_TARGET = 8.47e9      # LT debt incl current, operating leases excluded
NVDA_CASH_TARGET = 10.61e9     # cash & equivalents only
NVDA_SHARES_TARGET = 24.30e9

OUT = edgar.ROOT / "data" / "processed"
OUT.mkdir(parents=True, exist_ok=True)


def _name_of(ticker):
    for members in UNIVERSE.values():
        if ticker in members:
            return members[ticker]
    return ticker


def _b(x):
    return "--" if x is None else f"{x/1e9:.2f}"


def _pct_off(got, target):
    return "--" if got is None else f"{abs(got-target)/target*100:.2f}%"


def build():
    rows = []
    flagged = []

    for ticker in all_tickers():
        cik, doc = edgar.company_facts(ticker)
        if not doc:
            flagged.append((ticker, "couldn't resolve CIK / fetch facts"))
            continue

        facts = doc.get("facts", {})
        ebt = edgar.ebitda_for(facts)
        ev = edgar.ev_inputs(facts)

        row = {"ticker": ticker, "name": _name_of(ticker), "segment": segment_of(ticker)}

        if ebt["ok"]:
            row.update({
                "period_end": ebt["period_end"],
                "operating_income": ebt["operating_income"],
                "da": ebt["da"], "ebitda": ebt["ebitda"],
            })
        else:
            flagged.append((ticker, f"EBITDA: {ebt['reason']}"))
            row.update({"period_end": ebt.get("period_end"),
                        "operating_income": ebt.get("operating_income"),
                        "da": None, "ebitda": None})

        if ev["ok"]:
            row.update({
                "bs_date": ev["bs_date"], "total_debt": ev["total_debt"],
                "cash": ev["cash"], "net_debt": ev["net_debt"], "shares": ev["shares"],
            })
            if ev["cash"] is None:
                flagged.append((ticker, "no cash tag at year-end"))
            if ev["shares"] is None:
                flagged.append((ticker, "no shares tag"))
        else:
            flagged.append((ticker, f"EV inputs: {ev['reason']}"))
            row.update({"bs_date": None, "total_debt": None, "cash": None,
                        "net_debt": None, "shares": None})

        rows.append(row)

    df = pd.DataFrame(rows)

    # --- EBITDA table ---
    e = df.copy()
    for c in ("operating_income", "da", "ebitda"):
        e[c] = (e[c] / 1e9).round(2)
    e = e.rename(columns={"operating_income": "EBIT($B)", "da": "D&A($B)", "ebitda": "EBITDA($B)"})
    print("EBITDA layer")
    print(e[["ticker", "segment", "period_end", "EBIT($B)", "D&A($B)", "EBITDA($B)"]].to_string(index=False))

    # --- EV-inputs table ---
    v = df.copy()
    for c in ("total_debt", "cash", "net_debt"):
        v[c] = (v[c] / 1e9).round(2)
    v["shares"] = (v["shares"] / 1e9).round(3)
    v = v.rename(columns={"total_debt": "Debt($B)", "cash": "Cash($B)",
                          "net_debt": "NetDebt($B)", "shares": "Shares(B)"})
    print("\nEV inputs (market cap still to come)")
    print(v[["ticker", "bs_date", "Debt($B)", "Cash($B)", "NetDebt($B)", "Shares(B)"]].to_string(index=False))

    # --- oracle checks ---
    n = df[df["ticker"] == "NVDA"]
    if not n.empty:
        r = n.iloc[0]
        print("\nNVDA oracle checks:")
        print(f"  EBITDA ${_b(r['ebitda'])}B  (target {NVDA_EBITDA_TARGET/1e9:.2f}, off {_pct_off(r['ebitda'], NVDA_EBITDA_TARGET)})")
        print(f"  debt   ${_b(r['total_debt'])}B  (target {NVDA_DEBT_TARGET/1e9:.2f}, off {_pct_off(r['total_debt'], NVDA_DEBT_TARGET)})")
        print(f"  cash   ${_b(r['cash'])}B  (target {NVDA_CASH_TARGET/1e9:.2f}, off {_pct_off(r['cash'], NVDA_CASH_TARGET)})")
        print(f"  shares {r['shares']/1e9:.3f}B  (target {NVDA_SHARES_TARGET/1e9:.2f}, off {_pct_off(r['shares'], NVDA_SHARES_TARGET)})")

    debt_free = df[df["total_debt"] == 0]["ticker"].tolist()
    if debt_free:
        print(f"\nDebt-free (net debt = -cash): {', '.join(debt_free)}")

    if flagged:
        print("\nFLAGGED:")
        for tkr, why in flagged:
            print(f"  {tkr:6} {why}")
    else:
        print("\nAll 14 names built cleanly across both layers.")

    out_path = OUT / "screener_base.csv"
    df.to_csv(out_path, index=False)
    print(f"\nSaved -> {out_path.relative_to(edgar.ROOT)}")


if __name__ == "__main__":
    build()