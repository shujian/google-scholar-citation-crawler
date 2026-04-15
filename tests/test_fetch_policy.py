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

class FetchPolicyAndStrategyTests(FetcherTestCase):
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

    def test_effective_scholar_total_ignores_historical_cache_high_watermark(self):
        pub = {"title": "Paper", "num_citations": 109, "year": "2020"}
        cached = {
            "num_citations_on_scholar": 120,
            "num_citations_seen": 109,
        }

        self.assertEqual(self.fetcher._effective_scholar_total(pub, cached), 109)

    def test_promote_live_citation_count_keeps_upward_only_runtime_semantics(self):
        pub = {"title": "Paper", "num_citations": 109}
        self.fetcher._updated_publication_counts = {}

        promoted = self.fetcher._promote_live_citation_count(pub, 120, source="live_probe")
        unchanged = self.fetcher._promote_live_citation_count(pub, 118, source="live_probe")

        self.assertEqual(promoted, 120)
        self.assertEqual(unchanged, 120)
        self.assertEqual(pub["num_citations"], 120)
        self.assertEqual(self.fetcher._updated_publication_counts["Paper"], 120)

    def test_resolve_refresh_strategy_does_not_restore_direct_resume_state(self):
        pub = {"title": "Paper", "num_citations": 25, "year": "2024"}
        cached = {
            "citations": [{"title": "Cached", "authors": "A", "venue": "V", "year": "2024", "url": "u1"}],
            "num_citations_on_scholar": 25,
            "dedup_count": 0,
            "direct_resume_state": {
                "mode": "direct",
                "next_index": 13,
                "source_scholar_total": 25,
                "citedby_url": "/scholar?cites=123",
            },
        }

        strategy = self.fetcher._resolve_refresh_strategy(pub, cached, "partial", citedby_url="/scholar?cites=123")

        self.assertEqual(strategy["mode"], "resume")
        self.assertIsNone(strategy["direct_resume_state"])
        self.assertIn("direct fetch restarts from head", strategy["action"])

    def test_resolve_refresh_strategy_invalidates_direct_resume_state_on_total_change(self):
        pub = {"title": "Paper", "num_citations": 26, "year": "2024"}
        cached = {
            "citations": [{"title": "Cached", "authors": "A", "venue": "V", "year": "2024", "url": "u1"}],
            "num_citations_on_scholar": 25,
            "dedup_count": 0,
            "direct_resume_state": {
                "mode": "direct",
                "next_index": 13,
                "source_scholar_total": 25,
                "citedby_url": "/scholar?cites=123",
            },
        }

        strategy = self.fetcher._resolve_refresh_strategy(pub, cached, "partial", citedby_url="/scholar?cites=123")

        self.assertEqual(strategy["mode"], "update")
        self.assertIsNone(strategy["direct_resume_state"])
        self.assertIn("direct fetch restarts from head", strategy["action"])

    def test_resolve_refresh_strategy_invalidates_direct_resume_state_on_citedby_url_change(self):
        pub = {"title": "Paper", "num_citations": 25, "year": "2024"}
        cached = {
            "citations": [{"title": "Cached", "authors": "A", "venue": "V", "year": "2024", "url": "u1"}],
            "num_citations_on_scholar": 25,
            "dedup_count": 0,
            "direct_resume_state": {
                "mode": "direct",
                "next_index": 13,
                "source_scholar_total": 25,
                "citedby_url": "/scholar?cites=123",
            },
        }

        strategy = self.fetcher._resolve_refresh_strategy(pub, cached, "partial", citedby_url="/scholar?cites=456")

        self.assertEqual(strategy["mode"], "resume")
        self.assertIsNone(strategy["direct_resume_state"])
        self.assertIn("direct fetch restarts from head", strategy["action"])

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

    def test_resolve_refresh_strategy_update_drops_cached_unyeared_before_refresh(self):
        pub = {"title": "Paper", "num_citations": 5, "year": "2024"}
        cached = {
            "citations": [
                {"title": "Keep-2024", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
                {"title": "Drop-NY", "authors": "B", "venue": "V", "year": "N/A", "url": "u2"},
            ],
            "num_citations_on_scholar": 3,
            "dedup_count": 0,
        }

        strategy = self.fetcher._resolve_refresh_strategy(pub, cached, "partial")

        self.assertEqual(strategy["mode"], "update")
        self.assertEqual([c["title"] for c in strategy["resume_from"]], ["Keep-2024"])
        self.assertIn("drop cached unyeared before refresh", strategy["action"])

    def test_resolve_refresh_strategy_recheck_drops_cached_unyeared_before_refresh(self):
        self.fetcher.recheck_citations = True
        pub = {"title": "Paper", "num_citations": 5, "year": "2024"}
        cached = {
            "citations": [
                {"title": "Keep-2024", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
                {"title": "Drop-NY", "authors": "B", "venue": "V", "year": "N/A", "url": "u2"},
            ],
            "num_citations_on_scholar": 5,
            "dedup_count": 0,
            "completed_years_in_current_run": [2024],
        }

        strategy = self.fetcher._resolve_refresh_strategy(pub, cached, "partial")

        self.assertEqual(strategy["mode"], "recheck")
        self.assertEqual([c["title"] for c in strategy["resume_from"]], ["Keep-2024"])
        self.assertEqual(strategy["completed_years_in_current_run"], [])
        self.assertIn("drop cached unyeared before refresh", strategy["action"])




if __name__ == '__main__':
    unittest.main()
