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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# `tests.conftest` installs the `scholarly` / `openpyxl` stubs and MUST be
# imported before anything imports `code.*`: otherwise the real packages win
# `sys.modules`, `FetcherTestCase` writes its DummyNav into an object no code
# reads, and every patch-layer/xlsx test fails with whatever the real
# Navigator/Workbook happens to contain (the module alone still passes).
import tests.conftest  # noqa: E402,F401

import code.scholar_citation as SC  # noqa: E402
from code.config import papers_root  # noqa: E402
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
