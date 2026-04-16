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

        self.assertEqual(strategy["mode"], "fetch")
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

        self.assertEqual(strategy["mode"], "fetch")
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

        self.assertEqual(strategy["mode"], "fetch")
        self.assertIsNone(strategy["direct_resume_state"])
        self.assertIn("direct fetch restarts from head", strategy["action"])

    def test_resolve_refresh_strategy_drops_cached_unyeared_for_year_mode(self):
        # num_citations=100, pub_year=2025 → avg=50/yr → year-based fetch policy
        pub = {"title": "Paper", "num_citations": 100, "year": "2025"}
        cached = {
            "citations": [
                {"title": "Keep-2024", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
                {"title": "Drop-NY", "authors": "B", "venue": "V", "year": "N/A", "url": "u2"},
            ],
            "num_citations_on_scholar": 80,
            "dedup_count": 0,
        }

        strategy = self.fetcher._resolve_refresh_strategy(pub, cached, "partial")

        self.assertEqual(strategy["mode"], "fetch")
        self.assertEqual([c["title"] for c in strategy["resume_from"]], ["Keep-2024"])
        self.assertIn("drop unyeared", strategy["action"])



if __name__ == '__main__':
    unittest.main()
