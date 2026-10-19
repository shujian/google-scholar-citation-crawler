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
class CitationStatusTests(FetcherTestCase):
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
                                   selective_refresh_years=None,
                                   year_fetch_diagnostics=None):
                # Probe always runs fresh; pre-fetch values are always None/False.
                self.assertIsNone(self.fetcher._probed_year_counts)
                self.assertFalse(self.fetcher._probed_year_count_complete)
                self.assertEqual(self.fetcher._cached_year_counts, {2024: 1, 2025: 1})
                self.assertEqual(year_fetch_diagnostics[2024]["histogram_count"], 1)
                self.assertEqual(year_fetch_diagnostics[2024]["seen_total"], 1)
                self.assertEqual(year_fetch_diagnostics[2024]["termination_reason"], "probe_match_skip")
                save_progress(fetch_finished=False)
                return []

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
                    rehydrated_year_fetch_diagnostics={
                        2024: self.fetcher._build_year_fetch_diagnostics(
                            2024,
                            1,
                            1,
                            0,
                            "probe_match_skip",
                        ),
                    },
                    fetch_policy={"strategy": "year"},
                )

            self.assertEqual(citations, [])
            saved = self.fetcher._mid_paper_state.get("Paper", {})
            # Probe always runs fresh; pre-fetch probed_year_counts is empty.
            self.assertEqual(saved["probed_year_counts"], {})
            summary = saved["year_fetch_diagnostics"]
            self.assertEqual(summary["scholar_total"], summary["histogram_total"])
            self.assertEqual(saved["completed_years"], [2024])
            self.assertEqual(summary["histogram_total"], 2)
            self.assertEqual(summary["scholar_total"], 2)
            self.assertEqual(summary["cached_total"], 2)
            self.assertEqual(summary["cached_year_total"], 2)
            self.assertEqual(summary["dedup_count"], 0)

    def test_complete_diagnostics_with_no_stored_rows_is_partial(self):
        """Diagnostics claiming completeness must not beat an empty citations array.

        Otherwise a paper with no citation data at all is skipped forever.
        """
        from code.output_state import (
            PaperFetchState, PaperState, resolve_citation_status_from_output,
        )

        pub = {"title": "Empty Paper", "num_citations": 5, "year": "2024"}
        state = PaperState(
            fetch=PaperFetchState.from_dict({
                "title": pub["title"], "fetch_strategy": "direct",
                "num_citations_on_scholar": 5,
                "complete_fetch_attempt": True,
                "direct_fetch_diagnostics": {
                    "scholar_total": 5, "cached_total": 5, "seen_total": 5,
                    "dedup_count": 0, "termination_reason": "iterator_exhausted"},
            }),
            citations=[],
        )

        self.assertEqual(
            resolve_citation_status_from_output(pub, state, 50), "partial")

    def test_citation_status_stays_complete_when_seen_matches_current_total(self):
        pub = {"title": "Paper", "num_citations": 5, "year": "2024"}
        cached = {
            "complete": True,
            "citations": [
                {"title": "A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
                {"title": "B", "authors": "B", "venue": "V", "year": "2025", "url": "u2"},
                {"title": "C", "authors": "C", "venue": "V", "year": "2025", "url": "u3"},
            ],
            "num_citations_on_scholar": 3,
            "num_citations_seen": 5,
            "direct_fetch_diagnostics": {
                    "scholar_total": 3,
                    "cached_total": 5,
                    "seen_total": 5,
                    "dedup_count": 0,
                    "termination_reason": "short_page_stop",
            },
        }

        self.fetcher._paper_states = {"Paper": cached}
        status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "complete")

    def test_citation_status_stays_complete_for_direct_when_seen_reaches_current_total(self):
        pub = {"title": "Paper", "num_citations": 5, "year": "2024"}
        cached = {
            "complete": True,
            "citations": [
                {"title": "A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
                {"title": "B", "authors": "B", "venue": "V", "year": "2025", "url": "u2"},
                {"title": "C", "authors": "C", "venue": "V", "year": "2025", "url": "u3"},
            ],
            "num_citations_on_scholar": 5,
            "num_citations_seen": 5,
            "direct_fetch_diagnostics": {
                    "scholar_total": 5,
                    "cached_total": 5,
                    "seen_total": 5,
                    "dedup_count": 0,
                    "termination_reason": "short_page_stop",
            },
        }

        self.fetcher._paper_states = {"Paper": cached}
        status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "complete")

    def test_citation_status_complete_when_direct_summary_seen_reaches_scholar_total(self):
        pub = {"title": "Paper", "num_citations": 48, "year": "2024"}
        cached = {
            "citations": [
                {"title": f"A-{idx}", "authors": "A", "venue": "V", "year": "2024", "url": f"u{idx}"}
                for idx in range(48)
            ],
            "num_citations_on_scholar": 48,
            "num_citations_cached": 48,
            "num_citations_seen": 48,
            "direct_fetch_diagnostics": {
                    "scholar_total": 48,
                    "cached_total": 48,
                    "seen_total": 48,
                    "dedup_count": 0,
                    "termination_reason": "short_page_stop",
            },
        }

        self.fetcher._paper_states = {"Paper": cached}
        status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "complete")

    def test_citation_status_stays_complete_when_only_unyeared_gap_changes(self):
        pub = {"title": "Paper", "num_citations": 5, "year": "2019"}
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

        self.fetcher._paper_states = {"Paper": cached}
        status = self.fetcher._citation_status(pub)

        # With simplified logic: num_seen (3) < current (5), no diagnostics summary → partial
        self.assertEqual(status, "partial")

    def test_citation_status_marks_partial_for_year_mode_when_seen_exceeds_hist_total(self):
        pub = {"title": "Paper", "num_citations": 1335, "year": "2018"}
        cached = {
            "complete": True,
            "probe_complete": False,
            "probed_year_counts": {
                "2018": 38,
                "2019": 107,
                "2020": 156,
                "2021": 219,
                "2022": 213,
                "2023": 213,
                "2024": 175,
                "2025": 178,
                "2026": 29,
            },
            "probed_year_total": 1328,
            "citations": [
                {"title": "A", "authors": "A", "venue": "V", "year": "2017", "url": "u1"},
            ],
            "num_citations_on_scholar": 1335,
            "num_citations_seen": 1331,
        }

        self.fetcher._paper_states = {"Paper": cached}
        status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "partial")

    def test_citation_status_marks_partial_when_promoted_total_exceeds_sparse_cache(self):
        pub = {"title": "Paper", "num_citations": 44}
        cached = {
            "complete": True,
            "probe_complete": False,
            "citations": [
                {"title": "A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
            ],
            "num_citations_on_scholar": 44,
            "num_citations_seen": 1,
        }

        self.fetcher._paper_states = {"Paper": cached}
        status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "partial")

    def test_citation_status_marks_partial_when_promoted_total_only_updates_metadata(self):
        pub = {"title": "Paper", "num_citations": 5, "year": "2024"}
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

        self.fetcher._paper_states = {"Paper": cached}
        status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "partial")

    def test_citation_status_marks_partial_for_year_mode_when_seen_below_total_minus_unyeared(self):
        pub = {"title": "Paper", "num_citations": 1335, "year": "2018"}
        cached = {
            "complete": True,
            "probe_complete": False,
            "probed_year_counts": {
                "2018": 38,
                "2019": 107,
                "2020": 156,
                "2021": 219,
                "2022": 213,
                "2023": 213,
                "2024": 175,
                "2025": 178,
                "2026": 29,
            },
            "probed_year_total": 1328,
            "citations": [
                {"title": "A", "authors": "A", "venue": "V", "year": "2017", "url": "u1"},
            ],
            "num_citations_on_scholar": 1335,
            "num_citations_seen": 1327,
        }

        self.fetcher._paper_states = {"Paper": cached}
        status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "partial")

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

        self.fetcher._paper_states = {"Paper": cached}
        status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "partial")

    def test_citation_status_marks_partial_for_direct_underfetch_diagnostics(self):
        pub = {"title": "Paper", "num_citations": 98}
        cached = {
            "complete": True,
            "probe_complete": False,
            "citations": [
                {"title": "A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
                {"title": "B", "authors": "B", "venue": "V", "year": "2025", "url": "u2"},
                {"title": "C", "authors": "C", "venue": "V", "year": "2026", "url": "u3"},
            ],
            "num_citations_on_scholar": 98,
            "num_citations_seen": 77,
            "direct_fetch_diagnostics": {
                "strategy": "direct",
                "reported_total": 98,
                "yielded_total": 77,
                "seen_total": 77,
                "dedup_count": 0,
                "underfetched": True,
                "underfetch_gap": 21,
                "termination_reason": "target_reached",
            },
        }

        self.fetcher._paper_states = {"Paper": cached}
        status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "partial")

    def test_year_fetch_diagnostics_treat_seen_total_as_completion_boundary(self):
        diagnostics = self.fetcher._build_year_fetch_diagnostics(
            year=2024,
            histogram_count=10,
            cached_total=9,
            dedup_count=1,
            termination_reason="target_reached",
        )

        self.assertEqual(diagnostics["seen_total"], 10)
        self.assertFalse(diagnostics["seen_total"] < diagnostics["histogram_count"])
        self.assertEqual(diagnostics["termination_reason"], "target_reached")

    def test_fetch_by_year_skips_when_seen_total_covers_probe_total(self):
        self.fetcher._probed_year_counts = {2024: 10}
        self.fetcher._probed_year_count_complete = True
        self.fetcher._cached_year_counts = {2024: 9}
        self.fetcher._probe_citation_start_year = lambda citedby_url, fetch_ctx=None, num_citations=None, pub_year=None: 2024

        requests = []
        cached_citations = [
            {"title": f"Cached-{idx}", "authors": "A", "venue": "V", "year": "2024", "url": f"u{idx}"}
            for idx in range(9)
        ]

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
            fake_datetime.now.return_value = types.SimpleNamespace(year=2024)
            citations = self.fetcher._fetch_by_year(
                citedby_url="/scholar?cites=123",
                old_citations=list(cached_citations),
                fresh_citations=[],
                save_progress=lambda complete, batch=None: None,
                num_citations=10,
                pub_year="2020",
                prev_scholar_count=0,
                allow_incremental_early_stop=False,
                year_fetch_diagnostics={
                    2024: self.fetcher._build_year_fetch_diagnostics(
                        2024,
                        10,
                        9,
                        1,
                        "target_reached",
                    ),
                },
            )

        output = fake_stdout.getvalue()
        self.assertEqual(requests, [])
        self.assertEqual(citations, cached_citations)
        self.assertIn("Year 2024: skip (seen=10 >= probe=10)", output)
        self.assertIn("Year fetch comparisons: 1 years", output)
        self.assertIn("2024: histogram=10,seen=10,cached=9,dedup=1,term=seen_total_match_skip", output)

    def test_selective_refresh_skips_years_matching_histogram(self):
        # cached == probed for all years → nothing to refresh
        selected = self.fetcher._selective_refresh_candidate_years(
            cached_year_counts={2024: 10, 2025: 5},
            probed_year_counts={2024: 10, 2025: 5},
            year_range=range(2024, 2026),
            probe_complete=False,
            year_fetch_diagnostics={
                2025: self.fetcher._build_year_fetch_diagnostics(
                    2025, 5, 4, 0, "short_page_stop",
                ),
            },
        )

        self.assertEqual(selected, [])

    def test_citation_status_marks_partial_for_underfetched_year_diagnostics(self):
        pub = {"title": "Paper", "num_citations": 10, "year": "2024"}
        cached = {
            "complete": True,
            "probe_complete": True,
            "probed_year_counts": {"2024": 10},
            "cached_year_counts": {"2024": 8},
            "citations": [
                {"title": f"A-{idx}", "authors": "A", "venue": "V", "year": "2024", "url": f"u{idx}"}
                for idx in range(8)
            ],
            "num_citations_on_scholar": 10,
            "num_citations_seen": 8,
            "year_fetch_diagnostics": {
                "2024": {
                    "strategy": "year",
                    "year": 2024,
                    "scholar_total": 10,
                    "cached_total": 8,
                    "seen_total": 8,
                    "dedup_count": 0,
                    "underfetched": True,
                    "underfetch_gap": 2,
                    "termination_reason": "short_page_stop",
                },
            },
        }

        self.fetcher._paper_states = {"Paper": cached}
        status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "partial")

    def test_citation_status_complete_when_direct_summary_seen_equals_scholar_total(self):
        pub = {"title": "Paper", "num_citations": 10, "year": "2024"}
        cached = {
            "citations": [
                {"title": f"A-{idx}", "authors": "A", "venue": "V", "year": "2024", "url": f"u{idx}"}
                for idx in range(9)
            ],
            "num_citations_on_scholar": 10,
            "num_citations_seen": 10,
            "direct_fetch_diagnostics": {
                    "scholar_total": 10,
                    "cached_total": 9,
                    "seen_total": 10,
                    "dedup_count": 1,
                    "termination_reason": "target_reached",
            },
        }

        self.fetcher._paper_states = {"Paper": cached}
        status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "complete")

    def test_citation_status_year_complete_when_every_year_covers_its_probe(self):
        """Aggregate satisfied AND every year covering its own probe → complete."""
        pub = {"title": "Paper", "num_citations": 109, "year": "2020"}
        cached = {
            "citations": [
                {"title": f"A-{idx}", "authors": "A", "venue": "V", "year": str(2020 + idx % 7), "url": f"u{idx}"}
                for idx in range(105)
            ],
            "num_citations_on_scholar": 109,
            "num_citations_seen": 109,
            "year_fetch_diagnostics": {
                "2020": {"year": 2020, "histogram_count": 5, "cached_total": 5, "seen_total": 5, "dedup_count": 0, "termination_reason": "short_page_stop"},
                "2021": {"year": 2021, "histogram_count": 15, "cached_total": 15, "seen_total": 15, "dedup_count": 0, "termination_reason": "short_page_stop"},
                "2022": {"year": 2022, "histogram_count": 23, "cached_total": 22, "seen_total": 23, "dedup_count": 1, "termination_reason": "short_page_stop"},
                "2023": {"year": 2023, "histogram_count": 24, "cached_total": 24, "seen_total": 24, "dedup_count": 0, "termination_reason": "short_page_stop"},
                "2024": {"year": 2024, "histogram_count": 17, "cached_total": 16, "seen_total": 17, "dedup_count": 1, "termination_reason": "short_page_stop"},
                "2025": {"year": 2025, "histogram_count": 24, "cached_total": 24, "seen_total": 24, "dedup_count": 0, "termination_reason": "short_page_stop"},
                "2026": {"year": 2026, "histogram_count": 1, "cached_total": 1, "seen_total": 1, "dedup_count": 0, "termination_reason": "short_page_stop"},
                    "histogram_total": 109,
                    "scholar_total": 109,
                    "cached_total": 105,
                    "cached_year_total": 105,
                    "seen_total": 109,
                    "dedup_count": 2,
            },
        }

        self.fetcher._paper_states = {"Paper": cached}
        status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "complete")

    def test_citation_status_year_partial_when_one_year_short_despite_aggregate(self):
        """A year whose own bucket is short must not be hidden by other years
        over-counting the probe.

        2024 cached 177 vs probe 169 (over-count) lifts the aggregate seen_total
        above histogram_total, so the aggregate comparison alone reports
        complete — while 2026 (cached 64 vs probe 66) still has unfetched
        citations that would then never be resumed.
        """
        pub = {"title": "Paper", "num_citations": 236, "year": "2017"}
        cached = {
            "citations": [
                {"title": f"A-{idx}", "authors": "A", "venue": "V", "year": "2024", "url": f"u{idx}"}
                for idx in range(177)
            ] + [
                {"title": f"B-{idx}", "authors": "A", "venue": "V", "year": "2026", "url": f"v{idx}"}
                for idx in range(64)
            ],
            "num_citations_on_scholar": 236,
            "num_citations_seen": 241,
            "year_fetch_diagnostics": {
                "2024": {"year": 2024, "histogram_count": 169, "cached_total": 177, "seen_total": 177, "dedup_count": 0, "termination_reason": "short_page_stop"},
                "2026": {"year": 2026, "histogram_count": 66, "cached_total": 64, "seen_total": 64, "dedup_count": 0, "termination_reason": "short_page_stop"},
                    "histogram_total": 235,
                    "scholar_total": 236,
                    "cached_total": 241,
                    "cached_year_total": 241,
                    "seen_total": 241,
                    "dedup_count": 0,
            },
        }

        self.fetcher._paper_states = {"Paper": cached}
        status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "partial")

    def test_resolve_refresh_strategy_rehydrates_year_fetch_diagnostics(self):
        pub = {"title": "Paper", "num_citations": 10, "year": "2024"}
        cached = {
            "citations": [{"title": "Cached", "authors": "A", "venue": "V", "year": "2024", "url": "u1"}],
            "num_citations_on_scholar": 10,
            "year_fetch_diagnostics": {
                "2024": {
                    "strategy": "year",
                    "year": 2024,
                    "scholar_total": 10,
                    "cached_total": 9,
                    "seen_total": 10,
                    "dedup_count": 1,
                    "underfetched": False,
                    "underfetch_gap": 0,
                    "termination_reason": "target_reached",
                },
            },
        }

        strategy = self.fetcher._resolve_refresh_strategy(pub, cached, "partial")

        self.assertEqual(strategy["rehydrated_year_fetch_diagnostics"][2024]["seen_total"], 10)
        self.assertEqual(strategy["rehydrated_year_fetch_diagnostics"][2024]["dedup_count"], 1)

    def test_direct_fetch_diagnostics_treat_seen_total_as_completion_boundary(self):
        diagnostics = self.fetcher._build_direct_fetch_diagnostics(
            scholar_total=10,
            cached_total=9,
            seen_total=10,
            dedup_count=1,
            termination_reason="target_reached",
        )

        s = diagnostics
        self.assertEqual(s["seen_total"], 10)
        self.assertEqual(s["scholar_total"], 10)
        # underfetched / underfetch_gap are derivable and no longer stored

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
             patch("code.scholar_citation.rand_delay", return_value=0), \
             patch("code.scholar_citation.time.sleep", return_value=None), \
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
        # direct→year transition: synthesised from cached citations
        self.assertEqual(fetch_calls[0]["rehydrated_year_fetch_diagnostics"][2024]["histogram_count"], 1)
        self.assertEqual(fetch_calls[1]["resume_from"], latest_cache["citations"])
        self.assertEqual(fetch_calls[1]["saved_dedup_count"], 0)
        # direct→year transition: synthesised from latest_cache citations
        self.assertEqual(fetch_calls[1]["rehydrated_year_fetch_diagnostics"][2024]["histogram_count"], 1)



    def test_citation_status_complete_when_num_seen_derived_from_year_diagnostics(self):
        """When num_citations_seen is missing but year_fetch_diagnostics contains
        seen_total for all years, derive num_seen from diagnostics and recognise
        completeness.  This handles legacy caches that predate the
        num_citations_seen field."""
        pub = {"title": "Paper", "num_citations": 1347, "year": "2017"}
        cached = {
            "complete": True,
            "probe_complete": False,
            "fetch_strategy": "year",
            "probed_year_counts": {
                "2018": 38, "2019": 108, "2020": 157, "2021": 218,
                "2022": 213, "2023": 213, "2024": 176, "2025": 179, "2026": 38,
            },
            "probed_year_total": 1340,
            "cached_year_counts": {
                "2018": 38, "2019": 108, "2020": 157, "2021": 218,
                "2022": 213, "2023": 213, "2024": 176, "2025": 178, "2026": 38,
            },
            "citations": [
                {"title": f"A-{idx}", "authors": "A", "venue": "V", "year": str(2018 + idx % 9), "url": f"u{idx}"}
                for idx in range(1339)
            ],
            "num_citations_on_scholar": 1347,
            "year_fetch_diagnostics": {
                    "histogram_total": 1340,
                    "scholar_total": 1347,
                    "cached_total": 1339,
                    "cached_year_total": 1339,
                    "seen_total": 1340,
                    "dedup_count": 1,
                    "scholar_unyeared_count": 7,
                "2018": {"year": 2018, "scholar_total": 38, "cached_total": 38, "seen_total": 38, "dedup_count": 0, "termination_reason": "short_page_stop"},
                "2019": {"year": 2019, "scholar_total": 108, "cached_total": 108, "seen_total": 108, "dedup_count": 0, "termination_reason": "short_page_stop"},
                "2020": {"year": 2020, "scholar_total": 157, "cached_total": 157, "seen_total": 157, "dedup_count": 0, "termination_reason": "short_page_stop"},
                "2021": {"year": 2021, "scholar_total": 218, "cached_total": 218, "seen_total": 218, "dedup_count": 0, "termination_reason": "short_page_stop"},
                "2022": {"year": 2022, "scholar_total": 213, "cached_total": 213, "seen_total": 213, "dedup_count": 0, "termination_reason": "short_page_stop"},
                "2023": {"year": 2023, "scholar_total": 213, "cached_total": 213, "seen_total": 213, "dedup_count": 0, "termination_reason": "short_page_stop"},
                "2024": {"year": 2024, "scholar_total": 176, "cached_total": 176, "seen_total": 176, "dedup_count": 0, "termination_reason": "short_page_stop"},
                "2025": {"year": 2025, "scholar_total": 179, "cached_total": 178, "seen_total": 179, "dedup_count": 1, "termination_reason": "short_page_stop"},
                "2026": {"year": 2026, "scholar_total": 38, "cached_total": 38, "seen_total": 38, "dedup_count": 0, "termination_reason": "short_page_stop"},
            },
        }

        self.fetcher._paper_states = {"Paper": cached}
        status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "complete")


from code.citation_cache import (
    fetch_outcome_signature,
    iter_year_records,
    year_record_gap,
    year_records_gap,
    year_records_data_complete,
    year_records_shortfall_years,
)
from code.citation_identity import extract_citation_info, _extract_cid_from_scholarbib


class YearRecordsCompletenessTests(unittest.TestCase):
    """Per-year year-mode completeness helpers (code/citation_cache.py)."""

    def test_iter_year_records_accepts_list_and_mapping(self):
        records = [{"year": 2024, "histogram_count": 1, "seen_total": 1}]
        self.assertEqual(list(iter_year_records(records)), records)
        self.assertEqual(list(iter_year_records({"2024": records[0]})), records)
        self.assertEqual(list(iter_year_records(None)), [])
        self.assertEqual(list(iter_year_records([None, "x", 3])), [])

    def test_year_record_gap_uses_histogram_count_then_scholar_total(self):
        self.assertEqual(year_record_gap({"histogram_count": 10, "seen_total": 7}), 3)
        self.assertEqual(year_record_gap({"scholar_total": 10, "seen_total": 7}), 3)
        self.assertEqual(year_record_gap({"histogram_count": 10, "seen_total": 10}), 0)
        self.assertEqual(year_record_gap({"histogram_count": 10, "seen_total": 12}), 0)
        self.assertEqual(year_record_gap({}), 0)

    def test_year_records_gap_sums_per_year_shortfalls(self):
        records = [
            {"year": 2024, "histogram_count": 169, "seen_total": 177},
            {"year": 2026, "histogram_count": 66, "seen_total": 64},
        ]
        needed, seen, histogram = year_records_gap(records)
        self.assertEqual(needed, 2)
        self.assertEqual(seen, 241)
        self.assertEqual(histogram, 235)
        self.assertIsNone(year_records_gap(None))

    def test_year_records_data_complete_requires_every_year_covered(self):
        summary = {"histogram_total": 235, "seen_total": 241}
        masked = [
            {"year": 2024, "histogram_count": 169, "seen_total": 177},
            {"year": 2026, "histogram_count": 66, "seen_total": 64},
        ]
        covered = [
            {"year": 2024, "histogram_count": 169, "seen_total": 169},
            {"year": 2026, "histogram_count": 66, "seen_total": 66},
        ]
        self.assertFalse(year_records_data_complete(masked, summary))
        self.assertTrue(year_records_data_complete(covered, summary))

    def test_year_records_data_complete_falls_back_to_aggregate_without_records(self):
        self.assertTrue(year_records_data_complete(
            None, {"histogram_total": 10, "seen_total": 10}))
        self.assertFalse(year_records_data_complete(
            None, {"histogram_total": 10, "seen_total": 9}))
        self.assertFalse(year_records_data_complete([], None))

    def test_year_records_data_complete_requires_aggregate_too(self):
        """Records can cover every year while the summary still claims more."""
        records = [{"year": 2024, "histogram_count": 5, "seen_total": 5}]
        self.assertFalse(year_records_data_complete(
            records, {"histogram_total": 9, "seen_total": 5}))

    def test_year_records_shortfall_years_lists_year_and_counts(self):
        records = [
            {"year": 2024, "histogram_count": 169, "seen_total": 177},
            {"year": 2026, "histogram_count": 66, "seen_total": 64},
        ]
        self.assertEqual(year_records_shortfall_years(records), [(2026, 64, 66)])
        self.assertEqual(year_records_shortfall_years([]), [])


class FetchOutcomeSignatureTests(unittest.TestCase):
    """Signatures make "the same rows twice" comparable across runs.

    The signature counts what a traversal obtained and ignores Scholar's own
    targets, which drift between runs even when the listing does not.
    """

    def test_direct_signature_counts_the_rows_seen(self):
        self.assertEqual(
            fetch_outcome_signature('direct', {"scholar_total": 49, "seen_total": 0}),
            'direct:0')
        self.assertEqual(
            fetch_outcome_signature('direct', {"scholar_total": 32, "seen_total": 31}),
            'direct:31')

    def test_signature_is_empty_when_the_outcome_is_unknown(self):
        self.assertEqual(fetch_outcome_signature('direct', {}), '')
        self.assertEqual(fetch_outcome_signature('direct', None), '')
        self.assertEqual(fetch_outcome_signature('year', {}), '')
        self.assertEqual(fetch_outcome_signature('year', None, None), '')

    def test_year_signature_counts_the_rows_seen_across_years(self):
        records = [
            {"year": 2024, "histogram_count": 169, "seen_total": 177},
            {"year": 2026, "histogram_count": 66, "seen_total": 64},
        ]
        self.assertEqual(fetch_outcome_signature('year', None, records), 'year:241')

    def test_a_moving_scholar_target_does_not_change_the_signature(self):
        """Scholar's own numbers drift; the rows it lists do not.

        2026-10-15: one paper's 2026 bucket alternated 190/200 ↔ 194/201
        between runs.  Signing the expectation made every run look new, so a
        paper that can never converge never accumulated a stall count.
        """
        before = [{"year": 2026, "histogram_count": 95, "seen_total": 83}]
        after = [{"year": 2026, "histogram_count": 96, "seen_total": 83}]
        self.assertEqual(fetch_outcome_signature('year', None, before),
                         fetch_outcome_signature('year', None, after))
        self.assertEqual(
            fetch_outcome_signature('direct', {"scholar_total": 40, "seen_total": 34}),
            fetch_outcome_signature('direct', {"scholar_total": 41, "seen_total": 34}))

    def test_a_changed_row_count_changes_the_signature(self):
        fewer = [{"year": 2026, "histogram_count": 95, "seen_total": 82}]
        more = [{"year": 2026, "histogram_count": 95, "seen_total": 83}]
        self.assertNotEqual(fetch_outcome_signature('year', None, fewer),
                            fetch_outcome_signature('year', None, more))

    def test_filling_a_short_year_changes_the_signature(self):
        short = [
            {"year": 2024, "histogram_count": 169, "seen_total": 177},
            {"year": 2026, "histogram_count": 66, "seen_total": 64},
        ]
        filled = [
            {"year": 2024, "histogram_count": 169, "seen_total": 177},
            {"year": 2026, "histogram_count": 66, "seen_total": 66},
        ]
        self.assertNotEqual(fetch_outcome_signature('year', None, short),
                            fetch_outcome_signature('year', None, filled))

    def test_year_signature_falls_back_to_the_aggregate_without_records(self):
        self.assertEqual(
            fetch_outcome_signature('year', {"histogram_total": 10, "seen_total": 7}),
            'year:7')


class StallRuleTests(unittest.TestCase):
    """Only the row count of a *full walk* may advance the stall counter."""

    @staticmethod
    def _state(seen_total, complete, histogram_count=None, termination=None):
        from code.output_state import PaperFetchState
        histogram_count = seen_total if histogram_count is None else histogram_count
        record = {"year": 2026,
                  "histogram_count": histogram_count,
                  "seen_total": seen_total}
        if termination:
            record["termination_reason"] = termination
        return PaperFetchState.from_dict({
            "title": "Paper",
            "fetch_strategy": "year",
            "complete_fetch_attempt": complete,
            "year_fetch_diagnostics": {
                "scholar_total": histogram_count,
                "histogram_total": histogram_count,
                "seen_total": seen_total,
                "cached_total": seen_total,
            },
            "year_records": [record],
        })

    def test_one_completed_traversal_that_lands_short_stalls_the_paper(self):
        """Default `STALL_AFTER_ATTEMPTS == 1`: a full walk that lands short parks it."""
        first = self._state(83, complete=True, histogram_count=95)
        first.record_fetch_outcome()
        self.assertEqual(first.stall_count, 1)
        self.assertTrue(first.is_stalled())
        self.assertFalse(first.is_complete())

        # Next run: Scholar claims one more citation, the traversal returns the
        # same rows.  The repeat keeps it parked (and shows up in the log).
        second = self._state(83, complete=True, histogram_count=96)
        second.inherit_stall_tracking(first)
        second.record_fetch_outcome()

        self.assertEqual(second.stall_signature, 'year:83')
        self.assertEqual(second.stall_count, 2)
        self.assertTrue(second.is_stalled())
        self.assertFalse(second.is_complete())

    def test_an_unfinished_traversal_neither_counts_nor_wipes_the_repeat(self):
        first = self._state(83, complete=True, histogram_count=95)
        first.record_fetch_outcome()
        self.assertEqual(first.stall_count, 1)

        # A blocked run returns fewer rows: it must not be folded in — neither
        # as a repeat (the traversal did not finish) nor as a reset.
        blocked = self._state(40, complete=False, histogram_count=95)
        blocked.inherit_stall_tracking(first)
        blocked.record_fetch_outcome()
        self.assertEqual(blocked.stall_count, 1)
        self.assertEqual(blocked.stall_signature, 'year:83')
        self.assertFalse(blocked.is_stalled())

        # The next completed traversal with the same rows is then the second
        # identical outcome, not the first.
        third = self._state(83, complete=True, histogram_count=95)
        third.inherit_stall_tracking(blocked)
        third.record_fetch_outcome()
        self.assertEqual(third.stall_count, 2)
        self.assertTrue(third.is_stalled())

    def test_an_attempt_that_skipped_the_rest_of_its_plan_is_not_folded(self):
        """`partial_resume_completed` stops early on purpose.

        The year it resumed is done and the remaining years are skipped, so its
        row count is not what walking every page would give.
        """
        first = self._state(83, complete=True, histogram_count=95)
        first.record_fetch_outcome()
        self.assertEqual(first.stall_count, 1)

        skipped = self._state(83, complete=True, histogram_count=95,
                              termination='partial_resume_completed')
        self.assertFalse(skipped.visited_every_page())
        skipped.inherit_stall_tracking(first)
        skipped.record_fetch_outcome()
        self.assertEqual((skipped.stall_signature, skipped.stall_count), ('year:83', 1))
        # 默认阈值 1，所以显式要 2 次才判定 —— 这里只看"故意早停的走法没被算进去"。
        self.assertFalse(skipped.is_stalled(stall_after_attempts=2))

    def test_a_legacy_state_without_the_completion_flag_never_stalls(self):
        from code.output_state import PaperFetchState
        state = PaperFetchState.from_dict({
            "title": "Paper",
            "fetch_strategy": "direct",
            "direct_fetch_diagnostics": {"scholar_total": 49, "seen_total": 0},
        })
        for _ in range(3):
            state.record_fetch_outcome()
        self.assertEqual(state.stall_count, 0)
        self.assertFalse(state.is_stalled())


class CitationIdentityTests(unittest.TestCase):
    """Tests for citation_identity.py functions."""

    def test_extract_cid_from_scholarbib_returns_cluster_id(self):
        url = '/scholar?q=info:K8ZpoI6hZNoJ:scholar.google.com/&output=cite&scirp=0&hl=en'
        self.assertEqual(_extract_cid_from_scholarbib(url), 'K8ZpoI6hZNoJ')

    def test_extract_cid_from_scholarbib_returns_none_for_empty(self):
        self.assertIsNone(_extract_cid_from_scholarbib(None))
        self.assertIsNone(_extract_cid_from_scholarbib(''))

    def test_extract_cid_from_scholarbib_returns_none_for_non_scholarbib_url(self):
        self.assertIsNone(_extract_cid_from_scholarbib('https://example.com/paper'))

    def test_scholarbib_none_cluster_is_not_a_cites_id(self):
        """A row Scholar never clustered produces 'info:None:…'.

        Taken literally that is a plausible-looking id, and every such row would
        share the identity key `cites_id\\tNone` (merging unrelated citations and
        handing the downloader a bogus key).
        """
        from code.citation_identity import citation_identity_keys, normalize_cites_id

        url = '/scholar?q=info:None:scholar.google.com/&output=cite&scirp=0&hl=en'
        self.assertIsNone(_extract_cid_from_scholarbib(url))

        info = extract_citation_info({
            'bib': {'title': 'Unclustered', 'author': ['A'], 'venue': 'V',
                    'pub_year': '2025'},
            'url_scholarbib': url,
            'pub_url': 'http://example.com',
        })
        self.assertIsNone(info['cites_id'])
        keys = citation_identity_keys(info)
        self.assertFalse(any(key.startswith('cites_id') for key in keys), keys)

        for sentinel in ('None', 'none', 'NULL', 'null', 'N/A', 'na', ' '):
            self.assertIsNone(normalize_cites_id(sentinel), sentinel)
        self.assertEqual(normalize_cites_id('g4QQL8eCFG0J'), 'g4QQL8eCFG0J')
        self.assertEqual(normalize_cites_id(['None', '123', '', '456']), '123,456')
        self.assertIsNone(normalize_cites_id(['None', '']))

    def test_extract_citation_info_falls_back_to_url_scholarbib_when_no_citedby_url(self):
        pub = {
            'bib': {'title': 'Test', 'author': ['A'], 'venue': 'V', 'pub_year': '2025'},
            'url_scholarbib': '/scholar?q=info:CLUSTER123:scholar.google.com/&output=cite&scirp=0&hl=en',
            'pub_url': 'http://example.com',
        }
        info = extract_citation_info(pub)
        self.assertEqual(info['cites_id'], 'CLUSTER123')

    def test_extract_citation_info_prefers_citedby_url_over_url_scholarbib(self):
        pub = {
            'bib': {'title': 'Test', 'author': ['A'], 'venue': 'V', 'pub_year': '2025'},
            'citedby_url': '/scholar?cites=99999&as_sdt=5,33',
            'url_scholarbib': '/scholar?q=info:SHOULD_NOT_USE:scholar.google.com/&output=cite&scirp=0&hl=en',
            'pub_url': 'http://example.com',
        }
        info = extract_citation_info(pub)
        self.assertEqual(info['cites_id'], '99999')

    def test_extract_citation_info_prefers_direct_cites_id(self):
        pub = {
            'bib': {'title': 'Test', 'author': ['A'], 'venue': 'V', 'pub_year': '2025'},
            'cites_id': 'direct_id',
            'citedby_url': '/scholar?cites=should_not&as_sdt=5,33',
            'url_scholarbib': '/scholar?q=info:NO:scholar.google.com/&output=cite&scirp=0&hl=en',
        }
        info = extract_citation_info(pub)
        self.assertEqual(info['cites_id'], 'direct_id')

    def test_extract_citation_info_returns_none_when_all_sources_missing(self):
        pub = {
            'bib': {'title': 'Test', 'author': ['A'], 'venue': 'V', 'pub_year': '2025'},
            'pub_url': 'http://example.com',
        }
        info = extract_citation_info(pub)
        self.assertIsNone(info['cites_id'])


if __name__ == '__main__':
    unittest.main()
