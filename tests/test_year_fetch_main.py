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

class YearFetchMainTests(FetcherTestCase):
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

    def test_interrupted_year_persists_its_resume_offset(self):
        """The in-year offset must be written with the state, or the next run
        restarts the year at item 0 and re-walks everything already fetched."""
        from crawler.output_state import PaperFetchState

        class InterruptAfterFirstPage:
            def __init__(self, nav, url):
                self.index = 0
                self._finished_current_page = False
                self._items_in_current_page = 0

            def __iter__(self):
                return self

            def __next__(self):
                if self.index >= 10:
                    raise KeyboardInterrupt()
                self.index += 1
                self._items_in_current_page = self.index
                self._finished_current_page = self.index == 10
                return {"bib": {"title": f"New-{self.index}", "author": ["A"],
                                "venue": "V", "pub_year": "2025"},
                        "pub_url": f"u{self.index}", "cites_id": f"cid-{self.index}"}

        def fake_probe(citedby_url, fetch_ctx=None, num_citations=None, pub_year=None):
            counts = {2025: 30}
            self.fetcher._probed_year_counts = counts
            if fetch_ctx is not None:
                fetch_ctx.probed_year_counts = counts
                fetch_ctx.probed_year_count_complete = True
            return 2025

        self.fetcher._probe_citation_start_year = fake_probe
        with patch.object(scholar_citation, "_SearchScholarIterator", InterruptAfterFirstPage), \
             patch.object(scholar_citation, "datetime") as fake_datetime, \
             patch("sys.stdout", new_callable=StringIO):
            fake_datetime.now.return_value = types.SimpleNamespace(year=2025)
            with self.assertRaises(KeyboardInterrupt):
                self.fetcher._fetch_citations_with_progress(
                    citedby_url="/scholar?cites=123",
                    cache_path=None,
                    title="Paper",
                    num_citations=30,
                    pub_url="https://example.com/paper",
                    pub_year="2025",
                    resume_from=[],
                    completed_years_in_current_run=[],
                    prev_scholar_count=0,
                    fetch_policy={"strategy": "year", "pub_year": 2025,
                                  "reason": "at_or_above_year_threshold"},
                )

        saved = self.fetcher._mid_paper_state["Paper"]
        self.assertEqual(saved["partial_year_start"], {"2025": 10})
        self.assertEqual(
            PaperFetchState.from_dict(saved).partial_year_start, {2025: 10})

    def test_completed_years_skip_that_year_in_the_same_run(self):
        """A year finished earlier in the same run must not be walked again.

        The completed years are handed to _fetch_citations_with_progress, but the
        inner per-year context seeds itself from
        fetcher._completed_year_segments; without publishing them there, a
        same-run retry re-fetches every year from scratch.
        """
        requests = []
        pages = {
            (2026, 0): [
                {"bib": {"title": "New-2026", "author": ["A"], "venue": "V",
                         "pub_year": "2026"}, "pub_url": "u-new", "cites_id": "cid-new"},
            ],
        }

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

        def fake_probe(citedby_url, fetch_ctx=None, num_citations=None, pub_year=None):
            counts = {2026: 1}
            self.fetcher._probed_year_counts = counts
            if fetch_ctx is not None:
                fetch_ctx.probed_year_counts = counts
                fetch_ctx.probed_year_count_complete = True
            return 2026

        self.fetcher._probe_citation_start_year = fake_probe
        with patch.object(scholar_citation, "_SearchScholarIterator", FakeIterator), \
             patch.object(scholar_citation, "datetime") as fake_datetime, \
             patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            fake_datetime.now.return_value = types.SimpleNamespace(year=2026)
            self.fetcher._fetch_citations_with_progress(
                citedby_url="/scholar?cites=123",
                cache_path=None,
                title="Paper",
                num_citations=1,
                pub_url="https://example.com/paper",
                pub_year="2026",
                resume_from=[],
                completed_years_in_current_run=[2026],
                prev_scholar_count=0,
                fetch_policy={"strategy": "year", "pub_year": 2026,
                              "reason": "at_or_above_year_threshold"},
            )

        output = fake_stdout.getvalue()
        self.assertEqual(requests, [])
        self.assertIn("Year 2026: skip (already completed earlier in this run)", output)
        saved = self.fetcher._mid_paper_state["Paper"]
        self.assertEqual(saved["citations"], [])

    def test_years_absent_from_an_authoritative_histogram_are_skipped(self):
        def fake_probe(citedby_url, fetch_ctx=None, num_citations=None, pub_year=None):
            counts = {2024: 1}
            self.fetcher._probed_year_counts = counts
            if fetch_ctx is not None:
                fetch_ctx.probed_year_counts = counts
                fetch_ctx.probed_year_count_complete = True
            return 2024

        self.fetcher._probe_citation_start_year = fake_probe
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
                save_progress=lambda complete, batch=None: None,
                num_citations=2,
                pub_year="2025",
                prev_scholar_count=1,
            )

        # 2025 is absent from an authoritative histogram + has cache → skipped.
        # 2026 is absent from the histogram + no cache → fetched (conservative).
        self.assertEqual(requests, [(2024, 0), (2026, 0)])
        self.assertEqual([c["title"] for c in citations], ["Cached-2025", "New-2024"])

    def test_years_without_any_histogram_are_fetched_when_cache_exists(self):
        """No per-year counts at all → "not in the histogram" proves nothing.

        A failed probe leaves probed_year_counts empty; skipping cached years
        then silently stops fetching them, so newly added rows in those years
        would never be collected.
        """
        self.fetcher._probe_citation_start_year = lambda citedby_url, fetch_ctx=None, num_citations=None, pub_year=None: 2024
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

        with patch.object(scholar_citation, "_SearchScholarIterator", FakeIterator), \
             patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            citations = self.fetcher._fetch_by_year(
                citedby_url="/scholar?cites=123",
                old_citations=old_citations,
                fresh_citations=[],
                save_progress=lambda complete, batch=None: None,
                num_citations=2,
                pub_year="2025",
                prev_scholar_count=1,
            )

        output = fake_stdout.getvalue()
        # 2025 has cached data but the probe produced no histogram at all → fetch.
        self.assertEqual(requests, [(2024, 0), (2025, 0), (2026, 0)])
        self.assertEqual(sorted(c["title"] for c in citations),
                         ["Cached-2025", "New-2024"])
        self.assertIn("no histogram probe", output)


    def test_force_year_rebuild_uses_replaced_year_totals_for_status_and_stop(self):
        self.fetcher._probed_year_counts = {2018: 37, 2019: 27}
        self.fetcher._probed_year_count_complete = True
        self.fetcher._cached_year_counts = {2018: 36, 2019: 27}
        self.fetcher._probe_citation_start_year = lambda citedby_url, fetch_ctx=None, num_citations=None, pub_year=None: 2018

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
                    self.items = ([
                        {
                            "bib": {"title": f"Old-2018-{i}", "author": ["A"], "venue": "V2018", "pub_year": "2018"},
                            "pub_url": f"u2018-{i}",
                        }
                        for i in range(10)
                    ] if start == 0 else [])
                else:
                    self.items = ([
                        {
                            "bib": {"title": f"Fetched-2019-{i}", "author": ["B"], "venue": "V2019", "pub_year": "2019"},
                            "pub_url": f"uf2019-{i}",
                        }
                        for i in range(27)
                    ] if start == 0 else [])
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
                save_progress=lambda complete, batch=None: save_calls.append(complete),
                num_citations=64,
                pub_year="2018",
                prev_scholar_count=64,
                allow_incremental_early_stop=False,
                force_year_rebuild=True,
                selective_refresh_years={2018, 2019},

            )

        output = fake_stdout.getvalue()
        # With automatic pagination only one iterator is created per year.
        self.assertEqual(requests, [(2018, 0)])
        self.assertEqual(save_calls, [False, False, True])
        self.assertEqual(len(citations), 37)
        self.assertIn("Year 2018 status: year_total=10", output)
        self.assertIn("Year 2019: skip (seen=27 >= probe=27)", output)
        self.assertNotIn("Reached target (64 >= 64)", output)

    def test_main_mirrors_stdout_to_timestamped_log_file(self):
        fake_args = types.SimpleNamespace(
            author="test-author",
            output_dir=None,
            limit=None,
            skip=0,
            fetch_mode='normal',
            interactive_captcha=False,
            accelerate=1.0,
        )

        class FakeAuthorFetcher:
            def __init__(self, author_id, output_dir, delay_scale=1.0):
                self.output_dir = output_dir
                self.profile_json = os.path.join(output_dir, "author_test-author_profile.json")

            def load_prev_profile(self):
                return None

            def run(self):
                with open(self.profile_json, "w", encoding="utf-8") as f:
                    json.dump({"total_citations": 1, "total_publications": 1, "author_info": {"citedby": 1}}, f)
                print("Profile fetch done")
                return True

        class FakePaperFetcher:
            def __init__(self, *args, **kwargs):
                pass

            def _patch_scholarly(self):
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
        prev_profile = types.SimpleNamespace(
            total_citations=5,
            total_publications=2,
        )
        curr_profile = types.SimpleNamespace(
            total_citations=5,
            total_publications=2,
        )
        fake_args = types.SimpleNamespace(
            author="test-author",
            output_dir=None,
            limit=None,
            skip=0,
            fetch_mode='normal',
            interactive_captcha=False,
            accelerate=1.0,
        )

        class FakeAuthorFetcher:
            def __init__(self, author_id, output_dir, delay_scale=1.0):
                self.calls = 0

            def load_prev_profile(self):
                self.calls += 1
                return prev_profile if self.calls == 1 else curr_profile

            def run(self):
                print("Profile fetch done")
                return True

        class FakePaperFetcher:
            def __init__(self, *args, **kwargs):
                pass

            def _patch_scholarly(self):
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
        self.fetcher._probe_citation_start_year = lambda citedby_url, fetch_ctx=None, num_citations=None, pub_year=None: 2024
        self.fetcher._probed_year_counts = {2024: 1}
        self.fetcher._probed_year_count_complete = True
        self.fetcher._cached_year_counts = {}

        class FakeIterator:
            def __init__(self, nav, url):
                start = 0
                if "start=" in url:
                    start = int(url.split("start=")[1].split("&")[0])
                self.items = ([{
                    "bib": {"title": "Fetched-2024", "author": ["A"], "venue": "V2024"},
                    "pub_url": "u2024",
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
             patch.object(scholar_citation, "datetime") as fake_datetime:
            fake_datetime.now.return_value = types.SimpleNamespace(year=2024)
            citations = self.fetcher._fetch_by_year(
                citedby_url="/scholar?cites=123",
                old_citations=[],
                fresh_citations=[],
                save_progress=lambda complete, batch=None: None,
                num_citations=1,
                pub_year="2024",
                prev_scholar_count=0,
                allow_incremental_early_stop=False,
            )

        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0]["year"], "2024")
        self.assertEqual(self.fetcher._year_count_map(citations), {2024: 1})

    def test_year_fetch_keeps_explicit_pub_year_from_citation(self):
        self.fetcher._probe_citation_start_year = lambda citedby_url, fetch_ctx=None, num_citations=None, pub_year=None: 2024
        self.fetcher._probed_year_counts = {2024: 1}
        self.fetcher._probed_year_count_complete = True
        self.fetcher._cached_year_counts = {}

        class FakeIterator:
            def __init__(self, nav, url):
                start = 0
                if "start=" in url:
                    start = int(url.split("start=")[1].split("&")[0])
                self.items = ([{
                    "bib": {"title": "Fetched-2023", "author": ["A"], "venue": "V2023", "pub_year": "2023"},
                    "pub_url": "u2023",
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
             patch.object(scholar_citation, "datetime") as fake_datetime:
            fake_datetime.now.return_value = types.SimpleNamespace(year=2024)
            citations = self.fetcher._fetch_by_year(
                citedby_url="/scholar?cites=123",
                old_citations=[],
                fresh_citations=[],
                save_progress=lambda complete, batch=None: None,
                num_citations=1,
                pub_year="2024",
                prev_scholar_count=0,
                allow_incremental_early_stop=False,
            )

        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0]["year"], "2023")
        self.assertEqual(self.fetcher._year_count_map(citations), {2023: 1})

    def test_year_fetch_records_incomplete_when_one_year_ends_short(self):
        """The saved complete_fetch_attempt must use the per-year rule, so it
        cannot claim complete for a state is_complete() calls partial: 2025
        over-counts its probe (2 cached vs 1 probed) while 2026 stays short
        (2 cached vs 3 probed), leaving the aggregate satisfied (4 >= 4)."""
        from crawler.output_state import PaperFetchState

        pages = {
            (2025, 0): [
                {"bib": {"title": "A-2025", "author": ["A"], "venue": "V", "pub_year": "2025"}, "pub_url": "u1", "cites_id": "cid1"},
                {"bib": {"title": "B-2025", "author": ["B"], "venue": "V", "pub_year": "2025"}, "pub_url": "u2", "cites_id": "cid2"},
            ],
            (2026, 0): [
                {"bib": {"title": "A-2026", "author": ["A"], "venue": "V", "pub_year": "2026"}, "pub_url": "u3", "cites_id": "cid3"},
                {"bib": {"title": "B-2026", "author": ["B"], "venue": "V", "pub_year": "2026"}, "pub_url": "u4", "cites_id": "cid4"},
            ],
        }

        class FakeIterator:
            def __init__(self, nav, url):
                start = 0
                if "start=" in url:
                    start = int(url.split("start=")[1].split("&")[0])
                year = int(url.split("as_ylo=")[1].split("&")[0])
                self.items = list(pages.get((year, start), []))
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

        def fake_probe(citedby_url, fetch_ctx=None, num_citations=None, pub_year=None):
            counts = {2025: 1, 2026: 3}
            self.fetcher._probed_year_counts = counts
            if fetch_ctx is not None:
                fetch_ctx.probed_year_counts = counts
                fetch_ctx.probed_year_count_complete = True
            return 2025

        self.fetcher._probe_citation_start_year = fake_probe
        with patch.object(scholar_citation, "_SearchScholarIterator", FakeIterator), \
             patch("sys.stdout", new_callable=StringIO):
            self.fetcher._fetch_citations_with_progress(
                citedby_url="/scholar?cites=123",
                cache_path=None,
                title="Paper",
                num_citations=4,
                pub_url="https://example.com/paper",
                pub_year="2025",
                resume_from=[],
                completed_years_in_current_run=[],
                prev_scholar_count=0,
                fetch_policy={"strategy": "year", "pub_year": 2025, "reason": "at_or_above_year_threshold"},
            )

        saved = self.fetcher._mid_paper_state.get("Paper", {})
        self.assertFalse(saved["complete_fetch_attempt"])
        state = PaperFetchState.from_dict(saved)
        self.assertFalse(state.is_complete(current_scholar_total=4, pub_year="2025"))
        records = {r["year"]: r for r in state.year_records}
        self.assertEqual(records[2026]["cached_total"], 2)
        self.assertEqual(records[2026]["histogram_count"], 3)
        self.assertIn("2026(2/3)", state.year_shortfall_summary())


    def test_year_empty_page_recorded_when_probe_expects_citations(self):
        """A year whose probe says N>0 but whose page returns no rows at all must
        be recorded as empty_page; a year with no probe data stays exhausted."""
        from crawler.output_state import PaperFetchState

        pages = {}  # every year query comes back with no rows

        class FakeIterator:
            def __init__(self, nav, url):
                start = 0
                if "start=" in url:
                    start = int(url.split("start=")[1].split("&")[0])
                year = int(url.split("as_ylo=")[1].split("&")[0])
                self.items = list(pages.get((year, start), []))
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

        def fake_probe(citedby_url, fetch_ctx=None, num_citations=None, pub_year=None):
            counts = {2025: 2}
            self.fetcher._probed_year_counts = counts
            if fetch_ctx is not None:
                fetch_ctx.probed_year_counts = counts
                fetch_ctx.probed_year_count_complete = True
            return 2025

        self.fetcher._probe_citation_start_year = fake_probe
        with patch.object(scholar_citation, "_SearchScholarIterator", FakeIterator), \
             patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            self.fetcher._fetch_citations_with_progress(
                citedby_url="/scholar?cites=123",
                cache_path=None,
                title="Paper",
                num_citations=2,
                pub_url="https://example.com/paper",
                pub_year="2025",
                resume_from=[],
                completed_years_in_current_run=[],
                prev_scholar_count=0,
                fetch_policy={"strategy": "year", "pub_year": 2025,
                              "reason": "at_or_above_year_threshold"},
            )

        output = fake_stdout.getvalue()
        saved = self.fetcher._mid_paper_state.get("Paper", {})
        records = {r["year"]: r for r in PaperFetchState.from_dict(saved).year_records}
        # 2025 is probed (2 expected) so its empty page must survive in the state.
        self.assertEqual(records[2025]["termination_reason"], "empty_page")
        self.assertEqual(records[2025]["histogram_count"], 2)
        self.assertEqual(records[2025]["seen_total"], 0)
        # 2026 has neither probe data nor rows — nothing to record about it.
        self.assertNotIn(2026, records)
        self.assertIn("Year 2025: page returned 0 rows but the probe says 2", output)
        self.assertIn("recording termination as empty_page", output)

    def test_empty_page_keeps_cached_year_citations(self):
        """An empty page must not delete the citations already cached for a year.

        A blocked/consent/interstitial response is HTTP 200 with no rows.
        Treating it as an authoritative rebuild replaced the year's bucket with
        an empty list, deleting real citations (and, once stall detection kicks
        in, for good).
        """
        from crawler.output_state import PaperFetchState

        pages = {}  # every year query comes back with no rows

        class FakeIterator:
            def __init__(self, nav, url):
                start = 0
                if "start=" in url:
                    start = int(url.split("start=")[1].split("&")[0])
                year = int(url.split("as_ylo=")[1].split("&")[0])
                self.items = list(pages.get((year, start), []))
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

        def fake_probe(citedby_url, fetch_ctx=None, num_citations=None, pub_year=None):
            counts = {2025: 5}
            self.fetcher._probed_year_counts = counts
            if fetch_ctx is not None:
                fetch_ctx.probed_year_counts = counts
                fetch_ctx.probed_year_count_complete = True
            return 2025

        self.fetcher._probe_citation_start_year = fake_probe
        old_citations = [
            {"title": "A-2025", "authors": "A", "venue": "V", "year": "2025",
             "url": "u-a", "cites_id": "cid-a"},
            {"title": "B-2025", "authors": "B", "venue": "V", "year": "2025",
             "url": "u-b", "cites_id": "cid-b"},
        ]

        with patch.object(scholar_citation, "_SearchScholarIterator", FakeIterator), \
             patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            self.fetcher._fetch_citations_with_progress(
                citedby_url="/scholar?cites=123",
                cache_path=None,
                title="Paper",
                num_citations=5,
                pub_url="https://example.com/paper",
                pub_year="2025",
                resume_from=old_citations,
                completed_years_in_current_run=[],
                prev_scholar_count=2,
                fetch_policy={"strategy": "year", "pub_year": 2025,
                              "reason": "at_or_above_year_threshold"},
            )

        output = fake_stdout.getvalue()
        saved = self.fetcher._mid_paper_state["Paper"]
        self.assertEqual(sorted(c["title"] for c in saved["citations"]),
                         ["A-2025", "B-2025"])
        state = PaperFetchState.from_dict(saved)
        records = {r["year"]: r for r in state.year_records}
        self.assertEqual(records[2025]["termination_reason"], "empty_page")
        self.assertEqual(records[2025]["cached_total"], 2)
        self.assertEqual(records[2025]["seen_total"], 2)
        self.assertFalse(saved["complete_fetch_attempt"])
        self.assertFalse(state.is_complete(current_scholar_total=5, pub_year="2025"))
        self.assertIn("keeping 2 cached citation(s)", output)
        self.assertNotIn("skip (already completed earlier in this run)", output)

    def test_truncated_year_scan_keeps_cached_rows(self):
        """A full re-scan that returns fewer rows than the cache must not delete them.

        The batch is seeded empty when a year scan starts at page 0, so a
        truncated response comes back smaller than the cached bucket; the cached
        rows must be kept alongside the freshly fetched ones.
        """
        from crawler.output_state import PaperFetchState

        pages = {
            (2025, 0): [
                {"bib": {"title": "New-2025", "author": ["N"], "venue": "V",
                         "pub_year": "2025"}, "pub_url": "u-new", "cites_id": "cid-new"},
            ],
        }

        class FakeIterator:
            def __init__(self, nav, url):
                start = 0
                if "start=" in url:
                    start = int(url.split("start=")[1].split("&")[0])
                year = int(url.split("as_ylo=")[1].split("&")[0])
                self.items = list(pages.get((year, start), []))
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

        def fake_probe(citedby_url, fetch_ctx=None, num_citations=None, pub_year=None):
            counts = {2025: 3}
            self.fetcher._probed_year_counts = counts
            if fetch_ctx is not None:
                fetch_ctx.probed_year_counts = counts
                fetch_ctx.probed_year_count_complete = True
            return 2025

        self.fetcher._probe_citation_start_year = fake_probe
        old_citations = [
            {"title": "A-2025", "authors": "A", "venue": "V", "year": "2025",
             "url": "u-a", "cites_id": "cid-a"},
            {"title": "B-2025", "authors": "B", "venue": "V", "year": "2025",
             "url": "u-b", "cites_id": "cid-b"},
        ]

        with patch.object(scholar_citation, "_SearchScholarIterator", FakeIterator), \
             patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            self.fetcher._fetch_citations_with_progress(
                citedby_url="/scholar?cites=123",
                cache_path=None,
                title="Paper",
                num_citations=3,
                pub_url="https://example.com/paper",
                pub_year="2025",
                resume_from=old_citations,
                completed_years_in_current_run=[],
                prev_scholar_count=2,
                fetch_policy={"strategy": "year", "pub_year": 2025,
                              "reason": "at_or_above_year_threshold"},
            )

        output = fake_stdout.getvalue()
        saved = self.fetcher._mid_paper_state["Paper"]
        titles = sorted(c["title"] for c in saved["citations"])
        self.assertEqual(titles, ["A-2025", "B-2025", "New-2025"])
        records = {r["year"]: r for r in PaperFetchState.from_dict(saved).year_records}
        self.assertEqual(records[2025]["cached_total"], 3)
        self.assertIn("keeping the cached rows", output)


    def test_probed_year_with_no_rows_keeps_paper_incomplete(self):
        """A probed year that came back with no rows must stay visible.

        Dropping its record also drops its probe count from the aggregate, which
        made the paper look complete while a whole year was still missing — so
        it was never re-fetched.
        """
        from crawler.output_state import PaperFetchState

        pages = {
            (2024, 0): [
                {"bib": {"title": "A-2024", "author": ["A"], "venue": "V", "pub_year": "2024"}, "pub_url": "u1", "cites_id": "cid1"},
                {"bib": {"title": "B-2024", "author": ["B"], "venue": "V", "pub_year": "2024"}, "pub_url": "u2", "cites_id": "cid2"},
            ],
            # 2026 is probed with 3 citations but the page returns no rows.
        }

        class FakeIterator:
            def __init__(self, nav, url):
                start = 0
                if "start=" in url:
                    start = int(url.split("start=")[1].split("&")[0])
                year = int(url.split("as_ylo=")[1].split("&")[0])
                self.items = list(pages.get((year, start), []))
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

        def fake_probe(citedby_url, fetch_ctx=None, num_citations=None, pub_year=None):
            counts = {2024: 2, 2026: 3}
            self.fetcher._probed_year_counts = counts
            if fetch_ctx is not None:
                fetch_ctx.probed_year_counts = counts
                fetch_ctx.probed_year_count_complete = True
            return 2024

        self.fetcher._probe_citation_start_year = fake_probe
        with patch.object(scholar_citation, "_SearchScholarIterator", FakeIterator), \
             patch("sys.stdout", new_callable=StringIO):
            self.fetcher._fetch_citations_with_progress(
                citedby_url="/scholar?cites=123",
                cache_path=None,
                title="Paper",
                num_citations=5,
                pub_url="https://example.com/paper",
                pub_year="2024",
                resume_from=[],
                completed_years_in_current_run=[],
                prev_scholar_count=0,
                fetch_policy={"strategy": "year", "pub_year": 2024,
                              "reason": "at_or_above_year_threshold"},
            )

        saved = self.fetcher._mid_paper_state.get("Paper", {})
        state = PaperFetchState.from_dict(saved)
        records = {r["year"]: r for r in state.year_records}
        self.assertEqual(records[2024]["seen_total"], 2)
        self.assertEqual(records[2026]["histogram_count"], 3)
        self.assertEqual(records[2026]["seen_total"], 0)
        self.assertEqual(records[2026]["termination_reason"], "empty_page")
        # The aggregate keeps 2026's probe count, so the paper stays incomplete
        # and the empty year is retried on the next run.
        self.assertEqual(state.year_fetch_diagnostics["histogram_total"], 5)
        self.assertFalse(state.is_complete(current_scholar_total=5, pub_year="2024"))


if __name__ == '__main__':
    unittest.main()
