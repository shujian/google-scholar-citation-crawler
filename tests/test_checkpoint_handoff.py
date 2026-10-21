#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段 1 的阶段性交接（每处理完一篇就落盘）。

钉住四件事：
1. 每篇完成后写该篇的 `citations_meta.json`（带 index）与 cite 文件；
2. **不写 `*.partial.json` 全量快照**（见 AGENTS.md）；
3. 重复调用幂等，不重复写 / 不丢条目；
4. 中断报告按"产物写没写"分开措辞。
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

    def test_writes_meta_after_each_paper(self):
        """每篇完成就写该篇的 `citations_meta.json`（不写 *.partial.json 全量快照）。

        不写 partial 快照：下游没有人读它，而它不带 `_fetch_state`，当不了续跑账本，
        代价却是每篇重写 ~10 MB。
        """
        self.fetcher._checkpoint_results[0] = {"pub": self.p1,
                                               "citations": [_cit(1), _cit(2)]}
        self.fetcher._checkpoint_paper(self.p1, [_cit(1), _cit(2)])
        meta_path = os.path.join(self.root, target_dir_name(self.p1),
                                 "citations_meta.json")
        self.assertTrue(os.path.isfile(meta_path))
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        self.assertEqual([r["index"] for r in meta], [1, 2])
        self.assertFalse(
            [n for n in os.listdir(self.tmp) if n.endswith(".partial.json")],
            "每篇落盘不再产生 partial 快照")

    def test_each_paper_lands_in_the_product_immediately(self):
        """「按论文条目写入」：一篇抓完，正式产物里那篇就是最新的。

        这样中断（含 kill -9 / 断电）之后磁盘上的产物仍然可用，下一轮直接续跑，
        不会把已经抓过的论文再走一遍。
        """
        with open(self.fetcher.out_json, "w", encoding="utf-8") as f:
            json.dump({"author_id": "TEST", "baseline": True, "papers": [
                {"pub": self.p1, "citations": []},
                {"pub": self.p2, "citations": []}]}, f)

        self.fetcher._paper_states = {"Paper One": {
            "fetch_strategy": "direct", "complete_fetch_attempt": True}}
        self.fetcher._checkpoint_paper(self.p1, [_cit(1), _cit(2)])

        with open(self.fetcher.out_json, encoding="utf-8") as f:
            payload = json.load(f)
        by_title = {p["pub"].get("title"): p for p in payload["papers"]}
        self.assertEqual(len(by_title), 2, "产物里仍然有全部论文")
        self.assertEqual(len(by_title["Paper One"]["citations"]), 2)
        self.assertEqual(len(by_title["Paper Two"]["citations"]), 0, "别的论文一个字节都不动")
        self.assertEqual(by_title["Paper One"]["_fetch_state"]["fetch_strategy"], "direct")
        self.assertTrue(payload["baseline"], "抓取期间仍是 baseline 标记")
        self.assertEqual(payload["total_citations"], 2)

    def test_the_product_write_is_idempotent_per_paper(self):
        with open(self.fetcher.out_json, "w", encoding="utf-8") as f:
            json.dump({"author_id": "TEST", "papers": [
                {"pub": self.p1, "citations": []},
                {"pub": self.p2, "citations": []}]}, f)
        self.fetcher._paper_states = {}
        self.fetcher._checkpoint_paper(self.p1, [_cit(1)])
        self.fetcher._checkpoint_paper(self.p1, [_cit(1)])   # 同一篇再调一次
        with open(self.fetcher.out_json, encoding="utf-8") as f:
            payload = json.load(f)
        counts = {p["pub"].get("title"): len(p["citations"]) for p in payload["papers"]}
        self.assertEqual(counts, {"Paper One": 1, "Paper Two": 0})

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
        self.assertEqual(dedup.call_count, 1,
                         "checkpoint 不应再次去重")
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

    def test_final_save_writes_the_product_and_no_snapshot(self):
        self.fetcher._enrich_cite_formats = lambda *a, **k: None
        self.fetcher._save_xlsx = lambda *a, **k: None
        results = [
            {"pub": self.p1, "citations": [_cit(1)]},
            {"pub": self.p2, "citations": [_cit(2)]},
        ]
        self.fetcher._save_output(results)
        with open(self.fetcher.out_json, encoding="utf-8") as f:
            payload = json.load(f)
        self.assertEqual(len(payload["papers"]), 2)
        self.assertFalse(
            [n for n in os.listdir(self.tmp) if n.endswith(".partial.json")])

    def test_the_product_is_written_before_the_cite_dialog_step(self):
        """先写产物，再抓 Cite 对话框：对话框那一步被打断不该连引用一起丢。

        二段 Ctrl+C 可能让保存停在写 JSON 之前，而抓到的引用其实都在内存里；因此把
        最长的那一步放在写 JSON 之后。
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
        """中断后的报告只说事实：写到哪一步、产物与缓存是哪个时间。"""
        from io import StringIO
        self.fetcher._save_completed = False
        self.fetcher._save_stage = "json"
        with mock.patch("sys.stdout", new_callable=StringIO) as out:
            self.fetcher._report_saved_state()
        text = out.getvalue()
        self.assertIn("Interrupted — what is on disk now:", text)
        self.assertIn(self.fetcher.out_json, text)
        self.assertIn("cite cache:", text)
        self.assertIn("saving stopped at 'json'", text)
        self.assertNotIn("written completely", text)

    def test_a_save_interrupted_after_the_product_says_the_product_is_there(self):
        """产物已经写了、只是没重写成"带本轮对话框"的版本时，不能说"落后一轮"。

        `_save_output` 先写 JSON，之后跑 Cite 对话框时被打断；此时报告若写
        "the product may be one run behind"，就与磁盘上的事实相反。
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

if __name__ == "__main__":
    unittest.main()
