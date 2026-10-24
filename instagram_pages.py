#!/usr/bin/env python3
"""
Splits the upcoming gigs into the Instagram posting windows, then into pages
of 11 events, ready to copy into Canva.

Windows each month: 1st-7th, 8th-15th, 16th-23rd, 24th-end of month.
Each window is cut into pages of PAGE_SIZE events (the last page may be shorter).

Reads : scraped_data/norwich_gigs.json
Writes: scraped_data/instagram/<window>_pageNofM.csv - one CSV file per Instagram page,
        in the same format as norwich_gigs_flat.csv (no header): Oct 24th,Venue,Event
        e.g. 2026-10_24-31_page1of6.csv. Old files in that folder are cleared each run.

Usage : python3 instagram_pages.py [--months 2] [--size 11] [--today YYYY-MM-DD]
        --months N : this month plus the following months, N in total (default 2).
                     Windows that have already finished are left out.
"""
import argparse
import calendar
import csv
import json
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

EVENTS_JSON = Path("scraped_data/norwich_gigs.json")
OUT_DIR = Path("scraped_data/instagram")

MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]  # as in the flat CSV
MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]
WINDOWS = [(1, 7), (8, 15), (16, 23), (24, None)]  # None = last day of the month
SKIP = re.compile(r"postponed|cancel+ed|rescheduled", re.I)


def ordinal(n: int) -> str:
    if 11 <= n <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def clean_name(raw: str) -> str:
    name = re.sub(r"\(SC:https?://[^)]+\)\s*$", "", raw.strip(), flags=re.I)
    return re.sub(r"\(X\)\s*$", "", name, flags=re.I).strip()


def add_months(y: int, m: int, n: int):
    idx = y * 12 + (m - 1) + n
    return idx // 12, idx % 12 + 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=2)
    ap.add_argument("--size", type=int, default=11)
    ap.add_argument("--today")
    a = ap.parse_args()
    today = date.fromisoformat(a.today) if a.today else datetime.now(ZoneInfo("Europe/London")).date()

    events = json.loads(EVENTS_JSON.read_text(encoding="utf-8"))
    events = [e for e in events if re.fullmatch(r"\d{4}-\d{2}-\d{2}", e.get("date", ""))]
    events.sort(key=lambda e: e["date"])  # stable: same-day gigs keep their file order

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUT_DIR.glob("*.csv"):
        old.unlink()  # stale pages from earlier runs
    skipped, written = [], 0
    for k in range(a.months):
        y, m = add_months(today.year, today.month, k)
        last = calendar.monthrange(y, m)[1]
        for start, end in WINDOWS:
            end = min(end or last, last)
            if start > last or date(y, m, end) < today:
                continue  # window doesn't exist this month, or is already over
            lo, hi = date(y, m, start).isoformat(), date(y, m, end).isoformat()
            inwin = [e for e in events if max(lo, today.isoformat()) <= e["date"] <= hi]
            keep = [e for e in inwin if not SKIP.search(e.get("event_name", ""))]
            skipped += [e for e in inwin if SKIP.search(e.get("event_name", ""))]
            pages = [keep[i:i + a.size] for i in range(0, len(keep), a.size)]
            for p, chunk in enumerate(pages, 1):
                path = OUT_DIR / f"{y}-{m:02d}_{start:02d}-{end:02d}_page{p}of{len(pages)}.csv"
                with open(path, "w", newline="", encoding="utf-8") as f:
                    w = csv.writer(f, lineterminator="\n")
                    for e in chunk:
                        _, mm, dd = (int(x) for x in e["date"].split("-"))
                        w.writerow([f"{MONTHS_SHORT[mm - 1]} {ordinal(dd)}", e.get("venue", ""),
                                    clean_name(e.get("event_name", ""))])
                written += 1
    if skipped:
        with open(OUT_DIR / "_left_out_check_by_hand.txt", "w", encoding="utf-8") as f:
            f.write("Postponed / cancelled / rescheduled - not in the pages, check by hand:\n")
            for e in skipped:
                f.write(f"{e['date']} | {e.get('venue', '')} | {e.get('event_name', '')}\n")
    print(f"Wrote {written} page file(s) to {OUT_DIR}/ ({len(skipped)} gig(s) left out)")


if __name__ == "__main__":
    main()
