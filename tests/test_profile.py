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


class AuthorProfileCountSummaryTests(unittest.TestCase):
    def test_build_profile_count_summary_reports_gap(self):
        fetcher = scholar_citation.AuthorProfileFetcher("author", output_dir=".")
        basics = {
            "name": "Test Author",
            "citedby": 8035,
            "cites_per_year": {"2015": 100, "2016": 200, "2026": 7658},
        }

        summary = fetcher._build_profile_count_summary(basics)

        self.assertEqual(summary["scholar_total_citations"], 8035)
        self.assertEqual(summary["year_table_total_citations"], 7958)
        self.assertEqual(summary["year_table_gap"], 77)
        self.assertFalse(summary["year_table_matches_total"])
        self.assertIn("may exclude citations without usable year metadata", summary["year_table_note"])

    def test_save_profile_json_preserves_explicit_fetch_time(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fetcher = scholar_citation.AuthorProfileFetcher("author", output_dir=tmpdir)
            basics = {"name": "Test Author", "citedby": 10, "cites_per_year": {"2026": 10}}

            profile = fetcher.save_profile_json(
                basics,
                [],
                change_history=[],
                fetch_time="2026-04-01T00:00:00",
            )

            self.assertEqual(profile["fetch_time"], "2026-04-01T00:00:00")
            with open(fetcher.profile_json, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["fetch_time"], "2026-04-01T00:00:00")

    def test_save_profile_json_includes_count_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fetcher = scholar_citation.AuthorProfileFetcher("author", output_dir=tmpdir)
            basics = {
                "name": "Test Author",
                "citedby": 8035,
                "cites_per_year": {"2015": 100, "2016": 200, "2026": 7658},
            }
            publications = []

            profile = fetcher.save_profile_json(basics, publications, change_history=[])

            self.assertEqual(profile["total_citations"], 8035)
            self.assertEqual(profile["citation_count_summary"]["year_table_total_citations"], 7958)
            self.assertEqual(profile["citation_count_summary"]["year_table_gap"], 77)

            with open(fetcher.profile_json, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["citation_count_summary"], profile["citation_count_summary"])

    def test_save_profile_xlsx_labels_year_gap_explicitly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fetcher = scholar_citation.AuthorProfileFetcher("author", output_dir=tmpdir)
            basics = {
                "name": "Test Author",
                "affiliation": "Org",
                "interests": ["NLP"],
                "scholar_id": "author",
                "citedby": 8035,
                "citedby_this_year": 123,
                "citedby5y": 1000,
                "hindex": 10,
                "hindex5y": 9,
                "i10index": 20,
                "i10index5y": 18,
                "cites_per_year": {"2015": 100, "2016": 200, "2026": 7658},
            }

            with patch.object(scholar_citation, "datetime") as fake_datetime:
                fake_datetime.now.return_value = types.SimpleNamespace(
                    year=2026,
                    strftime=lambda fmt: "2026-04-02 12:00:00",
                )
                fetcher.save_profile_xlsx(basics, publications=[], change_history=[])

            workbook = fetcher._last_profile_workbook
            ws = workbook.active
            values = [cell.value for cell in ws.cells.values()]
            self.assertIn("Total Citations (Scholar profile)", values)
            self.assertIn("Year-table subtotal (cites_per_year)", values)
            self.assertIn("Year-table gap vs total", values)
            self.assertIn("Citations Per Year (Scholar cites_per_year)", values)
            self.assertTrue(any(
                isinstance(value, str) and "may exclude citations without usable year metadata" in value
                for value in values
            ))


if __name__ == '__main__':
    unittest.main()
