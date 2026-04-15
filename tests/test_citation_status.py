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
                self.assertEqual(self.fetcher._probed_year_counts, {2024: 1, 2025: 1})
                self.assertTrue(self.fetcher._probed_year_count_complete)
                self.assertEqual(self.fetcher._cached_year_counts, {2024: 1, 2025: 1})
                self.assertEqual(year_fetch_diagnostics[2024]["scholar_total"], 1)
                self.assertEqual(year_fetch_diagnostics[2024]["seen_total"], 1)
                self.assertEqual(year_fetch_diagnostics[2024]["termination_reason"], "probe_match_skip")
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
                    rehydrated_year_fetch_diagnostics={
                        2024: self.fetcher._build_year_fetch_diagnostics(
                            2024,
                            1,
                            1,
                            0,
                            "probe_match_skip",
                        ),
                    },
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

    def test_citation_status_stays_complete_when_seen_matches_current_total(self):
        pub = {"title": "Paper", "num_citations": 5, "year": "2024"}
        cached = {
            "complete": True,
            "probe_complete": False,
            "citations": [
                {"title": "A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
                {"title": "B", "authors": "B", "venue": "V", "year": "2025", "url": "u2"},
                {"title": "C", "authors": "C", "venue": "V", "year": "2025", "url": "u3"},
            ],
            "num_citations_on_scholar": 3,
            "num_citations_seen": 5,
        }

        with patch.object(self.fetcher, "_load_citation_cache", return_value=cached):
            status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "complete")

    def test_citation_status_stays_complete_for_direct_when_seen_reaches_current_total(self):
        pub = {"title": "Paper", "num_citations": 5, "year": "2024"}
        cached = {
            "complete": True,
            "probe_complete": False,
            "citations": [
                {"title": "A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
                {"title": "B", "authors": "B", "venue": "V", "year": "2025", "url": "u2"},
                {"title": "C", "authors": "C", "venue": "V", "year": "2025", "url": "u3"},
            ],
            "num_citations_on_scholar": 5,
            "num_citations_seen": 5,
        }

        with patch.object(self.fetcher, "_load_citation_cache", return_value=cached):
            status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "complete")

    def test_citation_status_ignores_direct_bad_flags_when_seen_reaches_current_total(self):
        pub = {"title": "Paper", "num_citations": 48, "year": "2024"}
        cached = {
            "complete": False,
            "probe_complete": False,
            "citations": [
                {"title": f"A-{idx}", "authors": "A", "venue": "V", "year": "2024", "url": f"u{idx}"}
                for idx in range(48)
            ],
            "num_citations_on_scholar": 48,
            "num_citations_cached": 48,
            "num_citations_seen": 48,
            "direct_fetch_diagnostics": {
                "mode": "direct",
                "reported_total": 48,
                "yielded_total": 7,
                "seen_total": 7,
                "dedup_count": 0,
                "underfetched": True,
                "underfetch_gap": 41,
                "termination_reason": "iterator_exhausted",
            },
        }

        with patch.object(self.fetcher, "_load_citation_cache", return_value=cached):
            status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "complete")

    def test_citation_status_recheck_bypasses_seen_equality_shortcut(self):
        self.fetcher.recheck_citations = True
        pub = {"title": "Paper", "num_citations": 5}
        cached = {
            "complete": True,
            "probe_complete": False,
            "citations": [
                {"title": "A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
                {"title": "B", "authors": "B", "venue": "V", "year": "2025", "url": "u2"},
                {"title": "C", "authors": "C", "venue": "V", "year": "2025", "url": "u3"},
            ],
            "num_citations_on_scholar": 3,
            "num_citations_seen": 5,
        }

        with patch.object(self.fetcher, "_load_citation_cache", return_value=cached):
            status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "partial")

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

        with patch.object(self.fetcher, "_load_citation_cache", return_value=cached):
            status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "complete")

    def test_citation_status_stays_complete_for_year_mode_when_seen_covers_total_minus_unyeared(self):
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

        with patch.object(self.fetcher, "_load_citation_cache", return_value=cached):
            status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "complete")

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

        with patch.object(self.fetcher, "_load_citation_cache", return_value=cached):
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

        with patch.object(self.fetcher, "_load_citation_cache", return_value=cached):
            status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "partial")

    def test_citation_status_legacy_cache_seen_shortcut_without_diagnostics(self):
        pub = {"title": "Paper", "num_citations": 5, "year": "2024"}
        cached = {
            "complete": False,
            "probe_complete": False,
            "citations": [
                {"title": "A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
                {"title": "B", "authors": "B", "venue": "V", "year": "2025", "url": "u2"},
                {"title": "C", "authors": "C", "venue": "V", "year": "2025", "url": "u3"},
            ],
            "num_citations_on_scholar": 3,
            "num_citations_seen": 5,
        }

        with patch.object(self.fetcher, "_load_citation_cache", return_value=cached):
            status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "complete")

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

        with patch.object(self.fetcher, "_load_citation_cache", return_value=cached):
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

        with patch.object(self.fetcher, "_load_citation_cache", return_value=cached):
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
                "mode": "direct",
                "reported_total": 98,
                "yielded_total": 77,
                "seen_total": 77,
                "dedup_count": 0,
                "underfetched": True,
                "underfetch_gap": 21,
                "termination_reason": "target_reached",
            },
        }

        with patch.object(self.fetcher, "_load_citation_cache", return_value=cached):
            status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "partial")

    def test_year_fetch_diagnostics_treat_seen_total_as_completion_boundary(self):
        diagnostics = self.fetcher._build_year_fetch_diagnostics(
            year=2024,
            scholar_total=10,
            cached_total=9,
            dedup_count=1,
            termination_reason="target_reached",
        )

        self.assertEqual(diagnostics["seen_total"], 10)
        self.assertFalse(diagnostics["underfetched"])
        self.assertEqual(diagnostics["underfetch_gap"], 0)
        self.assertEqual(diagnostics["termination_reason"], "target_reached")

    def test_fetch_by_year_skips_when_seen_total_covers_probe_total(self):
        self.fetcher._probed_year_counts = {2024: 10}
        self.fetcher._probed_year_count_complete = True
        self.fetcher._cached_year_counts = {2024: 9}
        self.fetcher._probe_citation_start_year = lambda citedby_url, num_citations=None, pub_year=None: 2024

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
                save_progress=lambda complete: None,
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
        self.assertIn("Year 2024: skip (seen total match; cached=9, seen=10, probe=10)", output)
        self.assertIn("Year fetch comparisons: 1 years [2024:scholar=10,seen=10,cached=9,dedup=1,term=seen_total_match_skip]", output)

    def test_selective_refresh_uses_underfetched_year_diagnostics(self):
        selected = self.fetcher._selective_refresh_candidate_years(
            cached_year_counts={2024: 10, 2025: 5},
            probed_year_counts={2024: 10, 2025: 5},
            year_range=range(2024, 2026),
            probe_complete=False,
            year_fetch_diagnostics={
                2025: self.fetcher._build_year_fetch_diagnostics(
                    2025,
                    5,
                    4,
                    0,
                    "short_page_stop",
                ),
            },
        )

        self.assertEqual(selected, [2025])

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
                    "mode": "year",
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

        with patch.object(self.fetcher, "_load_citation_cache", return_value=cached):
            status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "partial")

    def test_citation_status_ignores_year_bad_flags_when_histogram_is_satisfied(self):
        pub = {"title": "Paper", "num_citations": 10, "year": "2024"}
        cached = {
            "complete": False,
            "probe_complete": True,
            "probed_year_counts": {"2024": 10},
            "cached_year_counts": {"2024": 9},
            "citations": [
                {"title": f"A-{idx}", "authors": "A", "venue": "V", "year": "2024", "url": f"u{idx}"}
                for idx in range(9)
            ],
            "num_citations_on_scholar": 10,
            "num_citations_seen": 10,
            "year_fetch_diagnostics": {
                "2024": {
                    "mode": "year",
                    "year": 2024,
                    "scholar_total": 10,
                    "cached_total": 9,
                    "seen_total": 10,
                    "dedup_count": 1,
                    "underfetched": True,
                    "underfetch_gap": 1,
                    "termination_reason": "target_reached",
                },
            },
        }

        with patch.object(self.fetcher, "_load_citation_cache", return_value=cached):
            status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "complete")

    def test_citation_status_uses_current_profile_total_instead_of_cached_high_watermark(self):
        pub = {"title": "Paper", "num_citations": 109, "year": "2020"}
        cached = {
            "complete": True,
            "probe_complete": False,
            "citations": [
                {"title": f"A-{idx}", "authors": "A", "venue": "V", "year": "2020", "url": f"u{idx}"}
                for idx in range(109)
            ],
            "num_citations_on_scholar": 120,
            "num_citations_cached": 109,
            "num_citations_seen": 109,
        }

        with patch.object(self.fetcher, "_load_citation_cache", return_value=cached), \
             patch.object(scholar_citation, "datetime") as fake_datetime:
            fake_datetime.now.return_value = types.SimpleNamespace(year=2026)
            status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "complete")

    def test_citation_status_prefers_current_direct_policy_over_stale_year_probe_state(self):
        pub = {"title": "Paper", "num_citations": 109, "year": "2020"}
        cached = {
            "complete": True,
            "probe_complete": True,
            "probed_year_counts": {
                "2020": 5,
                "2021": 15,
                "2022": 23,
                "2023": 24,
                "2024": 17,
                "2025": 24,
                "2026": 1,
            },
            "cached_year_counts": {
                "2020": 5,
                "2021": 14,
                "2022": 21,
                "2023": 24,
                "2024": 17,
                "2025": 23,
                "2026": 1,
            },
            "citations": [
                {"title": "Cached", "authors": "A", "venue": "V", "year": "2020", "url": "u1"},
            ],
            "num_citations_on_scholar": 109,
            "num_citations_seen": 109,
            "year_fetch_diagnostics": {
                "2021": {
                    "mode": "year",
                    "year": 2021,
                    "scholar_total": 15,
                    "cached_total": 14,
                    "seen_total": 14,
                    "dedup_count": 0,
                    "underfetched": True,
                    "underfetch_gap": 1,
                    "termination_reason": "short_page_stop",
                },
                "2022": {
                    "mode": "year",
                    "year": 2022,
                    "scholar_total": 23,
                    "cached_total": 21,
                    "seen_total": 21,
                    "dedup_count": 0,
                    "underfetched": True,
                    "underfetch_gap": 2,
                    "termination_reason": "short_page_stop",
                },
                "2025": {
                    "mode": "year",
                    "year": 2025,
                    "scholar_total": 24,
                    "cached_total": 23,
                    "seen_total": 23,
                    "dedup_count": 0,
                    "underfetched": True,
                    "underfetch_gap": 1,
                    "termination_reason": "short_page_stop",
                },
            },
        }

        with patch.object(self.fetcher, "_load_citation_cache", return_value=cached), \
             patch.object(scholar_citation, "datetime") as fake_datetime:
            fake_datetime.now.return_value = types.SimpleNamespace(year=2026)
            status = self.fetcher._citation_status(pub)

        self.assertEqual(status, "complete")

    def test_refresh_reconciliation_uses_seen_total_completion_for_year_diagnostics(self):
        citations = [
            {"title": f"A-{idx}", "authors": "A", "venue": "V", "year": "2024", "url": f"u{idx}"}
            for idx in range(9)
        ]

        status = self.fetcher._refresh_reconciliation_status(
            citations=citations,
            num_citations=10,
            probed_year_counts={2024: 10},
            probe_complete=True,
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

        self.assertTrue(status["ok"])
        self.assertEqual(status["reason"], "matched_complete_histogram")
        self.assertEqual(status["year_fetch_diagnostics"][2024]["seen_total"], 10)

    def test_resolve_refresh_strategy_rehydrates_year_fetch_diagnostics(self):
        pub = {"title": "Paper", "num_citations": 10, "year": "2024"}
        cached = {
            "citations": [{"title": "Cached", "authors": "A", "venue": "V", "year": "2024", "url": "u1"}],
            "num_citations_on_scholar": 10,
            "year_fetch_diagnostics": {
                "2024": {
                    "mode": "year",
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
        diagnostics = self.fetcher._direct_fetch_diagnostics(
            reported_total=10,
            yielded_total=9,
            dedup_count=1,
            termination_reason="target_reached",
        )

        self.assertEqual(diagnostics["seen_total"], 10)
        self.assertFalse(diagnostics["underfetched"])
        self.assertEqual(diagnostics["underfetch_gap"], 0)

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
        self.assertEqual(fetch_calls[0]["rehydrated_probed_year_counts"], {2024: 1})
        self.assertFalse(fetch_calls[0]["rehydrated_probe_complete"])
        self.assertIsNone(fetch_calls[0]["rehydrated_year_fetch_diagnostics"])
        self.assertEqual(fetch_calls[1]["resume_from"], latest_cache["citations"])
        self.assertEqual(fetch_calls[1]["completed_years_in_current_run"], [2024, 2025])
        self.assertEqual(fetch_calls[1]["saved_dedup_count"], 3)
        self.assertEqual(fetch_calls[1]["rehydrated_probed_year_counts"], {2024: 1, 2025: 79})
        self.assertTrue(fetch_calls[1]["rehydrated_probe_complete"])
        self.assertIsNone(fetch_calls[1]["rehydrated_year_fetch_diagnostics"])



if __name__ == '__main__':
    unittest.main()
