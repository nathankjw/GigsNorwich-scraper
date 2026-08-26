#!/usr/bin/env python3
"""
Converts scraped_data/norwich_gigs.csv into scraped_data/norwich_gigs.json
for the blog widget to fetch.

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
OUTPUT_JSON = Path("scraped_data/norwich_gigs.json")


def load_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main() -> None:
    events = load_csv(SCRAPED_CSV)
    events.sort(key=lambda e: e.get("date", ""))

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)

    print(f"Wrote {len(events)} event(s) to {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
