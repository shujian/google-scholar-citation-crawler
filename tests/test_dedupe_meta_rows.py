#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`dedupe_meta_rows`：清掉同一 `cites_id` 的两行（编号漂移残留）。

来历（2026-10-19）：阶段 1 写者按 `index` 合并旧行，同一作品换号后旧行被当"退役条目"
留下 → `citations_meta.json` 里同一 `cites_id` 两行，阶段 2 的下载字段还挂在没有文件的
那行上。写者已修；本工具修存量。
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from code.citation_index import META_NAME  # noqa: E402
from code.data_upgrade import dedupe_meta_rows as D  # noqa: E402


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class TestDedupeMetaRows(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.papers = os.path.join(self.root, "papers")
        self.paper = os.path.join(self.papers, "001-A")
        os.makedirs(self.paper)
        self._product([{"index": 1434, "cites_id": "c1", "title": "T"}])

    def _product(self, citations):
        _write(os.path.join(self.root, "output", "author_X_paper_citations.json"),
               {"papers": [{"pub": {"no": 1, "title": "A"}, "citations": citations}]})

    def _meta(self, rows):
        _write(os.path.join(self.paper, META_NAME), rows)

    def test_same_cid_new_index_keeps_one_row_and_migrates_fields(self):
        self._meta([
            {"index": 1388, "cites_id": "c1", "title": "T", "download_status": "failed",
             "fail_reason": "Timeout", "local_filename": "1388-T.pdf"},
            {"index": 1434, "cites_id": "c1", "title": "T"},
        ])
        live = D.live_indices([({"no": 1}, [{"index": 1434, "cites_id": "c1"}])])
        planned = D.plan(self.papers, live)
        self.assertIn("001-A", planned)
        rows_after = planned["001-A"]["rows"]
        self.assertEqual([r["index"] for r in rows_after], [1434])
        self.assertEqual(rows_after[0]["download_status"], "failed", "阶段 2 字段要搬给活行")
        self.assertEqual(rows_after[0]["local_filename"], "1388-T.pdf")

    def test_dry_run_writes_nothing(self):
        self._meta([{"index": 1388, "cites_id": "c1", "title": "T"},
                    {"index": 1434, "cites_id": "c1", "title": "T"}])
        before = open(os.path.join(self.paper, META_NAME), "rb").read()
        code = D.main(["--papers-root", self.papers, "--crawl-root", self.root])
        self.assertEqual(code, 0)
        self.assertEqual(open(os.path.join(self.paper, META_NAME), "rb").read(), before)

    def test_apply_backs_up_and_is_idempotent(self):
        self._meta([
            {"index": 1388, "cites_id": "c1", "title": "T", "fail_reason": "Timeout"},
            {"index": 1434, "cites_id": "c1", "title": "T"},
        ])
        backup = os.path.join(self.root, "backup")
        code = D.main(["--papers-root", self.papers, "--crawl-root", self.root,
                       "--apply", "--backup-dir", backup])
        self.assertEqual(code, 0)
        rows = _read(os.path.join(self.paper, META_NAME))
        self.assertEqual([r["index"] for r in rows], [1434])
        self.assertTrue(os.path.isfile(os.path.join(backup, "001-A", META_NAME)))
        manifest = _read(os.path.join(backup, "manifest.json"))
        self.assertEqual(manifest["papers"][0]["dropped"][0]["dropped_index"], 1388)
        # 幂等
        live = D.live_indices([({"no": 1}, [{"index": 1434, "cites_id": "c1"}])])
        self.assertEqual(D.plan(self.papers, live), {})

    def test_no_duplicates_means_no_change(self):
        self._meta([{"index": 1434, "cites_id": "c1", "title": "T"},
                    {"index": 1435, "cites_id": "c2", "title": "U"}])
        live = D.live_indices([({"no": 1}, [{"index": 1434, "cites_id": "c1"},
                                            {"index": 1435, "cites_id": "c2"}])])
        self.assertEqual(D.plan(self.papers, live), {})


if __name__ == "__main__":
    unittest.main()
