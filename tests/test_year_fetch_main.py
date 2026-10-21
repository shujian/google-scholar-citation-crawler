import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.conftest import (
    FetcherTestCase, scholarly_mod, _DummyNav, _DummyIterator,
    patch, StringIO, json, tempfile,
)
import types
import unittest
from code import scholar_citation


def _no_proxy_config():
    """Stub ProxyConfig so ``main()`` stays hermetic.

    Without this, ``main()`` would resolve the proxy from the machine-local
    ``crawler_config.json`` / system environment instead of test-local data.
    """
    return types.SimpleNamespace(proxy_url=None, no_proxy=None,
                                 describe=lambda: "proxy=direct (none)")

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
        from code.output_state import PaperFetchState

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
        # 自动翻页时每年只建一个迭代器；但假数据里 2018 只给 10 条而 probe 是 10 条——
        # 这里 probe=10、走完 10 条，够数 → 不触发重走。（若把假数据改短，会看到两次。）
        self.assertEqual(requests, [(2018, 0), (2018, 0)])
        # 2018 的假数据只走 10 条而 probe=37 → 触发"重走一次"，于是多一次页面保存；
        # 这里只钉"中间都是 False、收尾是 True"，不依赖具体次数。
        self.assertTrue(save_calls[-1])
        self.assertFalse(any(save_calls[:-1]))
        self.assertEqual(len(citations), 37)
        self.assertIn("Year 2018 status: year_total=10", output)
        self.assertIn("Year 2019: skip (seen=27 >= probe=27)", output)
        self.assertNotIn("Reached target (64 >= 64)", output)

    def test_main_honors_accelerate_and_wires_verbose_logging(self):
        captured = {}
        fake_args = types.SimpleNamespace(
            author="test-author",
            output_dir=None,
            limit=None,
            skip=0,
            fetch_mode='normal',
            interactive_captcha=False,
            accelerate=0.1,
            verbose=True,
        )

        class FakeAuthorFetcher:
            def __init__(self, author_id, output_dir, delay_scale=1.0):
                self.delay_scale = delay_scale
                self.profile_json = os.path.join(output_dir, "author_test-author_profile.json")

            def load_prev_profile(self):
                return None

            def run(self):
                with open(self.profile_json, "w", encoding="utf-8") as f:
                    json.dump({"total_citations": 1, "total_publications": 1,
                               "author_info": {"citedby": 1}}, f)
                return True

        class FakePaperFetcher:
            def __init__(self, *args, **kwargs):
                captured.update(kwargs)

            def _patch_scholarly(self):
                pass

            # `main()` 在抓取前先落 baseline、并据此判断"这一轮有没有活"；
            # 假 fetch 只需如实回应，别让缺方法把 main 打断。
            def write_baseline(self):
                pass

            def has_pending_work(self):
                return True

            def has_cite_format_work(self):
                return False

            def run(self):
                return True

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_args.output_dir = tmpdir
            with patch.object(scholar_citation, "parse_args", return_value=fake_args), \
                 patch.object(scholar_citation, "resolve_proxy_config",
                              return_value=_no_proxy_config()), \
                 patch.object(scholar_citation, "setup_proxy"), \
                 patch.object(scholar_citation, "setup_logging") as setup_logging, \
                 patch.object(scholar_citation, "AuthorProfileFetcher", FakeAuthorFetcher), \
                 patch.object(scholar_citation, "PaperCitationFetcher", FakePaperFetcher), \
                 patch("sys.stdout", new_callable=StringIO) as fake_stdout:
                scholar_citation.main()

        # --accelerate applies even without --interactive-captcha, with a warning.
        self.assertEqual(captured["delay_scale"], 0.1)
        self.assertEqual(captured["interactive_captcha"], False)
        # -v reaches the structured logger instead of being accepted and ignored.
        setup_logging.assert_called_once_with(verbose=True)
        self.assertIn("--accelerate 0.1 shortens every wait", fake_stdout.getvalue())

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

            def write_baseline(self):
                pass

            def has_pending_work(self):
                return True

            def has_cite_format_work(self):
                return False

            def run(self):
                print("Citation fetch done")
                return True

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_args.output_dir = tmpdir
            with patch.object(scholar_citation, "parse_args", return_value=fake_args), \
                 patch.object(scholar_citation, "resolve_proxy_config",
                              return_value=_no_proxy_config()), \
                 patch.object(scholar_citation, "setup_proxy", side_effect=lambda *a, **k: print("Proxy ready")), \
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

            def write_baseline(self):
                pass

            def has_pending_work(self):
                return False

            def has_cite_format_work(self):
                return False

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_args.output_dir = tmpdir
            with patch.object(scholar_citation, "parse_args", return_value=fake_args), \
                 patch.object(scholar_citation, "resolve_proxy_config",
                              return_value=_no_proxy_config()), \
                 patch.object(scholar_citation, "setup_proxy", side_effect=lambda *a, **k: print("Proxy ready")), \
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
        from code.output_state import PaperFetchState

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
        # The attempt finished cleanly; the *data* is short (2026 has 2 of 3) and
        # that is what is_complete() must report.
        self.assertTrue(saved["complete_fetch_attempt"])
        state = PaperFetchState.from_dict(saved)
        self.assertFalse(state.is_complete(current_scholar_total=4, pub_year="2025"))
        records = {r["year"]: r for r in state.year_records}
        self.assertEqual(records[2026]["cached_total"], 2)
        self.assertEqual(records[2026]["histogram_count"], 3)
        self.assertIn("2026(2/3)", state.year_shortfall_summary())


    def test_year_empty_page_recorded_when_probe_expects_citations(self):
        """A year whose probe says N>0 but whose page returns no rows at all must
        be recorded as empty_page; a year with no probe data stays exhausted."""
        from code.output_state import PaperFetchState

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
        from code.output_state import PaperFetchState

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
        from code.output_state import PaperFetchState

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


    def test_a_cached_row_absorbs_the_cite_data_of_its_fresh_twin(self):
        """同身份的新行不丢弃：把它的 `cite` / `cid` 并进**缓存行**。

        逐页抓 Cite 对话框是按"这一页的行"付钱的 —— 年份重扫回来条数比缓存少时，缓存行留下 ✓，
        但同一身份那一行的 `cite` 必须补进去 ✓；缓存已有更全的 `cite` 时**不降级** ✗；
        身份字段（title/authors/venue/year/url/cites_id）**一个都不碰** ✗。
        （合并的具体判据在纯函数 `_merge_cite_into()`；端到端的"缓存行不丢 + 新身份追加"由
        `test_truncated_year_scan_keeps_cached_rows` 覆盖 ✓。）
        """
        from code.citation_fetch import _merge_cite_into

        full_cite = {"mla": "m", "apa": "a", "chicago": "c", "harvard": "h", "vancouver": "v"}

        # ① 缓存缺 cite/cid → 用新的补上 ✓
        cached = {"cites_id": "cid-a", "title": "A", "url": "u-a", "year": "2025"}
        fresh = {"cites_id": "cid-a", "title": "A", "url": "u-a", "year": "2025",
                 "cid": "cidA1", "cite": {"mla": "fresh"}}
        self.assertTrue(_merge_cite_into(cached, fresh))
        self.assertEqual(cached["cite"], {"mla": "fresh"})
        self.assertEqual(cached["cid"], "cidA1")

        # ② 缓存已有 5 种格式 → 不降级 ✗（cid 也不覆盖）
        cached = {"cites_id": "cid-b", "cid": "cidB0", "cite": dict(full_cite)}
        fresh = {"cites_id": "cid-b", "cid": "cidB1", "cite": {"mla": "fresh"}}
        self.assertFalse(_merge_cite_into(cached, fresh))
        self.assertEqual(cached["cite"], full_cite)
        self.assertEqual(cached["cid"], "cidB0")

        # ③ 新的 cite 为空/更少 → 绝不清空缓存 ✓
        cached = {"cites_id": "cid-c", "cite": {"mla": "m", "apa": "a"}}
        self.assertFalse(_merge_cite_into(cached, {"cites_id": "cid-c", "cite": {}}))
        self.assertEqual(cached["cite"], {"mla": "m", "apa": "a"})
        self.assertFalse(_merge_cite_into(cached, {"cites_id": "cid-c"}))
        self.assertEqual(cached["cite"], {"mla": "m", "apa": "a"})

        # ④ 身份字段一个都不碰 ✗（哪怕新的写法不同）
        cached = {"cites_id": "cid-d", "title": "Old Title", "authors": "O A",
                  "venue": "V", "year": "2025", "url": "u-d"}
        fresh = {"cites_id": "cid-d", "title": "New Title", "authors": "N B",
                 "venue": "V2", "year": "2026", "url": "u-d2", "cite": {"mla": "x"}}
        _merge_cite_into(cached, fresh)
        self.assertEqual((cached["title"], cached["authors"], cached["venue"],
                          cached["year"], cached["url"]),
                         ("Old Title", "O A", "V", "2025", "u-d"))
        self.assertEqual(cached["cite"], {"mla": "x"})

        # ⑤ 幂等：同样的合并再做一次不改变任何东西 ✓
        snapshot = dict(cached)
        self.assertFalse(_merge_cite_into(cached, fresh))
        self.assertEqual(cached, snapshot)

    def test_a_year_that_stayed_short_is_rescanned_from_page_one(self):
        """连续 `RESCAN_AFTER_SHORT_ATTEMPTS` 次没收全 → 下一次从第 1 页重扫。

        偏移续跑是"可变列表里的位置" ✗：列表上方插入条目后从旧偏移续会掠过被挤下去的行，
        而那一年永远"差几条"、每次都从同一个偏移续 ✗。连续几次都没收全 = 偏移不再可信 →
        清零重扫一遍（按身份键去重）✓。
        """
        from code.citation_fetch import _resume_start_after_short_attempts
        from code.common import RESCAN_AFTER_SHORT_ATTEMPTS as N

        # 未到阈值 → 照旧续 ✓
        self.assertEqual(_resume_start_after_short_attempts(60, N - 1), 60)
        # 到阈值 → 从头 ✓
        self.assertEqual(_resume_start_after_short_attempts(60, N), 0)
        self.assertEqual(_resume_start_after_short_attempts(60, N + 5), 0)
        # 本来就没有偏移（或脏值）→ 0 ✓
        self.assertEqual(_resume_start_after_short_attempts(0, N + 5), 0)
        self.assertEqual(_resume_start_after_short_attempts("junk", N), 0)
        # 阈值必须是个正整数 ✓
        self.assertGreaterEqual(N, 1)

    def test_a_short_attempt_is_counted_and_a_full_one_resets_the_count(self):
        """诊断里记"连续没收全次数"：又短了 +1，收全了归零，并且能原样落盘/读回 ✓。"""
        from code.citation_cache import (build_year_fetch_diagnostics,
                                         normalize_year_fetch_diagnostics)

        diag = build_year_fetch_diagnostics(2025, 153, 146, 0, "iterator_exhausted", 2)
        self.assertEqual(diag["short_attempts"], 2)
        self.assertEqual(build_year_fetch_diagnostics(2025, 153, 146, 0, "x")["short_attempts"], 0,
                         "旧调用点（不带该参数）默认 0 ✓")
        self.assertEqual(build_year_fetch_diagnostics(2025, 153, 12, 0, "x", 7)["short_attempts"], 7)
        # 归一化（读盘路径）不得把计数器丢掉 ✗
        normalized = normalize_year_fetch_diagnostics(
            {2025: {"year": 2025, "histogram_count": 153, "cached_total": 146,
                    "dedup_count": 0, "termination_reason": "x", "short_attempts": 4}})
        self.assertEqual(normalized[2025]["short_attempts"], 4)

    def test_a_year_settles_when_the_rescan_returns_the_same_count(self):
        """重扫一遍结果没变 → 认作"网页信息与 probe 不符"，不再算"差几条"、也不再重扫。

        判据落在 `year_record_gap()`：`stalled` 且 `stalled_probe >= histogram_count`（Scholar 的
        声称没继续涨）→ gap 记 0 ✓；probe 一涨 → 恢复成普通年份、gap 照算 ✓（自愈）。
        """
        from code.citation_cache import (build_year_fetch_diagnostics, year_record_gap,
                                         year_record_effective_histogram,
                                         year_records_data_complete)

        stalled = build_year_fetch_diagnostics(2018, 153, 114, 0, "iterator_exhausted",
                                              0, True, 153)
        self.assertEqual(year_record_gap(stalled), 0, "两遍都只有 114 条 → 不是没抓全 ✓")
        # 汇总与逐年都要一致：否则论文永远 partial ✗
        # （汇总的 histogram_total 由 year_record_effective_histogram() 扣掉陈旧声称 ✓）
        summary = {"histogram_total": year_record_effective_histogram(stalled),
                   "seen_total": 114, "termination_reason": "iterator_exhausted"}
        self.assertEqual(summary["histogram_total"], 114)
        self.assertTrue(year_records_data_complete([stalled], summary))
        # 汇总仍用旧的 153 → 判不完整（说明这条修正确实是它带来的 ✓）
        self.assertFalse(year_records_data_complete(
            [stalled], {"histogram_total": 153, "seen_total": 114,
                        "termination_reason": "iterator_exhausted"}))

        # Scholar 的声称涨了 → 重新算缺口（自愈）✓
        grown = build_year_fetch_diagnostics(2018, 160, 114, 0, "iterator_exhausted", 0, True, 153)
        self.assertEqual(year_record_gap(grown), 46)
        # 没判死的年份照常算 ✓
        plain = build_year_fetch_diagnostics(2018, 153, 114, 0, "iterator_exhausted")
        self.assertEqual(year_record_gap(plain), 39)

        # 落盘往返不得丢掉判死标记 ✗
        from code.citation_cache import normalize_year_fetch_diagnostics
        back = normalize_year_fetch_diagnostics(
            {2018: {"year": 2018, "histogram_count": 153, "cached_total": 114, "dedup_count": 0,
                    "termination_reason": "iterator_exhausted", "stalled": True,
                    "stalled_probe": 153}})
        self.assertTrue(back[2018]["stalled"])
        self.assertEqual(back[2018]["stalled_probe"], 153)
        self.assertEqual(year_record_gap(back[2018]), 0)

    def test_the_short_and_stalled_flags_survive_the_year_record_rebuild(self):
        """落盘路径的守卫：`build_year_records()` 重建每条记录时必须把三个字段带上 ✗ 否则判死标记一落盘就丢。"""
        import inspect
        from code import citation_fetch as _cf_mod
        source = inspect.getsource(_cf_mod)
        for needle in ("existing.get('short_attempts', 0)",
                       "existing.get('stalled', False)",
                       "existing.get('stalled_probe', 0)"):
            self.assertIn(needle, source)
        # 汇总也必须用"有效声称"，否则已判死的年份会把论文永远钉在 partial ✗
        self.assertIn("_year_effective_histogram(_record)", source)

    def test_probed_year_with_no_rows_keeps_paper_incomplete(self):
        """A probed year that came back with no rows must stay visible.

        Dropping its record also drops its probe count from the aggregate, which
        made the paper look complete while a whole year was still missing — so
        it was never re-fetched.
        """
        from code.output_state import PaperFetchState

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


class PlanUsesTheFreshProbeCountsTests(FetcherTestCase):
    """计划必须用**探针刚写回**的计数，而不是函数开头抓到的旧快照。

    `_fetch_by_year()` 开头抓一次 `year_ctx.probed_year_counts`（那时可能还是上一轮留下的
    `{}`），探针把新计数写进同一个 `year_ctx` —— 若计划仍用开头那份空快照，
    `cache_year_floor()` 就退回"取缓存最早年份"，paper 2 的计划又变成 1907–2026。
    """

    def _run(self, counts):
        requests = []

        class FakeIterator:
            def __init__(self, nav, url):
                year = int(url.split("as_ylo=")[1].split("&")[0])
                requests.append(year)
                self.index = 0
                self._finished_current_page = False
                self._items_in_current_page = 0

            def __iter__(self):
                return self

            def __next__(self):
                raise StopIteration

        def fake_probe(citedby_url, fetch_ctx=None, num_citations=None, pub_year=None):
            self.fetcher._probed_year_counts = counts
            if fetch_ctx is not None:
                fetch_ctx.probed_year_counts = counts
                fetch_ctx.probed_year_count_complete = True
            return 2017

        self.fetcher._probe_citation_start_year = fake_probe
        self.fetcher._probed_year_counts = None      # 探针之前的旧快照：空的
        cached = [{"title": f"Old-{y}", "authors": "A", "venue": "V", "year": str(y),
                   "url": f"u{y}"} for y in (1907,) + tuple(range(2017, 2027))]
        with patch.object(scholar_citation, "_SearchScholarIterator", FakeIterator), \
             patch.object(scholar_citation, "datetime") as fake_datetime, \
             patch("sys.stdout", new_callable=StringIO) as out:
            fake_datetime.now.return_value = types.SimpleNamespace(year=2026)
            self.fetcher._fetch_citations_with_progress(
                citedby_url="/scholar?cites=123",
                cache_path=None,
                title="Paper",
                num_citations=1011,
                pub_url="https://example.com/paper",
                pub_year="2017",
                resume_from=cached,
                completed_years_in_current_run=[],
                prev_scholar_count=1011,
                fetch_policy={"strategy": "year", "pub_year": 2017,
                              "reason": "at_or_above_year_threshold"},
            )
        return requests, out.getvalue()

    def test_a_stray_1907_does_not_pull_the_plan_back(self):
        requests, log = self._run({2017: 15, 2018: 153, 2019: 173, 2020: 141,
                                   2021: 140, 2022: 114, 2023: 82, 2024: 78,
                                   2025: 56, 2026: 49})
        self.assertIn("Year-based plan: 2017-2026", log)
        self.assertFalse([y for y in requests if y < 2017],
                         f"计划里不该出现 1908 之类的年份，实际请求：{sorted(set(requests))}")

    def test_the_stray_year_is_reported(self):
        _requests, log = self._run({2026: 49})
        self.assertIn("outside the probe's range", log)
        self.assertIn("1907", log)


class ShortWalkRetryTests(FetcherTestCase):
    """走完却明显少于应有条数 → 整段重走一次，只一次。

    例如 2021 年第 1 页只返回 3 行、且没有翻页链接，而这一年缓存 220 条、probe 222 ——
    同一 URL 稍后取是 10 行 + 有 next，说明那次拿到的是一份被截断的响应。
    """

    def _walk(self, rows_per_attempt, min_rows):
        from code.fetch_session import BatchFetchSession
        batch = BatchFetchSession(_url="/scholar?as_ylo=2021&as_yhi=2021&cites=1")
        calls = []

        class Iter:
            def __init__(self, rows):
                self.rows = rows
                self.index = 0
                self._finished_current_page = False
                self._items_in_current_page = len(rows)

            def __iter__(self):
                return self

            def __next__(self):
                if self.index >= len(self.rows):
                    raise StopIteration
                item = self.rows[self.index]
                self.index += 1
                return item

        iters = [Iter(rows) for rows in rows_per_attempt]

        def make_iterator(fetcher=None):
            calls.append(1)
            return iters[min(len(calls) - 1, len(iters) - 1)]

        batch._make_iterator = make_iterator
        batch.run(self.fetcher, min_rows=min_rows)
        return batch, calls

    def _rows(self, n):
        return [{"bib": {"title": f"T{i}", "author": ["A"], "venue": "V", "pub_year": "2021"},
                 "pub_url": f"u{i}", "cites_id": f"c{i}"} for i in range(n)]

    def test_a_short_walk_is_retried_once(self):
        batch, calls = self._walk([self._rows(3), self._rows(3)], min_rows=220)
        self.assertEqual(len(calls), 2, "走完只 3 行（应有 220）→ 再走一次")
        self.assertTrue(batch.short_walk_retried)
        self.assertEqual(len(batch.citations), 3, "两次一样 → 到此为止，不叠加")

    def test_the_retry_uses_the_better_result(self):
        batch, calls = self._walk([self._rows(3), self._rows(10)], min_rows=220)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(batch.citations), 10, "第二次有内容就用第二次")

    def test_a_complete_walk_is_not_retried(self):
        batch, calls = self._walk([self._rows(10)], min_rows=10)
        self.assertEqual(len(calls), 1)
        self.assertFalse(batch.short_walk_retried)
        self.assertEqual(len(batch.citations), 10)

    def test_without_min_rows_nothing_is_retried(self):
        batch, calls = self._walk([self._rows(3)], min_rows=None)
        self.assertEqual(len(calls), 1, "直接模式不传 min_rows（短页是正常结束）")
        self.assertFalse(batch.short_walk_retried)


class CacheYearFloorTests(unittest.TestCase):
    """计划起点只按**探针覆盖到**的缓存年份放宽。

    paper 2 的缓存里混着 `1907`（以及 2050 / 2090），`min(cache)` 会把计划拉成 1907–2026
    共 120 年，前面 109 年每年一次 `as_ylo=1908` 请求、全部零行。直方图（2017–2026）里
    没有那些年份 → probe=0 → 放宽没有收益，只会烧请求。
    """

    PROBE = {2017: 15, 2018: 153, 2026: 49}

    def test_a_junk_old_year_does_not_widen_the_plan(self):
        from code.citation_fetch import cache_year_floor
        cache = {1907: 1, 2019: 172, 2026: 48}
        self.assertEqual(cache_year_floor(cache, self.PROBE), 2019)

    def test_years_beyond_the_probe_are_ignored_too(self):
        from code.citation_fetch import cache_year_floor
        cache = {2050: 1, 2090: 1}
        self.assertIsNone(cache_year_floor(cache, self.PROBE))

    def test_without_a_probe_the_old_behaviour_stands(self):
        from code.citation_fetch import cache_year_floor
        self.assertEqual(cache_year_floor({1907: 1, 2019: 3}, {}), 1907)

    def test_empty_cache_has_no_floor(self):
        from code.citation_fetch import cache_year_floor
        self.assertIsNone(cache_year_floor({}, self.PROBE))

    def test_stray_years_are_reported(self):
        from code.citation_fetch import out_of_range_cache_years
        cache = {1907: 1, 2019: 5, 2050: 1, 2090: 1}
        self.assertEqual(out_of_range_cache_years(cache, self.PROBE), [1907, 2050, 2090])
        self.assertEqual(out_of_range_cache_years({2019: 1}, self.PROBE), [])
        self.assertEqual(out_of_range_cache_years(cache, {}), [])

    def test_the_plan_range_comes_out_as_the_probe_range(self):
        """把事故现场拼起来：缓存 1907… 但计划应只剩探针覆盖的 2017–2026。"""
        from code.citation_fetch import cache_year_floor
        cache = {1907: 1, 2017: 15, 2018: 151, 2026: 48, 2050: 1, 2090: 1}
        floor = cache_year_floor(cache, self.PROBE)
        self.assertEqual(floor, 2017)
        self.assertEqual(2026 - floor + 1, 10, "10 年，而不是 120 年")


if __name__ == '__main__':
    unittest.main()
