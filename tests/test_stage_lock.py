"""`papers/` 使用锁：阶段 2 写锁，阶段 1 看到活锁就只报告不改名。

为什么需要它：阶段 1 按最新排名改论文目录名，而阶段 2 拼路径只认输入快照的 `pub.no` + 标题，
写完还会 `makedirs` —— 两者同时跑会把旧名字的目录**重新建出来**，PDF / 进度 / meta 被拆成两套。
"""

import json
import os
import socket
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from code import papers_maintenance as PM  # noqa: E402
from code.shared import stage_lock as SL  # noqa: E402


def _pub(no, title, **extra):
    pub = {"no": no, "title": title, "url": "https://example.org/%d" % no}
    pub.update(extra)
    return pub


class LockFileTests(unittest.TestCase):

    def test_acquire_read_release_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(SL.read(tmp))
            info = SL.acquire(tmp, stage="stage2")
            self.assertEqual(SL.read(tmp)["pid"], os.getpid())
            self.assertEqual(SL.read(tmp)["stage"], "stage2")
            self.assertEqual(SL.read(tmp)["host"], socket.gethostname())
            self.assertIn("pid=", SL.describe(info))
            self.assertTrue(SL.release(tmp))
            self.assertIsNone(SL.read(tmp))
            self.assertFalse(SL.release(tmp), "release 是幂等的")

    def test_a_lock_from_a_dead_process_is_not_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(SL.lock_path(tmp), "w", encoding="utf-8") as f:
                json.dump({"stage": "stage2", "pid": 999999,
                           "host": socket.gethostname()}, f)
            self.assertFalse(SL.is_live(SL.read(tmp), tmp))

    def test_a_lock_from_this_process_is_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            SL.acquire(tmp)
            self.assertTrue(SL.is_live(SL.read(tmp), tmp))

    def test_a_foreign_host_lock_goes_by_file_age(self):
        """跨主机（SSHFS）看不到 pid，只能看年龄。"""
        with tempfile.TemporaryDirectory() as tmp:
            info = {"stage": "stage2", "pid": 1, "host": "some-remote-host"}
            with open(SL.lock_path(tmp), "w", encoding="utf-8") as f:
                json.dump(info, f)
            self.assertTrue(SL.is_live(info, tmp, now=datetime.now()))
            old = datetime.now() + timedelta(minutes=SL.STALE_AFTER_MINUTES + 1)
            self.assertFalse(SL.is_live(info, tmp, now=old))

    def test_a_broken_lock_does_not_block_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(SL.lock_path(tmp), "w", encoding="utf-8") as f:
                f.write("{not json")
            self.assertIsNone(SL.read(tmp))
            self.assertFalse(SL.is_live(None, tmp))


class MaintenanceRespectsTheLockTests(unittest.TestCase):

    def _root(self, tmp):
        root = os.path.join(tmp, "papers")
        os.makedirs(os.path.join(root, "002-B"))
        with open(os.path.join(root, "002-B", "paper_meta.json"), "w", encoding="utf-8") as f:
            json.dump({"no": 2, "title": "B", "url": "https://example.org/1"}, f)
        return root

    def _lines(self):
        lines = []
        return lines, lines.append

    def test_a_live_lock_downgrades_apply_to_a_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            SL.acquire(root, extra={"stage_note": "test"})
            self.addCleanup(SL.release, root)
            lines, log = self._lines()
            result = PM.run(root, [{"pub": _pub(1, "B")}], mode="apply",
                           output_dir=tmp, log=log)
            self.assertEqual(result["mode"], "dry-run", "有活锁时不许落盘")
            self.assertIn("pid=", result["blocked_by"])
            self.assertGreaterEqual(result["plan"]["moves"], 1, "仍然把计划报告出来")
            self.assertTrue(os.path.isdir(os.path.join(root, "002-B")), "不许改名")
            self.assertFalse(os.path.isdir(os.path.join(root, "001-B")))
            self.assertTrue(any("阶段 2 正在使用论文目录" in line for line in lines))
            self.assertTrue(any("--renumber-papers force" in line for line in lines))

    def test_force_still_renames_and_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            SL.acquire(root)
            self.addCleanup(SL.release, root)
            lines, log = self._lines()
            result = PM.run(root, [{"pub": _pub(1, "B")}], mode="force",
                           output_dir=tmp, log=log)
            self.assertEqual(result["mode"], "force")
            self.assertIn("pid=", result["forced_past"])
            self.assertTrue(os.path.isdir(os.path.join(root, "001-B")))
            self.assertTrue(any("照改不误" in line for line in lines))

    def test_without_a_lock_apply_renames_as_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            result = PM.run(root, [{"pub": _pub(1, "B")}], mode="apply",
                           output_dir=tmp, log=lambda *a: None)
            self.assertEqual(result["mode"], "apply")
            self.assertNotIn("blocked_by", result)
            self.assertTrue(os.path.isdir(os.path.join(root, "001-B")))

    def test_a_live_lock_with_nothing_to_do_says_nothing(self):
        """没有要改名/归档的论文时，锁与本轮无关 —— 不该弹那段提示。

        2026-10-21 用户：日志里先出现 `No paper position changes`，紧接着却是
        "阶段 2 正在使用论文目录…本轮不改名/不归档" —— 既然没有变化，就没有冲突。
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "papers")
            os.makedirs(os.path.join(root, "001-B"))
            with open(os.path.join(root, "001-B", "paper_meta.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"no": 1, "title": "B", "url": "https://example.org/1"}, f)
            SL.acquire(root)
            self.addCleanup(SL.release, root)
            lines, log = self._lines()
            result = PM.run(root, [{"pub": _pub(1, "B")}], mode="apply",
                           output_dir=tmp, log=log)
            self.assertEqual(result["plan"]["moves"], 0)
            self.assertNotIn("blocked_by", result)
            self.assertEqual(lines, [], "没有动作就不提示锁，也不提示 dry-run")

    def test_a_stale_lock_does_not_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            with open(SL.lock_path(root), "w", encoding="utf-8") as f:
                json.dump({"stage": "stage2", "pid": 999999,
                           "host": socket.gethostname()}, f)
            result = PM.run(root, [{"pub": _pub(1, "B")}], mode="apply",
                           output_dir=tmp, log=lambda *a: None)
            self.assertEqual(result["mode"], "apply")
            self.assertTrue(os.path.isdir(os.path.join(root, "001-B")))

    def test_off_does_not_even_look_at_the_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            SL.acquire(root)
            self.addCleanup(SL.release, root)
            result = PM.run(root, [{"pub": _pub(1, "B")}], mode="off",
                           output_dir=tmp, log=lambda *a: None)
            self.assertEqual(result["mode"], "off")
            self.assertNotIn("blocked_by", result)


if __name__ == "__main__":
    unittest.main()
