#!/usr/bin/env python3
"""
Converts scraped_data/norwich_gigs.csv into scraped_data/norwich_gigs.json
for the blog widget to fetch, merging in anything from manual_events.csv
(events added by hand via add_manual_event.py) that haven't happened yet.

manual_events.csv is never modified here — only read — so events you've
added stay there indefinitely; they're just left out of the merged JSON
once their date has passed.
"""
import csv
import json
from datetime import date
from pathlib import Path

SCRAPED_CSV = Path("scraped_data/norwich_gigs.csv")
MANUAL_CSV = Path("manual_events.csv")
OUTPUT_JSON = Path("scraped_data/norwich_gigs.json")


def load_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main() -> None:
    scraped = load_csv(SCRAPED_CSV)
    manual = load_csv(MANUAL_CSV)

    today_str = date.today().isoformat()
    manual_upcoming = [e for e in manual if e.get("date", "") >= today_str]

    combined = scraped + manual_upcoming
    combined.sort(key=lambda e: e.get("date", ""))

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)

    print(
        f"Wrote {len(combined)} events "
        f"({len(scraped)} scraped + {len(manual_upcoming)} manual, "
        f"of {len(manual)} total manual) to {OUTPUT_JSON}"
    )


if __name__ == "__main__":
    main()
