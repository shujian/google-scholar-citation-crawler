import sys
import types
import unittest
from unittest.mock import patch
from io import StringIO
import json
import os
import tempfile


class _CookieJar(dict):
    def set(self, key, value):
        self[key] = value


class _DummyNav:
    def __init__(self):
        self._session1 = types.SimpleNamespace(headers={}, cookies=_CookieJar())
        self._session2 = types.SimpleNamespace(headers={}, cookies=_CookieJar())
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


class _DummyWorkbook:
    def __init__(self):
        self.active = _DummyWorksheet()
        self.sheets = [self.active]

    def create_sheet(self, title):
        ws = _DummyWorksheet()
        ws.title = title
        self.sheets.append(ws)
        return ws

    def save(self, path):
        self.saved_path = path


class _DummyWorksheet:
    def __init__(self):
        self.title = ""
        self.column_dimensions = _DimensionMap()
        self.row_dimensions = _DimensionMap()
        self.cells = {}
        self.merged_ranges = []

    def merge_cells(self, cell_range):
        self.merged_ranges.append(cell_range)

    def cell(self, row, column, value=None):
        key = (row, column)
        if key not in self.cells:
            self.cells[key] = _DummyCell()
        cell = self.cells[key]
        if value is not None:
            cell.value = value
        return cell


class _DummyCell:
    def __init__(self):
        self.value = None
        self.fill = None
        self.font = None
        self.alignment = None
        self.hyperlink = None


class _DimensionMap(dict):
    def __missing__(self, key):
        value = types.SimpleNamespace(width=None, height=None)
        self[key] = value
        return value


scholarly_mod = types.ModuleType("scholarly")
scholarly_mod.scholarly = types.SimpleNamespace(
    _Scholarly__nav=_DummyNav(),
    _citedby_long=lambda obj, years: iter(()),
    citedby=lambda obj: iter(()),
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
openpyxl_mod.Workbook = _DummyWorkbook
sys.modules.setdefault("openpyxl", openpyxl_mod)

class _Style:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


styles_mod = types.ModuleType("openpyxl.styles")
styles_mod.Font = _Style
styles_mod.PatternFill = _Style
styles_mod.Alignment = _Style
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
        self.fetcher._papers_fetched_count = 0
        self.fetcher._delay_scale = 0
        self.fetcher._probed_year_counts = None
        self.fetcher._probed_year_count_complete = False
        self.fetcher.interactive_captcha = False
        self.fetcher._last_scholar_url = "https://scholar.google.com/citations?user=test-author&hl=en"
        self.fetcher._current_attempt_url = None
        self.fetcher._probe_citation_start_year = lambda citedby_url, num_citations=None, pub_year=None: 2025
        self.fetcher._refresh_scholarly_session = lambda: None
        self.fetcher._try_interactive_captcha = lambda url: False
        self.fetcher._injected_cookies = {}
        self.fetcher._injected_header_overrides = {}
        self.fetcher._curl_header_allowlist = {
            'accept',
            'accept-language',
            'priority',
            'sec-ch-ua',
            'sec-ch-ua-arch',
            'sec-ch-ua-bitness',
            'sec-ch-ua-full-version-list',
            'sec-ch-ua-mobile',
            'sec-ch-ua-model',
            'sec-ch-ua-platform',
            'sec-ch-ua-platform-version',
            'sec-ch-ua-wow64',
        }

    def test_inject_curl_keeps_cookie_only_behavior(self):
        curl = "curl 'https://scholar.google.com/scholar?cites=123' -b 'SID=abc; HSID=def'"

        injected = self.fetcher._inject_cookies_from_curl(curl)

        nav = scholarly_mod.scholarly._Scholarly__nav
        self.assertEqual(injected, 2)
        self.assertEqual(self.fetcher._injected_cookies, {'SID': 'abc', 'HSID': 'def'})
        self.assertEqual(self.fetcher._injected_header_overrides, {})
        self.assertEqual(nav._session1.cookies['SID'], 'abc')
        self.assertEqual(nav._session2.cookies['HSID'], 'def')
        self.assertEqual(nav._session1.headers['referer'], self.fetcher._last_scholar_url)

    def test_inject_curl_persists_allowlisted_headers(self):
        curl = (
            "curl 'https://scholar.google.com/scholar?cites=123' "
            "-b 'SID=abc' "
            "-H 'Accept-Language: en-US,en;q=0.9' "
            "-H 'sec-ch-ua-platform: \"Windows\"' "
            "-H 'Priority: u=1, i'"
        )

        injected = self.fetcher._inject_cookies_from_curl(curl)

        nav = scholarly_mod.scholarly._Scholarly__nav
        self.assertEqual(injected, 1)
        self.assertEqual(
            self.fetcher._injected_header_overrides,
            {
                'accept-language': 'en-US,en;q=0.9',
                'sec-ch-ua-platform': '"Windows"',
                'priority': 'u=1, i',
            },
        )
        self.assertEqual(nav._session1.headers['accept-language'], 'en-US,en;q=0.9')
        self.assertEqual(nav._session1.headers['sec-ch-ua-platform'], '"Windows"')
        self.assertEqual(nav._session2.headers['priority'], 'u=1, i')
        self.assertEqual(nav._session1.headers['referer'], self.fetcher._last_scholar_url)

    def test_inject_curl_ignores_disallowed_headers(self):
        curl = (
            "curl 'https://scholar.google.com/scholar?cites=123' "
            "-b 'SID=abc' "
            "-H 'User-Agent: injected-agent' "
            "-H 'Referer: https://example.com/' "
            "-H 'Host: scholar.google.com' "
            "-H 'sec-fetch-site: cross-site'"
        )

        self.fetcher._inject_cookies_from_curl(curl)

        nav = scholarly_mod.scholarly._Scholarly__nav
        self.assertEqual(self.fetcher._injected_header_overrides, {})
        self.assertNotIn('user-agent', nav._session1.headers)
        self.assertNotIn('host', nav._session1.headers)
        self.assertEqual(nav._session1.headers['referer'], self.fetcher._last_scholar_url)

    def test_inject_curl_without_cookie_still_fails(self):
        curl = "curl 'https://scholar.google.com/scholar?cites=123' -H 'Accept-Language: en-US,en;q=0.9'"

        injected = self.fetcher._inject_cookies_from_curl(curl)

        self.assertEqual(injected, 0)
        self.assertEqual(self.fetcher._injected_cookies, {})
        self.assertEqual(self.fetcher._injected_header_overrides, {})

    def test_parse_args_mentions_selected_headers_for_interactive_captcha(self):
        with patch.object(sys, 'argv', ['scholar_citation.py', '--author', 'test', '--help']):
            with self.assertRaises(SystemExit), patch('sys.stdout', new_callable=StringIO) as fake_stdout:
                scholar_citation.parse_args()

        help_text = fake_stdout.getvalue()
        self.assertIn('inject fresh cookies and selected headers', help_text)

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
                old_citations=[],
                fresh_citations=[],
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
                old_citations=[],
                fresh_citations=[],
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

    def test_probe_citation_start_year_no_longer_waits_before_request(self):
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
                return [FakeBar(2025, 1)]

            def find_all(self, *args, **kwargs):
                return []

        nav = scholarly_mod.scholarly._Scholarly__nav
        nav._get_soup = lambda citedby_url: FakeSoup()
        original_probe = scholar_citation.PaperCitationFetcher._probe_citation_start_year

        with patch("scholar_citation.time.sleep") as mock_sleep, \
             patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            start_year = original_probe(
                self.fetcher,
                "/scholar?cites=123",
                num_citations=1,
                pub_year="2025",
            )

        output = fake_stdout.getvalue()
        self.assertEqual(start_year, 2025)
        mock_sleep.assert_not_called()
        self.assertNotIn("Probing citation year range (", output)

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
                old_citations=[],
                fresh_citations=[],
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
                old_citations=[],
                fresh_citations=[],
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
                old_citations=list(cached_citations),
                fresh_citations=[],
                save_progress=lambda complete: save_calls.append(complete),
                num_citations=3,
                pub_year="2020",
                prev_scholar_count=0,
                allow_incremental_early_stop=False,
            )

        output = fake_stdout.getvalue()
        self.assertEqual(requests, [])
        self.assertEqual(citations, [])
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

        with patch.object(scholar_citation, "_SearchScholarIterator", FakeIterator):
            citations = self.fetcher._fetch_by_year(
                citedby_url="/scholar?cites=123",
                old_citations=list(cached_citations),
                fresh_citations=[],
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
                old_citations=[],
                fresh_citations=[],
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

    def test_materialize_citation_cache_overlays_fresh_on_incomplete_save(self):
        old_citations = [
            {"title": "Old-A", "authors": "A", "venue": "V", "year": "2024", "url": "old-a"},
            {"title": "Keep-B", "authors": "B", "venue": "V2", "year": "2023", "url": "old-b"},
        ]
        fresh_citations = [
            {"title": "Old-A", "authors": "A", "venue": "V", "year": "2024", "url": "new-a"},
            {"title": "Fresh-C", "authors": "C", "venue": "V3", "year": "2025", "url": "new-c"},
        ]

        materialized = self.fetcher._materialize_citation_cache(old_citations, fresh_citations, complete=False)

        self.assertEqual([c["title"] for c in materialized], ["Old-A", "Keep-B", "Fresh-C"])
        self.assertEqual(materialized[0]["url"], "new-a")

    def test_materialize_citation_cache_uses_only_fresh_on_complete_save(self):
        old_citations = [
            {"title": "Old-A", "authors": "A", "venue": "V", "year": "2024", "url": "old-a"},
            {"title": "Keep-B", "authors": "B", "venue": "V2", "year": "2023", "url": "old-b"},
        ]
        fresh_citations = [
            {"title": "Old-A", "authors": "A", "venue": "V", "year": "2024", "url": "new-a"},
        ]

        materialized = self.fetcher._materialize_citation_cache(old_citations, fresh_citations, complete=True)

        self.assertEqual(materialized, fresh_citations)

    def test_small_fetch_complete_replaces_old_cache_content(self):
        old_citations = [
            {"title": "Old-A", "authors": "A", "venue": "V", "year": "2024", "url": "old-a"},
            {"title": "Old-B", "authors": "B", "venue": "V2", "year": "2023", "url": "old-b"},
        ]
        fetched_items = [
            {"bib": {"title": "Old-A", "author": ["A"], "venue": "V", "pub_year": "2024"}, "pub_url": "new-a"},
            {"bib": {"title": "Fresh-C", "author": ["C"], "venue": "V3", "pub_year": "2025"}, "pub_url": "new-c"},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = os.path.join(tmpdir, "paper.json")
            with patch.object(scholar_citation.scholarly, "citedby", return_value=iter(fetched_items)):
                citations = self.fetcher._fetch_citations_with_progress(
                    citedby_url="/scholar?cites=123",
                    cache_path=cache_path,
                    title="Paper",
                    num_citations=2,
                    pub_url="https://example.com/paper",
                    pub_year="2024",
                    resume_from=old_citations,
                    completed_years=[],
                    prev_scholar_count=2,
                )

            self.assertEqual([c["title"] for c in citations], ["Old-A", "Fresh-C"])
            self.assertEqual(citations[0]["url"], "new-a")
            with open(cache_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual([c["title"] for c in saved["citations"]], ["Old-A", "Fresh-C"])
            self.assertEqual(saved["citations"][0]["url"], "new-a")

    def test_year_bucket_refresh_replaces_cached_year_slice(self):
        citations = [
            {"title": "Old-2024-A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
            {"title": "Old-2024-B", "authors": "B", "venue": "V", "year": "2024", "url": "u2"},
            {"title": "Keep-2023", "authors": "C", "venue": "V", "year": "2023", "url": "u3"},
            {"title": "Keep-NY", "authors": "D", "venue": "V", "year": "N/A", "url": "u4"},
        ]
        refreshed_year = [
            {"title": "New-2024-A", "authors": "A", "venue": "V2", "year": "2024", "url": "u5"},
            {"title": "New-2024-B", "authors": "B", "venue": "V2", "year": "2024", "url": "u6"},
            {"title": "New-2024-C", "authors": "C", "venue": "V2", "year": "2024", "url": "u7"},
        ]

        merged = self.fetcher._replace_citation_year_bucket(citations, 2024, refreshed_year)

        self.assertEqual(
            [c["title"] for c in merged],
            ["Keep-2023", "Keep-NY", "New-2024-A", "New-2024-B", "New-2024-C"],
        )
        self.assertEqual(self.fetcher._year_count_map(merged), {2023: 1, 2024: 3})

    def test_refresh_reconciliation_accepts_matching_complete_histogram(self):
        status = self.fetcher._refresh_reconciliation_status(
            citations=[
                {"title": "A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
                {"title": "B", "authors": "B", "venue": "V", "year": "2025", "url": "u2"},
                {"title": "C", "authors": "C", "venue": "V", "year": "2025", "url": "u3"},
            ],
            num_citations=3,
            probed_year_counts={2024: 1, 2025: 2},
            probe_complete=True,
        )

        self.assertTrue(status["ok"])
        self.assertEqual(status["reason"], "matched_complete_histogram")

    def test_refresh_reconciliation_requests_escalation_on_histogram_mismatch(self):
        status = self.fetcher._refresh_reconciliation_status(
            citations=[
                {"title": "A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
                {"title": "B", "authors": "B", "venue": "V", "year": "2025", "url": "u2"},
                {"title": "C", "authors": "C", "venue": "V", "year": "2025", "url": "u3"},
            ],
            num_citations=3,
            probed_year_counts={2024: 2, 2025: 1},
            probe_complete=True,
        )

        self.assertFalse(status["ok"])
        self.assertEqual(status["reason"], "year_count_mismatch")

    def test_refresh_reconciliation_uses_total_only_when_histogram_incomplete(self):
        status = self.fetcher._refresh_reconciliation_status(
            citations=[
                {"title": "A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
                {"title": "B", "authors": "B", "venue": "V", "year": "N/A", "url": "u2"},
                {"title": "C", "authors": "C", "venue": "V", "year": "N/A", "url": "u3"},
            ],
            num_citations=3,
            probed_year_counts={2024: 1},
            probe_complete=False,
        )

        self.assertTrue(status["ok"])
        self.assertEqual(status["reason"], "matched_total_with_incomplete_histogram")

    def test_rehydrate_probe_metadata_marks_complete_only_when_safe(self):
        counts, probe_complete = self.fetcher._rehydrate_probe_metadata(
            {
                "probed_year_counts": {"2024": 1, "2025": 2},
                "probe_complete": True,
            },
            current_scholar_total=3,
        )

        self.assertEqual(counts, {2024: 1, 2025: 2})
        self.assertTrue(probe_complete)

    def test_rehydrate_probe_metadata_downgrades_legacy_or_stale_complete_flags(self):
        legacy_counts, legacy_complete = self.fetcher._rehydrate_probe_metadata(
            {
                "probed_year_counts": {"2024": 1, "2025": 2},
            },
            current_scholar_total=3,
        )
        stale_counts, stale_complete = self.fetcher._rehydrate_probe_metadata(
            {
                "probed_year_counts": {"2024": 1, "2025": 2},
                "probe_complete": True,
            },
            current_scholar_total=4,
        )

        self.assertEqual(legacy_counts, {2024: 1, 2025: 2})
        self.assertFalse(legacy_complete)
        self.assertEqual(stale_counts, {2024: 1, 2025: 2})
        self.assertFalse(stale_complete)

    def test_fetch_citations_with_progress_rehydrates_probe_metadata_for_year_resume(self):
        cached_citations = [
            {"title": "Cached-2024", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
            {"title": "Cached-2025", "authors": "B", "venue": "V", "year": "2025", "url": "u2"},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = os.path.join(tmpdir, "paper.json")

            def fake_fetch_by_year(citedby_url, old_citations, fresh_citations, save_progress,
                                   num_citations, pub_year, prev_scholar_count=0,
                                   allow_incremental_early_stop=True,
                                   force_year_rebuild=False,
                                   selective_refresh_years=None):
                self.assertEqual(self.fetcher._probed_year_counts, {2024: 1, 2025: 1})
                self.assertTrue(self.fetcher._probed_year_count_complete)
                self.assertEqual(self.fetcher._cached_year_counts, {2024: 1, 2025: 1})
                save_progress(complete=False)
                return list(old_citations)

            with patch.object(self.fetcher, "_fetch_by_year", side_effect=fake_fetch_by_year):
                citations = self.fetcher._fetch_citations_with_progress(
                    citedby_url="/scholar?cites=123",
                    cache_path=cache_path,
                    title="Paper",
                    num_citations=2,
                    pub_url="https://example.com/paper",
                    pub_year="2024",
                    resume_from=cached_citations,
                    completed_years=[2024],
                    prev_scholar_count=2,
                    rehydrated_probed_year_counts={2024: 1, 2025: 1},
                    rehydrated_probe_complete=True,
                )

            self.assertEqual(citations, [])
            with open(cache_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["probed_year_counts"], {"2024": 1, "2025": 1})
            self.assertTrue(saved["probe_complete"])
            self.assertEqual(saved["completed_years"], [2024])

    def test_run_main_loop_retry_rehydrates_probe_metadata_from_latest_cache(self):
        pub = {
            "no": 1,
            "title": "Big Paper",
            "num_citations": 80,
            "year": "2024",
            "venue": "V",
        }
        cached = {
            "citations": [{"title": "Cached-1", "authors": "A", "venue": "V", "year": "2024", "url": "u1"}],
            "num_citations_on_scholar": 80,
            "completed_years": [2024],
            "dedup_count": 0,
            "complete": False,
            "probed_year_counts": {"2024": 1},
        }
        latest_cache = {
            "citations": [{"title": "Cached-2", "authors": "A", "venue": "V", "year": "2024", "url": "u2"}],
            "completed_years": [2024, 2025],
            "dedup_count": 3,
            "probe_complete": True,
            "probed_year_counts": {"2024": 1, "2025": 79},
        }
        fetch_calls = []

        def cache_status(current_pub):
            self.assertEqual(current_pub["title"], pub["title"])
            return "partial", cached

        def fake_fetch(*args, **kwargs):
            resume_from = list(args[6])
            fetch_calls.append(
                {
                    "resume_from": resume_from,
                    "completed_years": list(kwargs["completed_years"]),
                    "saved_dedup_count": kwargs["saved_dedup_count"],
                    "rehydrated_probed_year_counts": kwargs["rehydrated_probed_year_counts"],
                    "rehydrated_probe_complete": kwargs["rehydrated_probe_complete"],
                }
            )
            if len(fetch_calls) == 1:
                raise RuntimeError("temporary failure")
            return resume_from

        with patch.object(self.fetcher, "_fetch_citations_with_progress", side_effect=fake_fetch), \
             patch.object(self.fetcher, "_refresh_reconciliation_status", return_value={"ok": True, "reason": "matched_complete_histogram", "cached_total": 1, "scholar_total": 80}), \
             patch.object(self.fetcher, "_wait_proxy_switch", return_value=None), \
             patch.object(self.fetcher, "_load_citation_cache", return_value=latest_cache), \
             patch("scholar_citation.rand_delay", return_value=0), \
             patch("scholar_citation.time.sleep", return_value=None), \
             patch("sys.stdout", new_callable=StringIO):
            self.fetcher._run_main_loop(
                publications=[pub],
                cache_status=cache_status,
                url_map={pub["title"]: {"citedby_url": "/scholar?cites=123", "pub_url": "https://example.com/paper"}},
                need_fetch=[(pub, "partial", cached)],
                results=[],
                fetch_idx=0,
            )

        self.assertEqual(len(fetch_calls), 2)
        self.assertEqual(fetch_calls[0]["rehydrated_probed_year_counts"], {2024: 1})
        self.assertFalse(fetch_calls[0]["rehydrated_probe_complete"])
        self.assertEqual(fetch_calls[1]["resume_from"], latest_cache["citations"])
        self.assertEqual(fetch_calls[1]["completed_years"], [2024, 2025])
        self.assertEqual(fetch_calls[1]["saved_dedup_count"], 3)
        self.assertEqual(fetch_calls[1]["rehydrated_probed_year_counts"], {2024: 1, 2025: 79})
        self.assertTrue(fetch_calls[1]["rehydrated_probe_complete"])

    def test_run_main_loop_escalates_year_refresh_after_failed_reconciliation(self):
        pub = {
            "no": 1,
            "title": "Big Paper",
            "num_citations": 80,
            "year": "2024",
            "venue": "V",
        }
        cached = {
            "citations": [{"title": "Cached-1", "authors": "A", "venue": "V", "year": "2024", "url": "u1"}],
            "num_citations_on_scholar": 75,
            "completed_years": [2024],
            "dedup_count": 0,
            "complete": False,
        }
        stale_retry_cache = {
            "citations": [{"title": "Stale-2024", "authors": "A", "venue": "V", "year": "2024", "url": "u-stale"}],
            "completed_years": [2024, 2025],
            "dedup_count": 7,
            "probe_complete": True,
            "probed_year_counts": {"2024": 10, "2025": 70},
        }
        publications = [pub]
        results = []
        fetch_calls = []
        first_result = [
            {"title": "Fetched-2026-A", "authors": "A", "venue": "V", "year": "2026", "url": "u2"},
        ]
        second_result = [
            {"title": "Fetched-2026-A", "authors": "A", "venue": "V", "year": "2026", "url": "u2"},
            {"title": "Fetched-2025-A", "authors": "B", "venue": "V", "year": "2025", "url": "u3"},
        ]

        def cache_status(current_pub):
            self.assertEqual(current_pub["title"], pub["title"])
            return "partial", cached

        def fake_fetch(*args, **kwargs):
            fetch_calls.append(
                {
                    "resume_from": list(args[6]),
                    "completed_years": list(kwargs["completed_years"]),
                    "allow_incremental_early_stop": kwargs["allow_incremental_early_stop"],
                    "force_year_rebuild": kwargs["force_year_rebuild"],
                    "selective_refresh_years": kwargs["selective_refresh_years"],
                    "prev_scholar_count": kwargs["prev_scholar_count"],
                    "saved_dedup_count": kwargs["saved_dedup_count"],
                    "rehydrated_probed_year_counts": kwargs["rehydrated_probed_year_counts"],
                    "rehydrated_probe_complete": kwargs["rehydrated_probe_complete"],
                }
            )
            return first_result if len(fetch_calls) == 1 else second_result

        with patch.object(self.fetcher, "_fetch_citations_with_progress", side_effect=fake_fetch), \
             patch.object(
                 self.fetcher,
                 "_refresh_reconciliation_status",
                 side_effect=[
                     {"ok": False, "reason": "year_count_mismatch", "cached_total": 1, "scholar_total": 80},
                     {"ok": True, "reason": "matched_complete_histogram", "cached_total": 2, "scholar_total": 80},
                     {"ok": True, "reason": "matched_complete_histogram", "cached_total": 2, "scholar_total": 80},
                 ],
             ), \
             patch.object(self.fetcher, "_wait_proxy_switch", return_value=None), \
             patch.object(self.fetcher, "_load_citation_cache", return_value=stale_retry_cache) as mock_load_cache, \
             patch("scholar_citation.rand_delay", return_value=0), \
             patch("scholar_citation.time.sleep", return_value=None), \
             patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            self.fetcher._run_main_loop(
                publications=publications,
                cache_status=cache_status,
                url_map={pub["title"]: {"citedby_url": "/scholar?cites=123", "pub_url": "https://example.com/paper"}},
                need_fetch=[(pub, "partial", cached)],
                results=results,
                fetch_idx=0,
            )

        log_output = fake_stdout.getvalue()
        self.assertEqual(len(fetch_calls), 2)
        self.assertTrue(fetch_calls[0]["allow_incremental_early_stop"])
        self.assertFalse(fetch_calls[0]["force_year_rebuild"])
        self.assertIsNone(fetch_calls[0]["selective_refresh_years"])
        self.assertEqual(fetch_calls[0]["prev_scholar_count"], 75)
        self.assertEqual(fetch_calls[0]["resume_from"], cached["citations"])
        self.assertEqual(fetch_calls[0]["completed_years"], [])

        self.assertFalse(fetch_calls[1]["allow_incremental_early_stop"])
        self.assertTrue(fetch_calls[1]["force_year_rebuild"])
        self.assertIsNone(fetch_calls[1]["selective_refresh_years"])
        self.assertEqual(fetch_calls[1]["prev_scholar_count"], 75)
        self.assertEqual(fetch_calls[1]["resume_from"], first_result)
        self.assertEqual(fetch_calls[1]["completed_years"], [])
        self.assertEqual(fetch_calls[1]["saved_dedup_count"], 0)
        self.assertIsNone(fetch_calls[1]["rehydrated_probed_year_counts"])
        self.assertFalse(fetch_calls[1]["rehydrated_probe_complete"])

        mock_load_cache.assert_not_called()
        self.assertIn("Escalating to full revalidation", log_output)
        self.assertIn("Retrying escalated full revalidation with in-memory state", log_output)
        self.assertNotIn("Retrying with 1 cached citations from previous attempt", log_output)

        self.assertEqual(results[0]["citations"], second_result)
        self.assertEqual(self.fetcher._papers_fetched_count, 1)


class AuthorProfileCountSummaryTests(unittest.TestCase):
    def test_build_profile_count_summary_reports_gap(self):
        fetcher = scholar_citation.AuthorProfileFetcher("author", output_dir=".")
        basics = {
            "citedby": 8035,
            "cites_per_year": {"2015": 100, "2016": 200, "2026": 7658},
        }

        summary = fetcher._build_profile_count_summary(basics)

        self.assertEqual(summary["scholar_total_citations"], 8035)
        self.assertEqual(summary["year_table_total_citations"], 7958)
        self.assertEqual(summary["year_table_gap"], 77)
        self.assertFalse(summary["year_table_matches_total"])
        self.assertIn("may exclude citations without usable year metadata", summary["year_table_note"])

    def test_save_profile_json_includes_count_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fetcher = scholar_citation.AuthorProfileFetcher("author", output_dir=tmpdir)
            basics = {
                "name": "Test Author",
                "citedby": 8035,
                "cites_per_year": {"2015": 100, "2016": 200, "2026": 7658},
            }
            publications = []

            profile = fetcher.save_profile_json(basics, publications, change_history=[])

            self.assertEqual(profile["total_citations"], 8035)
            self.assertEqual(profile["citation_count_summary"]["year_table_total_citations"], 7958)
            self.assertEqual(profile["citation_count_summary"]["year_table_gap"], 77)

            with open(fetcher.profile_json, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["citation_count_summary"], profile["citation_count_summary"])

    def test_save_profile_xlsx_labels_year_gap_explicitly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fetcher = scholar_citation.AuthorProfileFetcher("author", output_dir=tmpdir)
            basics = {
                "name": "Test Author",
                "affiliation": "Org",
                "interests": ["NLP"],
                "scholar_id": "author",
                "citedby": 8035,
                "citedby_this_year": 123,
                "citedby5y": 1000,
                "hindex": 10,
                "hindex5y": 9,
                "i10index": 20,
                "i10index5y": 18,
                "cites_per_year": {"2015": 100, "2016": 200, "2026": 7658},
            }

            with patch.object(scholar_citation, "datetime") as fake_datetime:
                fake_datetime.now.return_value = types.SimpleNamespace(
                    year=2026,
                    strftime=lambda fmt: "2026-04-02 12:00:00",
                )
                fetcher.save_profile_xlsx(basics, publications=[], change_history=[])

            workbook = fetcher._last_profile_workbook
            ws = workbook.active
            values = [cell.value for cell in ws.cells.values()]
            self.assertIn("Total Citations (Scholar profile)", values)
            self.assertIn("Year-table subtotal (cites_per_year)", values)
            self.assertIn("Year-table gap vs total", values)
            self.assertIn("Citations Per Year (Scholar cites_per_year)", values)
            self.assertTrue(any(
                isinstance(value, str) and "may exclude citations without usable year metadata" in value
                for value in values
            ))
