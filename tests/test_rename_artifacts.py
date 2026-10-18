"""`code/data_upgrade/rename_artifacts.py`（E）的 hermetic 测试。

钉住五条：① 只把**在册编号**的旧名改成 `output_NNNN.json`；② 不在册的**不动**（那是 F 的事）；
③ 目标已存在 → 跳过、**绝不覆盖**；④ 内容 sha256 前后一致（只改名 ✓）；⑤ dry-run 零改动、
`--apply` 写 manifest、幂等。
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from code.data_upgrade import rename_artifacts as R  # noqa: E402


class RenameArtifactsTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rename-")
        self.papers = os.path.join(self.tmp, "papers")
        self.paper = os.path.join(self.papers, "001-Demo")
        self.backup = os.path.join(self.tmp, "backup")
        for stage in ("enrich_info", "analysis_info", "analysis_content"):
            os.makedirs(os.path.join(self.paper, stage))
        with open(os.path.join(self.paper, "citations_meta.json"), "w", encoding="utf-8") as f:
            json.dump([{"index": 1}, {"index": 2}], f)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def touch(self, stage, name, content=b'{"citation": {}}'):
        path = os.path.join(self.paper, stage, name)
        with open(path, "wb") as f:
            f.write(content)
        return path

    def run_tool(self, *extra):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = R.main(["--papers-root", self.papers, "--backup-dir", self.backup]
                          + list(extra))
        return code, buf.getvalue()

    def listing(self, stage):
        return sorted(os.listdir(os.path.join(self.paper, stage)))

    # ------------------------------------------------------------------ 判据
    def test_legacy_names_of_registered_indices_are_renamed(self):
        self.touch("enrich_info", "enrich_info_0001.json")
        self.touch("analysis_info", "analysis_info_0002.json")
        self.touch("analysis_content", "citation_analysis_0001.json")
        self.run_tool("--apply")
        self.assertEqual(self.listing("enrich_info"), ["output_0001.json"])
        self.assertEqual(self.listing("analysis_info"), ["output_0002.json"])
        self.assertEqual(self.listing("analysis_content"), ["output_0001.json"])

    def test_unregistered_and_already_new_names_are_left_alone(self):
        self.touch("enrich_info", "enrich_info_0009.json")   # 不在册 ✗
        self.touch("enrich_info", "output_0001.json")        # 已是新名 ✓
        self.run_tool("--apply")
        self.assertEqual(self.listing("enrich_info"),
                         ["enrich_info_0009.json", "output_0001.json"])

    def test_an_existing_target_is_never_overwritten(self):
        self.touch("enrich_info", "enrich_info_0001.json", b"old-content")
        self.touch("enrich_info", "output_0001.json", b"new-content")
        self.run_tool("--apply")
        # 跳过 = **不改名**：旧名文件原样留在原处 ✓，目标一个字节都不动 ✓
        self.assertEqual(self.listing("enrich_info"),
                         ["enrich_info_0001.json", "output_0001.json"])
        with open(os.path.join(self.paper, "enrich_info", "output_0001.json"), "rb") as f:
            self.assertEqual(f.read(), b"new-content", "目标已存在时绝不覆盖 ✓")
        with open(os.path.join(self.paper, "enrich_info", "enrich_info_0001.json"), "rb") as f:
            self.assertEqual(f.read(), b"old-content", "源文件也没被改写 ✓")

    def test_content_is_byte_identical_after_the_rename(self):
        import hashlib
        payload = b'{"citation": {"title": "x"}}'
        expected = hashlib.sha256(payload).hexdigest()
        self.touch("enrich_info", "enrich_info_0001.json", payload)
        self.run_tool("--apply")
        with open(os.path.join(self.paper, "enrich_info", "output_0001.json"), "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), expected, "只改名 ✓")

    def test_dry_run_manifest_and_idempotence(self):
        self.touch("enrich_info", "enrich_info_0001.json")
        before = self.listing("enrich_info")
        code, out = self.run_tool()
        self.assertEqual(code, 0)
        self.assertIn("需改名 1 个", out)
        self.assertEqual(self.listing("enrich_info"), before, "dry-run 零改动 ✓")
        self.run_tool("--apply")
        with open(os.path.join(self.backup, "manifest.json"), encoding="utf-8") as f:
            records = json.load(f)["renamed"]
        self.assertEqual(len(records), 1)
        self.assertEqual((records[0]["old"], records[0]["new"]),
                         ("enrich_info_0001.json", "output_0001.json"))
        self.assertTrue(records[0]["sha256"])
        _, out2 = self.run_tool("--apply")
        self.assertIn("需改名 0 个", out2, "幂等 ✓")


if __name__ == "__main__":
    unittest.main()
