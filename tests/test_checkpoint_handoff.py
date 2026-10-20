#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段 1 的阶段性交接（2026-10-19 用户要求：每处理完一篇就全部落盘）。

钉住三件事：
1. 每篇完成后写 `citations_meta.json`（带 index）、cite 文件与 `partial` 产物；
2. partial 产物里的 `pending_papers` 只含尚未完成的论文；
3. 重复调用幂等，不重复写 / 不丢条目。
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# `tests.conftest` installs the `scholarly` / `openpyxl` stubs and MUST be
# imported before anything imports `code.*`: otherwise the real packages win
# `sys.modules`, `FetcherTestCase` writes its DummyNav into an object no code
# reads, and every patch-layer/xlsx test fails with whatever the real
# Navigator/Workbook happens to contain (the module alone still passes).
import tests.conftest  # noqa: E402,F401

import code.scholar_citation as SC  # noqa: E402
from code.config import papers_root  # noqa: E402
from code.profile_io import AuthorProfile  # noqa: E402
from code.shared.papers_layout import target_dir_name  # noqa: E402


def _pub(no, title):
    return {"no": no, "title": title, "authors": "A B", "venue": "ACL",
            "year": "2024", "url": "https://example.com/%d" % no,
            "num_citations": 2}


def _cit(i):
    return {"title": "Citation %d" % i, "authors": "C D", "venue": "ACL",
            "year": "2024", "url": "https://a.example.com/%d" % i,
            "cites_id": "cid-%d" % i}


class TestCheckpointHandoff(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fetcher = SC.PaperCitationFetcher("TEST", output_dir=self.tmp,
                                               cite_formats=False)
        self.fetcher._checkpoint_enabled = True
        self.p1, self.p2 = _pub(1, "Paper One"), _pub(2, "Paper Two")
        self.fetcher._profile_data = {
            "fetch_time": "2026-10-19T00:00:00", "publications": [self.p1, self.p2]}
        self.fetcher._checkpoint_publications = [self.p1, self.p2]
        self.fetcher._checkpoint_results = [None, None]
        self.root = papers_root(self.tmp, "TEST")
        for p in (self.p1, self.p2):
            os.makedirs(os.path.join(self.root, target_dir_name(p)), exist_ok=True)

    def _partial(self):
        with open(self.fetcher.partial_json, encoding="utf-8") as f:
            return json.load(f)

    def test_writes_meta_cite_and_partial_after_each_paper(self):
        self.fetcher._checkpoint_results[0] = {"pub": self.p1,
                                               "citations": [_cit(1), _cit(2)]}
        self.fetcher._checkpoint_paper(self.p1, [_cit(1), _cit(2)])
        meta_path = os.path.join(self.root, target_dir_name(self.p1),
                                 "citations_meta.json")
        self.assertTrue(os.path.isfile(meta_path))
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        self.assertEqual([r["index"] for r in meta], [1, 2])
        partial = self._partial()
        self.assertTrue(partial["partial"])
        self.assertEqual(len(partial["papers"]), 2, "完整 schema：未处理的论文也在")
        self.assertEqual(partial["completed_papers"], [1])
        self.assertEqual(partial["generation"], "2026-10-19T00:00:00")

        self.fetcher._checkpoint_results[1] = {"pub": self.p2,
                                               "citations": [_cit(3)]}
        self.fetcher._checkpoint_paper(self.p2, [_cit(3)])
        partial = self._partial()
        self.assertEqual(len(partial["papers"]), 2)
        self.assertEqual(sorted(partial["completed_papers"]), [1, 2])

    def test_paper_order_changes_are_reported(self):
        old = AuthorProfile(publications=[
            {"no": 1, "title": "Paper One", "url": "https://a/1"},
            {"no": 2, "title": "Paper Two", "url": "https://a/2"},
        ])
        new = AuthorProfile(publications=[
            {"no": 1, "title": "Paper Two", "url": "https://a/2"},
            {"no": 2, "title": "Paper One", "url": "https://a/1"},
            {"no": 3, "title": "Paper Three", "url": "https://a/3"},
        ])
        changed, added, removed = SC.paper_order_changes(old, new)
        self.assertEqual([(o, n, t) for o, n, t in changed],
                         [(2, 1, "Paper Two"), (1, 2, "Paper One")])
        self.assertEqual(added, ["Paper Three"])
        self.assertEqual(removed, [])

    def test_order_change_matches_by_url_when_title_changed(self):
        old = AuthorProfile(publications=[
            {"no": 7, "title": "Old Title", "url": "https://a/7"},
        ])
        new = AuthorProfile(publications=[
            {"no": 9, "title": "New Title", "url": "https://a/7"},
        ])
        changed, added, removed = SC.paper_order_changes(old, new)
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0][:2], (7, 9))
        self.assertEqual(added, [])
        self.assertEqual(removed, [])

    def test_baseline_writes_all_papers_and_preserves_fetch_state(self):
        with open(self.fetcher.out_json, "w", encoding="utf-8") as f:
            json.dump({"author_id": "TEST", "papers": [
                {"pub": self.p1, "citations": [_cit(1)],
                 "_fetch_state": {"fetch_strategy": "direct"}}]}, f)
        self.fetcher.write_baseline()
        with open(self.fetcher.out_json, encoding="utf-8") as f:
            payload = json.load(f)
        self.assertTrue(payload["baseline"])
        self.assertEqual(len(payload["papers"]), 2)
        self.assertEqual(payload["papers"][0]["_fetch_state"],
                         {"fetch_strategy": "direct"})
        self.assertEqual(len(payload["papers"][1]["citations"]), 0)

    def test_dedup_happens_once_at_result_assignment(self):
        dup = [_cit(1), dict(_cit(1))]          # same cites_id → one row
        results = [None, None]
        with mock.patch.object(self.fetcher, "_dedup_citations_for_output",
                               wraps=self.fetcher._dedup_citations_for_output) as dedup:
            self.fetcher._store_result(results, 1, self.p1, dup)
            self.fetcher._checkpoint_paper(self.p1, results[0]["citations"])
            self.fetcher._checkpoint_results = results
            self.fetcher._write_partial_product()
        self.assertEqual(dedup.call_count, 1,
                         "checkpoint / partial 不应再次去重")
        self.assertEqual(len(results[0]["citations"]), 1)

    def test_final_save_only_writes_updated_papers(self):
        self.fetcher._baseline_titles = {"Paper One", "Paper Two"}
        self.fetcher._updated_titles = {"Paper One"}
        self.fetcher._dirty_titles = {"Paper One"}
        self.fetcher._enrich_cite_formats = lambda *a, **k: None
        self.fetcher._save_xlsx = lambda *a, **k: None
        results = [
            {"pub": self.p1, "citations": [_cit(1)]},
            {"pub": self.p2, "citations": [_cit(2)]},
        ]
        with mock.patch.object(SC, "write_cite_files", return_value=(0, 0, 0)) as cite, \
             mock.patch.object(SC, "write_citations_meta_for_pub") as meta:
            self.fetcher._save_output(results)
        self.assertEqual([c.args[1]["title"] for c in cite.call_args_list],
                         ["Paper One"])
        self.assertEqual([c.args[1]["title"] for c in meta.call_args_list],
                         ["Paper One"])

    def test_final_save_marks_checkpoint_complete(self):
        self.fetcher._checkpoint_results = [
            {"pub": self.p1, "citations": [_cit(1)]},
            {"pub": self.p2, "citations": [_cit(2)]},
        ]
        self.fetcher._enrich_cite_formats = lambda *a, **k: None
        self.fetcher._save_xlsx = lambda *a, **k: None
        results = [
            {"pub": self.p1, "citations": [_cit(1)]},
            {"pub": self.p2, "citations": [_cit(2)]},
        ]
        self.fetcher._save_output(results)
        partial = self._partial()
        self.assertFalse(partial["partial"])
        self.assertEqual(len(partial["papers"]), 2)
        self.assertEqual(sorted(partial["completed_papers"]), [1, 2])

    def test_the_product_is_written_before_the_cite_dialog_step(self):
        """先写产物，再抓 Cite 对话框：对话框那一步被打断不该连引用一起丢。

        2026-10-20 实测：一次二段 Ctrl+C 让 9 MB 产物停在上一轮，而抓到的引用其实都在
        内存里 —— 因为旧顺序把最长的那一步放在了写 JSON 之前。
        """
        self.fetcher.cite_formats = True
        results = [
            {"pub": self.p1, "citations": [_cit(1)]},
            {"pub": self.p2, "citations": [_cit(2)]},
        ]
        self.fetcher._save_xlsx = lambda *a, **k: None

        def boom(*a, **k):
            raise KeyboardInterrupt

        self.fetcher._enrich_cite_formats = boom
        with mock.patch.object(SC, "write_cite_files", return_value=(0, 0, 0)), \
             mock.patch.object(SC, "write_citations_meta_for_pub"):
            self.fetcher._save_output(results)
        with open(self.fetcher.out_json, encoding="utf-8") as f:
            payload = json.load(f)
        self.assertEqual([len(p["citations"]) for p in payload["papers"]], [1, 1])

    def test_an_interrupt_says_what_is_actually_on_disk(self):
        """中断后的报告只说事实：写到哪一步、产物是哪个时间、快照覆盖几篇。"""
        from io import StringIO
        self.fetcher._save_completed = False
        self.fetcher._save_stage = "json"
        with mock.patch("sys.stdout", new_callable=StringIO) as out:
            self.fetcher._report_saved_state()
        text = out.getvalue()
        self.assertIn("Interrupted — what is on disk now:", text)
        self.assertIn(self.fetcher.out_json, text)
        self.assertIn(self.fetcher.partial_json, text)
        self.assertIn("saving stopped at 'json'", text)
        self.assertNotIn("written completely", text)

    def test_a_save_interrupted_after_the_product_says_the_product_is_there(self):
        """产物已经写了、只是没重写成"带本轮对话框"的版本时，不能说"落后一轮"。

        2026-10-20 实测：`_save_output` 先写 JSON，之后跑 Cite 对话框时被打断，报告却写
        "the product may be one run behind" —— 与磁盘上的事实相反。
        """
        from io import StringIO
        self.fetcher._save_completed = False
        self.fetcher._save_stage = "json"
        self.fetcher._save_json_written = True
        with mock.patch("sys.stdout", new_callable=StringIO) as out:
            self.fetcher._report_saved_state()
        text = out.getvalue()
        self.assertIn("the product was written at", text)
        self.assertIn("Cite dialogs", text)
        self.assertNotIn("may be one run behind", text)

    def test_a_completed_save_is_reported_as_complete(self):
        from io import StringIO
        self.fetcher._save_completed = True
        self.fetcher._save_stage = "done"
        with mock.patch("sys.stdout", new_callable=StringIO) as out:
            self.fetcher._report_saved_state()
        self.assertIn("written completely", out.getvalue())

    def test_resume_state_prefers_the_newer_snapshot(self):
        """被中断的那一轮只更新了 partial：下次要以它续跑，否则已抓的论文会被重抓。"""
        with open(self.fetcher.out_json, "w", encoding="utf-8") as f:
            json.dump({"author_id": "TEST", "papers": []}, f)
        os.utime(self.fetcher.out_json, (1000, 1000))
        with open(self.fetcher.partial_json, "w", encoding="utf-8") as f:
            json.dump({"author_id": "TEST", "papers": []}, f)
        os.utime(self.fetcher.partial_json, (2000, 2000))
        self.assertEqual(self.fetcher._resume_product_path(),
                         self.fetcher.partial_json)

    def test_an_older_snapshot_is_ignored(self):
        with open(self.fetcher.partial_json, "w", encoding="utf-8") as f:
            json.dump({"author_id": "TEST", "papers": []}, f)
        os.utime(self.fetcher.partial_json, (1000, 1000))
        with open(self.fetcher.out_json, "w", encoding="utf-8") as f:
            json.dump({"author_id": "TEST", "papers": []}, f)
        os.utime(self.fetcher.out_json, (2000, 2000))
        self.assertEqual(self.fetcher._resume_product_path(),
                         self.fetcher.out_json)

    def test_is_idempotent_and_does_not_drop_previous_entries(self):
        self.fetcher._checkpoint_results[0] = {"pub": self.p1,
                                               "citations": [_cit(1)]}
        self.fetcher._checkpoint_paper(self.p1, [_cit(1)])
        self.fetcher._checkpoint_paper(self.p1, [_cit(1)])
        self.fetcher._checkpoint_results[1] = {"pub": self.p2,
                                               "citations": [_cit(2)]}
        self.fetcher._checkpoint_paper(self.p2, [_cit(2)])
        partial = self._partial()
        self.assertEqual(len(partial["papers"]), 2)
        self.assertEqual(sorted(partial["completed_papers"]), [1, 2])


if __name__ == "__main__":
    unittest.main()
