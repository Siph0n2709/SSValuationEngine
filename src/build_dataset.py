"""
Build the EBITDA layer across my full 14-name universe, straight from EDGAR.

This is the spike scaled up: operating income + full D&A, run over every in-scope
ticker. It doubles as a quality gate -- stale filings or missing D&A get flagged with
their actual available tags, and a D&A cross-check shows where a filer's combined line
disagrees with depreciation+intangible-amortization, so no understatement slips through.

Balance-sheet items (debt, cash, shares) and price/market cap are later passes.
NVDA stays my ground truth throughout.
"""

import sys
from pathlib import Path

import pandas as pd

# universe.py lives in src/ (I moved it out of data/raw, which is gitignored). I make
# sure src/, data/, and root are all importable no matter where I launch from.
_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "src", _ROOT / "data", _ROOT):
    sys.path.insert(0, str(_p))

import edgar
from universe import UNIVERSE, all_tickers, segment_of

NVDA_EBITDA_TARGET = 133.23e9
OUT = edgar.ROOT / "data" / "processed"
OUT.mkdir(parents=True, exist_ok=True)


def _name_of(ticker):
    for members in UNIVERSE.values():
        if ticker in members:
            return members[ticker]
    return ticker


def _b(x):
    return "--" if x is None else f"{x/1e9:.2f}"


def build():
    rows = []
    flagged = []      # (ticker, reason, facts) for names I couldn't build cleanly
    crosscheck = []   # (ticker, combined, summed, used) to expose D&A disagreements

    for ticker in all_tickers():
        cik, doc = edgar.company_facts(ticker)
        if not doc:
            flagged.append((ticker, "couldn't resolve CIK / fetch facts", None))
            continue

        facts = doc.get("facts", {})
        result = edgar.ebitda_for(facts)
        base = {
            "ticker": ticker, "name": _name_of(ticker), "segment": segment_of(ticker),
            "period_end": result.get("period_end"),
            "operating_income": result.get("operating_income"),
        }

        if not result["ok"]:
            flagged.append((ticker, result["reason"], facts))
            rows.append({**base, "da": None, "da_tag": None,
                         "ebitda": None, "status": result["reason"]})
            continue

        info = result["da_info"]
        crosscheck.append((ticker, info["combined"], info["summed"], info["method"]))
        # If I had to use the summed figure because the combined line came in materially
        # lower, that's the intangible-amort fix firing -- I note it so it's not invisible.
        status = "ok"
        if (info["method"] == "summed" and info["combined"] is not None
                and info["summed"] > info["combined"] * 1.05):
            status = "ok (summed > combined)"

        rows.append({**base, "da": result["da"], "da_tag": result["da_tag"],
                     "ebitda": result["ebitda"], "status": status})

    df = pd.DataFrame(rows)

    show = df.copy()
    for col in ("operating_income", "da", "ebitda"):
        show[col] = (show[col] / 1e9).round(2)
    show = show.rename(columns={
        "operating_income": "EBIT($B)", "da": "D&A($B)", "ebitda": "EBITDA($B)"})
    print(show[["ticker", "segment", "period_end",
                "EBIT($B)", "D&A($B)", "EBITDA($B)", "status"]].to_string(index=False))
    print()

    # Oracle check.
    nvda = df[df["ticker"] == "NVDA"]
    if not nvda.empty and pd.notna(nvda.iloc[0]["ebitda"]):
        got = float(nvda.iloc[0]["ebitda"])
        off = abs(got - NVDA_EBITDA_TARGET) / NVDA_EBITDA_TARGET * 100
        verdict = "matches spike" if off <= 1.0 else "DRIFTED -- investigate"
        print(f"Oracle check -- NVDA EBITDA ${got/1e9:.2f}B vs spike "
              f"${NVDA_EBITDA_TARGET/1e9:.2f}B (off {off:.2f}%) -> {verdict}")

    # D&A cross-check: where combined and summed disagree, I want to see it by eye.
    print("\nD&A cross-check ($B)  [combined line vs depreciation+intangible-amort]")
    for tkr, comb, summ, used in crosscheck:
        gap = ""
        if comb is not None and summ is not None and comb > 0:
            diff = abs(summ - comb) / comb * 100
            if diff > 5:
                gap = f"  <-- differ {diff:.0f}%"
        print(f"  {tkr:6} combined={_b(comb):>7}  summed={_b(summ):>7}  used={used}{gap}")

    # Diagnostics for anything I couldn't build: dump the filer's real tags.
    if flagged:
        print(f"\n{'='*70}\nFLAGGED -- {len(flagged)} name(s) need a look\n{'='*70}")
        for ticker, reason, facts in flagged:
            print(f"\n{ticker}: {reason}")
            if facts is None:
                continue
            print("  D&A / amortization tags:")
            for t in edgar.available_tags(facts, "depreciation", "amortization"):
                print(f"      {t}")
            if "OperatingIncome" in reason or "stale" in reason:
                print("  operating-income tags:")
                for t in edgar.available_tags(facts, "operatingincome"):
                    print(f"      {t}")
    else:
        print("\nAll 14 names built cleanly.")

    out_path = OUT / "ebitda.csv"
    df.to_csv(out_path, index=False)
    print(f"\nSaved -> {out_path.relative_to(edgar.ROOT)}")


if __name__ == "__main__":
    build()