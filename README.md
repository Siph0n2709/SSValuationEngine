# SSValuationEngine

A relative-value screen across 14 US-listed semiconductor names built entirely from SEC EDGAR filings, plus full DCF models on the two names the screen flagged as cheapest in their segment.

The screen and the DCFs disagree. They disagree in opposite directions on the two names, and working out why is most of what this project is about.

## What it found

QCOM screens cheapest among designers at 14.6x EV/EBITDA against a 55.3x segment median. The DCF values it at $102.86 against a $185.27 market price. The bull case, built from QCOM's own June 2026 Investor Day guidance, reaches $124.62 and still falls a third short.

ENTG screens cheapest among equipment names at 28.5x against a 53.0x median. The DCF values it at $48.04 against $136.26.

Two similar-looking results, but they don't survive the same test. Hold the exit multiple at whatever the company trades at today, so the terminal assumption stops doing any work, and QCOM still comes out at roughly $152 against a $185 price. The market needs the multiple to expand on a business consensus says grows 2% a year. Run the same test on ENTG and it gives about $149 against $136. The sign flips.

So on QCOM the screen was misleading and the DCF caught it. On ENTG the DCF was out of its depth and the screen's relative ranking was the better read. Terminal value is 63.6% of enterprise value for QCOM and 73.6% for ENTG, which is the number that tells you which situation you're in before you run the test.

Full reasoning is in [QCOM_DCF_Report.docx](QCOM_DCF_Report.docx) and [ENTG_DCF_Report.docx](ENTG_DCF_Report.docx).

## Why EDGAR and not a data vendor

Vendor-computed fields fail quietly. Testing against NVIDIA as a validation oracle, one commercial EBITDA field overstated the figure by $11.3 billion because it folded non-operating income into the calculation. Nothing about the number looked wrong.

Everything here is built from raw XBRL tags, so every figure traces to a line in a filing. Market capitalization and beta come from yfinance, since they're market data and can't come from a 10-K. NVDA is carried through every stage as a regression check: EBITDA lands within 0.03% of the independently verified figure, debt within 0.02%, cash within 0.05%.

That discipline is the point of the project, and it cost more time than the valuation work did.

## Extraction failures found

Six, all silent, each of which would have produced a plausible wrong number.

KLA stopped reporting the `OperatingIncomeLoss` tag after 2015, so operating income has to be reconstructed from income-statement components. The annual-period selector also had to key off actual period length (340 to 380 days) rather than the `fp` and `form` flags, which filers code inconsistently and which had sent the extractor to a 2014 filing.

Intangible amortization was dropped for AVGO, AMD, MRVL and ENTG because their combined D&A tag excludes it. Fixed by taking the greater of the combined tag and the summed components.

AMD splits its non-acquisition D&A across `Depreciation` and `OtherDepreciationAndAmortization`. Taking only the first understated D&A by $750M and inflated the multiple from 120x to 134x. MRVL had the same problem, 69x reported as 84x, and would never have been caught on its own because 84x didn't look wrong enough to investigate.

KLA's 10-for-1 split after its filing date broke any market cap computed as price times EDGAR share count, understating it by a factor of ten. Market cap now comes from a single live snapshot.

The screen originally nulled any multiple above an arbitrary 100x threshold. That quietly removed names from the segment median, moving the designer median from 46x to 55x depending on whether the filter fired, and made a considered exclusion indistinguishable from a broken extraction. Replaced with named exclusions carrying a stated reason, written to the CSV.

ENTG re-tagged interest expense partway through its series. The extractor resolves a candidate tag list once for the whole series rather than per year, so the first tag matched, the search stopped, and the reported window silently shifted back by a year.

Every one of these was caught by dumping the available tags and reconciling them against a subtotal on the face of the filing. None would have been caught by assuming the obvious tag name was the right one.

## Methodology

EBITDA is operating income plus D&A from the cash flow statement, including intangible amortization. Not a vendor's EBITDA field, and not revenue minus costs.

Debt is long-term debt plus finance leases. Operating leases are excluded, because EBITDA is struck after operating lease expense and adding the lease liability to enterprise value would count it twice.

Cash is cash and equivalents at the screen level, where consistency across 14 names matters more than precision on any one. At the DCF stage liquidity is refined by hand: QCOM's includes $4.63B of current marketable securities but excludes $2.32B of restricted cash escrowed for the Alphawave acquisition, and ENTG holds no marketable securities at all.

Tax rates are normalized rather than reported. QCOM's effective rates ran 1%, 2% and 56% across three years, the last driven by a one-time charge. ENTG's are struck after interest on a leveraged balance sheet, so the unlevered rate is derived by adding the interest shield back at 21%.

## Universe

Equipment: AMAT, LRCX, KLAC, TER, MKSI, ENTG. Designer and fabless: NVDA, AVGO, QCOM, AMD, TXN, MRVL, MPWR, LSCC.

Memory names like MU and WDC are excluded because a commodity pricing cycle drives their earnings rather than design or process differentiation, which makes trailing multiples incomparable. Foundries and foreign private issuers (TSM, ASML, UMC) are excluded because they file 20-Fs under IFRS and their tags don't map to the us-gaap taxonomy this pipeline reads.

LSCC stays in the screen but out of the segment median. Its stock compensation of $120M exceeds its EBITDA of $33M, so the 530x multiple measures a collapsed denominator rather than a valuation. TXN is the same failure inverted: it screens cheap at 35.6x, but fab-buildout depreciation is depressing its EBITDA rather than a genuine discount, so it was ruled out as a DCF candidate.

EV/EBITDA breaks at both tails. Depreciation intensity crushes the denominator at one end, stock compensation and operating leverage at the other.

## Running it

Requires Python 3 with `requests`, `pandas` and `yfinance`. The SEC requires a contact string in the User-Agent header or it blocks the request:

```bash
export SEC_USER_AGENT="Your Name your_email@example.com"   # PowerShell: $env:SEC_USER_AGENT="..."
python src/build_dataset.py    # pulls filings, writes screener_base.csv
python src/screen.py           # adds market data, ranks against segment medians
```

Filings cache to `data/raw/` on first run, so repeat runs don't re-hit the SEC.

DCF inputs:

```bash
python src/QCOM_DCF_Extract.py
python src/ENTG_DCF_Extract.py
```

Diagnostics live alongside these and take a ticker where relevant. `income_trace.py` prints which path the operating income and D&A logic actually took, `nwc_dump.py` and `cash_dump.py` list every working-capital and liquidity tag a filer reports, and `wacc_inputs.py` assembles the cost of capital. These exist because guessing at tags is how the six failures above happened.

## Limitations

A five-year forecast with a GDP-anchored perpetuity can't reproduce a 50x multiple at any defensible discount rate. Run this model on AMAT at 52.1x or LRCX at 65.0x and it returns the same verdict with a different ticker. The ENTG result is a statement about the method applied to a sector, not a finding about Entegris, and the report says so.

Both models anchor to the most recent 10-K balance sheet, which is nine months stale for QCOM. Beta is the single most load-bearing input in each: published estimates for QCOM range from 1.2 to 1.7, and the fair value moves from $103 to $117 across that range.

The screen is trailing twelve months throughout. A name whose EBITDA is at a cycle trough will screen expensive and one at a peak will screen cheap, which is exactly what LSCC and TXN demonstrate.
