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
from crawler.citation_strategy import refresh_reconciliation_status as _cs_refresh_reconciliation_status

class OutputAndReconciliationTests(FetcherTestCase):
    def test_save_output_writes_excel_run_metadata_from_json_payload(self):
        pub = {
            "no": 1,
            "title": "Paper One",
            "num_citations": 1,
            "year": "2024",
            "venue": "Venue",
        }
        result = {
            "pub": pub,
            "citations": [
                {"title": "Citing Paper", "authors": "A", "venue": "CV", "year": "2024", "url": "https://example.com/cite"}
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            self.fetcher.profile_json = os.path.join(tmpdir, "author_test-author_profile.json")
            self.fetcher.out_json = os.path.join(tmpdir, "author_test-author_paper_citations.json")
            self.fetcher.out_xlsx = os.path.join(tmpdir, "author_test-author_paper_citations.xlsx")
            self.fetcher._run_start_time = 0
            with open(self.fetcher.profile_json, "w", encoding="utf-8") as f:
                json.dump({"publications": [pub]}, f)

            captured = {}
            original_workbook = scholar_citation.openpyxl.Workbook

            class CapturingWorkbook(original_workbook):
                def __init__(self):
                    super().__init__()
                    captured["workbook"] = self

            with patch.object(scholar_citation.openpyxl, "Workbook", CapturingWorkbook), \
                 patch.object(scholar_citation, "datetime") as fake_datetime:
                fake_datetime.now.return_value = types.SimpleNamespace(
                    isoformat=lambda: "2026-04-07T12:34:56",
                    strftime=lambda fmt: "20260407_123456",
                    year=2026,
                )
                self.fetcher._save_output([result])

            with open(self.fetcher.out_json, "r", encoding="utf-8") as f:
                payload = json.load(f)

        workbook = captured["workbook"]
        self.assertEqual(payload["author_id"], "test-author")
        self.assertEqual(payload["fetch_time"], "2026-04-07T12:34:56")
        self.assertEqual(payload["total_papers"], 1)
        self.assertEqual(payload["total_citations_collected"], 1)
        self.assertEqual(len(workbook.sheets), 3)
        self.assertEqual(workbook.sheets[2].title, "Run Metadata")
        metadata_sheet = workbook.sheets[2]
        self.assertEqual(metadata_sheet.cells[(1, 1)].value, "Author ID")
        self.assertEqual(metadata_sheet.cells[(1, 2)].value, payload["author_id"])
        self.assertEqual(metadata_sheet.cells[(2, 1)].value, "Fetch Time")
        self.assertEqual(metadata_sheet.cells[(2, 2)].value, payload["fetch_time"])
        self.assertEqual(metadata_sheet.cells[(3, 1)].value, "Total Papers")
        self.assertEqual(metadata_sheet.cells[(3, 2)].value, payload["total_papers"])
        self.assertEqual(metadata_sheet.cells[(4, 1)].value, "Total Citations Collected")
        self.assertEqual(metadata_sheet.cells[(4, 2)].value, payload["total_citations_collected"])

        citations_sheet = workbook.sheets[1]
        self.assertEqual(citations_sheet.title, "All Citations")
        self.assertEqual(citations_sheet.cells[(2, 5)].value, payload["papers"][0]["citations"][0]["year"])

        status = _cs_refresh_reconciliation_status(
            [
                {"title": "A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
                {"title": "B", "authors": "B", "venue": "V", "year": "2025", "url": "u2"},
                {"title": "C", "authors": "C", "venue": "V", "year": "2025", "url": "u3"},
            ],
            5,
            dedup_count=0,
            probed_year_counts={2024: 1, 2025: 2},
            probe_complete=True,
        )

        self.assertTrue(status["ok"])
        self.assertEqual(status["reason"], "matched_complete_histogram")
        self.assertEqual(status["histogram_total"], 3)
        self.assertEqual(status["unyeared_count"], 2)
        self.assertEqual(status["cached_year_total"], 3)

    def test_save_output_includes_fetch_state_from_cache(self):
        pub = {
            "no": 1,
            "title": "Paper With Cache",
            "num_citations": 150,
            "year": "2020",
            "venue": "Venue",
        }
        result = {
            "pub": pub,
            "citations": [
                {"title": "Citing Paper", "authors": "A", "venue": "CV", "year": "2024", "url": "https://example.com/cite"}
            ],
        }
        cache_content = {
            "title": "Paper With Cache",
            "num_citations_on_scholar": 150,
            "num_citations_cached": 1,
            "num_citations_seen": 1,
            "dedup_count": 0,
            "complete": True,
            "complete_fetch_attempt": True,
            "year_fetch_diagnostics": {
                "2024": {
                    "mode": "year", "year": 2024,
                    "scholar_total": 50, "cached_total": 1,
                    "seen_total": 50, "dedup_count": 0,
                    "underfetched": False,
                    "termination_reason": "short_page_stop",
                }
            },
            "citations": result["citations"],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            self.fetcher.profile_json = os.path.join(tmpdir, "author_test-author_profile.json")
            self.fetcher.out_json = os.path.join(tmpdir, "author_test-author_paper_citations.json")
            self.fetcher.out_xlsx = os.path.join(tmpdir, "author_test-author_paper_citations.xlsx")
            self.fetcher._run_start_time = 0
            with open(self.fetcher.profile_json, "w", encoding="utf-8") as f:
                json.dump({"publications": [pub]}, f)
            # Seed the cache so _save_output can read it
            cache_path = self.fetcher._citation_cache_path(pub["title"])
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache_content, f)

            with patch.object(scholar_citation.openpyxl, "Workbook", return_value=scholar_citation.openpyxl.Workbook()), \
                 patch.object(scholar_citation, "datetime") as fake_datetime:
                fake_datetime.now.return_value = types.SimpleNamespace(
                    isoformat=lambda: "2026-05-01T12:00:00",
                    strftime=lambda fmt: "20260501_120000",
                    year=2026,
                )
                self.fetcher._save_output([result])

            with open(self.fetcher.out_json, "r", encoding="utf-8") as f:
                payload = json.load(f)

        paper = payload["papers"][0]
        self.assertIn("_fetch_state", paper)
        fs = paper["_fetch_state"]
        self.assertEqual(fs["num_citations_on_scholar"], 150)
        self.assertTrue(fs["complete"])
        self.assertTrue(fs["complete_fetch_attempt"])
        self.assertIn("year_fetch_diagnostics", fs)
        self.assertEqual(fs["year_fetch_diagnostics"]["2024"]["scholar_total"], 50)
        # citations should NOT be duplicated inside _fetch_state
        self.assertNotIn("citations", fs)

    def test_refresh_reconciliation_requests_escalation_on_histogram_mismatch(self):
        status = _cs_refresh_reconciliation_status(
            [
                {"title": "A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
                {"title": "B", "authors": "B", "venue": "V", "year": "2025", "url": "u2"},
                {"title": "C", "authors": "C", "venue": "V", "year": "2025", "url": "u3"},
            ],
            5,
            dedup_count=0,
            probed_year_counts={2024: 2, 2025: 1},
            probe_complete=True,
        )

        self.assertFalse(status["ok"])
        self.assertEqual(status["reason"], "year_count_mismatch")
        self.assertEqual(status["histogram_total"], 3)

    def test_refresh_reconciliation_accepts_matching_year_histogram_when_probe_incomplete(self):
        status = _cs_refresh_reconciliation_status(
            [
                {"title": "A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
                {"title": "B", "authors": "B", "venue": "V", "year": "N/A", "url": "u2"},
                {"title": "C", "authors": "C", "venue": "V", "year": "N/A", "url": "u3"},
            ],
            5,
            dedup_count=0,
            probed_year_counts={2024: 1},
            probe_complete=False,
        )

        self.assertTrue(status["ok"])
        self.assertEqual(status["reason"], "matched_incomplete_histogram")
        self.assertEqual(status["histogram_total"], 1)
        self.assertEqual(status["cached_unyeared_count"], 2)

    def test_refresh_reconciliation_keeps_histogram_incomplete_status_when_probe_incomplete_histogram_mismatches(self):
        status = _cs_refresh_reconciliation_status(
            [
                {"title": "A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
            ],
            3,
            dedup_count=0,
            probed_year_counts={2024: 2},
            probe_complete=False,
        )

        self.assertFalse(status["ok"])
        self.assertEqual(status["reason"], "histogram_incomplete")
        self.assertEqual(status["histogram_total"], 2)
        self.assertEqual(status["cached_total"], 1)

    def test_refresh_reconciliation_accepts_count_match_without_probe_histogram(self):
        status = _cs_refresh_reconciliation_status(
            [
                {"title": "A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
                {"title": "B", "authors": "B", "venue": "V", "year": "N/A", "url": "u2"},
                {"title": "C", "authors": "C", "venue": "V", "year": "N/A", "url": "u3"},
            ],
            3,
            dedup_count=0,
            probed_year_counts=None,
            probe_complete=False,
        )

        self.assertTrue(status["ok"])
        self.assertEqual(status["reason"], "count_matched_without_histogram")
        self.assertEqual(status["histogram_total"], 0)
        self.assertEqual(status["cached_unyeared_count"], 2)

    def test_refresh_reconciliation_accepts_count_match_when_probe_incomplete(self):
        status = _cs_refresh_reconciliation_status(
            [
                {"title": "A", "authors": "A", "venue": "V", "year": "2024", "url": "u1"},
                {"title": "B", "authors": "B", "venue": "V", "year": "N/A", "url": "u2"},
                {"title": "C", "authors": "C", "venue": "V", "year": "N/A", "url": "u3"},
            ],
            3,
            dedup_count=0,
            probed_year_counts={2024: 1},
            probe_complete=False,
        )

        self.assertTrue(status["ok"])
        self.assertEqual(status["reason"], "matched_incomplete_histogram")
        self.assertEqual(status["histogram_total"], 1)
        self.assertEqual(status["cached_unyeared_count"], 2)

        counts, probe_complete = self.fetcher._rehydrate_probe_metadata(
            {
                "probed_year_counts": {"2024": 1, "2025": 2},
                "probe_complete": True,
            },
            current_scholar_total=3,
        )

        self.assertEqual(counts, {2024: 1, 2025: 2})
        self.assertTrue(probe_complete)

    def test_rehydrate_probe_metadata_downgrades_legacy_or_stale_complete_flags(self):
        legacy_counts, legacy_complete = self.fetcher._rehydrate_probe_metadata(
            {
                "probed_year_counts": {"2024": 1, "2025": 2},
            },
            current_scholar_total=3,
        )
        stale_counts, stale_complete = self.fetcher._rehydrate_probe_metadata(
            {
                "probed_year_counts": {"2024": 1, "2025": 2},
                "probe_complete": True,
            },
            current_scholar_total=4,
        )

        self.assertEqual(legacy_counts, {2024: 1, 2025: 2})
        self.assertFalse(legacy_complete)
        self.assertEqual(stale_counts, {2024: 1, 2025: 2})
        self.assertFalse(stale_complete)



if __name__ == '__main__':
    unittest.main()
