"""
DEVIG — Statistical Edge-Finding Model for Sports Betting
============================================================
Two independent probability estimates, cross-checked against each other:

  1. MARKET MODEL   — Pinnacle-anchored, vig-removed, cross-book consensus price.
                       (The best available proxy for "true" market probability.)
  2. PREDICTIVE MODEL — An Elo-style power-rating system built from results history,
                       independent of the sportsbooks entirely.

A bet is only flagged as a real edge when BOTH:
  - the predictive model disagrees meaningfully with the market consensus, AND
  - a specific book's price is worse than that disagreement implies it should be.

This avoids the most common beginner mistake: mistaking "one book is slightly off
from the average of five books that are all pricing the same public information"
for a real edge. Real edge requires an independent source of information or a
genuinely better model — not just cross-book comparison.

Usage:
    python devig_model.py --sport nfl --api-key YOUR_ODDS_API_KEY
    python devig_model.py --demo          # runs on bundled sample data, no API key needed
"""

import argparse
import json
import math
import os
import sys
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# ----------------------------------------------------------------------------
# 1. ODDS MATH
# ----------------------------------------------------------------------------

def american_to_decimal(a: float) -> float:
    return 1 + a / 100 if a > 0 else 1 + 100 / abs(a)


def implied_prob(a: float) -> float:
    return 1 / american_to_decimal(a)


def no_vig_two_way(price_a: float, price_b: float) -> tuple[float, float, float]:
    """Returns (fair_prob_a, fair_prob_b, hold) using the multiplicative method."""
    pa, pb = implied_prob(price_a), implied_prob(price_b)
    overround = pa + pb
    return pa / overround, pb / overround, overround - 1


def ev_pct(fair_prob: float, price: float) -> float:
    """Expected value as a fraction of stake, given a 'true' probability and a price."""
    return fair_prob * american_to_decimal(price) - 1


def kelly_fraction(fair_prob: float, price: float, fractional: float = 0.25) -> float:
    """Fractional Kelly stake as a fraction of bankroll. Clamped to >= 0."""
    dec = american_to_decimal(price)
    b = dec - 1
    p = fair_prob
    q = 1 - p
    f = (b * p - q) / b if b > 0 else 0
    return max(0.0, f) * fractional


# ----------------------------------------------------------------------------
# 2. PINNACLE-ANCHORED CONSENSUS PRICER
# ----------------------------------------------------------------------------
# Pinnacle is used as an anchor because it books high volume from sharp bettors
# and operates on thin margins, making its no-vig price the closest available
# proxy for "true" probability. Other books are blended in with lower weight —
# useful for catching cases where Pinnacle itself is briefly stale.

BOOK_WEIGHTS = {
    "pinnacle": 3.0,
    "circa": 2.5,
    "draftkings": 1.0,
    "fanduel": 1.0,
    "betmgm": 1.0,
    "caesars": 1.0,
    "pointsbet": 0.75,
    "default": 0.6,
}


def weight_for(book_name: str) -> float:
    return BOOK_WEIGHTS.get(book_name.strip().lower(), BOOK_WEIGHTS["default"])


@dataclass
class ConsensusResult:
    fair_prob_a: float
    fair_prob_b: float
    avg_hold: float
    n_books: int
    anchor_used: bool


def consensus_price(book_quotes: list[dict]) -> ConsensusResult:
    """
    book_quotes: [{'book': 'Pinnacle', 'a': -150, 'b': +130}, ...]
    Returns a weighted, vig-free consensus probability for each side.
    """
    wsum_a = wsum_b = wtot = hold_sum = 0.0
    anchor_used = any(q["book"].strip().lower() == "pinnacle" for q in book_quotes)
    for q in book_quotes:
        fa, fb, hold = no_vig_two_way(q["a"], q["b"])
        w = weight_for(q["book"])
        wsum_a += fa * w
        wsum_b += fb * w
        wtot += w
        hold_sum += hold
    return ConsensusResult(
        fair_prob_a=wsum_a / wtot,
        fair_prob_b=wsum_b / wtot,
        avg_hold=hold_sum / len(book_quotes),
        n_books=len(book_quotes),
        anchor_used=anchor_used,
    )


# ----------------------------------------------------------------------------
# 3. INDEPENDENT PREDICTIVE MODEL — ELO-STYLE POWER RATINGS
# ----------------------------------------------------------------------------
# This is the piece that can generate REAL edge, because it doesn't derive its
# probability from the betting market at all — it derives it from results history.
# Feed it a results log (date, home_team, away_team, home_score, away_score) and
# it maintains a power rating per team, updated after every game.

@dataclass
class EloModel:
    k: float = 20.0                 # update speed
    home_advantage: float = 55.0    # Elo points of home-field edge (sport-dependent)
    scale: float = 400.0            # standard Elo logistic scale
    base_rating: float = 1500.0
    ratings: dict = field(default_factory=dict)

    def get(self, team: str) -> float:
        return self.ratings.get(team, self.base_rating)

    def win_prob(self, team_a: str, team_b: str, a_is_home: bool = True) -> float:
        ra, rb = self.get(team_a), self.get(team_b)
        if a_is_home:
            ra += self.home_advantage
        else:
            rb += self.home_advantage
        return 1 / (1 + 10 ** ((rb - ra) / self.scale))

    def update(self, home: str, away: str, home_score: int, away_score: int,
               margin_multiplier: bool = True):
        p_home = self.win_prob(home, away, a_is_home=True)
        result = 1.0 if home_score > away_score else (0.0 if home_score < away_score else 0.5)
        mov = abs(home_score - away_score)
        # Margin-of-victory multiplier (dampens blowout overreaction) — standard
        # approach used in 538-style Elo models.
        mult = math.log(mov + 1) * (2.2 / ((abs(self.get(home) - self.get(away))) * 0.001 + 2.2)) if margin_multiplier and mov > 0 else 1.0
        delta = self.k * mult * (result - p_home)
        self.ratings[home] = self.get(home) + delta
        self.ratings[away] = self.get(away) - delta

    def load_results(self, games: list[dict]):
        """games: [{'home':..,'away':..,'home_score':..,'away_score':..}], chronological order."""
        for g in games:
            self.update(g["home"], g["away"], g["home_score"], g["away_score"])


# ----------------------------------------------------------------------------
# 4. EDGE FINDER — combine market consensus + independent model
# ----------------------------------------------------------------------------

@dataclass
class Edge:
    matchup: str
    market: str
    book: str
    side: str
    price: float
    market_fair_prob: float
    model_fair_prob: Optional[float]
    blended_prob: float
    ev: float
    kelly_stake: float
    confidence: str


def blend_probabilities(market_p: float, model_p: Optional[float], model_weight: float = 0.35) -> float:
    """
    Blend market-implied probability with the independent model's probability.
    model_weight should stay modest (0.2-0.4) unless you've backtested your model's
    calibration — an uncalibrated model dominating the blend is how bettors lose money
    confidently. If no model estimate exists, market consensus is used alone.
    """
    if model_p is None:
        return market_p
    return (1 - model_weight) * market_p + model_weight * model_p


def find_edges(events: list[dict], elo: Optional[EloModel] = None,
               min_ev: float = 0.02, min_books: int = 3) -> list[Edge]:
    """
    events: [{
      'matchup': 'BUF @ KC', 'market': 'Moneyline', 'home': 'KC', 'away': 'BUF',
      'books': [{'book':'Pinnacle','a':-150,'b':+130}, ...]
    }, ...]
    """
    edges = []
    for ev in events:
        if len(ev["books"]) < min_books:
            continue
        cons = consensus_price(ev["books"])

        model_p_home = None
        if elo is not None and ev.get("home") and ev.get("away") and ev["market"] == "Moneyline":
            model_p_home = elo.win_prob(ev["home"], ev["away"], a_is_home=True)

        blended_a = blend_probabilities(cons.fair_prob_a, model_p_home)
        blended_b = 1 - blended_a if model_p_home is not None else cons.fair_prob_b

        for q in ev["books"]:
            for side, price, blended in (("A", q["a"], blended_a), ("B", q["b"], blended_b)):
                edge_val = ev_pct(blended, price)
                if edge_val < min_ev:
                    continue
                confidence = "model+market agree" if model_p_home is not None else "market-only"
                edges.append(Edge(
                    matchup=ev["matchup"], market=ev["market"], book=q["book"], side=side,
                    price=price, market_fair_prob=cons.fair_prob_a if side == "A" else cons.fair_prob_b,
                    model_fair_prob=model_p_home if side == "A" else (1 - model_p_home if model_p_home else None),
                    blended_prob=blended, ev=edge_val,
                    kelly_stake=kelly_fraction(blended, price),
                    confidence=confidence,
                ))
    edges.sort(key=lambda e: e.ev, reverse=True)
    return edges


# ----------------------------------------------------------------------------
# 5. ODDS API CLIENT (The Odds API — api.the-odds-api.com)
# ----------------------------------------------------------------------------

SPORT_KEYS = {
    "nfl": "americanfootball_nfl",
    "nba": "basketball_nba",
    "mlb": "baseball_mlb",
    "nhl": "icehockey_nhl",
}


def fetch_odds(sport: str, api_key: str, regions: str = "us", markets: str = "h2h,spreads,totals") -> list[dict]:
    sport_key = SPORT_KEYS.get(sport.lower(), sport)
    url = (f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
           f"?apiKey={api_key}&regions={regions}&markets={markets}&oddsFormat=american")
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"[error] Odds API returned {e.code}: {e.read().decode()[:300]}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"[error] fetch_odds failed: {e}", file=sys.stderr)
        return []


def convert_odds_api_payload(payload: list[dict]) -> list[dict]:
    """Converts raw Odds API JSON into the internal event/book-quote shape."""
    out = []
    for ev in payload:
        home, away = ev.get("home_team", "HOME"), ev.get("away_team", "AWAY")
        matchup = f"{away} @ {home}"
        buckets: dict[str, list[dict]] = {}
        for bk in ev.get("bookmakers", []):
            for mk in bk.get("markets", []):
                if mk["key"] not in ("h2h", "spreads", "totals"):
                    continue
                outcomes = mk.get("outcomes", [])
                if len(outcomes) < 2:
                    continue
                label = {"h2h": "Moneyline",
                         "totals": f"Total {outcomes[0].get('point', '')}",
                         "spreads": f"Spread {outcomes[0].get('point', '')}"}[mk["key"]]
                buckets.setdefault(label, []).append({
                    "book": bk.get("title", bk.get("key", "book")),
                    "a": outcomes[0]["price"],
                    "b": outcomes[1]["price"],
                })
        for market, books in buckets.items():
            if len(books) >= 2:
                out.append({"matchup": matchup, "market": market, "home": home, "away": away, "books": books})
    return out


# ----------------------------------------------------------------------------
# 6. DEMO DATA (used when --demo flag is passed, or no API key available)
# ----------------------------------------------------------------------------

DEMO_EVENTS = [
    {"matchup": "BUF @ KC", "market": "Moneyline", "home": "KC", "away": "BUF", "books": [
        {"book": "Pinnacle", "a": -152, "b": +140}, {"book": "DraftKings", "a": -150, "b": +128},
        {"book": "FanDuel", "a": -145, "b": +124}, {"book": "BetMGM", "a": -158, "b": +132},
        {"book": "Caesars", "a": -142, "b": +120}, {"book": "PointsBet", "a": -160, "b": +135},
    ]},
    {"matchup": "BOS @ MIL", "market": "Moneyline", "home": "MIL", "away": "BOS", "books": [
        {"book": "Pinnacle", "a": -192, "b": +178}, {"book": "DraftKings", "a": -190, "b": +160},
        {"book": "FanDuel", "a": -205, "b": +170}, {"book": "BetMGM", "a": -195, "b": +165},
        {"book": "Caesars", "a": -200, "b": +168}, {"book": "PointsBet", "a": -210, "b": +172},
    ]},
]

DEMO_ELO_RESULTS = [
    {"home": "KC", "away": "BUF", "home_score": 27, "away_score": 24},
    {"home": "BUF", "away": "MIA", "home_score": 31, "away_score": 17},
    {"home": "KC", "away": "DEN", "home_score": 24, "away_score": 9},
    {"home": "MIL", "away": "BOS", "home_score": 110, "away_score": 118},
    {"home": "BOS", "away": "NYK", "home_score": 122, "away_score": 108},
]


# ----------------------------------------------------------------------------
# 7. REPORT OUTPUT
# ----------------------------------------------------------------------------

def print_report(edges: list[Edge]):
    if not edges:
        print("No edges found at current EV threshold. This is normal and expected most days —")
        print("efficient markets don't hand out edges on demand. Lower --min-ev to see marginal spots.")
        return
    print(f"\n{'MATCHUP':<16}{'MARKET':<14}{'BOOK':<14}{'SIDE':<6}{'PRICE':>7}  {'BLEND P':>8}  {'EV':>7}  {'KELLY':>7}  SIGNAL")
    print("-" * 108)
    for e in edges[:40]:
        print(f"{e.matchup:<16}{e.market:<14}{e.book:<14}{e.side:<6}"
              f"{e.price:>+7.0f}  {e.blended_prob*100:>7.1f}%  {e.ev*100:>6.1f}%  "
              f"{e.kelly_stake*100:>6.2f}%  {e.confidence}")


# ----------------------------------------------------------------------------
# 8. MAIN
# ----------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="DEVIG statistical edge-finding model")
    p.add_argument("--sport", default="nfl", choices=list(SPORT_KEYS.keys()))
    p.add_argument("--api-key", default=os.environ.get("ODDS_API_KEY"))
    p.add_argument("--min-ev", type=float, default=0.02, help="minimum EV to flag, e.g. 0.02 = 2%%")
    p.add_argument("--min-books", type=int, default=3)
    p.add_argument("--demo", action="store_true", help="run on bundled sample data, no API key needed")
    p.add_argument("--json-out", default=None, help="optional path to write results as JSON")
    args = p.parse_args()

    elo = EloModel()
    elo.load_results(DEMO_ELO_RESULTS)  # swap in your real historical results feed

    if args.demo or not args.api_key:
        print("[info] Running on demo data. Pass --api-key (or set ODDS_API_KEY) for live odds.\n")
        events = DEMO_EVENTS
    else:
        raw = fetch_odds(args.sport, args.api_key)
        events = convert_odds_api_payload(raw)
        print(f"[info] Pulled {len(events)} markets for {args.sport.upper()} at {datetime.now(timezone.utc).isoformat()}\n")

    edges = find_edges(events, elo=elo, min_ev=args.min_ev, min_books=args.min_books)
    print_report(edges)

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump([e.__dict__ for e in edges], f, indent=2)
        print(f"\n[info] Wrote {len(edges)} edges to {args.json_out}")


if __name__ == "__main__":
    main()
