"""Tests for the code-side filters. Standard library only:

  python3 -m unittest discover tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import ats  # noqa: E402
import discover  # noqa: E402

RULES = {
    "run": {"region_order": ["new_york", "london"]},
    "regions": {
        "new_york": {"location_keywords": ["new york", "nyc"], "remote_country_keywords": ["united states"]},
        "london": {"location_keywords": ["london"], "location_exclude": ["ontario", "london, on"],
                   "remote_country_keywords": ["united kingdom"]},
    },
}
TARGETING = {
    "title_include": ["product manager", "strategy and operations"],
    "title_exclude": ["intern", "associate"],
    "blocked_languages": ["german", "japanese"],
}


def job(**kw):
    return {"title": "", "description": "", "location": "", **kw}


class Titles(unittest.TestCase):
    def test_include_and_exclude(self):
        self.assertTrue(discover.title_ok("Senior Product Manager, Payments", TARGETING))
        self.assertFalse(discover.title_ok("Associate Product Manager", TARGETING))
        self.assertFalse(discover.title_ok("Product Designer", TARGETING))

    def test_and_matches_ampersand(self):
        self.assertTrue(discover.title_ok("Strategy & Operations Lead", TARGETING))

    def test_region_extra_titles(self):
        self.assertTrue(discover.title_ok("Product Owner", TARGETING, ["product owner"]))


class Regions(unittest.TestCase):
    def test_city(self):
        self.assertEqual(discover.classify_region(job(location="New York, NY"), RULES), "new_york")

    def test_london_ontario_is_not_london(self):
        self.assertIsNone(discover.classify_region(job(location="London, ON, Canada"), RULES))
        self.assertEqual(discover.classify_region(job(location="London, UK"), RULES), "london")

    def test_remote_needs_a_matching_country(self):
        self.assertEqual(discover.classify_region(job(location="Remote - United States"), RULES), "new_york")
        self.assertIsNone(discover.classify_region(job(location="Remote - Canada"), RULES))


class Languages(unittest.TestCase):
    def test_required_language_blocks(self):
        reason = discover.language_block(job(description="Fluent German is required for this role."), TARGETING)
        self.assertEqual(reason, "requires German")

    def test_nice_to_have_passes(self):
        self.assertIsNone(discover.language_block(job(description="Fluent German is a plus."), TARGETING))

    def test_market_mention_passes(self):
        self.assertIsNone(discover.language_block(job(description="You'll own our expansion into the German market."), TARGETING))

    def test_cjk_posting_blocks_only_when_blocked(self):
        text = "東京オフィスでプロダクトマネージャーを募集しています。" * 20
        self.assertIsNotNone(discover.language_block(job(description=text), TARGETING))
        self.assertIsNone(discover.language_block(job(description=text), {"blocked_languages": ["german"]}))


class Pay(unittest.TestCase):
    def test_usd_range_top(self):
        self.assertEqual(ats.max_annual_pay("The base salary range is $140,000 - $180,000.", "USD"), 180000)

    def test_usd_ignores_other_dollars(self):
        self.assertIsNone(ats.max_annual_pay("CA$150,000 - CA$190,000", "USD"))
        self.assertEqual(ats.max_annual_pay("CA$150,000 - CA$190,000", "CAD"), 190000)

    def test_monthly_is_annualised(self):
        self.assertEqual(ats.max_annual_pay("EUR 6,000 per month", "EUR"), 72000)

    def test_k_suffix(self):
        self.assertEqual(ats.max_annual_pay("£70k-£85k", "GBP"), 85000)

    def test_hourly_is_ignored(self):
        self.assertIsNone(ats.max_annual_pay("$95 per hour", "USD"))

    def test_too_low_against_floor(self):
        rules = {"regions": {"new_york": {"salary_floor": 150000, "salary_currency": "USD"}}}
        self.assertTrue(discover.pay_too_low({"region": "new_york", "compensation": "$100,000-$120,000"}, rules))
        self.assertFalse(discover.pay_too_low({"region": "new_york", "compensation": "$130,000-$170,000"}, rules))


class BoardUrls(unittest.TestCase):
    def test_known_sites(self):
        self.assertEqual(ats.parse_board_url("https://job-boards.greenhouse.io/example/jobs/123"), ("greenhouse", "example"))
        self.assertEqual(ats.parse_board_url("https://jobs.ashbyhq.com/example/abc"), ("ashby", "example"))
        self.assertEqual(ats.parse_board_url("https://jobs.lever.co/example/xyz/apply"), ("lever", "example"))
        self.assertEqual(ats.parse_board_url("https://example.bamboohr.com/careers/12"), ("bamboohr", "example"))

    def test_reserved_paths_are_not_companies(self):
        self.assertIsNone(ats.parse_board_url("https://jobs.lever.co/login"))
        self.assertIsNone(ats.parse_board_url("https://example.com/careers"))


if __name__ == "__main__":
    unittest.main()
