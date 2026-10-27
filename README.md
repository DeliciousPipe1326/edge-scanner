# Edge Scanner

**Arbitrage • Middles • +EV**

## IMPORTANT — ODDS ANALYSIS, NOT BETTING ADVICE

> Edge Scanner is free software for inspecting and comparing sports odds. Its results do not establish that a bet is profitable or that displayed prices and stakes are available.
>
> **Variance is not an edge. Short-term wins do not demonstrate a reliable advantage. Gambling can cause financial loss.**
>
> The software is provided “as is”, without warranty, under the MIT license. You are responsible for your decisions and use of its results. **Use at your own risk.**

Edge Scanner is a **free, open-source sports betting scanner** you can run locally, use as-is, or adapt to your own needs under the MIT license. It scans for arbitrage candidates, middles, and +EV estimates across US, UK, EU, and Australian bookmakers. There is no Edge Scanner subscription.

**You need your own [The Odds API](https://the-odds-api.com/) key.** Scans consume credits from your API account and are subject to the provider's credit limits, rate limits, and plan terms. The scanner does not include an API key or an odds-data allowance; any data-provider charges are separate from this free software.

## Features

| Tab | What it finds | Risk profile |
|-----|---------------|--------------|
| **Arbitrage** | Positive modeled return by covering both sides | Execution and settlement risk |
| **Middles** | Line gaps where both bets can win if the result lands in between | Exact payoff scenarios and conservative break-even thresholds |
| **+EV** | Soft book odds better than a de-vigged sharp reference price | Model risk and variance |

## How It Works

The scanner pulls odds from The Odds API across multiple regions (US, EU, UK, AU) and:

1. **Arbitrage** — finds where the sum of inverse odds across books is less than 1, then calculates cent-rounded stakes and net return after exchange commission
2. **Middles** — finds where books disagree on spreads/totals, creating a gap where both sides can win
3. **+EV** — compares matching soft-book outcomes against a qualified de-vigged reference; edges remain estimates under that market model

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
3. Get your own API key from [The Odds API](https://the-odds-api.com/) and check the credit allowance and limits for your plan
4. Run: `python app.py`

Your browser opens automatically. Enter your API key, choose your settings, and select **Scan Now**. Review the maximum credit cost, then confirm **Run scan**. Canceling the preview makes no paid odds requests. **Download JSON** saves the complete timestamped response without the API key.

Use `python app.py --port 5050` to choose a preferred port, or `--port 0` to bind an available OS-assigned port. The browser opens the actual bound port.

This is a local desktop tool. Host validation permits localhost and loopback addresses only. It has no user authentication or shared-server deployment support. Keys are accepted from the form or environment and are not saved by the app.

**Optional:** Create a `.env` file with your API key to skip manual entry:
```
ODDS_API_KEY=your_api_key_here
```

## Markets Scanned

| Sport | Markets |
|-------|---------|
| American football, basketball, baseball, ice hockey | Moneyline, Spreads, Totals |
| Soccer (including leagues selected with all-sports) | Spreads, Totals only* |
| Other active non-outright sports | Moneyline; only genuine two-way markets are calculated |

*Soccer moneyline is three-way (win/draw/lose) — excluded from two-way scanning.

Arbitrage and +EV spread/total calculations support **half-point lines only**. Whole-point lines can push, and quarter-point lines require split-stake settlement; both are excluded until those outcomes are modeled. Middles support whole- and half-point lines for integer-scored sports, including exact endpoint pushes. Quarter lines are excluded. Unsupported markets are excluded rather than assigned simplified settlement rules.

## API Usage

All odds-data usage is charged against **your own API account's credit allowance**, not an allowance provided by Edge Scanner. Your selected API plan determines the available credits and any associated cost.

Each scan makes one sports-list request (quota-free) and one odds request per active selected sport. **Requests and credits differ:** odds quota cost is markets × regions. A three-market US/EU request can cost six credits. The scanner also includes the selected sharp reference's region (normally EU), even if you did not select it.

Before each scan, the quota-free `/plan` request lists active selected sports, effective regions, estimated maximum credits, and the account's remaining credits. `/scan` checks that bound again before making paid requests. Concurrent scans are rejected; authentication failures, rate limiting, and depleted quota stop further requests.

The UI reports actual scan credits, remaining credits, and odds request count separately using `x-requests-last`, `x-requests-used`, and `x-requests-remaining`. Missing headers or interrupted requests produce **unknown** usage rather than an invented zero. See [The Odds API quota documentation](https://the-odds-api.com/liveapi/guides/v4/#usage-quota-costs-1).

API clients must provide `maxCredits` in `/scan` JSON (the default budget is zero). Optional fields include `maxQuoteAge` (15–900 seconds, default 120), `windowHours` (`24`, `48`, `168`, or `null` for Infinite), and `bookmakers` (a list of result-filter keys). Regions still determine quota cost; bookmaker filters do not reduce requests.

## Quote and reference quality

- Only future events within the selected event window, with valid complete market outcomes, are used. The time window is applied in the provider request and validated again against returned events. Stale, missing, invalid, or future-dated quote timestamps are excluded; market timestamps take precedence over bookmaker timestamps.
- Results expire at the earliest contributing quote's age limit or event start, both on the server and in the browser. Expiry removes rows without replacing still-valid expanded payoff details every second.
- The API is asked for bet limits. Arbitrage searches price combinations that can accommodate the requested stakes when limits are supplied. The browser also hides candidates exceeding reported limits after stake/bankroll changes. Missing limits remain explicitly **unverified**; prices and settlement rules still need checking at the books.
- +EV references fall back per matching market and signed line, in selected-book priority order. References with an absolute implied margin above 5% are excluded. Exchange references additionally require positive reported limits for both sides. Diagnostics distinguish an unavailable reference from the absence of a positive edge.
- All returned regional soft books are eligible, without an aging name allowlist. Optional target-book keys filter all tabs while leaving sharp references available. Suggestions come from fresh observed coverage.

## Understanding the Results

### Arbitrage

ROI is the modeled minimum return on total stake, conditional on both bets being accepted and settling as modeled. A 2% ROI means $2 profit on $100 staked.

Stakes target equal payouts, with cent rounding reflected in the reported minimum return. Quotes are snapshots: changes in odds, stake limits, exchange liquidity, void bets, and differing bookmaker settlement rules can invalidate a modeled return. Only positive net-return arbitrage candidates are returned; exchange commission and cent rounding are applied before that check. Results can share outcomes, so summing their modeled profits is not a realizable portfolio return.

### Middles

The previous fixed probability weights have been removed. The scanner does **not** predict a middle probability or label a middle +EV without a validated outcome distribution.

Instead it shows cent-rounded stakes, all distinct integer-score payoff scenarios (including pushes), the worst payoff, the both-win payoff, and a conservative break-even probability. If the worst non-middle result loses $5 and winning both earns $90, the threshold is `5 / (90 + 5) = 5.26%`. That is the required frequency, not an estimate that the middle occurs 5.26% of the time. Assigning every non-middle result its worst payoff makes this threshold conservative.

First and last scenario rows represent the outside tails. For spreads, scores represent the displayed side A team's winning margin and can be negative. Changing the total stake recalculates the scenarios and threshold in cents, rather than scaling previously rounded stakes.

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
- Event window: **24 hours** (default), **48 hours**, **7 days**, or **Infinite** (all future events returned by the API)
- Minimum net ROI for displaying paper arbitrage candidates (default 2%)
- Maximum quote age
- Optional target-bookmaker keys

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

CI runs the Python tests on 3.11–3.14 on Linux, a Windows 3.14 check, frontend regressions, Ruff checks, and a dependency vulnerability audit. Install `ruff==0.17.2` and `pip-audit==2.10.1` for the same optional local checks.

Direct dependency ranges live in `requirements.in`; `requirements.txt` pins the complete dependency tree with hashes. To update it using [uv](https://docs.astral.sh/uv/):

```sh
uv pip compile requirements.in --upgrade --generate-hashes --universal --python-version 3.11 -o requirements.txt
python -m pip install --require-hashes -r requirements.txt
pip-audit -r requirements.txt
```

### Acceptance coverage

The offline suite exercises quota preview and budgets, actual/missing quota headers, stale and malformed provider data, rate-limit stopping, partial failures, regional book selection, reference quality, liquidity fallback, stake rounding, push settlement, and the Flask-to-provider boundary. Frontend tests cover two-step scan confirmation and cancellation, expired results, HTML escaping, limit checks, live stake changes, and recoverable errors.

The maintenance release was also exercised against the live NBA endpoint: two six-credit scans processed seven events each, with the response quota moving from 500 to 488. The retained local response lives under ignored `data/`; no API key or live snapshot is included in the repository. Subsequent reference-quality regressions use the poor-margin exchange prices observed in that check without additional API calls.

### Supported product scope

This is a local analysis tool, not an automated bet-placement system. It does not authenticate to bookmakers, reserve prices or liquidity, validate account-specific eligibility/currency, or guarantee realized returns. It supports two-way arbitrage and +EV modeling on the markets above, plus deterministic middle payoff analysis. Three-way arbitrage, Asian quarter-line settlement, player props, and empirically calibrated middle prediction are outside the current product scope.

MIT licensed. Contributions are welcome.

## Support

If this tool saves you time or helps you find an edge, consider buying me a coffee:

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support-yellow.svg)](https://buymeacoffee.com/deliciouspipe1326)
