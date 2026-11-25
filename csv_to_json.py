#!/usr/bin/env python3
"""
Converts scraped_data/norwich_gigs.csv into scraped_data/norwich_gigs.json
for the blog widget to fetch.

Extra gig details found by event_details.py (start / door / end times, age
restriction, a short description, support acts) are merged in from
scraped_data/event_details.json, matched on the gig's link. Gigs without
details simply don't get those fields.

Note: manual_events.csv (events added by hand via add_manual_event.py) is
now merged in by scrape_headless.py itself, before norwich_gigs.csv is
written — so norwich_gigs.csv already contains manual events, and this
script no longer needs to (and must not) merge manual_events.csv in again,
or every manual event ends up duplicated in the JSON.
"""
import csv
import json
from pathlib import Path

SCRAPED_CSV = Path("scraped_data/norwich_gigs.csv")
DETAILS_JSON = Path("scraped_data/event_details.json")
OUTPUT_JSON = Path("scraped_data/norwich_gigs.json")
DETAIL_FIELDS = ("time", "doors", "end", "age", "description", "support")


def load_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_details(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("events", {})
    except Exception:
        return {}   # no details file yet (or it's broken) - just skip the extras


def main() -> None:
    events = load_csv(SCRAPED_CSV)
    events.sort(key=lambda e: e.get("date", ""))

    details = load_details(DETAILS_JSON)
    enriched = 0
    for e in events:
        extra = details.get((e.get("url") or "").strip()) or {}
        added = False
        for key in DETAIL_FIELDS:
            if extra.get(key):
                e[key] = extra[key]
                added = True
        enriched += added

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)

    print(f"Wrote {len(events)} event(s) to {OUTPUT_JSON} "
          f"({enriched} with extra details from event pages)")


if __name__ == "__main__":
    main()
