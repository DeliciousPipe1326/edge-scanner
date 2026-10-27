# Edge Scanner

**Arbitrage • Middles • +EV**

Open-source sports betting scanner that finds arbitrage opportunities, middle bets, and +EV plays across US and EU bookmakers.

## Features

| Tab | What it finds | Risk profile |
|-----|---------------|--------------|
| **Arbitrage** | Positive modeled return by covering both sides | Execution and settlement risk |
| **Middles** | Line gaps where both bets can win if the result lands in between | High variance; EV uses uncalibrated estimates |
| **+EV** | Soft book odds better than a de-vigged sharp reference price | Model risk and variance |

## How It Works

The scanner pulls odds from The Odds API across multiple regions (US, EU, UK, AU) and:

1. **Arbitrage** — finds where the sum of inverse odds across books is less than 1, then calculates cent-rounded stakes and net return after exchange commission
2. **Middles** — finds where books disagree on spreads/totals, creating a gap where both sides can win
3. **+EV** — compares soft book odds to sharp reference (Pinnacle), identifying bets with positive expected value

## Setup

Requires **Python 3.11 or newer**. Use a virtual environment:

```sh
git clone https://github.com/DeliciousPipe1326/edge-scanner.git
cd edge-scanner
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install --require-hashes -r requirements.txt
python app.py
```

1. Clone this repo and create the environment above
2. Install the pinned dependencies above
3. Get a free API key from [The Odds API](https://the-odds-api.com/)
4. Run: `python app.py`

Your browser opens automatically. Enter your API key and scan.

This is a local desktop tool. Host validation permits localhost and loopback addresses only. It has no user authentication or shared-server deployment support. Keys are accepted from the form or environment and are not saved by the app.

**Optional:** Create a `.env` file with your API key to skip manual entry:
```
ODDS_API_KEY=your_api_key_here
```

## Markets Scanned

| Sport | Markets |
|-------|---------|
| NFL, NBA, MLB, NHL | Moneyline, Spreads, Totals |
| Soccer (EPL, La Liga, etc.) | Spreads, Totals only* |

*Soccer moneyline is three-way (win/draw/lose) — excluded from two-way scanning.

Arbitrage and +EV spread/total calculations support **half-point lines only**. Whole-point lines can push, and quarter-point lines require split-stake settlement; both are excluded until those outcomes are modeled. Middle estimates can include whole-point lines but do not account for endpoint pushes.

## API Usage

Each scan makes one sports-list request (quota-free) and one odds request per active selected sport. **Requests and credits differ:** odds quota cost is markets × regions. A three-market US/EU request can cost six credits. The scanner also includes the selected sharp reference's region (normally EU), even if you did not select it.

The UI reports odds request count, not credits consumed. Actual quota usage is available in the provider's `x-requests-last`, `x-requests-used`, and `x-requests-remaining` response headers. See [The Odds API quota documentation](https://the-odds-api.com/liveapi/guides/v4/#usage-quota-costs-1) and your account plan for current limits.

## Understanding the Results

### Arbitrage

ROI = guaranteed return on total stake. A 2% ROI means $2 profit on $100 staked.

Stakes target equal payouts, with cent rounding reflected in the reported minimum return. Quotes are snapshots: changes in odds, stake limits, exchange liquidity, void bets, and differing bookmaker settlement rules can invalidate a modeled return. Gross-positive exchange candidates may still have a negative net return; inspect net ROI.

### Middles

EV uses fixed probability weights from `config.py`. These are uncalibrated heuristics; the repository contains no historical dataset or backtest establishing their accuracy. The calculation assumes each miss direction is equally likely and omits pushes.

NFL key numbers (3, 7) significantly boost probability for spread middles — these margins occur more often due to field goals and touchdowns.

Middles can lose on one side and win both inside the gap. A positive estimated EV does not establish profitability.

### +EV

Edge = how much better the soft book odds are compared to sharp book fair value.

The scanner normalizes inverse sharp odds to remove vig and compares matching outcomes and half-point lines. The resulting probability is a market-based estimate, not a known true probability. Stale quotes and inconsistent settlement rules can produce apparent edges.

Kelly staking helps size bets based on edge — quarter Kelly recommended to reduce variance.

## Configuration

Configurable via the UI:
- Regions (US, US2, UK, EU, AU)
- Sports selection
- Exchange commission rate (for Betfair, etc.)
- Minimum gap for middles
- Minimum edge for +EV
- Sharp reference book (Pinnacle, Betfair, Matchbook)
- Kelly fraction (full, half, quarter, tenth)
- Stake amount / bankroll

## Tech Stack

- **Backend:** Python, Flask
- **Frontend:** HTML, CSS, JavaScript
- **Data:** The Odds API

## License

MIT

## Contributing

Run the offline regression tests (no API key or paid quota needed):

```sh
python -m unittest discover -s tests -v
node --test tests/frontend.test.cjs  # Node.js 22+; no npm dependencies
```

CI runs the Python tests on 3.11–3.14, frontend regressions, Ruff checks, and a dependency vulnerability audit. Install `ruff==0.17.2` and `pip-audit==2.10.1` for the same optional local checks.

Direct dependency ranges live in `requirements.in`; `requirements.txt` pins the complete dependency tree with hashes. To update it using [uv](https://docs.astral.sh/uv/):

```sh
uv pip compile requirements.in --upgrade --generate-hashes --universal --python-version 3.11 -o requirements.txt
python -m pip install --require-hashes -r requirements.txt
pip-audit -r requirements.txt
```

### Technical follow-up priorities

- Track actual quota headers and show scan cost before starting an all-sports scan.
- Check quote ages and exchange liquidity before treating prices as actionable.
- Calibrate middle probabilities against historical results and model pushes and split settlements.
- Refresh regional bookmaker coverage and replace the fixed soft-book allowlist with explicit user selection.
- Split the inline frontend script into modules once these behaviors have regression coverage.

PRs welcome. Ideas for future versions:
- Alerts/notifications
- Historical tracking
- Three-way arbitrage (soccer moneylines)
- Player props

## Support

If this tool saves you time or helps you find an edge, consider buying me a coffee:

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support-yellow.svg)](https://buymeacoffee.com/deliciouspipe1326)
