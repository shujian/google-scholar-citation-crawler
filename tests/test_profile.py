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
from crawler.profile_io import AuthorProfile


class AuthorProfileCountSummaryTests(unittest.TestCase):
    def test_build_profile_count_summary_reports_gap(self):
        from crawler.profile_io import build_profile_count_summary
        basics = {
            "name": "Test Author",
            "citedby": 8035,
            "cites_per_year": {"2015": 100, "2016": 200, "2026": 7658},
        }

        summary = build_profile_count_summary(basics)

        self.assertEqual(summary["scholar_total_citations"], 8035)
        self.assertEqual(summary["year_table_total_citations"], 7958)
        self.assertEqual(summary["year_table_gap"], 77)
        self.assertFalse(summary["year_table_matches_total"])
        self.assertIn("may exclude citations without usable year metadata", summary["year_table_note"])

    def test_author_profile_save_json_preserves_explicit_fetch_time(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = os.path.join(tmpdir, "author_test_profile.json")
            basics = {"name": "Test Author", "citedby": 10, "cites_per_year": {"2026": 10}}
            profile = AuthorProfile(
                author_info=basics,
                publications=[],
                fetch_time="2026-04-01T00:00:00",
                change_history=[],
            )

            payload = profile.save_json(profile_path)

            self.assertEqual(payload["fetch_time"], "2026-04-01T00:00:00")
            with open(profile_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["fetch_time"], "2026-04-01T00:00:00")

    def test_author_profile_save_json_includes_count_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = os.path.join(tmpdir, "author_test_profile.json")
            basics = {
                "name": "Test Author",
                "citedby": 8035,
                "cites_per_year": {"2015": 100, "2016": 200, "2026": 7658},
            }
            profile = AuthorProfile(
                author_info=basics,
                publications=[],
                fetch_time="2026-04-01T00:00:00",
                change_history=[],
            )

            payload = profile.save_json(profile_path)

            self.assertEqual(payload["total_citations"], 8035)
            self.assertEqual(payload["citation_count_summary"]["year_table_total_citations"], 7958)
            self.assertEqual(payload["citation_count_summary"]["year_table_gap"], 77)

            with open(profile_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["citation_count_summary"], payload["citation_count_summary"])

    def test_author_profile_xlsx_labels_year_gap_explicitly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from crawler.profile_io import save_profile_xlsx
            import openpyxl
            from openpyxl.styles import Font, PatternFill, Alignment

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
            profile = AuthorProfile(
                author_info=basics,
                publications=[],
                fetch_time="",
                change_history=[],
            )

            xlsx_path = os.path.join(tmpdir, "author_test_profile.xlsx")
            with patch("crawler.profile_io.datetime") as fake_datetime:
                fake_datetime.now.return_value = types.SimpleNamespace(
                    year=2026,
                    strftime=lambda fmt: "2026-04-02 12:00:00",
                )
                workbook = save_profile_xlsx(
                    xlsx_path,
                    profile,
                    datetime_module=fake_datetime,
                    openpyxl_module=openpyxl,
                    font_cls=Font,
                    pattern_fill_cls=PatternFill,
                    alignment_cls=Alignment,
                    print_fn=print,
                )

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


class AuthorProfileDataclassTests(unittest.TestCase):
    def test_load_returns_none_for_missing_file(self):
        profile = AuthorProfile.load("/nonexistent/path.json")
        self.assertIsNone(profile)

    def test_roundtrip_preserves_fields(self):
        basics = {"name": "Test", "citedby": 42, "cites_per_year": {}}
        profile = AuthorProfile(
            author_info=basics,
            publications=[],
            fetch_time="2026-05-08T00:00:00",
            change_history=[{"fetch_time": "2026-05-07"}],
        )
        d = profile.to_dict()
        loaded = AuthorProfile.from_dict(d)
        self.assertEqual(loaded.author_info["name"], "Test")
        self.assertEqual(loaded.total_citations, 42)
        self.assertEqual(loaded.total_publications, 0)
        self.assertEqual(loaded.fetch_time, "2026-05-08T00:00:00")
        self.assertEqual(len(loaded.change_history), 1)

    def test_total_citations_falls_back_to_zero(self):
        profile = AuthorProfile(author_info={}, publications=[])
        self.assertEqual(profile.total_citations, 0)
        self.assertEqual(profile.total_publications, 0)

    def test_append_history_without_prev(self):
        profile = AuthorProfile(
            author_info={"citedby": 5, "citedby_this_year": 1, "hindex": 2, "i10index": 3},
            publications=[],
        )
        record = profile.append_history(None)
        self.assertEqual(record["citedby"], 5)
        self.assertEqual(record["new_papers"], [])
        self.assertEqual(len(profile.change_history), 1)

    def test_append_history_detects_new_papers(self):
        prev = AuthorProfile(
            publications=[{"title": "Old Paper", "num_citations": 10, "url": "", "citedby_url": ""}],
        )
        profile = AuthorProfile(
            author_info={"citedby": 5, "citedby_this_year": 1, "hindex": 2, "i10index": 3},
            publications=[
                {"title": "Old Paper", "num_citations": 12, "url": "", "citedby_url": ""},
                {"title": "New Paper", "num_citations": 3, "url": "", "citedby_url": ""},
            ],
        )
        record = profile.append_history(prev)
        self.assertIn("New Paper", record["new_papers"])
        self.assertEqual(len(record["changed_citations"]), 1)
        self.assertEqual(record["changed_citations"][0]["old"], 10)
        self.assertEqual(record["changed_citations"][0]["new"], 12)

    def test_append_history_no_changes(self):
        prev = AuthorProfile(
            publications=[{"title": "Paper A", "num_citations": 5, "url": "", "citedby_url": ""}],
        )
        profile = AuthorProfile(
            author_info={"citedby": 5, "citedby_this_year": 1, "hindex": 2, "i10index": 3},
            publications=[{"title": "Paper A", "num_citations": 5, "url": "", "citedby_url": ""}],
        )
        record = profile.append_history(prev)
        self.assertEqual(record["new_papers"], [])
        self.assertEqual(record["changed_citations"], [])


if __name__ == '__main__':
    unittest.main()
