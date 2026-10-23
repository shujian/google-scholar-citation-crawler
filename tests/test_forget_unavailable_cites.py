"""`code.data_upgrade.forget_unavailable_cites`：清掉"这条记录没有对话框"的否证。

Hermetic：合成缓存文件 + 临时目录，不联网。钉住四件事：
默认 dry-run 不写；`--apply` 只删 `unavailable` 条目；写前备份并留下 manifest；
再跑一次没有可删的就不动文件、也不留备份目录。
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import StringIO, patch  # noqa: E402,F401  (installs the stubs)

from code.data_upgrade import forget_unavailable_cites as tool  # noqa: E402


def _cache():
    return {
        "good-cid": {"formats": {"chicago": "…"}, "fetched_at": "2026-10-20T10:00:00"},
        "dead-cid": {"unavailable": True, "status": 404,
                     "fetched_at": "2026-10-21T22:57:42"},
        "dead-old": {"unavailable": True, "status": 404,
                     "fetched_at": "2026-10-19T09:00:00"},
    }


class ForgetUnavailableCitesTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "cite_format_cache.json")
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(_cache(), handle)

    def _run(self, *args):
        out = StringIO()
        with patch("sys.stdout", out):
            code = tool.main(["--root", self.tmp.name, "--cache", self.path] + list(args))
        return code, out.getvalue()

    def _entries(self):
        with open(self.path, encoding="utf-8") as handle:
            return json.load(handle)

    def test_dry_run_writes_nothing(self):
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("清掉 2 条否证", out)
        self.assertEqual(sorted(self._entries()), ["dead-cid", "dead-old", "good-cid"])

    def test_apply_removes_only_the_unavailable_entries(self):
        code, out = self._run("--apply")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(self._entries()), ["good-cid"])
        self.assertIn("已写回", out)
        out_dir = os.path.join(self.tmp.name, "output")
        backups = [n for n in os.listdir(out_dir) if n.startswith("forget_unavailable_")]
        self.assertEqual(len(backups), 1, "写前必须备份")
        backup_dir = os.path.join(out_dir, backups[0])
        self.assertTrue(os.path.isfile(os.path.join(backup_dir, "cite_format_cache.json")))
        with open(os.path.join(backup_dir, "manifest.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertEqual(sorted(manifest["dropped"]), ["dead-cid", "dead-old"])
        self.assertEqual(manifest["entries_after"], 1)

    def test_before_only_drops_older_verdicts(self):
        code, out = self._run("--apply", "--before", "2026-10-20")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(self._entries()), ["dead-cid", "good-cid"])

    def test_a_second_run_is_a_no_op(self):
        self._run("--apply")
        before = os.listdir(self.tmp.name)
        code, out = self._run("--apply")
        self.assertEqual(code, 0)
        self.assertIn("没有要清理的条目", out)
        self.assertEqual(os.listdir(self.tmp.name), before, "幂等：不再写、也不留新备份")

    def test_a_missing_cache_is_an_error_not_a_silent_pass(self):
        os.remove(self.path)
        code, _ = self._run("--apply")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
