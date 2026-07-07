"""
The screen itself: I bolt a live price onto my EDGAR financials to finish EV/EBITDA and rank
each name against its SEGMENT median (equipment vs designer), since a fair comp set is
segment-specific.

Price is the one input that isn't a filed figure -- it's a live market quote -- so its source
doesn't need the reproducibility of EDGAR. I use yfinance: one batched call for all 14, and it
rides over the endpoint fragility that broke my first (Stooq) attempt. The analytical content
still comes entirely from EDGAR; this just stamps on today's price.

Reads data/processed/screener_base.csv (my financials) and writes data/processed/screener.csv.
"""

import pandas as pd
import yfinance as yf
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "data" / "processed" / "screener_base.csv"
OUT = ROOT / "data" / "processed" / "screener.csv"

NM_THRESHOLD = 100.0  # above this EV/EBITDA is "not meaningful" (trough-year names like LSCC)


def fetch_prices(tickers):
    """Latest close for each ticker in one batched yfinance call. I pull a few days and take
    the last available close so a single missing session doesn't leave a hole."""
    data = yf.download(tickers, period="5d", progress=False, auto_adjust=False)
    close = data["Close"] if "Close" in data.columns.get_level_values(0) else data
    close = close.ffill()
    last = close.iloc[-1]
    out = {}
    for t in tickers:
        try:
            v = last[t]
            out[t] = float(v) if pd.notna(v) else None
        except Exception:
            out[t] = None
    return out


def build_screen():
    df = pd.read_csv(BASE)
    prices = fetch_prices(list(df["ticker"]))

    mcaps, evs, multiples = [], [], []
    for _, r in df.iterrows():
        px = prices.get(r["ticker"])
        if px is None or pd.isna(r["shares"]) or pd.isna(r["ebitda"]):
            mcaps.append(None); evs.append(None); multiples.append(None)
            continue
        mcap = px * r["shares"]
        ev = mcap + r["net_debt"]                  # net_debt = debt - cash (already signed)
        mult = ev / r["ebitda"] if r["ebitda"] and r["ebitda"] > 0 else None
        if mult is not None and (mult > NM_THRESHOLD or mult < 0):
            mult = None                             # not meaningful (trough EBITDA / negative EV)
        mcaps.append(mcap); evs.append(ev); multiples.append(mult)

    df["price"] = df["ticker"].map(prices)
    df["market_cap"] = mcaps
    df["ev"] = evs
    df["ev_ebitda"] = multiples

    df["seg_median"] = df.groupby("segment")["ev_ebitda"].transform("median")
    df["vs_median"] = df["ev_ebitda"] / df["seg_median"]  # <1 = cheaper than peers

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

    nv = df[df["ticker"] == "NVDA"].iloc[0]
    if pd.notna(nv["ev_ebitda"]):
        print(f"\nNVDA gut-check: EV/EBITDA {nv['ev_ebitda']:.1f}x, market cap "
              f"${nv['market_cap']/1e9:.0f}B (earlier validation put this ~36x, ~$4.8T)")

    df.to_csv(OUT, index=False)
    print(f"\nSaved -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    build_screen()