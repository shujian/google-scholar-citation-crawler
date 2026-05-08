import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.conftest import (
    FetcherTestCase, patch, json, tempfile,
)
import unittest
import scholar_citation
from crawler.output_state import (
    PaperFetchState,
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
            self.assertEqual(mapping["Paper A"].num_citations_on_scholar, 100)
            self.assertTrue(mapping["Paper A"].complete_fetch_attempt)
            self.assertFalse(mapping["Paper B"].complete_fetch_attempt)
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
            "direct_fetch_diagnostics": {
                "summary": {
                    "scholar_total": 10,
                    "cached_total": 10,
                    "seen_total": 10,
                    "dedup_count": 0,
                    "termination_reason": "page_end",
                },
            },
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
        self.assertEqual(state.title, "T")
        self.assertEqual(state.num_citations_on_scholar, 10)
        self.assertNotIn("citations", state.to_dict())
        self.assertNotIn("extra_field", state.to_dict())

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
                "year_fetch_diagnostics": {
                    "summary": {
                        "histogram_total": 100,
                        "scholar_total": 100,
                        "cached_total": 100,
                        "cached_year_total": 100,
                        "seen_total": 100,
                        "dedup_count": 0,
                    },
                },
            }
        }
        status = self.fetcher._citation_status(pub)
        self.assertEqual(status, "complete")

    def test_citation_status_returns_missing_when_output_state_absent(self):
        """When _output_fetch_state does not contain a paper, status is 'missing'.
        Cache files are for within-run recovery only and are not read here."""
        pub = {"title": "Unknown Paper", "num_citations": 10, "year": "2024"}
        self.fetcher._output_fetch_state = {}
        status = self.fetcher._citation_status(pub)
        self.assertEqual(status, "missing")


class PaperFetchStateTests(unittest.TestCase):
    def test_from_dict_roundtrip(self):
        d = {
            "title": "Test Paper",
            "pub_url": "http://example.com",
            "citedby_url": "/scholar?cites=123",
            "fetch_strategy": "direct",
            "num_citations_on_scholar": 42,
            "complete_fetch_attempt": True,
            "year_fetch_diagnostics": None,
            "direct_fetch_diagnostics": {
                "summary": {
                    "scholar_total": 42,
                    "cached_total": 42,
                    "seen_total": 42,
                    "dedup_count": 0,
                    "termination_reason": "iterator_exhausted",
                },
            },
            "fetched_at": "2026-05-07T12:00:00",
        }
        fs = PaperFetchState.from_dict(d)
        self.assertEqual(fs.title, "Test Paper")
        self.assertEqual(fs.num_citations_on_scholar, 42)
        self.assertTrue(fs.complete_fetch_attempt)
        self.assertEqual(fs.fetch_strategy, "direct")
        out = fs.to_dict()
        self.assertEqual(out["title"], "Test Paper")
        self.assertEqual(out["num_citations_on_scholar"], 42)
        self.assertEqual(out["direct_fetch_diagnostics"]["summary"]["scholar_total"], 42)
        self.assertEqual(set(out.keys()), {
            "title", "pub_url", "citedby_url", "fetch_strategy",
            "num_citations_on_scholar", "complete_fetch_attempt",
            "year_fetch_diagnostics", "direct_fetch_diagnostics",
            "year_records", "fetched_at",
        })

    def test_from_dict_accepts_legacy_complete_key(self):
        fs = PaperFetchState.from_dict({"title": "T", "complete": True})
        self.assertTrue(fs.complete_fetch_attempt)
        fs2 = PaperFetchState.from_dict({"title": "T", "complete": False})
        self.assertFalse(fs2.complete_fetch_attempt)

    def test_is_complete_direct_mode(self):
        fs = PaperFetchState.from_dict({
            "title": "T", "fetch_strategy": "direct",
            "direct_fetch_diagnostics": {"summary": {"scholar_total": 10, "seen_total": 10}},
        })
        self.assertTrue(fs.is_complete(current_scholar_total=10))

    def test_is_complete_direct_mode_partial(self):
        fs = PaperFetchState.from_dict({
            "title": "T", "fetch_strategy": "direct",
            "direct_fetch_diagnostics": {"summary": {"scholar_total": 10, "seen_total": 5}},
        })
        self.assertFalse(fs.is_complete(current_scholar_total=10))

    def test_is_complete_year_mode(self):
        fs = PaperFetchState.from_dict({
            "title": "T", "fetch_strategy": "year",
            "year_fetch_diagnostics": {"summary": {"histogram_total": 100, "seen_total": 100}},
        })
        self.assertTrue(fs.is_complete(current_scholar_total=110, pub_year="2020"))

    def test_is_complete_year_mode_partial(self):
        fs = PaperFetchState.from_dict({
            "title": "T", "fetch_strategy": "year",
            "year_fetch_diagnostics": {"summary": {"histogram_total": 100, "seen_total": 90}},
        })
        self.assertFalse(fs.is_complete(current_scholar_total=110, pub_year="2020"))

    def test_is_complete_no_diagnostics_returns_false(self):
        """Without diagnostics summary, completeness cannot be verified → False."""
        fs = PaperFetchState.from_dict({
            "title": "T", "fetch_strategy": "direct", "num_citations_on_scholar": 10,
        })
        self.assertFalse(fs.is_complete(current_scholar_total=10))
        self.assertFalse(fs.is_complete(current_scholar_total=0))

    def test_completeness_diag_direct_complete(self):
        fs = PaperFetchState.from_dict({
            "title": "T", "fetch_strategy": "direct",
            "direct_fetch_diagnostics": {"summary": {"scholar_total": 10, "seen_total": 10}},
        })
        self.assertIn("≥", fs.completeness_diag())
        self.assertIn("seen_total=10", fs.completeness_diag())

    def test_completeness_diag_direct_partial(self):
        fs = PaperFetchState.from_dict({
            "title": "T", "fetch_strategy": "direct",
            "direct_fetch_diagnostics": {"summary": {"scholar_total": 10, "seen_total": 5}},
        })
        diag = fs.completeness_diag()
        self.assertIn("<", diag)
        self.assertIn("seen_total=5", diag)

    def test_completeness_diag_no_diagnostics(self):
        fs = PaperFetchState.from_dict({
            "title": "T", "fetch_strategy": "direct", "num_citations_on_scholar": 10,
        })
        self.assertIn("(no diagnostics)", fs.completeness_diag(citations_len=10))


    def test_to_dict_normalizes_direct_summary(self):
        """Extra fields in direct_fetch_diagnostics.summary are stripped."""
        fs = PaperFetchState.from_dict({
            "title": "T", "fetch_strategy": "direct",
            "direct_fetch_diagnostics": {
                "summary": {
                    "scholar_total": 10, "cached_total": 10, "seen_total": 10,
                    "dedup_count": 0, "termination_reason": "ok",
                    "histogram_total": 999,  # leaked year field
                    "cached_unyeared_count": 5,  # leaked year field
                },
            },
        })
        out = fs.to_dict()
        ds = out["direct_fetch_diagnostics"]["summary"]
        self.assertEqual(set(ds.keys()), {
            "scholar_total", "cached_total", "seen_total", "dedup_count", "termination_reason",
        })
        self.assertEqual(ds["scholar_total"], 10)
        self.assertEqual(ds["termination_reason"], "ok")

    def test_to_dict_sorts_year_entries(self):
        """Year records are sorted by year ascending."""
        fs = PaperFetchState.from_dict({
            "title": "T", "fetch_strategy": "year",
            "year_fetch_diagnostics": {
                "2023": {"year": 2023, "histogram_count": 10, "cached_total": 10, "seen_total": 10, "dedup_count": 0, "termination_reason": "ok"},
                "2020": {"year": 2020, "histogram_count": 5, "cached_total": 5, "seen_total": 5, "dedup_count": 0, "termination_reason": "ok"},
                "summary": {"histogram_total": 15, "seen_total": 15, "scholar_total": 20,
                            "cached_total": 15, "cached_year_total": 15, "cached_unyeared_count": 0,
                            "dedup_count": 0, "scholar_unyeared_count": 5},
            },
        })
        out = fs.to_dict()
        records = out["year_records"]
        years = [r["year"] for r in records]
        self.assertEqual(years, [2020, 2023])

    def test_to_dict_strips_unknown_year_fields(self):
        """Per-year records only contain allowed keys."""
        fs = PaperFetchState.from_dict({
            "title": "T", "fetch_strategy": "year",
            "year_fetch_diagnostics": {
                "2024": {"year": 2024, "histogram_count": 1, "cached_total": 1,
                         "seen_total": 1, "dedup_count": 0, "termination_reason": "ok",
                         "underfetched": True, "mode": "year"},
                "summary": {"histogram_total": 1, "scholar_total": 1, "cached_total": 1,
                            "cached_year_total": 1, "seen_total": 1, "cached_unyeared_count": 0,
                            "dedup_count": 0, "scholar_unyeared_count": 0},
            },
        })
        out = fs.to_dict()
        entry = out["year_records"][0]
        self.assertNotIn("underfetched", entry)
        self.assertNotIn("mode", entry)
        self.assertIn("year", entry)

    def test_to_dict_all_nine_keys_present(self):
        fs = PaperFetchState.from_dict({"title": "T"})
        out = fs.to_dict()
        self.assertEqual(set(out.keys()), {
            "title", "pub_url", "citedby_url", "fetch_strategy",
            "num_citations_on_scholar", "complete_fetch_attempt",
            "year_fetch_diagnostics", "direct_fetch_diagnostics",
            "year_records", "fetched_at",
        })


if __name__ == '__main__':
    unittest.main()

