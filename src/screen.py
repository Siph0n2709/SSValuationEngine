"""
The screen itself: I bolt a live market cap onto my EDGAR financials to finish EV/EBITDA and
rank each name against its SEGMENT median (equipment vs designer).

Lesson learned the hard way: I must NOT compute market cap as price x shares from EDGAR. The
EDGAR share count is as of the last 10-K, but the price is live, and if a name split or bought
back stock in between (KLAC did a 10:1 split after its filing), the two don't match and market
cap comes out wildly wrong. So I take market cap DIRECTLY from yfinance, where price and shares
are one consistent live snapshot, and only use it split safe. EV = live market cap + EDGAR net
debt. I fetch each name one at a time to dodge the batch 'database is locked' issue.

On exclusions. I used to null out any multiple above 100x as "not meaningful." That was a bad
rule. The number was arbitrary, it silently pulled names out of the segment median (the
designer median moved from 46x to 67x depending on whether the filter fired), and the CSV came
out blank so I couldn't tell a considered exclusion from a broken extraction. That ambiguity
cost me real time when I went hunting for a bug in LSCC that didn't exist.

Now I always keep the raw multiple, however ugly, and I exclude names from the median by NAME
with a REASON. If I can't write down why a name doesn't belong in the median, it belongs in it.

Reads data/processed/screener_base.csv and writes data/processed/screener.csv.
"""

import time

import pandas as pd
import yfinance as yf
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "data" / "processed" / "screener_base.csv"
OUT = ROOT / "data" / "processed" / "screener.csv"


# Names I hold out of the segment median, each with the analytical reason. These are judgment
# calls I can defend, not a numeric cutoff.
#
# The general point is that EV/EBITDA breaks down at both tails. It fails when EBITDA is
# structurally depressed, whether that's by depreciation intensity at one end or by stock comp
# and operating leverage at the other. A name whose denominator has collapsed tells me nothing
# about valuation, so it shouldn't be setting the benchmark for everyone else.
MEDIAN_EXCLUSIONS = {
    "LSCC": "stock comp of $120M exceeds EBITDA of $33M. Trough revenue against fixed opex "
            "leaves GAAP EBITDA near zero, so the multiple is measuring the denominator "
            "collapsing, not valuation",
}

# Note to self: AMD prints around 134x and I am deliberately leaving it IN the median until I
# verify its D&A. If the Xilinx intangible amortization is fully captured then 134x is real and
# it belongs. If the amortization is being dropped, EBITDA is understated and that's a bug in
# my extraction, not a reason to exclude the name. Run income_trace.py AMD to settle it.


def _fi_get(fi, *keys):
    """fast_info key names vary by yfinance version, so I try a few spellings."""
    for k in keys:
        try:
            v = fi[k]
            if v is not None:
                return v
        except (KeyError, TypeError):
            pass
        try:
            v = getattr(fi, k)
            if v is not None:
                return v
        except AttributeError:
            pass
    return None


def market_data(ticker, retries=2):
    """Live price, market cap, and share count for one ticker, all from the same snapshot."""
    for attempt in range(retries + 1):
        try:
            fi = yf.Ticker(ticker).fast_info
            return {
                "price": _fi_get(fi, "lastPrice", "last_price"),
                "market_cap": _fi_get(fi, "marketCap", "market_cap"),
                "yf_shares": _fi_get(fi, "shares", "shares_outstanding"),
            }
        except Exception as e:
            if attempt == retries:
                print(f"  {ticker}: fetch failed ({e})")
                return {"price": None, "market_cap": None, "yf_shares": None}
            time.sleep(0.5)


def build_screen():
    df = pd.read_csv(BASE)

    md = {}
    for t in df["ticker"]:
        md[t] = market_data(t)
        time.sleep(0.2)  # sequential and polite, avoids the batch db lock

    df["price"] = df["ticker"].map(lambda t: md[t]["price"])
    df["market_cap"] = df["ticker"].map(lambda t: md[t]["market_cap"])
    df["yf_shares"] = df["ticker"].map(lambda t: md[t]["yf_shares"])

    evs, mults, eligible, reasons = [], [], [], []
    for _, r in df.iterrows():
        mc, nd, eb = r["market_cap"], r["net_debt"], r["ebitda"]

        if pd.isna(mc) or pd.isna(nd) or pd.isna(eb):
            evs.append(None)
            mults.append(None)
            eligible.append(False)
            reasons.append("missing market cap, net debt, or EBITDA")
            continue

        ev = mc + nd
        evs.append(ev)

        # EV/EBITDA is undefined on non positive EBITDA. This is the one hard rule and it's
        # arithmetic, not judgment.
        if eb <= 0:
            mults.append(None)
            eligible.append(False)
            reasons.append("EBITDA is zero or negative, multiple is undefined")
            continue

        mults.append(ev / eb)  # I always keep the number, however ugly it looks

        why = MEDIAN_EXCLUSIONS.get(r["ticker"])
        eligible.append(why is None)
        reasons.append(why or "")

    df["ev"] = evs
    df["ev_ebitda"] = mults
    df["median_eligible"] = eligible
    df["nm_reason"] = reasons

    # The median only sees eligible names. I mask the rest out first so a name I've excluded
    # can't drag the benchmark it's being measured against.
    med_input = df["ev_ebitda"].where(df["median_eligible"])
    df["seg_median"] = med_input.groupby(df["segment"]).transform("median")
    df["vs_median"] = df["ev_ebitda"] / df["seg_median"]

    for seg in ["equipment", "designer"]:
        block = df[df["segment"] == seg].sort_values("ev_ebitda", na_position="last")
        med = block.loc[block["median_eligible"], "ev_ebitda"].median()
        med_str = "n/a" if pd.isna(med) else f"{med:.1f}x"
        n_elig = int(block["median_eligible"].sum())

        print(f"\n{seg.upper()}  (median EV/EBITDA: {med_str}, on {n_elig} of {len(block)} names)")
        print(f"  {'ticker':6} {'price':>9} {'mktcap$B':>9} {'EV$B':>9} {'EV/EBITDA':>10} {'vs med':>8}")
        for _, r in block.iterrows():
            mult = "--" if pd.isna(r["ev_ebitda"]) else f"{r['ev_ebitda']:.1f}x"
            if pd.notna(r["ev_ebitda"]) and not r["median_eligible"]:
                mult += "*"
            vsm = "" if pd.isna(r["vs_median"]) else f"{r['vs_median']:.2f}"
            mc = "--" if pd.isna(r["market_cap"]) else f"{r['market_cap']/1e9:.0f}"
            ev = "--" if pd.isna(r["ev"]) else f"{r['ev']/1e9:.0f}"
            px = "--" if pd.isna(r["price"]) else f"{r['price']:.2f}"
            print(f"  {r['ticker']:6} {px:>9} {mc:>9} {ev:>9} {mult:>10} {vsm:>8}")

        # Anything I held out of the median has to say why, right here where I can see it.
        held_out = block[~block["median_eligible"]]
        for _, r in held_out.iterrows():
            print(f"  * {r['ticker']} held out of median: {r['nm_reason']}")

    # Split and buyback watch: where my EDGAR share count and the live count diverge a lot,
    # that's a split or a big buyback since the last 10-K, exactly what broke KLAC.
    print("\nEDGAR vs live shares (ratio far from 1.0 = split/buyback since last 10-K):")
    for _, r in df.iterrows():
        if pd.notna(r["yf_shares"]) and pd.notna(r["shares"]) and r["shares"]:
            ratio = r["yf_shares"] / r["shares"]
            flag = "  <-- SPLIT/DRIFT" if (ratio > 1.2 or ratio < 0.83) else ""
            print(f"  {r['ticker']:6} edgar={r['shares']/1e9:.3f}B  "
                  f"live={r['yf_shares']/1e9:.3f}B  ratio={ratio:.2f}{flag}")

    nv = df[df["ticker"] == "NVDA"].iloc[0]
    if pd.notna(nv["ev_ebitda"]):
        print(f"\nNVDA gut check: EV/EBITDA {nv['ev_ebitda']:.1f}x, market cap "
              f"${nv['market_cap']/1e9:.0f}B (validation: ~36x, ~$4.8T)")

    df.to_csv(OUT, index=False)
    print(f"\nSaved -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    build_screen()