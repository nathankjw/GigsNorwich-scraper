#!/usr/bin/env python3
"""
Norwich Gigs — Headless Scraper (no GUI)

This is the "engine" from norwich_gigs_combined.py with the tkinter desktop
app stripped out. It runs every venue scraper in sequence and writes the
results straight to a CSV — no windows, no clicking. This is the version
meant to run automatically (e.g. on a schedule via GitHub Actions), while
norwich_gigs_combined.py remains the version you run by hand on your laptop.

If you fix or improve a scraper function, make the same change in both
files until they get merged into one properly.

Install deps (once):
    pip install requests beautifulsoup4 selenium webdriver-manager lxml python-dateutil

Usage:
    python3 scrape_headless.py
"""

import csv
import html
import os
import re
import subprocess
import sys
import threading
import time
from difflib import SequenceMatcher
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

# ── Optional scraping imports (checked at runtime) ───────────────────────────
try:
    import requests
    from bs4 import BeautifulSoup
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
    SELENIUM_OK = True
except ImportError:
    SELENIUM_OK = False

try:
    from dateutil.parser import parse as dateutil_parse
    DATEUTIL_OK = True
except ImportError:
    DATEUTIL_OK = False


# ═══════════════════════════════════════════════════════════════════════════════
# VERSION
# ═══════════════════════════════════════════════════════════════════════════════

APP_VERSION      = "1.2"
APP_VERSION_DATE = "2026-08-24"  # date this scraper build was last updated

# ═══════════════════════════════════════════════════════════════════════════════
# SHARED HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_ALIASES: dict[str, str] = {
    "Gonzo's Tea Room":        "Gonzos",
    "Gonzos Tea Room":         "Gonzos",
    "Voodoo Daddy's Showroom": "Voodoos",
    "The Holloway":            "Holloway",
    # Space — known name variants collapse to "Space"
    "Space Studios Norwich":   "Space",
    "Space Studios":           "Space",
    "Norwich Arts Centre":     "Arts Centre",
    "Nick Rayns LCR":          "LCR",
    # Waterfront — known name variants collapse to "Waterfront"
    "Waterfront Studio":       "Waterfront",
    "The Waterfront":          "Waterfront",
    "Norwich Waterfront":      "Waterfront",
    # Epic — known name variants collapse to "Epic"
    "Epic Studios":            "Epic",
    "Epic Studios Norwich":    "Epic",
    "The Brickmakers - Norwich": "Brickmakers",
    "Madder Market Theatre":     "Maddermarket",
    "The Halls":                 "The Halls",
    "The Hangar":                 "Hangar",
    "The Hangar Norwich":         "Hangar",
    "Revolución de Cuba Norwich": "Revolucion de Cuba",
    "Revolucion de Cuba Norwich": "Revolucion de Cuba",
}

# Default venue → ticket-link URL, used by the HTML Export tab to build
# "Get Tickets" buttons. Keys should match the short display names produced
# by DEFAULT_ALIASES above.
DEFAULT_VENUE_LINKS: dict[str, str] = {
    "Waterfront":   "https://www.ueaticketbookings.co.uk/adrian-flux-waterfront/",
    "Epic":         "https://epic-tv.com/events",
    "LCR":          "https://www.ueaticketbookings.co.uk/uea-lcr/",
    "Voodoos":      "https://www.voodoodaddysshowroom.co.uk/events/",
    "Gonzos":       "https://www.fatsoma.com/p/gonzostworoom",
    "Holloway":     "https://thehollowaynorwich.com/events",
    "Yalm":         "https://www.yalm.co.uk/events",
    "Arts Centre":  "https://norwichartscentre.co.uk/event/category/music/",
    "Brickmakers":  "https://brickmakersnorwich.co.uk/",
    "Space":        "https://www.spacestudiosnorwich.com/",
    "Last Pub":     "https://www.instagram.com/lastpubstanding/?hl=en",
    "HMV":          "https://www.instagram.com/hmv_norwich/?hl=en",
    "Dead Wax":     "https://www.deadwaxnorwich.pub/whats-on",
    "First Draft":  "https://www.firstdraftkitchen.com/",
    "Maddermarket": "https://booking.maddermarket.co.uk/Events",
    "The Halls":    "https://www.norwich.gov.uk/thehalls/whats",
    "Hangar":       "https://fixr.co/venue/the-hangar-norwich-26779",
    "Revolucion de Cuba": "https://www.revoluciondecuba.com/events/?location=Norwich&type=Live+Music",
}


def ordinal(n: int) -> str:
    if 11 <= n <= 13:
        return f"{n}th"
    return f"{n}{['th','st','nd','rd','th'][min(n % 10, 4)]}"


def format_date(date_str: str) -> str | None:
    try:
        dt = datetime.strptime(date_str.strip(), "%Y-%m-%d")
        return f"{dt.strftime('%b')} {ordinal(dt.day)}"
    except ValueError:
        return None


def shorten_venue(venue: str, aliases: dict) -> str:
    return aliases.get(venue.strip(), venue.strip())


# Phrases (case-insensitive) that flag an event for removal.
_FILTER_PHRASES = re.compile(
    r"\b(film\s+screening|screening|bingo|quiz|cribbage|private\s+event)\b",
    re.IGNORECASE,
)
# Norwich-area phone numbers: 01603 followed by digits (with optional spaces/hyphens)
_FILTER_PHONE = re.compile(r"01603[\s\-]?\d")


def _normalise_for_match(name: str) -> str:
    """Lowercase, strip punctuation, and collapse whitespace for fuzzy matching."""
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def dedupe_events(events: list[dict], threshold: float = 0.6) -> list[dict]:
    """
    Remove duplicate events that share the same venue + date and have a
    similar title. This is needed because some venues (e.g. Brickmakers)
    are scraped from more than one source, so the same gig can come
    through twice with slightly different title formatting (e.g.
    "Battle Of The Bands" vs "Brickmakers Battle Of The Bands").

    Keeps the first occurrence of each duplicate group.
    """
    kept: list[dict] = []
    seen: list[tuple[str, str, str]] = []  # (venue, date, normalised title)

    for e in events:
        venue = e.get("venue", "").strip()
        date  = e.get("date", "").strip()
        title = _normalise_for_match(e.get("event_name", ""))

        is_dup = False
        for s_venue, s_date, s_title in seen:
            if s_venue != venue or s_date != date:
                continue
            if s_title == title or SequenceMatcher(None, s_title, title).ratio() >= threshold:
                is_dup = True
                break

        if is_dup:
            continue

        seen.append((venue, date, title))
        kept.append(e)

    return kept


def should_filter_event(event_name: str) -> bool:
    """Return True if the event should be excluded from output."""
    if _FILTER_PHRASES.search(event_name):
        return True
    if _FILTER_PHONE.search(event_name):
        return True
    return False


_LOWER_WORDS = {"a","an","the","and","or","but","for","nor","so","yet",
                "at","by","in","of","on","to","up","as","is","it","vs","vs."}


def normalise_title(name: str) -> str:
    """
    Normalise event name capitalisation.
    If the name is ALL CAPS (>85% uppercase letters), converts to title case
    with common small words (a, the, of, …) kept lowercase mid-phrase.
    Mixed-case names are returned unchanged.
    """
    alpha = [c for c in name if c.isalpha()]
    if not alpha:
        return name
    if sum(1 for c in alpha if c.isupper()) / len(alpha) <= 0.85:
        return name   # already mixed case — leave it alone

    words = name.split()
    result = []
    for i, word in enumerate(words):
        titled = word.capitalize()
        if i > 0 and word.lower() in _LOWER_WORDS:
            prev = result[-1] if result else ""
            if not prev.endswith((":", "-")):
                titled = word.lower()
        result.append(titled)
    return " ".join(result)


# ═══════════════════════════════════════════════════════════════════════════════
# SCRAPER ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

OUTPUT_DIR     = Path.home() / "norwich-scraper" / "scraped_data"
CSV_FILE       = OUTPUT_DIR / "norwich_gigs.csv"
CSV_FIELDS     = ["venue", "event_name", "date", "url"]
CSV_FLAT_FILE  = OUTPUT_DIR / "norwich_gigs_flat.csv"

def _setup_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 Chrome/120 Safari/537.36"
    )
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=opts)


def _strip_ordinal(text: str) -> str:
    """23rd → 23, 1st → 1, etc."""
    return re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", text, flags=re.IGNORECASE)


def _parse_date(text: str) -> str | None:
    """Try to turn a human date string into YYYY-MM-DD."""
    if not DATEUTIL_OK or not text:
        return None
    cleaned = _strip_ordinal(text.strip())
    year    = datetime.now().year
    for candidate in [cleaned, f"{cleaned} {year}"]:
        try:
            dt = dateutil_parse(candidate, dayfirst=True, fuzzy=True)
            if abs(dt.year - year) <= 1:
                return dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    return None


# ── Individual venue scrapers ─────────────────────────────────────────────────

def scrape_space_studios(driver, log) -> list[dict]:
    """
    Space Studios Norwich — Wix repeater cards (Selenium).

    URL : https://www.spacestudiosnorwich.com  (front page has current events)
    The site has no fixed URL structure for "next month" pages — the team
    running it just spins up a new Wix page with a fresh, unpredictable
    slug each month (e.g. "copy-of-august-events") and links to it from a
    "More Events" button on the front page. Some of those pages then have
    their own "Month" nav link to yet another page.

    Strategy: scrape the front page, then look for an <a> whose text is
    "More Events" (front page) or "Month" (subsequent pages) and follow it,
    repeating until no new/unvisited link is found or a safety cap of hops
    is hit. Events are de-duplicated across pages by (title, date) since
    later pages tend to re-list events already seen on an earlier page.

    Typical card structure inside div/li[role="listitem"]:
        h2/h3       → event title
        h4 / p      → room, date, ticket label, genre (order can vary after Wix updates)

    Date is found positionally-independent: whichever heading/paragraph
    contains an ordinal day number (1st, 2nd … 31st) is treated as the date.
    This survives Wix layout changes that shift h4 index positions.
    """
    VENUE = "Space Studios Norwich"
    URL   = "https://www.spacestudiosnorwich.com"
    MAX_HOPS = 6  # front page + up to 5 "More Events"/"Month" follow-ons, as a safety cap

    log(f"\n{chr(9472)*48}", "dim")
    log(f"  Scraping {VENUE}", "plain")
    log(f"{chr(9472)*48}", "dim")

    _ORDINAL = re.compile(r"\d{1,2}(st|nd|rd|th)", re.I)
    _MONTH   = re.compile(
        r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b",
        re.I,
    )
    _NEXT_LINK_LABELS = {"more events", "month"}

    def _parse_cards(soup, page_url):
        """Parse every event card on the page, return list of event dicts."""
        cards = (
            soup.find_all("div", attrs={"role": "listitem"})
            or soup.find_all("li",  attrs={"role": "listitem"})
            or soup.find_all(attrs={"role": "listitem"})
        )
        log(f"  Found {len(cards)} card(s)", "dim")

        # Diagnostic: log heading structure of first card so layout changes are visible
        if cards:
            first = cards[0]
            headings = [(t.name, " ".join(t.get_text().split())[:60])
                        for t in first.find_all(["h1","h2","h3","h4","h5","p"])
                        if t.get_text(strip=True)]
            log(f"  ℹ  First card headings: {headings}", "dim")

        page_events = []
        for card in cards:
            try:
                # Title — prefer h3, fall back to h2, then h4
                title = ""
                for tag in ("h3", "h2", "h4"):
                    el = card.find(tag)
                    if el:
                        title = " ".join(el.get_text().split()).strip()
                        if title:
                            break
                if not title:
                    continue

                # Date — scan ALL h4/p/span elements; pick first one that looks
                # like a date (contains an ordinal). This is position-independent.
                raw_date = ""
                for el in card.find_all(["h4", "h5", "p", "span"]):
                    txt = " ".join(el.get_text().split())
                    if _ORDINAL.search(txt) and _MONTH.search(txt):
                        raw_date = txt
                        break
                # Fallback: ordinal alone (e.g. "Friday 1st May" without year)
                if not raw_date:
                    for el in card.find_all(["h4", "h5", "p", "span"]):
                        txt = " ".join(el.get_text().split())
                        if _ORDINAL.search(txt):
                            raw_date = txt
                            break

                if not raw_date:
                    log(f"  ⚠  No date element for: {title}", "warn")
                    continue

                # Append current year if missing
                if not re.search(r"\d{4}", raw_date):
                    raw_date += f" {datetime.now().year}"

                date_str = _parse_date(raw_date)
                if not date_str:
                    log(f"  ⚠  Could not parse date '{raw_date}' for: {title}", "warn")
                    continue

                # Ticket URL — any <a href> in the card; prefer ones that look
                # like ticket links, otherwise fall back to the site root.
                event_url = page_url
                for a_tag in card.find_all("a", href=True):
                    href = a_tag["href"]
                    if re.search(r"ticket|book|event|wix", href, re.I):
                        event_url = href
                        break
                    event_url = href  # take the last-resort first href

                page_events.append({
                    "venue":      VENUE,
                    "event_name": title,
                    "date":       date_str,
                    "url":        event_url,
                })

            except Exception as e:
                log(f"  ⚠  Card error: {e}", "warn")

        return page_events

    def _find_next_page(soup, current_url, visited):
        """Find a 'More Events' or 'Month' link not yet visited."""
        for a_tag in soup.find_all("a", href=True):
            label = " ".join(a_tag.get_text().split()).strip().lower()
            if label in _NEXT_LINK_LABELS:
                next_url = urljoin(current_url, a_tag["href"])
                if next_url not in visited:
                    return next_url
        return None

    events: list[dict] = []
    seen_keys: set[tuple[str, str]] = set()  # (event_name, date) — dedupe across pages
    visited: set[str] = set()

    try:
        current_url = URL
        for hop in range(MAX_HOPS):
            visited.add(current_url)
            log(f"  → Page {hop + 1}: {current_url}", "dim")

            driver.get(current_url)

            # Wait for any of: listitem div, listitem li, or just an h3/h2 heading —
            # whichever Wix is currently rendering.
            for css in ('[role="listitem"]', 'h3', 'h2'):
                try:
                    WebDriverWait(driver, 15).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, css))
                    )
                    log(f"  ✓  Page ready (matched '{css}')", "dim")
                    break
                except Exception:
                    pass
            time.sleep(3)   # extra settle time for Wix JS hydration

            soup = BeautifulSoup(driver.page_source, "lxml")
            page_events = _parse_cards(soup, current_url)

            new_on_page = 0
            for ev in page_events:
                key = (ev["event_name"].strip().lower(), ev["date"])
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                events.append(ev)
                new_on_page += 1
                log(f"  ✓  {ev['date']}  {ev['event_name']}", "ok")

            log(f"  → {new_on_page} new event(s) on this page ({len(page_events)} total on page)", "dim")

            next_url = _find_next_page(soup, current_url, visited)
            if not next_url:
                log(f"  No further 'More Events'/'Month' link — done", "dim")
                break
            current_url = next_url

    except Exception as e:
        log(f"  ✗  {VENUE} failed: {e}", "err")

    log(f"  → {len(events)} event(s) total", "dim")
    return events


def scrape_norwich_arts_centre(session, log) -> list[dict]:
    """
    Norwich Arts Centre — custom-theme events grid (no longer The Events
    Calendar / tribe-events markup — that was swapped out on the venue's
    site at some point; see git history for the old class-based version
    this replaced).

    Page 1 : https://norwichartscentre.co.uk/event/category/music/
    Page N : https://norwichartscentre.co.uk/event/category/music/page/N/
    Stops on HTTP 404, a WordPress redirect back to base URL, or a page
    that yields no *new* event URLs (belt-and-braces in case pagination
    behaviour also changed).

    New markup has no distinctive card/date class names. What's reliable:
      - every event has an <a href="…/event/<slug>/…"> — appears twice per
        card (once wrapping the thumbnail, once as the h3 title link)
      - a plain text date line somewhere in the card, e.g.
        "Fri 28 Aug 2026 @ 8:00 PM"

    Strategy: collect all /event/ links, dedupe by resolved URL, walk up
    from each anchor to find its card container, pull the title from a
    heading (or the anchor text if none), and regex-search the container's
    text for a date line. This keys off URL shape + plain text rather than
    CSS classes, so it should be more resilient to future markup/styling
    changes than matching specific classes.
    """
    BASE  = "https://norwichartscentre.co.uk/event/category/music/"
    VENUE = "Norwich Arts Centre"

    log(f"\n{'─'*48}", "dim")
    log(f"  Scraping {VENUE}", "plain")
    log(f"{'─'*48}", "dim")

    # Anchored + excludes /event/category/... — the old unanchored version
    # of this pattern also matched the genre filter sidebar links
    # (/event/category/folk/ etc.), which polluted results with bogus
    # "events" and duplicate entries carrying the wrong (category-archive)
    # URL.
    _EVENT_HREF = re.compile(r"/event/(?!category/)[a-z0-9\-]+/?(?:$|[?#])", re.I)
    _DATE_LINE = re.compile(
        r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\s+\d{1,2}\s+"
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}"
        r"(\s*@\s*\d{1,2}[:.]\d{2}\s*[APap][Mm])?",
        re.IGNORECASE,
    )

    events = []
    page = 1
    seen_urls: set[str] = set()

    while True:
        url = BASE if page == 1 else f"{BASE}page/{page}/"
        log(f"  Page {page}…", "dim")
        try:
            resp = session.get(url, timeout=15)
            if resp.status_code == 404:
                log(f"  404 — no more pages", "dim")
                break
            if page > 1 and resp.url.rstrip("/") == BASE.rstrip("/"):
                log(f"  Redirected to base — done", "dim")
                break
            soup = BeautifulSoup(resp.text, "lxml")
        except Exception as e:
            log(f"  ✗  Page {page} failed: {e}", "err")
            break

        links = soup.find_all("a", href=_EVENT_HREF)

        # Dedupe by resolved URL — thumbnail + title anchors point at the
        # same event.
        by_url: dict[str, "BeautifulSoup"] = {}
        for a in links:
            full_url = urljoin(url, a["href"])
            by_url.setdefault(full_url, a)

        if not by_url:
            log(f"  No event links on page {page} — done", "dim")
            break

        new_this_page = [u for u in by_url if u not in seen_urls]
        if not new_this_page:
            log(f"  No new events on page {page} — done", "dim")
            break

        log(f"  Found {len(by_url)} event link(s) ({len(new_this_page)} new)", "dim")

        for event_url, a_tag in by_url.items():
            if event_url in seen_urls:
                continue
            seen_urls.add(event_url)
            try:
                # Walk up to find a card-ish container that holds both the
                # title and the date text.
                container = a_tag
                card = None
                for _ in range(6):
                    container = container.find_parent(["article", "div", "li"])
                    if container is None:
                        break
                    if container.find(["h2", "h3", "h4"]) and _DATE_LINE.search(
                        container.get_text(" ", strip=True)
                    ):
                        card = container
                        break
                if card is None:
                    # Fall back to whatever the nearest reasonable ancestor was
                    card = container or a_tag

                # Title — prefer a heading inside the card, else the anchor text
                title = ""
                heading = card.find(["h2", "h3", "h4"])
                if heading:
                    title = " ".join(heading.get_text().split())
                if not title:
                    title = " ".join(a_tag.get_text().split())
                if not title:
                    log(f"  ⚠  No title for {event_url}", "warn")
                    continue

                card_text = card.get_text(" ", strip=True)
                m = _DATE_LINE.search(card_text)
                if not m:
                    log(f"  ⚠  No date found for: {title}", "warn")
                    continue

                raw_date = m.group(0)
                date_str = _parse_date(raw_date)
                if not date_str:
                    log(f"  ⚠  Could not parse date '{raw_date}' for: {title}", "warn")
                    continue

                events.append({"venue": VENUE, "event_name": title,
                               "date": date_str, "url": event_url})
                log(f"  ✓  {date_str}  {title}", "ok")
            except Exception as e:
                log(f"  ⚠  Card error ({event_url}): {e}", "warn")

        page += 1
        if page > 20:
            log("  ⚠  Reached page limit (20) — stopping", "warn")
            break
        time.sleep(1)

    log(f"  → {len(events)} event(s)", "dim")
    return events


# Venue display names as they appear on ueaticketbookings.co.uk event
# cards. Ordered longest-first so a more specific name wins over a
# shorter one it starts with when regex-matching card text (otherwise
# "The Adrian Flux Waterfront" would match inside "...Waterfront Studio"
# cards too, since alternation tries options in listed order and stops at
# the first one that matches at a given position).
_UEA_VENUE_NAMES = [
    "The Adrian Flux Waterfront Studio",
    "The Adrian Flux Waterfront",
    "The Nick Rayns LCR, UEA",
    "Voodoo Daddys Showroom",
    "Norwich Arts Centre",
    "Food Museum, Stowmarket",
    "The Brickmakers",
    "Epic Studios",
    "Dead Wax",
]
_UEA_VENUE_RE = re.compile("|".join(re.escape(v) for v in _UEA_VENUE_NAMES))

_UEA_DATE_RE = re.compile(
    r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\s+\d{1,2}\s+"
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{4}\b",
    re.IGNORECASE,
)

_UEA_EVENT_HREF = re.compile(r"/event/[a-z0-9\-]+/?(?:$|[?#])", re.I)


def _scrape_uea_whats_on(session, log,
                          base_url="https://www.ueaticketbookings.co.uk/whats-on/",
                          max_pages=25) -> list[dict]:
    """
    Shared crawler for ueaticketbookings.co.uk's "What's On" listing.

    This used to be filterable server-side per venue via a
    ?_sfm_venue=<Venue+Name> query param, which scrape_waterfront and
    scrape_lcr each used with their own venue value. That no longer works:
    fetching the listing with the old venue value, with the venue's
    current slug (taken from the site's own venue-filter links, e.g.
    ?_sfm_venue=Waterfront), and with no venue param at all, all return
    the identical unfiltered listing — every venue mixed together. The
    facet filtering is presumably applied client-side via JS now, which a
    plain GET never triggers, so there's no URL-based way left to ask the
    site for just one venue's events.

    So instead this fetches the single combined, paginated listing once
    and tags every event with whichever known venue name
    (_UEA_VENUE_NAMES) appears on its card — scrape_waterfront/scrape_lcr
    then just filter this combined list down to the venue(s) they want.
    Each event card is, in order:

        <a href=".../event/<slug>/">          thumbnail image link
        "<Weekday> <day> <Month> <year>"       date line, e.g. "Sat 29 August 2026"
        "<venue name>"                         one of _UEA_VENUE_NAMES
        #### <title>                           event title (h4)
        ###### <subtitle>                      optional subtitle (h6)
        <a href="...same event url...">CTA</a> "Book tickets" / "Selling fast" / etc.

    Returns {"venue", "event_name", "date", "url"} dicts, where "venue" is
    the full name as printed on the card (not yet run through
    DEFAULT_ALIASES).
    """
    events = []
    page = 1
    seen_urls: set[str] = set()

    while page <= max_pages:
        url = base_url if page == 1 else f"{base_url}?sf_paged={page}"
        log(f"  Page {page}…", "dim")
        try:
            resp = session.get(url, timeout=15)
            if resp.status_code == 404:
                log(f"  404 — no more pages", "dim")
                break
            soup = BeautifulSoup(resp.text, "lxml")
        except Exception as e:
            log(f"  ✗  Page {page} failed: {e}", "err")
            break

        links = soup.find_all("a", href=_UEA_EVENT_HREF)
        by_url: dict[str, "BeautifulSoup"] = {}
        for a in links:
            full_url = urljoin(url, a["href"])
            by_url.setdefault(full_url, a)

        if not by_url:
            log(f"  No event links on page {page} — done", "dim")
            break

        new_this_page = [u for u in by_url if u not in seen_urls]
        if not new_this_page:
            log(f"  No new events on page {page} — done", "dim")
            break

        log(f"  Found {len(by_url)} event link(s) ({len(new_this_page)} new)", "dim")

        for event_url, a_tag in by_url.items():
            if event_url in seen_urls:
                continue
            seen_urls.add(event_url)
            try:
                # Walk up to find a card-ish container with a heading, a
                # date, and a known venue name all present together.
                container = a_tag
                card = None
                for _ in range(6):
                    container = container.find_parent(["article", "div", "li"])
                    if container is None:
                        break
                    text = container.get_text(" ", strip=True)
                    if (container.find(["h4", "h3", "h2"])
                            and _UEA_DATE_RE.search(text)
                            and _UEA_VENUE_RE.search(text)):
                        card = container
                        break
                if card is None:
                    card = container or a_tag

                heading = card.find(["h4", "h3", "h2"])
                title = " ".join(heading.get_text().split()) if heading else ""
                if not title:
                    title = " ".join(a_tag.get_text().split())
                if not title:
                    log(f"  ⚠  No title for {event_url}", "warn")
                    continue

                card_text = card.get_text(" ", strip=True)

                date_m = _UEA_DATE_RE.search(card_text)
                if not date_m:
                    log(f"  ⚠  No date for: {title}", "warn")
                    continue
                date_str = _parse_date(date_m.group(0))
                if not date_str:
                    log(f"  ⚠  Could not parse date '{date_m.group(0)}' for: {title}", "warn")
                    continue

                venue_m = _UEA_VENUE_RE.search(card_text)
                if not venue_m:
                    log(f"  ⚠  No recognised venue name for: {title}", "warn")
                    continue
                venue_name = venue_m.group(0)

                events.append({"venue": venue_name, "event_name": title,
                               "date": date_str, "url": event_url})
                log(f"  ✓  {date_str}  [{venue_name}]  {title}", "ok")
            except Exception as e:
                log(f"  ⚠  Card error ({event_url}): {e}", "warn")

        page += 1
        time.sleep(1)

    return events


def scrape_waterfront(session, log) -> list[dict]:
    """
    Waterfront + Waterfront Studio — ueaticketbookings.co.uk.

    Pulls the combined listing via _scrape_uea_whats_on() (see that
    function's docstring for why the old per-venue ?_sfm_venue= filtering
    no longer works) and keeps only cards tagged with one of the two
    Waterfront venue names, same output shape as before.

    Note: scrape_lcr() also calls _scrape_uea_whats_on() independently, so
    the listing gets fetched twice per full run (once per venue group).
    That's a bit wasteful but keeps this function self-contained and
    matches the rest of this file's one-function-per-venue structure.
    """
    log(f"\n{'─'*48}", "dim")
    log(f"  Scraping Waterfront (UEA ticket bookings)", "plain")
    log(f"{'─'*48}", "dim")

    all_events = _scrape_uea_whats_on(session, log)

    rename = {
        "The Adrian Flux Waterfront":       "Waterfront",
        "The Adrian Flux Waterfront Studio": "Waterfront Studio",
    }
    events = [
        {**e, "venue": rename[e["venue"]]}
        for e in all_events
        if e["venue"] in rename
    ]

    log(f"  → {len(events)} Waterfront event(s)", "dim")
    return events


def scrape_lcr(session, log) -> list[dict]:
    """
    Nick Rayns LCR — ueaticketbookings.co.uk.

    Same combined-listing approach as scrape_waterfront() — see
    _scrape_uea_whats_on() for why the old ?_sfm_venue= filtering broke.
    """
    VENUE = "Nick Rayns LCR"

    log(f"\n{'─'*48}", "dim")
    log(f"  Scraping {VENUE}", "plain")
    log(f"{'─'*48}", "dim")

    all_events = _scrape_uea_whats_on(session, log)

    events = [
        {**e, "venue": VENUE}
        for e in all_events
        if e["venue"] == "The Nick Rayns LCR, UEA"
    ]

    log(f"  → {len(events)} LCR event(s)", "dim")
    return events


def scrape_gonzos(driver, log) -> list[dict]:
    """
    Gonzo's Tea Room — Fatsoma (Selenium + incremental JSON-LD harvesting).

    Fatsoma client-side renders most events, AND uses virtual scrolling that
    removes off-screen DOM nodes. Two-stage strategy:

    1. After each scroll step, grab driver.page_source and parse every
       <script type="application/ld+json"> block for Event objects.
    2. Accumulate results in a seen-URL dict so removed-from-DOM cards are
       not lost.

    Paginates via ?page=N until a page yields zero new events.
    """
    import json as _json

    VENUE    = "Gonzo's Tea Room"
    BASE_URL = "https://www.fatsoma.com/p/gonzostworoom/events"

    log(f"\n{'─'*48}", "dim")
    log(f"  Scraping {VENUE} (Fatsoma)", "plain")
    log(f"{'─'*48}", "dim")

    def _harvest_page_source(html: str, seen: dict) -> int:
        """Parse JSON-LD Event blocks from html; add new ones to seen{url→event}.
        Returns count of newly added events."""
        added = 0
        soup = BeautifulSoup(html, "lxml")
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = _json.loads(script.string or "")
            except Exception:
                continue
            if data.get("@type") != "Event":
                continue
            name      = (data.get("name") or "").strip()
            start_iso = data.get("startDate", "")
            event_url = (data.get("url") or "").strip()
            if not name or not start_iso or not event_url:
                continue
            if event_url in seen:
                continue
            date_str = start_iso[:10]          # "2026-05-22T22:…" → "2026-05-22"
            seen[event_url] = {
                "venue":      VENUE,
                "event_name": name,
                "date":       date_str,
                "url":        event_url,
            }
            added += 1
        return added

    all_seen: dict = {}   # url → event dict, persists across pages
    page = 1

    while True:
        url = BASE_URL if page == 1 else f"{BASE_URL}?page={page}"
        log(f"  Loading page {page}…", "dim")

        try:
            driver.get(url)
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, 'div[data-theme="event-card"]')
                )
            )
            time.sleep(1.5)
        except Exception:
            log(f"  No cards on page {page} — done paginating", "dim")
            break

        # Harvest initial load
        before = len(all_seen)
        _harvest_page_source(driver.page_source, all_seen)
        log(f"  After initial load: {len(all_seen) - before} new event(s)", "dim")

        # Scroll incrementally, harvesting after each step
        no_new_scrolls = 0
        for scroll_n in range(30):   # safety cap
            driver.execute_script("window.scrollBy(0, 800);")
            time.sleep(1.0)
            newly_added = _harvest_page_source(driver.page_source, all_seen)
            if newly_added:
                log(f"  Scroll {scroll_n+1}: +{newly_added} event(s) (total {len(all_seen)})", "dim")
                no_new_scrolls = 0
            else:
                no_new_scrolls += 1
                if no_new_scrolls >= 3:
                    # Three consecutive scrolls with nothing new → bottom of page
                    break

        found_on_page = len(all_seen) - before
        log(f"  → {found_on_page} new event(s) on page {page}", "dim")

        if found_on_page == 0:
            log(f"  No new events on page {page} — done paginating", "dim")
            break

        page += 1
        if page > 10:
            log("  ⚠  Reached page limit (10) — stopping", "warn")
            break

        time.sleep(1)

    events = list(all_seen.values())
    for ev in events:
        log(f"  ✓  {ev['date']}  {ev['event_name']}", "ok")
    log(f"  → {len(events)} Gonzos event(s) total", "dim")
    return events
def scrape_voodoos(session, log) -> list[dict]:
    """
    Voodoo Daddy's Showroom — Fatsoma card layout.
    """
    URL   = "https://www.voodoodaddysshowroom.co.uk/events/"
    VENUE = "Voodoo Daddy's Showroom"

    log(f"\n{'─'*48}", "dim")
    log(f"  Scraping {VENUE}", "plain")
    log(f"{'─'*48}", "dim")

    events = []
    try:
        resp = session.get(URL, timeout=15)
        soup = BeautifulSoup(resp.text, "lxml")
        cards = soup.find_all("div", class_="card__wrapper")
        log(f"  Found {len(cards)} card(s)", "dim")
        for card in cards:
            try:
                h3 = card.find("h3", class_="card__heading")
                if not h3:
                    continue
                a = h3.find("a")
                title = a.get_text(strip=True) if a else h3.get_text(strip=True)
                link  = a["href"] if a and a.get("href") else ""
                if not title:
                    continue
                de  = card.find("span", class_="meta--date")
                raw = de.get_text(strip=True) if de else ""
                date_str = _parse_date(raw) if raw else None
                if not date_str:
                    log(f"  ⚠  No date for: {title}", "warn")
                    continue
                events.append({"venue": VENUE, "event_name": title,
                               "date": date_str, "url": link})
                log(f"  ✓  {date_str}  {title}", "ok")
            except Exception:
                continue
    except Exception as e:
        log(f"  ✗  {VENUE} failed: {e}", "err")

    log(f"  → {len(events)} event(s)", "dim")
    return events


def scrape_holloway(driver, log) -> list[dict]:
    """
    The Holloway — Vue.js rendered, requires Selenium.
    """
    URL   = "https://thehollowaynorwich.com/events"
    VENUE = "The Holloway"

    log(f"\n{'─'*48}", "dim")
    log(f"  Scraping {VENUE}", "plain")
    log(f"{'─'*48}", "dim")

    events = []
    try:
        driver.get(URL)
        time.sleep(5)
        soup  = BeautifulSoup(driver.page_source, "lxml")
        cards = soup.find_all("div", class_="event-card")
        log(f"  Found {len(cards)} card(s)", "dim")
        for card in cards:
            try:
                h3    = card.find("h3")
                title = h3.get_text(strip=True) if h3 else ""
                if not title:
                    continue
                de   = card.find("p", class_="date")
                raw  = de.get_text(strip=True) if de else ""
                date_str = _parse_date(raw) if raw else None
                if not date_str:
                    log(f"  ⚠  No date for: {title}", "warn")
                    continue
                events.append({"venue": VENUE, "event_name": title,
                               "date": date_str, "url": URL})
                log(f"  ✓  {date_str}  {title}", "ok")
            except Exception:
                continue
    except Exception as e:
        log(f"  ✗  {VENUE} failed: {e}", "err")

    log(f"  → {len(events)} event(s)", "dim")
    return events


def scrape_epic_studios(driver, log) -> list[dict]:
    """
    Epic Studios — event links on epic-tv.com (Selenium).
    """
    URL   = "https://epic-tv.com/events"
    VENUE = "Epic Studios"

    log(f"\n{'─'*48}", "dim")
    log(f"  Scraping {VENUE}", "plain")
    log(f"{'─'*48}", "dim")

    events = []
    try:
        driver.get(URL)
        time.sleep(4)
        soup  = BeautifulSoup(driver.page_source, "lxml")
        links = soup.find_all("a", href=re.compile(r"/events/event/"))
        log(f"  Found {len(links)} event link(s)", "dim")
        for a in links:
            try:
                h3    = a.find("h3")
                title = h3.get_text(strip=True) if h3 else ""
                if not title:
                    continue
                sm   = a.find("small")
                raw  = sm.get_text(strip=True) if sm else ""
                date_str = _parse_date(raw) if raw else None
                if not date_str:
                    log(f"  ⚠  No date for: {title}", "warn")
                    continue
                link = a.get("href", "")
                if link and not link.startswith("http"):
                    link = "https://epic-tv.com" + link
                events.append({"venue": VENUE, "event_name": title,
                               "date": date_str, "url": link})
                log(f"  ✓  {date_str}  {title}", "ok")
            except Exception:
                continue
    except Exception as e:
        log(f"  ✗  {VENUE} failed: {e}", "err")

    log(f"  → {len(events)} event(s)", "dim")
    return events


def scrape_dead_wax(session, log) -> list[dict]:
    """
    Dead Wax — Squarespace eventlist (static HTML, requests).
    URL: https://www.deadwaxnorwich.pub/whats-on
    Each event is an <article class="eventlist-event"> with:
        h1.eventlist-title > a  → title + link
        time.event-date         → datetime attr (ISO) or text fallback
    """
    VENUE = "Dead Wax"
    URL   = "https://www.deadwaxnorwich.pub/whats-on"

    log(f"\n{chr(9472)*48}", "dim")
    log(f"  Scraping {VENUE}", "plain")
    log(f"{chr(9472)*48}", "dim")

    events = []
    try:
        resp = session.get(URL, timeout=15)
        resp.raise_for_status()
        soup     = BeautifulSoup(resp.content, "lxml")
        articles = soup.find_all("article", class_="eventlist-event")
        log(f"  Found {len(articles)} article(s)", "dim")

        for art in articles:
            try:
                title_el = art.find("h1", class_="eventlist-title")
                if not title_el:
                    continue
                a_el  = title_el.find("a")
                title = " ".join((a_el or title_el).get_text().split())
                link  = urljoin(URL, a_el["href"]) if a_el and a_el.get("href") else URL
                if not title:
                    continue

                date_el  = art.find("time", class_="event-date")
                raw_date = ""
                if date_el:
                    raw_date = (date_el.get("datetime")
                                or " ".join(date_el.get_text().split()))
                date_str = _parse_date(raw_date) if raw_date else None
                if not date_str:
                    log(f"  ⚠  No date for: {title}", "warn")
                    continue

                events.append({"venue": VENUE, "event_name": title,
                               "date": date_str, "url": link})
                log(f"  ✓  {date_str}  {title}", "ok")
            except Exception as e:
                log(f"  ⚠  Article error: {e}", "warn")

    except Exception as e:
        log(f"  ✗  {VENUE} failed: {e}", "err")

    log(f"  → {len(events)} Dead Wax event(s)", "dim")
    return events


def scrape_brickmakers_gig_guide(session, log) -> list[dict]:
    """
    Norfolk Gig Guide — scrape all pages and return only Brickmakers gigs.

    Page structure:
        div.gig_bg
          div.large-4 col 0 → act name   (h3 > a)
          div.large-4 col 1 → date       (h3 text, e.g. "Wednesday 20 May 2026")
          div.large-4 col 2 → venue      (h3 > a)

    Stops paginating when a page returns no gig_bg divs.
    """
    VENUE      = "The Brickmakers - Norwich"
    BASE_URL   = "https://www.norfolkgigguide.com/"
    VENUE_SLUG = "The+Brickmakers+-+Norwich"

    log(f"\n{'─'*48}", "dim")
    log(f"  Scraping Norfolk Gig Guide → {VENUE}", "plain")
    log(f"{'─'*48}", "dim")

    events = []
    page   = 1

    while True:
        url = f"{BASE_URL}?pg={page}&searchList=&searchFor="
        log(f"  Fetching page {page}…", "dim")

        try:
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            log(f"  ✗  Page {page} failed: {e}", "err")
            break

        soup = BeautifulSoup(resp.text, "lxml")
        gigs = soup.select("div.gig_bg")

        if not gigs:
            log(f"  No gigs on page {page} — done paginating", "dim")
            break

        found_on_page = 0
        for gig in gigs:
            cols = gig.select("div.large-4.columns")
            if len(cols) < 3:
                continue

            # Venue — third column
            venue_tag = cols[2].select_one("h3 a")
            if not venue_tag:
                continue
            venue_name = venue_tag.get_text(strip=True)
            if "brickmakers" not in venue_name.lower():
                continue

            # Act name — first column
            act_tag  = cols[0].select_one("h3 a")
            act_name = act_tag.get_text(strip=True) if act_tag else cols[0].get_text(strip=True)

            # Date — second column h3 text ("Wednesday 20 May 2026")
            date_tag = cols[1].select_one("h3")
            date_str = _parse_date(date_tag.get_text(strip=True)) if date_tag else None

            if not date_str:
                log(f"  ⚠  No date for: {act_name}", "warn")
                continue

            # Detail URL — link on the act name
            act_href = act_tag["href"] if act_tag and act_tag.get("href") else ""
            event_url = BASE_URL.rstrip("/") + act_href if act_href.startswith("/") else act_href or BASE_URL

            events.append({
                "venue":      VENUE,
                "event_name": act_name,
                "date":       date_str,
                "url":        event_url,
            })
            log(f"  ✓  {date_str}  {act_name}", "ok")
            found_on_page += 1

        log(f"  → {found_on_page} Brickmakers gig(s) on page {page}", "dim")
        page += 1

        # Safety cap — gig guide unlikely to exceed 20 pages of upcoming events
        if page > 20:
            log("  ⚠  Reached page limit (20) — stopping", "warn")
            break

        time.sleep(0.5)   # be polite between requests

    log(f"  → {len(events)} Brickmakers gig(s) total", "dim")
    return events


def scrape_brickmakers_website(driver, log) -> list[dict]:
    """
    The Brickmakers — direct scrape from their own website (Selenium).
    URL: https://brickmakersnorwich.co.uk/home/brickmakers/brickmakers-gigs/

    Each gig lives in a div.wp-block-media-text__content block.
    The first <p> inside each block holds the event info in <strong> tags:

        Variation A (separate strongs):
            <strong>Wed 3rd June:</strong>
            <strong>ACT NAME</strong>
            <strong>8pm</strong>

        Variation B (date:title in one strong):
            <strong>Sat 13th June:Brickmakers Battle Of The Bands</strong>
            <strong>8pm</strong>

    Junk titles (FREE ENTRY, TICKETS HERE, phone numbers, etc.) are skipped.
    """
    VENUE = "The Brickmakers - Norwich"
    URL   = "https://brickmakersnorwich.co.uk/home/brickmakers/brickmakers-gigs/"

    # Strings that are not act names
    _JUNK = re.compile(
        r"^(free\s+entry|tickets?\s+(here|available|on\s+sale)|doors?\s+\d|"
        r"\d{5}[\s\-]?\d+|cask\s+master|£\d|\d+\s*(am|pm)$|^uk$|"
        r"entry\s+£|no\s+entry|\+\s+support)",
        re.IGNORECASE,
    )

    log(f"\n{'─'*48}", "dim")
    log(f"  Scraping Brickmakers (website)", "plain")
    log(f"{'─'*48}", "dim")

    events = []
    try:
        driver.get(URL)
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "div.wp-block-media-text__content")
                )
            )
        except Exception:
            pass
        time.sleep(3)

        soup   = BeautifulSoup(driver.page_source, "lxml")
        blocks = soup.find_all("div", class_="wp-block-media-text__content")
        log(f"  Found {len(blocks)} content block(s)", "dim")

        for block in blocks:
            try:
                first_p = block.find("p")
                if not first_p:
                    continue
                strongs = [s.get_text(strip=True)
                           for s in first_p.find_all("strong")
                           if s.get_text(strip=True)]
                if not strongs:
                    continue

                date_str = None
                title    = None

                for i, s in enumerate(strongs):
                    # Variation B: "Date:Title" fused in one strong
                    if ":" in s:
                        parts          = s.split(":", 1)
                        date_candidate = parts[0].strip()
                        title_candidate = parts[1].strip() if len(parts) > 1 else ""
                        parsed = _parse_date(date_candidate)
                        if parsed:
                            date_str = parsed
                            if title_candidate and not _JUNK.match(title_candidate):
                                title = title_candidate
                            else:
                                # Title must come from a later strong
                                for s2 in strongs[i + 1:]:
                                    if re.fullmatch(
                                            r"\d{1,2}[\.:]*\d{0,2}\s*(am|pm)?",
                                            s2, re.IGNORECASE):
                                        continue
                                    if not _JUNK.match(s2):
                                        title = s2
                                        break
                            break
                    else:
                        # Variation A: pure date string (may end with colon)
                        parsed = _parse_date(s.rstrip(":"))
                        if parsed:
                            date_str = parsed
                            for s2 in strongs[i + 1:]:
                                if re.fullmatch(
                                        r"\d{1,2}[\.:]*\d{0,2}\s*(am|pm)?",
                                        s2, re.IGNORECASE):
                                    continue
                                if not _JUNK.match(s2):
                                    title = s2
                                    break
                            break

                if not date_str or not title:
                    if date_str:  # date found but no usable title — silently skip
                        pass
                    else:
                        log(f"  ⚠  Could not parse block: {strongs[:3]}", "warn")
                    continue

                events.append({"venue": VENUE, "event_name": title,
                               "date": date_str, "url": URL})
                log(f"  ✓  {date_str}  {title}", "ok")

            except Exception as e:
                log(f"  ⚠  Block error: {e}", "warn")

    except Exception as e:
        log(f"  ✗  Brickmakers website failed: {e}", "err")

    log(f"  → {len(events)} Brickmakers website event(s)", "dim")
    return events

# Music-related keywords used to filter Madder Market and The Halls
_MUSIC_KEYWORDS = re.compile(
    r"\b(tribute|band|live\s+music|music|concert|gig|singer|quartet|"
    r"orchestra|choir|jazz|blues|rock|folk|acoustic|duo|ensemble|"
    r"recital|symphony|philharmonic|swing|soul|reggae|pop|punk|metal|"
    r"country|classical|string|brass|violin|piano|guitar|drum|"
    r"vocalist|songwriter|covers|disco|funk|latin|afrobeat|afro|"
    r"hip.?hop|r&b|rnb|electronic|ambient|indie|alternative|"
    r"flamenco|tango|cabaret|burlesque|beatles|bowie|elvis|"
    r"tour|live\s+show|evening\s+of|night\s+of|sounds\s+of|"
    r"songs\s+of|music\s+of|hits\s+of|the\s+music\s+of|"
    r"motown|abba|queen|fleetwood\s+mac|dire\s+straits|"
    r"sixties|seventies|eighties|nineties|noughties|"
    r"in\s+concert|unplugged|a\s*cappella|gospel|opera|ukulele|"
    r"years\s+of|decades\s+of|experience|floyd|elton|bee\s+gees|"
    r"showband|big\s+band|singalong|sing-along)\b",
    re.IGNORECASE,
)


def _is_music_event(text: str) -> bool:
    """Return True if the event text (title, or title + description) looks
    like a music/tribute act. Pass in as much surrounding text as is
    available — many tribute acts are just a band name (e.g. "The
    Counterfeit Sixties") with no obvious music keyword in the title alone,
    so callers should include any short description/subtitle text too."""
    return bool(_MUSIC_KEYWORDS.search(text))


# Phrases identifying non-gig posts (recurring deals/classes) that show up
# mixed in with real events on Revolución de Cuba's Norwich listing.
_NON_GIG = re.compile(
    r"\b(salsa\s+class(es)?|cuban\s+salsa|tapas|happy\s+hour|brunch|"
    r"lunch\s+deal|industria|good\s+sunday|good\s+time)\b",
    re.IGNORECASE,
)


def scrape_madder_market(driver, log) -> list[dict]:
    """
    Madder Market Theatre — Spektrix booking system (Selenium).
    URL: https://booking.maddermarket.co.uk/Events

    Only include events whose title matches music-related keywords
    (tribute, band, music, concert, etc.) to filter out plays/comedy.

    Card structure (MUI):
        h2.MuiTypography-root  → event title
        p  (first date-like)   → date text (e.g. "Fri 05 June 2026")
        a[href*="EventId"]     → "More Info" link
    """
    VENUE    = "Madder Market Theatre"
    BASE_URL = "https://booking.maddermarket.co.uk"
    URL      = f"{BASE_URL}/Events"

    log(f"\n{'─'*48}", "dim")
    log(f"  Scraping {VENUE}", "plain")
    log(f"{'─'*48}", "dim")

    events = []
    try:
        driver.get(URL)
        # Wait for MUI card grid to load
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "div.MuiCard-root, div.EventItem")
            )
        )
        time.sleep(3)

        # Scroll to load any lazy-rendered cards
        for _ in range(5):
            driver.execute_script("window.scrollBy(0, 800);")
            time.sleep(1)

        soup  = BeautifulSoup(driver.page_source, "lxml")
        cards = soup.find_all("div", class_=re.compile(r"MuiCard-root|EventItem", re.I))
        log(f"  Found {len(cards)} card(s)", "dim")

        for card in cards:
            try:
                # Title — h2 inside the card
                h2 = card.find("h2")
                if not h2:
                    continue
                title = " ".join(h2.get_text().split())
                if not title:
                    continue

                # Filter: only keep music-related events. Match against the
                # whole card's text (title + any subtitle/description), not
                # just the title — lots of tribute acts are just a band name
                # ("The Counterfeit Sixties", "Legends of Motown") with no
                # music keyword in the title itself.
                card_text = card.get_text(" ", strip=True)
                if not _is_music_event(card_text):
                    log(f"  –  Skipped (non-music): {title}", "dim")
                    continue

                # Date — <p style="text-transform: capitalize;"> contains "Fri 05 June 2026"
                # Fall back to any <p> with a month name if the style attribute isn't present.
                date_str = None
                for p in card.find_all("p"):
                    style = p.get("style", "")
                    raw   = p.get_text(strip=True)
                    if "text-transform" in style and re.search(
                            r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b", raw, re.I):
                        date_str = _parse_date(raw)
                        if date_str:
                            break
                if not date_str:
                    for p in card.find_all("p"):
                        raw = p.get_text(strip=True)
                        if re.search(r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b", raw, re.I):
                            date_str = _parse_date(raw)
                            if date_str:
                                break
                if not date_str:
                    log(f"  ⚠  No date for: {title}", "warn")
                    continue

                # Link — "More Info" or "Book Now" anchor
                link = URL
                a_tag = card.find("a", href=re.compile(r"EventId", re.I))
                if a_tag:
                    href = a_tag.get("href", "")
                    link = href if href.startswith("http") else BASE_URL + href

                events.append({"venue": VENUE, "event_name": title,
                               "date": date_str, "url": link})
                log(f"  ✓  {date_str}  {title}", "ok")
            except Exception as e:
                log(f"  ⚠  Card error: {e}", "warn")

    except Exception as e:
        log(f"  ✗  {VENUE} failed: {e}", "err")

    log(f"  → {len(events)} Madder Market music event(s)", "dim")
    return events


def scrape_the_halls(session, log) -> list[dict]:
    """
    The Halls Norwich — Norwich City Council events listing (static HTML).
    URL: https://www.norwich.gov.uk/thehalls/whats

    Each event is an <article class="box-link ..."> with:
        div.box-link__title > a  → event title + relative URL
        div.field (plain text)   → date text (e.g. "Thursday 6 August 2026")

    Only events with music-related keywords in the title are kept.
    """
    VENUE    = "The Halls"
    BASE_URL = "https://www.norwich.gov.uk"
    URL      = f"{BASE_URL}/thehalls/whats"

    log(f"\n{'─'*48}", "dim")
    log(f"  Scraping {VENUE}", "plain")
    log(f"{'─'*48}", "dim")

    events = []
    try:
        resp = session.get(URL, timeout=15)
        resp.raise_for_status()
        soup     = BeautifulSoup(resp.text, "lxml")
        articles = soup.find_all("article", class_=re.compile(r"box-link", re.I))
        log(f"  Found {len(articles)} article(s)", "dim")

        for art in articles:
            try:
                # Title + link
                title_div = art.find("div", class_="box-link__title")
                if not title_div:
                    continue
                a_tag = title_div.find("a", href=True)
                if not a_tag:
                    continue
                title = " ".join(a_tag.get_text().split())
                href  = a_tag["href"]
                link  = href if href.startswith("http") else BASE_URL + href
                if not title:
                    continue

                # Filter: only keep music-related events. Match against the
                # whole article's text, not just the title (see Madder
                # Market scraper for why).
                article_text = art.get_text(" ", strip=True)
                if not _is_music_event(article_text):
                    log(f"  –  Skipped (non-music): {title}", "dim")
                    continue

                # Date — div.field--name-localgov-text-plain contains "Saturday 6 June 2026\n7.30pm"
                date_str = None
                date_field = art.find("div", class_=re.compile(r"field--name-localgov-text-plain", re.I))
                if date_field:
                    # getText with separator so <br> becomes a space; take first line (the date)
                    raw = date_field.get_text(" ", strip=True)
                    # raw may be "Saturday 6 June 2026 7.30pm" — dateutil handles trailing time fine
                    date_str = _parse_date(raw)
                if not date_str:
                    # Fallback: any div whose class contains "field" and has a day/month word
                    for field in art.find_all("div", class_=re.compile(r"field", re.I)):
                        raw = field.get_text(" ", strip=True)
                        if re.search(r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|"
                                     r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b", raw, re.I):
                            date_str = _parse_date(raw)
                            if date_str:
                                break
                if not date_str:
                    log(f"  ⚠  No date for: {title}", "warn")
                    continue

                events.append({"venue": VENUE, "event_name": title,
                               "date": date_str, "url": link})
                log(f"  ✓  {date_str}  {title}", "ok")
            except Exception as e:
                log(f"  ⚠  Article error: {e}", "warn")

    except Exception as e:
        log(f"  ✗  {VENUE} failed: {e}", "err")

    log(f"  → {len(events)} The Halls event(s)", "dim")
    return events


def scrape_hangar_fixr(session, log) -> list[dict]:
    """
    The Hangar (Norwich) — fixr.co venue page (server-rendered Next.js).
    URL: https://fixr.co/venue/the-hangar-norwich-26779

    Fixr's venue page is server-rendered, so a plain requests.get() returns
    the full event list without needing Selenium. Each event is a single
    <a href="/event/..."> card, but Fixr doesn't mark up title/date/venue
    as separate elements in any stable way — the card's combined text just
    concatenates the event title (it appears twice back-to-back, once from
    an image alt attribute and once from a heading), then a
    "Weekday D Month" date, then "<Venue>, <Town>". So instead of relying
    on specific child tags, this pulls everything from the anchor's full
    text via regex, which should survive minor markup changes.
    """
    VENUE = "The Hangar"
    URL   = "https://fixr.co/venue/the-hangar-norwich-26779"

    log(f"\n{'─'*48}", "dim")
    log(f"  Scraping {VENUE} (Fixr)", "plain")
    log(f"{'─'*48}", "dim")

    _DATE_RE = re.compile(
        r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\s+\d{1,2}\s+"
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b",
        re.IGNORECASE,
    )

    events = []
    try:
        resp = session.get(URL, timeout=15)
        resp.raise_for_status()
        soup  = BeautifulSoup(resp.text, "lxml")
        links = soup.find_all("a", href=re.compile(r"/event/"))
        log(f"  Found {len(links)} event link(s)", "dim")

        seen_urls = set()
        for a_tag in links:
            try:
                href = a_tag["href"]
                link = href if href.startswith("http") else urljoin(URL, href)
                if link in seen_urls:
                    continue

                text = " ".join(a_tag.get_text(" ", strip=True).split())
                if not text:
                    continue

                m = _DATE_RE.search(text)
                if not m:
                    log(f"  ⚠  No date found in card text: {text[:60]}", "warn")
                    continue

                raw_date   = m.group(0)
                title_part = text[:m.start()].strip()
                if not title_part:
                    continue

                # Title is typically duplicated back-to-back in the card's
                # combined text (once from an <img alt>, once from a
                # heading), e.g. "Some Gig Title Some Gig Title" — collapse
                # that down to a single "Some Gig Title".
                dup_match = re.match(r"^(.+?)\s+\1$", title_part)
                title = dup_match.group(1).strip() if dup_match else title_part.strip()

                if not title:
                    continue

                date_str = _parse_date(raw_date)
                if not date_str:
                    log(f"  ⚠  Could not parse date '{raw_date}' for: {title}", "warn")
                    continue

                seen_urls.add(link)
                events.append({"venue": VENUE, "event_name": title,
                               "date": date_str, "url": link})
                log(f"  ✓  {date_str}  {title}", "ok")
            except Exception as e:
                log(f"  ⚠  Card error: {e}", "warn")

    except Exception as e:
        log(f"  ✗  {VENUE} failed: {e}", "err")

    log(f"  → {len(events)} {VENUE} event(s)", "dim")
    return events


def scrape_revolucion_de_cuba(session, log) -> list[dict]:
    """
    Revolución de Cuba, Norwich — live music/DJ nights (requests + regex).
    URL: https://www.revoluciondecuba.com/events/?location=Norwich&type=Live+Music

    Two quirks this scraper works around:

    1. The ?location=/?type= query params only drive client-side JS
       filtering — a plain requests.get() ignores them and returns every
       event across every Revolución de Cuba bar in the country. So this
       scraper fetches the whole listing, then keeps only "View Event"
       links whose URL contains "/events/norwich/" (the location is baked
       into the URL itself, so this is reliable regardless of the page's
       CSS/JS).

    2. The listing page never shows a date at all — only the event's own
       page does, as a heading like "From 28th Aug 2026 to 30th Aug 2026".
       For a single one-off event that's just the one day repeated; for a
       recurring event (checked directly: a "Barney Holmes!" event whose
       description still lists four dates going back to April) that
       heading only shows the *next upcoming* date — the site itself does
       the "what's coming up" filtering, so this scraper just takes the
       first date in that range and doesn't need to parse the recurring
       schedule out of the description text. This does mean one extra
       page fetch per Norwich event, but there are usually only a
       handful.

    The listing also mixes in non-gig posts under "Norwich" (weekly salsa
    classes, the Sunday tapas deal, Happy Hour, etc.) which aren't really
    "live music" in the way this site cares about. Unlike the Madder
    Market/The Halls scrapers, this doesn't require a music-keyword match
    to keep an event — RDC gig titles are often just an artist name
    ("Barney Holmes!", "Mambo Kings") with no music-related word anywhere
    on the page, so requiring one would drop real gigs. Instead it
    excludes by matching known non-gig phrases (salsa class, tapas, happy
    hour, etc.) and keeps everything else.
    """
    VENUE = "Revolucion de Cuba"
    LISTING_URL = "https://www.revoluciondecuba.com/events/?location=Norwich&type=Live+Music"

    log(f"\n{'─'*48}", "dim")
    log(f"  Scraping {VENUE}", "plain")
    log(f"{'─'*48}", "dim")

    events = []
    try:
        resp = session.get(LISTING_URL, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        norwich_links = soup.find_all("a", href=re.compile(r"/events/norwich/\d+/"))
        # De-duplicate by resolved URL — a card can contain more than one
        # anchor pointing at the same event (e.g. the image is often also
        # a link).
        by_url = {}
        for a in norwich_links:
            url = urljoin(LISTING_URL, a["href"])
            by_url.setdefault(url, a)
        log(f"  Found {len(by_url)} Norwich event link(s) on the listing page", "dim")

        for event_url, a_tag in by_url.items():
            try:
                # Title — nearest heading above the "View Event" link, within its card.
                title = ""
                container = a_tag
                for _ in range(5):
                    container = container.find_parent(["article", "div", "li"])
                    if container is None:
                        break
                    heading = container.find(["h1", "h2", "h3", "h4", "h5"])
                    if heading and heading.get_text(strip=True):
                        title = heading.get_text(strip=True)
                        break
                if not title:
                    log(f"  ⚠  Could not find a title for {event_url}", "warn")
                    continue

                # The date only lives on the event's own page.
                detail_resp = session.get(event_url, timeout=15)
                detail_soup = BeautifulSoup(detail_resp.text, "lxml")

                date_tag = None
                for tag in detail_soup.find_all(["h4", "h5", "h6"]):
                    if re.search(r"\bfrom\b.*\bto\b", tag.get_text(strip=True), re.IGNORECASE):
                        date_tag = tag
                        break
                if not date_tag:
                    log(f"  ⚠  No date range found for: {title}", "warn")
                    continue

                m = re.search(r"from\s+(.+?)\s+to\s+", date_tag.get_text(strip=True), re.IGNORECASE)
                date_str = _parse_date(m.group(1).strip()) if m else None
                if not date_str:
                    log(f"  ⚠  Could not parse date '{date_tag.get_text(strip=True)}' for: {title}", "warn")
                    continue

                # Description — the paragraph right after the date heading.
                desc_tag = date_tag.find_next("p")
                description = desc_tag.get_text(" ", strip=True) if desc_tag else ""
                combined = f"{title} {description}"

                if _NON_GIG.search(combined):
                    log(f"  –  Skipped (deal/class, not a gig): {title}", "dim")
                    continue

                events.append({"venue": VENUE, "event_name": title,
                               "date": date_str, "url": event_url})
                log(f"  ✓  {date_str}  {title}", "ok")

                time.sleep(0.5)  # be polite between per-event detail-page requests

            except Exception as e:
                log(f"  ⚠  Event error ({event_url}): {e}", "warn")

    except Exception as e:
        log(f"  ✗  {VENUE} failed: {e}", "err")

    log(f"  → {len(events)} {VENUE} event(s)", "dim")
    return events


def load_manual_events(log) -> list[dict]:
    """
    Load hand-added events from manual_events.csv, which lives in the same
    repo folder as this script (alongside add_manual_event.py). This file
    is never written to by the scraper — only appended to by
    add_manual_event.py — so entries persist across every daily run.

    Events whose date has already passed are silently dropped here, so
    nobody needs to come back and delete old entries by hand.

    Rows are tagged with "_manual": True so the keyword filter further
    down (screening/bingo/quiz/etc.) skips them — an event someone
    deliberately typed in shouldn't get auto-filtered because its title
    happens to contain one of those words. They still go through the
    normal duplicate check, in case a manually-added event later shows up
    from a scraper too.
    """
    manual_file = Path(__file__).parent / "manual_events.csv"

    log(f"\n{'─'*48}", "dim")
    log(f"  Loading manual events", "plain")
    log(f"{'─'*48}", "dim")

    if not manual_file.exists():
        log(f"  No manual_events.csv found — skipping", "dim")
        return []

    today = datetime.now().strftime("%Y-%m-%d")
    events = []
    try:
        with open(manual_file, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                venue      = (row.get("venue") or "").strip()
                event_name = (row.get("event_name") or "").strip()
                date       = (row.get("date") or "").strip()
                url        = (row.get("url") or "").strip()

                if not venue or not event_name or not date:
                    log(f"  ⚠  Skipping incomplete row: {row}", "warn")
                    continue

                if date < today:
                    log(f"  –  Skipping past event: {date}  {event_name}", "dim")
                    continue

                events.append({
                    "venue":      venue,
                    "event_name": event_name,
                    "date":       date,
                    "url":        url,
                    "_manual":    True,
                })
                log(f"  ✓  {date}  {venue} — {event_name}", "ok")

    except Exception as e:
        log(f"  ✗  Failed to read manual_events.csv: {e}", "err")

    log(f"  → {len(events)} manual event(s)", "dim")
    return events


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_all_scrapers(log, on_complete, stop_flag: threading.Event):
    """
    Runs all venue scrapers in sequence, writes the CSV, then calls
    on_complete(csv_path_or_None).  Designed to run in a daemon thread.
    """
    def _check():
        return stop_flag.is_set()

    if not SELENIUM_OK:
        log("  ✗  Selenium not installed — run 'Install Deps' first.", "err")
        on_complete(None)
        return
    if not DATEUTIL_OK:
        log("  ✗  python-dateutil not installed — run 'Install Deps' first.", "err")
        on_complete(None)
        return

    events = []
    driver = None
    session = None
    if REQUESTS_OK:
        session = requests.Session()
        session.headers["User-Agent"] = "Mozilla/5.0 Norwich-Gigs/1.0"

    try:
        log("🌐  Starting headless Chrome…", "dim")
        driver = _setup_driver()
        log("  ✓  Chrome ready", "ok")

        if not _check():
            events += scrape_space_studios(driver, log)

        # Selenium scrapers (JS-rendered sites)
        for fn in [scrape_gonzos, scrape_holloway, scrape_epic_studios,
                   scrape_brickmakers_website, scrape_madder_market]:
            if _check():
                break
            events += fn(driver, log)

        # Session-based (static HTML) scrapers
        # NOTE: scrape_brickmakers_gig_guide pulls from norfolkgigguide.com — active ✓
        if session:
            for fn in [
                scrape_norwich_arts_centre,
                scrape_waterfront,
                scrape_lcr,
                scrape_voodoos,
                scrape_dead_wax,
                scrape_brickmakers_gig_guide,
                scrape_the_halls,
                scrape_hangar_fixr,
                scrape_revolucion_de_cuba,
            ]:
                if _check():
                    break
                events += fn(session, log)

    except Exception as e:
        log(f"\n  ✗  Unexpected error: {e}", "err")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    # Merge in hand-added events from manual_events.csv (see load_manual_events
    # for why this lives outside the scraped `events` list up to this point).
    events += load_manual_events(log)

    if stop_flag.is_set():
        log("\n⏹  Stopped — partial results discarded.", "warn")
        on_complete(None)
        return

    if not events:
        log("\n⚠  No events collected.", "warn")
        on_complete(None)
        return

    # Sort, dedupe, filter, normalise venue names, then write CSV
    events.sort(key=lambda e: e.get("date", ""))

    # Drop duplicate events (same venue + date, similar title) — happens
    # when a venue like Brickmakers is scraped from more than one source
    before_dedupe = len(events)
    events = dedupe_events(events)
    removed_dupes = before_dedupe - len(events)
    if removed_dupes:
        log(f"  ⚑  Removed {removed_dupes} duplicate event(s)", "warn")

    # Drop events matching the keyword/phone-number filter (manual events are
    # exempt — see load_manual_events)
    before_filter = len(events)
    events = [e for e in events
              if e.get("_manual") or not should_filter_event(e.get("event_name", ""))]
    dropped = before_filter - len(events)
    if dropped:
        log(f"  ⚑  Filtered out {dropped} event(s) (screening/bingo/quiz/cribbage/private event/phone)", "warn")

    # Apply venue aliases so the CSV already has short names
    for e in events:
        e["venue"]      = shorten_venue(e.get("venue", ""), DEFAULT_ALIASES)
        e["event_name"] = normalise_title(e.get("event_name", ""))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(events)

    # Second, flat-format CSV: one row per event, "Aug 16th,Venue,Event Name"
    # (no header, no url column, date shown as "Mon Dth" via format_date()).
    with open(CSV_FLAT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for e in events:
            display_date = format_date(e.get("date", "")) or e.get("date", "")
            writer.writerow([display_date, e.get("venue", ""), e.get("event_name", "")])

    log(f"\n✅  {len(events)} event(s) written to {CSV_FILE}", "ok")
    log(f"✅  {len(events)} event(s) written to {CSV_FLAT_FILE}", "ok")
    on_complete(str(CSV_FILE))



# ═══════════════════════════════════════════════════════════════════════════════
# CONSOLE RUNNER (replaces the GUI's log box and Start button)
# ═══════════════════════════════════════════════════════════════════════════════

def console_log(msg: str, tag: str = "plain") -> None:
    """Print progress straight to the terminal instead of a GUI log box."""
    print(msg)


def main() -> int:
    result_path = {"csv": None}

    def on_complete(csv_path):
        result_path["csv"] = csv_path

    stop_flag = threading.Event()  # never set — headless run always runs to completion

    console_log("🌐  Norwich Gigs — headless scrape starting…")
    run_all_scrapers(console_log, on_complete, stop_flag)

    if not result_path["csv"]:
        console_log("\n✗  No CSV was produced — see errors/warnings above.")
        return 1

    console_log(f"\n✓  Done. CSV written to: {result_path['csv']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
