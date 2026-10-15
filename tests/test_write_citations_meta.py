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


class PreserveStageTwoFieldsTests(unittest.TestCase):
    """阶段 1 开始写主体后，**不得让阶段 2 丢状态** ✓（Y-7：只动自己产生的数据 ✓）。"""

    def test_existing_download_fields_are_carried_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, META_NAME)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump([{"index": 1, "cites_id": "c1", "title": "T",
                            "download_status": "downloaded", "local_filename": "0001-T.pdf",
                            "fail_reason": None, "size_bytes": 999},
                           {"index": 2, "cites_id": "c2", "download_status": "failed"}], handle)
            write_citations_meta(tmp, [{"index": 1, "cites_id": "c1", "title": "T"},
                                       {"index": 2, "cites_id": "c2", "title": "U"}])
            saved = {e["index"]: e for e in json.load(open(path, encoding="utf-8"))}
            self.assertEqual(saved[1]["download_status"], "downloaded", "阶段 2 的状态必须原样保留 ✓")
            self.assertEqual(saved[1]["local_filename"], "0001-T.pdf")
            self.assertEqual(saved[1]["size_bytes"], 999)
            self.assertEqual(saved[2]["download_status"], "failed")
            # 阶段 1 自己的字段仍然以**抓取侧**为准 ✓（不被旧值覆盖 ✗）
            self.assertEqual(saved[2]["title"], "U")

    def test_no_existing_file_means_a_pure_stage_one_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_citations_meta(tmp, [{"index": 1, "title": "T", "download_status": "x"}])
            with open(os.path.join(tmp, META_NAME), encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), [{"index": 1, "title": "T"}],
                                 "新文件里不该有下载字段 ✗")


class RetiredEntriesAndPubEntryPointTests(unittest.TestCase):

    def test_entries_missing_from_the_new_input_are_kept(self):
        """上游消失 = 退役 ✓，但一次重写**不得**让盘上的记录凭空消失 ✗。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, META_NAME)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump([{"index": 1, "title": "Old"}, {"index": 5, "title": "Retired"}], handle)
            write_citations_meta(tmp, [{"index": 1, "title": "New"}])
            saved = json.load(open(path, encoding="utf-8"))
            self.assertEqual([e["index"] for e in saved], [1, 5], "退役条目应保留 ✓")
            self.assertEqual(saved[0]["title"], "New", "在册条目按抓取侧更新 ✓")
            self.assertEqual(saved[1]["title"], "Retired", "退役条目原样保留 ✓")

    def test_pub_entry_point_reuses_the_layout_and_never_creates_a_dir(self):
        from code.citation_index import write_citations_meta_for_pub
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(write_citations_meta_for_pub(tmp, {"no": None}, [{"index": 1}]), 0)
            self.assertEqual(write_citations_meta_for_pub(tmp, {"no": 3, "title": "X"},
                                                          [{"index": 1, "title": "T"}]), 0,
                             "目录不存在 → 只报告不创建 ✗")
            self.assertEqual(len(os.listdir(tmp)), 0, "不该创建任何目录/文件 ✗")
