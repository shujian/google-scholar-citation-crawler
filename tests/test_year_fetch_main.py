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

    def test_selective_refresh_does_not_early_stop_after_refetching_cached_citation(self):
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

        self.assertEqual(requests, [(2024, 0), (2025, 0)])
        self.assertEqual([c["title"] for c in citations], ["New-2024", "Cached-2025"])
        self.assertEqual(self.fetcher._new_citations_count, 1)

        self.fetcher._completed_year_segments = {2024}
        self.fetcher._probed_year_counts = {2024: 2}
        self.fetcher._probed_year_count_complete = True
        self.fetcher._cached_year_counts = {2024: 1}
        self.fetcher._probe_citation_start_year = lambda citedby_url, fetch_ctx=None, num_citations=None, pub_year=None: 2024

        requests = []

        class FakeIterator:
            def __init__(self, nav, url):
                start = 0
                if "start=" in url:
                    start = int(url.split("start=")[1].split("&")[0])
                requests.append(url)
                self.items = ([{
                    "bib": {"title": "Fetched-2024", "author": ["A"], "venue": "V2024", "pub_year": "2024"},
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
                save_progress=lambda complete: save_calls.append(complete),
                num_citations=64,
                pub_year="2018",
                prev_scholar_count=64,
                allow_incremental_early_stop=False,
                force_year_rebuild=True,
                selective_refresh_years={2018, 2019},

            )

        output = fake_stdout.getvalue()
        self.assertEqual(requests, [(2018, 0), (2018, 10)])
        self.assertEqual(save_calls, [False, False, True])
        self.assertEqual(len(citations), 37)
        self.assertIn("Year 2018 status: paper_total=37", output)
        self.assertIn("Year 2019: skip (histogram count match; cached=27, probe=27, probe_complete=True)", output)
        self.assertNotIn("Reached target (64 >= 64)", output)

    def test_year_partial_save_uses_authoritative_replaced_totals(self):
        self.fetcher._probed_year_counts = {2024: 2}
        self.fetcher._probed_year_count_complete = True
        self.fetcher._cached_year_counts = {2024: 1}
        self.fetcher._probe_citation_start_year = lambda citedby_url, fetch_ctx=None, num_citations=None, pub_year=None: 2024

        old_citations = [
            {"title": "Old-2024", "authors": "A", "venue": "V2024", "year": "2024", "url": "u-old"},
            {"title": "Keep-2023", "authors": "B", "venue": "V2023", "year": "2023", "url": "u-keep"},
        ]
        partial_snapshots = []

        class FakeIterator:
            def __init__(self, nav, url):
                start = 0
                if "start=" in url:
                    start = int(url.split("start=")[1].split("&")[0])
                self.items = ([
                    {
                        "bib": {"title": "Fresh-2024-A", "author": ["A"], "venue": "V2024", "pub_year": "2024"},
                        "pub_url": "u-fresh-a",
                    },
                    {
                        "bib": {"title": "Fresh-2024-B", "author": ["C"], "venue": "V2024", "pub_year": "2024"},
                        "pub_url": "u-fresh-b",
                    },
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
            fetch_mode='normal',
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
            fetch_mode='normal',
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
                save_progress=lambda complete: None,
                num_citations=1,
                pub_year="2024",
                prev_scholar_count=0,
                allow_incremental_early_stop=False,
            )

        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0]["year"], "2023")
        self.assertEqual(self.fetcher._year_count_map(citations), {2023: 1})



if __name__ == '__main__':
    unittest.main()
