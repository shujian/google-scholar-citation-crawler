"""
tests/test_log_utils.py — the run-log facade and the run-log archive policy.

The run log is the stdout stream (``TeeStream`` mirrors it), so these tests
capture ``sys.stdout``: no file handler, no network, no crawl.

Why the facade exists (2026-10-16): one run printed 21 ``=`` banners, the same
URL twice in a row on every year (``URL:`` then ``Request URL:``), one fact as
three lines (``Year N done`` / ``compare`` / ``status``), hand-written 2/4/6/8
space indents and timestamps on only some lines — and ``--verbose`` changed
nothing, because the logger it configured was never called by the crawl.
"""

import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import StringIO, patch  # noqa: E402  (installs the stubs)

from code import log_utils as lu  # noqa: E402


def captured(fn, *args, **kwargs):
    with patch("sys.stdout", new_callable=StringIO) as fake_stdout:
        result = fn(*args, **kwargs)
    return result, fake_stdout.getvalue()


class RunLogShapeTests(unittest.TestCase):
    """Every line has the same shape: time, level, module, per-level indent."""

    def setUp(self):
        self.log = lu.RunLog()
        self.addCleanup(self.log.set_threshold, lu.INFO)

    def test_line_shape(self):
        _, out = captured(self.log.write, lu.INFO, "hello", module="fetch", indent=2)
        expected = f"INFO  {'fetch':<9} {'  ' * 2}hello"
        self.assertRegex(out, r"^\d{2}:\d{2}:\d{2} " + re.escape(expected) + r"\n$")

    def test_module_is_padded_so_messages_line_up(self):
        _, short = captured(self.log.write, lu.INFO, "x", module="http")
        _, long = captured(self.log.write, lu.INFO, "x", module="profile")
        self.assertIn(f"INFO  {'http':<9} x", short)
        self.assertIn(f"INFO  {'profile':<9} x", long)
        # Same column for the message no matter how long the module tag is.
        self.assertEqual(short.index("x"), long.index("x"))

    def test_levels_gate_the_per_item_detail(self):
        shown = self.log.write
        _, out = captured(shown, lu.DEBUG, "per page detail", module="http")
        self.assertEqual(out, "")                     # hidden at the default level
        self.log.set_threshold(lu.DEBUG)
        _, out = captured(shown, lu.DEBUG, "per page detail", module="http")
        self.assertIn("DEBUG http", out)
        self.assertIn("per page detail", out)

    def test_trace_stays_hidden_even_with_debug(self):
        self.log.set_threshold(lu.DEBUG)
        _, out = captured(self.log.write, lu.TRACE, "parse detail", module="page")
        self.assertEqual(out, "")

    def test_warnings_are_visible_at_the_default_level(self):
        _, out = captured(self.log.warn, "page returned 0 rows", module="year")
        self.assertIn("WARN  year", out)

    def test_filtered_line_returns_false(self):
        self.assertFalse(self.log.debug("hidden", module="http"))
        self.assertTrue(self.log.info("shown", module="http"))

    def test_section_is_one_ruled_line(self):
        _, out = captured(self.log.section, "Phase 2 · citations · test-author", module="phase")
        lines = out.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r"^\d{2}:\d{2}:\d{2} INFO  ")
        self.assertIn(f"INFO  {'phase':<9} ── ", lines[0])
        self.assertIn("Phase 2 · citations · test-author", lines[0])
        self.assertNotIn("=", lines[0])
        self.assertTrue(lines[0].rstrip().endswith("─"))

    def test_setup_logging_makes_verbose_meaningful(self):
        """``--verbose`` switches the facade to DEBUG; without it, INFO."""
        with patch("sys.stderr", new_callable=StringIO):
            lu.setup_logging(verbose=False)
            self.assertEqual(lu.run_log.threshold, lu.INFO)
            lu.setup_logging(verbose=True)
            self.assertEqual(lu.run_log.threshold, lu.DEBUG)
        lu.run_log.set_threshold(lu.INFO)


class PruneRunLogsTests(unittest.TestCase):
    """Keep the newest N run logs; move the rest to logs/old/ — never delete."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.logs_dir = self._tmp.name

    def _make(self, count, name="author_test_run_202601{i:02d}_120000.log", start=1):
        paths = []
        for i in range(start, start + count):
            path = os.path.join(self.logs_dir, name.format(i=i))
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(f"run {i}\n")
            os.utime(path, (1_700_000_000 + i, 1_700_000_000 + i))
            paths.append(path)
        return paths

    def test_keeps_the_newest_and_moves_the_rest(self):
        self._make(35)
        kept, moved = lu.prune_run_logs(self.logs_dir, keep=30)
        self.assertEqual((kept, moved), (30, 5))
        remaining = sorted(n for n in os.listdir(self.logs_dir) if n.endswith(".log"))
        self.assertEqual(len(remaining), 30)
        archived = sorted(os.listdir(os.path.join(self.logs_dir, "old")))
        self.assertEqual(len(archived), 5)
        # The five oldest are the ones that moved.
        self.assertEqual(archived[0], "author_test_run_20260101_120000.log")
        self.assertEqual(remaining[0], "author_test_run_20260106_120000.log")

    def test_nothing_is_deleted(self):
        self._make(35)
        lu.prune_run_logs(self.logs_dir, keep=30)
        total = sum(len(files) for _, _, files in os.walk(self.logs_dir))
        self.assertEqual(total, 35)

    def test_current_run_log_is_never_archived(self):
        paths = self._make(35)
        oldest = paths[0]
        kept, moved = lu.prune_run_logs(self.logs_dir, keep=2, keep_path=oldest)
        self.assertEqual((kept, moved), (3, 32))
        self.assertTrue(os.path.exists(oldest))

    def test_second_run_is_a_no_op(self):
        self._make(35)
        lu.prune_run_logs(self.logs_dir, keep=30)
        self.assertEqual(lu.prune_run_logs(self.logs_dir, keep=30), (30, 0))

    def test_non_log_files_and_the_archive_dir_are_ignored(self):
        self._make(32)
        with open(os.path.join(self.logs_dir, "curl.txt"), "w", encoding="utf-8") as handle:
            handle.write("cookies\n")
        os.makedirs(os.path.join(self.logs_dir, "old"), exist_ok=True)
        kept, moved = lu.prune_run_logs(self.logs_dir, keep=30)
        self.assertEqual((kept, moved), (30, 2))
        self.assertTrue(os.path.exists(os.path.join(self.logs_dir, "curl.txt")))

    def test_missing_directory_is_not_an_error(self):
        self.assertEqual(lu.prune_run_logs(os.path.join(self.logs_dir, "nope")), (0, 0))

    def test_keep_zero_archives_every_older_log(self):
        self._make(3)
        self.assertEqual(lu.prune_run_logs(self.logs_dir, keep=0), (0, 3))
        self.assertEqual(len(os.listdir(os.path.join(self.logs_dir, "old"))), 3)


if __name__ == "__main__":
    unittest.main()
