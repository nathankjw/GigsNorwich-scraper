#!/usr/bin/env python3
"""
Norwich Gigs — Add a Manual Event

For events you hear about directly rather than from a scraped venue site.
Appends one event to manual_events.csv, sitting in the repo alongside your
scraper — a file the daily scraper never touches or overwrites.

Every day, when the GitHub Action runs, it merges whatever is in this file
into the site's data automatically — so an event you add here keeps showing
up on the blog every single day, through every re-scrape, right up until
its date has passed. Once the date is in the past, it quietly stops being
included — you don't need to come back and delete it.

After running this, upload the updated manual_events.csv to GitHub the same
way you've uploaded other files (Add file -> Upload files), replacing the
old version. It only needs updating when you add a new event.

Usage:
    python3 add_manual_event.py
"""
import csv
from pathlib import Path

CSV_FILE = Path(__file__).parent / "manual_events.csv"
FIELDS = ["venue", "event_name", "date", "url"]


def prompt_date() -> str:
    while True:
        raw = input("Date (YYYY-MM-DD, e.g. 2026-10-15): ").strip()
        parts = raw.split("-")
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            y, m, d = parts
            if len(y) == 4 and 1 <= int(m) <= 12 and 1 <= int(d) <= 31:
                return raw
        print("  That doesn't look like YYYY-MM-DD — try again, e.g. 2026-10-15.")


def main() -> None:
    print("Add a manual event (one not produced by any scraper)\n")
    venue = input("Venue name (e.g. Holloway): ").strip()
    event_name = input("Event name: ").strip()
    date = prompt_date()
    url = input("Ticket / info URL (optional, press Enter to skip): ").strip()

    is_new = not CSV_FILE.exists()
    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow({
            "venue": venue,
            "event_name": event_name,
            "date": date,
            "url": url,
        })

    print(f"\n✓  Added: {date} — {venue} — {event_name}")
    print(f"Saved to {CSV_FILE}")
    print("Now upload manual_events.csv to GitHub for it to take effect.")


if __name__ == "__main__":
    main()
