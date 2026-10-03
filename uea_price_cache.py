#!/usr/bin/env python3
"""
Fetch UEA ticket prices (Spektrix API + ChooseSeats page) -> scraped_data/uea_prices.json
Run from a HOME connection. Resumable: just rerun if it stops.

The ticket site blocks a browser session after its first page load, so this
uses a brand-new Chrome for every showing and retries blocked ones.
"""
import json, random, re, time
from datetime import datetime
from pathlib import Path

import requests
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

API = "https://system.spektrix.com/ueastudentsunion/api/v3"
TICKETS = ("https://tickets.ueaticketbookings.co.uk/ueastudentsunion/"
           "website/ChooseSeats.aspx?resize=true&EventInstanceId={}")
OUT = Path(__file__).parent / "scraped_data" / "uea_prices.json"

INCLUDE_FEE = True
DELAY = (2, 4)            # seconds between showings (try lower/higher later)
RETRIES = 3               # fresh-browser attempts per showing if blocked
BLOCK_PAUSE = 20          # seconds before retrying a blocked showing
MAX_GIVEUPS_IN_ROW = 4    # stop if this many showings in a row stay blocked

PRICE_RE = re.compile(
    r"@\s*£\s*(\d+(?:\.\d{1,2})?)"
    r"(?:\s*\(\s*inc\.?\s*£\s*(\d+(?:\.\d{1,2})?)\s*(?:cmsn|commission|fee)[^)]*\))?",
    re.I)

DRIVER_PATH = None


def fmt(v):
    return f"£{v:.0f}" if float(v).is_integer() else f"£{v:.2f}"


def price_label(text):
    amounts = []
    for base, fee in PRICE_RE.findall(text):
        v = float(base)
        if fee and not INCLUDE_FEE:
            v -= float(fee)
        amounts.append(v)
    amounts = [a for a in amounts if a > 0]
    if not amounts:
        return None
    lo, hi = min(amounts), max(amounts)
    return fmt(lo) if lo == hi else f"{fmt(lo)}–{fmt(hi)}"


def fetch_once(url, wait=20):
    """Open a brand-new Chrome, load one page, return its text, close Chrome."""
    driver = webdriver.Chrome(service=Service(DRIVER_PATH))
    try:
        driver.get(url)
        end, text = time.time() + wait, ""
        while time.time() < end:
            text = driver.find_element("tag name", "body").text
            low = text.lower()
            if "@ £" in text or "@£" in text:
                break
            if ("sold out" in low or "cannot find the event" in low
                    or "you have been blocked" in low):
                break
            time.sleep(1)
        return text
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def save(results):
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")


def main():
    global DRIVER_PATH
    DRIVER_PATH = ChromeDriverManager().install()

    results = []
    if OUT.exists():
        try:
            results = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            results = []
    results = [r for r in results if r["date"] >= datetime.now().strftime("%Y-%m-%d")]
    done = {(r["name"], r["date"]) for r in results}
    print(f"Resuming with {len(results)} prices already saved")

    events = requests.get(f"{API}/events", timeout=30).json()
    today = datetime.now().strftime("%Y-%m-%d")
    todo = []
    for ev in events:
        if not ev.get("isOnSale"):
            continue
        try:
            insts = requests.get(f"{API}/events/{ev['id']}/instances", timeout=30).json()
        except Exception as e:
            print(f"  ! instances failed for {ev['name']}: {e}")
            continue
        for inst in insts:
            date = (inst.get("start") or "")[:10]
            if inst.get("cancelled") or not inst.get("isOnSale") or date < today:
                continue
            if (ev["name"].strip(), date) in done:
                continue
            todo.append((ev["name"].strip(), date, re.match(r"\d+", inst["id"]).group(0)))
    print(f"{len(todo)} showings to check\n")

    sold_out = giveups = giveups_in_row = failures = 0
    try:
        for i, (name, date, short_id) in enumerate(todo, 1):
            text, outcome = "", None
            for attempt in range(1, RETRIES + 1):
                try:
                    text = fetch_once(TICKETS.format(short_id))
                except WebDriverException as e:
                    print(f"  ! Chrome error ({type(e).__name__}), retrying")
                    time.sleep(5)
                    continue
                if "you have been blocked" in text.lower():
                    if attempt < RETRIES:
                        time.sleep(BLOCK_PAUSE)
                    continue
                outcome = "ok"
                break

            label = price_label(text) if outcome else None
            if label:
                giveups_in_row = 0
                results.append({"name": name, "date": date, "price": label})
                save(results)
                print(f"  ✓ [{i}/{len(todo)}] {date}  {name}  {label}")
            elif outcome and "sold out" in text.lower():
                giveups_in_row = 0
                sold_out += 1
                print(f"  – [{i}/{len(todo)}] {date}  {name}  (sold out)")
            elif outcome:
                giveups_in_row = 0
                failures += 1
                if failures <= 5:
                    print(f"  ✗ {date}  {name}: page text starts {text[:200]!r}")
            else:
                giveups += 1
                giveups_in_row += 1
                print(f"  ⏸ [{i}/{len(todo)}] {name}: blocked {RETRIES}x, skipping "
                      f"({giveups_in_row}/{MAX_GIVEUPS_IN_ROW})")
                if giveups_in_row >= MAX_GIVEUPS_IN_ROW:
                    print("Blocked repeatedly - stopping. Progress saved; rerun later.")
                    break
            time.sleep(random.uniform(*DELAY))
    finally:
        save(results)

    print(f"\nSaved {len(results)} prices to {OUT} "
          f"({sold_out} sold out, {giveups} blocked, {failures} other failures)")


if __name__ == "__main__":
    main()
