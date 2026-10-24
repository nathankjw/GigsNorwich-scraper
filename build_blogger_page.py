#!/usr/bin/env python3
"""
Builds the Norwich Gigs home page as plain HTML so the gig list is in the page
source (crawlable without JavaScript), then writes it to blogger/dist/.

Inputs : scraped_data/norwich_gigs.json, blogger/page_template.html,
         blogger/site_config.json (venue links / logos / names / month banners)
Outputs: blogger/dist/page_body.html  - paste/push this as the Blogger page body
         blogger/dist/page_meta.json  - title + description for the page

Usage:  python3 blogger/build_blogger_page.py [--today YYYY-MM-DD]
(run from the repo root; the workflow does this after csv_to_json.py)

The page body deliberately contains NO <html>/<head>/<title>/<meta>/<link
rel=canonical> - Google ignores those inside <body>. Set the title via the
Blogger page title (publish_to_blogger.py does this) and the description in
Blogger's settings (see blogger/README.md).
"""
import argparse
import html
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EVENTS_JSON = ROOT / "scraped_data" / "norwich_gigs.json"
TEMPLATE = HERE / "page_template.html"
CONFIG = HERE / "site_config.json"
DIST = HERE / "dist"

JSONLD_DAYS = 60   # how far ahead gigs are published as Event markup
SITE = "https://www.norwichgigs.com/"
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "June", "July", "Aug",
                "Sept", "Oct", "Nov", "Dec"]
DESCRIPTION = ("Every live music gig in Norwich this month in one list: Waterfront, "
               "LCR, Voodoo Daddys, Norwich Arts Centre and more. Search by artist, "
               "venue or date.")

# Venue pages that are "generic" listings; for these the old browser script
# guessed the specific event URL from the event name. Ported 1:1.
GENERIC_URLS = {
    "https://www.ueaticketbookings.co.uk/uea-lcr/",
    "https://www.ueaticketbookings.co.uk/adrian-flux-waterfront/",
    "https://www.voodoodaddysshowroom.co.uk/events/",
    "https://norwichartscentre.co.uk/event/category/music/",
    "https://www.deadwaxnorwich.pub/whats-on",
    "https://www.gonzostworoom.com/event/",
    "https://epic-tv.com/events",
}


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def ordinal(n: int) -> str:
    if 11 <= n <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def event_slug(name: str, venue: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"^[^\w\s]+", "", s).strip()
    s = re.sub(r"\(x\)\s*$", "", s, flags=re.I).strip()

    def dash(x, keep=r"a-z0-9\s\-"):
        x = re.sub(rf"[^{keep}]", "", x)
        x = re.sub(r"\s+", " ", x).strip().replace(" ", "-")
        return re.sub(r"-+", "-", x)

    if venue in ("Waterfront", "LCR"):
        s = s.replace("'", "")
        return dash(s, r"a-z0-9\s\-").rstrip("-")
    if venue == "Voodoos":
        s = re.split(r"\s+\+\s+", s)[0].strip()
        s = re.sub(r"[^a-z0-9\s&\-]", "", s)
        s = re.sub(r"\s*&\s*", "-", s)
        s = re.sub(r"\s+", " ", s).strip().replace(" ", "-")
        return re.sub(r"-+", "-", s).rstrip("-")
    if venue == "Dead Wax":
        s = re.sub(r"[^a-z0-9\s+\-]", "", s)
        s = re.sub(r"[\s+]+", "-", s)
        return re.sub(r"-+", "-", s).strip("-")
    if venue in ("Arts Centre", "Gonzos", "Epic"):
        return dash(s).strip("-")
    return s


def ticket_url(ev: dict, cfg: dict) -> str:
    venue = ev.get("venue", "")
    url = cfg["VENUE_LINKS"].get(venue) or ev.get("url") or "#"
    if url.rstrip("/") + "/" not in {u.rstrip("/") + "/" for u in GENERIC_URLS}:
        return url
    slug = event_slug(ev.get("event_name", ""), venue)
    patterns = {
        "Waterfront": "https://www.ueaticketbookings.co.uk/event/{}/",
        "LCR": "https://www.ueaticketbookings.co.uk/event/{}/",
        "Voodoos": "https://www.voodoodaddysshowroom.co.uk/event/{}/",
        "Arts Centre": "https://norwichartscentre.co.uk/event/{}/",
        "Dead Wax": "https://www.deadwaxnorwich.pub/whats-on/{}",
        "Gonzos": "https://www.gonzostworoom.com/event/{}/",
        "Epic": "https://epic-tv.com/events/event/{}",
    }
    return patterns[venue].format(slug) if venue in patterns and slug else url


def clean_name(raw: str):
    """-> (display name, soundcloud url or None, suppress_spotify flag)."""
    name = raw.strip()
    m = re.search(r"\(SC:(https?://[^)]+)\)\s*$", name, re.I)
    if m:
        return name[:m.start()].strip(), m.group(1), True
    if re.search(r"\(X\)\s*$", name, re.I):
        return re.sub(r"\(X\)\s*$", "", name, flags=re.I).strip(), None, True
    return name, None, False


def row_html(ev: dict, cfg: dict) -> str:
    y, m, d = (int(x) for x in ev["date"].split("-"))
    venue = ev.get("venue", "")
    name, sc_url, no_spotify = clean_name(ev.get("event_name", ""))
    price = (ev.get("price") or "").strip()
    price_cell = (f'<td class="price-cell">{esc(price)}</td>' if price
                  else '<td class="price-cell price-unknown">Check website</td>')
    logo = cfg["VENUE_LOGOS"].get(venue, "")
    if ev.get("image"):
        poster = (f'<td class="poster-cell"><img class="event-poster" src="{esc(ev["image"])}" '
                  f'alt="" loading="lazy"></td>')
    elif logo:
        poster = (f'<td class="poster-cell"><img class="event-poster is-logo" data-fallback="logo" '
                  f'src="{esc(logo)}" alt="" loading="lazy"></td>')
    else:
        poster = '<td class="poster-cell"><i class="fas fa-music"></i></td>'
    sc = (f' <a href="{esc(sc_url)}" class="soundcloud-link" target="_blank" title="Listen on '
          f'SoundCloud"><i class="fab fa-soundcloud"></i></a>') if sc_url else ""
    flag = ' data-no-spotify="1"' if no_spotify else ""
    return (
        f'<tr data-iso-date="{esc(ev["date"])}"{flag}>'
        f'{poster}'
        f'<td class="date-cell">{ordinal(d)} {MONTHS_SHORT[m - 1]}</td>'
        f'<td class="venue-cell">{esc(venue)}</td>'
        f'<td class="event-cell">{esc(name)}{sc}</td>'
        f'{price_cell}'
        f'<td class="actions-cell"><div class="row-actions"><a href="{esc(ticket_url(ev, cfg))}" '
        f'class="ticket-btn" aria-label="Get Tickets" title="Get tickets"><i class="fas fa-ticket-alt">'
        f'</i></a><button type="button" class="calendar-btn" aria-label="Add to calendar" '
        f'title="Add to calendar"><i class="fas fa-calendar-plus"></i></button></div></td></tr>'
    )


def divider_html(y: int, m: int, today: date, cfg: dict) -> str:
    if (y, m) == (today.year, today.month):  # no banner for the current month
        return ('<tr class="month-divider" data-suppress-banner="true" style="display:none;">'
                '<td colspan="6"></td></tr>')
    img = cfg["MONTH_DIVIDER_IMAGES"].get(str(m))
    if img:
        return ('<tr class="month-divider"><td colspan="6" style="padding:0;background:transparent;'
                'border-radius:15px;overflow:hidden;"><img src="%s" alt="%s %d" style="display:block;'
                'width:100%%;height:auto;border-radius:15px;" loading="lazy"></td></tr>'
                % (esc(img), MONTHS[m - 1], y))
    return f'<tr class="month-divider"><td colspan="6">{MONTHS_SHORT[m - 1]} {y}</td></tr>'


def offer(ev: dict, url: str):
    price = (ev.get("price") or "").strip()
    if not price:
        return None
    base = {"@type": "Offer", "priceCurrency": "GBP", "url": url,
            "availability": "https://schema.org/InStock"}
    if price.lower() == "free":
        return {**base, "price": "0"}
    nums = re.findall(r"\d+(?:\.\d+)?", price.replace(",", ""))
    if "£" not in price or not nums:
        return None
    if len(nums) == 1:
        return {**base, "price": nums[0]}
    return {"@type": "AggregateOffer", "priceCurrency": "GBP", "url": url,
            "lowPrice": min(nums, key=float), "highPrice": max(nums, key=float)}


def music_event(ev: dict, cfg: dict) -> dict:
    name, _, _ = clean_name(ev.get("event_name", ""))
    venue = ev.get("venue", "")
    url = ticket_url(ev, cfg)
    item = {
        "@type": "MusicEvent",
        "name": name,
        "startDate": ev["date"],
        "eventStatus": "https://schema.org/EventScheduled",
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "location": {
            "@type": "Place",
            "name": cfg["VENUE_NAMES"].get(venue, venue),
            "address": {"@type": "PostalAddress", "addressLocality": "Norwich",
                        "addressRegion": "Norfolk", "addressCountry": "GB"},
        },
    }
    if url != "#":
        item["url"] = url
    if ev.get("image"):
        item["image"] = ev["image"]
    o = offer(ev, url)
    if o:
        item["offers"] = o
    return item


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--today", help="override today's date (YYYY-MM-DD), for testing")
    args = ap.parse_args()
    today = (date.fromisoformat(args.today) if args.today
             else datetime.now(ZoneInfo("Europe/London")).date())

    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    events = json.loads(EVENTS_JSON.read_text(encoding="utf-8"))
    events = [e for e in events if re.fullmatch(r"\d{4}-\d{2}-\d{2}", e.get("date", ""))]
    events.sort(key=lambda e: e["date"])
    upcoming = [e for e in events if e["date"] >= today.isoformat()]
    shown = upcoming or events  # same fallback as the page script: all past -> show all
    if not shown:
        print("No events in JSON - refusing to build an empty page.", file=sys.stderr)
        return 1

    rows, last = [], None
    for ev in shown:
        y, m, _ = (int(x) for x in ev["date"].split("-"))
        if (y, m) != last:
            rows.append(divider_html(y, m, today, cfg))
            last = (y, m)
        rows.append(row_html(ev, cfg))

    # Structured data: next 60 days only (keeps the page small), and skip
    # postponed/cancelled gigs, whose listed date would be wrong.
    horizon = date.fromordinal(today.toordinal() + JSONLD_DAYS).isoformat()
    ld_events = [e for e in shown if e["date"] <= horizon
                 and not re.search(r"postponed|cancel+ed|rescheduled", e.get("event_name", ""), re.I)]
    jsonld = json.dumps({"@context": "https://schema.org",
                         "@graph": [music_event(e, cfg) for e in ld_events]},
                        ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    label = f"{MONTHS[today.month - 1]} {today.year}"
    venues = {e.get("venue", "") for e in shown}

    def js_json(d):
        return json.dumps(d, ensure_ascii=False, indent=4).replace("</", "<\\/")

    out = TEMPLATE.read_text(encoding="utf-8")
    for key, val in {
        "%%MONTH_LABEL%%": label,
        "%%EVENT_COUNT%%": str(len(shown)),
        "%%VENUE_COUNT%%": str(len(venues)),
        "%%ROWS%%": "\n".join(rows),
        "%%EVENTS_JSONLD%%": f'<script type="application/ld+json" id="eventsJsonLd">{jsonld}</script>',
        "%%VENUE_LOGOS_JSON%%": js_json(cfg["VENUE_LOGOS"]),
        "%%VENUE_NAMES_JSON%%": js_json(cfg["VENUE_NAMES"]),
    }.items():
        out = out.replace(key, val)
    assert "%%" not in out, "unreplaced placeholder"

    meta = {"title": f"Norwich Gigs {label} - Live Music Events & Concerts Guide",
            "description": DESCRIPTION, "canonical": SITE,
            "events": len(shown), "jsonld_events": len(ld_events), "built_for": today.isoformat()}
    DIST.mkdir(exist_ok=True)
    (DIST / "page_body.html").write_text(out, encoding="utf-8")
    (DIST / "page_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Built {len(shown)} events across {len(venues)} venues "
          f"({len(out) / 1024:.0f} KB) -> {DIST / 'page_body.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
