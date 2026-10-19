import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.conftest import (
    FetcherTestCase, patch, json, tempfile,
)
import unittest
from code import scholar_citation
from code.output_state import (
    PaperFetchState,
    load_output_fetch_state,
    resolve_citation_status_from_output,
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
                    "scholar_total": 10,
                    "cached_total": 10,
                    "seen_total": 10,
                    "dedup_count": 0,
                    "termination_reason": "page_end",
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


    def test_citation_status_prefers_output_state_over_cache(self):
        """When _paper_states contains a paper, _citation_status should use it."""
        pub = {"title": "Output Paper", "num_citations": 100, "year": "2024"}
        # No cache file
        self.fetcher._paper_states = {
            "Output Paper": {
                "title": "Output Paper",
                "num_citations_on_scholar": 100,
                "num_citations_cached": 100,
                "num_citations_seen": 100,
                "complete": True,
                "complete_fetch_attempt": True,
                "year_fetch_diagnostics": {
                        "histogram_total": 100,
                        "scholar_total": 100,
                        "cached_total": 100,
                        "cached_year_total": 100,
                        "seen_total": 100,
                        "dedup_count": 0,
                },
            }
        }
        status = self.fetcher._citation_status(pub)
        self.assertEqual(status, "complete")

    def test_citation_status_returns_missing_when_output_state_absent(self):
        """When _paper_states does not contain a paper, status is 'missing'.
        Cache files are for within-run recovery only and are not read here."""
        pub = {"title": "Unknown Paper", "num_citations": 10, "year": "2024"}
        self.fetcher._paper_states = {}
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
                    "scholar_total": 42,
                    "cached_total": 42,
                    "seen_total": 42,
                    "dedup_count": 0,
                    "termination_reason": "iterator_exhausted",
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
        self.assertEqual(out["direct_fetch_diagnostics"]["scholar_total"], 42)
        self.assertEqual(set(out.keys()), {
            "title", "pub_url", "citedby_url", "fetch_strategy",
            "num_citations_on_scholar", "complete_fetch_attempt",
            "scholar_changed",
            "year_fetch_diagnostics", "direct_fetch_diagnostics",
            "year_records", "stall_signature", "stall_count", "fetched_at",
            "partial_year_start",
        })

    def test_partial_year_start_roundtrips_and_normalizes(self):
        fs = PaperFetchState.from_dict({
            "title": "T",
            "partial_year_start": {"2025": 10, "2024": 0, "not-a-year": 3},
        })
        self.assertEqual(fs.partial_year_start, {2025: 10})
        self.assertEqual(fs.to_dict()["partial_year_start"], {"2025": 10})
        self.assertIsNone(PaperFetchState.from_dict({"title": "T"}).partial_year_start)
        self.assertIsNone(PaperFetchState.from_dict(
            {"title": "T", "partial_year_start": {}}).partial_year_start)

    def test_from_dict_accepts_legacy_complete_key(self):
        fs = PaperFetchState.from_dict({"title": "T", "complete": True})
        self.assertTrue(fs.complete_fetch_attempt)
        fs2 = PaperFetchState.from_dict({"title": "T", "complete": False})
        self.assertFalse(fs2.complete_fetch_attempt)

    def test_is_complete_direct_mode(self):
        fs = PaperFetchState.from_dict({
            "title": "T", "fetch_strategy": "direct",
            "direct_fetch_diagnostics": {"scholar_total": 10, "seen_total": 10},
        })
        self.assertTrue(fs.is_complete(current_scholar_total=10))

    def test_is_complete_direct_mode_partial(self):
        fs = PaperFetchState.from_dict({
            "title": "T", "fetch_strategy": "direct",
            "direct_fetch_diagnostics": {"scholar_total": 10, "seen_total": 5},
        })
        self.assertFalse(fs.is_complete(current_scholar_total=10))

    def test_is_complete_year_mode(self):
        fs = PaperFetchState.from_dict({
            "title": "T", "fetch_strategy": "year",
            "year_fetch_diagnostics": {"histogram_total": 100, "seen_total": 100},
        })
        self.assertTrue(fs.is_complete(current_scholar_total=110, pub_year="2020"))

    def test_is_complete_year_mode_partial(self):
        fs = PaperFetchState.from_dict({
            "title": "T", "fetch_strategy": "year",
            "year_fetch_diagnostics": {"histogram_total": 100, "seen_total": 90},
        })
        self.assertFalse(fs.is_complete(current_scholar_total=110, pub_year="2020"))

    def test_is_complete_year_mode_partial_when_one_year_is_short(self):
        """Aggregate totals can hide a year whose own bucket is short."""
        fs = PaperFetchState.from_dict({
            "title": "T", "fetch_strategy": "year",
            "year_fetch_diagnostics": {
                "2024": {"year": 2024, "histogram_count": 169, "cached_total": 177,
                         "seen_total": 177, "dedup_count": 0},
                "2026": {"year": 2026, "histogram_count": 66, "cached_total": 64,
                         "seen_total": 64, "dedup_count": 0},
                "histogram_total": 235, "seen_total": 241,
            },
        })
        self.assertFalse(fs.is_complete(current_scholar_total=236, pub_year="2017"))

    def test_is_complete_year_mode_complete_when_every_year_covers_probe(self):
        fs = PaperFetchState.from_dict({
            "title": "T", "fetch_strategy": "year",
            "year_fetch_diagnostics": {
                "2024": {"year": 2024, "histogram_count": 169, "cached_total": 169,
                         "seen_total": 169, "dedup_count": 0},
                "2026": {"year": 2026, "histogram_count": 66, "cached_total": 65,
                         "seen_total": 66, "dedup_count": 1},
                "histogram_total": 235, "seen_total": 235,
            },
        })
        self.assertTrue(fs.is_complete(current_scholar_total=236, pub_year="2017"))
        self.assertEqual(fs.year_shortfall_summary(), '')

    def test_completeness_diag_lists_short_years(self):
        fs = PaperFetchState.from_dict({
            "title": "T", "fetch_strategy": "year",
            "year_fetch_diagnostics": {
                "2026": {"year": 2026, "histogram_count": 66, "cached_total": 64,
                         "seen_total": 64, "dedup_count": 0},
                "histogram_total": 66, "seen_total": 66,
            },
        })
        self.assertEqual(fs.year_shortfall_summary(), '2026(64/66)')
        self.assertIn('short years: 2026(64/66)', fs.completeness_diag())

    def test_year_records_keep_the_short_attempt_and_stall_fields(self):
        """The per-year bookkeeping must survive the save/reload rebuild.

        `_normalize_year_records()` rebuilds every record from a fixed key list;
        it used to drop `short_attempts` / `stalled` / `stalled_probe`, so the
        "re-scan once, then treat the probe as stale" rule could never fire
        across runs — every run restarted at `short_attempts = 0`.
        """
        fs = PaperFetchState.from_dict({
            "title": "T", "fetch_strategy": "year",
            "year_records": [{
                "year": 2021, "histogram_count": 222, "cached_total": 220,
                "seen_total": 220, "dedup_count": 0,
                "termination_reason": "short_page_stop",
                "short_attempts": 3, "stalled": True, "stalled_probe": 222,
            }],
        })
        out = fs.to_dict()
        record = out["year_records"][0]
        self.assertEqual(record["short_attempts"], 3)
        self.assertTrue(record["stalled"])
        self.assertEqual(record["stalled_probe"], 222)
        # ...and the rules that read them agree.
        from code.citation_cache import year_record_gap
        self.assertEqual(year_record_gap(record), 0,
                         "两遍都只有 220 条 → gap 记 0，论文不再永远 partial")
        back = PaperFetchState.from_dict(out).year_records[0]
        self.assertTrue(back["stalled"])
        self.assertEqual(back["short_attempts"], 3)

    def test_resync_year_record_counts_follows_the_stored_rows(self):
        """`cached_total` must describe the rows on disk, `seen_total` the fetch."""
        from code.output_state import resync_year_record_counts
        records = [
            {"year": 2021, "histogram_count": 222, "cached_total": 221,
             "seen_total": 221, "dedup_count": 0,
             "termination_reason": "short_page_stop",
             "short_attempts": 1, "stalled": False, "stalled_probe": 0},
            {"year": 2026, "histogram_count": 71, "cached_total": 71,
             "seen_total": 71, "dedup_count": 0,
             "termination_reason": "short_page_stop"},
        ]
        citations = ([{"year": "2021", "title": "a"}] * 220
                     + [{"year": "2026", "title": "b"}] * 69)
        resync_year_record_counts(records, citations)
        # The file holds 220 / 69 rows, so that is what the records now claim...
        self.assertEqual((records[0]["cached_total"], records[1]["cached_total"]), (220, 69))
        # ...while the fetch-time truth (`seen_total`) and its verdict stay put.
        self.assertEqual((records[0]["seen_total"], records[1]["seen_total"]), (221, 71))
        self.assertEqual((records[0]["dedup_count"], records[1]["dedup_count"]), (1, 2))
        self.assertEqual(records[0]["short_attempts"], 1)
        self.assertEqual(records[0]["termination_reason"], "short_page_stop")

    def test_complete_fetch_attempt_is_tri_state(self):
        """A missing flag means 'unknown', an explicit False means 'did not finish'."""
        self.assertIsNone(PaperFetchState.from_dict({"title": "T"}).complete_fetch_attempt)
        self.assertIsNone(PaperFetchState().complete_fetch_attempt)
        self.assertIsNone(PaperFetchState.from_dict(
            {"title": "T", "complete_fetch_attempt": None}).complete_fetch_attempt)
        self.assertFalse(PaperFetchState.from_dict(
            {"title": "T", "complete_fetch_attempt": False}).complete_fetch_attempt)
        self.assertTrue(PaperFetchState.from_dict(
            {"title": "T", "complete_fetch_attempt": True}).complete_fetch_attempt)

    def test_unknown_complete_fetch_attempt_roundtrips_as_none(self):
        out = PaperFetchState.from_dict({"title": "T"}).to_dict()
        self.assertIsNone(out["complete_fetch_attempt"])
        self.assertIsNone(PaperFetchState.from_dict(out).complete_fetch_attempt)


class FetchStallTests(unittest.TestCase):
    """Repeating the same fetch outcome must stop the paper from being retried.

    Only an attempt that ran the traversal to the end is folded in, so the
    states here carry `complete_fetch_attempt: True` like a real post-fetch
    snapshot does.
    """

    @staticmethod
    def _direct_state(seen_total=0, scholar_total=49, **extra):
        state = {
            "title": "T",
            "fetch_strategy": "direct",
            "complete_fetch_attempt": True,
            "direct_fetch_diagnostics": {
                "scholar_total": scholar_total,
                "cached_total": seen_total,
                "seen_total": seen_total,
                "dedup_count": 0,
                "termination_reason": "empty_first_page",
            },
        }
        state.update(extra)
        return PaperFetchState.from_dict(state)

    @staticmethod
    def _direct_state_without_completion_flag():
        return PaperFetchState.from_dict({
            "title": "T",
            "fetch_strategy": "direct",
            "direct_fetch_diagnostics": {"scholar_total": 49, "seen_total": 0},
        })

    def test_record_fetch_outcome_tracks_identical_outcomes(self):
        fs = self._direct_state()
        self.assertEqual(fs.current_outcome_signature(), "direct:0")
        fs.record_fetch_outcome()
        self.assertEqual((fs.stall_signature, fs.stall_count), ("direct:0", 1))
        fs.record_fetch_outcome()
        self.assertEqual(fs.stall_count, 2)

    def test_record_fetch_outcome_restarts_on_a_different_outcome(self):
        fs = self._direct_state()
        fs.record_fetch_outcome()
        fs.record_fetch_outcome()
        # A page that finally returns rows produces a different outcome.
        fs._direct_fetch_diagnostics["seen_total"] = 20
        fs.record_fetch_outcome()
        self.assertEqual((fs.stall_signature, fs.stall_count), ("direct:20", 1))
        # 计数器确实重开了：默认阈值 1 下"新结果"本身就算一次，所以要显式要 2 次才判定。
        self.assertFalse(fs.is_stalled(stall_after_attempts=2))

    def test_record_fetch_outcome_ignores_an_unfinished_traversal(self):
        fs = self._direct_state()
        fs.record_fetch_outcome()
        self.assertEqual(fs.stall_count, 1)
        # A blocked attempt (complete_fetch_attempt=False) is not an outcome.
        blocked = self._direct_state(seen_total=0, complete_fetch_attempt=False)
        blocked.inherit_stall_tracking(fs)
        blocked.record_fetch_outcome()
        self.assertEqual((blocked.stall_signature, blocked.stall_count), ("direct:0", 1))
        # 默认阈值是 1，所以这里显式要 2 次才判定 —— 只看"未完成的走法没被算进去"。
        self.assertFalse(blocked.is_stalled(stall_after_attempts=2))

    def test_record_fetch_outcome_without_diagnostics_clears_tracking(self):
        fs = PaperFetchState.from_dict({
            "title": "T", "complete_fetch_attempt": True,
        })
        fs.record_fetch_outcome()
        self.assertIsNone(fs.stall_signature)
        self.assertEqual(fs.stall_count, 0)
        self.assertFalse(fs.is_stalled())

    def test_a_state_without_the_completion_flag_is_never_recorded(self):
        fs = self._direct_state_without_completion_flag()
        fs.record_fetch_outcome()
        self.assertEqual((fs.stall_signature, fs.stall_count), (None, 0))

    def test_one_full_walk_is_enough_by_default(self):
        """STALL_AFTER_ATTEMPTS == 1 (2026-10-19 用户定：抓过一遍还不够就判死)."""
        fs = self._direct_state()
        fs.record_fetch_outcome()
        self.assertTrue(fs.is_stalled())

    def test_is_stalled_needs_the_configured_number_of_repeats(self):
        fs = self._direct_state()
        fs.record_fetch_outcome()
        self.assertFalse(fs.is_stalled(stall_after_attempts=2))   # 一次不算
        fs.record_fetch_outcome()
        self.assertTrue(fs.is_stalled(stall_after_attempts=2))    # 两次才算

    def test_is_stalled_false_when_scholar_count_changed(self):
        fs = self._direct_state(stall_signature="direct:0", stall_count=2)
        self.assertTrue(fs.is_stalled())
        fs.mark_scholar_changed()
        self.assertFalse(fs.is_stalled())          # retry when Scholar moved

    def test_is_stalled_false_when_the_outcome_no_longer_matches(self):
        fs = self._direct_state(stall_signature="direct:0", stall_count=5)
        self.assertTrue(fs.is_stalled())
        grown = self._direct_state(seen_total=20, stall_signature="direct:0", stall_count=5)
        self.assertFalse(grown.is_stalled())

    def test_is_stalled_respects_a_custom_threshold(self):
        fs = self._direct_state(stall_signature="direct:0", stall_count=3)
        self.assertTrue(fs.is_stalled(stall_after_attempts=2))
        self.assertFalse(fs.is_stalled(stall_after_attempts=4))

    def test_stall_fields_survive_a_roundtrip(self):
        fs = self._direct_state()
        fs.record_fetch_outcome()
        fs.record_fetch_outcome()
        out = fs.to_dict()
        self.assertEqual(out["stall_signature"], "direct:0")
        self.assertEqual(out["stall_count"], 2)
        again = PaperFetchState.from_dict(out)
        self.assertEqual((again.stall_signature, again.stall_count), ("direct:0", 2))
        self.assertTrue(again.is_stalled())

    def test_resolve_citation_status_from_output_returns_stalled(self):
        pub = {"num_citations": 49, "year": "2020"}
        state = {
            "title": "T", "fetch_strategy": "direct",
            "direct_fetch_diagnostics": {"scholar_total": 49, "seen_total": 0},
            "stall_signature": "direct:0", "stall_count": 2,
        }
        self.assertEqual(
            resolve_citation_status_from_output(pub, state, year_based_threshold=50),
            "stalled")
        # A changed Scholar count re-enables the fetch.
        self.assertEqual(
            resolve_citation_status_from_output(
                pub, dict(state, scholar_changed=True), year_based_threshold=50),
            "partial")

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
            "direct_fetch_diagnostics": {"scholar_total": 10, "seen_total": 10},
        })
        self.assertIn("≥", fs.completeness_diag())
        self.assertIn("seen_total=10", fs.completeness_diag())

    def test_completeness_diag_direct_partial(self):
        fs = PaperFetchState.from_dict({
            "title": "T", "fetch_strategy": "direct",
            "direct_fetch_diagnostics": {"scholar_total": 10, "seen_total": 5},
        })
        diag = fs.completeness_diag()
        self.assertIn("<", diag)
        self.assertIn("seen_total=5", diag)

    def test_completeness_diag_no_diagnostics(self):
        fs = PaperFetchState.from_dict({
            "title": "T", "fetch_strategy": "direct", "num_citations_on_scholar": 10,
        })
        self.assertIn("diagnostics summary absent", fs.completeness_diag(citations_len=10))


    def test_to_dict_normalizes_direct_summary(self):
        """Extra fields in direct_fetch_diagnostics.summary are stripped."""
        fs = PaperFetchState.from_dict({
            "title": "T", "fetch_strategy": "direct",
            "direct_fetch_diagnostics": {
                    "scholar_total": 10, "cached_total": 10, "seen_total": 10,
                    "dedup_count": 0, "termination_reason": "ok",
                    "histogram_total": 999,  # leaked year field
                    "cached_unyeared_count": 5,  # leaked year field
            },
        })
        out = fs.to_dict()
        ds = out["direct_fetch_diagnostics"]
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
                "histogram_total": 15, "seen_total": 15, "scholar_total": 20,
                "cached_total": 15, "cached_year_total": 15, "cached_unyeared_count": 0,
                "dedup_count": 0, "scholar_unyeared_count": 5,
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
                "histogram_total": 1, "scholar_total": 1, "cached_total": 1,
                "cached_year_total": 1, "seen_total": 1, "cached_unyeared_count": 0,
                "dedup_count": 0, "scholar_unyeared_count": 0,
            },
        })
        out = fs.to_dict()
        entry = out["year_records"][0]
        self.assertNotIn("underfetched", entry)
        self.assertNotIn("mode", entry)
        self.assertIn("year", entry)

    def test_to_dict_all_keys_present(self):
        fs = PaperFetchState.from_dict({"title": "T"})
        out = fs.to_dict()
        self.assertEqual(set(out.keys()), {
            "title", "pub_url", "citedby_url", "fetch_strategy",
            "num_citations_on_scholar", "complete_fetch_attempt",
            "scholar_changed",
            "year_fetch_diagnostics", "direct_fetch_diagnostics",
            "year_records", "stall_signature", "stall_count", "fetched_at",
            "partial_year_start",
        })


if __name__ == '__main__':
    unittest.main()

