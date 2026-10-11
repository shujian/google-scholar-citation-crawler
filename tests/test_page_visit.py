import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.conftest import patch, StringIO
import types
import unittest

from code.page_visit import PageVisit


def _ctx(**overrides):
    values = dict(
        interactive_captcha=False,
        try_interactive_captcha_fn=None,
        wait_proxy_switch_fn=None,
        wait_status_fn=None,
        delay_scale=0.0,
    )
    values.update(overrides)
    return types.SimpleNamespace(**values)


class PageVisitTests(unittest.TestCase):
    """PageVisit must recover, but never loop forever."""

    def _visit(self, fn, ctx=None, label="page"):
        with patch("code.page_visit.time.sleep", return_value=None), \
             patch("code.page_visit.rand_delay", return_value=0), \
             patch("sys.stdout", new_callable=StringIO) as stdout:
            try:
                return PageVisit(ctx or _ctx()).fetch(fn, "https://example", label=label), \
                       stdout.getvalue(), None
            except Exception as exc:  # noqa: BLE001 - asserted by the caller
                return None, stdout.getvalue(), exc

    def test_transient_error_is_retried_then_succeeds(self):
        calls = []

        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("transient")
            return "ok"

        result, output, error = self._visit(fn)

        self.assertIsNone(error)
        self.assertEqual(result, "ok")
        self.assertEqual(len(calls), 3)
        self.assertIn("retry 1/2", output)

    def test_gives_up_after_cooldowns_without_proxy_prompt(self):
        calls = []

        def fn():
            calls.append(1)
            raise RuntimeError("blocked")

        result, output, error = self._visit(fn)

        self.assertIsInstance(error, RuntimeError)
        self.assertIn("still failing after 3 cooldowns", output)
        # Each cooldown round is 2 quick retries + the attempt that triggers the
        # cooldown, so 3 cooldowns + the prompting attempt = 12 calls.
        self.assertEqual(len(calls), 12)
        self.assertIn("Rate-limit cooldown", output)

    def test_proxy_switch_confirmation_resumes_the_fetch(self):
        asked = []

        def wait_proxy_switch():
            asked.append(1)
            return True

        calls = []

        def fn():
            calls.append(1)
            if not asked:
                raise RuntimeError("blocked")
            return "ok"

        result, output, error = self._visit(fn, ctx=_ctx(wait_proxy_switch_fn=wait_proxy_switch))

        self.assertIsNone(error)
        self.assertEqual(result, "ok")
        self.assertEqual(len(asked), 1)
        self.assertEqual(len(calls), 13)  # 12 failures, then the resumed attempt

    def test_proxy_switch_timeout_reraises_original_error(self):
        calls = []

        def fn():
            calls.append(1)
            raise RuntimeError("blocked")

        result, output, error = self._visit(fn, ctx=_ctx(wait_proxy_switch_fn=lambda: False))

        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(str(error), "blocked")
        self.assertEqual(len(calls), 12)

    def test_interactive_captcha_solve_retries_immediately(self):
        solves = []

        def try_captcha(url):
            solves.append(url)
            return len(solves) >= 2

        calls = []

        def fn():
            calls.append(1)
            if len(solves) < 2:
                raise RuntimeError("captcha")
            return "ok"

        result, output, error = self._visit(
            fn, ctx=_ctx(interactive_captcha=True, try_interactive_captcha_fn=try_captcha))

        self.assertIsNone(error)
        self.assertEqual(result, "ok")
        self.assertEqual(len(solves), 2)

    def test_keyboard_interrupt_propagates(self):
        def fn():
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            PageVisit(_ctx()).fetch(fn, "https://example")


class InteractiveInputGuardTests(unittest.TestCase):
    """Unattended runs must not block or spin on stdin."""

    def test_try_interactive_captcha_skips_prompt_without_a_tty(self):
        from code.interactive import try_interactive_captcha
        with patch("sys.stdin", new_callable=StringIO), \
             patch("sys.stdout", new_callable=StringIO) as stdout:
            solved = try_interactive_captcha("https://example", inject_fn=lambda curl: 1)
        self.assertFalse(solved)
        self.assertIn("not a terminal", stdout.getvalue())

    def test_read_multiline_input_returns_empty_without_a_fileno(self):
        from code.interactive import _read_multiline_input
        with patch("sys.stdin", new_callable=StringIO), \
             patch("sys.stdout", new_callable=StringIO):
            self.assertEqual(_read_multiline_input(), [])

    def test_wait_proxy_switch_returns_immediately_on_closed_stdin(self):
        from code.interactive import wait_proxy_switch
        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        original_stdin = sys.stdin
        sys.stdin = os.fdopen(read_fd, "r")
        try:
            with patch("sys.stdout", new_callable=StringIO) as stdout:
                confirmed = wait_proxy_switch(max_hours=1)
        finally:
            sys.stdin.close()
            sys.stdin = original_stdin
        self.assertFalse(confirmed)
        self.assertIn("stdin closed", stdout.getvalue())


if __name__ == '__main__':
    unittest.main()
