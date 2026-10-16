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

    def test_venue_key_is_recomputed_not_copied(self):
        """**派生字段重算** ✓：条目里那份（可能来自旧一代缓存）不作数。

        代价实证（2026-10-15）：照抄会让"补过 key 的存量"被重抓静默抹掉
        （阶段 2 `backfill_venue_key --apply` 补的 7,901 条就这么没的）。
        """
        entry = meta_entry({"index": 1, "cites_id": "c1", "title": "T", "venue": "ACL 2024",
                            "year": "2024", "venue_key": "STALE", "venue_key_source": "raw"})
        self.assertEqual(entry["venue_key"], "ACL2024")
        self.assertEqual(entry["venue_key_source"], "normalized")

    def test_venue_key_prefers_the_cite_dialog_venue(self):
        entry = meta_entry({"index": 1, "cites_id": "c1", "title": "T", "year": "2024",
                            "venue": "Proceedings of the …",
                            "cite": {"venue_text": "Proceedings of the 2024 Conference on "
                                                   "Empirical Methods in Natural Language Processing"}})
        self.assertEqual(entry["venue_key"], "EMNLP2024")
        self.assertEqual(entry["venue_key_source"], "normalized")

    def test_missing_venue_means_no_key_at_all(self):
        entry = meta_entry({"index": 4, "cites_id": "c4", "title": "T", "venue": "N/A",
                            "venue_key": "STALE", "venue_key_source": "normalized"})
        self.assertNotIn("venue_key", entry, "占位 venue 不给 key ✗（更不该继承旧值 ✗）")
        self.assertNotIn("venue_key_source", entry)


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


class VenueKeyIsDerivedAndNeverDowngradedTests(unittest.TestCase):
    """`venue_key*` 是派生字段：**重算 + 永不降级**（2026-10-15）。"""

    def _read(self, paper_dir):
        with open(os.path.join(paper_dir, META_NAME), encoding="utf-8") as handle:
            return json.load(handle)

    def _seed(self, paper_dir, rows):
        with open(os.path.join(paper_dir, META_NAME), "w", encoding="utf-8") as handle:
            json.dump(rows, handle)

    def test_a_truncated_row_keeps_the_normalized_key_already_on_disk(self):
        """列表页被截断（重算只能给 raw）时，盘上同一条引用的 normalized 必须保住 ✓。"""
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(tmp, [{"index": 1, "cites_id": "c1", "title": "T", "year": "2024",
                              "venue": "Proceedings of the …",
                              "venue_key": "EMNLP2024", "venue_key_source": "normalized"}])
            write_citations_meta(tmp, [{"index": 1, "cites_id": "c1", "title": "T", "year": "2024",
                                        "venue": "Proceedings of the …"}])
            entry = self._read(tmp)[0]
            self.assertEqual(entry["venue_key"], "EMNLP2024")
            self.assertEqual(entry["venue_key_source"], "normalized")
            self.assertEqual(write_citations_meta(tmp, [{"index": 1, "cites_id": "c1", "title": "T",
                                                         "year": "2024",
                                                         "venue": "Proceedings of the …"}]), 0,
                             "重算 + 保底之后必须幂等 ✓")

    def test_a_different_work_at_the_same_index_inherits_nothing(self):
        """编号会被复用（K123/K128）：**别的**作品不许继承盘上那条的 key ✗。"""
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(tmp, [{"index": 1, "cites_id": "old", "title": "Old work", "year": "2024",
                              "venue": "Journal of Obscure Studies 1, 2019",
                              "venue_key": "EMNLP2024", "venue_key_source": "normalized"}])
            write_citations_meta(tmp, [{"index": 1, "cites_id": "new", "title": "New work",
                                        "year": "2024", "venue": "Journal of Obscure Studies 1, 2019"}])
            entry = self._read(tmp)[0]
            self.assertEqual(entry["title"], "New work")
            self.assertEqual(entry["venue_key"], "Journal of Obscure Studies 1, 2019")
            self.assertEqual(entry["venue_key_source"], "raw")

    def test_a_raw_key_on_disk_is_upgraded_by_the_recompute(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(tmp, [{"index": 1, "cites_id": "c1", "title": "T", "year": "2024",
                              "venue": "ACL 2024", "venue_key": "ACL 2024",
                              "venue_key_source": "raw"}])
            write_citations_meta(tmp, [{"index": 1, "cites_id": "c1", "title": "T", "year": "2024",
                                        "venue": "ACL 2024"}])
            entry = self._read(tmp)[0]
            self.assertEqual(entry["venue_key"], "ACL2024")
            self.assertEqual(entry["venue_key_source"], "normalized")

    def test_the_whole_library_can_be_rewritten_without_losing_keys(self):
        """回归（2026-10-15 的真实事故）：重抓（=用缓存行重写 meta）**不得**抹掉 key ✓。"""
        with tempfile.TemporaryDirectory() as tmp:
            disk = [{"index": i, "cites_id": "c%d" % i, "title": "T%d" % i, "year": "2024",
                     "venue": "Proceedings of the …",
                     "venue_key": "EMNLP2024", "venue_key_source": "normalized"}
                    for i in range(1, 6)]
            self._seed(tmp, disk)
            # 重抓：条目只带列表页 venue（缓存恢复的行没有 cite）
            write_citations_meta(tmp, [{"index": i, "cites_id": "c%d" % i, "title": "T%d" % i,
                                        "year": "2024", "venue": "Proceedings of the …"}
                                       for i in range(1, 6)])
            saved = self._read(tmp)
            self.assertEqual([e["venue_key"] for e in saved], ["EMNLP2024"] * 5)
            self.assertTrue(all(e["venue_key_source"] == "normalized" for e in saved))


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
                rows = json.load(handle)
            self.assertEqual(rows[0]["index"], 1)
            self.assertEqual(rows[0]["title"], "T")
            for field in ("download_status", "local_filename", "fail_reason", "size_bytes"):
                self.assertNotIn(field, rows[0], "新文件里不该有下载字段 ✗")
            # K144（2026-10-16）：没有真实 `cites_id` 的行现在带上**共享件算出的合成键** ✓ ——
            # 主体归阶段 1 之后，键也得由它产出（`code/shared/citation_key.py`，键只有那一份 ✓）。
            from code.shared.citation_key import synthetic_cites_id
            self.assertEqual(rows[0]["cites_id"], synthetic_cites_id({"title": "T"}))

    def test_rows_without_a_real_id_get_the_shared_synthetic_key(self):
        """id-less 行补合成键：同基础键的多条按 `#doi:` / `#url:` 消歧（与阶段 2 同一份实现 ✓）。"""
        with tempfile.TemporaryDirectory() as tmp:
            write_citations_meta(tmp, [
                {"index": 1, "title": "Same Title", "year": "2024", "authors": "X Li",
                 "url": "https://doi.org/10.1234/abc"},
                {"index": 2, "title": "Same Title", "year": "2024", "authors": "X Li",
                 "url": "https://arxiv.org/abs/2401.00001"},
                {"index": 3, "title": "Same Title", "year": "2024", "authors": "X Li",
                 "url": "https://example.org/it", "cites_id": "real-id-3"},
            ])
            with open(os.path.join(tmp, META_NAME), encoding="utf-8") as handle:
                rows = {r["index"]: r for r in json.load(handle)}
            self.assertTrue(rows[1]["cites_id"].startswith("no_id:sametitle|2024|li#doi:"))
            self.assertTrue(rows[2]["cites_id"].startswith("no_id:sametitle|2024|li#arxiv:"))
            self.assertEqual(rows[3]["cites_id"], "real-id-3", "有真实 id 的一字不动 ✓")


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
