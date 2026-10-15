"""阶段 1 侧的论文目录维护（Y-6 步骤 3）：创建 + `paper_meta.json`、编号改名、三种模式、失败隔离。"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from code import papers_maintenance as PM  # noqa: E402
from code.config import resolve_renumber_papers  # noqa: E402


def _pub(no, title, **extra):
    pub = {"no": no, "title": title, "url": "https://example.org/%d" % no}
    pub.update(extra)
    return pub


class LoadPublicationsTests(unittest.TestCase):

    def test_missing_or_broken_profile_is_empty_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(PM.load_publications(tmp, "X"), [])
            with open(os.path.join(tmp, "author_X_profile.json"), "w", encoding="utf-8") as f:
                f.write("{not json")
            self.assertEqual(PM.load_publications(tmp, "X"), [])

    def test_shapes_and_filters_noise(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "author_X_profile.json"), "w", encoding="utf-8") as f:
                json.dump({"publications": [_pub(1, "A"), "junk", None, _pub(2, "B")]}, f)
            self.assertEqual([e["pub"]["no"] for e in PM.load_publications(tmp, "X")], [1, 2])


class RunModeTests(unittest.TestCase):

    def _root(self, tmp):
        root = os.path.join(tmp, "papers")
        # 目录名与当前排名不一致：B 现在是 no=1，但目录还叫 002-…（带 meta，能被认领）
        os.makedirs(os.path.join(root, "002-B"))
        with open(os.path.join(root, "002-B", "paper_meta.json"), "w", encoding="utf-8") as f:
            json.dump({"no": 2, "title": "B", "url": "https://example.org/1"}, f)
        return root

    def test_off_does_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            result = PM.run(root, [{"pub": _pub(1, "B")}], mode="off", log=lambda *a: None)
            self.assertEqual(result["mode"], "off")
            self.assertTrue(os.path.isdir(os.path.join(root, "002-B")))

    def test_dry_run_reports_without_touching_the_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            result = PM.run(root, [{"pub": _pub(1, "B")}], mode="dry-run", log=lambda *a: None)
            self.assertGreaterEqual(result["plan"]["moves"], 1)
            self.assertNotIn("created", result)
            self.assertFalse(os.path.isdir(os.path.join(root, "001-B")), "dry-run 不许改名")

    def test_apply_creates_dirs_with_meta_and_renames(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            result = PM.run(root, [
                {"pub": _pub(1, "B")},
                {"pub": _pub(2, "C", venue="Proceedings of …", venue_full="Proceedings of EMNLP 2025",
                                venue_key="EMNLP2025", venue_key_source="normalized",
                                authors="H Wang, F Zhang", num_citations=7, year="2025")},
            ], mode="apply", output_dir=tmp, log=lambda *a: None)
            self.assertEqual(result["plan"]["moves"], 1)
            self.assertTrue(os.path.isdir(os.path.join(root, "001-B")))
            self.assertIn("002-C", result["created"])
            with open(os.path.join(root, "002-C", "paper_meta.json"), encoding="utf-8") as f:
                meta = json.load(f)
            self.assertEqual(meta["venue_key"], "EMNLP2025")
            self.assertEqual(meta["venue_full"], "Proceedings of EMNLP 2025")
            self.assertEqual(meta["venue_key_source"], "normalized")
            self.assertEqual(meta["total_collected_citations"], 0)
            self.assertEqual(meta["authors"], "H Wang, F Zhang")

    def test_an_existing_dir_keeps_its_own_meta(self):
        """已存在的目录（阶段 2 的地盘）绝不被改写 meta。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "papers")
            os.makedirs(os.path.join(root, "001-B"))
            with open(os.path.join(root, "001-B", "paper_meta.json"), "w", encoding="utf-8") as f:
                json.dump({"no": 1, "title": "B", "total_collected_citations": 42}, f)
            PM.run(root, [{"pub": _pub(1, "B")}], mode="apply", output_dir=tmp, log=lambda *a: None)
            with open(os.path.join(root, "001-B", "paper_meta.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["total_collected_citations"], 42)

    def test_apply_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            papers = [{"pub": _pub(1, "B")}, {"pub": _pub(2, "C")}]
            PM.run(root, papers, mode="apply", output_dir=tmp, log=lambda *a: None)
            again = PM.run(root, papers, mode="apply", output_dir=tmp, log=lambda *a: None)
            self.assertEqual(again["plan"]["moves"], 0)
            self.assertEqual(again["created"], [])
            self.assertEqual(len(again.get("plan") or {}), 5)

    def test_a_failure_never_escapes(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = os.path.join(tmp, "not-a-dir")
            with open(broken, "w", encoding="utf-8") as f:
                f.write("x")
            result = PM.run(broken, [{"pub": _pub(1, "B")}], mode="apply",
                            output_dir=tmp, log=lambda *a: None)
            self.assertIn("error", result)

    def test_unknown_mode_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                PM.run(self._root(tmp), [{"pub": _pub(1, "B")}], mode="bogus")


class ResolverTests(unittest.TestCase):

    def test_cli_config_default_precedence(self):
        self.assertEqual(resolve_renumber_papers("apply"), ("apply", "cli"))
        self.assertEqual(resolve_renumber_papers(), ("dry-run", "default"))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "crawler_config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"renumber_papers": "OFF"}, f)
            self.assertEqual(resolve_renumber_papers(None, path), ("off", "config"))
            self.assertEqual(resolve_renumber_papers("apply", path), ("apply", "cli"))

    def test_invalid_value_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "crawler_config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"renumber_papers": "yes"}, f)
            with self.assertRaises(ValueError):
                resolve_renumber_papers(None, path)


class PaperMetaBuilderTests(unittest.TestCase):

    def test_shared_builder_is_the_only_definition(self):
        """阶段 1 与阶段 2 写的是同一份形状（阶段 2 只是补 total_collected_citations）。"""
        from code.shared.papers_layout import paper_meta_from_pub
        meta = paper_meta_from_pub({"no": 1, "title": "T", "venue_key": "ACL2024"}, total_collected=3)
        self.assertEqual(meta["venue_key"], "ACL2024")
        self.assertEqual(meta["total_collected_citations"], 3)
        # 没有的字段不硬造（venue_full 缺失时连键都不出现）
        self.assertNotIn("venue_full", meta)


if __name__ == "__main__":
    unittest.main()
