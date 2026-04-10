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

    def test_extract_citation_info_keeps_cites_id_and_fallback_year(self):
        info = self.fetcher._extract_citation_info(
            {
                "bib": {"title": "Paper", "author": ["A", "B"], "venue": "Venue", "pub_year": "N/A"},
                "pub_url": "https://example.com/cite",
                "cites_id": ["123", "456"],
            },
            fallback_year=2025,
        )

        self.assertEqual(info["title"], "Paper")
        self.assertEqual(info["authors"], "A, B")
        self.assertEqual(info["venue"], "Venue")
        self.assertEqual(info["year"], "2025")
        self.assertEqual(info["url"], "https://example.com/cite")
        self.assertEqual(info["cites_id"], "123,456")

    def test_citation_identity_prefers_cites_id_with_metadata_fallback(self):
        citation = {
            "title": "Paper",
            "authors": "A",
            "venue": "Venue",
            "year": "2025",
            "url": "u",
            "cites_id": "cid-1",
        }

        keys = self.fetcher._citation_identity_keys(citation)

        self.assertEqual(keys[0], "cites_id\tcid-1")
        self.assertIn("meta\tpaper\tvenue", keys)
        self.assertEqual(self.fetcher._citation_identity_key(citation), "cites_id\tcid-1")

    def test_overlay_citations_matches_legacy_cache_to_new_cites_id(self):
        old_citations = [
            {"title": "Paper", "authors": "A", "venue": "Venue", "year": "2025", "url": "old-u"},
        ]
        refreshed_citations = [
            {"title": "Paper", "authors": "A", "venue": "Venue", "year": "2025", "url": "new-u", "cites_id": "cid-1"},
        ]

        merged = self.fetcher._overlay_citations_by_identity(old_citations, refreshed_citations)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["url"], "new-u")
        self.assertEqual(merged[0]["cites_id"], "cid-1")

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

    def test_fetch_by_year_fetches_all_selected_years_without_early_stop(self):
        self.fetcher._probe_citation_start_year = lambda citedby_url, num_citations=None, pub_year=None: 2025
        pages = {
            (2026, 0): [
                {"bib": {"title": "Page1-A", "author": ["A"], "venue": "V1", "pub_year": "2026"}, "pub_url": "u1"},
                {"bib": {"title": "Page1-B", "author": ["B"], "venue": "V2", "pub_year": "2026"}, "pub_url": "u2"},
                {"bib": {"title": "Page1-C", "author": ["C"], "venue": "V3", "pub_year": "2026"}, "pub_url": "u3"},
            ],
            (2026, 3): [
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

        with patch.object(scholar_citation, "_SearchScholarIterator", FakeIterator), \
             patch.object(scholar_citation, "datetime") as fake_datetime:
            fake_datetime.now.return_value = types.SimpleNamespace(year=2026)
            citations = self.fetcher._fetch_by_year(
                citedby_url="/scholar?cites=123",
                old_citations=[],
                fresh_citations=[],
                save_progress=lambda complete: save_calls.append(complete),
                num_citations=2,
                pub_year="2025",
                prev_scholar_count=1,
            )

        self.assertEqual([c["title"] for c in citations], ["OldYear-A", "Page1-A", "Page1-B", "Page1-C", "Page2-A"])
        self.assertEqual(requests, [(2025, 0), (2025, 1), (2026, 0), (2026, 3), (2026, 4)])
        self.assertEqual(self.fetcher._new_citations_count, 5)
        self.assertEqual(save_calls, [False, False, False, True])
    def test_early_stop_status_suppresses_only_target_reached_when_requested(self):
        self.assertTrue(
            scholar_citation.PaperCitationFetcher._get_early_stop_status(
                3, 3, 0, 0, allow_incremental_early_stop=False,
            )["should_stop"]
        )
        self.assertFalse(
            scholar_citation.PaperCitationFetcher._get_early_stop_status(
                3, 3, 0, 0, allow_incremental_early_stop=False,
                suppress_target_reached=True,
            )["should_stop"]
        )
        partial = scholar_citation.PaperCitationFetcher._get_early_stop_status(
            1, 3, 0, 0,
            allow_incremental_early_stop=False,
            suppress_target_reached=True,
            stop_after_partial_resume=True,
        )
        self.assertTrue(partial["should_stop"])
        self.assertEqual(partial["reason"], "partial_resume_completed")
        disabled_target = scholar_citation.PaperCitationFetcher._get_early_stop_status(
            3, 3, 0, 0,
            allow_incremental_early_stop=False,
            disable_target_reached=True,
        )
        self.assertFalse(disabled_target["should_stop"])
        recovered = scholar_citation.PaperCitationFetcher._get_early_stop_status(
            1, 3, 2, 1,
            allow_incremental_early_stop=True,
            suppress_target_reached=True,
        )
        self.assertTrue(recovered["should_stop"])
        self.assertEqual(recovered["reason"], "scholar_increase_recovered")

    def test_histogram_authoritative_mode_does_not_stop_on_cached_total_match(self):
        self.fetcher._probed_year_counts = {2024: 1, 2025: 1, 2026: 1}
        self.fetcher._probed_year_count_complete = True
        self.fetcher._cached_year_counts = {2024: 0, 2025: 0, 2026: 0}
        self.fetcher._partial_year_start = {2024: 1}
        self.fetcher._probe_citation_start_year = lambda citedby_url, num_citations=None, pub_year=None: 2024

        old_citations = [
            {"title": f"Old-Unyeared-{i}", "authors": "A", "venue": "V", "year": "N/A", "url": f"u-old-{i}"}
            for i in range(3)
        ]
        requests = []

        class FakeIterator:
            def __init__(self, nav, url):
                year = int(url.split("as_ylo=")[1].split("&")[0])
                requests.append(year)
                self.items = [{
                    "bib": {"title": f"Fetched-{year}", "author": ["A"], "venue": f"V{year}", "pub_year": str(year)},
                    "pub_url": f"u{year}",
                }]
                self.index = 0
                self._finished_current_page = False

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
            fake_datetime.now.return_value = types.SimpleNamespace(year=2026)
            citations = self.fetcher._fetch_by_year(
                citedby_url="/scholar?cites=123",
                old_citations=old_citations,
                fresh_citations=[],
                save_progress=lambda complete: None,
                num_citations=3,
                pub_year="2024",
                prev_scholar_count=3,
                allow_incremental_early_stop=False,
            )

        output = fake_stdout.getvalue()
        self.assertEqual(requests, [2024, 2025, 2026])
        self.assertEqual(self.fetcher._year_count_map(citations), {2024: 1, 2025: 1, 2026: 1})
        self.assertNotIn("Reached target (3 >= 3)", output)

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
        self.fetcher._new_citations_count = 0

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
                pub_year="2025",
                prev_scholar_count=10,
                allow_incremental_early_stop=False,
                selective_refresh_years={2025, 2026},
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

    def test_fetch_by_year_uses_histogram_total_as_target_and_backfills_year(self):
        self.fetcher._probed_year_counts = {2025: 1, 2026: 1}
        self.fetcher._probed_year_count_complete = False
        self.fetcher._probe_citation_start_year = lambda citedby_url, num_citations=None, pub_year=None: 2025

        requests = []

        class FakeIterator:
            def __init__(self, nav, url):
                start = 0
                if "start=" in url:
                    start = int(url.split("start=")[1].split("&")[0])
                year = int(url.split("as_ylo=")[1].split("&")[0])
                requests.append((year, start))
                if year == 2025:
                    self.items = [{
                        "bib": {"title": "Y2025", "author": ["A"], "venue": "V2025", "pub_year": "?"},
                        "pub_url": "u2025",
                    }]
                elif year == 2026:
                    self.items = [{
                        "bib": {"title": "Y2026", "author": ["B"], "venue": "V2026"},
                        "pub_url": "u2026",
                    }]
                else:
                    self.items = []
                self.index = 0
                self._finished_current_page = False

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
            fake_datetime.now.return_value = types.SimpleNamespace(year=2026)
            citations = self.fetcher._fetch_by_year(
                citedby_url="/scholar?cites=123",
                old_citations=[],
                fresh_citations=[],
                save_progress=lambda complete: None,
                num_citations=5,
                pub_year="2024",
                prev_scholar_count=0,
                allow_incremental_early_stop=False,
            )

        output = fake_stdout.getvalue()
        self.assertEqual(requests, [(2025, 0), (2026, 0)])
        self.assertEqual([c["year"] for c in citations], ["2025", "2026"])
        self.assertIn("target=2", output)
        self.assertIn("Reached target (2 >= 2)", output)

    def test_fetch_policy_allows_direct_mode_for_old_high_total_paper(self):
        with patch.object(scholar_citation, "datetime") as fake_datetime:
            fake_datetime.now.return_value = types.SimpleNamespace(year=2026)
            policy = self.fetcher._resolve_citation_fetch_policy(60, "2023")

        self.assertEqual(policy["mode"], "direct")
        self.assertEqual(policy["pub_year"], 2023)
        self.assertEqual(policy["covered_years"], 4)
        self.assertEqual(policy["avg_citations_per_year"], 15)
        self.assertEqual(policy["reason"], "low_average_per_year")

    def test_fetch_policy_keeps_year_mode_for_recent_high_total_paper(self):
        with patch.object(scholar_citation, "datetime") as fake_datetime:
            fake_datetime.now.return_value = types.SimpleNamespace(year=2026)
            policy = self.fetcher._resolve_citation_fetch_policy(60, "2026")

        self.assertEqual(policy["mode"], "year")
        self.assertEqual(policy["pub_year"], 2026)
        self.assertEqual(policy["covered_years"], 1)
        self.assertEqual(policy["avg_citations_per_year"], 60)
        self.assertEqual(policy["reason"], "high_average_per_year")

    def test_resolve_refresh_strategy_recheck_uses_direct_policy_without_year_rebuild(self):
        self.fetcher.recheck_citations = True
        pub = {"title": "Paper", "num_citations": 60, "year": "2023"}
        cached = {
            "citations": [{"title": "Cached", "authors": "A", "venue": "V", "year": "2023", "url": "u1"}],
            "num_citations_on_scholar": 60,
            "dedup_count": 0,
            "completed_years_in_current_run": [2023],
        }

        with patch.object(scholar_citation, "datetime") as fake_datetime:
            fake_datetime.now.return_value = types.SimpleNamespace(year=2026)
            strategy = self.fetcher._resolve_refresh_strategy(pub, cached, "partial")

        self.assertEqual(strategy["mode"], "recheck")
        self.assertEqual(strategy["fetch_policy"]["mode"], "direct")
        self.assertFalse(strategy["force_year_rebuild"])
        self.assertFalse(strategy["allow_incremental_early_stop"])
        self.assertEqual(strategy["completed_years_in_current_run"], [])


    def test_fetch_citations_with_progress_uses_direct_policy_for_old_high_total_paper(self):
        self.fetcher.save_every = 100
        citedby_items = iter([
            {"bib": {"title": "Y2024", "author": ["A"], "venue": "V1", "pub_year": "2024"}, "pub_url": "u1"},
            {"bib": {"title": "Y2025", "author": ["B"], "venue": "V2", "pub_year": "2025"}, "pub_url": "u2"},
        ])
        fetch_policy = {
            "mode": "direct",
            "covered_years": 4,
            "avg_citations_per_year": 15,
            "pub_year": 2023,
            "reason": "low_average_per_year",
        }

        with patch.object(scholar_citation.scholarly, "citedby", return_value=citedby_items), \
             patch.object(self.fetcher, "_fetch_by_year") as mock_fetch_by_year, \
             patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            citations = self.fetcher._fetch_citations_with_progress(
                citedby_url="/scholar?cites=123",
                cache_path=os.path.join(tempfile.gettempdir(), "paper-direct-policy.json"),
                title="Paper",
                num_citations=60,
                pub_url="https://example.com/paper",
                pub_year="2023",
                resume_from=[],
                fetch_policy=fetch_policy,
            )

        output = fake_stdout.getvalue()
        self.assertEqual(len(citations), 2)
        mock_fetch_by_year.assert_not_called()
        self.assertIn("Direct fetch mode: no year probe, summary shown after fetch", output)

    def test_small_fetch_logs_year_comparison_without_revalidation(self):
        self.fetcher.save_every = 100

        citedby_items = iter([
            {"bib": {"title": "Y2024", "author": ["A"], "venue": "V1", "pub_year": "2024"}, "pub_url": "u1"},
            {"bib": {"title": "Y2025", "author": ["B"], "venue": "V2", "pub_year": "2025"}, "pub_url": "u2"},
            {"bib": {"title": "NoYear", "author": ["C"], "venue": "V3"}, "pub_url": "u3"},
        ])

        with patch.object(scholar_citation.scholarly, "citedby", return_value=citedby_items), \
             patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            citations = self.fetcher._fetch_citations_with_progress(
                citedby_url="/scholar?cites=123",
                cache_path=os.path.join(tempfile.gettempdir(), "paper-small.json"),
                title="Small Paper",
                num_citations=3,
                pub_url="https://example.com/paper",
                pub_year="2024",
                resume_from=[],
            )

        output = fake_stdout.getvalue()
        self.assertEqual(len(citations), 3)
        self.assertIn("Direct fetch mode: no year probe, summary shown after fetch", output)
        self.assertIn("Probe summary: none", output)
        self.assertIn("Probe totals: scholar_total=3, year_sum=0, missing_from_histogram=?", output)
        self.assertIn("Cache totals: cached_total=3, cached_year_sum=2, cached_unyeared=1, dedup_num=0", output)

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

    def test_fetch_by_year_uses_incomplete_probe_deficit_years_for_selective_refresh(self):
        self.fetcher._probed_year_counts = {2024: 1, 2025: 3, 2026: 2}
        self.fetcher._probed_year_count_complete = False
        self.fetcher._cached_year_counts = {2024: 1, 2025: 1, 2026: 2}
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
            fake_datetime.now.return_value = types.SimpleNamespace(year=2026)
            citations = self.fetcher._fetch_by_year(
                citedby_url="/scholar?cites=123",
                old_citations=[],
                fresh_citations=[],
                save_progress=lambda complete: None,
                num_citations=10,
                pub_year="2020",
                prev_scholar_count=0,
            )

        output = fake_stdout.getvalue()
        self.assertEqual(requests, [(2025, 0)])
        self.assertEqual([c["title"] for c in citations], ["Y2025"])
        self.assertIn("Selective refresh years: 2025", output)
        self.assertIn("Year 2024: skip (not selected for refresh)", output)
        self.assertIn("Year 2026: skip (not selected for refresh)", output)

    def test_incomplete_probe_missing_year_is_not_treated_as_zero_for_selective_refresh(self):
        self.fetcher._probed_year_counts = {2025: 2}
        self.fetcher._probed_year_count_complete = False
        self.fetcher._cached_year_counts = {2024: 5, 2025: 1}
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
            )

        output = fake_stdout.getvalue()
        self.assertEqual(requests, [(2025, 0)])
        self.assertEqual([c["title"] for c in citations], ["Y2025"])
        self.assertIn("Selective refresh years: 2025", output)
        self.assertIn("Year 2024: skip (not selected for refresh)", output)

    def test_incomplete_probe_with_no_deficit_years_falls_back_to_full_year_traversal(self):
        self.fetcher._probed_year_counts = {2024: 1, 2025: 2}
        self.fetcher._probed_year_count_complete = False
        self.fetcher._cached_year_counts = {2024: 1, 2025: 2}
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
            )

        output = fake_stdout.getvalue()
        self.assertEqual(requests, [(2024, 0), (2025, 0)])
        self.assertEqual([c["title"] for c in citations], ["Y2024", "Y2025"])
        self.assertIn("Selective refresh years: none", output)
        self.assertNotIn("skip (not selected for refresh)", output)

    def test_incomplete_probe_partial_resume_still_fetches_resumed_year(self):
        self.fetcher._probed_year_counts = {2024: 1, 2025: 2}
        self.fetcher._probed_year_count_complete = False
        self.fetcher._cached_year_counts = {2024: 1, 2025: 2}
        self.fetcher._partial_year_start = {2024: 2}
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
            )

        output = fake_stdout.getvalue()
        self.assertEqual(requests, [(2024, 2)])
        self.assertEqual([c["title"] for c in citations], ["Y2024"])
        self.assertIn("Selective refresh years: 2024", output)
        self.assertIn("Year 2025: skip (not selected for refresh)", output)

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
        self.assertIn("years_with_citations=1", output)
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
        self.assertEqual([c["title"] for c in citations], ["Y2025", "Y2024", "NoYear"])
        self.assertEqual(save_calls, [False, True])
        self.assertIn("    Probe totals: scholar_total=3, year_sum=2, missing_from_histogram=1", output)
        self.assertIn("    Cache totals: cached_total=3, cached_year_sum=2, cached_unyeared=1, dedup_num=0", output)
        self.assertIn("Year fetch skipped: histogram-authoritative match", output)
        self.assertIn("scholar_total=3, year_sum=2, cached_total=3, cached_year_sum=2, dedup_num=0", output)

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
            {"bib": {"title": "Old-A", "author": ["A"], "venue": "V", "pub_year": "2024"}, "pub_url": "new-a", "cites_id": "cid-old-a"},
            {"bib": {"title": "Fresh-C", "author": ["C"], "venue": "V3", "pub_year": "2025"}, "pub_url": "new-c", "cites_id": "cid-fresh-c"},
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
                    completed_years_in_current_run=[],
                    prev_scholar_count=2,
                )

            self.assertEqual([c["title"] for c in citations], ["Old-A", "Fresh-C"])
            self.assertEqual(citations[0]["url"], "new-a")
            with open(cache_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual([c["title"] for c in saved["citations"]], ["Old-A", "Fresh-C"])
            self.assertEqual(saved["citations"][0]["url"], "new-a")
            self.assertEqual(saved["citations"][0]["cites_id"], "cid-old-a")
            self.assertEqual(saved["citations"][1]["cites_id"], "cid-fresh-c")

    def test_direct_fetch_early_stops_after_reaching_target_total(self):
        fetched_items = [
            {"bib": {"title": "Cached-A", "author": ["A"], "venue": "V", "pub_year": "2024"}, "pub_url": "new-a", "cites_id": "cid-a"},
            {"bib": {"title": "Fresh-B", "author": ["B"], "venue": "V2", "pub_year": "2024"}, "pub_url": "new-b", "cites_id": "cid-b"},
            {"bib": {"title": "Fresh-C", "author": ["C"], "venue": "V3", "pub_year": "2025"}, "pub_url": "new-c", "cites_id": "cid-c"},
        ]
        old_citations = [
            {"title": "Cached-A", "authors": "A", "venue": "V", "year": "2024", "url": "old-a", "cites_id": "cid-a"},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = os.path.join(tmpdir, "paper.json")
            with patch.object(scholar_citation.scholarly, "citedby", return_value=iter(fetched_items)), \
                 patch("sys.stdout", new_callable=StringIO) as fake_stdout:
                citations = self.fetcher._fetch_citations_with_progress(
                    citedby_url="/scholar?cites=123",
                    cache_path=cache_path,
                    title="Paper",
                    num_citations=2,
                    pub_url="https://example.com/paper",
                    pub_year="2024",
                    resume_from=old_citations,
                    completed_years_in_current_run=[],
                    prev_scholar_count=1,
                )

        output = fake_stdout.getvalue()
        self.assertEqual([c["title"] for c in citations], ["Cached-A", "Fresh-B"])
        self.assertIn("Direct fetch: reached target (2 >= 2), stopping early", output)
        self.assertNotIn("Fresh-C", [c["title"] for c in citations])

    def test_direct_fetch_first_fetch_does_not_stop_on_scholar_increase(self):
        fetched_items = [
            {"bib": {"title": "Fresh-A", "author": ["A"], "venue": "V", "pub_year": "2024"}, "pub_url": "new-a", "cites_id": "cid-a"},
            {"bib": {"title": "Fresh-B", "author": ["B"], "venue": "V2", "pub_year": "2025"}, "pub_url": "new-b", "cites_id": "cid-b"},
            {"bib": {"title": "Fresh-C", "author": ["C"], "venue": "V3", "pub_year": "2026"}, "pub_url": "new-c", "cites_id": "cid-c"},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = os.path.join(tmpdir, "paper.json")
            with patch.object(scholar_citation.scholarly, "citedby", return_value=iter(fetched_items)), \
                 patch("sys.stdout", new_callable=StringIO) as fake_stdout:
                citations = self.fetcher._fetch_citations_with_progress(
                    citedby_url="/scholar?cites=123",
                    cache_path=cache_path,
                    title="Paper",
                    num_citations=44,
                    pub_url="https://example.com/paper",
                    pub_year="2024",
                    resume_from=[],
                    completed_years_in_current_run=[],
                    prev_scholar_count=0,
                )

        output = fake_stdout.getvalue()
        self.assertEqual([c["title"] for c in citations], ["Fresh-A", "Fresh-B", "Fresh-C"])
        self.assertNotIn("recovered Scholar increase", output)

    def test_direct_fetch_recheck_does_not_early_stop(self):
        fetched_items = [
            {"bib": {"title": "Fresh-A", "author": ["A"], "venue": "V", "pub_year": "2024"}, "pub_url": "new-a", "cites_id": "cid-a"},
            {"bib": {"title": "Fresh-B", "author": ["B"], "venue": "V2", "pub_year": "2025"}, "pub_url": "new-b", "cites_id": "cid-b"},
            {"bib": {"title": "Fresh-C", "author": ["C"], "venue": "V3", "pub_year": "2026"}, "pub_url": "new-c", "cites_id": "cid-c"},
        ]
        old_recheck = self.fetcher.recheck_citations
        self.fetcher.recheck_citations = True
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                cache_path = os.path.join(tmpdir, "paper.json")
                with patch.object(scholar_citation.scholarly, "citedby", return_value=iter(fetched_items)), \
                     patch("sys.stdout", new_callable=StringIO) as fake_stdout:
                    citations = self.fetcher._fetch_citations_with_progress(
                        citedby_url="/scholar?cites=123",
                        cache_path=cache_path,
                        title="Paper",
                        num_citations=2,
                        pub_url="https://example.com/paper",
                        pub_year="2024",
                        resume_from=[],
                        completed_years_in_current_run=[],
                        prev_scholar_count=1,
                        force_year_rebuild=True,
                    )
        finally:
            self.fetcher.recheck_citations = old_recheck

        output = fake_stdout.getvalue()
        self.assertEqual([c["title"] for c in citations], ["Fresh-A", "Fresh-B", "Fresh-C"])
        self.assertNotIn("stopping early", output)

        fetched_items = [
            {"bib": {"title": "Old-A", "author": ["A"], "venue": "V", "pub_year": "2024"}, "pub_url": "new-a", "cites_id": "cid-old-a"},
            {"bib": {"title": "Fresh-B", "author": ["B"], "venue": "V2", "pub_year": "2024"}, "pub_url": "new-b", "cites_id": "cid-fresh-b"},
            {"bib": {"title": "Fresh-C", "author": ["C"], "venue": "V3", "pub_year": "2025"}, "pub_url": "new-c", "cites_id": "cid-fresh-c"},
        ]
        pub = {"title": "Paper", "num_citations": 2}

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
                    resume_from=[],
                    completed_years_in_current_run=[],
                    prev_scholar_count=2,
                    pub_obj=pub,
                    force_year_rebuild=True,
                )

            self.assertEqual(len(citations), 3)
            self.assertEqual(pub["num_citations"], 3)
            with open(cache_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["num_citations_on_scholar"], 3)
            self.assertEqual(saved["citation_count_summary"]["scholar_total"], 3)

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

    def test_materialize_year_fetch_citations_preserves_untouched_years(self):
        old_citations = [
            {"title": "Old-2024-A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
            {"title": "Keep-2023", "authors": "B", "venue": "V", "year": "2023", "url": "u2"},
            {"title": "Keep-NY", "authors": "C", "venue": "V", "year": "N/A", "url": "u3"},
        ]
        materialized = self.fetcher._materialize_year_fetch_citations(
            old_citations,
            {2024: [
                {"title": "New-2024-A", "authors": "A", "venue": "V2", "year": "2024", "url": "u4"},
                {"title": "New-2024-B", "authors": "D", "venue": "V2", "year": "2024", "url": "u5"},
            ]},
            refreshed_unyeared=[{"title": "Keep-NY", "authors": "C", "venue": "V", "year": "N/A", "url": "u3"}],
        )

        self.assertEqual(
            [c["title"] for c in materialized],
            ["Keep-2023", "New-2024-A", "New-2024-B", "Keep-NY"],
        )

    def test_selective_refresh_does_not_early_stop_after_refetching_cached_citation(self):
        self.fetcher._probe_citation_start_year = lambda citedby_url, num_citations=None, pub_year=None: 2024
        pages = {
            (2025, 0): [
                {"bib": {"title": "Cached-2025", "author": ["A"], "venue": "V2025", "pub_year": "2025"}, "pub_url": "u-cached", "cites_id": "cid-cached"},
            ],
            (2024, 0): [
                {"bib": {"title": "New-2024", "author": ["B"], "venue": "V2024", "pub_year": "2024"}, "pub_url": "u-new"},
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

        old_citations = [
            {"title": "Cached-2025", "authors": "A", "venue": "V2025", "year": "2025", "url": "u-cached", "cites_id": "cid-cached"},
        ]

        with patch.object(scholar_citation, "_SearchScholarIterator", FakeIterator):
            citations = self.fetcher._fetch_by_year(
                citedby_url="/scholar?cites=123",
                old_citations=old_citations,
                fresh_citations=[],
                save_progress=lambda complete: None,
                num_citations=2,
                pub_year="2025",
                prev_scholar_count=1,
                selective_refresh_years={2024, 2025},
            )

        self.assertEqual(requests, [(2025, 0), (2024, 0)])
        self.assertEqual([c["title"] for c in citations], ["New-2024", "Cached-2025"])
        self.assertEqual(self.fetcher._new_citations_count, 1)

        self.fetcher._completed_year_segments = {2024}
        self.fetcher._probed_year_counts = {2024: 2}
        self.fetcher._probed_year_count_complete = True
        self.fetcher._cached_year_counts = {2024: 1}
        self.fetcher._probe_citation_start_year = lambda citedby_url, num_citations=None, pub_year=None: 2024

        requests = []

        class FakeIterator:
            def __init__(self, nav, url):
                requests.append(url)
                self.items = [{
                    "bib": {"title": "Fetched-2024", "author": ["A"], "venue": "V2024", "pub_year": "2024"},
                    "pub_url": "u2024",
                }]
                self.index = 0
                self._finished_current_page = False

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
            fake_datetime.now.return_value = types.SimpleNamespace(year=2024)
            citations = self.fetcher._fetch_by_year(
                citedby_url="/scholar?cites=123",
                old_citations=[{"title": "Old-2024", "authors": "A", "venue": "V", "year": "2024", "url": "u-old"}],
                fresh_citations=[],
                save_progress=lambda complete: None,
                num_citations=2,
                pub_year="2024",
                prev_scholar_count=0,
                allow_incremental_early_stop=False,
                selective_refresh_years={2024},
            )

        output = fake_stdout.getvalue()
        self.assertEqual(len(requests), 1)
        self.assertEqual([c["title"] for c in citations], ["Fetched-2024"])
        self.assertIn("Selective refresh years: 2024", output)
        self.assertIn("Year 2024: fetching", output)
        self.assertNotIn("skip (already completed earlier in this run)", output)

    def test_force_year_rebuild_uses_replaced_year_totals_for_status_and_stop(self):
        self.fetcher._probed_year_counts = {2018: 37, 2019: 27}
        self.fetcher._probed_year_count_complete = True
        self.fetcher._cached_year_counts = {2018: 36, 2019: 27}
        self.fetcher._probe_citation_start_year = lambda citedby_url, num_citations=None, pub_year=None: 2018

        old_citations = [
            {"title": f"Old-2018-{i}", "authors": "A", "venue": "V2018", "year": "2018", "url": f"u2018-{i}"}
            for i in range(37)
        ] + [
            {"title": f"Old-2019-{i}", "authors": "B", "venue": "V2019", "year": "2019", "url": f"u2019-{i}"}
            for i in range(27)
        ]
        requests = []
        save_calls = []

        class FakeIterator:
            def __init__(self, nav, url):
                start = 0
                if "start=" in url:
                    start = int(url.split("start=")[1].split("&")[0])
                year = int(url.split("as_ylo=")[1].split("&")[0])
                requests.append((year, start))
                if year == 2018:
                    self.items = [
                        {
                            "bib": {"title": f"Old-2018-{i}", "author": ["A"], "venue": "V2018", "pub_year": "2018"},
                            "pub_url": f"u2018-{i}",
                        }
                        for i in range(10)
                    ]
                else:
                    self.items = [
                        {
                            "bib": {"title": f"Fetched-2019-{i}", "author": ["B"], "venue": "V2019", "pub_year": "2019"},
                            "pub_url": f"uf2019-{i}",
                        }
                        for i in range(27)
                    ]
                self.index = 0
                self._finished_current_page = False

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
            fake_datetime.now.return_value = types.SimpleNamespace(year=2019)
            citations = self.fetcher._fetch_by_year(
                citedby_url="/scholar?cites=123",
                old_citations=old_citations,
                fresh_citations=[],
                save_progress=lambda complete: save_calls.append(complete),
                num_citations=64,
                pub_year="2018",
                prev_scholar_count=64,
                allow_incremental_early_stop=False,
                force_year_rebuild=True,
                selective_refresh_years={2018, 2019},

            )

        output = fake_stdout.getvalue()
        self.assertEqual(requests, [(2018, 0)])
        self.assertEqual(save_calls, [False, False, True])
        self.assertEqual(len(citations), 37)
        self.assertIn("Year 2018 status: paper_total=37", output)
        self.assertIn("Year 2019: skip (histogram count match; cached=27, probe=27, probe_complete=True)", output)
        self.assertNotIn("Reached target (64 >= 64)", output)

    def test_year_partial_save_uses_authoritative_replaced_totals(self):
        self.fetcher._probed_year_counts = {2024: 2}
        self.fetcher._probed_year_count_complete = True
        self.fetcher._cached_year_counts = {2024: 1}
        self.fetcher._probe_citation_start_year = lambda citedby_url, num_citations=None, pub_year=None: 2024

        old_citations = [
            {"title": "Old-2024", "authors": "A", "venue": "V2024", "year": "2024", "url": "u-old"},
            {"title": "Keep-2023", "authors": "B", "venue": "V2023", "year": "2023", "url": "u-keep"},
        ]
        partial_snapshots = []

        class FakeIterator:
            def __init__(self, nav, url):
                self.items = [
                    {
                        "bib": {"title": "Fresh-2024-A", "author": ["A"], "venue": "V2024", "pub_year": "2024"},
                        "pub_url": "u-fresh-a",
                    },
                    {
                        "bib": {"title": "Fresh-2024-B", "author": ["C"], "venue": "V2024", "pub_year": "2024"},
                        "pub_url": "u-fresh-b",
                    },
                ]
                self.index = 0
                self._finished_current_page = False

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

        fresh_citations = []

        def fake_save_progress(complete):
            partial_snapshots.append((complete, [c["title"] for c in fresh_citations]))

        with patch.object(scholar_citation, "_SearchScholarIterator", FakeIterator), \
             patch.object(scholar_citation, "datetime") as fake_datetime:
            fake_datetime.now.return_value = types.SimpleNamespace(year=2024)
            citations = self.fetcher._fetch_by_year(
                citedby_url="/scholar?cites=123",
                old_citations=old_citations,
                fresh_citations=fresh_citations,
                save_progress=fake_save_progress,
                num_citations=3,
                pub_year="2024",
                prev_scholar_count=1,
                allow_incremental_early_stop=False,
                force_year_rebuild=True,
                selective_refresh_years={2024},
            )

        self.assertEqual([c["title"] for c in citations], ["Keep-2023", "Fresh-2024-A", "Fresh-2024-B"])
        self.assertEqual(partial_snapshots[0], (False, ["Keep-2023", "Fresh-2024-A", "Fresh-2024-B"]))
        self.assertEqual(partial_snapshots[-1], (True, ["Keep-2023", "Fresh-2024-A", "Fresh-2024-B"]))

    def test_main_mirrors_stdout_to_timestamped_log_file(self):
        fake_args = types.SimpleNamespace(
            author="test-author",
            output_dir=None,
            limit=None,
            skip=0,
            recheck_citations=False,
            interactive_captcha=False,
            accelerate=1.0,
            force_refresh_pubs=False,
        )

        class FakeAuthorFetcher:
            def __init__(self, author_id, output_dir, delay_scale=1.0):
                self.output_dir = output_dir
                self.profile_json = os.path.join(output_dir, "author_test-author_profile.json")

            def load_prev_profile(self):
                return None

            def run(self, force_refresh_pubs=False):
                with open(self.profile_json, "w", encoding="utf-8") as f:
                    json.dump({"total_citations": 1, "total_publications": 1, "author_info": {"citedby": 1}}, f)
                print("Profile fetch done")
                return True

        class FakePaperFetcher:
            def __init__(self, *args, **kwargs):
                pass

            def run(self):
                print("Citation fetch done")
                return True

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_args.output_dir = tmpdir
            with patch.object(scholar_citation, "parse_args", return_value=fake_args), \
                 patch.object(scholar_citation, "setup_proxy", side_effect=lambda: print("Proxy ready")), \
                 patch.object(scholar_citation, "AuthorProfileFetcher", FakeAuthorFetcher), \
                 patch.object(scholar_citation, "PaperCitationFetcher", FakePaperFetcher), \
                 patch("sys.stdout", new_callable=StringIO) as fake_stdout:
                scholar_citation.main()

            output = fake_stdout.getvalue()
            logs_dir = os.path.join(tmpdir, "logs")
            log_files = os.listdir(logs_dir)
            self.assertEqual(len(log_files), 1)
            self.assertRegex(log_files[0], r"^author_test-author_run_\d{8}_\d{6}\.log$")
            log_path = os.path.join(logs_dir, log_files[0])
            with open(log_path, "r", encoding="utf-8") as f:
                log_text = f.read()

        self.assertIn("Run log:", output)
        self.assertIn("Author ID: test-author", output)
        self.assertIn("Proxy ready", output)
        self.assertIn("Profile fetch done", output)
        self.assertIn("Citation fetch done", output)
        self.assertIn("Author ID: test-author", log_text)
        self.assertIn("Proxy ready", log_text)
        self.assertIn("Profile fetch done", log_text)
        self.assertIn("Citation fetch done", log_text)

    def test_main_skip_message_is_written_to_log_file(self):
        prev_profile = {
            "total_citations": 5,
            "total_publications": 2,
            "author_info": {"citedby": 5},
        }
        curr_profile = {
            "total_citations": 5,
            "total_publications": 2,
            "author_info": {"citedby": 5},
        }
        fake_args = types.SimpleNamespace(
            author="test-author",
            output_dir=None,
            limit=None,
            skip=0,
            recheck_citations=False,
            interactive_captcha=False,
            accelerate=1.0,
            force_refresh_pubs=False,
        )

        class FakeAuthorFetcher:
            def __init__(self, author_id, output_dir, delay_scale=1.0):
                self.calls = 0

            def load_prev_profile(self):
                self.calls += 1
                return prev_profile if self.calls == 1 else curr_profile

            def run(self, force_refresh_pubs=False):
                print("Profile fetch done")
                return True

        class FakePaperFetcher:
            def __init__(self, *args, **kwargs):
                pass

            def has_pending_work(self):
                return False

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_args.output_dir = tmpdir
            with patch.object(scholar_citation, "parse_args", return_value=fake_args), \
                 patch.object(scholar_citation, "setup_proxy", side_effect=lambda: print("Proxy ready")), \
                 patch.object(scholar_citation, "AuthorProfileFetcher", FakeAuthorFetcher), \
                 patch.object(scholar_citation, "PaperCitationFetcher", FakePaperFetcher), \
                 patch("sys.stdout", new_callable=StringIO) as fake_stdout:
                scholar_citation.main()

            output = fake_stdout.getvalue()
            logs_dir = os.path.join(tmpdir, "logs")
            log_path = os.path.join(logs_dir, os.listdir(logs_dir)[0])
            with open(log_path, "r", encoding="utf-8") as f:
                log_text = f.read()

        self.assertIn("All citation caches are complete. Skipping citation fetch.", output)
        self.assertIn("All citation caches are complete. Skipping citation fetch.", log_text)

    def test_year_fetch_backfills_missing_year_from_query_context(self):
        self.fetcher._probe_citation_start_year = lambda citedby_url, num_citations=None, pub_year=None: 2024
        self.fetcher._probed_year_counts = {2024: 1}
        self.fetcher._probed_year_count_complete = True
        self.fetcher._cached_year_counts = {}

        class FakeIterator:
            def __init__(self, nav, url):
                self.items = [{
                    "bib": {"title": "Fetched-2024", "author": ["A"], "venue": "V2024"},
                    "pub_url": "u2024",
                }]
                self.index = 0
                self._finished_current_page = False

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
            fake_datetime.now.return_value = types.SimpleNamespace(year=2024)
            citations = self.fetcher._fetch_by_year(
                citedby_url="/scholar?cites=123",
                old_citations=[],
                fresh_citations=[],
                save_progress=lambda complete: None,
                num_citations=1,
                pub_year="2024",
                prev_scholar_count=0,
                allow_incremental_early_stop=False,
            )

        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0]["year"], "2024")
        self.assertEqual(self.fetcher._year_count_map(citations), {2024: 1})

    def test_year_fetch_keeps_explicit_pub_year_from_citation(self):
        self.fetcher._probe_citation_start_year = lambda citedby_url, num_citations=None, pub_year=None: 2024
        self.fetcher._probed_year_counts = {2024: 1}
        self.fetcher._probed_year_count_complete = True
        self.fetcher._cached_year_counts = {}

        class FakeIterator:
            def __init__(self, nav, url):
                self.items = [{
                    "bib": {"title": "Fetched-2023", "author": ["A"], "venue": "V2023", "pub_year": "2023"},
                    "pub_url": "u2023",
                }]
                self.index = 0
                self._finished_current_page = False

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
            fake_datetime.now.return_value = types.SimpleNamespace(year=2024)
            citations = self.fetcher._fetch_by_year(
                citedby_url="/scholar?cites=123",
                old_citations=[],
                fresh_citations=[],
                save_progress=lambda complete: None,
                num_citations=1,
                pub_year="2024",
                prev_scholar_count=0,
                allow_incremental_early_stop=False,
            )

        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0]["year"], "2023")
        self.assertEqual(self.fetcher._year_count_map(citations), {2023: 1})

    def test_save_output_flushes_promoted_publication_counts(self):
        pub = {
            "no": 1,
            "title": "Paper One",
            "num_citations": 1,
            "year": "2024",
            "venue": "Venue",
        }
        cache_pub = {
            "title": "Paper One",
            "num_citations": 1,
            "citedby_url": "/scholar?cites=1",
            "url": "https://example.com/paper",
        }
        result = {
            "pub": dict(pub),
            "citations": [
                {"title": "Citing Paper", "authors": "A", "venue": "CV", "year": "2024", "url": "https://example.com/cite"}
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            self.fetcher.profile_json = os.path.join(tmpdir, "author_test-author_profile.json")
            self.fetcher.pubs_cache = os.path.join(tmpdir, "publications.json")
            self.fetcher.out_json = os.path.join(tmpdir, "author_test-author_paper_citations.json")
            self.fetcher.out_xlsx = os.path.join(tmpdir, "author_test-author_paper_citations.xlsx")
            self.fetcher._run_start_time = 0
            self.fetcher._updated_publication_counts = {"Paper One": 3}
            self.fetcher._profile_data = {
                "author_info": {"name": "Author", "citedby": 10, "cites_per_year": {"2026": 10}},
                "publications": [dict(pub)],
                "fetch_time": "2026-04-01T00:00:00",
                "total_publications": 1,
                "total_citations": 10,
                "citation_count_summary": {},
                "change_history": [],
            }
            self.fetcher._pubs_data = {"publications": [dict(cache_pub)]}
            with open(self.fetcher.profile_json, "w", encoding="utf-8") as f:
                json.dump(self.fetcher._profile_data, f)
            with open(self.fetcher.pubs_cache, "w", encoding="utf-8") as f:
                json.dump(self.fetcher._pubs_data, f)

            self.fetcher._save_output([result])

            with open(self.fetcher.profile_json, "r", encoding="utf-8") as f:
                saved_profile = json.load(f)
            with open(self.fetcher.pubs_cache, "r", encoding="utf-8") as f:
                saved_pubs = json.load(f)

        self.assertEqual(saved_profile["publications"][0]["num_citations"], 3)
        self.assertEqual(saved_profile["publications"][0]["no"], 1)
        self.assertEqual(saved_profile["fetch_time"], "2026-04-01T00:00:00")
        self.assertEqual(saved_pubs["publications"][0]["num_citations"], 3)

    def test_save_output_writes_excel_run_metadata_from_json_payload(self):
        pub = {
            "no": 1,
            "title": "Paper One",
            "num_citations": 1,
            "year": "2024",
            "venue": "Venue",
        }
        result = {
            "pub": pub,
            "citations": [
                {"title": "Citing Paper", "authors": "A", "venue": "CV", "year": "2024", "url": "https://example.com/cite"}
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            self.fetcher.profile_json = os.path.join(tmpdir, "author_test-author_profile.json")
            self.fetcher.out_json = os.path.join(tmpdir, "author_test-author_paper_citations.json")
            self.fetcher.out_xlsx = os.path.join(tmpdir, "author_test-author_paper_citations.xlsx")
            self.fetcher._run_start_time = 0
            with open(self.fetcher.profile_json, "w", encoding="utf-8") as f:
                json.dump({"publications": [pub]}, f)

            captured = {}
            original_workbook = scholar_citation.openpyxl.Workbook

            class CapturingWorkbook(original_workbook):
                def __init__(self):
                    super().__init__()
                    captured["workbook"] = self

            with patch.object(scholar_citation.openpyxl, "Workbook", CapturingWorkbook), \
                 patch.object(scholar_citation, "datetime") as fake_datetime:
                fake_datetime.now.return_value = types.SimpleNamespace(
                    isoformat=lambda: "2026-04-07T12:34:56",
                    strftime=lambda fmt: "20260407_123456",
                    year=2026,
                )
                self.fetcher._save_output([result])

            with open(self.fetcher.out_json, "r", encoding="utf-8") as f:
                payload = json.load(f)

        workbook = captured["workbook"]
        self.assertEqual(payload["author_id"], "test-author")
        self.assertEqual(payload["fetch_time"], "2026-04-07T12:34:56")
        self.assertEqual(payload["total_papers"], 1)
        self.assertEqual(payload["total_citations_collected"], 1)
        self.assertEqual(len(workbook.sheets), 3)
        self.assertEqual(workbook.sheets[2].title, "Run Metadata")
        metadata_sheet = workbook.sheets[2]
        self.assertEqual(metadata_sheet.cells[(1, 1)].value, "Author ID")
        self.assertEqual(metadata_sheet.cells[(1, 2)].value, payload["author_id"])
        self.assertEqual(metadata_sheet.cells[(2, 1)].value, "Fetch Time")
        self.assertEqual(metadata_sheet.cells[(2, 2)].value, payload["fetch_time"])
        self.assertEqual(metadata_sheet.cells[(3, 1)].value, "Total Papers")
        self.assertEqual(metadata_sheet.cells[(3, 2)].value, payload["total_papers"])
        self.assertEqual(metadata_sheet.cells[(4, 1)].value, "Total Citations Collected")
        self.assertEqual(metadata_sheet.cells[(4, 2)].value, payload["total_citations_collected"])

        citations_sheet = workbook.sheets[1]
        self.assertEqual(citations_sheet.title, "All Citations")
        self.assertEqual(citations_sheet.cells[(2, 5)].value, payload["papers"][0]["citations"][0]["year"])

        status = self.fetcher._refresh_reconciliation_status(
            citations=[
                {"title": "A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
                {"title": "B", "authors": "B", "venue": "V", "year": "2025", "url": "u2"},
                {"title": "C", "authors": "C", "venue": "V", "year": "2025", "url": "u3"},
            ],
            num_citations=5,
            probed_year_counts={2024: 1, 2025: 2},
            probe_complete=True,
        )

        self.assertTrue(status["ok"])
        self.assertEqual(status["reason"], "matched_complete_histogram")
        self.assertEqual(status["histogram_total"], 3)
        self.assertEqual(status["unyeared_count"], 2)
        self.assertEqual(status["cached_year_total"], 3)

    def test_refresh_reconciliation_requests_escalation_on_histogram_mismatch(self):
        status = self.fetcher._refresh_reconciliation_status(
            citations=[
                {"title": "A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
                {"title": "B", "authors": "B", "venue": "V", "year": "2025", "url": "u2"},
                {"title": "C", "authors": "C", "venue": "V", "year": "2025", "url": "u3"},
            ],
            num_citations=5,
            probed_year_counts={2024: 2, 2025: 1},
            probe_complete=True,
        )

        self.assertFalse(status["ok"])
        self.assertEqual(status["reason"], "year_count_mismatch")
        self.assertEqual(status["histogram_total"], 3)

    def test_refresh_reconciliation_keeps_histogram_incomplete_status_when_probe_incomplete(self):
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

        self.assertFalse(status["ok"])
        self.assertEqual(status["reason"], "histogram_incomplete")
        self.assertEqual(status["histogram_total"], 1)
        self.assertEqual(status["cached_unyeared_count"], 2)

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
                    completed_years_in_current_run=[2024],
                    prev_scholar_count=2,
                    rehydrated_probed_year_counts={2024: 1, 2025: 1},
                    rehydrated_probe_complete=True,
                )

            self.assertEqual(citations, [])
            with open(cache_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["probed_year_counts"], {"2024": 1, "2025": 1})
            self.assertTrue(saved["probe_complete"])
            self.assertEqual(saved["completed_years_in_current_run"], [2024])
            self.assertEqual(saved["probed_year_total"], 2)
            self.assertEqual(saved["cached_unyeared_count"], 0)
            self.assertEqual(saved["citation_count_summary"]["scholar_total"], 2)
            self.assertEqual(saved["citation_count_summary"]["histogram_total"], 2)
            self.assertEqual(saved["citation_count_summary"]["cached_total"], 2)
            self.assertEqual(saved["citation_count_summary"]["cached_year_total"], 2)
            self.assertEqual(saved["citation_count_summary"]["dedup_count"], 0)

    def test_citation_status_stays_complete_when_only_unyeared_gap_changes(self):
        pub = {"title": "Paper", "num_citations": 5}
        cached = {
            "complete": True,
            "probed_year_counts": {"2024": 1, "2025": 2},
            "probed_year_total": 3,
            "probe_complete": True,
            "cached_year_counts": {"2024": 1, "2025": 2},
            "citations": [
                {"title": "A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
                {"title": "B", "authors": "B", "venue": "V", "year": "2025", "url": "u2"},
                {"title": "C", "authors": "C", "venue": "V", "year": "2025", "url": "u3"},
            ],
            "num_citations_on_scholar": 4,
            "num_citations_seen": 3,
        }

        with patch.object(self.fetcher, "_load_citation_cache", return_value=cached):
            status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "complete")

    def test_citation_status_stays_complete_when_cache_promoted_total_covers_current(self):
        pub = {"title": "Paper", "num_citations": 5}
        cached = {
            "complete": True,
            "probe_complete": False,
            "citations": [
                {"title": "A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
                {"title": "B", "authors": "B", "venue": "V", "year": "2025", "url": "u2"},
                {"title": "C", "authors": "C", "venue": "V", "year": "2025", "url": "u3"},
            ],
            "num_citations_on_scholar": 6,
            "num_citations_seen": 3,
        }

        with patch.object(self.fetcher, "_load_citation_cache", return_value=cached):
            status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "complete")

    def test_citation_status_marks_partial_when_histogram_changes(self):
        pub = {"title": "Paper", "num_citations": 5}
        cached = {
            "complete": True,
            "probed_year_counts": {"2024": 1, "2025": 3},
            "probed_year_total": 4,
            "probe_complete": True,
            "cached_year_counts": {"2024": 1, "2025": 2},
            "citations": [
                {"title": "A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
                {"title": "B", "authors": "B", "venue": "V", "year": "2025", "url": "u2"},
                {"title": "C", "authors": "C", "venue": "V", "year": "2025", "url": "u3"},
            ],
        }

        with patch.object(self.fetcher, "_load_citation_cache", return_value=cached):
            status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "partial")

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
            "completed_years_in_current_run": [2024],
            "dedup_count": 0,
            "complete": False,
            "probed_year_counts": {"2024": 1},
        }
        latest_cache = {
            "citations": [{"title": "Cached-2", "authors": "A", "venue": "V", "year": "2024", "url": "u2"}],
            "completed_years_in_current_run": [2024, 2025],
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
                    "completed_years_in_current_run": list(kwargs["completed_years_in_current_run"]),
                    "saved_dedup_count": kwargs["saved_dedup_count"],
                    "rehydrated_probed_year_counts": kwargs["rehydrated_probed_year_counts"],
                    "rehydrated_probe_complete": kwargs["rehydrated_probe_complete"],
                }
            )
            if len(fetch_calls) == 1:
                raise RuntimeError("temporary failure")
            return resume_from

        with patch.object(self.fetcher, "_fetch_citations_with_progress", side_effect=fake_fetch), \
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
        self.assertEqual(fetch_calls[1]["completed_years_in_current_run"], [2024, 2025])
        self.assertEqual(fetch_calls[1]["saved_dedup_count"], 3)
        self.assertEqual(fetch_calls[1]["rehydrated_probed_year_counts"], {2024: 1, 2025: 79})
        self.assertTrue(fetch_calls[1]["rehydrated_probe_complete"])

    def test_run_main_loop_logs_year_count_mismatch_without_escalating_direct_policy(self):
        pub = {
            "no": 1,
            "title": "Big Paper",
            "num_citations": 60,
            "year": "2023",
            "venue": "V",
        }
        results = []
        final_citations = [
            {"title": "Fetched-2024-A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
        ]

        def cache_status(current_pub):
            self.assertEqual(current_pub["title"], pub["title"])
            return "partial", {"citations": []}

        with patch.object(self.fetcher, "_fetch_citations_with_progress", return_value=final_citations) as mock_fetch, \
             patch.object(self.fetcher, "_wait_proxy_switch", side_effect=AssertionError("should not wait for proxy switch")), \
             patch.object(self.fetcher, "_refresh_reconciliation_status", return_value={"ok": False, "reason": "year_count_mismatch"}), \
             patch.object(scholar_citation, "datetime") as fake_datetime, \
             patch("scholar_citation.rand_delay", return_value=0), \
             patch("scholar_citation.time.sleep", return_value=None), \
             patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            fake_datetime.now.return_value = types.SimpleNamespace(
                year=2026,
                strftime=lambda fmt: "2026-04-10 12:00:00",
                isoformat=lambda: "2026-04-10T12:00:00",
            )
            self.fetcher._run_main_loop(
                publications=[pub],
                cache_status=cache_status,
                url_map={pub["title"]: {"citedby_url": "/scholar?cites=123", "pub_url": "https://example.com/paper"}},
                need_fetch=[(pub, "partial", {"citations": []})],
                results=results,
                fetch_idx=0,
            )

        log_output = fake_stdout.getvalue()
        self.assertEqual(mock_fetch.call_count, 1)
        self.assertEqual(mock_fetch.call_args.kwargs["fetch_policy"]["mode"], "direct")
        self.assertFalse(mock_fetch.call_args.kwargs["force_year_rebuild"])
        self.assertNotIn("Escalating to full revalidation", log_output)
        self.assertNotIn("Retrying escalated full revalidation with in-memory state", log_output)
        self.assertIn("Refresh check: year_count_mismatch", log_output)
        self.assertEqual(results[0]["citations"], final_citations)

    def test_run_main_loop_logs_final_year_summary_when_years_present(self):
        pub = {
            "no": 1,
            "title": "Big Paper",
            "num_citations": 3,
            "year": "2024",
            "venue": "V",
        }
        cached = None
        publications = [pub]
        results = []
        final_citations = [
            {"title": "Y2025", "authors": "A", "venue": "V1", "year": "2025", "url": "u1"},
            {"title": "Y2024", "authors": "B", "venue": "V2", "year": "2024", "url": "u2"},
            {"title": "NoYear", "authors": "C", "venue": "V3", "year": "N/A", "url": "u3"},
        ]

        def cache_status(current_pub):
            self.assertEqual(current_pub["title"], pub["title"])
            return "missing", cached

        with patch.object(self.fetcher, "_resolve_refresh_strategy", return_value={
                "mode": "missing",
                "resume_from": [],
                "completed_years_in_current_run": [],
                "partial_year_start": {},
                "saved_dedup_count": 0,
                "allow_incremental_early_stop": True,
                "force_year_rebuild": False,
                "selective_refresh_years": None,
                "rehydrated_probed_year_counts": None,
                "rehydrated_probe_complete": False,
                "action": "first fetch",
                "prev_scholar_count": 0,
             }), \
             patch.object(self.fetcher, "_fetch_citations_with_progress", return_value=final_citations), \
             patch("scholar_citation.rand_delay", return_value=0), \
             patch("scholar_citation.time.sleep", return_value=None), \
             patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            self.fetcher._run_main_loop(
                publications=publications,
                cache_status=cache_status,
                url_map={pub["title"]: {"citedby_url": "/scholar?cites=123", "pub_url": "https://example.com/paper"}},
                need_fetch=[(pub, "missing", cached)],
                results=results,
                fetch_idx=0,
            )

        output = fake_stdout.getvalue()
        self.assertIn("Done: 3 cached, 3 seen (Scholar: 3)", output)
        self.assertIn("Year summary:", output)
        self.assertIn("years_with_citations=2", output)
        self.assertIn("range=2024-2025 [2024:1, 2025:1]", output)
        self.assertIn("unyeared=1", output)
        self.assertEqual(results[0]["citations"], final_citations)
        self.assertEqual(self.fetcher._papers_fetched_count, 1)

    def test_run_main_loop_retries_with_saved_cache_after_failure(self):
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
            "completed_years_in_current_run": [2024],
            "dedup_count": 0,
            "complete": False,
        }
        stale_retry_cache = {
            "citations": [{"title": "Stale-2024", "authors": "A", "venue": "V", "year": "2024", "url": "u-stale"}],
            "completed_years_in_current_run": [2024, 2025],
            "dedup_count": 7,
            "probe_complete": True,
            "probed_year_counts": {"2024": 10, "2025": 70},
        }
        publications = [pub]
        results = []
        fetch_calls = []
        first_error = RuntimeError("temporary failure")
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
                    "completed_years_in_current_run": list(kwargs["completed_years_in_current_run"]),
                    "allow_incremental_early_stop": kwargs["allow_incremental_early_stop"],
                    "force_year_rebuild": kwargs["force_year_rebuild"],
                    "selective_refresh_years": kwargs["selective_refresh_years"],
                    "prev_scholar_count": kwargs["prev_scholar_count"],
                    "saved_dedup_count": kwargs["saved_dedup_count"],
                    "rehydrated_probed_year_counts": kwargs["rehydrated_probed_year_counts"],
                    "rehydrated_probe_complete": kwargs["rehydrated_probe_complete"],
                }
            )
            if len(fetch_calls) == 1:
                raise first_error
            return second_result

        with patch.object(self.fetcher, "_fetch_citations_with_progress", side_effect=fake_fetch), \
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
        self.assertEqual(fetch_calls[0]["completed_years_in_current_run"], [])

        self.assertTrue(fetch_calls[1]["allow_incremental_early_stop"])
        self.assertFalse(fetch_calls[1]["force_year_rebuild"])
        self.assertIsNone(fetch_calls[1]["selective_refresh_years"])
        self.assertEqual(fetch_calls[1]["prev_scholar_count"], 75)
        self.assertEqual(fetch_calls[1]["resume_from"], stale_retry_cache["citations"])
        self.assertEqual(fetch_calls[1]["completed_years_in_current_run"], [2024, 2025])
        self.assertEqual(fetch_calls[1]["saved_dedup_count"], 7)
        self.assertEqual(fetch_calls[1]["rehydrated_probed_year_counts"], {2024: 10, 2025: 70})
        self.assertTrue(fetch_calls[1]["rehydrated_probe_complete"])

        mock_load_cache.assert_called_once()
        self.assertIn("Retrying with 1 cached citations from previous attempt", log_output)
        self.assertNotIn("Escalating to full revalidation", log_output)
        self.assertNotIn("Retrying escalated full revalidation with in-memory state", log_output)

        self.assertEqual(results[0]["citations"], second_result)
        self.assertEqual(self.fetcher._papers_fetched_count, 1)

    def test_run_main_loop_records_histogram_incomplete_without_refetch(self):
        pub = {
            "no": 1,
            "title": "Looping Paper",
            "num_citations": 132,
            "year": "2021",
            "venue": "V",
        }
        cached = {
            "citations": [{"title": "Cached", "authors": "A", "venue": "V", "year": "2021", "url": "u0"}],
            "num_citations_on_scholar": 131,
            "complete": False,
        }
        fetched_citations = [
            {"title": "Fetched-2021", "authors": "A", "venue": "V", "year": "2021", "url": "u1"},
            {"title": "Fetched-2022", "authors": "B", "venue": "V", "year": "2022", "url": "u2"},
            {"title": "Fetched-2023", "authors": "C", "venue": "V", "year": "2023", "url": "u3"},
        ]
        publications = [pub]
        results = []

        def cache_status(current_pub):
            self.assertEqual(current_pub["title"], pub["title"])
            return "partial", cached

        refresh_status = {
            "ok": False,
            "reason": "histogram_incomplete",
            "probe_complete": False,
            "scholar_total": 132,
            "histogram_total": 0,
            "cached_total": 3,
            "cached_year_total": 3,
            "dedup_count": 0,
        }

        with patch.object(self.fetcher, "_fetch_citations_with_progress", return_value=fetched_citations) as mock_fetch, \
             patch.object(self.fetcher, "_refresh_reconciliation_status", return_value=refresh_status), \
             patch.object(self.fetcher, "_wait_proxy_switch", return_value=None), \
             patch.object(self.fetcher, "_load_citation_cache") as mock_load_cache, \
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
        self.assertEqual(mock_fetch.call_count, 1)
        mock_load_cache.assert_not_called()
        self.assertNotIn("Retrying post-fetch reconciliation with in-memory citations", log_output)
        self.assertIn("Histogram is incomplete; recording current results without escalation", log_output)
        self.assertNotIn("Escalating to full revalidation", log_output)
        self.assertEqual(results[0]["citations"], fetched_citations)
    def test_run_main_loop_records_selective_refresh_reconciliation_failure_without_escalation(self):
        pub = {
            "no": 1,
            "title": "Selective Paper",
            "num_citations": 131,
            "year": "2021",
            "venue": "V",
        }
        cached = {
            "citations": [{"title": "Cached", "authors": "A", "venue": "V", "year": "2021", "url": "u0"}],
            "num_citations_on_scholar": 129,
            "complete": False,
        }
        fetched_citations = [
            {"title": "Fetched-2022", "authors": "A", "venue": "V", "year": "2022", "url": "u1"},
            {"title": "Fetched-2024", "authors": "B", "venue": "V", "year": "2024", "url": "u2"},
        ]
        results = []

        def cache_status(current_pub):
            self.assertEqual(current_pub["title"], pub["title"])
            return "partial", cached

        attempt_state = {
            "mode": "update",
            "resume_from": cached["citations"],
            "completed_years_in_current_run": [],
            "partial_year_start": {},
            "saved_dedup_count": 0,
            "allow_incremental_early_stop": True,
            "force_year_rebuild": False,
            "selective_refresh_years": {2022, 2024, 2026},
            "rehydrated_probed_year_counts": {2021: 2, 2022: 10, 2023: 17, 2024: 36, 2025: 51, 2026: 14},
            "rehydrated_probe_complete": False,
            "action": "update",
            "prev_scholar_count": 129,
            "fetch_policy": {"mode": "year", "covered_years": 6, "avg_citations_per_year": 21.8, "pub_year": 2021, "reason": "high_average_per_year"},
        }

        with patch.object(self.fetcher, "_resolve_refresh_strategy", return_value=attempt_state), \
             patch.object(self.fetcher, "_fetch_citations_with_progress", return_value=fetched_citations) as mock_fetch, \
             patch.object(self.fetcher, "_refresh_reconciliation_status", return_value={"ok": False, "reason": "histogram_incomplete"}), \
             patch("scholar_citation.rand_delay", return_value=0), \
             patch("scholar_citation.time.sleep", return_value=None), \
             patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            self.fetcher._run_main_loop(
                publications=[pub],
                cache_status=cache_status,
                url_map={pub["title"]: {"citedby_url": "/scholar?cites=123", "pub_url": "https://example.com/paper"}},
                need_fetch=[(pub, "partial", cached)],
                results=results,
                fetch_idx=0,
            )

        log_output = fake_stdout.getvalue()
        self.assertEqual(mock_fetch.call_count, 1)
        self.assertIn("Selective refresh reconciliation failed; recording current results without escalation", log_output)
        self.assertNotIn("Escalating to full revalidation", log_output)
        self.assertNotIn("Retrying escalated full revalidation with in-memory state", log_output)
        self.assertEqual(results[0]["citations"], fetched_citations)


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

    def test_save_profile_json_preserves_explicit_fetch_time(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fetcher = scholar_citation.AuthorProfileFetcher("author", output_dir=tmpdir)
            basics = {"name": "Test Author", "citedby": 10, "cites_per_year": {"2026": 10}}

            profile = fetcher.save_profile_json(
                basics,
                [],
                change_history=[],
                fetch_time="2026-04-01T00:00:00",
            )

            self.assertEqual(profile["fetch_time"], "2026-04-01T00:00:00")
            with open(fetcher.profile_json, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["fetch_time"], "2026-04-01T00:00:00")

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
