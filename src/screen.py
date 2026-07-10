"""
The screen itself: I bolt a live market cap onto my EDGAR financials to finish EV/EBITDA and
rank each name against its SEGMENT median (equipment vs designer).

Lesson learned the hard way: I must NOT compute market cap as price x shares-from-EDGAR. The
EDGAR share count is as-of the last 10-K, but the price is live -- and if a name split or
bought back stock in between (KLAC did a 10:1 split after its filing), the two don't match and
market cap comes out wildly wrong. So I take market cap DIRECTLY from yfinance, where price and
shares are one consistent live snapshot, and only use it split-safe. EV = live market cap +
EDGAR net debt. I fetch each name one at a time to dodge the batch 'database is locked' issue.

Reads data/processed/screener_base.csv and writes data/processed/screener.csv.
"""

import time

import pandas as pd
import yfinance as yf
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "data" / "processed" / "screener_base.csv"
OUT = ROOT / "data" / "processed" / "screener.csv"

NM_THRESHOLD = 100.0


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
    """Live price, market cap, and share count for one ticker -- all from the same snapshot."""
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
        time.sleep(0.2)  # sequential + polite -> avoids the batch db-lock

    df["price"] = df["ticker"].map(lambda t: md[t]["price"])
    df["market_cap"] = df["ticker"].map(lambda t: md[t]["market_cap"])
    df["yf_shares"] = df["ticker"].map(lambda t: md[t]["yf_shares"])

    evs, mults = [], []
    for _, r in df.iterrows():
        mc, nd, eb = r["market_cap"], r["net_debt"], r["ebitda"]
        if pd.isna(mc) or pd.isna(nd) or pd.isna(eb):
            evs.append(None); mults.append(None); continue
        ev = mc + nd
        m = ev / eb if eb and eb > 0 else None
        if m is not None and (m > NM_THRESHOLD or m < 0):
            m = None
        evs.append(ev); mults.append(m)
    df["ev"] = evs
    df["ev_ebitda"] = mults

    df["seg_median"] = df.groupby("segment")["ev_ebitda"].transform("median")
    df["vs_median"] = df["ev_ebitda"] / df["seg_median"]

    for seg in ["equipment", "designer"]:
        block = df[df["segment"] == seg].sort_values("ev_ebitda", na_position="last")
        med = block["ev_ebitda"].median()
        med_str = "n/a" if pd.isna(med) else f"{med:.1f}x"
        print(f"\n{seg.upper()}  (segment median EV/EBITDA: {med_str})")
        print(f"  {'ticker':6} {'price':>9} {'mktcap$B':>9} {'EV$B':>9} {'EV/EBITDA':>10} {'vs med':>8}")
        for _, r in block.iterrows():
            mult = "n.m." if pd.isna(r["ev_ebitda"]) else f"{r['ev_ebitda']:.1f}x"
            vsm = "" if pd.isna(r["vs_median"]) else f"{r['vs_median']:.2f}"
            mc = "--" if pd.isna(r["market_cap"]) else f"{r['market_cap']/1e9:.0f}"
            ev = "--" if pd.isna(r["ev"]) else f"{r['ev']/1e9:.0f}"
            px = "--" if pd.isna(r["price"]) else f"{r['price']:.2f}"
            print(f"  {r['ticker']:6} {px:>9} {mc:>9} {ev:>9} {mult:>10} {vsm:>8}")

    # Split / buyback watch: where my EDGAR share count and the live count diverge a lot,
    # that's a split or big buyback since the last 10-K -- exactly what broke KLAC.
    print("\nEDGAR vs live shares (ratio far from 1.0 = split/buyback since last 10-K):")
    for _, r in df.iterrows():
        if pd.notna(r["yf_shares"]) and pd.notna(r["shares"]) and r["shares"]:
            ratio = r["yf_shares"] / r["shares"]
            flag = "  <-- SPLIT/DRIFT" if (ratio > 1.2 or ratio < 0.83) else ""
            print(f"  {r['ticker']:6} edgar={r['shares']/1e9:.3f}B  live={r['yf_shares']/1e9:.3f}B  ratio={ratio:.2f}{flag}")

    nv = df[df["ticker"] == "NVDA"].iloc[0]
    if pd.notna(nv["ev_ebitda"]):
        print(f"\nNVDA gut-check: EV/EBITDA {nv['ev_ebitda']:.1f}x, market cap "
              f"${nv['market_cap']/1e9:.0f}B (validation: ~36x, ~$4.8T)")

    df.to_csv(OUT, index=False)
    print(f"\nSaved -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    build_screen()