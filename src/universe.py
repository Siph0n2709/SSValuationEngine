"""
The semiconductor universe for the relative-value screen.

Scoped deliberately to US-listed companies that file 10-Ks under US GAAP, so
financials are comparable out of the box and the FMP pipeline ingests them
cleanly. Two segments are in scope: equipment and designer (fabless/IDM).

Memory and foundry are OUT, and the reason is a finding worth stating in the
writeup, not a limitation to hide:
  - Memory's largest players (Samsung, SK Hynix, Kioxia) file only with their
    home regulators in Korea/Japan -- not on EDGAR -- so a memory comp set
    built from US filings understates the segment badly.
  - Foundry collapses to Intel once foreign private issuers are removed:
    TSMC, UMC, and GlobalFoundries all file 20-Fs under IFRS, and SMIC/Samsung
    Foundry aren't cleanly available. A single name isn't a comp set.

Segmentation follows the empirical groupings from the AlgoGators capstone
(equipment / memory / designer / foundry), so the splits are defensible rather
than pulled off a screener.
"""

UNIVERSE = {
    "equipment": {
        "AMAT": "Applied Materials",
        "LRCX": "Lam Research",
        "KLAC": "KLA Corporation",
        "TER":  "Teradyne",
        "MKSI": "MKS Instruments",
        "ENTG": "Entegris",
    },
    "designer": {
        "NVDA": "NVIDIA",
        "AVGO": "Broadcom",
        "QCOM": "Qualcomm",
        "AMD":  "Advanced Micro Devices",
        "TXN":  "Texas Instruments",
        "MRVL": "Marvell Technology",
        "MPWR": "Monolithic Power Systems",
        "LSCC": "Lattice Semiconductor",
    },
}

# Foreign private issuers (20-F / IFRS). Not in the automated core, but candidates
# for a second-tier pass later if you want to extend coverage with caveats.
FOREIGN_FILERS = {
    "equipment": {"ASML": "ASML Holding"},
    "foundry":   {"TSM": "TSMC", "UMC": "United Microelectronics", "GFS": "GlobalFoundries"},
}


def all_tickers():
    """Flat list of every in-scope ticker across segments."""
    return [t for seg in UNIVERSE.values() for t in seg]


def segment_of(ticker):
    """Return which segment a ticker belongs to, or None."""
    for seg, members in UNIVERSE.items():
        if ticker in members:
            return seg
    return None


if __name__ == "__main__":
    for seg, members in UNIVERSE.items():
        print(f"\n{seg.upper()} ({len(members)})")
        for tkr, name in members.items():
            print(f"  {tkr:6} {name}")
    print(f"\nTotal in-scope: {len(all_tickers())} names")