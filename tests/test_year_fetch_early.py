import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.conftest import (
    FetcherTestCase, scholarly_mod, _DummyNav, _DummyIterator,
    patch, StringIO, json, tempfile,
)
import types
import unittest
import scholar_citation

class YearFetchEarlyTests(FetcherTestCase):
    def test_fetch_by_year_fetches_all_selected_years_without_early_stop(self):
        self.fetcher._probe_citation_start_year = lambda citedby_url, fetch_ctx=None, num_citations=None, pub_year=None: 2025
        pages = {
            (2026, 0): [
                {"bib": {"title": "Page1-A", "author": ["A"], "venue": "V1", "pub_year": "2026"}, "pub_url": "u1"},
                {"bib": {"title": "Page1-B", "author": ["B"], "venue": "V2", "pub_year": "2026"}, "pub_url": "u2"},
                {"bib": {"title": "Page1-C", "author": ["C"], "venue": "V3", "pub_year": "2026"}, "pub_url": "u3"},
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

        self.assertEqual([c["title"] for c in citations], ["OldYear-A", "Page1-A", "Page1-B", "Page1-C"])
        self.assertEqual(requests, [(2025, 0), (2026, 0)])
        self.assertEqual(self.fetcher._new_citations_count, 4)
        self.assertEqual(save_calls, [False, False, True])
    def test_histogram_authoritative_mode_does_not_stop_on_cached_total_match(self):
        self.fetcher._probed_year_counts = {2024: 1, 2025: 1, 2026: 1}
        self.fetcher._probed_year_count_complete = True
        self.fetcher._cached_year_counts = {2024: 0, 2025: 0, 2026: 0}
        self.fetcher._partial_year_start = {2024: 1}
        self.fetcher._probe_citation_start_year = lambda citedby_url, fetch_ctx=None, num_citations=None, pub_year=None: 2024

        old_citations = [
            {"title": f"Old-Unyeared-{i}", "authors": "A", "venue": "V", "year": "N/A", "url": f"u-old-{i}"}
            for i in range(3)
        ]
        requests = []

        class FakeIterator:
            def __init__(self, nav, url):
                start = 0
                if "start=" in url:
                    start = int(url.split("start=")[1].split("&")[0])
                year = int(url.split("as_ylo=")[1].split("&")[0])
                requests.append((year, start))
                self.items = ([{
                    "bib": {"title": f"Fetched-{year}", "author": ["A"], "venue": f"V{year}", "pub_year": str(year)},
                    "pub_url": f"u{year}",
                }] if start == 0 else [])
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
        self.assertEqual(requests, [(2024, 0), (2025, 0), (2026, 0)])
        self.assertEqual(self.fetcher._year_count_map(citations), {2025: 1, 2026: 1})
        self.assertIn("resuming from position 1 via page start 0 (skip first 1)", output)
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
                elif year == 2026 and start == 10:
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

        self.assertEqual(
            [c["title"] for c in citations],
            ["OldYear-A", "Page1-A", "Page1-B"],
        )
        self.assertEqual(requests, [(2025, 0), (2026, 0)])
        self.assertEqual(self.fetcher._new_citations_count, 3)

    def test_fetch_by_year_uses_histogram_total_as_target_and_backfills_year(self):
        self.fetcher._probed_year_counts = {2025: 1, 2026: 1}
        self.fetcher._probed_year_count_complete = False
        self.fetcher._probe_citation_start_year = lambda citedby_url, fetch_ctx=None, num_citations=None, pub_year=None: 2025

        requests = []

        class FakeIterator:
            def __init__(self, nav, url):
                start = 0
                if "start=" in url:
                    start = int(url.split("start=")[1].split("&")[0])
                year = int(url.split("as_ylo=")[1].split("&")[0])
                requests.append((year, start))
                if year == 2025:
                    self.items = ([{
                        "bib": {"title": "Y2025", "author": ["A"], "venue": "V2025", "pub_year": "?"},
                        "pub_url": "u2025",
                    }] if start == 0 else [])
                elif year == 2026:
                    self.items = ([{
                        "bib": {"title": "Y2026", "author": ["B"], "venue": "V2026"},
                        "pub_url": "u2026",
                    }] if start == 0 else [])
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
        self.assertIn("Year 2026 status: year_total=1", output)

    def test_short_page_exception_at_next_page_does_not_retry(self):
        """
        When the last fetched page is short (< SCHOLAR_PAGE_SIZE), an exception
        raised by the iterator while trying to auto-paginate to the next (non-existent)
        page should be treated as normal end-of-year, not as a captcha that needs retry.
        The while-True loop must exit without creating a second iterator.
        """
        self.fetcher._probe_citation_start_year = (
            lambda citedby_url, fetch_ctx=None, num_citations=None, pub_year=None: 2026
        )

        iterators_created = []

        class FakeIterator:
            def __init__(self_iter, nav, url):
                iterators_created.append(url)
                # Page at start=210: 9 items (short page)
                self_iter.items = [
                    {"bib": {"title": f"Item-{i}", "author": ["A"],
                             "venue": "V", "pub_year": "2026"},
                     "pub_url": f"u{i}"}
                    for i in range(9)
                ]
                self_iter.index = 0
                self_iter._finished_current_page = False
                self_iter._items_in_current_page = 0

            def __iter__(self_iter):
                return self_iter

            def __next__(self_iter):
                if self_iter.index >= len(self_iter.items):
                    # Simulate iterator trying the next page and hitting a block
                    self_iter._items_in_current_page = 0
                    self_iter._finished_current_page = False
                    raise RuntimeError("Simulated block at next page")
                item = self_iter.items[self_iter.index]
                self_iter.index += 1
                self_iter._items_in_current_page = self_iter.index
                if self_iter.index >= len(self_iter.items):
                    self_iter._finished_current_page = True
                return item

        with patch.object(scholar_citation, "_SearchScholarIterator", FakeIterator), \
             patch.object(scholar_citation, "datetime") as fake_datetime:
            fake_datetime.now.return_value = types.SimpleNamespace(year=2026)
            citations = self.fetcher._fetch_by_year(
                citedby_url="/scholar?cites=123",
                old_citations=[],
                fresh_citations=[],
                save_progress=lambda complete: None,
                num_citations=9,
                pub_year="2025",
                prev_scholar_count=0,
            )

        # Only ONE iterator should have been created (no retry loop)
        self.assertEqual(len(iterators_created), 1,
                         "Expected exactly one iterator; got retry loop")
        self.assertEqual(len(citations), 9)
        self.assertEqual([c["title"] for c in citations],
                         [f"Item-{i}" for i in range(9)])


if __name__ == '__main__':
    unittest.main()
