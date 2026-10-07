import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.conftest import (
    FetcherTestCase, scholarly_mod, _DummyNav, _DummyIterator,
    patch, StringIO, json, tempfile,
)
import types
import unittest
import shutil
import scholar_citation
from crawler.output_state import PaperFetchState
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

    def test_save_output_includes_fetch_state_from_output_state(self):
        pub = {
            "no": 1,
            "title": "Paper With State",
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
        state_dict = {
            "title": "Paper With State",
            "num_citations_on_scholar": 150,
            "complete_fetch_attempt": True,
            "fetch_strategy": "year",
            "year_fetch_diagnostics": {
                "2024": {
                    "year": 2024,
                    "histogram_count": 50, "cached_total": 1,
                    "seen_total": 50, "dedup_count": 0,
                    "termination_reason": "short_page_stop",
                },
                "histogram_total": 50,
                "scholar_total": 150,
                "cached_total": 1,
                "cached_year_total": 1,
                "seen_total": 50,
                "dedup_count": 0,
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
            # Set output state (not cache)
            self.fetcher._paper_states = {
                "Paper With State": PaperFetchState.from_dict(state_dict),
            }

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
        self.assertTrue(fs["complete_fetch_attempt"])
        self.assertIn("year_fetch_diagnostics", fs)
        self.assertIn("year_records", fs)
        yr = fs["year_records"][0]
        self.assertEqual(yr["histogram_count"], 50)
        # citations should NOT be duplicated inside _fetch_state
        self.assertNotIn("citations", fs)

    # -- interrupted run: keep this run's progress -------------------------

    _INTERRUPTED_PUB = {
        "no": 1,
        "title": "Interrupted Paper",
        "num_citations": 95,
        "year": "2022",
        "venue": "V",
    }

    @staticmethod
    def _stale_complete_state():
        """What the previous run persisted: aggregate claims complete."""
        return {
            "title": "Interrupted Paper",
            "fetch_strategy": "year",
            "num_citations_on_scholar": 91,
            "complete_fetch_attempt": True,
            "year_fetch_diagnostics": {
                "2022": {"year": 2022, "histogram_count": 6, "cached_total": 7,
                         "seen_total": 7, "dedup_count": 0},
                "2026": {"year": 2026, "histogram_count": 4, "cached_total": 4,
                         "seen_total": 4, "dedup_count": 0},
                "histogram_total": 91, "seen_total": 93,
            },
        }

    @staticmethod
    def _mid_paper_snapshot(partial_citations):
        """What save_progress leaves behind when the fetch is interrupted.

        2026 was probed (5) and only 4 are cached, so the year is short — the
        aggregate (93 >= 91) still hides it.
        """
        return {
            "title": "Interrupted Paper",
            "fetch_strategy": "year",
            "num_citations_on_scholar": 95,
            "complete_fetch_attempt": False,
            "year_records": [
                {"year": 2022, "histogram_count": 6, "cached_total": 7,
                 "seen_total": 7, "dedup_count": 0},
                {"year": 2026, "histogram_count": 5, "cached_total": 4,
                 "seen_total": 4, "dedup_count": 0},
            ],
            "year_fetch_diagnostics": {"histogram_total": 91, "seen_total": 93},
            "citations": partial_citations,
        }

    def _save_and_load(self, state, with_snapshot, mid_snapshot=None, pub=None):
        """Persist one paper via _save_output and return (entry, partial_citations).

        The temporary directory survives the call so the caller can reload the
        written output (see the round-trip test below).
        """
        partial_citations = [
            {"title": f"New-2026-{idx}", "authors": "A", "venue": "V", "year": "2026", "url": f"u{idx}"}
            for idx in range(3)
        ]
        pub = dict(pub or self._INTERRUPTED_PUB)
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        self.fetcher.profile_json = os.path.join(tmpdir, "author_test-author_profile.json")
        self.fetcher.out_json = os.path.join(tmpdir, "author_test-author_paper_citations.json")
        self.fetcher.out_xlsx = os.path.join(tmpdir, "author_test-author_paper_citations.xlsx")
        self.fetcher._run_start_time = 0
        with open(self.fetcher.profile_json, "w", encoding="utf-8") as f:
            json.dump({"publications": [pub]}, f)
        self.fetcher._paper_states = {
            "Interrupted Paper": PaperFetchState.from_dict(state),
        }
        if with_snapshot:
            if mid_snapshot is None:
                mid_snapshot = self._mid_paper_snapshot(partial_citations)
            self.fetcher._mid_paper_state = {"Interrupted Paper": mid_snapshot}
        else:
            self.fetcher._mid_paper_state = {}

        with patch("sys.stdout", new_callable=StringIO):
            self.fetcher._save_output([None])

        with open(self.fetcher.out_json, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload["papers"][0], partial_citations

    def test_save_output_keeps_interrupted_run_progress(self):
        """The in-run snapshot must win: it holds the live probe (so 2026 stays
        visibly short) and the citations fetched before the interrupt.  Without
        it the stale start-of-run state would persist fetch_complete=True and
        the un-fetched citations would never be resumed."""
        paper, partial_citations = self._save_and_load(
            self._stale_complete_state(), with_snapshot=True)
        self.assertFalse(paper["fetch_complete"])
        self.assertIs(paper["_fetch_state"]["complete_fetch_attempt"], False)
        self.assertEqual([c["title"] for c in paper["citations"]],
                         [c["title"] for c in partial_citations])
        records = {r["year"]: r for r in paper["_fetch_state"]["year_records"]}
        self.assertEqual(records[2026]["histogram_count"], 5)
        self.assertEqual(records[2026]["seen_total"], 4)

    def test_save_output_uses_stale_state_without_in_run_snapshot(self):
        """Baseline for the test above: with no in-run snapshot the stale state
        is persisted as complete, hiding the short year."""
        paper, _ = self._save_and_load(self._stale_complete_state(), with_snapshot=False)
        self.assertTrue(paper["fetch_complete"])
        records = {r["year"]: r for r in paper["_fetch_state"]["year_records"]}
        self.assertEqual(records[2026]["histogram_count"], 4)

    def test_save_output_persists_stall_counters_alongside_in_run_snapshot(self):
        """Stall counters must survive a save that uses the in-run snapshot.

        save_progress() builds the snapshot without stall fields and the counters
        are recorded on _paper_states after the fetch, so persisting the snapshot
        verbatim reset stall_count to 0 on every run and `stalled` could never be
        reached.  The signature is taken from the snapshot-derived state (the
        live probe is what the next run compares against), so this also pins that
        it does not drift across the write/read cycle.
        """
        snapshot = self._mid_paper_snapshot([
            {"title": f"New-2026-{idx}", "authors": "A", "venue": "V",
             "year": "2026", "url": f"u{idx}"}
            for idx in range(3)
        ])
        signature = PaperFetchState.from_dict(snapshot).current_outcome_signature()
        self.assertTrue(signature)
        state = self._stale_complete_state()
        state["stall_signature"] = signature
        state["stall_count"] = 2

        paper, _ = self._save_and_load(state, with_snapshot=True, mid_snapshot=snapshot)

        saved = paper["_fetch_state"]
        self.assertEqual(saved["stall_signature"], signature)
        self.assertEqual(saved["stall_count"], 2)
        reloaded = PaperFetchState.from_dict(saved)
        self.assertTrue(reloaded.is_stalled())

    def test_save_output_ignores_direct_snapshot_that_lacks_diagnostics(self):
        """A direct-mode mid-fetch save has no fetch_strategy/direct diagnostics
        yet; letting it replace the persisted state would infer year mode and
        report complete for a paper still missing citations."""
        state = {
            "title": "Interrupted Paper",
            "fetch_strategy": "direct",
            "num_citations_on_scholar": 45,
            "complete_fetch_attempt": True,
            "direct_fetch_diagnostics": {
                "scholar_total": 45, "cached_total": 30, "seen_total": 30,
                "dedup_count": 0, "termination_reason": "in_progress",
            },
        }
        stale_partial = {
            "title": "Interrupted Paper",
            "num_citations_on_scholar": 45,
            "complete_fetch_attempt": False,
            "fetch_strategy": None,
            "direct_fetch_diagnostics": None,
            "year_fetch_diagnostics": {
                "histogram_total": 30, "seen_total": 30, "scholar_total": 45,
            },
            "citations": [],
        }
        pub = dict(self._INTERRUPTED_PUB, num_citations=45)
        paper, _ = self._save_and_load(
            state, with_snapshot=True, mid_snapshot=stale_partial, pub=pub)
        self.assertFalse(paper["fetch_complete"])
        self.assertEqual(paper["_fetch_state"]["fetch_strategy"], "direct")
        self.assertEqual(
            paper["_fetch_state"]["direct_fetch_diagnostics"]["seen_total"], 30)

    def test_interrupted_paper_is_partial_and_plannable_on_next_run(self):
        """Round trip for the reported bug: after an interrupted run the reloaded
        state must be 'partial' and show a real gap in the fetch plan, so the
        un-fetched citations are resumed instead of silently dropped."""
        from crawler.output_state import load_paper_states, to_paper_fetch_state
        _, _ = self._save_and_load(self._stale_complete_state(), with_snapshot=True)

        next_run_states = load_paper_states(self.fetcher.out_json)
        self.fetcher._paper_states = next_run_states

        pub = dict(self._INTERRUPTED_PUB)
        self.assertEqual(self.fetcher._citation_status(pub), "partial")

        state = next_run_states[pub["title"]]
        pst = to_paper_fetch_state(state)
        cached = pst.to_dict()
        cached["citations"] = list(state.citations)
        needed, seen, target, short_years = self.fetcher._plan_fetch_gap(
            "year", cached, pub["num_citations"], len(state.citations))
        self.assertEqual(needed, 1)
        self.assertEqual(short_years, "2026(4/5)")
        self.assertEqual([c["title"] for c in state.citations][:1], ["New-2026-0"])


if __name__ == '__main__':
    unittest.main()
