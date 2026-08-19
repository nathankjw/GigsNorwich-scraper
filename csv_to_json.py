#!/usr/bin/env python3
"""
Converts scraped_data/norwich_gigs.csv into scraped_data/norwich_gigs.json
for the blog widget to fetch. Run after scrape_headless.py.
"""
import csv
import json

with open("scraped_data/norwich_gigs.csv", newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

with open("scraped_data/norwich_gigs.json", "w", encoding="utf-8") as f:
    json.dump(rows, f, ensure_ascii=False, indent=2)

print(f"Wrote {len(rows)} events to norwich_gigs.json")
