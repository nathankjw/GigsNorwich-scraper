#!/usr/bin/env python3
"""
Norwich Gigs - extra gig details

Visits each upcoming gig's own web page and pulls out the extra details
the planned gig pages need:

    time         start / on-stage time     "19:30"   (24-hour, Europe/London)
    doors        doors-open time           "19:00"
    end          finish time               "23:00"
    age          age restriction           "14+", "18+", "All ages"
    description  first sentence or two of the gig write-up (short excerpt)
    support      support acts              ["Niche", "Godkill"]

Nothing here is venue-specific. It reads, in order of preference:
  1. schema.org Event data in the page (<script type="application/ld+json">)
  2. labelled text on the page: "Doors 7pm", "Doors time: 19:30",
     "Open from 7pm", "Starts 8pm", "Age restrictions: 18+", "Support from ..."
  3. looser patterns: "at 11:00pm-3:00am", a line that's just "7:00 PM 11:00 PM",
     a date line like "Thu 26 November 2026 7:30pm"
Anything it can't find is simply left out.

Results are cached per link in scraped_data/event_details.json so pages
aren't downloaded every day (see needs_fetch for the refresh rules).
csv_to_json.py merges them into scraped_data/norwich_gigs.json.

Usage (from the repo root):
    python3 event_details.py                 # normal daily run
    python3 event_details.py --url URL       # test one page, print what's found
    python3 event_details.py --limit 20      # cap pages fetched this run
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:  # the workflow installs these; only matters locally
    requests = None
    BeautifulSoup = None

ROOT = Path(__file__).resolve().parent
EVENTS_CSV = ROOT / "scraped_data" / "norwich_gigs.csv"
DETAILS_FILE = ROOT / "scraped_data" / "event_details.json"
SITE_CONFIG = ROOT / "blogger" / "site_config.json"

FIELDS = ("time", "doors", "end", "age", "description", "support")

# ── Refresh rules ─────────────────────────────────────────────────────────────
REFRESH_DAYS = 10          # re-check every page this often
SOON_DAYS = 4              # ...but gigs this close get re-checked more often,
SOON_REFRESH_DAYS = 2      #    because venues often add times late
MAX_FETCHES = 350          # per run, nearest gigs first
PER_HOST_DELAY = 0.6       # seconds between requests to the same website
MAX_WORKERS = 6            # websites fetched in parallel (one thread per site)
DESCRIPTION_MAX = 280      # characters kept from a write-up

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}

# Sites whose text isn't a write-up of the gig itself (Norfolk Gig Guide
# pages are band profiles, not the Brickmakers gig), so no description is
# taken from them. Times / age / support are still read.
NO_DESCRIPTION_HOSTS = ("norfolkgigguide.com",)

# Sites whose hidden Event data (JSON-LD) carries the wrong description, so
# the write-up on the page itself is used first. Norwich Arts Centre's has
# their "NAC Supporters - save £1.50..." membership blurb.
PAGE_TEXT_FIRST_HOSTS = ("norwichartscentre.co.uk",)

# Links that aren't a single gig's page (venue home / listings pages, social
# media). Venue listing links from blogger/site_config.json are added at run time.
SKIP_HOSTS = ("instagram.com", "facebook.com", "tiktok.com", "x.com", "twitter.com")
SKIP_URLS = {
    "https://thehollowaynorwich.com/events",
    "https://norwichartscentre.co.uk/event/category/music/",
    "https://www.ueaticketbookings.co.uk/uea-lcr/",
    "https://www.ueaticketbookings.co.uk/adrian-flux-waterfront/",
    "https://www.voodoodaddysshowroom.co.uk/events/",
    "https://www.deadwaxnorwich.pub/whats-on",
    "https://epic-tv.com/events",
}


# ═══════════════════════════════════════════════════════════════════════════════
# TIMES
# ═══════════════════════════════════════════════════════════════════════════════

# "7pm", "7 pm", "7.30pm", "7:30 p.m.", "19:30", "19.30". A bare number with no
# minutes and no am/pm ("7") is never treated as a time. Lookarounds stop it
# matching prices (£7.50), dates (26.11.2026) or bits of longer numbers.
_TIME = (r"(?<![\d£$€:./])(\d{1,2})(?:[:.]([0-5]\d))?\s*([ap])\.?\s?m\b\.?"
         r"|(?<![\d£$€:./])(\d{1,2})[:.]([0-5]\d)(?![\d.:/])")
TIME_RE = re.compile(_TIME, re.I)

MONTH_WORDS = (r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
               r"aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?")


def _to_hhmm(h: int, m: int, ap: str | None) -> str | None:
    if ap:
        ap = ap.lower()
        if not 1 <= h <= 12:
            return None
        if ap == "p" and h != 12:
            h += 12
        elif ap == "a" and h == 12:
            h = 0
    else:
        if h > 23:
            return None
        # "7:30" with no am/pm at a gig means the evening. 10/11 are left alone
        # because daytime events (brunch parties, matinees) do use them.
        if 1 <= h <= 9:
            h += 12
    return f"{h:02d}:{m:02d}"


def find_times(text: str) -> list[tuple[str, int, int]]:
    """All times in text, as (HH:MM, start index, end index)."""
    found = []
    for mt in TIME_RE.finditer(text):
        if mt.group(1) is not None:
            hhmm = _to_hhmm(int(mt.group(1)), int(mt.group(2) or 0), mt.group(3))
        else:
            hhmm = _to_hhmm(int(mt.group(4)), int(mt.group(5)), None)
        if hhmm:
            found.append((hhmm, mt.start(), mt.end()))
    return found


def first_time(text: str) -> str | None:
    t = find_times(text)
    return t[0][0] if t else None


# Labels that come just before a time. The time must follow within a few
# characters ("Doors: 7pm", "Doors open at 19:30", "Doors time\n19:30").
DOORS_LABEL = re.compile(
    r"\bdoors?\b(?:\s+(?:open(?:s|ing)?|times?))?(?:\s+time)?|\bopen(?:s|ing)?\s+(?:from|at)\b",
    re.I)
START_LABEL = re.compile(
    r"\b(?:start(?:s|ing)?(?:\s+time)?|show\s*(?:starts?|time)|on\s*stage|stage\s*times?|"
    r"music\s*(?:from|starts?)|performance\s*(?:starts?|time)|curtain\s*up|"
    r"(?:first|live)\s+act\s*(?:on|from)?|event\s*time|time)\b",
    re.I)
END_LABEL = re.compile(r"\b(?:ends?|finish(?:es)?|curfew|close|closing|until|till)\b", re.I)
_LABEL_GAP = re.compile(r"^\s*(?:[:\-–—|]|at|from|is)?\s*(?:[:\-–—|]|at|from)?\s*", re.I)

# "7pm - 11pm", "8pm – end", "11:00pm-3:00am", "7pm till late"
RANGE_RE = re.compile(
    rf"(?:{_TIME})\s*(?:-|–|—|to|till|until)\s*(?:(?:{_TIME})|(late|close|end|curfew))", re.I)


# Opening hours / box office hours, e.g. "Mon-Fri 10am - 4pm", "Open daily 12pm".
HOURS_LINE = re.compile(
    r"\b(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?\s*(?:-|–|—|to)\s*(?:mon|tue|wed|thu|fri|sat|sun)"
    r"|\b(?:daily|weekdays?|weekends?|opening\s+(?:hours|times)|box\s+office|reception|"
    r"call\s+us|phone|lines\s+open|kitchen|food\s+served|brunch\s+served)\b",
    re.I)


def time_after_label(label_re: re.Pattern, text: str, window: int = 14) -> str | None:
    for mt in label_re.finditer(text):
        rest = text[mt.end():]
        gap = _LABEL_GAP.match(rest)
        rest = rest[gap.end() if gap else 0:]
        times = find_times(rest[:window + 12])
        if times and times[0][1] <= window:
            return times[0][0]
    return None


def text_windows(lines: list[str]) -> list[str]:
    """Each line, joined to the next one when it's a short label on its own
    ("Doors" / "19:30" in separate table cells end up on separate lines)."""
    out = []
    for i, line in enumerate(lines):
        if len(line) <= 30 and i + 1 < len(lines):
            out.append(f"{line} {lines[i + 1]}")
        else:
            out.append(line)
    return out


def extract_times(lines: list[str]) -> dict:
    windows = text_windows(lines)
    res: dict[str, str] = {}

    for w in windows:
        if "doors" not in res:
            t = time_after_label(DOORS_LABEL, w)
            if t:
                res["doors"] = t
        if "time" not in res:
            # Don't let "Doors time 19:30" count as the start time too.
            cleaned = DOORS_LABEL.sub(" ", w)
            cleaned = re.sub(r"\blast\s+entry\b.*", " ", cleaned, flags=re.I)
            t = time_after_label(START_LABEL, cleaned)
            if t:
                res["time"] = t
        if "end" not in res:
            t = time_after_label(END_LABEL, w, window=6)
            if t:
                res["end"] = t

    # Ranges like "at 11:00pm-3:00am" or "8pm – end"
    if "time" not in res or "end" not in res:
        for line in lines:
            if len(line) > 160 or re.search(r"\b(?:doors|last\s+entry)\b", line, re.I) or HOURS_LINE.search(line):
                continue
            mt = RANGE_RE.search(line)
            if not mt:
                continue
            times = find_times(mt.group(0))
            if not times:
                continue
            res.setdefault("time", times[0][0])
            if len(times) > 1:
                res.setdefault("end", times[1][0])
            break

    # A short line that is nothing but a time or two: "7:00 pm", "7:00 PM 11:00 PM"
    if "time" not in res:
        for i, line in enumerate(lines):
            if len(line) > 30 or HOURS_LINE.search(line):
                continue
            # skip the value under a "Doors" / "Curfew" / "Last entry" label
            prev = lines[i - 1] if i else ""
            if len(prev) <= 30 and (DOORS_LABEL.search(prev) or END_LABEL.search(prev)
                                    or re.search(r"last\s+entry", prev, re.I)):
                continue
            times = find_times(line)
            leftover = TIME_RE.sub("", line)
            if times and not re.search(r"[a-z]{2,}", leftover, re.I):
                res["time"] = times[0][0]
                if len(times) > 1:
                    res.setdefault("end", times[1][0])
                break

    # A date line with a time on it: "Thu 26 November 2026 7:30pm"
    if "time" not in res:
        for line in lines:
            if len(line) <= 60 and re.search(rf"\b(?:{MONTH_WORDS})\b", line, re.I) and not HOURS_LINE.search(line):
                t = first_time(line)
                if t:
                    res["time"] = t
                    break

    # Sense checks: the start is at or after doors (within 5 hours), and the
    # end is after the start (within 12 hours, allowing for past midnight).
    if res.get("doors") and res.get("time"):
        if not 0 <= _minutes_between(res["doors"], res["time"]) <= 5 * 60:
            res.pop("time")
    if res.get("end"):
        start = res.get("time") or res.get("doors")
        if start and not 0 < _minutes_between(start, res["end"]) <= 12 * 60:
            res.pop("end")
    return res


def _minutes_between(a: str, b: str) -> int:
    """Minutes from a to b on a gig night (b may be after midnight)."""
    ma = int(a[:2]) * 60 + int(a[3:])
    mb = int(b[:2]) * 60 + int(b[3:])
    diff = mb - ma
    if diff < 0 and mb < 6 * 60:       # e.g. 23:00 -> 03:00
        diff += 24 * 60
    return diff


# ═══════════════════════════════════════════════════════════════════════════════
# AGE, SUPPORT, DESCRIPTION
# ═══════════════════════════════════════════════════════════════════════════════

_AGE_NUM = r"(1[2-8]|21)"
AGE_PATTERNS = [
    re.compile(rf"(?<![£$€\d.]){_AGE_NUM}\s*\+(?!\s*\d)", re.I),        # 14+, 18 +
    re.compile(rf"\b(?:over|aged|ages)\s*{_AGE_NUM}'?s?\b(?!\s*(?:get|go|free|half))", re.I),  # over 18s
    re.compile(rf"\b{_AGE_NUM}'?s?\s*(?:and|&)\s*over\b", re.I),             # 18s and over
    re.compile(rf"\bno\s+(?:one\s+)?under\s*{_AGE_NUM}'?s?\b", re.I),        # no under 18s
    re.compile(rf"\bstrictly\s*{_AGE_NUM}\b", re.I),                         # strictly 18
]
ALL_AGES_RE = re.compile(r"\ball\s+ages\b", re.I)
AGE_CONTEXT_SKIP = re.compile(r"\b(?:free\s+for|discount|concession|tickets?\s+for)\b", re.I)


def extract_age(lines: list[str]) -> str | None:
    for line in lines:
        if len(line) > 300 or AGE_CONTEXT_SKIP.search(line):
            continue
        for pat in AGE_PATTERNS:
            mt = pat.search(line)
            if mt:
                return f"{mt.group(1)}+"
        if ALL_AGES_RE.search(line):
            return "All ages"
    return None


# Only at the START of a short line ("Support from X", "Plus special guests: X",
# "+ X"), so a biography mentioning "supporting the likes of Neil Young..."
# isn't mistaken for tonight's line-up.
SUPPORT_RE = re.compile(
    r"^\W{0,3}(?:(?:plus|with|and|\+)\s+)?(?:very\s+)?"
    r"(?:special\s+guests?|guests?|support(?:\s+acts?)?(?:\s+(?:comes?|is|are|will\s+be))?(?:\s+(?:from|by))?)"
    r"\s*[:\-–—]?\s*(?P<acts>[^.\n!?]{2,140})",
    re.I)
# A line that is just "+ Wood Burnt Red" under the headliner (UEA venues).
PLUS_LINE_RE = re.compile(r"^\s*\+\s*(?P<acts>[^.\n!?]{2,100})$")
SUPPORT_LINE_SKIP = re.compile(
    r"\b(?:council|lottery|funding|funded|sponsor|donat|thank|charity|"
    r"support\s+(?:us|our|local|independent|the\s+venue|this)|your\s+support|"
    r"wheelchair|access|accessib|hearing|assistance|carer)",
    re.I)
SUPPORT_STOP = {"tba", "tbc", "tbd", "guests", "guest", "special guests", "more", "others",
                "very special guests", "to be announced", "to be confirmed", "local bands",
                "and more", "more tba", "friends", "support", "plus"}


def extract_support(lines: list[str]) -> list[str]:
    for line in lines:
        if len(line) > 160 or SUPPORT_LINE_SKIP.search(line):
            continue
        mt = SUPPORT_RE.search(line) or PLUS_LINE_RE.match(line)
        if not mt:
            continue
        raw = re.split(r"\s+[-–—|]\s+|\(|\bdoors?\b|\btickets?\b|\bonly\b", mt.group("acts"), flags=re.I)[0]
        parts = re.split(r",|\s\+\s|\s&\s|\s+and\s+|\s/\s|;", raw)
        acts = []
        for p in parts:
            p = p.strip(" :-–—'\"“”‘’*")
            if not p or len(p) > 45 or p.lower() in SUPPORT_STOP:
                continue
            if find_times(p):
                continue
            acts.append(p)
        if acts:
            return acts[:6]
    return []


BOILERPLATE_RE = re.compile(
    r"cookie|javascript|newsletter|sign\s*up|subscribe|privacy|terms\s*(?:&|and)\s*conditions|"
    r"all\s+rights\s+reserved|©|book\s+tickets\s+online|tickets?\s+(?:are\s+)?(?:now\s+)?(?:on\s+sale|available)\s+(?:from|at|online)|"
    r"please\s+note|refund|booking\s+fee|box\s+office|follow\s+us|share\s+this|"
    r"click\s+here|add\s+to\s+(?:basket|cart|calendar)|"
    # membership / offer blurbs
    r"per\s+ticket|join\s+(?:us\s+)?today|support\s+us|supporters?\b|members?\s+(?:save|get)|"
    r"\bsave\s+(?:between|up\s+to|£)|\d+\s*%\s+off|under\s+2[0-9]s|gift\s+vouchers?",
    re.I)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])[\"”’']?\s+(?=[A-Z0-9\"“‘'(])")


def clean_text(s: str) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)       # JSON-LD descriptions sometimes hold HTML
    return re.sub(r"\s+", " ", s).strip()


def useful_description(s: str) -> bool:
    if len(s) < 50 or BOILERPLATE_RE.search(s[:200]):
        return False
    letters = sum(c.isalpha() for c in s)
    if letters < len(s) * 0.6:            # mostly numbers / dates / prices
        return False
    return len(s.split()) >= 8


def shorten_description(s: str, limit: int = DESCRIPTION_MAX) -> str:
    """First sentence or two, at most `limit` characters - a short excerpt,
    not the venue's full write-up."""
    s = clean_text(s)
    out = ""
    for sentence in SENTENCE_SPLIT.split(s):
        candidate = f"{out} {sentence}".strip()
        if len(candidate) > limit:
            break
        out = candidate
        if len(out) >= 120:
            break
    if not out:
        cut = s[:limit].rsplit(" ", 1)[0].rstrip(",;:-–— ")
        out = cut + "…"
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE PARSING
# ═══════════════════════════════════════════════════════════════════════════════

# Class / id names of page furniture to drop. "header" is deliberately not
# here: many venues put the gig's date and time in an "event-header" block.
CHROME_HINT = re.compile(r"cookie|consent|newsletter|site-footer|\bfooter\b|navbar|"
                         r"\bnav\b|\bmenu\b|sidebar|breadcrumb|related|share|social|modal|popup|"
                         r"upcoming|other-events|more-events|also-like|may-like|contact|newsletter|opening-hours",
                         re.I)


def _iter_ld_nodes(data):
    if isinstance(data, list):
        for item in data:
            yield from _iter_ld_nodes(item)
    elif isinstance(data, dict):
        yield data
        for key in ("@graph", "subEvent", "event"):
            if key in data:
                yield from _iter_ld_nodes(data[key])


def _is_event(node: dict) -> bool:
    t = node.get("@type")
    types = t if isinstance(t, list) else [t]
    return any(isinstance(x, str) and x.endswith("Event") for x in types)


def _ld_time(value) -> str | None:
    """'2026-11-26T19:30:00+00:00' -> '19:30'. Midnight is treated as 'no time
    given' because many sites put T00:00 on date-only events."""
    if not isinstance(value, str):
        return None
    mt = re.search(r"T(\d{2}):(\d{2})", value)
    if mt:
        hhmm = f"{mt.group(1)}:{mt.group(2)}"
        return None if hhmm == "00:00" else hhmm
    return first_time(value)


def _names(value) -> list[str]:
    if isinstance(value, dict):
        value = [value]
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        return []
    out = []
    for v in value:
        name = v.get("name") if isinstance(v, dict) else v if isinstance(v, str) else None
        if name and clean_text(name):
            out.append(clean_text(name))
    return out


def details_from_jsonld(soup) -> dict:
    res: dict = {}
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except Exception:
            continue
        for node in _iter_ld_nodes(data):
            if not _is_event(node):
                continue
            t = _ld_time(node.get("startDate"))
            if t:
                res.setdefault("time", t)
            t = _ld_time(node.get("doorTime"))
            if t:
                res.setdefault("doors", t)
            t = _ld_time(node.get("endDate"))
            if t:
                res.setdefault("end", t)
            age = node.get("typicalAgeRange")
            if isinstance(age, str):
                mt = re.match(r"\s*(\d{1,2})\s*(?:-|\+|$)", age)
                if mt and 12 <= int(mt.group(1)) <= 21:
                    res.setdefault("age", f"{int(mt.group(1))}+")
                elif re.search(r"all", age, re.I):
                    res.setdefault("age", "All ages")
            desc = clean_text(node.get("description") or "")
            if desc and useful_description(desc):
                res.setdefault("description", desc)
            performers = _names(node.get("performer"))
            if len(performers) > 1:
                res.setdefault("support", performers[1:7])
            if res:
                return res
    return res


def main_content(soup):
    """The page's main content, with header / footer / menus / cookie banners
    removed, so opening hours or a site-wide 18+ notice in the footer aren't
    mistaken for this gig's details."""
    for tag in soup(["script", "style", "noscript", "template", "svg", "iframe",
                     "nav", "aside", "form", "button"]):
        tag.decompose()
    # Site-wide header/footer only; a <header> inside the article is the gig's own.
    for tag in soup(["header", "footer"]):
        if not tag.find_parent(["main", "article"]):
            tag.decompose()
    for tag in soup.find_all(True):
        if getattr(tag, "decomposed", False) or tag.attrs is None:
            continue
        if tag.name in ("html", "body", "main", "article"):
            continue
        ident = " ".join(tag.get("class", []) or []) + " " + (tag.get("id") or "")
        if ident.strip() and CHROME_HINT.search(ident):
            tag.decompose()
    return soup.find("main") or soup.find("article") or soup.find(attrs={"role": "main"}) or soup.body or soup


def page_lines(root) -> list[str]:
    """Visible text, one entry per line. A line that is only a dash is joined
    to its neighbours, so "7:00 PM" / "–" / "11:00 PM" becomes one line."""
    lines: list[str] = []
    join_next = False
    for raw in root.get_text("\n").split("\n"):
        line = " ".join(raw.split())
        if not line:
            continue
        if line in ("-", "–", "—", "to", "until", "till") and lines:
            lines[-1] = f"{lines[-1]} {line}"
            join_next = True
            continue
        if join_next:
            lines[-1] = f"{lines[-1]} {line}"
            join_next = False
        else:
            lines.append(line)
    return lines


def description_from_paragraphs(root) -> str | None:
    for p in root.find_all(["p", "div"]):
        if p.name == "div" and p.find(["p", "div"]):
            continue        # only leaf blocks, so we don't grab whole sections
        text = clean_text(p.get_text(" "))
        if useful_description(text) and len(text) >= 70:
            return text
    return None


def description_from_meta(soup) -> str | None:
    for attrs in ({"property": "og:description"}, {"name": "description"},
                  {"name": "twitter:description"}):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            text = clean_text(tag["content"])
            if useful_description(text):
                return text
    return None


def _host_in(url: str, hosts) -> bool:
    netloc = urlparse(url or "").netloc.lower()
    return any(netloc == h or netloc.endswith("." + h) for h in hosts)


def extract_details(page_html: str, url: str = "") -> dict:
    """Everything we can find on one gig page. Missing fields are left out."""
    soup = BeautifulSoup(page_html, "lxml")
    res = details_from_jsonld(soup)
    meta_desc = description_from_meta(soup)
    if _host_in(url, PAGE_TEXT_FIRST_HOSTS):
        res.pop("description", None)      # filled from the page text below

    root = main_content(soup)
    lines = page_lines(root)

    for key, val in extract_times(lines).items():
        res.setdefault(key, val)
    if "age" not in res:
        age = extract_age(lines)
        if age:
            res["age"] = age
    if "support" not in res:
        support = extract_support(lines)
        if support:
            res["support"] = support
    if "description" not in res:
        desc = description_from_paragraphs(root) or meta_desc
        if desc:
            res["description"] = desc
    if res.get("description"):
        res["description"] = shorten_description(res["description"])
    if _host_in(url, NO_DESCRIPTION_HOSTS):
        res.pop("description", None)
    return {k: res[k] for k in FIELDS if res.get(k)}


# ═══════════════════════════════════════════════════════════════════════════════
# FETCHING + CACHE
# ═══════════════════════════════════════════════════════════════════════════════

def load_skip_urls() -> set[str]:
    urls = set(SKIP_URLS)
    try:
        cfg = json.loads(SITE_CONFIG.read_text(encoding="utf-8"))
        urls.update(cfg.get("VENUE_LINKS", {}).values())
    except Exception:
        pass
    return {u.rstrip("/") for u in urls}


def is_gig_page(url: str, skip: set[str]) -> bool:
    if not url.startswith("http"):
        return False
    parsed = urlparse(url)
    if parsed.path in ("", "/"):
        return False
    if any(parsed.netloc == h or parsed.netloc.endswith("." + h) for h in SKIP_HOSTS):
        return False
    return url.rstrip("/") not in skip


def load_cache() -> dict:
    try:
        data = json.loads(DETAILS_FILE.read_text(encoding="utf-8"))
        return data.get("events", {}) if isinstance(data, dict) else {}
    except Exception:
        return {}


def clean_cache(cache: dict) -> dict:
    """Apply today's rules to details saved on earlier runs: drop descriptions
    from sites we no longer take them from, and drop any that now count as
    boilerplate - those pages are re-fetched this run to look again."""
    cleared = 0
    for url, entry in cache.items():
        desc = entry.get("description")
        if not desc:
            continue
        if _host_in(url, NO_DESCRIPTION_HOSTS):
            entry.pop("description")
            cleared += 1
        elif not useful_description(desc):
            entry.pop("description")
            entry["checked"] = ""          # forces a re-fetch
            cleared += 1
    if cleared:
        print(f"🧹  Cleared {cleared} saved description(s) that no longer pass the checks")
    return cache


def save_cache(cache: dict) -> None:
    DETAILS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
               "events": dict(sorted(cache.items()))}
    DETAILS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def needs_fetch(entry: dict | None, gig_date: str, today: date) -> bool:
    if not entry or not entry.get("checked"):
        return True
    try:
        checked = date.fromisoformat(entry["checked"])
        gig = date.fromisoformat(gig_date)
    except ValueError:
        return True
    age = (today - checked).days
    if entry.get("error"):            # site was down / blocked: try again tomorrow
        return age >= 1
    if (gig - today).days <= SOON_DAYS and not entry.get("time"):
        return age >= SOON_REFRESH_DAYS
    return age >= REFRESH_DAYS


def fetch_page(session, url: str) -> tuple[str | None, str | None]:
    try:
        resp = session.get(url, timeout=20)
    except Exception as e:
        return None, type(e).__name__
    if not resp.ok:
        return None, f"HTTP {resp.status_code}"
    ctype = resp.headers.get("Content-Type", "")
    if "html" not in ctype and "xml" not in ctype and ctype:
        return None, f"not HTML ({ctype.split(';')[0]})"
    return resp.text, None


def run(limit: int = MAX_FETCHES) -> int:
    if requests is None:
        print("requests / beautifulsoup4 not installed", file=sys.stderr)
        return 1
    if not EVENTS_CSV.exists():
        print(f"{EVENTS_CSV} not found - run the scraper first", file=sys.stderr)
        return 1

    today = date.today()
    with open(EVENTS_CSV, newline="", encoding="utf-8") as f:
        events = list(csv.DictReader(f))
    skip = load_skip_urls()
    cache = clean_cache(load_cache())

    # Upcoming gigs with their own page; one entry per link (several gigs can
    # share a link, e.g. a multi-day festival), keeping the nearest date.
    wanted: dict[str, dict] = {}
    for e in events:
        url = (e.get("url") or "").strip()
        if e.get("date", "") < today.isoformat() or not is_gig_page(url, skip):
            continue
        if url not in wanted or e["date"] < wanted[url]["date"]:
            wanted[url] = e

    todo = [u for u, e in wanted.items() if needs_fetch(cache.get(u), e["date"], today)]
    todo.sort(key=lambda u: (u in cache, wanted[u]["date"]))   # new links first, then soonest
    todo = todo[:limit]
    print(f"🔎  Gig details: {len(wanted)} gig page(s), {len(todo)} to fetch this run "
          f"({len(wanted) - len(todo)} up to date in the cache)")

    by_host: dict[str, list[str]] = defaultdict(list)
    for u in todo:
        by_host[urlparse(u).netloc].append(u)

    lock = threading.Lock()
    errors: dict[str, list[str]] = defaultdict(list)

    def work(host_urls: list[str]) -> None:
        session = requests.Session()
        session.headers.update(HEADERS)
        for i, url in enumerate(host_urls):
            if i:
                time.sleep(PER_HOST_DELAY)
            page, err = fetch_page(session, url)
            entry = {"checked": today.isoformat()}
            if page:
                try:
                    entry.update(extract_details(page, url))
                except Exception as e:      # a bad page must never stop the run
                    err = f"parse error: {e}"
            if err:
                entry["error"] = err
                old = cache.get(url) or {}
                # keep what we found last time if the site is just having a bad day
                entry.update({k: old[k] for k in FIELDS if old.get(k)})
            with lock:
                cache[url] = entry
                if err:
                    errors[wanted[url].get("venue", "?")].append(err)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        list(pool.map(work, by_host.values()))

    # Forget links that are no longer listed.
    cache = {u: v for u, v in cache.items() if u in wanted}
    save_cache(cache)
    report(wanted, cache, errors)
    return 0


def report(wanted: dict, cache: dict, errors: dict) -> None:
    """Per-venue coverage table, printed and added to the GitHub run summary."""
    stats: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    for url, ev in wanted.items():
        venue = ev.get("venue") or "?"
        entry = cache.get(url, {})
        s = stats[venue]
        s["gigs"] += 1
        for k in FIELDS:
            if entry.get(k):
                s[k] += 1
    header = "| Venue | Gig pages | Start | Doors | Age | Description | Support | Errors |"
    rows = [header, "|---|---:|---:|---:|---:|---:|---:|---|"]
    for venue, s in sorted(stats.items(), key=lambda x: -x[1]["gigs"]):
        errs = errors.get(venue, [])
        err_txt = f"{len(errs)} ({', '.join(sorted(set(errs))[:2])})" if errs else ""
        rows.append(f"| {venue} | {s['gigs']} | {s['time']} | {s['doors']} | {s['age']} | "
                    f"{s['description']} | {s['support']} | {err_txt} |")
    table = "\n".join(rows)
    print(table)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n### Gig details found on event pages\n\n" + table + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="fetch one page and print what was found (doesn't touch the cache)")
    ap.add_argument("--limit", type=int, default=MAX_FETCHES, help="max pages to fetch this run")
    args = ap.parse_args()
    if args.url:
        session = requests.Session()
        session.headers.update(HEADERS)
        page, err = fetch_page(session, args.url)
        if err:
            print(f"Couldn't fetch: {err}")
            return 1
        print(json.dumps(extract_details(page, args.url), ensure_ascii=False, indent=2))
        return 0
    return run(args.limit)


if __name__ == "__main__":
    sys.exit(main())
