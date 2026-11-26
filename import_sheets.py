#!/usr/bin/env python3
"""
One-off: add the gigs from Nathan's old hand-made Google Sheet ("SHEET 2") to
scraped_data/gig_archive.json.

Give it the .xlsx copies of the sheet's old versions (Google Sheets: Version
history -> Make a copy -> Download as .xlsx). The file names carry the date of
each version, e.g. "Copy of SHEET 2 - 30 January, 13_45.xlsx"; a name without a
year is this year, and plain "SHEET 2.xlsx" is today's version.

    python3 import_sheets.py path/to/*.xlsx            # add to the archive
    python3 import_sheets.py --dry-run path/to/*.xlsx  # just report

How the sheet is read:
  * each row is: date ("Oct 14th", "27th Mar"), venue, gig name, and sometimes a
    price (2025 tab) or a set time (the October 2025 festival line-up)
  * rows with no venue are day headings; a few later tabs have the date twice,
    which shifts the columns along by one - both are handled
  * the sheet has no years: each row gets the year that puts it between 4
    months before and 8 months after the first version it appears in (the
    sheet listed gigs coming up)
  * a sheet gig that lands in the future but isn't in the scraper's listings
    for that venue is moved back a year (old gigs copied in later)
  * the same gig in several tabs / versions is kept once (latest version wins);
    gigs the scraper also saw are merged, the sheet only filling in gaps
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, time
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from archive_gigs import Archive, norm, same_gig

MONTHS = {m: i for i, m in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1)}

# Sheet venue names -> the names the scraper uses.
VENUES = {
    "the lcr": "LCR", "lcr": "LCR",
    "madder market": "Maddermarket", "maddermarket": "Maddermarket",
    "epic studios": "Epic",
    "space studios": "Space",
    "gonzos tea room": "Gonzos",
    "the reindeer": "Reindeer",
    "st peter p.": "St Peter Parmentergate",
}

FILE_DATE = re.compile(r"(\d{1,2}) ([A-Za-z]+)(?: (\d{4}))?, (\d{1,2})_(\d{2})")


def version_date(path: Path, today: date) -> date:
    m = FILE_DATE.search(path.stem)
    if not m:
        return today                    # "SHEET 2.xlsx": the current version
    day, month, year = int(m.group(1)), MONTHS[m.group(2)[:3].lower()], m.group(3)
    return date(int(year) if year else today.year, month, day)


DAY_MONTH = re.compile(r"^\s*(?:(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)|([A-Za-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?)\s*$")


def parse_day(value) -> tuple[int, int] | None:
    """'Oct 14th' / 'September 1st' / '27th Mar' / a real date -> (month, day)."""
    if isinstance(value, (datetime, date)):
        return value.month, value.day
    m = DAY_MONTH.match(str(value or ""))
    if not m:
        return None
    day, month = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
    mon = MONTHS.get(month[:3].lower())
    return (mon, int(day)) if mon and 1 <= int(day) <= 31 else None


def with_year(month: int, day: int, seen: date) -> str | None:
    """Pick the year from the version the row first appeared in. The sheet listed
    gigs coming up, so look from 4 months before that version to 8 months after."""
    for y in (seen.year - 1, seen.year, seen.year + 1):
        try:
            d = date(y, month, day)
        except ValueError:
            continue
        months = (d.year - seen.year) * 12 + d.month - seen.month
        if -4 <= months < 8:
            return d.isoformat()
    return None


def price_text(v) -> str:
    if v in (None, ""):
        return ""
    if isinstance(v, (int, float)):
        return "Free" if v == 0 else f"£{v:.2f}".replace(".00", "")
    v = str(v).strip()
    return "" if v.lower() == "price" else v


def rows_of(path: Path):
    """(month, day, venue, name, extra) for every gig row in every tab."""
    wb = openpyxl.load_workbook(path, data_only=True)
    for ws in wb.worksheets:
        for r in ws.iter_rows(values_only=True):
            cells = list(r) + [None] * 6
            if parse_day(cells[1]):            # date written twice: shift along
                cells = cells[1:]
            if not parse_day(cells[0]) and cells[0] in (None, "") and parse_day(cells[1]):
                cells = cells[1:]
            md = parse_day(cells[0])
            venue = str(cells[1] or "").strip()
            name = re.sub(r"\s+", " ", str(cells[2] or "")).strip()
            if not md or not venue or not name or parse_day(venue):
                continue
            yield md, VENUES.get(venue.lower(), venue), name, cells[3]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--today", default=date.today().isoformat())
    args = ap.parse_args()
    today = date.fromisoformat(args.today)

    versions = sorted((version_date(p, today), p) for p in args.files)
    archive = Archive()
    # The scraper reads every venue's full listings, so a sheet gig dated in the
    # future that the scraper doesn't list was really last year's - copied in
    # later (the June 2026 tab has a backlog of Dec 2025 / Jan 2026 gigs).
    scraped_venues = {g["venue"] for g in archive.gigs if "last_seen" in g}
    upcoming = {(g["date"], g["venue"]) for g in archive.gigs if "last_seen" in g}
    first_seen: dict[tuple, date] = {}       # row identity -> first version date
    gigs: dict[tuple, list[dict]] = {}       # (date, venue) -> deduped sheet gigs
    for seen, path in versions:              # oldest first, so later fixes win
        for (mon, day), venue, name, extra in rows_of(path):
            key = (mon, day, venue.lower(), norm(name))
            first_seen.setdefault(key, seen)
            d = with_year(mon, day, first_seen[key])
            if not d:
                continue
            if d > today.isoformat() and venue in scraped_venues and (d, venue) not in upcoming:
                d = with_year(mon, day, date(first_seen[key].year - 1, first_seen[key].month, 1))
                if not d:
                    continue
            rec = {"date": d, "venue": venue, "event_name": name}
            if isinstance(extra, time):
                rec["time"] = extra.strftime("%H:%M")
            elif price_text(extra) and not re.search(r"[a-z]{4}", price_text(extra).lower()):
                rec["price"] = price_text(extra)
            elif isinstance(extra, str) and extra.strip():
                # A few tabs have a second act/name in the 4th column.
                rec["support"] = extra.strip()
            bucket = gigs.setdefault((d, venue), [])
            match = next((g for g in bucket if same_gig(g, rec, {})), None)
            if match:
                match.update({k: v for k, v in rec.items() if v})
            else:
                bucket.append(rec)

    records = [g for bucket in gigs.values() for g in bucket]
    print(f"Read {len(versions)} version(s): {len(records)} different gigs, "
          f"{min(g['date'] for g in records)} to {max(g['date'] for g in records)}")
    counts = {"new": 0, "merged": 0, "skipped": 0}
    for rec in sorted(records, key=lambda g: g["date"]):
        counts[archive.add_external(rec)] += 1
    print(f"Archive: {counts['new']} new gig(s) from the sheet, {counts['merged']} already "
          f"there (filled in / merged), {counts['skipped']} skipped")
    if args.dry_run:
        print("Dry run: archive not saved")
    else:
        archive.save()
        print(f"Saved: {len(archive.gigs)} gigs in the archive")
    return 0


if __name__ == "__main__":
    sys.exit(main())
