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

class DirectFetchTests(FetcherTestCase):
    def test_fetch_citations_with_progress_resumes_direct_fetch_from_page_offset(self):
        self.fetcher.save_every = 100
        seen_request_urls = []

        class FakeIterator:
            def __init__(self, nav, url):
                seen_request_urls.append(url)
                self.items = iter([
                    {"bib": {"title": "Skip-1", "author": ["A"], "venue": "V", "pub_year": "2024"}, "pub_url": "u1"},
                    {"bib": {"title": "Skip-2", "author": ["B"], "venue": "V", "pub_year": "2024"}, "pub_url": "u2"},
                    {"bib": {"title": "Skip-3", "author": ["C"], "venue": "V", "pub_year": "2024"}, "pub_url": "u3"},
                    {"bib": {"title": "Keep-4", "author": ["D"], "venue": "V", "pub_year": "2024"}, "pub_url": "u4"},
                    {"bib": {"title": "Keep-5", "author": ["E"], "venue": "V", "pub_year": "2025"}, "pub_url": "u5"},
                ])

            def __iter__(self):
                return self

            def __next__(self):
                return next(self.items)

        with patch.object(scholar_citation, "_SearchScholarIterator", FakeIterator), \
             patch.object(scholar_citation.scholarly, "citedby") as mock_citedby, \
             patch("sys.stdout", new_callable=StringIO):
            citations = self.fetcher._fetch_citations_with_progress(
                citedby_url="/scholar?cites=123",
                cache_path=os.path.join(tempfile.gettempdir(), "paper-direct-resume.json"),
                title="Paper",
                num_citations=25,
                pub_url="https://example.com/paper",
                pub_year="2024",
                resume_from=[],
                direct_resume_state={
                    "mode": "direct",
                    "next_index": 13,
                    "source_scholar_total": 25,
                    "citedby_url": "/scholar?cites=123",
                },
            )

        mock_citedby.assert_not_called()
        self.assertEqual(seen_request_urls, ["/scholar?cites=123&start=10"])
        self.assertEqual([citation["title"] for citation in citations], ["Keep-4", "Keep-5"])

    def test_fetch_citations_with_progress_partial_save_persists_direct_resume_state(self):
        cache_path = os.path.join(tempfile.gettempdir(), "paper-direct-partial-save.json")

        class InterruptAfterPageIterator:
            def __init__(self):
                self.items = [
                    {"bib": {"title": "First", "author": ["A"], "venue": "V", "pub_year": "2024"}, "pub_url": "u1"},
                    {"bib": {"title": "Second", "author": ["B"], "venue": "V", "pub_year": "2024"}, "pub_url": "u2"},
                ]
                self.index = 0
                self._finished_current_page = False

            def __iter__(self):
                return self

            def __next__(self):
                if self.index >= len(self.items):
                    raise KeyboardInterrupt()
                item = self.items[self.index]
                self.index += 1
                self._finished_current_page = self.index >= len(self.items)
                return item

        with patch.object(self.fetcher, "_iter_direct_citedby", return_value=InterruptAfterPageIterator()), \
             patch("sys.stdout", new_callable=StringIO):
            with self.assertRaises(KeyboardInterrupt):
                self.fetcher._fetch_citations_with_progress(
                    citedby_url="/scholar?cites=123",
                    cache_path=cache_path,
                    title="Paper",
                    num_citations=25,
                    pub_url="https://example.com/paper",
                    pub_year="2024",
                    resume_from=[{"title": "Cached", "authors": "A", "venue": "V", "year": "2024", "url": "u0"}],
                )

        with open(cache_path, "r", encoding="utf-8") as f:
            saved = json.load(f)

        self.assertEqual(saved["direct_resume_state"], {
            "mode": "direct",
            "next_index": 2,
            "source_scholar_total": 25,
            "citedby_url": "/scholar?cites=123",
        })

    def test_fetch_citations_with_progress_complete_save_clears_direct_resume_state(self):
        self.fetcher.save_every = 100
        cache_path = os.path.join(tempfile.gettempdir(), "paper-direct-complete-save.json")

        with patch.object(self.fetcher, "_iter_direct_citedby", return_value=iter([
            {"bib": {"title": "Only", "author": ["A"], "venue": "V", "pub_year": "2024"}, "pub_url": "u1"},
        ])), patch("sys.stdout", new_callable=StringIO):
            self.fetcher._fetch_citations_with_progress(
                citedby_url="/scholar?cites=123",
                cache_path=cache_path,
                title="Paper",
                num_citations=1,
                pub_url="https://example.com/paper",
                pub_year="2024",
                resume_from=[],
                direct_resume_state={
                    "mode": "direct",
                    "next_index": 7,
                    "source_scholar_total": 1,
                    "citedby_url": "/scholar?cites=123",
                },
            )

        with open(cache_path, "r", encoding="utf-8") as f:
            saved = json.load(f)

        self.assertIsNone(saved["direct_resume_state"])

    def test_fetch_citations_with_progress_direct_mode_passes_num_citations_to_scholarly(self):
        self.fetcher.save_every = 100
        seen_publications = []

        def fake_citedby(pub):
            seen_publications.append(dict(pub))
            return iter([
                {"bib": {"title": "Only", "author": ["A"], "venue": "V", "pub_year": "2024"}, "pub_url": "u1"},
            ])

        fetch_policy = {
            "mode": "direct",
            "covered_years": 3,
            "avg_citations_per_year": 40,
            "pub_year": 2024,
            "reason": "low_average_per_year",
        }

        with patch.object(scholar_citation.scholarly, "citedby", side_effect=fake_citedby), \
             patch("sys.stdout", new_callable=StringIO):
            citations = self.fetcher._fetch_citations_with_progress(
                citedby_url="/scholar?cites=123",
                cache_path=os.path.join(tempfile.gettempdir(), "paper-direct-num-citations.json"),
                title="Paper",
                num_citations=120,
                pub_url="https://example.com/paper",
                pub_year="2024",
                resume_from=[],
                fetch_policy=fetch_policy,
            )

        self.assertEqual(len(citations), 1)
        self.assertEqual(len(seen_publications), 1)
        self.assertEqual(seen_publications[0]["num_citations"], 120)
        self.assertEqual(seen_publications[0]["citedby_url"], "/scholar?cites=123")

    def test_fetch_citations_with_progress_sets_direct_current_attempt_url_before_fetch(self):
        self.fetcher.save_every = 100
        self.fetcher._current_attempt_url = (
            "https://scholar.google.com/scholar?as_ylo=2026&as_yhi=2026&hl=en&as_sdt=2005&sciodt=0,5&cites=old&scipsc=&start=70"
        )

        def fake_iter(citedby_url, direct_resume_state=None, num_citations=0):
            self.assertEqual(num_citations, 120)
            self.assertEqual(
                self.fetcher._current_attempt_url,
                "https://scholar.google.com/scholar?cites=123",
            )
            raise RuntimeError("boom")
            yield

        fetch_policy = {
            "mode": "direct",
            "covered_years": 3,
            "avg_citations_per_year": 40,
            "pub_year": 2024,
            "reason": "low_average_per_year",
        }

        with patch.object(self.fetcher, "_iter_direct_citedby", side_effect=fake_iter), \
             patch("sys.stdout", new_callable=StringIO):
            with self.assertRaisesRegex(RuntimeError, r"^boom$"):
                self.fetcher._fetch_citations_with_progress(
                    citedby_url="/scholar?cites=123",
                    cache_path=os.path.join(tempfile.gettempdir(), "paper-direct-current-url.json"),
                    title="Paper",
                    num_citations=120,
                    pub_url="https://example.com/paper",
                    pub_year="2024",
                    resume_from=[],
                    fetch_policy=fetch_policy,
                )

        self.assertEqual(self.fetcher._current_attempt_url, "https://scholar.google.com/scholar?cites=123")

    def test_fetch_citations_with_progress_resets_cached_year_counts_for_new_paper(self):
        resume_from = [
            {"title": "Current-2023", "authors": "A", "venue": "V1", "year": "2023", "url": "u1"},
            {"title": "Current-2024", "authors": "B", "venue": "V2", "year": "2024", "url": "u2"},
            {"title": "Current-NY", "authors": "C", "venue": "V3", "year": "N/A", "url": "u3"},
        ]
        self.fetcher._cached_year_counts = {2017: 3, 2018: 38, 2019: 107}
        observed = {}
        fetch_policy = {
            "mode": "year",
            "covered_years": 8,
            "avg_citations_per_year": 25,
            "pub_year": 2017,
            "reason": "high_average_per_year",
        }

        def fake_fetch_by_year(citedby_url, old_citations, fresh_citations, save_progress,
                               num_citations, pub_year, prev_scholar_count, **kwargs):
            observed["cached_year_counts"] = dict(self.fetcher._cached_year_counts)
            observed["old_titles"] = [citation["title"] for citation in old_citations]
            return list(old_citations)

        with patch.object(self.fetcher, "_fetch_by_year", side_effect=fake_fetch_by_year), \
             patch("sys.stdout", new_callable=StringIO):
            citations = self.fetcher._fetch_citations_with_progress(
                citedby_url="/scholar?cites=456",
                cache_path=os.path.join(tempfile.gettempdir(), "paper-year-reset.json"),
                title="Next Paper",
                num_citations=80,
                pub_url="https://example.com/next-paper",
                pub_year="2017",
                resume_from=resume_from,
                fetch_policy=fetch_policy,
            )

        self.assertEqual(observed["cached_year_counts"], {2023: 1, 2024: 1})
        self.assertEqual(observed["old_titles"], ["Current-2023", "Current-2024", "Current-NY"])
        self.assertEqual(self.fetcher._cached_year_counts, {2023: 1, 2024: 1})
        self.assertEqual(citations, resume_from)

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
        self.assertIn("Direct fetch target: scholar_total=3, prev_scholar=0, cached_total=0, allow_early_stop=True", output)
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
                self.items = ([{
                    "bib": {"title": f"Y{year}", "author": ["A"], "venue": f"V{year}", "pub_year": str(year)},
                    "pub_url": f"u{year}",
                }] if start == 0 else [])
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
                self.items = ([{
                    "bib": {"title": f"Y{year}", "author": ["A"], "venue": f"V{year}", "pub_year": str(year)},
                    "pub_url": f"u{year}",
                }] if start == 0 else [])
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
                self.items = ([{
                    "bib": {"title": f"Y{year}", "author": ["A"], "venue": f"V{year}", "pub_year": str(year)},
                    "pub_url": f"u{year}",
                }] if start == 0 else [])
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
                self.items = ([{
                    "bib": {"title": f"Y{year}", "author": ["A"], "venue": f"V{year}", "pub_year": str(year)},
                    "pub_url": f"u{year}",
                }] if start == 0 else [])
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
                self.items = ([
                    {"bib": {"title": f"Skip-1-{year}", "author": ["A"], "venue": f"V{year}", "pub_year": str(year)}, "pub_url": f"u{year}-1"},
                    {"bib": {"title": f"Skip-2-{year}", "author": ["B"], "venue": f"V{year}", "pub_year": str(year)}, "pub_url": f"u{year}-2"},
                    {"bib": {"title": f"Y{year}", "author": ["A"], "venue": f"V{year}", "pub_year": str(year)}, "pub_url": f"u{year}"},
                ] if start == 0 else [])
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
        self.assertEqual(requests, [(2024, 0)])
        self.assertEqual([c["title"] for c in citations], ["Y2024"])
        self.assertIn("Selective refresh years: 2024", output)
        self.assertIn("resuming from position 2 via page start 0 (skip first 2)", output)
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
                self._finished_current_page = False

            def __iter__(self):
                return self

            def __next__(self):
                self._finished_current_page = True
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
                self.items = ([
                    {"bib": {"title": f"Skip-1-{year}", "author": ["A"], "venue": f"V{year}", "pub_year": str(year)}, "pub_url": f"u{year}-1"},
                    {"bib": {"title": f"Skip-2-{year}", "author": ["B"], "venue": f"V{year}", "pub_year": str(year)}, "pub_url": f"u{year}-2"},
                    {"bib": {"title": f"Fetched-{year}", "author": ["D"], "venue": f"V{year}", "pub_year": str(year)}, "pub_url": f"uf{year}"},
                ] if start == 0 else [])
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

        self.assertEqual(requests, [(2025, 0)])
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
                self.items = ([
                    {"bib": {"title": f"Skip-1-{year}", "author": ["A"], "venue": f"V{year}", "pub_year": str(year)}, "pub_url": f"u{year}-1"},
                    {"bib": {"title": f"Skip-2-{year}", "author": ["A"], "venue": f"V{year}", "pub_year": str(year)}, "pub_url": f"u{year}-2"},
                    {"bib": {"title": f"Y{year}", "author": ["A"], "venue": f"V{year}", "pub_year": str(year)}, "pub_url": f"u{year}"},
                ] if start == 0 else [])
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
        self.assertEqual(requests, [(2025, 0)])
        self.assertEqual([c["title"] for c in citations], ["Y2025"])
        self.assertIn("    Partial resume points: 2025->2", output)
        self.assertIn("Year 2025: resuming from position 2 via page start 0 (skip first 2)", output)
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
        fetched_items = self._paged_direct_iterator([
            [
                {"bib": {"title": "Cached-A", "author": ["A"], "venue": "V", "pub_year": "2024"}, "pub_url": "new-a", "cites_id": "cid-a"},
                {"bib": {"title": "Fresh-B", "author": ["B"], "venue": "V2", "pub_year": "2024"}, "pub_url": "new-b", "cites_id": "cid-b"},
                {"bib": {"title": "Fresh-C", "author": ["C"], "venue": "V3", "pub_year": "2025"}, "pub_url": "new-c", "cites_id": "cid-c"},
            ],
            [
                {"bib": {"title": "Fresh-D should not be reached", "author": ["D"], "venue": "V4", "pub_year": "2025"}, "pub_url": "new-d", "cites_id": "cid-d"},
            ],
        ])
        old_citations = [
            {"title": "Cached-A", "authors": "A", "venue": "V", "year": "2024", "url": "old-a", "cites_id": "cid-a"},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = os.path.join(tmpdir, "paper.json")
            with patch.object(scholar_citation.scholarly, "citedby", return_value=fetched_items), \
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
        self.assertEqual([c["title"] for c in citations], ["Cached-A", "Fresh-B", "Fresh-C"])
        self.assertIn("Direct fetch: reached target (3 >= 2 including dedup), stopping early", output)
        self.assertNotIn("Fresh-D should not be reached", [c["title"] for c in citations])

    def test_direct_fetch_does_not_count_overlayed_cache_toward_target(self):
        fetched_items = [
            {"bib": {"title": "Fresh-A", "author": ["A"], "venue": "V", "pub_year": "2024"}, "pub_url": "new-a", "cites_id": "cid-a"},
            {"bib": {"title": "Fresh-B", "author": ["B"], "venue": "V2", "pub_year": "2024"}, "pub_url": "new-b", "cites_id": "cid-b"},
            {"bib": {"title": "Fresh-C", "author": ["C"], "venue": "V3", "pub_year": "2025"}, "pub_url": "new-c", "cites_id": "cid-c"},
        ]
        old_citations = [
            {"title": "Old-1", "authors": "A", "venue": "V", "year": "2020", "url": "old-1", "cites_id": "old-1"},
            {"title": "Old-2", "authors": "B", "venue": "V", "year": "2021", "url": "old-2", "cites_id": "old-2"},
            {"title": "Old-3", "authors": "C", "venue": "V", "year": "2022", "url": "old-3", "cites_id": "old-3"},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = os.path.join(tmpdir, "paper.json")
            with patch.object(scholar_citation.scholarly, "citedby", return_value=iter(fetched_items)), \
                 patch("sys.stdout", new_callable=StringIO) as fake_stdout:
                citations = self.fetcher._fetch_citations_with_progress(
                    citedby_url="/scholar?cites=123",
                    cache_path=cache_path,
                    title="Paper",
                    num_citations=3,
                    pub_url="https://example.com/paper",
                    pub_year="2024",
                    resume_from=old_citations,
                    completed_years_in_current_run=[],
                    prev_scholar_count=0,
                )

        output = fake_stdout.getvalue()
        self.assertEqual([c["title"] for c in citations], ["Fresh-A", "Fresh-B", "Fresh-C"])
        self.assertIn("Direct fetch: reached target (3 >= 3 including dedup), stopping early", output)
        self.assertNotIn("Direct fetch: reached target (4 >= 3)", output)
        self.assertNotIn("Direct fetch: reached target (5 >= 3)", output)
        self.assertNotIn("Direct fetch: reached target (6 >= 3)", output)

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

    def test_direct_fetch_scholar_increase_uses_per_paper_new_count(self):
        fetched_items = [
            {"bib": {"title": "Cached-A", "author": ["A"], "venue": "V", "pub_year": "2024"}, "pub_url": "cached-a", "cites_id": "cid-a"},
            {"bib": {"title": "Fresh-B", "author": ["B"], "venue": "V2", "pub_year": "2025"}, "pub_url": "fresh-b", "cites_id": "cid-b"},
            {"bib": {"title": "Fresh-C", "author": ["C"], "venue": "V3", "pub_year": "2026"}, "pub_url": "fresh-c", "cites_id": "cid-c"},
        ]
        old_citations = [
            {"title": "Cached-A", "authors": "A", "venue": "V", "year": "2024", "url": "old-a", "cites_id": "cid-a"},
        ]
        self.fetcher._new_citations_count = 267

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = os.path.join(tmpdir, "paper.json")
            with patch.object(scholar_citation.scholarly, "citedby", return_value=iter(fetched_items)), \
                 patch("sys.stdout", new_callable=StringIO) as fake_stdout:
                citations = self.fetcher._fetch_citations_with_progress(
                    citedby_url="/scholar?cites=123",
                    cache_path=cache_path,
                    title="Paper",
                    num_citations=18,
                    pub_url="https://example.com/paper",
                    pub_year="2024",
                    resume_from=old_citations,
                    completed_years_in_current_run=[],
                    prev_scholar_count=0,
                )

        output = fake_stdout.getvalue()
        self.assertEqual([c["title"] for c in citations], ["Cached-A", "Fresh-B", "Fresh-C"])
        self.assertNotIn("Direct fetch: recovered Scholar increase (268 >= 18)", output)
        self.assertNotIn("Direct fetch: recovered Scholar increase", output)

    def test_direct_fetch_stops_when_seen_total_reaches_target_with_dedup(self):
        fetched_items = self._paged_direct_iterator([
            [
                {"bib": {"title": "Fresh-A", "author": ["A"], "venue": "V", "pub_year": "2024"}, "pub_url": "new-a", "cites_id": "cid-a"},
                {"bib": {"title": "Fresh-A duplicate", "author": ["A"], "venue": "V", "pub_year": "2024"}, "pub_url": "new-a-dup", "cites_id": "cid-a"},
                {"bib": {"title": "Fresh-B", "author": ["B"], "venue": "V2", "pub_year": "2025"}, "pub_url": "new-b", "cites_id": "cid-b"},
            ],
            [
                {"bib": {"title": "Fresh-C should not be reached", "author": ["C"], "venue": "V3", "pub_year": "2026"}, "pub_url": "new-c", "cites_id": "cid-c"},
            ],
        ])

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = os.path.join(tmpdir, "paper.json")
            with patch.object(scholar_citation.scholarly, "citedby", return_value=fetched_items), \
                 patch("sys.stdout", new_callable=StringIO) as fake_stdout:
                citations = self.fetcher._fetch_citations_with_progress(
                    citedby_url="/scholar?cites=123",
                    cache_path=cache_path,
                    title="Paper",
                    num_citations=3,
                    pub_url="https://example.com/paper",
                    pub_year="2024",
                    resume_from=[],
                    completed_years_in_current_run=[],
                    prev_scholar_count=0,
                )

            with open(cache_path, "r", encoding="utf-8") as f:
                saved = json.load(f)

        output = fake_stdout.getvalue()
        self.assertEqual([c["title"] for c in citations], ["Fresh-A", "Fresh-B"])
        self.assertEqual(saved["num_citations_cached"], 2)
        self.assertEqual(saved["num_citations_seen"], 3)
        self.assertTrue(saved["complete"])
        self.assertEqual(saved["direct_fetch_diagnostics"]["seen_total"], 3)
        self.assertFalse(saved["direct_fetch_diagnostics"]["underfetched"])
        self.assertEqual(saved["direct_fetch_diagnostics"]["dedup_count"], 1)
        self.assertIn("Direct fetch: reached target (3 >= 3 including dedup)", output)

    def test_direct_fetch_reaching_target_waits_until_page_boundary(self):
        fetched_items = self._paged_direct_iterator([
            [
                {"bib": {"title": "Fresh-A", "author": ["A"], "venue": "V", "pub_year": "2024"}, "pub_url": "new-a", "cites_id": "cid-a"},
                {"bib": {"title": "Fresh-B", "author": ["B"], "venue": "V2", "pub_year": "2025"}, "pub_url": "new-b", "cites_id": "cid-b"},
                {"bib": {"title": "Fresh-C", "author": ["C"], "venue": "V3", "pub_year": "2026"}, "pub_url": "new-c", "cites_id": "cid-c"},
            ],
            [
                {"bib": {"title": "Fresh-D should not be reached", "author": ["D"], "venue": "V4", "pub_year": "2026"}, "pub_url": "new-d", "cites_id": "cid-d"},
            ],
        ])

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = os.path.join(tmpdir, "paper.json")
            with patch.object(scholar_citation.scholarly, "citedby", return_value=fetched_items), \
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
                    prev_scholar_count=0,
                )

        output = fake_stdout.getvalue()
        self.assertEqual([c["title"] for c in citations], ["Fresh-A", "Fresh-B", "Fresh-C"])
        self.assertIn("Progress saved (3 citations, 3 new in this run)", output)
        self.assertIn("Direct fetch: reached target (3 >= 2 including dedup), stopping early", output)
        self.assertNotIn("Fresh-D should not be reached", [c["title"] for c in citations])

    def test_direct_fetch_final_page_emits_single_progress_save(self):
        fetched_items = self._paged_direct_iterator([
            [
                {"bib": {"title": "Fresh-A", "author": ["A"], "venue": "V", "pub_year": "2024"}, "pub_url": "new-a", "cites_id": "cid-a"},
                {"bib": {"title": "Fresh-B", "author": ["B"], "venue": "V2", "pub_year": "2025"}, "pub_url": "new-b", "cites_id": "cid-b"},
                {"bib": {"title": "Fresh-C", "author": ["C"], "venue": "V3", "pub_year": "2026"}, "pub_url": "new-c", "cites_id": "cid-c"},
            ],
        ])

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = os.path.join(tmpdir, "paper.json")
            with patch.object(scholar_citation.scholarly, "citedby", return_value=fetched_items), \
                 patch("sys.stdout", new_callable=StringIO) as fake_stdout:
                citations = self.fetcher._fetch_citations_with_progress(
                    citedby_url="/scholar?cites=123",
                    cache_path=cache_path,
                    title="Paper",
                    num_citations=30,
                    pub_url="https://example.com/paper",
                    pub_year="2024",
                    resume_from=[],
                    completed_years_in_current_run=[],
                    prev_scholar_count=0,
                )

        output = fake_stdout.getvalue()
        self.assertEqual([c["title"] for c in citations], ["Fresh-A", "Fresh-B", "Fresh-C"])
        self.assertEqual(output.count("Progress saved (3 citations, 3 new in this run)"), 1)

        fetched_items = [
            {"bib": {"title": "Fresh-A", "author": ["A"], "venue": "V", "pub_year": "2024"}, "pub_url": "new-a", "cites_id": "cid-a"},
            {"bib": {"title": "Fresh-B", "author": ["B"], "venue": "V2", "pub_year": "2025"}, "pub_url": "new-b", "cites_id": "cid-b"},
            {"bib": {"title": "Fresh-C", "author": ["C"], "venue": "V3", "pub_year": "2026"}, "pub_url": "new-c", "cites_id": "cid-c"},
        ]
        fetch_policy = {
            "mode": "direct",
            "covered_years": 10,
            "avg_citations_per_year": 9.8,
            "pub_year": 2017,
            "reason": "low_average_per_year",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = os.path.join(tmpdir, "paper.json")
            with patch.object(scholar_citation.scholarly, "citedby", return_value=iter(fetched_items)), \
                 patch.object(self.fetcher, "_fetch_by_year") as mock_fetch_by_year, \
                 patch("sys.stdout", new_callable=StringIO) as fake_stdout:
                citations = self.fetcher._fetch_citations_with_progress(
                    citedby_url="/scholar?cites=123",
                    cache_path=cache_path,
                    title="Paper",
                    num_citations=98,
                    pub_url="https://example.com/paper",
                    pub_year="2024",
                    resume_from=[],
                    completed_years_in_current_run=[],
                    prev_scholar_count=0,
                    fetch_policy=fetch_policy,
                )
                mock_fetch_by_year.assert_not_called()

            with open(cache_path, "r", encoding="utf-8") as f:
                saved = json.load(f)

        output = fake_stdout.getvalue()
        self.assertEqual([c["title"] for c in citations], ["Fresh-A", "Fresh-B", "Fresh-C"])
        self.assertFalse(saved["complete"])
        self.assertEqual(saved["num_citations_on_scholar"], 98)
        self.assertEqual(saved["num_citations_cached"], 3)
        self.assertEqual(saved["num_citations_seen"], 3)
        self.assertEqual(
            saved["direct_fetch_diagnostics"],
            {
                "mode": "direct",
                "reported_total": 98,
                "yielded_total": 3,
                "seen_total": 3,
                "dedup_count": 0,
                "underfetched": True,
                "underfetch_gap": 95,
                "termination_reason": "iterator_exhausted",
            },
        )
        self.assertIn("Direct fetch under-fetched", output)
        self.assertNotIn("Direct fetch: reached target", output)

    def test_direct_fetch_logs_materialized_totals_separately(self):
        fetched_items = [
            {"bib": {"title": "Fresh-A", "author": ["A"], "venue": "V", "pub_year": "2024"}, "pub_url": "new-a", "cites_id": "cid-a"},
            {"bib": {"title": "Fresh-B", "author": ["B"], "venue": "V2", "pub_year": "2025"}, "pub_url": "new-b", "cites_id": "cid-b"},
            {"bib": {"title": "Fresh-C", "author": ["C"], "venue": "V3", "pub_year": "2026"}, "pub_url": "new-c", "cites_id": "cid-c"},
        ]
        old_citations = [
            {"title": "Old-1", "authors": "A", "venue": "V", "year": "2020", "url": "old-1", "cites_id": "old-1"},
            {"title": "Old-2", "authors": "B", "venue": "V", "year": "2021", "url": "old-2", "cites_id": "old-2"},
            {"title": "Old-3", "authors": "C", "venue": "V", "year": "2022", "url": "old-3", "cites_id": "old-3"},
            {"title": "Old-4", "authors": "D", "venue": "V", "year": "2023", "url": "old-4", "cites_id": "old-4"},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = os.path.join(tmpdir, "paper.json")
            with patch.object(scholar_citation.scholarly, "citedby", return_value=iter(fetched_items)), \
                 patch("sys.stdout", new_callable=StringIO) as fake_stdout:
                self.fetcher._fetch_citations_with_progress(
                    citedby_url="/scholar?cites=123",
                    cache_path=cache_path,
                    title="Paper",
                    num_citations=48,
                    pub_url="https://example.com/paper",
                    pub_year="2024",
                    resume_from=old_citations,
                    completed_years_in_current_run=[],
                    prev_scholar_count=48,
                )

        output = fake_stdout.getvalue()
        self.assertIn("Cache totals: cached_total=7", output)
        self.assertIn("Direct fetch totals: reported_total=48, yielded_total=3, seen_total=3, materialized_total=7, materialized_seen_total=7", output)

    def test_direct_fetch_recheck_does_not_early_stop(self):
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
                    num_citations=2,
                    pub_url="https://example.com/paper",
                    pub_year="2024",
                    resume_from=[],
                    completed_years_in_current_run=[],
                    prev_scholar_count=1,
                    force_year_rebuild=True,
                )

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
            self.assertEqual(pub["num_citations"], 2)
            with open(cache_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["num_citations_on_scholar"], 2)
            self.assertEqual(saved["citation_count_summary"]["scholar_total"], 2)

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



if __name__ == '__main__':
    unittest.main()
