# DEVIG — Statistical Edge-Finding Model

A two-layer system for finding genuine +EV betting opportunities: a Pinnacle-anchored
market consensus pricer, and an independent Elo-style predictive model, cross-checked
against each other so you're not just mistaking book-to-book noise for real edge.

## Why two layers, not one

Comparing one book's price to the average of five other books ("line shopping") mostly
finds *stale prices*, not mispricing — a book that hasn't updated as fast as the market
moved. That's still worth capturing (see "How to use the output" below), but it isn't a
repeatable statistical edge on its own, because the market usually corrects within
minutes and the "edge" often reflects information already baked into the sharper books.

Real, repeatable edge requires an independent source of signal — either information the
market hasn't priced yet, or a genuinely well-calibrated predictive model. This system's
second layer (`EloModel`) is that independent signal: it estimates win probability purely
from a results history, with zero input from sportsbook prices. When the model and the
market disagree by more than a book's price can explain, *that's* a real signal worth
investigating — not proof of an edge, but a legitimate hypothesis to size cautiously.

## Quick start

```bash
# No API key needed — runs on bundled sample data
python3 devig_model.py --demo

# Live data (requires a free or paid key from the-odds-api.com)
python3 devig_model.py --sport nfl --api-key YOUR_KEY --min-ev 0.02

# Or set it once:
export ODDS_API_KEY=your_key_here
python3 devig_model.py --sport nba
```

Flags:
- `--sport` — nfl / nba / mlb / nhl
- `--min-ev` — minimum edge to report, as a decimal (0.02 = 2%). Start at 0.03–0.05;
  anything reporting huge EV (10%+) on a mainstream market is far more likely a data
  error (stale line, wrong market matched) than a real opportunity — check it manually.
- `--min-books` — require at least N books quoting a market before trusting consensus
- `--json-out` — dump results to a JSON file for your own dashboard/pipeline

## Plugging in real predictive power

`EloModel.load_results()` takes a chronological list of `{home, away, home_score,
away_score}` — replace `DEMO_ELO_RESULTS` in the script with a real results feed
(easy free sources: nflfastR/nflverse data for NFL, Basketball-Reference exports for
NBA, Retrosheet for MLB, MoneyPuck for NHL). The model updates ratings incrementally,
so a full season of results gives you meaningfully calibrated team strength estimates.

This bundled Elo is deliberately simple — a starting scaffold, not a finished
predictive model. The honest path to a real edge is iterating on this layer:
adding injury/lineup adjustments, pace/possession-adjusted ratings, rest and travel
factors, or a regression model on top of Elo. That iteration is where the actual
skill (and the actual work) in quant sports betting lives — no single script
shortcuts it.

## How to use the output responsibly

- `blended_prob` — the model's best estimate of true win probability (market consensus
  blended with your Elo model at a conservative 35% weight — raise this only after
  you've backtested your model's calibration on held-out seasons)
- `ev` — expected value of that specific book's price against the blended probability
- `kelly_stake` — quarter-Kelly recommended stake as a fraction of bankroll. Never bet
  full Kelly on a model you haven't extensively backtested; the size is punishing to
  estimation error
- `confidence` — flags whether the edge is corroborated by your independent model or
  is market-only (pure line-shopping, weaker signal)

## What this will not do

- Guarantee profit on any individual bet — EV is a long-run statistical property, not
  a prediction about one game
- Reliably beat Pinnacle's closing line on mainstream NFL/NBA sides — that market is
  extremely efficient
- Replace bankroll discipline — even a real, well-calibrated edge loses on any given
  day; the model only pays off across a large sample and consistent, sane sizing

## Files

- `devig_model.py` — the full engine (odds math, consensus pricer, Elo model, edge
  finder, Odds API client, CLI)
- `daily_run.py` — pulls all 4 sports, ranks edges, dedupes to 5 distinct games,
  emails you the picks. This is what runs on the schedule.
- `.github/workflows/daily-picks.yml` — the "do nothing" automation
- `results_data/{sport}.json` — put your real historical results here (see below);
  falls back to tiny demo data if missing
- `requirements.txt` — stdlib only, no install needed for the base script

## Setting up the "runs every morning automatically" version

This uses GitHub Actions, which is free for this use case and needs no server of
yours running anywhere. One-time setup, roughly 10 minutes:

1. **Create a GitHub repo** and push these files to it (a private repo is fine —
   GitHub Actions works the same on private repos, still free at this scale).

2. **Get an Odds API key** — free tier at the-odds-api.com
   (500 requests/month free; this workflow uses ~4/day so you're well within it).

3. **Set up an email sender.** Easiest: a Gmail account with a 16-character
   App Password (not your real Gmail password — Google blocks plain password
   SMTP login). Generated in Google Account → Security → App Passwords, ~2 minutes.

4. **Add repo secrets** — in your GitHub repo: Settings → Secrets and variables →
   Actions → New repository secret. Add:
   - `ODDS_API_KEY` — from step 2
   - `SMTP_USER` — the Gmail address
   - `SMTP_PASS` — the app password from step 3
   - `TO_EMAIL` — where you want the picks sent (can be the same Gmail address)

5. **Done.** The workflow fires automatically at 11:00 UTC (~7am ET) every day.
   To test it immediately instead of waiting: GitHub repo → Actions tab →
   "DEVIG Daily Picks" → Run workflow (the workflow_dispatch trigger).

### Feeding it real historical results (do this before trusting any picks)

Right now the Elo layer runs on a handful of hardcoded demo games — it has no real
predictive power yet. Drop a chronological results file at `results_data/nfl.json`
(and nba.json, mlb.json, nhl.json) shaped like:

```json
[
  {"home": "KC", "away": "BUF", "home_score": 27, "away_score": 24},
  {"home": "BUF", "away": "MIA", "home_score": 31, "away_score": 17}
]
```

Free sources: nflverse/nflfastR (NFL), Basketball-Reference (NBA),
Retrosheet (MLB), MoneyPuck (NHL). A full season of results gives Elo enough
signal to be meaningfully calibrated — until then, treat "model+market agree"
tags with real skepticism.
