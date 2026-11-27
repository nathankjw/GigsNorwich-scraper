#!/usr/bin/env python3
"""
Builds a page for every gig - the Norwich Gigs equivalent of a Headfirst
event page - as a plain static website for GitHub Pages.

Inputs : scraped_data/norwich_gigs.json   (gigs + extra details from event_details.py)
         blogger/site_config.json         (venue names / logos / links, GIG_PAGES settings)
         gig_pages/style.css
Output : gig_pages/dist/
           index.html                     all upcoming gigs, by month
           gigs/<slug>/index.html         one page per gig
           gigs/<slug>/gig.ics            add-to-calendar file
           assets/style.css, sitemap.xml, robots.txt, 404.html

Usage (from the repo root):  python3 gig_pages/build_gig_pages.py [--today YYYY-MM-DD]

Links between pages are relative, so the site works both at
https://<user>.github.io/<repo>/ (before the custom domain is set up) and at
GIG_PAGES.site_url. Canonical links and the sitemap use site_url.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "blogger"))
from build_blogger_page import (  # noqa: E402  (shared with the home page build)
    MONTHS, assign_gig_slugs, blogger_img, build_home, clean_name, gig_key, offer, ordinal,
    poster_url, ticket_url,
)

EVENTS_JSON = ROOT / "scraped_data" / "norwich_gigs.json"
SITE_PAGES = ROOT / "site_pages"      # the site's other pages (About Us, Venues...), edit these
CONFIG = ROOT / "blogger" / "site_config.json"
CSS = HERE / "style.css"
FONTS_CSS = HERE / "fonts.css"
FONTS_DIR = HERE / "assets" / "fonts"
PAGE_CSS = ""  # style sheet put inside every page (set in main) - saves a request before first paint
DIST = HERE / "dist"

LONDON = ZoneInfo("Europe/London")
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
GA_ID = "G-DVR3H0T1TK"

# ── Pieces of the main site's Blogger theme, so the gig pages look the same ──
# (logo = the Header widget image; menu = the Main Menu link list; sidebar =
# the visible "About Us" and "Promote Your Gig" widgets.) Update here if the
# Blogger theme changes.
# The site logo, kept in gig_pages/assets (copied to /assets/ on the site).
LOGO = "/assets/norwichgigs-logo.png"   # full size, used for social media previews
LOGO_IMG = ('<img src="/assets/norwichgigs-logo-240.webp" '
            'srcset="/assets/norwichgigs-logo-240.webp 240w, /assets/norwichgigs-logo-360.webp 360w, '
            '/assets/norwichgigs-logo-480.webp 480w" sizes="(max-width: 980px) 155px, 262px" '
            'alt="Norwich Gigs - live music in Norwich" width="480" height="220">')
MENU = [("Home", ""), ("Venues", "p/music-venues-in-our-city.html"), ("About Us", "p/aboutus.html"),
        ("Archive", "archive/"),
        ("Advertisements", "p/advertise.html"), ("List Your Event", "p/list-your-gig.html")]
BOTTOM_NAV = [("fa-home", "Home", ""), ("fa-map-marker-alt", "Venues", "p/music-venues-in-our-city.html"),
              ("fa-search", "Search", "#searchInput"), ("fa-bullhorn", "Ads", "p/advertise.html"),
              ("fa-info-circle", "About Us", "p/aboutus.html")]
TEAM_PHOTOS = [
    ("Nathan", "https://blogger.googleusercontent.com/img/b/R29vZ2xl/AVvXsEj50-D7KcvkWMvOC1BK3FxYGLm2zvP2y_paEv2C"
               "PE4T0Pu8FL3a7sdIupY4JD2X7oiFtqNvm-JDgr-AeJGaPHLYyd7uuyl-Y5h5dViEupdGY7IpCpV_XwOZ7kmIDjszbWRjDvaX1vd"
               "rKEEPtnY1_gZdk_MuVAdgVj3bK1YbByVJgPkoSYiYv49WKdlB3kQ/s320/NW.webp"),
    ("Trish", "https://blogger.googleusercontent.com/img/b/R29vZ2xl/AVvXsEjcGdC2ZCX8ZHBzLsdwDdpVVcanHLojdjKrDef5"
              "GLkVI2RIgSYMWoXfU1I76r1AMO_7wsdYXKavtrTNTdnKXy28zZPchO_9lLg4Dc-HOv6tf0lepKNZCkAUuzQOnmAZODB2FOCPl"
              "f9R9d239w9Nvks-3CBSh-m1EOnfw3YBIDKt4ZCivUvOvntOgZbMK9U/s320/TD.webp"),
]
ADVERTISE_FORM = ("https://docs.google.com/forms/d/e/1FAIpQLSewJwaNZmXlq1Q2b7zS7HK0mOzwpFNkPXeN0aCtPpUuuAmKnw/"
                  "viewform?usp=sharing&ouid=104642703046795432590")
STAR = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">'
        '<path d="M12 2l2.9 6.6L22 9.3l-5 4.9 1.2 7.1L12 17.8l-6.2 3.5L7 14.2 2 9.3l7.1-.7z"/></svg>')


def site_link(home_url: str, path: str) -> str:
    """A page on the main (Blogger) site: "" -> home, "p/x.html", "#searchInput"."""
    return path if path.startswith("http") else home_url.rstrip("/") + "/" + path


def header_html(home_url: str) -> str:
    menu = "".join(f'<li><a href="{esc(site_link(home_url, p))}">{esc(t)}</a></li>' for t, p in MENU)
    return f"""<header class="site-header">
  <a class="logo" href="{esc(home_url)}">{LOGO_IMG}</a>
</header>
<nav class="menu-bar" aria-label="Main menu"><ul>{menu}</ul></nav>"""


def sidebar_html(home_url: str) -> str:
    photos = "".join(f'<img src="{esc(src)}" alt="{esc(name)}" loading="lazy">' for name, src in TEAM_PHOTOS)
    perks = "".join(f"<li>{STAR}{esc(t)}</li>" for t in
                    ("Large monthly reach", "Engaged target audience", "Website exposure", "Social media posts"))
    return f"""<aside class="sidebar">
  <div class="widget about-widget">
    <span class="badge">About Us</span>
    <p>Norwich Gig List is a social enterprise highlighting the fantastic music talent across the fine city -
    every event and venue, all weighted the same.</p>
    <div class="thumbs">{photos}</div>
    <a class="cta" href="{esc(site_link(home_url, 'p/aboutus.html'))}">Meet the team</a>
  </div>
  <div class="widget promote-widget">
    <span class="badge tilt-right">Promote Your Gig</span>
    <p class="sub">Get featured in Norwich's best music guide!</p>
    <div class="perks"><h3>Why advertise with us?</h3><ul>{perks}</ul></div>
    <a class="cta block" href="{esc(ADVERTISE_FORM)}" rel="noopener">Book Featured Advert</a>
  </div>
</aside>"""


def bottom_nav_html(home_url: str, index_href: str) -> str:
    items = []
    for icon, label, path in BOTTOM_NAV:
        items.append(f'<a href="{esc(site_link(home_url, path))}"><i class="fas {icon}"></i><span>{esc(label)}</span></a>')
    return f'<nav class="bottom-nav" aria-label="Quick navigation">{"".join(items)}</nav>'

CANCELLED_RE = re.compile(r"postponed|cancel+ed|rescheduled", re.I)

# Fonts and icons are served from this site (assets/fonts, see fonts.css), not
# Google Fonts / cdnjs: fewer connections and ~250 KB less to download.
HEAD_LINKS = """<link rel="icon" type="image/png" href="/favicon-96x96.png" sizes="96x96">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="shortcut icon" href="/favicon.ico">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<link rel="manifest" href="/site.webmanifest">
<meta name="theme-color" content="#ffffff">
<link rel="preconnect" href="https://blogger.googleusercontent.com">
<link rel="preconnect" href="https://wsrv.nl">"""
# Lines in the home page template that loaded fonts from elsewhere.
THIRD_PARTY_FONT_RE = re.compile(r"fonts\.googleapis\.com|fonts\.gstatic\.com|font-awesome")

GA = """<script>
window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}
gtag('js',new Date());gtag('config','%GA_ID%');
// The analytics script itself waits until the page has finished loading, so it
// doesn't compete with the posters and fonts. Anything recorded before then is queued.
window.addEventListener('load',function(){setTimeout(function(){
  var s=document.createElement('script');s.async=true;
  s.src='https://www.googletagmanager.com/gtag/js?id=%GA_ID%';document.head.appendChild(s);
},1000);});
</script>""".replace("%GA_ID%", GA_ID)


def esc(s) -> str:
    return html.escape(str(s), quote=True)


# ── Dates and times ───────────────────────────────────────────────────────────

def parse_date(iso: str) -> date:
    return date.fromisoformat(iso)


def long_date(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]} {ordinal(d.day)} {MONTHS[d.month - 1]} {d.year}"


def short_date(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()][:3]} {d.day} {MONTHS_ABBR[d.month - 1]}"


def nice_time(hhmm: str) -> str:
    """'19:30' -> '7.30pm', '20:00' -> '8pm', '00:00' -> 'midnight'."""
    h, m = int(hhmm[:2]), int(hhmm[3:])
    if (h, m) == (0, 0):
        return "midnight"
    if (h, m) == (12, 0):
        return "noon"
    suffix = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12}{suffix}" if m == 0 else f"{h12}.{m:02d}{suffix}"


def local_dt(d: date, hhmm: str, after: str | None = None) -> datetime:
    """A London date-time. If `after` is given and this time is earlier
    (an end time past midnight), it's the next day."""
    day = d
    if after and hhmm < after:
        day = d + timedelta(days=1)
    h, m = int(hhmm[:2]), int(hhmm[3:])
    return datetime(day.year, day.month, day.day, h, m, tzinfo=LONDON)


# ── Bits of a gig ─────────────────────────────────────────────────────────────

def spotify_url(name: str) -> str:
    """Same rule as the home page: first act, without 'X presents'."""
    cleaned = re.sub(r"^.+?\s+presents\s+", "", name, flags=re.I)
    first = re.split(r"\+|\bwith\b", cleaned, flags=re.I)[0].strip() or name
    return f"https://open.spotify.com/search/{quote(first)}"


def title_acts(name: str) -> list[str]:
    """Acts named in the title: "A + B + C" -> [A, B, C] (without "X presents")."""
    cleaned = re.sub(r"^.+?\s+presents\s+", "", name, flags=re.I)
    acts = [a.strip(" -–") for a in re.split(r"\s\+\s|\s+with\s+", cleaned, flags=re.I)]
    return [a for a in acts if a][:8] or [name]


def poster_src(ev: dict, cfg: dict) -> tuple[str, bool]:
    if ev.get("image"):
        return ev["image"], False
    logo = cfg["VENUE_LOGOS"].get(ev.get("venue", ""), "")
    return logo, bool(logo)


def venue_name(ev: dict, cfg: dict) -> str:
    v = ev.get("venue", "")
    return cfg["VENUE_NAMES"].get(v, v)


def support_acts(ev: dict) -> list[str]:
    acts = ev.get("support") or []
    if isinstance(acts, str):
        acts = [a.strip() for a in acts.split(",") if a.strip()]
    name_l = ev.get("event_name", "").lower()
    # Don't repeat acts already in the gig's title ("A + B + C").
    return [a for a in acts if a.lower() not in name_l]


# ── Calendar file ─────────────────────────────────────────────────────────────

def _ics_text(s: str) -> str:
    return (str(s).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line: str) -> str:
    parts = []
    while len(line) > 72:
        parts.append(line[:72])
        line = " " + line[72:]
    parts.append(line)
    return "\r\n".join(parts)


def build_ics(ev: dict, cfg: dict, page_url: str, tickets: str) -> str:
    name, _, _ = clean_name(ev.get("event_name", ""))
    d = parse_date(ev["date"])
    venue = venue_name(ev, cfg)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    start_t = ev.get("time") or ev.get("doors")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Norwich Gigs//Gig Pages//EN",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "BEGIN:VEVENT",
             f"UID:{ev['date'].replace('-', '')}-{page_url.rstrip('/').rsplit('/', 1)[-1]}@norwichgigs.com",
             f"DTSTAMP:{stamp}"]
    if start_t:
        start = local_dt(d, start_t)
        end = (local_dt(d, ev["end"], after=start_t) if ev.get("end")
               else start + timedelta(hours=3))
        utc = lambda x: x.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: E731
        lines += [f"DTSTART:{utc(start)}", f"DTEND:{utc(end)}"]
    else:
        lines += [f"DTSTART;VALUE=DATE:{d:%Y%m%d}",
                  f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}"]
    desc = [f"{name} at {venue}"]
    if ev.get("doors"):
        desc.append(f"Doors {nice_time(ev['doors'])}")
    if ev.get("price"):
        desc.append(f"Price: {ev['price']}")
    desc.append(f"Gig info: {page_url}")
    if tickets and tickets != "#":
        desc.append(f"Tickets: {tickets}")
    desc.append("Please check times with the venue.")
    location = venue if re.search("norwich", venue, re.I) else f"{venue}, Norwich"
    lines += [f"SUMMARY:{_ics_text(name)}", f"LOCATION:{_ics_text(location)}",
              f"DESCRIPTION:{_ics_text(chr(10).join(desc))}", f"URL:{page_url}",
              "STATUS:CONFIRMED", "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"


# ── Structured data ───────────────────────────────────────────────────────────

def music_event_ld(ev: dict, cfg: dict, page_url: str, tickets: str) -> dict:
    name, _, _ = clean_name(ev.get("event_name", ""))
    d = parse_date(ev["date"])
    item = {
        "@context": "https://schema.org",
        "@type": "MusicEvent",
        "name": name,
        "url": page_url,
        "startDate": (local_dt(d, ev["time"]).isoformat() if ev.get("time") else ev["date"]),
        "eventStatus": ("https://schema.org/EventPostponed" if CANCELLED_RE.search(name)
                        else "https://schema.org/EventScheduled"),
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "location": {
            "@type": "Place",
            "name": venue_name(ev, cfg),
            "address": {"@type": "PostalAddress", "addressLocality": "Norwich",
                        "addressRegion": "Norfolk", "addressCountry": "GB"},
        },
        "performer": [{"@type": "PerformingGroup", "name": n}
                      for n in title_acts(name) + support_acts(ev)],
    }
    if ev.get("doors"):
        item["doorTime"] = local_dt(d, ev["doors"]).isoformat()
    if ev.get("end") and ev.get("time"):
        item["endDate"] = local_dt(d, ev["end"], after=ev["time"]).isoformat()
    if ev.get("image"):
        item["image"] = ev["image"]
    if ev.get("description"):
        item["description"] = ev["description"]
    if ev.get("age"):
        item["typicalAgeRange"] = ev["age"].replace("+", "-") if ev["age"] != "All ages" else "0-"
    o = offer(ev, tickets if tickets != "#" else page_url)
    if o:
        item["offers"] = o
    return item


# ── HTML ──────────────────────────────────────────────────────────────────────

def page_shell(*, title: str, description: str, canonical: str, body: str,
               og_image: str = "", extra_head: str = "", noindex: bool = False,
               home_url: str = "/", index_href: str = "/gigs/", crumbs: bool = True,
               analytics: bool = True, bottom_nav: bool = True, css: str = "/assets/style.css") -> str:
    """Every page of the site: logo header, menu bar, main column + sidebar,
    footer and (on phones) the bottom bar. The home page turns off the parts its
    own template already has (analytics tag, bottom bar)."""
    og_img = (f'<meta property="og:image" content="{esc(og_image)}">\n'
              f'<meta name="twitter:card" content="summary_large_image">') if og_image else \
        '<meta name="twitter:card" content="summary">'
    robots = '<meta name="robots" content="noindex">\n' if noindex else ""
    return f"""<!DOCTYPE html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
{robots}<link rel="canonical" href="{esc(canonical)}">
<meta property="og:site_name" content="Norwich Gigs">
<meta property="og:type" content="website">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{esc(canonical)}">
{og_img}
{HEAD_LINKS}
{f"<style>{PAGE_CSS}</style>" if PAGE_CSS else f'<link rel="stylesheet" href="{esc(css)}">'}
{extra_head}
{GA if analytics else ""}
</head>
<body>
{header_html(home_url)}
<div class="layout">
<main class="main-col">
{crumbs_html(home_url, index_href) if crumbs else ""}
{body}
</main>
{sidebar_html(home_url)}
</div>
<footer class="site-footer">
  <p>Gig details come from venues and promoters and can change. Please check with the venue before you go.
  Some ticket links are affiliate links.</p>
  <p>Copyright &copy; {date.today().year} <a href="{esc(home_url)}">Norwich Gigs</a> ·
  <a href="https://instagram.com/gigsnorwich" rel="noopener">Instagram</a> ·
  <a href="mailto:norwichgigs@gmail.com">norwichgigs@gmail.com</a></p>
</footer>
{bottom_nav_html(home_url, index_href) if bottom_nav else ""}
</body>
</html>
"""


def crumbs_html(home_url: str, index_href: str) -> str:
    return (f'<p class="crumbs"><a href="{esc(home_url)}">Home</a> <i class="fas fa-chevron-right"></i> '
            f'<a href="{esc(index_href)}">All gigs</a></p>')


def mini_card(ev: dict, cfg: dict, href: str) -> str:
    name, _, _ = clean_name(ev.get("event_name", ""))
    src, is_logo = poster_src(ev, cfg)
    logo = cfg["VENUE_LOGOS"].get(ev.get("venue", ""), "")
    shown = src if is_logo else poster_url(src, 160)
    img = (f'<img src="{esc(shown)}" alt="{esc(name if not is_logo else ev.get("venue", "") + " logo")}" loading="lazy" width="60" height="60"{" class=is-logo" if is_logo else ""} '
           f'data-orig="{esc("" if is_logo else src)}" data-logo="{esc(logo if not is_logo else "")}">'
           if src else '<i class="fas fa-music"></i>')
    return (f'<a class="mini-card" href="{esc(href)}"><span class="mini-poster">{img}</span>'
            f'<span class="mini-text"><span class="mini-date">{esc(short_date(parse_date(ev["date"])))}'
            f' · {esc(ev.get("venue", ""))}</span><span class="mini-name">{esc(name)}</span></span></a>')


def gig_page(ev: dict, cfg: dict, *, slug: str, site_url: str, home_url: str, today: date,
             same_venue: list, same_night: list, slug_of) -> tuple[str, str]:
    name, sc_url, no_spotify = clean_name(ev.get("event_name", ""))
    d = parse_date(ev["date"])
    venue = venue_name(ev, cfg)
    page_url = f"{site_url}/gigs/{slug}/"
    tickets = ticket_url(ev, cfg)
    is_past = d < today
    support = support_acts(ev)

    # Facts strip: only what we actually know.
    facts = []
    if ev.get("doors"):
        facts.append(("Doors", nice_time(ev["doors"])))
    if ev.get("time"):
        facts.append(("On stage" if ev.get("doors") else "Starts", nice_time(ev["time"])))
    if ev.get("end"):
        facts.append(("Ends", nice_time(ev["end"])))
    if ev.get("age"):
        facts.append(("Age", ev["age"]))
    facts.append(("Price", ev.get("price") or "See venue"))
    facts_html = "".join(f'<div class="fact"><span>{esc(k)}</span><strong>{esc(v)}</strong></div>'
                         for k, v in facts)

    src, is_logo = poster_src(ev, cfg)
    logo = cfg["VENUE_LOGOS"].get(ev.get("venue", ""), "")
    poster = (f'<img src="{esc(src if is_logo else poster_url(src, 900))}" alt="Poster for {esc(name)}"'
              f'{" class=is-logo" if is_logo else ""} width="450" height="450" '
              f'data-orig="{esc("" if is_logo else src)}" data-logo="{esc(logo if not is_logo else "")}" fetchpriority="high">'
              if src else '<i class="fas fa-music"></i>')

    buttons = []
    if tickets and tickets != "#" and not is_past:
        buttons.append(f'<a class="btn btn-tickets" href="{esc(tickets)}" rel="noopener" '
                       f'data-ga="ticket_click"><i class="fas fa-ticket-alt"></i> Get tickets</a>')
    if not is_past:
        buttons.append('<a class="btn btn-calendar" href="gig.ics" download '
                       'data-ga="calendar_click"><i class="fas fa-calendar-plus"></i> Add to calendar</a>')
    if sc_url:
        buttons.append(f'<a class="btn btn-listen sc" href="{esc(sc_url)}" rel="noopener">'
                       f'<i class="fab fa-soundcloud"></i> SoundCloud</a>')
    elif not no_spotify:
        buttons.append(f'<a class="btn btn-listen" href="{esc(spotify_url(name))}" rel="noopener">'
                       f'<i class="fab fa-spotify"></i> Listen</a>')

    about = ""
    if ev.get("description"):
        src_link = ev.get("url") or tickets
        more = (f' <a class="read-more" href="{esc(src_link)}" rel="noopener">Read more on the '
                f'{esc(venue)} website</a>') if src_link and src_link != "#" else ""
        about = (f'<section class="about"><h2>About this gig</h2>'
                 f'<p>{esc(ev["description"])}</p><p class="source">From the venue\'s listing.{more}</p></section>')

    support_html = (f'<p class="support">with {esc(", ".join(support[:-1]) + " and " + support[-1] if len(support) > 1 else support[0])}</p>'
                    if support else "")
    past_banner = ('<p class="past-banner">This gig has already happened. '
                   '<a href="/gigs/">See what\'s coming up</a></p>') if is_past else ""

    def related(title, items):
        if not items:
            return ""
        cards = "".join(mini_card(e, cfg, f"/gigs/{slug_of(e)}/") for e in items)
        return f'<section class="related"><h2>{esc(title)}</h2><div class="mini-grid">{cards}</div></section>'

    body = f"""<div class="gig">
{past_banner}
<div class="gig-top">
  <div class="poster{' no-image' if not src else ''}">{poster}</div>
  <div class="gig-info">
    <p class="gig-date"><time datetime="{esc(ev['date'])}">{esc(long_date(d))}</time></p>
    <h1>{esc(name)}</h1>
    {support_html}
    <p class="gig-venue"><i class="fas fa-map-marker-alt"></i> {esc(venue)}, Norwich</p>
    <div class="facts">{facts_html}</div>
    <div class="buttons">{''.join(buttons)}</div>
  </div>
</div>
{about}
{related(f"More at {venue}", same_venue)}
{related(f"Also on {WEEKDAYS[d.weekday()]} {ordinal(d.day)} {MONTHS[d.month - 1]}", same_night)}
</div>
<script>
// Poster didn't load (venue moved or blocked it): try the venue logo, then a music note.
document.querySelectorAll('img[data-logo]').forEach(function (img) {{
  function fallback() {{
    if (img.dataset.orig && !img.dataset.triedOrig) {{
      img.dataset.triedOrig = '1'; img.src = img.dataset.orig; return;
    }}
    if (img.dataset.logo && !img.dataset.tried) {{
      img.dataset.tried = '1'; img.classList.add('is-logo'); img.src = img.dataset.logo;
    }} else {{
      var box = img.parentNode; box.innerHTML = '<i class="fas fa-music"></i>';
    }}
  }}
  img.addEventListener('error', fallback);
  if (img.complete && img.naturalWidth === 0 && img.getAttribute('src')) fallback();
}});
document.querySelectorAll('[data-ga]').forEach(function (a) {{
  a.addEventListener('click', function () {{
    if (typeof gtag === 'function') gtag('event', a.dataset.ga, {{
      event_label: {json.dumps(name)}, venue: {json.dumps(ev.get('venue', ''))},
      gig_date: {json.dumps(ev['date'])}, transport_type: 'beacon' }});
  }});
}});
</script>"""

    when = f"{short_date(d)} {d.year}"
    title = f"{name} at {venue}, Norwich – {when} | Norwich Gigs"
    if ev.get("description"):
        description = ev["description"]
    else:
        bits = [f"{name} live at {venue}, Norwich on {long_date(d)}."]
        if ev.get("doors") or ev.get("time"):
            bits.append(f"Doors {nice_time(ev['doors'])}." if ev.get("doors")
                        else f"Starts {nice_time(ev['time'])}.")
        if ev.get("price"):
            bits.append(f"Tickets {ev['price']}.")
        description = " ".join(bits)
    ld = json.dumps(music_event_ld(ev, cfg, page_url, tickets), ensure_ascii=False,
                    separators=(",", ":")).replace("</", "<\\/")
    page = page_shell(title=title, description=description[:300], canonical=page_url,
                      body=body, og_image=(src if is_logo else poster_url(src, 1200)) if src else "",
                      extra_head=f'<script type="application/ld+json">{ld}</script>',
                      noindex=is_past, home_url=home_url)
    return page, build_ics(ev, cfg, page_url, tickets)


def index_page(upcoming: list, cfg: dict, *, site_url: str, home_url: str, slug_of) -> str:
    groups, current = [], None
    for ev in upcoming:
        d = parse_date(ev["date"])
        if (d.year, d.month) != current:
            current = (d.year, d.month)
            groups.append((f"{MONTHS[d.month - 1]} {d.year}", []))
        groups[-1][1].append(ev)
    sections = []
    for label, evs in groups:
        rows = "".join(
            f'<li><a href="/gigs/{esc(slug_of(e))}/"><span class="li-date">{esc(short_date(parse_date(e["date"])))}</span>'
            f'<span class="li-name">{esc(clean_name(e.get("event_name", ""))[0])}</span>'
            f'<span class="li-venue">{esc(e.get("venue", ""))}</span></a></li>' for e in evs)
        sections.append(f'<section class="month"><h2>{esc(label)}</h2><ul class="gig-list">{rows}</ul></section>')
    body = (f'<div class="listing"><h1>Upcoming gigs in Norwich</h1>'
            f'<p class="lede">{len(upcoming)} gigs across the city. For search, the calendar and '
            f'"what\'s on tonight", use the <a href="{esc(home_url)}">home page</a>.</p>'
            f'{"".join(sections)}</div>')
    return page_shell(title="Upcoming gigs in Norwich | Norwich Gigs",
                      description="Every upcoming live music gig in Norwich, with times, prices and tickets.",
                      canonical=f"{site_url}/gigs/", body=body, home_url=home_url)


ARCHIVE_JSON = ROOT / "scraped_data" / "gig_archive.json"
# Same venue under two names in different sources: show one.
ARCHIVE_VENUE_NAMES = {"The Reindeer": "Reindeer", "The LCR": "LCR", "Epic Studios": "Epic"}


def _event_filter():
    """The scraper's own keyword filter (quiz, bingo, cinema, karaoke, comedy,
    phone numbers...), so the archive leaves out exactly what the home page
    does - including old gigs from before a keyword was added, and gigs from
    the hand-made sheet. Returns None if the scraper can't be imported."""
    try:
        sys.path.insert(0, str(ROOT))
        from scrape_headless import should_filter_event
        return should_filter_event
    except Exception as exc:  # never let this stop the site build
        print(f"Archive: couldn't load the scraper's keyword filter ({exc}) - not filtering", file=sys.stderr)
        return None


def archive_data(today: date, gig_pages: dict, cfg: dict) -> tuple[list, dict]:
    """Past gigs from scraped_data/gig_archive.json for the archive page, newest
    first, as compact rows [date, venue, name, support, price, gig page, poster].
    Left out: gigs that vanished from listings before their date (probably
    cancelled) and anything the scraper's keyword filter would drop."""
    if not ARCHIVE_JSON.exists():
        return [], {}
    gigs = json.loads(ARCHIVE_JSON.read_text(encoding="utf-8")).get("gigs", [])
    keep_out = _event_filter()

    def text(v):   # support can be a list of acts
        return ", ".join(map(str, v)) if isinstance(v, list) else str(v or "").strip()

    rows, filtered = [], 0
    for g in gigs:
        if g.get("date", "") >= today.isoformat() or g.get("removed_early"):
            continue
        if keep_out and any(keep_out(n) for n in [g.get("event_name", ""), *g.get("also_listed_as", [])]):
            filtered += 1
            continue
        name = clean_name(g.get("event_name", ""))[0].strip()
        if not name:
            continue
        venue = ARCHIVE_VENUE_NAMES.get(g.get("venue", ""), g.get("venue", ""))
        img = g.get("image") or ""
        rows.append([g["date"], venue, name, text(g.get("support")), text(g.get("price")),
                     gig_pages.get(gig_key(g), ""), poster_url(img, 288) if img.startswith("http") else ""])
    rows.sort(key=lambda r: (r[0], r[1].lower(), r[2].lower()), reverse=True)
    venues: dict[str, int] = {}
    for r in rows:
        venues[r[1]] = venues.get(r[1], 0) + 1
    stats = {"total": len(rows), "venues": venues, "filtered": filtered,
             "first": rows[-1][0] if rows else "", "last": rows[0][0] if rows else ""}
    return rows, stats


def archive_page(stats: dict, cfg: dict, home_css: str, *, site_url: str, home_url: str, data_url: str) -> str:
    """The archive, built from the home page's own pieces: its search box,
    filter pills, counters and poster cards (same CSS, same markup), so it
    looks like the home page's gig list."""
    first = parse_date(stats["first"])
    since = f"{MONTHS[first.month - 1]} {first.year}"
    top = sorted(stats["venues"].items(), key=lambda kv: -kv[1])
    years = [str(y) for y in range(int(stats["last"][:4]), first.year - 1, -1)]
    venue_opts = "".join(f'<option value="{esc(v)}">{esc(v)} ({n})</option>'
                         for v, n in sorted(stats["venues"].items(), key=lambda kv: kv[0].lower()))
    year_btns = "".join(f'<button type="button" class="qf-btn" data-year="{y}" aria-pressed="false">{y}</button>'
                        for y in years)
    months = {int(k): blogger_img(v, 700) for k, v in cfg.get("MONTH_DIVIDER_IMAGES", {}).items() if v}
    logos = {v: poster_url(u, 288, "inside") for v, u in cfg.get("VENUE_LOGOS", {}).items() if u}
    body = f"""{home_css}
<style>
/* Archive extras, in the home page's style */
.archive-intro h1 {{ font-family: 'Lexend Exa', sans-serif; font-size: clamp(22px, 3vw, 30px); letter-spacing: -0.05em; margin: 4px 0 6px; text-align: center; }}
.archive-intro p {{ text-align: center; margin: 0 0 4px; color: #444; }}
.arch-venue {{ appearance: auto; font-family: inherit; width: 100%; max-width: 340px; cursor: pointer; }}
.arch-row2 {{ margin-top: 6px !important; }}
@media (max-width: 480px) {{ .arch-venue {{ max-width: none; }} }}
.arch-sub {{ display: block; font-size: 13px; font-weight: 500; opacity: .75; margin-top: 2px; }}
.arch-more {{ display: block; margin: 18px auto 4px; padding: 10px 26px; font-size: 15px; }}
.arch-empty {{ text-align: center; padding: 30px 10px; font-weight: 600; }}
</style>
<div class="archive-intro">
  <h1>Norwich gig archive</h1>
  <p>Every gig we've listed since {esc(since)}. Search for a band, a venue or a night you remember.</p>
</div>
<div class="search-container">
  <label for="archQ" class="search-label"><i class="fas fa-magic"></i> Search the archive:</label>
  <div class="search-wrapper">
    <i class="fas fa-search search-icon"></i>
    <input type="text" id="archQ" class="search-box" placeholder="Search by artist, venue or month..." autocomplete="off">
  </div>
  <div class="quick-filters" role="group" aria-label="Filter the archive">
    <button type="button" class="qf-btn" data-year="" aria-pressed="true">All years</button>{year_btns}
    <button type="button" class="qf-btn" id="archSort" aria-pressed="false"><i class="fas fa-clock"></i> <span>Oldest first</span></button>
  </div>
  <div class="quick-filters arch-row2">
    <select class="qf-btn arch-venue" id="archVenue" aria-label="Venue"><option value="">All venues</option>{venue_opts}</select>
  </div>
  <div class="counters">
    <div class="counter"><i class="fas fa-calendar-alt"></i> Gigs: {stats['total']:,}</div>
    <div class="counter"><i class="fas fa-map-marker-alt"></i> Venues: {len(stats['venues'])}</div>
    <div class="counter"><i class="fas fa-eye"></i> Showing: <span id="archShowing">…</span></div>
  </div>
</div>
<div class="events-table-container view-detailed">
  <table class="events-table">
    <thead><tr><th>Date</th><th>Venue</th><th>Artist/Event</th><th>Price</th><th></th></tr></thead>
    <tbody id="archBody"></tbody>
  </table>
</div>
<p class="arch-empty" id="archEmpty" hidden>No gigs found - try fewer words, another year or all venues.</p>
<button type="button" class="qf-btn arch-more" id="archMore" hidden>Show more</button>
<noscript><p>The archive search needs JavaScript. The <a href="{esc(data_url)}">full list</a> is also available as data.</p></noscript>
<script>
(function () {{
  var MONTHS = {json.dumps(MONTHS)}, BANNERS = {json.dumps(months)}, LOGOS = {json.dumps(logos)};
  var PAGE = 60, rows = [], hits = [], shown = 0, lastMonth = '', yearSel = '', oldest = false;
  var q = document.getElementById('archQ'), venue = document.getElementById('archVenue'),
      body = document.getElementById('archBody'), showing = document.getElementById('archShowing'),
      more = document.getElementById('archMore'), empty = document.getElementById('archEmpty'),
      sortBtn = document.getElementById('archSort');
  function fold(s) {{ return (s || '').toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); }}
  function el(tag, cls, text) {{ var e = document.createElement(tag); if (cls) e.className = cls;
    if (text != null) e.textContent = text; return e; }}
  function ord(n) {{ var s = ['th','st','nd','rd'], v = n % 100; return n + (s[(v - 20) % 10] || s[v] || s[0]); }}
  function dateLabel(iso) {{ var p = iso.split('-'); return ord(+p[2]) + ' ' + MONTHS[+p[1] - 1].slice(0, 3) + ' ' + p[0]; }}
  function run(keepUrl) {{
    var words = fold(q.value).split(/\\s+/).filter(Boolean), v = venue.value;
    hits = rows.filter(function (r) {{
      if (v && r[1] !== v) return false;
      if (yearSel && r[0].slice(0, 4) !== yearSel) return false;
      return words.every(function (w) {{ return r._t.indexOf(w) !== -1; }});
    }});
    if (oldest) hits.reverse();
    body.textContent = ''; shown = 0; lastMonth = ''; render();
    showing.textContent = hits.length.toLocaleString('en-GB');
    empty.hidden = hits.length > 0;
    if (!keepUrl) {{
      var p = new URLSearchParams();
      if (q.value.trim()) p.set('q', q.value.trim()); if (v) p.set('venue', v); if (yearSel) p.set('year', yearSel);
      if (oldest) p.set('sort', 'old');
      var s = p.toString(); history.replaceState(null, '', location.pathname + (s ? '?' + s : ''));
    }}
  }}
  function banner(m) {{
    var tr = el('tr', 'month-divider'), td = el('td'); td.colSpan = 6;
    var y = m.slice(0, 4), mo = +m.slice(5), src = BANNERS[mo];
    td.style.cssText = 'padding:0;background:transparent;border-radius:15px;overflow:hidden;position:relative;';
    if (src) {{
      var img = el('img'); img.src = src; img.alt = MONTHS[mo - 1] + ' ' + y; img.loading = 'lazy';
      img.width = 1400; img.height = 300; img.style.cssText = 'display:block;width:100%;height:auto;border-radius:15px;';
      td.appendChild(img);
      var tag = el('span', null, y);
      tag.style.cssText = "position:absolute;right:12px;bottom:10px;background:#fef403;color:#000;font:700 14px 'Archivo Black',sans-serif;padding:3px 12px;border:2px solid #000;border-radius:30px;";
      td.appendChild(tag);
    }} else {{
      var h = el('div', null, MONTHS[mo - 1] + ' ' + y);
      h.style.cssText = "background:#000;color:#fff;text-align:center;padding:12px;border-radius:15px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;";
      td.appendChild(h);
    }}
    tr.appendChild(td); return tr;
  }}
  function card(r) {{
    var tr = el('tr'), pc = el('td', 'poster-cell'), src = r[6] || LOGOS[r[1]] || '';
    if (src) {{
      var img = el('img', 'event-poster' + (r[6] ? '' : ' is-logo'));
      img.src = src; img.alt = r[6] ? r[2] + ' poster' : r[1] + ' logo'; img.loading = 'lazy'; img.width = 96; img.height = 96;
      img.onerror = function () {{
        if (r[6] && LOGOS[r[1]] && img.src !== LOGOS[r[1]]) {{ img.className = 'event-poster is-logo'; img.src = LOGOS[r[1]]; }}
        else {{ pc.textContent = ''; pc.appendChild(el('i', 'fas fa-music')); }}
      }};
      pc.appendChild(img);
    }} else pc.appendChild(el('i', 'fas fa-music'));
    tr.appendChild(pc);
    tr.appendChild(el('td', 'date-cell', dateLabel(r[0])));
    tr.appendChild(el('td', 'venue-cell', r[1]));
    var ev = el('td', 'event-cell', r[2]);
    if (r[3]) ev.appendChild(el('span', 'arch-sub', 'with ' + r[3]));
    tr.appendChild(ev);
    if (r[4]) tr.appendChild(el('td', 'price-cell', r[4]));
    if (r[5]) {{ tr.className = 'is-clickable'; tr.addEventListener('click', function () {{ location.href = r[5]; }}); }}
    return tr;
  }}
  function render() {{
    var end = Math.min(shown + PAGE, hits.length), frag = document.createDocumentFragment();
    for (var i = shown; i < end; i++) {{
      var m = hits[i][0].slice(0, 7);
      if (m !== lastMonth) {{ lastMonth = m; frag.appendChild(banner(m)); }}
      frag.appendChild(card(hits[i]));
    }}
    body.appendChild(frag); shown = end; more.hidden = shown >= hits.length;
    if (!more.hidden) more.textContent = 'Show more (' + (hits.length - shown).toLocaleString('en-GB') + ' left)';
  }}
  var timer;
  q.addEventListener('input', function () {{ clearTimeout(timer); timer = setTimeout(function () {{ run(); }}, 150); }});
  venue.addEventListener('change', function () {{ run(); }});
  more.addEventListener('click', render);
  function setYear(y) {{ yearSel = y; document.querySelectorAll('[data-year]').forEach(function (b) {{
    b.setAttribute('aria-pressed', String(b.getAttribute('data-year') === y)); }}); }}
  document.querySelectorAll('[data-year]').forEach(function (b) {{
    b.addEventListener('click', function () {{ setYear(b.getAttribute('data-year')); run(); }}); }});
  function setSort(o) {{ oldest = o; sortBtn.setAttribute('aria-pressed', String(o));
    sortBtn.querySelector('span').textContent = o ? 'Newest first' : 'Oldest first'; }}
  sortBtn.addEventListener('click', function () {{ setSort(!oldest); run(); }});
  var p = new URLSearchParams(location.search);
  q.value = p.get('q') || ''; venue.value = p.get('venue') || ''; setYear(p.get('year') || ''); setSort(p.get('sort') === 'old');
  fetch({json.dumps(data_url)}).then(function (r) {{ return r.json(); }}).then(function (data) {{
    rows = data.gigs;
    rows.forEach(function (r) {{ r._t = fold(r[2] + ' ' + r[3] + ' ' + r[1] + ' ' + dateLabel(r[0]) + ' ' + MONTHS[+r[0].slice(5, 7) - 1]); }});
    run(true);
  }}).catch(function () {{ showing.textContent = '0'; empty.hidden = false;
    empty.textContent = "Sorry - the archive didn't load. Please refresh the page."; }});
}})();
</script>"""
    return page_shell(title="Norwich gig archive - every gig since " + since + " | Norwich Gigs",
                      description=f"Search {stats['total']:,} past gigs in Norwich since {since}, "
                                  f"at {len(stats['venues'])} venues including "
                                  + ", ".join(v for v, _ in top[:4]) + ".",
                      canonical=f"{site_url}/archive/", body=f'<div class="home-card">{body}</div>',
                      home_url=home_url, crumbs=False)


def home_page(cfg: dict, events: list, today: date, *, site_url: str, home_url: str) -> str | None:
    """The home page: search, calendar and gig cards from blogger/page_template.html
    (the same template the Blogger page used), inside the site's header and
    sidebar. Cards open the gig's own page here."""
    built = build_home(today, cfg, events, gig_base="")
    if not built:
        return None
    body, meta = built
    # The template carries its own font/icon links; the page head already has them.
    body = "\n".join(line for line in body.splitlines()
                     if not (line.strip().startswith(("<link", "<noscript><link"))
                             and (line.strip() in HEAD_LINKS or THIRD_PARTY_FONT_RE.search(line))))
    return page_shell(title=meta["title"], description=meta["description"], canonical=f"{site_url}/",
                      body=f'<div class="home-card">{body}</div>', og_image=site_url + LOGO,
                      home_url=home_url, crumbs=False, analytics=False, bottom_nav=False)


def site_pages(home_url: str, site_url: str) -> list[tuple[str, str]]:
    """Pages kept in site_pages/ (copied from Blogger by import_blogger_backup.py,
    then edited by hand), each put inside the site's header, menu and sidebar.
    Returns [(address, html)]. A file starts with <!-- title: Page title -->."""
    out = []
    for f in sorted(SITE_PAGES.glob("**/*.html")):
        rel = "/" + f.relative_to(SITE_PAGES).as_posix()
        raw = f.read_text(encoding="utf-8")
        m = re.match(r"\s*<!--\s*title:\s*(.*?)\s*-->", raw)
        title = m.group(1) if m else f.stem.replace("-", " ").title()
        body = raw[m.end():] if m else raw
        d = re.match(r"\s*<!--\s*description:\s*(.*?)\s*-->", body)
        description = d.group(1) if d else f"{title} - Norwich Gigs, live music in Norwich."
        body = body[d.end():] if d else body
        out.append((rel, page_shell(title=f"{title} | Norwich Gigs",
                                    description=description,
                                    canonical=f"{site_url}{rel}", body=f'<div class="site-page">{body}</div>',
                                    home_url=home_url, crumbs=False)))
    return out


def redirect_pages(site_url: str) -> list[tuple[str, str]]:
    """Old Blogger post addresses (site_pages/redirects.json) forward to their
    new home, so links people saved or shared still work."""
    try:
        redirects = json.loads((SITE_PAGES / "redirects.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    out = []
    for old, new in redirects.items():
        target = new if new.startswith("http") else f"{site_url}{new}"
        out.append((old, f"""<!DOCTYPE html><html lang="en-GB"><head><meta charset="utf-8">
<title>Moved | Norwich Gigs</title><link rel="canonical" href="{esc(target)}">
<meta name="robots" content="noindex"><meta http-equiv="refresh" content="0; url={esc(new)}">
<script>location.replace({json.dumps(new)});</script></head>
<body><p>This page has moved: <a href="{esc(new)}">Norwich Gigs</a>.</p></body></html>"""))
    return out


def write_page(address: str, html_text: str) -> None:
    """/p/aboutus.html -> dist/p/aboutus.html; /gigs/ -> dist/gigs/index.html."""
    rel = address.lstrip("/")
    path = DIST / (rel + "index.html" if rel.endswith("/") or not rel else rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_text, encoding="utf-8")


def not_found_page(home_url: str, css_text: str) -> str:
    # Served for any missing address, at any depth, so the CSS is inlined.
    return f"""<!DOCTYPE html><html lang="en-GB"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Gig not found | Norwich Gigs</title>
<meta name="robots" content="noindex">{HEAD_LINKS}<style>{css_text}</style></head><body>
{header_html(home_url)}
<main class="listing card"><h1>That gig isn't here any more</h1>
<p class="lede">It may have already happened, or its details changed.
<a href="{esc(home_url)}">See all upcoming gigs in Norwich</a>.</p></main></body></html>"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--today", help="override today's date (YYYY-MM-DD), for testing")
    args = ap.parse_args()
    today = (date.fromisoformat(args.today) if args.today else datetime.now(LONDON).date())

    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    gp = cfg.get("GIG_PAGES", {})
    site_url = gp.get("site_url", "https://gigs.norwichgigs.com").rstrip("/")
    home_url = "/"   # the whole site is now served from here
    past_days = int(gp.get("past_days", 30))

    events = json.loads(EVENTS_JSON.read_text(encoding="utf-8"))
    events = [e for e in events if re.fullmatch(r"\d{4}-\d{2}-\d{2}", e.get("date", ""))]
    events.sort(key=lambda e: (e["date"], e.get("venue", ""), e.get("event_name", "")))
    slugs = assign_gig_slugs(events)

    def slug_of(e):
        return slugs[gig_key(e)]

    oldest = (today - timedelta(days=past_days)).isoformat()
    shown = [e for e in events if e["date"] >= oldest]
    upcoming = [e for e in shown if e["date"] >= today.isoformat()]
    if not upcoming:
        print("No upcoming gigs in the JSON - refusing to build an empty site.", file=sys.stderr)
        return 1

    if DIST.exists():
        shutil.rmtree(DIST)
    (DIST / "assets").mkdir(parents=True)
    global PAGE_CSS
    css_text = (FONTS_CSS.read_text(encoding="utf-8") if FONTS_CSS.exists() else "") + CSS.read_text(encoding="utf-8")
    (DIST / "assets" / "style.css").write_text(css_text, encoding="utf-8")
    PAGE_CSS = css_text
    if FONTS_DIR.exists():
        shutil.copytree(FONTS_DIR, DIST / "assets" / "fonts", dirs_exist_ok=True)
    # Site icons (browser tabs, Google results, phone home screens) go at the site root.
    for asset in (HERE / "assets").glob("norwichgigs-logo*"):
        shutil.copyfile(asset, DIST / "assets" / asset.name)
    for icon in (HERE / "icons").glob("*"):
        shutil.copyfile(icon, DIST / icon.name)

    by_venue: dict[str, list] = {}
    by_date: dict[str, list] = {}
    for e in upcoming:
        by_venue.setdefault(e.get("venue", ""), []).append(e)
        by_date.setdefault(e["date"], []).append(e)

    for ev in shown:
        slug = slug_of(ev)
        same_venue = [e for e in by_venue.get(ev.get("venue", ""), []) if e is not ev
                      and e["date"] >= ev["date"]][:4]
        if len(same_venue) < 4:   # past gig, or near the end of the listings
            same_venue += [e for e in by_venue.get(ev.get("venue", ""), [])
                           if e is not ev and e not in same_venue][:4 - len(same_venue)]
        same_night = [e for e in by_date.get(ev["date"], []) if e is not ev][:6]
        page, ics = gig_page(ev, cfg, slug=slug, site_url=site_url, home_url=home_url, today=today,
                             same_venue=same_venue, same_night=same_night, slug_of=slug_of)
        out = DIST / "gigs" / slug
        out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text(page, encoding="utf-8")
        (out / "gig.ics").write_text(ics, encoding="utf-8", newline="")

    home = home_page(cfg, events, today, site_url=site_url, home_url=home_url)
    if not home:
        print("Home page has no gigs - refusing to build.", file=sys.stderr)
        return 1
    (DIST / "index.html").write_text(home, encoding="utf-8")
    (DIST / "gigs" / "index.html").write_text(
        index_page(upcoming, cfg, site_url=site_url, home_url=home_url, slug_of=slug_of), encoding="utf-8")
    # The archive: every past gig in scraped_data/gig_archive.json, searchable.
    gig_page_of = {gig_key(e): f"/gigs/{slug_of(e)}/" for e in shown}
    arch_rows, arch_stats = archive_data(today, gig_page_of, cfg)
    # The home page's own card / search styles (inside its body), reused by the archive.
    home_css = "\n".join(re.findall(r"<style>.*?</style>", home.split("<body", 1)[-1], re.S))
    if arch_rows:
        (DIST / "archive").mkdir(parents=True, exist_ok=True)
        (DIST / "archive" / "gigs.json").write_text(
            json.dumps({"updated": today.isoformat(),
                        "fields": ["date", "venue", "name", "support", "price", "page", "poster"],
                        "gigs": arch_rows}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        (DIST / "archive" / "index.html").write_text(
            archive_page(arch_stats, cfg, home_css, site_url=site_url, home_url=home_url,
                         data_url="/archive/gigs.json"),
            encoding="utf-8")
    extra_pages = site_pages(home_url, site_url)
    for address, page in extra_pages + redirect_pages(site_url):
        write_page(address, page)
    (DIST / "404.html").write_text(not_found_page(home_url, css_text), encoding="utf-8")
    (DIST / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {site_url}/sitemap.xml\n",
                                     encoding="utf-8")
    urls = ([f"{site_url}/", f"{site_url}/gigs/"] + ([f"{site_url}/archive/"] if arch_rows else []) + [f"{site_url}{a}" for a, _ in extra_pages]
            + [f"{site_url}/gigs/{slug_of(e)}/" for e in upcoming])
    sitemap = "".join(f"<url><loc>{esc(u)}</loc><lastmod>{today.isoformat()}</lastmod></url>" for u in urls)
    (DIST / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{sitemap}</urlset>\n', encoding="utf-8")
    (DIST / ".nojekyll").write_text("", encoding="utf-8")

    with_details = sum(1 for e in upcoming if e.get("time") or e.get("doors") or e.get("description"))
    print(f"Archive page: {len(arch_rows)} past gigs ({arch_stats.get('filtered', 0)} left out by the keyword filter)")
    print(f"Built the home page, the gig list, {len(extra_pages)} other page(s) and {len(shown)} gig page(s) ({len(upcoming)} upcoming, {len(shown) - len(upcoming)} recent past; "
          f"{with_details} upcoming with extra details) -> {DIST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
