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

class MainLoopTests(FetcherTestCase):
    def test_run_main_loop_retry_restores_year_fetch_diagnostics_from_latest_cache(self):
        pub = {
            "no": 1,
            "title": "Year Resume Paper",
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
            "year_fetch_diagnostics": {
                "2024": {
                    "mode": "year",
                    "year": 2024,
                    "scholar_total": 1,
                    "cached_total": 1,
                    "seen_total": 1,
                    "dedup_count": 0,
                    "underfetched": False,
                    "underfetch_gap": 0,
                    "termination_reason": "probe_match_skip",
                },
            },
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
                    "rehydrated_year_fetch_diagnostics": kwargs["rehydrated_year_fetch_diagnostics"],
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
        self.assertIsNone(fetch_calls[0]["rehydrated_year_fetch_diagnostics"])
        self.assertEqual(fetch_calls[1]["rehydrated_year_fetch_diagnostics"][2024]["termination_reason"], "probe_match_skip")
        self.assertEqual(fetch_calls[1]["rehydrated_year_fetch_diagnostics"][2024]["seen_total"], 1)
        pub = {
            "no": 1,
            "title": "Big Paper",
            "num_citations": 80,
            "year": "2024",
            "venue": "V",
        }
        cached = {
            "citations": [
                {"title": "Cached-2024", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
                {"title": "Cached-NY", "authors": "B", "venue": "V", "year": "N/A", "url": "u2"},
            ],
            "num_citations_on_scholar": 70,
            "completed_years_in_current_run": [2024],
            "dedup_count": 0,
            "complete": False,
            "probed_year_counts": {"2024": 1},
        }
        latest_cache = {
            "citations": [
                {"title": "Cached-2025", "authors": "A", "venue": "V", "year": "2025", "url": "u3"},
                {"title": "Latest-NY", "authors": "B", "venue": "V", "year": "N/A", "url": "u4"},
            ],
            "num_citations_on_scholar": 80,
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
                    "rehydrated_year_fetch_diagnostics": kwargs["rehydrated_year_fetch_diagnostics"],
                    "direct_resume_state": kwargs.get("direct_resume_state"),
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
        # year-based fetch always drops unyeared
        self.assertEqual([c["title"] for c in fetch_calls[0]["resume_from"]], ["Cached-2024"])
        self.assertEqual([c["title"] for c in fetch_calls[1]["resume_from"]], ["Cached-2025"])
        self.assertEqual(fetch_calls[1]["completed_years_in_current_run"], [])
        self.assertEqual(fetch_calls[1]["saved_dedup_count"], 0)
        self.assertEqual(fetch_calls[1]["rehydrated_probed_year_counts"], None)
        self.assertFalse(fetch_calls[1]["rehydrated_probe_complete"])
        self.assertIsNone(fetch_calls[1]["rehydrated_year_fetch_diagnostics"])
    def test_run_main_loop_retry_does_not_restore_direct_resume_state(self):
        pub = {
            "no": 1,
            "title": "Direct Resume Paper",
            "num_citations": 25,
            "year": "2024",
            "venue": "V",
        }
        cached = {
            "citations": [{"title": "Cached-2024", "authors": "A", "venue": "V", "year": "2024", "url": "u1"}],
            "num_citations_on_scholar": 25,
            "dedup_count": 0,
            "complete": False,
        }
        latest_cache = {
            "citations": [{"title": "Cached-2025", "authors": "A", "venue": "V", "year": "2025", "url": "u2"}],
            "num_citations_on_scholar": 25,
            "dedup_count": 2,
            "complete": False,
            "direct_resume_state": {
                "mode": "direct",
                "next_index": 13,
                "source_scholar_total": 25,
                "citedby_url": "/scholar?cites=123",
            },
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
                    "saved_dedup_count": kwargs["saved_dedup_count"],
                    "direct_resume_state": kwargs.get("direct_resume_state"),
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
             patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            self.fetcher._run_main_loop(
                publications=[pub],
                cache_status=cache_status,
                url_map={pub["title"]: {"citedby_url": "/scholar?cites=123", "pub_url": "https://example.com/paper"}},
                need_fetch=[(pub, "partial", cached)],
                results=[],
                fetch_idx=0,
            )

        log_output = fake_stdout.getvalue()
        self.assertEqual(len(fetch_calls), 2)
        self.assertIsNone(fetch_calls[0]["direct_resume_state"])
        self.assertEqual(fetch_calls[1]["resume_from"], latest_cache["citations"])
        self.assertEqual(fetch_calls[1]["saved_dedup_count"], 0)
        self.assertIsNone(fetch_calls[1]["direct_resume_state"])
        self.assertIn("Retrying with 1 cached citations from previous attempt", log_output)
        self.assertNotIn("direct offset=13", log_output)

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
                "rehydrated_year_fetch_diagnostics": None,
                "action": "first fetch",
                "prev_scholar_count": 0,
                "fetch_policy": {"mode": "direct", "covered_years": 2, "avg_citations_per_year": 1.5, "pub_year": 2024, "reason": "low_average_per_year"},
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
        self.assertNotIn("Year fetch comparisons:", output)
        self.assertEqual(results[0]["citations"], final_citations)
        self.assertEqual(self.fetcher._papers_fetched_count, 0)

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
                    "rehydrated_year_fetch_diagnostics": kwargs["rehydrated_year_fetch_diagnostics"],
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
        self.assertIsNone(fetch_calls[0]["rehydrated_year_fetch_diagnostics"])

        self.assertTrue(fetch_calls[1]["allow_incremental_early_stop"])
        self.assertFalse(fetch_calls[1]["force_year_rebuild"])
        self.assertIsNone(fetch_calls[1]["selective_refresh_years"])
        self.assertEqual(fetch_calls[1]["prev_scholar_count"], 75)
        self.assertEqual(fetch_calls[1]["resume_from"], stale_retry_cache["citations"])
        self.assertEqual(fetch_calls[1]["completed_years_in_current_run"], [])
        self.assertEqual(fetch_calls[1]["saved_dedup_count"], 0)
        self.assertIsNone(fetch_calls[1]["rehydrated_probed_year_counts"])
        self.assertFalse(fetch_calls[1]["rehydrated_probe_complete"])
        self.assertIsNone(fetch_calls[1]["rehydrated_year_fetch_diagnostics"])
        self.assertEqual(mock_load_cache.call_count, 2)
        self.assertIn("Retrying with 1 cached citations from previous attempt", log_output)
        self.assertNotIn("Escalating to full revalidation", log_output)
        self.assertNotIn("Retrying escalated full revalidation with in-memory state", log_output)

        self.assertEqual(results[0]["citations"], second_result)
        self.assertEqual(self.fetcher._papers_fetched_count, 0)

    def test_run_main_loop_records_direct_underfetch_without_escalation(self):
        pub = {
            "no": 1,
            "title": "Direct Paper",
            "num_citations": 98,
            "year": "2024",
            "venue": "V",
        }
        cached = {
            "citations": [{"title": "Cached", "authors": "A", "venue": "V", "year": "2024", "url": "u0"}],
            "num_citations_on_scholar": 98,
            "complete": False,
        }
        fetched_citations = [
            {"title": "Fetched-1", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
            {"title": "Fetched-2", "authors": "B", "venue": "V", "year": "2025", "url": "u2"},
            {"title": "Fetched-3", "authors": "C", "venue": "V", "year": "2026", "url": "u3"},
        ]
        latest_cache = {
            "direct_fetch_diagnostics": {
                "mode": "direct",
                "reported_total": 98,
                "yielded_total": 77,
                "seen_total": 77,
                "dedup_count": 0,
                "underfetched": True,
                "underfetch_gap": 21,
                "termination_reason": "target_reached",
            }
        }
        results = []

        def cache_status(current_pub):
            self.assertEqual(current_pub["title"], pub["title"])
            return "partial", cached

        with patch.object(self.fetcher, "_fetch_citations_with_progress", return_value=fetched_citations) as mock_fetch, \
             patch.object(self.fetcher, "_wait_proxy_switch", return_value=None), \
             patch.object(self.fetcher, "_load_citation_cache", return_value=latest_cache) as mock_load_cache, \
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
        mock_load_cache.assert_called_once_with(pub["title"])
        self.assertIn("Direct fetch under-fetched", log_output)
        self.assertIn("Direct fetch under-fetched; recording current results", log_output)
        self.assertNotIn("Escalating to full revalidation", log_output)
        self.assertEqual(results[0]["citations"], fetched_citations)

    def test_run_main_loop_logs_direct_fetch_summary_when_present(self):
        pub = {
            "no": 1,
            "title": "Direct Summary Paper",
            "num_citations": 12,
            "year": "2024",
            "venue": "V",
        }
        cached = None
        results = []
        final_citations = [
            {"title": "Y2024-A", "authors": "A", "venue": "V1", "year": "2024", "url": "u1"},
            {"title": "Y2025-A", "authors": "B", "venue": "V2", "year": "2025", "url": "u2"},
        ]
        direct_fetch_diagnostics = self.fetcher._build_direct_fetch_diagnostics(12, 11, 1, "target_reached")

        def cache_status(current_pub):
            self.assertEqual(current_pub["title"], pub["title"])
            return "missing", cached

        def fake_fetch(*args, **kwargs):
            self.fetcher._dedup_count = 1
            self.fetcher._year_fetch_diagnostics = None
            return final_citations

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
                "rehydrated_year_fetch_diagnostics": None,
                "action": "first fetch",
                "prev_scholar_count": 0,
                "fetch_policy": {"mode": "direct", "covered_years": 2, "avg_citations_per_year": 6, "pub_year": 2024, "reason": "low_average_per_year"},
             }), \
             patch.object(self.fetcher, "_fetch_citations_with_progress", side_effect=fake_fetch), \
             patch.object(self.fetcher, "_load_citation_cache", return_value={"direct_fetch_diagnostics": direct_fetch_diagnostics}), \
             patch("scholar_citation.rand_delay", return_value=0), \
             patch("scholar_citation.time.sleep", return_value=None), \
             patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            self.fetcher._run_main_loop(
                publications=[pub],
                cache_status=cache_status,
                url_map={pub["title"]: {"citedby_url": "/scholar?cites=123", "pub_url": "https://example.com/paper"}},
                need_fetch=[(pub, "missing", cached)],
                results=results,
                fetch_idx=0,
            )

        output = fake_stdout.getvalue()
        self.assertIn("Done: 2 cached, 3 seen, 1 dupes (Scholar: 12)", output)
        self.assertIn("Direct fetch summary (reported_total=12, yielded_total=11, seen_total=12, dedup_num=1, gap=0, termination=target_reached)", output)
        self.assertNotIn("Direct fetch under-fetched; recording current results", output)
        self.assertEqual(results[0]["citations"], final_citations)

    def test_rough_mode_skips_partial_paper_with_unchanged_scholar_count(self):
        pub = {
            "no": 1,
            "title": "Partial Paper",
            "num_citations": 5,
            "year": "2024",
            "venue": "V",
        }
        cached = {
            "citations": [
                {"title": "A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
            ],
            "num_citations_on_scholar": 5,
        }
        results = []
        fetch_called = []

        def cache_status(current_pub):
            return "partial", cached

        def fake_fetch(*args, **kwargs):
            fetch_called.append(True)
            return []

        self.fetcher.fetch_mode = 'rough'
        with patch.object(self.fetcher, "_fetch_citations_with_progress", side_effect=fake_fetch), \
             patch("sys.stdout", new_callable=StringIO):
            self.fetcher._run_main_loop(
                publications=[pub],
                cache_status=cache_status,
                url_map={pub["title"]: {"citedby_url": "/scholar?cites=123", "pub_url": "u"}},
                need_fetch=[(pub, "partial", cached)],
                results=results,
                fetch_idx=0,
            )

        self.assertEqual(fetch_called, [])
        self.assertEqual(results[0]["citations"], cached["citations"])

    def test_rough_mode_fetches_paper_with_missing_cache(self):
        pub = {
            "no": 1,
            "title": "Missing Paper",
            "num_citations": 5,
            "year": "2024",
            "venue": "V",
        }
        results = []
        fetch_called = []

        def cache_status(current_pub):
            return "missing", None

        final_citations = [
            {"title": "New-A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
        ]

        def fake_fetch(*args, **kwargs):
            fetch_called.append(True)
            return final_citations

        self.fetcher.fetch_mode = 'rough'
        with patch.object(self.fetcher, "_resolve_refresh_strategy", return_value={
                "mode": "first_fetch",
                "resume_from": [],
                "completed_years_in_current_run": [],
                "partial_year_start": {},
                "saved_dedup_count": 0,
                "allow_incremental_early_stop": True,
                "force_year_rebuild": False,
                "selective_refresh_years": None,
                "rehydrated_probed_year_counts": None,
                "rehydrated_probe_complete": False,
                "rehydrated_year_fetch_diagnostics": None,
                "action": "first fetch",
                "prev_scholar_count": 0,
                "fetch_policy": {"mode": "direct", "covered_years": 1, "avg_citations_per_year": 5.0,
                                 "pub_year": 2024, "reason": "low_average_per_year"},
            }), \
             patch.object(self.fetcher, "_fetch_citations_with_progress", side_effect=fake_fetch), \
             patch("scholar_citation.rand_delay", return_value=0), \
             patch("scholar_citation.time.sleep", return_value=None), \
             patch("sys.stdout", new_callable=StringIO):
            self.fetcher._run_main_loop(
                publications=[pub],
                cache_status=cache_status,
                url_map={pub["title"]: {"citedby_url": "/scholar?cites=123", "pub_url": "u"}},
                need_fetch=[(pub, "missing", None)],
                results=results,
                fetch_idx=0,
            )

        self.assertEqual(fetch_called, [True])
        self.assertEqual(results[0]["citations"], final_citations)

    def test_force_mode_deletes_cache_before_computing_statuses(self):
        """run() in force mode deletes cache files for in-range papers before status check."""
        pub = {
            "no": 1,
            "title": "Cached Paper",
            "num_citations": 5,
            "year": "2024",
            "venue": "V",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            fetcher = self.sc.PaperCitationFetcher(
                "test-author", output_dir=tmpdir, fetch_mode='force'
            )
            cache_path = fetcher._citation_cache_path(pub["title"])
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump({"citations": [], "num_citations_on_scholar": 5}, f)
            self.assertTrue(os.path.exists(cache_path))

            profile_path = os.path.join(tmpdir, "author_test-author_profile.json")
            with open(profile_path, 'w', encoding='utf-8') as f:
                json.dump({"publications": [pub]}, f)
            pubs_dir = os.path.join(tmpdir, "scholar_cache", "author_test-author")
            os.makedirs(pubs_dir, exist_ok=True)
            with open(os.path.join(pubs_dir, "publications.json"), 'w', encoding='utf-8') as f:
                json.dump({"publications": [pub]}, f)

            loop_saw_cache_gone = []

            def fake_loop(*args, **kwargs):
                loop_saw_cache_gone.append(not os.path.exists(cache_path))

            with patch.object(fetcher, '_patch_scholarly'), \
                 patch.object(fetcher, '_run_main_loop', side_effect=fake_loop), \
                 patch.object(fetcher, '_save_output'), \
                 patch("sys.stdout", new_callable=StringIO):
                fetcher.run()

            self.assertEqual(loop_saw_cache_gone, [True])


if __name__ == '__main__':
    unittest.main()
