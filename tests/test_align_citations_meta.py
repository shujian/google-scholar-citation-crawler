"""阶段 1 的**存量主体对齐**（`code/data_upgrade/align_citations_meta.py`，Y-9 ③）。

判据四条：dry-run 零写入、只重写不一致的（其余字节不变）、**下载字段与退役条目原样保留**、
幂等；另加 `--papers` 选择与"产物里 0 条引文的论文跳过"。
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from code.citation_index import META_NAME  # noqa: E402
from code.data_upgrade import align_citations_meta as AL  # noqa: E402
from code.shared.papers_layout import target_dir_name  # noqa: E402

PUB_A = {"no": 1, "title": "Paper One", "url": "https://example.org/1", "year": "2024"}
PUB_B = {"no": 2, "title": "Paper Two", "url": "https://example.org/2", "year": "2024"}
PUB_EMPTY = {"no": 3, "title": "Paper Three", "url": "https://example.org/3", "year": "2024"}


def _citation(index, **extra):
    row = {"index": index, "title": "T%d" % index, "authors": "X Li, C Ma",
           "year": "2024", "url": "https://example.org/c/%d" % index,
           "venue": "Proceedings of the …", "cites_id": "cid-%d" % index}
    row.update(extra)
    return row


class AlignCitationsMetaTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="align-meta-")
        self.papers = os.path.join(self.tmp, "papers")
        self.backup = os.path.join(self.tmp, "backup")
        os.makedirs(self.papers)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- 夹具 -----------------------------------------------------------
    def paper_dir(self, pub):
        """按**阶段 1 自己的**目录命名函数建目录（不在测试里另写一套规则）。"""
        path = os.path.join(self.papers, target_dir_name(pub))
        os.makedirs(path, exist_ok=True)
        return path

    def write_meta(self, pub, rows):
        path = os.path.join(self.paper_dir(pub), META_NAME)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(rows, handle, ensure_ascii=False, indent=1)
        return path

    def write_product(self, entries):
        out = os.path.join(self.tmp, "output")
        os.makedirs(out, exist_ok=True)
        payload = {"author_id": "X", "total_papers": len(entries),
                   "papers": [{"pub": pub, "citations": rows} for pub, rows in entries]}
        with open(os.path.join(out, "author_X_paper_citations.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        return self.tmp

    def run_tool(self, *extra):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = AL.main(["--papers-root", self.papers, "--crawl-root", self.tmp,
                            "--backup-dir", self.backup] + list(extra))
        self.output = buf.getvalue()
        return code

    def read(self, pub):
        with open(os.path.join(self.paper_dir(pub), META_NAME), encoding="utf-8") as handle:
            return json.load(handle)

    # ---- 判据 -----------------------------------------------------------
    def test_dry_run_writes_nothing(self):
        self.write_meta(PUB_A, [_citation(1)])
        self.write_product([(PUB_A, [_citation(1, authors="Li, Ximing", venue="ACL 2024")])])
        before = self.read(PUB_A)
        self.assertEqual(self.run_tool(), 0)
        self.assertEqual(self.read(PUB_A), before)
        self.assertFalse(os.path.exists(self.backup))

    def test_apply_rewrites_only_what_differs_and_keeps_stage_two_fields(self):
        self.write_meta(PUB_A, [
            dict(_citation(1), local_filename="0001-T1.pdf", download_status="downloaded",
                 fail_reason=None, _source="google_scholar", size_bytes=123),
            dict(_citation(2), download_status="failed", fail_reason="HTTP 403"),
            # 退役条目（产物里没有）——必须原样保留 ✓
            dict(_citation(99), title="Retired", download_status="downloaded",
                 local_filename="0099-R.pdf"),
        ])
        self.write_product([(PUB_A, [
            _citation(1, authors="Li, Ximing, Chen Ma", venue="ACL 2024"),
            _citation(2),
        ])])
        self.assertEqual(self.run_tool("--apply"), 0)
        rows = {r["index"]: r for r in self.read(PUB_A)}
        self.assertEqual(sorted(rows), [1, 2, 99])
        # 主体字段按产物更新 ✓
        self.assertEqual(rows[1]["authors"], "Li, Ximing, Chen Ma")
        self.assertEqual(rows[1]["venue"], "ACL 2024")
        self.assertEqual(rows[1]["venue_key"], "ACL2024")
        # 阶段 2 的字段一个都没丢 ✓
        self.assertEqual(rows[1]["local_filename"], "0001-T1.pdf")
        self.assertEqual(rows[1]["download_status"], "downloaded")
        self.assertEqual(rows[1]["_source"], "google_scholar")
        self.assertEqual(rows[1]["size_bytes"], 123)
        self.assertEqual(rows[2]["fail_reason"], "HTTP 403")
        # 退役条目原样 ✓
        self.assertEqual(rows[99]["title"], "Retired")
        self.assertEqual(rows[99]["local_filename"], "0099-R.pdf")
        # 备份 + manifest ✓
        manifest = json.load(open(os.path.join(self.backup, "manifest.json"), encoding="utf-8"))
        self.assertTrue(manifest["applied"])
        self.assertEqual(manifest["totals"]["papers_to_change"], 1)
        self.assertTrue(manifest["papers"][0]["sha256_before"])

    def test_second_apply_is_a_no_op(self):
        self.write_meta(PUB_A, [_citation(1)])
        self.write_product([(PUB_A, [_citation(1, authors="Li, Ximing", venue="ACL 2024")])])
        self.run_tool("--apply")
        after_first = self.read(PUB_A)
        self.assertEqual(self.run_tool("--apply"), 0)
        self.assertEqual(self.read(PUB_A), after_first)
        self.assertEqual(self.run_tool(), 0)

    def test_a_missing_meta_is_created_from_the_product(self):
        self.paper_dir(PUB_B)          # 目录在、meta 不在
        self.write_product([(PUB_B, [_citation(1, venue="ACL 2024")])])
        self.assertEqual(self.run_tool("--apply"), 0)
        rows = self.read(PUB_B)
        self.assertEqual([r["index"] for r in rows], [1])
        self.assertNotIn("download_status", rows[0], "阶段 1 写的主体不该带下载字段 ✗")

    def test_papers_without_citations_are_skipped_without_error(self):
        self.paper_dir(PUB_EMPTY)
        self.write_meta(PUB_EMPTY, [_citation(1)])
        self.write_product([(PUB_EMPTY, [])])
        before = self.read(PUB_EMPTY)
        self.assertEqual(self.run_tool("--apply"), 0)
        self.assertEqual(self.read(PUB_EMPTY), before, "0 条引文 → 不动 ✗")
        self.assertFalse(os.path.exists(self.backup))

    def test_the_whole_library_is_aligned_in_one_pass(self):
        """整库一次对齐（**没有 `-p`**：`shared/spec.py` 宿主在阶段 2，阶段 1 引它是向后依赖 ✗）。"""
        self.write_meta(PUB_A, [_citation(1)])
        self.write_meta(PUB_B, [_citation(1)])
        full = _citation(1, authors="Li, Ximing", venue="ACL 2024")
        self.write_product([(PUB_A, [full]), (PUB_B, [full])])
        self.assertEqual(self.run_tool("--apply"), 0)
        for pub in (PUB_A, PUB_B):
            self.assertEqual(self.read(pub)[0]["venue"], "ACL 2024")

    def test_plan_reports_without_writing(self):
        self.write_meta(PUB_A, [_citation(1)])
        entries, names = AL.load_output(self.write_product(
            [(PUB_A, [_citation(1, venue="ACL 2024")])]))
        self.assertEqual(names, ["author_X_paper_citations.json"])
        plans = AL.plan_papers(entries, self.papers)
        self.assertEqual(plans[0]["changed"], 1)
        self.assertEqual(plans[0]["paper_dir"], self.paper_dir(PUB_A))
        # 编号从**条目自己**的 index 来（不重算、不按位置）✓
        self.assertEqual([c["index"] for c in AL.target_rows(entries[0][1])], [1])
        self.assertEqual(AL.target_rows([{"title": "no index"}]), [])


if __name__ == "__main__":
    unittest.main()
