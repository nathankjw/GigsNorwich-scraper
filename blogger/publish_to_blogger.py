#!/usr/bin/env python3
"""
Pushes blogger/dist/page_body.html (and the title) to your Blogger page/post
through the Blogger API v3. Run after build_blogger_page.py.

Environment variables (GitHub Actions secrets):
  BLOGGER_CLIENT_ID, BLOGGER_CLIENT_SECRET, BLOGGER_REFRESH_TOKEN
  BLOGGER_BLOG_ID   - numeric blog id
  BLOGGER_PAGE_ID   - numeric id of the page (or post) that holds the gig list
  BLOGGER_KIND      - "pages" (default) or "posts"

If the secrets aren't set yet it prints a note and exits 0, so the daily
scrape keeps working before you've finished the one-off Google setup.
Add --dry-run to check everything except the final update.
"""
import json
import os
import sys
from pathlib import Path

import requests

DIST = Path(__file__).resolve().parent / "dist"
REQUIRED = ["BLOGGER_CLIENT_ID", "BLOGGER_CLIENT_SECRET", "BLOGGER_REFRESH_TOKEN",
            "BLOGGER_BLOG_ID", "BLOGGER_PAGE_ID"]


def main() -> int:
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        print("Blogger publish skipped - secrets not set:", ", ".join(missing))
        return 0
    kind = os.environ.get("BLOGGER_KIND", "pages")
    if kind not in ("pages", "posts"):
        print("BLOGGER_KIND must be 'pages' or 'posts'", file=sys.stderr)
        return 1

    body = (DIST / "page_body.html").read_text(encoding="utf-8")
    meta = json.loads((DIST / "page_meta.json").read_text(encoding="utf-8"))

    tok = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": os.environ["BLOGGER_CLIENT_ID"],
        "client_secret": os.environ["BLOGGER_CLIENT_SECRET"],
        "refresh_token": os.environ["BLOGGER_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }, timeout=30)
    if tok.status_code != 200:
        print("Could not refresh the Google token:", tok.text, file=sys.stderr)
        return 1
    headers = {"Authorization": f"Bearer {tok.json()['access_token']}"}

    url = (f"https://www.googleapis.com/blogger/v3/blogs/{os.environ['BLOGGER_BLOG_ID']}"
           f"/{kind}/{os.environ['BLOGGER_PAGE_ID']}")
    cur = requests.get(url, headers=headers, timeout=30)
    if cur.status_code != 200:
        print(f"Could not read the existing {kind[:-1]}: {cur.status_code} {cur.text}", file=sys.stderr)
        return 1
    if cur.json().get("content") == body and cur.json().get("title") == meta["title"]:
        print("Blogger already up to date - nothing to do.")
        return 0

    if "--dry-run" in sys.argv:
        print(f"Dry run OK: would update '{meta['title']}' ({len(body) / 1024:.0f} KB).")
        return 0

    res = requests.patch(url, headers=headers, timeout=120,
                         json={"title": meta["title"], "content": body})
    if res.status_code != 200:
        print(f"Blogger update failed: {res.status_code} {res.text}", file=sys.stderr)
        return 1
    print(f"Updated Blogger {kind[:-1]}: {res.json().get('url')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
