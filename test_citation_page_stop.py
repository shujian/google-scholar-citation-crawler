import sys
import types
import unittest
from unittest.mock import patch


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
        self.fetcher._cached_year_counts = {}
        self.fetcher._dedup_count = 0
        self.fetcher._new_citations_count = 0
        self.fetcher._total_page_count = 0
        self.fetcher._delay_scale = 0
        self.fetcher.interactive_captcha = False
        self.fetcher._last_scholar_url = "https://scholar.google.com/citations?user=test-author&hl=en"
        self.fetcher._probe_citation_start_year = lambda citedby_url: 2025
        self.fetcher._refresh_scholarly_session = lambda: None

    def test_fetch_by_year_finishes_current_page_before_stopping(self):
        self.fetcher._probe_citation_start_year = lambda citedby_url: 2026
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


if __name__ == "__main__":
    unittest.main()
