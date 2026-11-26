import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from archive_gigs import Archive  # noqa: E402
import import_sheets  # noqa: E402
from datetime import date  # noqa: E402


def gig(name, date="2026-10-10", venue="Brickmakers", **kw):
    return {"event_name": name, "date": date, "venue": venue, "url": kw.pop("url", ""), **kw}


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "gig_archive.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_gig_on_several_days_is_kept_once(self):
        a = Archive(self.path)
        a.add_day("2026-10-01", [gig("The Band", price="£10")])
        a.add_day("2026-10-02", [gig("THE BAND!", price="£12")])
        self.assertEqual(len(a.gigs), 1)
        g = a.gigs[0]
        self.assertEqual((g["first_seen"], g["last_seen"], g["price"]), ("2026-10-01", "2026-10-02", "£12"))
        self.assertEqual(g["event_name"], "THE BAND!")  # newest name wins

    def test_details_are_not_lost_when_a_venue_drops_them(self):
        a = Archive(self.path)
        a.add_day("2026-10-01", [gig("The Band", price="£10", time="19:30", support="Opener")])
        a.add_day("2026-10-09", [gig("The Band", price="", time="")])
        g = a.gigs[0]
        self.assertEqual((g["price"], g["time"], g["support"]), ("£10", "19:30", "Opener"))

    def test_double_bill_stays_two_gigs(self):
        a = Archive(self.path)
        a.add_day("2026-10-01", [gig("Hangry Yeti + Lara Price"), gig("Anna B (DJ set)")])
        self.assertEqual(len(a.gigs), 2)

    def test_renamed_listing_is_merged(self):
        a = Archive(self.path)
        a.add_day("2026-10-01", [gig("Open Mic with Peter T"), gig("Other gig", date="2026-10-20")])
        a.add_day("2026-10-02", [gig("Open mic night with Peter Turrell"), gig("Other gig", date="2026-10-20")])
        a.save()
        names = [g["event_name"] for g in a.gigs if g["date"] == "2026-10-10"]
        self.assertEqual(names, ["Open mic night with Peter Turrell"])
        g = next(g for g in a.gigs if g["date"] == "2026-10-10")
        self.assertEqual(g["also_listed_as"], ["Open Mic with Peter T"])
        self.assertEqual(g["first_seen"], "2026-10-01")
        self.assertNotIn("removed_early", g)

    def test_gig_that_vanishes_early_is_flagged(self):
        a = Archive(self.path)
        a.add_day("2026-10-01", [gig("Cancelled Band"), gig("Other gig", date="2026-10-20")])
        a.add_day("2026-10-02", [gig("Other gig", date="2026-10-20")])
        a.add_day("2026-10-03", [gig("Other gig", date="2026-10-20")])
        a.save()
        flagged = {g["event_name"]: g.get("removed_early", False) for g in a.gigs}
        self.assertEqual(flagged, {"Cancelled Band": True, "Other gig": False})

    def test_venue_scraper_outage_is_not_a_cancellation(self):
        a = Archive(self.path)
        a.add_day("2026-10-01", [gig("Band"), gig("Elsewhere", venue="Epic")])
        a.add_day("2026-10-02", [gig("Elsewhere", venue="Epic")])  # Brickmakers not scraped
        a.save()
        self.assertFalse(next(g for g in a.gigs if g["event_name"] == "Band").get("removed_early", False))

    def test_sheet_gig_fills_gaps_and_keeps_its_name(self):
        a = Archive(self.path)
        a.add_day("2026-10-01", [gig("Round Midnight presents Yetii")])
        self.assertEqual(a.add_external(gig("Yetii", price="£8")), "merged")
        g = a.gigs[0]
        self.assertEqual((g["event_name"], g["price"], g["also_listed_as"], g["from_sheet"]),
                         ("Round Midnight presents Yetii", "£8", ["Yetii"], True))

    def test_sheet_only_gig_is_never_flagged_as_cancelled(self):
        a = Archive(self.path)
        a.add_external(gig("Hand-listed gig", date="2026-10-20"))
        a.add_day("2026-10-01", [gig("Other", date="2026-10-25")])
        a.add_day("2026-10-02", [gig("Other", date="2026-10-25")])
        a.save()
        g = next(g for g in a.gigs if g["event_name"] == "Hand-listed gig")
        self.assertNotIn("removed_early", g)
        self.assertNotIn("last_seen", g)

    def test_different_sheet_gig_same_night_is_added(self):
        a = Archive(self.path)
        a.add_day("2026-10-01", [gig("Hips (soul stew)")])
        self.assertEqual(a.add_external(gig("Diving Deep with Bag of Cans")), "new")

    def test_saved_archive_reloads(self):
        a = Archive(self.path)
        a.add_day("2026-10-01", [gig("The Band")])
        a.save()
        b = Archive(self.path)
        b.add_day("2026-10-02", [gig("The Band")])
        self.assertEqual(len(b.gigs), 1)
        self.assertEqual(b.gigs[0]["last_seen"], "2026-10-02")


class SheetReadingTests(unittest.TestCase):
    def test_dates(self):
        p = import_sheets.parse_day
        self.assertEqual([p("Oct 14th "), p("September 1st"), p("27th Mar"), p("dec 3rd"), p("Price")],
                         [(10, 14), (9, 1), (3, 27), (12, 3), None])

    def test_year_looks_forward_from_the_version(self):
        y = import_sheets.with_year
        self.assertEqual(y(8, 23, date(2026, 1, 30)), "2026-08-23")   # listed early
        self.assertEqual(y(9, 1, date(2025, 9, 26)), "2025-09-01")
        self.assertEqual(y(1, 22, date(2026, 7, 31)), "2027-01-22")

    def test_version_dates_from_file_names(self):
        from pathlib import Path
        v = import_sheets.version_date
        today = date(2026, 11, 26)
        self.assertEqual(v(Path("Copy of SHEET 2 - 26 September 2025, 12_23.xlsx"), today), date(2025, 9, 26))
        self.assertEqual(v(Path("Copy of SHEET 2 - 30 January, 13_45.xlsx"), today), date(2026, 1, 30))
        self.assertEqual(v(Path("SHEET 2.xlsx"), today), today)


if __name__ == "__main__":
    unittest.main()
