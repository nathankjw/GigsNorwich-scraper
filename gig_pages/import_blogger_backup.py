#!/usr/bin/env python3
"""
One-off: copies the pages from a Blogger backup file into site_pages/, so the
GitHub site serves them at the same addresses as before (/p/aboutus.html ...),
and lists every old post address in site_pages/redirects.json so those
addresses forward to the home page instead of breaking.

Get the backup in Blogger: Settings -> Manage blog -> Back up content -> Download.

Usage (from the repo root):
    python3 gig_pages/import_blogger_backup.py path/to/blog-backup.xml
    python3 gig_pages/import_blogger_backup.py --page /p/advertise.html advertise.html
        (one page, from HTML copied out of Blogger's HTML view)

After this, edit the files in site_pages/ directly - this script is only
needed once. Running it again overwrites site_pages/p/*.html.
"""
from __future__ import annotations

import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site_pages"

ATOM = "{http://www.w3.org/2005/Atom}"
BLOGGER = "{http://schemas.google.com/blogger/2018}"
KIND = "http://schemas.google.com/blogger/2008/kind#"
APP = "{http://purl.org/atom/app#}"

SITE_HOSTS = ("www.norwichgigs.com", "norwichgigs.com")


def text(el) -> str:
    return (el.text or "") if el is not None else ""


def entries(path: Path):
    """Yield (kind, status, path, title, html) for every page and post.
    Handles both Blogger backup formats (the older Atom-with-categories one
    and the newer one with <blogger:type>)."""
    root = ET.parse(path).getroot()
    for e in root.iter(f"{ATOM}entry"):
        kind = text(e.find(f"{BLOGGER}type")).strip().upper()
        if not kind:
            for c in e.findall(f"{ATOM}category"):
                term = c.get("term", "")
                if term.startswith(KIND):
                    kind = term[len(KIND):].upper()
        if kind not in ("PAGE", "POST"):
            continue
        status = text(e.find(f"{BLOGGER}status")).strip().upper() or "LIVE"
        draft = e.find(f"{APP}control/{APP}draft")
        if draft is not None and text(draft).strip().lower() == "yes":
            status = "DRAFT"
        url_path = text(e.find(f"{BLOGGER}filename")).strip()
        if not url_path:
            for link in e.findall(f"{ATOM}link"):
                if link.get("rel") == "alternate":
                    url_path = urlparse(link.get("href", "")).path
        title = text(e.find(f"{ATOM}title")).strip()
        content = text(e.find(f"{ATOM}content"))
        yield kind, status, url_path, title, content


def make_links_relative(content: str) -> str:
    for host in SITE_HOSTS:
        content = re.sub(rf"https?://{re.escape(host)}/", "/", content)
    return content


def to_fragment(content: str) -> tuple[str, str, str]:
    """A page may be a whole HTML document (About Us, List Your Gig) or just
    body HTML. Returns (title, meta description, HTML for the site's main
    column). From <head> it keeps only stylesheets, <style> and JSON-LD data
    (not its <title>/<meta>/canonical - the site writes those); from the page
    it drops its own mobile bottom bar (the site adds one)."""
    title = description = ""
    m = re.search(r"<title>(.*?)</title>", content, re.S | re.I)
    if m:
        title = html.unescape(m.group(1)).strip()
    m = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', content, re.I)
    if m:
        description = html.unescape(m.group(1)).strip()
    if re.search(r"<body", content, re.I):
        head = re.search(r"<head[^>]*>(.*?)</head>", content, re.S | re.I)
        head_bits = ""
        if head:
            keep = re.findall(r'<link\b[^>]*rel="stylesheet"[^>]*>|<link\b[^>]*rel="preconnect"[^>]*>'
                              r'|<style\b.*?</style>|<script type="application/ld\+json">.*?</script>',
                              head.group(1), re.S | re.I)
            head_bits = "\n".join(keep)
        body = re.search(r"<body[^>]*>(.*)</body>", content, re.S | re.I)
        content = head_bits + "\n" + (body.group(1) if body else content)
    content = re.sub(r'<nav class="bottom-nav".*?</nav>', "", content, flags=re.S)
    return title, description, content.strip()


def clean_title(title: str) -> str:
    title = re.sub(r"\s*[-|–]\s*Norwich Gig(?:s| List).*$", "", title).strip()
    if title.islower():
        title = title[:1].upper() + title[1:]          # "privacy policy"
    return title


def write_page(url_path: str, content: str, title: str = "") -> tuple[str, str]:
    doc_title, description, fragment = to_fragment(make_links_relative(content))
    name = Path(url_path).name                     # e.g. aboutus.html
    page_title = clean_title(title or doc_title or name.replace(".html", "").replace("-", " ").title())
    header = f"<!-- title: {page_title} -->\n"
    if description:
        header += f"<!-- description: {description} -->\n"
    out = OUT / "p" / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(header + fragment + "\n", encoding="utf-8")
    return url_path, page_title


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] == "--page":
        url_path, title = write_page(sys.argv[2], Path(sys.argv[3]).read_text(encoding="utf-8"))
        print(f"page      {url_path}  ({title})")
        return 0
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    backup = Path(sys.argv[1])
    pages, redirects = [], {}
    for kind, status, url_path, title, content in entries(backup):
        if status != "LIVE" or not url_path:
            continue
        if kind == "POST":
            redirects[url_path] = "/"
            continue
        pages.append(write_page(url_path, content, title))
    OUT.mkdir(exist_ok=True)
    (OUT / "redirects.json").write_text(json.dumps(dict(sorted(redirects.items())), indent=1) + "\n",
                                        encoding="utf-8")
    for url_path, title in pages:
        print(f"page      {url_path}  ({title})")
    print(f"{len(pages)} page(s) written to {OUT / 'p'}; {len(redirects)} old post address(es) "
          f"will forward to the home page (see {OUT / 'redirects.json'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
