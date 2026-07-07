"""
daily_run.py — the "do nothing" layer on top of devig_model.py

Pulls live odds for NFL/NBA/MLB/NHL, runs the Pinnacle-anchored consensus model +
Elo predictive model, ranks every edge found, and emails you the top 5 every
morning when run on a schedule (see .github/workflows/daily-picks.yml).

Required environment variables (set as GitHub Actions secrets, see README):
  ODDS_API_KEY   - from the-odds-api.com
  SMTP_USER      - sending email address (e.g. a Gmail address)
  SMTP_PASS      - app password for that account (NOT your normal password)
  TO_EMAIL       - where the picks get sent (can be same as SMTP_USER)

Optional:
  MIN_EV         - minimum EV to qualify as a pick, default 0.03 (3%)
  SMTP_HOST      - default smtp.gmail.com
  SMTP_PORT      - default 587
"""

import os
import smtplib
import json
import glob
from email.mime.text import MIMEText
from datetime import datetime, timezone

from devig_model import (
    EloModel, fetch_odds, convert_odds_api_payload, find_edges,
    DEMO_ELO_RESULTS, SPORT_KEYS,
)

MIN_EV = float(os.environ.get("MIN_EV", "0.03"))
TOP_N = 5


def load_results_for_sport(sport: str) -> list[dict]:
    """
    Looks for results_data/{sport}.json — a chronological list of
    {home, away, home_score, away_score}. Falls back to the bundled demo
    results if none exists yet (you should replace these per README).
    """
    path = os.path.join(os.path.dirname(__file__), "results_data", f"{sport}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return DEMO_ELO_RESULTS


def build_elo(sport: str) -> EloModel:
    elo = EloModel()
    elo.load_results(load_results_for_sport(sport))
    return elo


def gather_all_edges() -> list:
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        raise SystemExit("[fatal] ODDS_API_KEY not set — cannot fetch live odds.")

    all_edges = []
    for sport in ["mlb"]:
        elo = build_elo(sport)
        raw = fetch_odds(sport, api_key)
        events = convert_odds_api_payload(raw)
        if not events:
            print(f"[warn] no live events returned for {sport} (off-season or no games today)")
            continue
        edges = find_edges(events, elo=elo, min_ev=MIN_EV, min_books=3)
        for e in edges:
            e_dict = e.__dict__.copy()
            e_dict["sport"] = sport.upper()
            all_edges.append(e_dict)

    # Prefer model-corroborated edges over market-only, then sort by EV
    all_edges.sort(key=lambda e: (e["confidence"] != "model+market agree", -e["ev"]))

    # Dedupe so the top N are distinct matchups (best single price per game),
    # not the same game repeated across every book that's slow to update.
    seen_matchups = set()
    deduped = []
    for e in all_edges:
        if e["matchup"] in seen_matchups:
            continue
        seen_matchups.add(e["matchup"])
        deduped.append(e)
        if len(deduped) == TOP_N:
            break
    return deduped


def format_email(picks: list) -> str:
    if not picks:
        return (
            "No picks cleared the EV threshold today. This is normal — efficient "
            "markets don't hand out edges every day, and a system that always finds "
            "5 picks regardless of market conditions is a red flag, not a feature.\n"
        )
    lines = [f"DEVIG — Top {len(picks)} Picks — {datetime.now(timezone.utc).strftime('%A, %B %d %Y')}\n"]
    for i, p in enumerate(picks, 1):
        lines.append(
            f"{i}. [{p['sport']}] {p['matchup']} — {p['market']}\n"
            f"   {p['book']}: {p['side']} @ {p['price']:+.0f}\n"
            f"   Model probability: {p['blended_prob']*100:.1f}%  |  EV: +{p['ev']*100:.1f}%  |  "
            f"Suggested stake (¼ Kelly): {p['kelly_stake']*100:.2f}% of bankroll\n"
            f"   Signal: {p['confidence']}\n"
        )
    lines.append(
        "\nReminder: these are statistical edges, not certainties. Even a genuine "
        "positive-EV bet loses often — the edge only pays off across many bets sized "
        "consistently. Never stake more than the suggested Kelly fraction."
    )
    return "\n".join(lines)


def send_email(body: str):
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_PASS"]
    to_email = os.environ.get("TO_EMAIL", smtp_user)
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))

    msg = MIMEText(body)
    msg["Subject"] = f"DEVIG Picks — {datetime.now(timezone.utc).strftime('%b %d')}"
    msg["From"] = smtp_user
    msg["To"] = to_email

    with smtplib.SMTP(host, port) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, [to_email], msg.as_string())
    print(f"[info] Emailed {len(body)} chars to {to_email}")


def main():
    picks = gather_all_edges()
    body = format_email(picks)
    print(body)  # always visible in the GitHub Actions log too

    if os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASS"):
        send_email(body)
    else:
        print("[info] SMTP_USER/SMTP_PASS not set — skipping email, printed picks above only.")


if __name__ == "__main__":
    main()
