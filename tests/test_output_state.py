import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.conftest import (
    FetcherTestCase, patch, json, tempfile,
)
import unittest
import scholar_citation
from crawler.output_state import (
    load_output_fetch_state,
    resolve_citation_status_from_output,
    extract_fetch_state,
)


class OutputStateTests(FetcherTestCase):
    def test_load_output_fetch_state_maps_titles_to_state(self):
        payload = {
            "papers": [
                {
                    "pub": {"title": "Paper A"},
                    "citations": [],
                    "_fetch_state": {
                        "title": "Paper A",
                        "num_citations_on_scholar": 100,
                        "complete": True,
                    },
                },
                {
                    "pub": {"title": "Paper B"},
                    "citations": [],
                    "_fetch_state": {
                        "title": "Paper B",
                        "num_citations_on_scholar": 50,
                        "complete": False,
                    },
                },
            ]
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
            json.dump(payload, f)
            path = f.name
        try:
            mapping = load_output_fetch_state(path)
            self.assertEqual(set(mapping.keys()), {"Paper A", "Paper B"})
            self.assertEqual(mapping["Paper A"]["num_citations_on_scholar"], 100)
            self.assertTrue(mapping["Paper A"]["complete"])
            self.assertFalse(mapping["Paper B"]["complete"])
        finally:
            os.remove(path)

    def test_load_output_fetch_state_returns_empty_on_missing_file(self):
        self.assertEqual(load_output_fetch_state("/nonexistent/path.json"), {})

    def test_load_output_fetch_state_skips_entries_without_fetch_state(self):
        payload = {
            "papers": [
                {"pub": {"title": "Has State"}, "_fetch_state": {"title": "Has State", "complete": True}},
                {"pub": {"title": "No State"}},
            ]
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
            json.dump(payload, f)
            path = f.name
        try:
            mapping = load_output_fetch_state(path)
            self.assertEqual(set(mapping.keys()), {"Has State"})
        finally:
            os.remove(path)

    def test_resolve_citation_status_from_output_returns_skip_zero(self):
        pub = {"num_citations": 0, "year": "2024"}
        state = {"num_citations_on_scholar": 0, "complete": True}
        self.assertEqual(
            resolve_citation_status_from_output(pub, state, year_based_threshold=50),
            "skip_zero",
        )

    def test_resolve_citation_status_from_output_returns_complete(self):
        pub = {"num_citations": 10, "year": "2024"}
        state = {
            "num_citations_on_scholar": 10,
            "num_citations_cached": 10,
            "num_citations_seen": 10,
            "complete": True,
            "complete_fetch_attempt": True,
            "direct_fetch_diagnostics": {"mode": "direct", "seen_total": 10, "dedup_count": 0, "termination_reason": "page_end"},
        }
        self.assertEqual(
            resolve_citation_status_from_output(pub, state, year_based_threshold=50),
            "complete",
        )

    def test_resolve_citation_status_from_output_returns_partial(self):
        pub = {"num_citations": 100, "year": "2024"}
        state = {
            "num_citations_on_scholar": 100,
            "num_citations_cached": 50,
            "num_citations_seen": 50,
            "complete": False,
            "complete_fetch_attempt": False,
            "probed_year_counts": {},
            "probe_complete": False,
            "year_fetch_diagnostics": {},
        }
        self.assertEqual(
            resolve_citation_status_from_output(pub, state, year_based_threshold=50),
            "partial",
        )

    def test_extract_fetch_state_excludes_citations(self):
        cached = {
            "title": "T",
            "num_citations_on_scholar": 10,
            "citations": [{"title": "Cite"}],
            "extra_field": "should be excluded",
        }
        state = extract_fetch_state(cached)
        self.assertEqual(state["title"], "T")
        self.assertEqual(state["num_citations_on_scholar"], 10)
        self.assertNotIn("citations", state)
        self.assertNotIn("extra_field", state)

    def test_citation_status_prefers_output_state_over_cache(self):
        """When _output_fetch_state contains a paper, _citation_status should use it."""
        pub = {"title": "Output Paper", "num_citations": 100, "year": "2024"}
        # No cache file
        self.fetcher._output_fetch_state = {
            "Output Paper": {
                "title": "Output Paper",
                "num_citations_on_scholar": 100,
                "num_citations_cached": 100,
                "num_citations_seen": 100,
                "complete": True,
                "complete_fetch_attempt": True,
                "direct_fetch_diagnostics": {"mode": "direct", "seen_total": 100, "dedup_count": 0, "termination_reason": "page_end"},
            }
        }
        status = self.fetcher._citation_status(pub)
        self.assertEqual(status, "complete")

    def test_citation_status_falls_back_to_cache_when_output_state_missing(self):
        """When _output_fetch_state does not contain a paper, fall back to cache."""
        pub = {"title": "Cache Paper", "num_citations": 10, "year": "2024"}
        self.fetcher._output_fetch_state = {}
        # Seed cache
        cache_path = self.fetcher._citation_cache_path("Cache Paper")
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({
                "title": "Cache Paper",
                "num_citations_on_scholar": 10,
                "num_citations_cached": 10,
                "num_citations_seen": 10,
                "complete": True,
                "complete_fetch_attempt": True,
                "direct_fetch_diagnostics": {"mode": "direct", "seen_total": 10, "dedup_count": 0, "termination_reason": "page_end"},
                "citations": [{"title": "Cite"}],
            }, f)
        status = self.fetcher._citation_status(pub)
        self.assertEqual(status, "complete")


if __name__ == '__main__':
    unittest.main()
