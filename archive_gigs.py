#!/usr/bin/env python3
"""
Keep a permanent record of every gig the scraper has ever seen.

norwich_gigs.json only holds what venues are listing *now*, so gigs vanish
from it once they've happened. This script copies each day's listings into
scraped_data/gig_archive.json, which is never trimmed:

  * one entry per gig (date + venue + name), merged across days - if a venue
    tweaks a gig's name or drops its price after the night, the archive keeps
    the newest name and the last known price, times, support acts etc.
  * first_seen / last_seen: the first and last day the gig was listed
  * also_listed_as: earlier names, when a listing was swapped for a new one on
    the same night at the same venue (e.g. "Tribute Night" -> the band's name)
  * removed_early: true when the gig stopped being listed while the scraper was
    still seeing other gigs at that venue before the gig's date - usually a
    cancellation or postponement, sometimes a big rename (worth a quick check
    before quoting it)

Gigs with a date before today are the history; later ones are still upcoming.

Run daily by the workflow, straight after csv_to_json.py:
    python3 archive_gigs.py
One-off fill from the repo's git history (needs the full history checked out):
    python3 archive_gigs.py --from-git
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "scraped_data"
LISTINGS = DATA / "norwich_gigs.json"
ARCHIVE = DATA / "gig_archive.json"

# Fields kept for each gig, in the order they're written.
FIELDS = ("date", "venue", "event_name", "support", "time", "doors", "end", "price",
          "age", "description", "url", "image")


def norm(text: str) -> str:
    """Lower-case letters and digits only, for comparing names."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def same_gig(a: dict, b: dict, url_counts: dict) -> bool:
    """Two listings on the same date at the same venue: are they one gig?"""
    na, nb = norm(a.get("event_name")), norm(b.get("event_name"))
    if na and na == nb:
        return True
    # A link that belongs to just this one gig (not a venue's general what's-on
    # page shared by all its gigs) is a strong sign it's the same gig, renamed.
    ua, ub = a.get("url") or "", b.get("url") or ""
    if ua and ua == ub and url_counts.get(ua, 0) <= 1:
        return True
    if na and nb and (na in nb or nb in na) and min(len(na), len(nb)) >= 6:
        return True
    return bool(na and nb and SequenceMatcher(None, na, nb).ratio() >= 0.85)


class Archive:
    def __init__(self, path: Path = ARCHIVE):
        self.path = path
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.gigs: list[dict] = data.get("gigs", [])
        # scrape_days: {day: [venues with gigs that day]} - used for removed_early
        self.scrape_days: dict[str, list[str]] = data.get("scrape_days", {})
        self._index: dict[tuple, list[dict]] = {}
        for g in self.gigs:
            self._index.setdefault((g["date"], g["venue"]), []).append(g)

    def add_day(self, day: str, listings: list[dict]) -> tuple[int, int]:
        """Merge one day's listings in. Returns (new gigs, updated gigs)."""
        new = updated = 0
        url_counts: dict[str, int] = {}
        for ev in listings:
            if ev.get("url"):
                url_counts[ev["url"]] = url_counts.get(ev["url"], 0) + 1
        venues = set()
        for ev in listings:
            d, venue = (ev.get("date") or "").strip(), (ev.get("venue") or "").strip()
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) or not venue or not ev.get("event_name"):
                continue
            venues.add(venue)
            bucket = self._index.setdefault((d, venue), [])
            match = next((g for g in bucket if same_gig(g, ev, url_counts)), None)
            if match is None:
                gig = {k: ev.get(k, "") for k in FIELDS}
                gig.update(first_seen=day, last_seen=day)
                bucket.append(gig)
                self.gigs.append(gig)
                new += 1
                continue
            changed = False
            later = day >= match["last_seen"]
            for k in FIELDS:
                v = ev.get(k, "")
                # Newer listings win; older ones only fill gaps (e.g. a price
                # the venue removed after the night stays in the archive).
                if v and v != match.get(k) and (later or not match.get(k)):
                    match[k] = v
                    changed = True
            if day < match["first_seen"]:
                match["first_seen"], changed = day, True
            if day > match["last_seen"]:
                match["last_seen"], changed = day, True
            updated += changed
        if venues:
            self.scrape_days[day] = sorted(set(self.scrape_days.get(day, [])) | venues)
        return new, updated

    def merge_renames(self) -> None:
        """A listing that vanished, replaced on the very next scrape by a new one
        for the same night at the same venue, is the same gig renamed."""
        days = sorted(self.scrape_days)
        next_day = {d: days[i + 1] for i, d in enumerate(days[:-1])}
        for bucket in self._index.values():
            for old in sorted(bucket, key=lambda g: g["last_seen"]):
                if old not in bucket or old["last_seen"] >= old["date"]:
                    continue
                swap = next_day.get(old["last_seen"])
                new = [g for g in bucket if g is not old and g["first_seen"] == swap]
                if swap is None or len(new) != 1 or swap > old["date"]:
                    continue
                new = new[0]
                for k in FIELDS:
                    if not new.get(k) and old.get(k):
                        new[k] = old[k]
                names = new.setdefault("also_listed_as", [])
                for n in [old["event_name"], *old.get("also_listed_as", [])]:
                    if n not in names and n != new["event_name"]:
                        names.append(n)
                new["first_seen"] = min(new["first_seen"], old["first_seen"])
                bucket.remove(old)
                self.gigs.remove(old)

    def mark_removed_early(self) -> None:
        """Flag gigs that disappeared from a venue's listings before the date,
        while that venue was still being scraped successfully."""
        days = sorted(self.scrape_days)
        for g in self.gigs:
            later = [d for d in days if g["last_seen"] < d < g["date"]
                     and g["venue"] in self.scrape_days[d]]
            if later:
                g["removed_early"] = True
            else:
                g.pop("removed_early", None)

    def save(self) -> None:
        self.merge_renames()
        self.mark_removed_early()
        self.gigs.sort(key=lambda g: (g["date"], g["venue"].lower(), norm(g["event_name"])))
        today = date.today().isoformat()
        out = {
            "about": "Every gig the Norwich Gigs scraper has seen. Gigs dated before "
                     "'updated' have happened. See archive_gigs.py.",
            "updated": today,
            "total_gigs": len(self.gigs),
            "past_gigs": sum(g["date"] < today for g in self.gigs),
            "gigs": self.gigs,
            "scrape_days": dict(sorted(self.scrape_days.items())),
        }
        self.path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def load_listings(text: str) -> list[dict]:
    data = json.loads(text)
    return data if isinstance(data, list) else data.get("events", [])


def from_git(archive: Archive) -> None:
    """Replay every saved version of norwich_gigs.json, oldest first."""
    rel = LISTINGS.relative_to(HERE).as_posix()
    log = subprocess.run(["git", "log", "--reverse", "--format=%H %cI", "--", rel],
                         cwd=HERE, capture_output=True, text=True, check=True).stdout
    versions = [line.split() for line in log.splitlines() if line.strip()]
    if not versions:
        sys.exit("No history found - is this a full clone (not --depth 1)?")
    for sha, stamp in versions:
        day = datetime.fromisoformat(stamp).astimezone(timezone.utc).date().isoformat()
        try:
            listings = load_listings(subprocess.run(
                ["git", "show", f"{sha}:{rel}"], cwd=HERE, capture_output=True,
                text=True, check=True).stdout)
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            continue
        archive.add_day(day, listings)
    print(f"Replayed {len(versions)} saved versions from git history")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from-git", action="store_true", help="fill from the repo's git history first")
    args = ap.parse_args()
    archive = Archive()
    before = len(archive.gigs)
    if args.from_git:
        from_git(archive)
    if LISTINGS.exists():
        new, updated = archive.add_day(date.today().isoformat(), load_listings(LISTINGS.read_text(encoding="utf-8")))
        print(f"Today's listings: {new} new gig(s), {updated} updated")
    archive.save()
    today = date.today().isoformat()
    print(f"🗂️  Archive: {len(archive.gigs)} gigs ({len(archive.gigs) - before} added), "
          f"{sum(g['date'] < today for g in archive.gigs)} in the past -> {ARCHIVE.relative_to(HERE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
