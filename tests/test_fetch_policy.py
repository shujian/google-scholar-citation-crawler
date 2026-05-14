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

        self.assertEqual(policy["strategy"], "year")
        self.assertEqual(policy["pub_year"], 2023)
        self.assertEqual(policy["reason"], "at_or_above_year_threshold")

    def test_fetch_policy_keeps_year_mode_for_recent_high_total_paper(self):
        with patch.object(scholar_citation, "datetime") as fake_datetime:
            fake_datetime.now.return_value = types.SimpleNamespace(year=2026)
            policy = self.fetcher._resolve_citation_fetch_policy(60, "2026")

        self.assertEqual(policy["strategy"], "year")
        self.assertEqual(policy["pub_year"], 2026)
        self.assertEqual(policy["reason"], "at_or_above_year_threshold")

    def test_effective_scholar_total_returns_pub_num_citations(self):
        pub = {"title": "Paper", "num_citations": 109, "year": "2020"}

        self.assertEqual(self.fetcher._effective_scholar_total(pub), 109)

    def test_resolve_refresh_strategy_does_not_restore_direct_resume_state(self):
        pub = {"title": "Paper", "num_citations": 25, "year": "2024"}
        cached = {
            "citations": [{"title": "Cached", "authors": "A", "venue": "V", "year": "2024", "url": "u1"}],
            "num_citations_on_scholar": 25,
            "dedup_count": 0,
            "direct_resume_state": {
                "strategy": "direct",
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
                "strategy": "direct",
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
                "strategy": "direct",
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
            "direct_fetch_diagnostics": {"strategy": "direct"},
        }

        strategy = self.fetcher._resolve_refresh_strategy(pub, cached, "partial")

        self.assertEqual(strategy["mode"], "fetch")
        self.assertEqual([c["title"] for c in strategy["resume_from"]], ["Keep-2024"])
        self.assertIn("drop unyeared", strategy["action"])



if __name__ == '__main__':
    unittest.main()
