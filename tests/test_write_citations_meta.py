"""阶段 1 写 `citations_meta.json` **主体**（Y-9）：字段筛选、排序、幂等、不写下载字段。"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from code.citation_index import META_NAME, meta_entry, write_citations_meta  # noqa: E402


class MetaEntryTests(unittest.TestCase):

    def test_only_stage_one_fields_survive(self):
        entry = meta_entry({"index": 7, "cites_id": "c1", "title": "T", "year": "2020",
                            "download_status": "downloaded", "local_filename": "0007-x.pdf",
                            "fail_reason": "403", "size_bytes": 12})
        self.assertEqual(set(entry), {"index", "cites_id", "title", "year"})
        for field in ("download_status", "local_filename", "fail_reason", "size_bytes"):
            self.assertNotIn(field, entry, "下载字段归阶段 2 ✗")

    def test_empty_values_are_omitted_and_no_index_is_not_allocated(self):
        self.assertEqual(meta_entry({"index": 3, "title": "T", "url": "", "venue": None}), 
                         {"index": 3, "title": "T"})
        self.assertNotIn("index", meta_entry({"title": "T"}))


class WriteCitationsMetaTests(unittest.TestCase):

    def _read(self, paper_dir):
        with open(os.path.join(paper_dir, META_NAME), encoding="utf-8") as handle:
            return json.load(handle)

    def test_writes_in_index_order_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            citations = [{"index": 9, "title": "Nine"}, {"index": 2, "title": "Two"},
                         {"title": "no index"}]
            self.assertEqual(write_citations_meta(tmp, citations), 2)
            self.assertEqual([e["index"] for e in self._read(tmp)], [2, 9])
            self.assertEqual(write_citations_meta(tmp, citations), 0, "内容没变不该重写 ✓")
            before = os.path.getmtime(os.path.join(tmp, META_NAME))
            write_citations_meta(tmp, citations)
            self.assertEqual(os.path.getmtime(os.path.join(tmp, META_NAME)), before, "不得重写 ✓")

    def test_no_index_means_no_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(write_citations_meta(tmp, [{"title": "T"}]), 0)
            self.assertFalse(os.path.isfile(os.path.join(tmp, META_NAME)), "不该造空 meta ✗")

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(write_citations_meta(tmp, [{"index": 1, "title": "T"}], dry_run=True), 1)
            self.assertFalse(os.path.isfile(os.path.join(tmp, META_NAME)))


if __name__ == "__main__":
    unittest.main()
