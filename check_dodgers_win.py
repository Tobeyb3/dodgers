#!/usr/bin/env python3
"""Dodgers home-game win notifier.

Run daily by cron. Checks whether the date in question (yesterday by
default) was a Dodgers home game per the promotion schedule CSV; if the
Dodgers won that game, publishes the promo message to ntfy.sh/dodgers.
Losses and off-days do nothing.

Usage:
  check_dodgers_win.py                 # check yesterday's date
  check_dodgers_win.py --date 2026-08-05
  check_dodgers_win.py --date 2026-08-05 --dry-run   # print, don't send
"""

import argparse
import csv
import json
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

CSV_PATH = Path("/Users/tobias/Downloads/GameTicketPromotionPrice.csv")
NTFY_URL = "https://ntfy.sh/dodgers"
DODGERS_TEAM_ID = 119
LOG_PATH = Path(__file__).with_name("mailer.log")

EMAIL_SUBJECT = "Dodgers Win! Code DODGERSWIN is active"
EMAIL_BODY = (
    "code dodgerswin is active step up to the plate and grab so panda expressly"
)
DP_LINE = "\n\ndouble play is in play its time to be habitual with code DODGERS26"


def log(msg: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line)
    with LOG_PATH.open("a") as f:
        f.write(line + "\n")


def is_home_game_date(target: date) -> bool:
    with CSV_PATH.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                start = datetime.strptime(row["START DATE"], "%m/%d/%y").date()
            except (ValueError, KeyError):
                continue
            if start == target and "at Dodgers" in row.get("SUBJECT", ""):
                return True
    return False


def fetch_results(target: date) -> list[dict]:
    """Return Dodgers home games for the date from the MLB Stats API."""
    url = (
        "https://statsapi.mlb.com/api/v1/schedule"
        f"?sportId=1&teamId={DODGERS_TEAM_ID}"
        f"&startDate={target:%Y-%m-%d}&endDate={target:%Y-%m-%d}"
    )
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.load(resp)

    results = []
    for day in data.get("dates", []):
        for game in day.get("games", []):
            home = game["teams"]["home"]
            away = game["teams"]["away"]
            if home["team"]["id"] != DODGERS_TEAM_ID:
                continue  # road game or listing quirk; CSV is home-only anyway
            results.append(
                {
                    "gamePk": game["gamePk"],
                    "final": game["status"]["abstractGameState"] == "Final",
                    "status": game["status"]["detailedState"],
                    "won": home.get("isWinner", False),
                    "home_score": home.get("score"),
                    "away_score": away.get("score"),
                    "opponent": away["team"]["name"],
                }
            )
    return results


def turned_double_play(game_pk: int) -> bool:
    """True if the Dodgers (home team) turned at least one double play."""
    url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.load(resp)
        for item in data["teams"]["home"].get("info", []):
            if item.get("title", "").upper() == "FIELDING":
                for field in item.get("fieldList", []):
                    if field.get("label") == "DP":
                        return True
    except Exception as e:
        log(f"boxscore lookup failed for game {game_pk}: {e}")
    return False


def send_ntfy(subject: str, body: str) -> None:
    """Publish to the ntfy.sh topic (equivalent of curl -d body -H Title: ...)."""
    req = urllib.request.Request(
        NTFY_URL,
        data=body.encode("utf-8"),
        headers={"Title": subject},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()
    log(f"ntfy notification sent to {NTFY_URL}: {subject}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="date to check, YYYY-MM-DD (default: yesterday)")
    parser.add_argument(
        "--dry-run", action="store_true", help="print instead of notifying"
    )
    args = parser.parse_args()

    target = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date
        else date.today() - timedelta(days=1)
    )

    if not is_home_game_date(target):
        log(f"{target}: no home game in schedule CSV; nothing to do")
        return 0

    try:
        games = fetch_results(target)
    except Exception as e:
        log(f"{target}: MLB API lookup failed: {e}")
        return 1

    if not games:
        log(f"{target}: home game in CSV but MLB API returned no games (postponed?)")
        return 0

    for g in games:
        if not g["final"]:
            log(
                f"{target}: game vs {g['opponent']} not final yet ({g['status']}); skipping"
            )
            continue
        score = f"{g['home_score']}-{g['away_score']}"
        dp = turned_double_play(g["gamePk"])
        if not g["won"]:
            log(
                f"{target}: Dodgers lost {score} to {g['opponent']} (double play: {dp}); no notification"
            )
            continue
        log(f"{target}: Dodgers WON {score} vs {g['opponent']} (double play: {dp})")
        body = EMAIL_BODY + DP_LINE if dp else EMAIL_BODY
        if args.dry_run:
            log(f"DRY RUN — would send: {EMAIL_SUBJECT}")
            print("\n" + body + "\n")
        else:
            send_ntfy(EMAIL_SUBJECT, body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
