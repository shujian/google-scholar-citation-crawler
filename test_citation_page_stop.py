import sys
import types
import unittest
from unittest.mock import patch
from io import StringIO


class _DummyNav:
    def __init__(self):
        self._session1 = types.SimpleNamespace(headers={}, cookies={})
        self._session2 = types.SimpleNamespace(headers={}, cookies={})
        self.pm1 = types.SimpleNamespace(_handle_captcha2=lambda pagerequest: None)
        self.pm2 = types.SimpleNamespace(_handle_captcha2=lambda pagerequest: None)
        self.got_403 = False

    def _set_retries(self, retries):
        self.retries = retries

    def _get_page(self, pagerequest, premium=False):
        return None

    def _new_session(self, premium=True, **kwargs):
        return None


class _DummyIterator:
    def __init__(self, *args, **kwargs):
        pass

    def __iter__(self):
        return self

    def __next__(self):
        raise StopIteration


scholarly_mod = types.ModuleType("scholarly")
scholarly_mod.scholarly = types.SimpleNamespace(
    _Scholarly__nav=_DummyNav(),
    _citedby_long=lambda obj, years: iter(()),
)
scholarly_mod.ProxyGenerator = object
sys.modules.setdefault("scholarly", scholarly_mod)

proxy_mod = types.ModuleType("scholarly._proxy_generator")
proxy_mod.MaxTriesExceededException = Exception
sys.modules.setdefault("scholarly._proxy_generator", proxy_mod)

pub_parser_mod = types.ModuleType("scholarly.publication_parser")
pub_parser_mod._SearchScholarIterator = _DummyIterator
sys.modules.setdefault("scholarly.publication_parser", pub_parser_mod)

openpyxl_mod = types.ModuleType("openpyxl")
openpyxl_mod.Workbook = object
sys.modules.setdefault("openpyxl", openpyxl_mod)

styles_mod = types.ModuleType("openpyxl.styles")
styles_mod.Font = object
styles_mod.PatternFill = object
styles_mod.Alignment = object
sys.modules.setdefault("openpyxl.styles", styles_mod)

import scholar_citation


class CitationPageStopTests(unittest.TestCase):
    def setUp(self):
        self.fetcher = scholar_citation.PaperCitationFetcher("test-author", output_dir=".")
        self.fetcher._completed_year_segments = set()
        self.fetcher._partial_year_start = {}
        self.fetcher._probed_year_counts = None
        self.fetcher._probed_year_count_complete = False
        self.fetcher._cached_year_counts = {}
        self.fetcher._dedup_count = 0
        self.fetcher._new_citations_count = 0
        self.fetcher._total_page_count = 0
        self.fetcher._delay_scale = 0
        self.fetcher._probed_year_counts = None
        self.fetcher._probed_year_count_complete = False
        self.fetcher.interactive_captcha = False
        self.fetcher._last_scholar_url = "https://scholar.google.com/citations?user=test-author&hl=en"
        self.fetcher._current_attempt_url = None
        self.fetcher._probe_citation_start_year = lambda citedby_url, num_citations=None, pub_year=None: 2025
        self.fetcher._refresh_scholarly_session = lambda: None
        self.fetcher._try_interactive_captcha = lambda url: False
        self.fetcher._wait_proxy_switch = lambda max_hours=24: None

    def test_fetch_by_year_finishes_current_page_before_stopping(self):
        self.fetcher._probe_citation_start_year = lambda citedby_url, num_citations=None, pub_year=None: 2026
        pages = {
            (2026, 0): [
                {"bib": {"title": "Page1-A", "author": ["A"], "venue": "V1", "pub_year": "2026"}, "pub_url": "u1"},
                {"bib": {"title": "Page1-B", "author": ["B"], "venue": "V2", "pub_year": "2026"}, "pub_url": "u2"},
                {"bib": {"title": "Page1-C", "author": ["C"], "venue": "V3", "pub_year": "2026"}, "pub_url": "u3"},
            ],
            (2026, 10): [
                {"bib": {"title": "Page2-A", "author": ["D"], "venue": "V4", "pub_year": "2026"}, "pub_url": "u4"},
            ],
            (2025, 0): [
                {"bib": {"title": "OldYear-A", "author": ["E"], "venue": "V5", "pub_year": "2025"}, "pub_url": "u5"},
            ],
        }
        requests = []

        class FakeIterator:
            def __init__(self, nav, url):
                start = 0
                if "start=" in url:
                    start = int(url.split("start=")[1].split("&")[0])
                year = int(url.split("as_ylo=")[1].split("&")[0])
                self.items = list(pages.get((year, start), []))
                self.index = 0
                self._finished_current_page = False
                requests.append((year, start))

            def __iter__(self):
                return self

            def __next__(self):
                if self.index >= len(self.items):
                    self._finished_current_page = True
                    raise StopIteration
                item = self.items[self.index]
                self.index += 1
                if self.index >= len(self.items):
                    self._finished_current_page = True
                return item

        save_calls = []

        with patch.object(scholar_citation, "_SearchScholarIterator", FakeIterator):
            citations = self.fetcher._fetch_by_year(
                citedby_url="/scholar?cites=123",
                citations=[],
                save_progress=lambda complete: save_calls.append(complete),
                num_citations=2,
                pub_year="2026",
                prev_scholar_count=1,
            )

        self.assertEqual([c["title"] for c in citations], ["Page1-A", "Page1-B", "Page1-C"])
        self.assertEqual(requests, [(2026, 0)])
        self.assertEqual(self.fetcher._new_citations_count, 3)
        self.assertTrue(save_calls)
        self.assertTrue(save_calls[-1])
    def test_recheck_mode_does_not_stop_after_recovering_scholar_increase(self):
        pages = {
            (2026, 0): [
                {"bib": {"title": "Page1-A", "author": ["A"], "venue": "V1", "pub_year": "2026"}, "pub_url": "u1"},
                {"bib": {"title": "Page1-B", "author": ["B"], "venue": "V2", "pub_year": "2026"}, "pub_url": "u2"},
            ],
            (2026, 10): [
                {"bib": {"title": "Page2-A", "author": ["C"], "venue": "V3", "pub_year": "2026"}, "pub_url": "u3"},
            ],
            (2025, 0): [
                {"bib": {"title": "OldYear-A", "author": ["D"], "venue": "V4", "pub_year": "2025"}, "pub_url": "u4"},
            ],
        }
        requests = []

        class FakeIterator:
            def __init__(self, nav, url):
                start = 0
                if "start=" in url:
                    start = int(url.split("start=")[1].split("&")[0])
                year = int(url.split("as_ylo=")[1].split("&")[0])
                if year == 2026 and start == 0:
                    self.items = list(pages[(2026, 0)])
                elif year == 2026 and start == 2:
                    self.items = list(pages[(2026, 10)])
                else:
                    self.items = list(pages.get((year, start), []))
                self.index = 0
                self._finished_current_page = False
                requests.append((year, start))

            def __iter__(self):
                return self

            def __next__(self):
                if self.index >= len(self.items):
                    self._finished_current_page = True
                    raise StopIteration
                item = self.items[self.index]
                self.index += 1
                if self.index >= len(self.items):
                    self._finished_current_page = True
                return item

        with patch.object(scholar_citation, "_SearchScholarIterator", FakeIterator):
            citations = self.fetcher._fetch_by_year(
                citedby_url="/scholar?cites=123",
                citations=[],
                save_progress=lambda complete: None,
                num_citations=12,
                pub_year="2026",
                prev_scholar_count=10,
                allow_incremental_early_stop=False,
            )

        self.assertFalse(
            scholar_citation.PaperCitationFetcher._get_early_stop_status(
                len(citations), 12, 2, 10, allow_incremental_early_stop=False,
            )["should_stop"]
        )

        self.assertEqual(
            [c["title"] for c in citations],
            ["OldYear-A", "Page1-A", "Page1-B"],
        )
        self.assertEqual(requests, [(2025, 0), (2026, 0)])
        self.assertEqual(self.fetcher._new_citations_count, 3)

    def test_recheck_mode_uses_oldest_to_newest_plan(self):
        plan = scholar_citation.PaperCitationFetcher._build_year_fetch_plan(
            2020, 2026, 10, 12, allow_incremental_early_stop=False,
        )

        self.assertEqual(list(plan["year_range"]), list(range(2020, 2027)))
        self.assertEqual(plan["direction_label"], "oldest→newest")
        self.assertEqual(plan["direction_reason"], "recheck mode, full year revalidation")

    def test_incomplete_histogram_falls_back_to_pub_year(self):
        class FakeBar:
            def __init__(self, year, count):
                self.year = year
                self.count = count

            def get(self, key, default=None):
                if key == "data-year":
                    return str(self.year)
                if key == "data-count":
                    return str(self.count)
                return default

        class FakeSoup:
            def select(self, selector):
                return [
                    FakeBar(2024, 2),
                    FakeBar(2025, 3),
                ]

            def find_all(self, *args, **kwargs):
                return []

        nav = scholarly_mod.scholarly._Scholarly__nav
        nav._get_soup = lambda citedby_url: FakeSoup()
        original_probe = scholar_citation.PaperCitationFetcher._probe_citation_start_year

        with patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            start_year = original_probe(
                self.fetcher,
                "/scholar?cites=123",
                num_citations=10,
                pub_year="2021",
            )

        output = fake_stdout.getvalue()
        self.assertEqual(start_year, 2021)
        self.assertEqual(self.fetcher._probed_year_counts, {2024: 2, 2025: 3})
        self.assertFalse(self.fetcher._probed_year_count_complete)
        self.assertEqual(sum(self.fetcher._probed_year_counts.values()), 5)
        self.assertIn("histogram incomplete", output)
        self.assertIn("Year histogram summary:", output)
        self.assertIn("pub_year=2021 (pub_year fallback applied)", output)

    def test_fetch_by_year_does_not_skip_years_when_histogram_incomplete(self):
        self.fetcher._probed_year_counts = {2024: 0, 2025: 2}
        self.fetcher._probed_year_count_complete = False
        self.fetcher._probe_citation_start_year = lambda citedby_url, num_citations=None, pub_year=None: 2024

        requests = []

        class FakeIterator:
            def __init__(self, nav, url):
                start = 0
                if "start=" in url:
                    start = int(url.split("start=")[1].split("&")[0])
                year = int(url.split("as_ylo=")[1].split("&")[0])
                self.items = [{
                    "bib": {"title": f"Y{year}", "author": ["A"], "venue": f"V{year}", "pub_year": str(year)},
                    "pub_url": f"u{year}",
                }]
                self.index = 0
                self._finished_current_page = False
                requests.append((year, start))

            def __iter__(self):
                return self

            def __next__(self):
                if self.index >= len(self.items):
                    self._finished_current_page = True
                    raise StopIteration
                item = self.items[self.index]
                self.index += 1
                if self.index >= len(self.items):
                    self._finished_current_page = True
                return item

        with patch.object(scholar_citation, "_SearchScholarIterator", FakeIterator), \
             patch.object(scholar_citation, "datetime") as fake_datetime:
            fake_datetime.now.return_value = types.SimpleNamespace(year=2025)
            citations = self.fetcher._fetch_by_year(
                citedby_url="/scholar?cites=123",
                citations=[],
                save_progress=lambda complete: None,
                num_citations=10,
                pub_year="2020",
                prev_scholar_count=0,
                allow_incremental_early_stop=False,
            )

        self.assertEqual(requests, [(2024, 0), (2025, 0)])
        self.assertEqual([c["title"] for c in citations], ["Y2024", "Y2025"])

    def test_complete_histogram_still_skips_matching_year_counts(self):
        self.fetcher._probed_year_counts = {2024: 0, 2025: 2}
        self.fetcher._probed_year_count_complete = True
        self.fetcher._cached_year_counts = {2025: 2}
        self.fetcher._probe_citation_start_year = lambda citedby_url, num_citations=None, pub_year=None: 2024

        requests = []

        class FakeIterator:
            def __init__(self, nav, url):
                start = 0
                if "start=" in url:
                    start = int(url.split("start=")[1].split("&")[0])
                year = int(url.split("as_ylo=")[1].split("&")[0])
                self.items = [{
                    "bib": {"title": f"Y{year}", "author": ["A"], "venue": f"V{year}", "pub_year": str(year)},
                    "pub_url": f"u{year}",
                }]
                self.index = 0
                self._finished_current_page = False
                requests.append((year, start))

            def __iter__(self):
                return self

            def __next__(self):
                if self.index >= len(self.items):
                    self._finished_current_page = True
                    raise StopIteration
                item = self.items[self.index]
                self.index += 1
                if self.index >= len(self.items):
                    self._finished_current_page = True
                return item

        with patch.object(scholar_citation, "_SearchScholarIterator", FakeIterator), \
             patch.object(scholar_citation, "datetime") as fake_datetime, \
             patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            fake_datetime.now.return_value = types.SimpleNamespace(year=2025)
            citations = self.fetcher._fetch_by_year(
                citedby_url="/scholar?cites=123",
                citations=[],
                save_progress=lambda complete: None,
                num_citations=10,
                pub_year="2020",
                prev_scholar_count=0,
                allow_incremental_early_stop=False,
            )

        output = fake_stdout.getvalue()
        self.assertEqual(requests, [])
        self.assertEqual(citations, [])
        self.assertIn("    Probe summary:", output)
        self.assertIn("    Cache summary:", output)
        self.assertIn("skip (probe count=0, probe_complete=True)", output)
        self.assertIn("histogram count match", output)

    def test_total_count_match_skips_fetch_when_cached_citations_include_missing_years(self):
        cached_citations = [
            {"title": "Y2025", "author": ["A"], "venue": "V2025", "year": 2025, "pub_url": "u1"},
            {"title": "Y2024", "author": ["B"], "venue": "V2024", "year": 2024, "pub_url": "u2"},
            {"title": "NoYear", "author": ["C"], "venue": "V?", "year": "N/A", "pub_url": "u3"},
        ]
        self.fetcher._probed_year_counts = {2024: 1, 2025: 1}
        self.fetcher._probed_year_count_complete = True
        self.fetcher._cached_year_counts = {2024: 1, 2025: 1}
        self.fetcher._probe_citation_start_year = lambda citedby_url, num_citations=None, pub_year=None: 2024

        requests = []
        save_calls = []

        class FakeIterator:
            def __init__(self, nav, url):
                requests.append(url)
                self.items = []
                self.index = 0
                self._finished_current_page = True

            def __iter__(self):
                return self

            def __next__(self):
                raise StopIteration

        with patch.object(scholar_citation, "_SearchScholarIterator", FakeIterator), \
             patch.object(scholar_citation, "datetime") as fake_datetime, \
             patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            fake_datetime.now.return_value = types.SimpleNamespace(year=2025)
            citations = self.fetcher._fetch_by_year(
                citedby_url="/scholar?cites=123",
                citations=list(cached_citations),
                save_progress=lambda complete: save_calls.append(complete),
                num_citations=3,
                pub_year="2020",
                prev_scholar_count=0,
                allow_incremental_early_stop=False,
            )

        output = fake_stdout.getvalue()
        self.assertEqual(requests, [])
        self.assertEqual(citations, cached_citations)
        self.assertEqual(save_calls, [False, True])
        self.assertIn("    Probe totals: scholar_total=3, histogram_total=2, missing_from_histogram=1", output)
        self.assertIn("    Cache totals: total=3, year_total=2, unyeared=1", output)
        self.assertIn("Year fetch skipped: total-count fallback", output)
        self.assertIn("cached_total=3, scholar_total=3", output)
        self.assertIn("citations without usable year metadata", output)

    def test_total_count_match_does_not_skip_when_partial_resume_exists(self):
        cached_citations = [
            {"title": "Y2025", "author": ["A"], "venue": "V2025", "year": 2025, "pub_url": "u1"},
            {"title": "Y2024", "author": ["B"], "venue": "V2024", "year": 2024, "pub_url": "u2"},
            {"title": "NoYear", "author": ["C"], "venue": "V?", "year": "N/A", "pub_url": "u3"},
        ]
        self.fetcher._probed_year_counts = {2024: 1, 2025: 1}
        self.fetcher._probed_year_count_complete = True
        self.fetcher._cached_year_counts = {2024: 1, 2025: 1}
        self.fetcher._partial_year_start = {2025: 2}
        self.fetcher._probe_citation_start_year = lambda citedby_url, num_citations=None, pub_year=None: 2024

        requests = []

        class FakeIterator:
            def __init__(self, nav, url):
                start = 0
                if "start=" in url:
                    start = int(url.split("start=")[1].split("&")[0])
                year = int(url.split("as_ylo=")[1].split("&")[0])
                self.items = [{
                    "bib": {"title": f"Fetched-{year}", "author": ["D"], "venue": f"V{year}", "pub_year": str(year)},
                    "pub_url": f"uf{year}",
                }]
                self.index = 0
                self._finished_current_page = False
                requests.append((year, start))

            def __iter__(self):
                return self

            def __next__(self):
                if self.index >= len(self.items):
                    self._finished_current_page = True
                    raise StopIteration
                item = self.items[self.index]
                self.index += 1
                if self.index >= len(self.items):
                    self._finished_current_page = True
                return item

        with patch.object(scholar_citation, "_SearchScholarIterator", FakeIterator), \
             patch.object(scholar_citation, "datetime") as fake_datetime:
            fake_datetime.now.return_value = types.SimpleNamespace(year=2025)
            citations = self.fetcher._fetch_by_year(
                citedby_url="/scholar?cites=123",
                citations=list(cached_citations),
                save_progress=lambda complete: None,
                num_citations=3,
                pub_year="2020",
                prev_scholar_count=0,
                allow_incremental_early_stop=False,
            )

        self.assertEqual(requests, [(2025, 2)])
        self.assertEqual(citations[-1]["title"], "Fetched-2025")

    def test_resume_logging_includes_year_context(self):
        self.fetcher._probed_year_counts = {2025: 3}
        self.fetcher._probed_year_count_complete = True
        self.fetcher._cached_year_counts = {2025: 1}
        self.fetcher._partial_year_start = {2025: 2}
        self.fetcher._probe_citation_start_year = lambda citedby_url, num_citations=None, pub_year=None: 2025

        requests = []

        class FakeIterator:
            def __init__(self, nav, url):
                start = 0
                if "start=" in url:
                    start = int(url.split("start=")[1].split("&")[0])
                year = int(url.split("as_ylo=")[1].split("&")[0])
                self.items = [{
                    "bib": {"title": f"Y{year}", "author": ["A"], "venue": f"V{year}", "pub_year": str(year)},
                    "pub_url": f"u{year}",
                }]
                self.index = 0
                self._finished_current_page = False
                requests.append((year, start))

            def __iter__(self):
                return self

            def __next__(self):
                if self.index >= len(self.items):
                    self._finished_current_page = True
                    raise StopIteration
                item = self.items[self.index]
                self.index += 1
                if self.index >= len(self.items):
                    self._finished_current_page = True
                return item

        with patch.object(scholar_citation, "_SearchScholarIterator", FakeIterator), \
             patch.object(scholar_citation, "datetime") as fake_datetime, \
             patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            fake_datetime.now.return_value = types.SimpleNamespace(year=2025)
            citations = self.fetcher._fetch_by_year(
                citedby_url="/scholar?cites=123",
                citations=[],
                save_progress=lambda complete: None,
                num_citations=10,
                pub_year="2020",
                prev_scholar_count=0,
                allow_incremental_early_stop=False,
            )

        output = fake_stdout.getvalue()
        self.assertEqual(requests, [(2025, 2)])
        self.assertEqual([c["title"] for c in citations], ["Y2025"])
        self.assertIn("    Partial resume points: 2025->2", output)
        self.assertIn("Year 2025: resuming from position 2 (cached=1, probe=3)", output)
        self.assertIn("Year 2025 status:", output)

