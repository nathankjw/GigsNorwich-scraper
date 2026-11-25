"""
Tests for event_details.py. The sample pages are simplified versions of the
way each venue's site lays out its gig details (wording and layout, not their
real write-ups).

Run from the repo root:  python3 -m unittest discover tests
"""
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import event_details as ed  # noqa: E402

LOREM = ("Sample Band are a four-piece from somewhere in England who play loud, "
         "melodic guitar music with big choruses. They return to Norwich on the "
         "back of a new album and a sold-out spring tour. A third sentence that "
         "should not make it into the short excerpt at all.")


def page(body: str, head: str = "") -> str:
    return f"""<html><head>{head}</head><body>
    <header class="site-header"><nav>Home Events Tickets</nav><p>Open from 11am daily</p></header>
    <div class="cookie-banner">We use cookies. Over 18s only after 9pm.</div>
    {body}
    <footer><p>Opening hours: Mon-Sun 12pm - 11pm. Strictly 18+ after 9pm.</p>
    <p>Sign up to our newsletter for the latest gigs and offers every single week.</p></footer>
    </body></html>"""


class TimeParsing(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(ed.first_time("7pm"), "19:00")
        self.assertEqual(ed.first_time("7.30pm"), "19:30")
        self.assertEqual(ed.first_time("7:30 p.m."), "19:30")
        self.assertEqual(ed.first_time("19:30"), "19:30")
        self.assertEqual(ed.first_time("12AM"), "00:00")
        self.assertEqual(ed.first_time("12pm"), "12:00")
        self.assertEqual(ed.first_time("7:30"), "19:30")   # no am/pm at a gig = evening
        self.assertEqual(ed.first_time("11:00"), "11:00")  # left alone: could be a matinee

    def test_not_times(self):
        self.assertIsNone(ed.first_time("£7.50"))
        self.assertIsNone(ed.first_time("26.11.2026"))
        self.assertIsNone(ed.first_time("Room 7"))
        self.assertIsNone(ed.first_time("Est. 1992"))


class VenueLayouts(unittest.TestCase):
    def test_uea_label_and_value_in_separate_cells(self):
        html = page(f"""<main><h1>Sample Band</h1>
          <dl><dt>Date</dt><dd>Thu 26 November 2026 7:30pm</dd>
              <dt>Doors time</dt><dd>19:30</dd>
              <dt>Age restriction</dt><dd>14+ (under 16's to be accompanied by an adult)</dd></dl>
          <p>{LOREM}</p></main>""",
          head='<meta name="description" content="Book tickets online for Sample Band at The Adrian Flux Waterfront Studio.">')
        d = ed.extract_details(html)
        self.assertEqual(d["doors"], "19:30")
        self.assertEqual(d["time"], "19:30")
        self.assertEqual(d["age"], "14+")
        self.assertTrue(d["description"].startswith("Sample Band are a four-piece"))
        self.assertNotIn("third sentence", d["description"])

    def test_arts_centre_open_from_and_range_to_end(self):
        html = page(f"""<article><h1>An Evening With Sample Band</h1>
          <p>Open from 7pm</p><p>Sample Band 8pm – end</p>
          <p>FREE for under 26s with a young person's ticket.</p>
          <p>{LOREM}</p></article>""")
        d = ed.extract_details(html)
        self.assertEqual(d["doors"], "19:00")
        self.assertEqual(d["time"], "20:00")
        self.assertNotIn("age", d)          # "under 26s" is a price offer, not an age limit

    def test_voodoos_bare_time(self):
        html = page(f"""<main><h1>Sample Band</h1><div class="event-meta">
          <span>November 25, 2026</span></div><div>7:00 pm</div><p>{LOREM}</p></main>""")
        d = ed.extract_details(html)
        self.assertEqual(d["time"], "19:00")
        self.assertNotIn("doors", d)
        self.assertNotIn("age", d)          # footer "18+ after 9pm" must not leak in

    def test_dead_wax_start_and_end_on_one_line(self):
        html = page("""<main><h1>Sample Band + Niche + Godkill</h1>
          <p>Wednesday, 25 November 2026</p><p>7:00 PM 11:00 PM</p>
          <p>Support from Niche and Godkill.</p>
          <p>This is Sample Band, a display of terrible affairs brought live to the stage with theatrics.</p></main>""")
        d = ed.extract_details(html)
        self.assertEqual(d["time"], "19:00")
        self.assertEqual(d["end"], "23:00")
        self.assertEqual(d["support"], ["Niche", "Godkill"])

    def test_fatsoma_range_with_at_and_age(self):
        html = page(f"""<main><h1>Sample DJ All Night Long</h1>
          <p>Fri 27th Nov at 11:00pm-3:00am</p>
          <p>Last entry time: 12AM</p>
          <p>Age restrictions: 18+ - Please bring ID</p>
          <p>{LOREM}</p></main>""")
        d = ed.extract_details(html)
        self.assertEqual(d["time"], "23:00")
        self.assertEqual(d["end"], "03:00")
        self.assertEqual(d["age"], "18+")

    def test_json_ld_takes_priority(self):
        head = """<script type="application/ld+json">{"@context":"https://schema.org",
          "@type":"MusicEvent","name":"Sample Band","startDate":"2026-12-04T20:00:00+00:00",
          "doorTime":"2026-12-04T19:00:00+00:00","typicalAgeRange":"16-",
          "description":"<p>Sample Band bring their critically acclaimed live show to Norwich for one night only this winter.</p>",
          "performer":[{"@type":"MusicGroup","name":"Sample Band"},{"@type":"MusicGroup","name":"Opener One"}]}</script>"""
        html = page("<main><h1>Sample Band</h1><p>Doors 6pm</p></main>", head=head)
        d = ed.extract_details(html)
        self.assertEqual(d["time"], "20:00")
        self.assertEqual(d["doors"], "19:00")
        self.assertEqual(d["age"], "16+")
        self.assertEqual(d["support"], ["Opener One"])
        self.assertTrue(d["description"].startswith("Sample Band bring"))

    def test_json_ld_midnight_means_no_time(self):
        head = """<script type="application/ld+json">{"@type":"Event","name":"X",
          "startDate":"2026-12-04T00:00:00"}</script>"""
        d = ed.extract_details(page("<main><p>Doors 7.30pm, live music from 8.15pm</p></main>", head=head))
        self.assertEqual(d["doors"], "19:30")
        self.assertEqual(d["time"], "20:15")

    def test_support_with_special_guests_and_tba(self):
        d = ed.extract_details(page("<main><p>Plus very special guests: Band A, Band B &amp; Band C</p></main>"))
        self.assertEqual(d["support"], ["Band A", "Band B", "Band C"])
        d = ed.extract_details(page("<main><p>Support: TBA</p></main>"))
        self.assertNotIn("support", d)

    def test_funding_line_is_not_a_support_act(self):
        d = ed.extract_details(page("<main><p>Supported by Arts Council England and the National Lottery.</p>"
                                    "<p>Your support keeps independent venues alive.</p></main>"))
        self.assertNotIn("support", d)

    def test_meta_description_used_when_page_has_no_text(self):
        head = ('<meta property="og:description" content="Local heroes Sample Band headline '
                'a night of noisy indie rock with three of the city\'s best new bands.">')
        d = ed.extract_details(page("<main><h1>Sample Band</h1></main>", head=head))
        self.assertTrue(d["description"].startswith("Local heroes"))

    def test_long_description_is_cut_short(self):
        long_text = "word " * 200
        d = ed.extract_details(page(f"<main><p>{long_text}</p></main>"))
        self.assertLessEqual(len(d["description"]), ed.DESCRIPTION_MAX + 1)
        self.assertTrue(d["description"].endswith("…"))


class RealPageStructures(unittest.TestCase):
    """Layouts copied from real venue pages (Nov 2026), write-ups replaced."""

    def test_uea_waterfront(self):
        html = f"""<html><body><header><nav>What's On The Venues FAQs</nav></header>
        <main><div class="event-hero">
          <h1>Sample Band</h1><p>+ Opening Act</p>
          <div><div>Thu 26 November 2026 7:30pm</div><div>The Adrian Flux Waterfront Studio</div></div>
          <div>Tickets subject to booking fee"</div><a href="/book/">Sold Out</a>
          <div><div><p>Doors Time:</p><p>19:30</p></div>
               <div><p>Curfew:</p><p>23:00</p></div>
               <div><p>Age Restriction:</p><p>14+ (under 16's to be accompanied by an adult)</p></div></div>
        </div>
        <div><p>About Event</p>
          <p>Sample Band roar back to the UK with their big winter tour of small rooms</p>
          <p>{LOREM}</p></div>
        <div class="related-events"><p>You may also like...</p><div>Sun 7 February 2027</div></div>
        <div class="contact-block"><p>Call Us</p><p>Mon-Fri 10am - 4pm</p></div>
        </main><footer>Copyright</footer></body></html>"""
        d = ed.extract_details(html)
        self.assertEqual(d["doors"], "19:30")
        self.assertEqual(d["time"], "19:30")
        self.assertEqual(d["end"], "23:00")
        self.assertEqual(d["age"], "14+")
        self.assertEqual(d["support"], ["Opening Act"])
        self.assertTrue(d["description"].startswith("Sample Band roar back"))

    def test_uea_hours_line_without_class_hint(self):
        # Same page, but the box office block has no helpful class name.
        html = """<main><h1>Sample Band</h1><div><p>Doors Time:</p><p>19:30</p></div>
          <div><p>Call Us</p><p>Mon-Fri 10am - 4pm</p></div></main>"""
        d = ed.extract_details(html)
        self.assertEqual(d["doors"], "19:30")
        self.assertNotIn("time", d)
        self.assertNotIn("end", d)

    def test_norwich_arts_centre(self):
        html = """<html><body><div class="content"><h1>An Evening with Sample Band</h1>
          <strong>Fri 27 Nov 2026</strong>
          <p><em>Approx running times:</em></p><p><em>Sample Band 8pm – end</em></p>
          <p>Sample Band are a four piece known for harmonies, and over the years have toured
          the world supporting the likes of Big Band One, Big Band Two and Big Band Three.</p>
          <div><h3>Book Now</h3><strong>Show(s):</strong><ul><li>At <a>Norwich Arts Centre</a>
            on Fri 27 Nov 2026 @ 8:00 PM</li></ul>
          <strong>nac supporters</strong> Save between £1.50-£3.50+ per ticket. FREE for under 26s.
          <div><strong>VENUE</strong> Open from 7pm</div></div></div></body></html>"""
        d = ed.extract_details(html)
        self.assertEqual(d["doors"], "19:00")
        self.assertEqual(d["time"], "20:00")
        self.assertNotIn("support", d)       # "supporting the likes of..." is biography
        self.assertNotIn("age", d)

    def test_norwich_arts_centre_ignores_supporters_blurb(self):
        blurb = ("NAC Supporters Save between £1.50-£3.50+ per ticket and 10% off the NAC bar. "
                 "Visit our 'Support Us' page to join today. FREE for under 26s.")
        head = ('<script type="application/ld+json">{"@type":"Event","name":"FORM presents Sample",'
                f'"startDate":"2027-03-24T20:00:00+00:00","description":"{blurb}"}}</script>'
                f'<meta property="og:description" content="{blurb}">')
        html = f"""<html><head>{head}</head><body><div><h1>FORM presents Sample</h1>
          <strong>Wed 24 Mar 2027</strong>
          <div><figure></figure><div><p><strong>+ special guests</strong></p>
            <p>Sample are a Birmingham-based duo blending post-punk, indie dance and techno, with
            driving rhythms and a high-energy live sound. Formed in 2023, they released their debut album in 2025.</p>
            <p>For fans of: Band One, Band Two and experimental post-punk.</p></div></div>
          <div><h3>Book Now</h3><div><strong>NAC Supporters</strong> Save between £1.50-£3.50+ per
            ticket and 10% off the NAC bar. Visit our 'Support Us' page to join today. FREE for under 26s.</div>
            <div><strong>Venue</strong> Open from 7.30pm</div></div></div></body></html>"""
        d = ed.extract_details(html, "https://norwichartscentre.co.uk/event/form-presents-sample/")
        self.assertTrue(d["description"].startswith("Sample are a Birmingham-based duo"), d.get("description"))
        self.assertEqual(d["doors"], "19:30")
        self.assertEqual(d["time"], "20:00")
        self.assertNotIn("support", d)       # "+ special guests" names nobody
        # ...and the blurb is rejected even on a site without the page-first rule
        d = ed.extract_details(html, "https://example.com/event/x/")
        self.assertTrue(d["description"].startswith("Sample are a Birmingham-based duo"))

    def test_description_inside_block_named_like_page_furniture(self):
        # The gig sits inside a wrapper whose class contains "sidebar".
        html = f"""<html><body><div class="content-with-sidebar"><div class="event-head">
          <h1>FORM presents Sample</h1><strong>Wed 24 Mar 2027</strong></div>
          <div class="event-body"><p><strong>+ special guests</strong></p><p>{LOREM}</p></div>
          <div class="booking"><div><strong>Venue</strong> Open from 7.30pm</div></div></div>
          <div class="site-sidebar"><p>Sign up to our newsletter and never miss a gig again, it is free forever.</p></div>
          </body></html>"""
        d = ed.extract_details(html, "https://norwichartscentre.co.uk/event/x/")
        self.assertTrue(d["description"].startswith("Sample Band are a four-piece"), d.get("description"))
        self.assertEqual(d["doors"], "19:30")

    def test_description_fallback_after_title(self):
        # Even if the write-up's own block is removed, the paragraph after the title is used.
        html = f"""<html><body><main><h1>Sample</h1>
          <div class="share-and-details"><p>{LOREM}</p></div></main></body></html>"""
        d = ed.extract_details(html, "https://example.com/event/x/")
        self.assertTrue(d["description"].startswith("Sample Band are a four-piece"), d.get("description"))

    def test_old_parser_version_is_refetched(self):
        today = date(2026, 11, 25)
        self.assertTrue(ed.needs_fetch({"checked": "2026-11-25"}, "2027-03-24", today))
        self.assertFalse(ed.needs_fetch({"checked": "2026-11-25", "v": ed.PARSER_VERSION}, "2027-03-24", today))

    def test_no_description_from_norfolk_gig_guide(self):
        html = page(f"<main><h1>Sample Band</h1><p>Doors 8pm</p><p>{LOREM}</p></main>")
        d = ed.extract_details(html, "https://www.norfolkgigguide.com/band-details/Sample+Band")
        self.assertNotIn("description", d)
        self.assertEqual(d["doors"], "20:00")

    def test_clean_cache(self):
        cache = {
            "https://www.norfolkgigguide.com/band-details/X": {"checked": "2026-11-25", "description": LOREM},
            "https://norwichartscentre.co.uk/event/x/": {"checked": "2026-11-25",
                "description": "NAC Supporters Save between £1.50-£3.50+ per ticket and 10% off the NAC bar."},
            "https://www.voodoodaddysshowroom.co.uk/event/y/": {"checked": "2026-11-25", "description": LOREM},
        }
        cache = ed.clean_cache(cache)
        self.assertNotIn("description", cache["https://www.norfolkgigguide.com/band-details/X"])
        self.assertNotIn("description", cache["https://norwichartscentre.co.uk/event/x/"])
        self.assertTrue(ed.needs_fetch(cache["https://norwichartscentre.co.uk/event/x/"], "2027-03-24", date(2026, 11, 25)))
        self.assertEqual(cache["https://www.voodoodaddysshowroom.co.uk/event/y/"]["description"], LOREM)

    def test_dead_wax_squarespace(self):
        html = """<main><article><a href="/whats-on">← Back to All Events</a><article>
          <h1>Sample Band + Niche + Godkill</h1>
          <ul><li><time>Wednesday, November 25, 2026</time></li>
              <li><span><time>7:00 PM</time><span>–</span><time>11:00 PM</time></span></li></ul>
          <p>This is Sample Band, a display of terrible affairs brought live to the stage with theatrics.</p>
          </article><div class="item-pagination"><a>Previous November 23 another night</a></div>
          </article></main>"""
        d = ed.extract_details(html)
        self.assertEqual(d["time"], "19:00")
        self.assertEqual(d["end"], "23:00")
        self.assertTrue(d["description"].startswith("This is Sample Band"))


class Refresh(unittest.TestCase):
    def test_rules(self):
        today = date(2026, 11, 25)
        self.assertTrue(ed.needs_fetch(None, "2026-12-20", today))
        self.assertFalse(ed.needs_fetch({"checked": "2026-11-20", "v": ed.PARSER_VERSION}, "2026-12-20", today))
        self.assertTrue(ed.needs_fetch({"checked": "2026-11-10"}, "2026-12-20", today))
        # gig in 2 days with no time yet: checked again after 2 days
        self.assertTrue(ed.needs_fetch({"checked": "2026-11-23"}, "2026-11-27", today))
        self.assertFalse(ed.needs_fetch({"checked": "2026-11-23", "time": "19:30", "v": ed.PARSER_VERSION}, "2026-11-27", today))
        # a failed fetch is retried the next day
        self.assertTrue(ed.needs_fetch({"checked": "2026-11-24", "error": "HTTP 503"}, "2026-12-20", today))
        self.assertFalse(ed.needs_fetch({"checked": "2026-11-25", "error": "HTTP 503", "v": ed.PARSER_VERSION}, "2026-12-20", today))

    def test_skip_listing_pages(self):
        skip = ed.load_skip_urls()
        self.assertFalse(ed.is_gig_page("https://www.instagram.com/lastpubstanding/?hl=en", skip))
        self.assertFalse(ed.is_gig_page("https://thehollowaynorwich.com/events", skip))
        self.assertFalse(ed.is_gig_page("https://brickmakersnorwich.co.uk/", skip))
        self.assertTrue(ed.is_gig_page("https://www.voodoodaddysshowroom.co.uk/event/the-love-buzz/", skip))


if __name__ == "__main__":
    unittest.main()
