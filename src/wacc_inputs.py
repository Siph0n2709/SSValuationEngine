"""
Pull the WACC inputs for my QCOM DCF.

Two sources, same split as the rest of the project. Market data (beta, market cap) comes
from yfinance. Everything from the filings (interest expense, debt) comes from EDGAR
through my own client, so the balance sheet side stays primary source.

Cost of debt here is the EFFECTIVE rate: interest expense over average total debt across
the year. Strictly the theory wants the marginal rate, meaning what QCOM would pay to
borrow today, which is higher because a lot of their bonds were issued at lower coupons.
I'm using the effective rate anyway because debt is only about 6 percent of the capital
structure, so the whole input barely moves WACC. I say so in the writeup instead of
hiding it.

Run:  python src/wacc_inputs.py
"""

import sys
from pathlib import Path

import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent))

import edgar

TICKER = "QCOM"

RISK_FREE = 0.0455   # US 10Y Treasury
ERP = 0.050          # Damodaran implied ERP, forward looking, not the 6% historical average
TAX_RATE = 0.14      # my normalized rate, same one the projection uses

INTEREST_TAGS = [
    "InterestExpense",
    "InterestExpenseDebt",
    "InterestExpenseNonoperating",
    "InterestIncomeExpenseNet",
]


def fiscal_year_ends(facts, n=2):
    """Last n fiscal year ends off 10-K instant rows. I need two so I can average debt."""
    ends = set()
    for tag in edgar.BALANCE_ANCHOR_TAGS:
        for r in edgar._instant_rows(facts, tag):
            if str(r.get("form", "")).startswith("10-K"):
                ends.add(r["end"])
    return sorted(ends)[-n:]


def main():
    cik, doc = edgar.company_facts(TICKER)
    if not doc:
        sys.exit(f"couldn't resolve {TICKER}")
    facts = doc["facts"]

    # Market side
    tk = yf.Ticker(TICKER)
    info = tk.info
    beta = info.get("beta")
    mcap = info.get("marketCap")
    price = info.get("currentPrice")

    if beta is None or mcap is None:
        sys.exit("yfinance didn't return beta or market cap, try again")

    # Debt side, from filings
    ends = fiscal_year_ends(facts, n=2)
    prior_end, latest_end = ends[0], ends[1]

    debt_now, detail = edgar.total_debt_for(facts, latest_end)
    debt_prior, _ = edgar.total_debt_for(facts, prior_end)
    avg_debt = (debt_now + debt_prior) / 2

    # Interest expense for the latest year. I try the tags in order and take the first hit,
    # then print which one landed so I can check it against the 10-K myself.
    interest, interest_tag = None, None
    for tag in INTEREST_TAGS:
        v = edgar.annual_value_for_end(facts, tag, latest_end)
        if v is not None:
            interest, interest_tag = v, tag
            break

    if interest is None:
        sys.exit("no interest expense tag matched, need to dump tags and look")

    cost_of_debt = interest / avg_debt

    # CAPM
    cost_of_equity = RISK_FREE + beta * ERP

    # Weights on market value of equity and book value of debt. Book is the standard proxy
    # for market value of debt and it's what I have from the filings.
    v = mcap + debt_now
    we = mcap / v
    wd = debt_now / v

    wacc = we * cost_of_equity + wd * cost_of_debt * (1 - TAX_RATE)

    print(f"{TICKER}  CIK {cik}")
    print(f"balance sheet dates: {prior_end} -> {latest_end}\n")

    print("EQUITY SIDE")
    print(f"  price                 ${price}")
    print(f"  market cap            ${mcap/1e9:.2f}B")
    print(f"  beta (yfinance)        {beta:.2f}")
    print(f"  risk free              {RISK_FREE:.2%}")
    print(f"  equity risk premium    {ERP:.2%}")
    print(f"  cost of equity         {cost_of_equity:.2%}   (Rf + beta x ERP)\n")

    print("DEBT SIDE")
    print(f"  total debt now        ${debt_now/1e9:.2f}B   ({detail})")
    print(f"  total debt prior      ${debt_prior/1e9:.2f}B")
    print(f"  average debt          ${avg_debt/1e9:.2f}B")
    print(f"  interest expense      ${interest/1e9:.2f}B   (tag: {interest_tag})")
    print(f"  cost of debt pre tax   {cost_of_debt:.2%}")
    print(f"  cost of debt after tax {cost_of_debt*(1-TAX_RATE):.2%}\n")

    print("WEIGHTS")
    print(f"  equity weight          {we:.1%}")
    print(f"  debt weight            {wd:.1%}\n")

    print(f"  WACC                   {wacc:.2%}")
    print("\nsanity check: I expect something in the 10 to 11 percent range for QCOM.")
    print("if this is way off, the interest tag or the debt figure is probably wrong.")


if __name__ == "__main__":
    main()