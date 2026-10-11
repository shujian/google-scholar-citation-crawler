"""Tests for cookie acquisition in crawler/interactive.py.

Covers the 2026-10-11 hardening + convenience work:
  * curl.txt is written 0600 (it carries live session cookies)
  * CR/LF/TAB in a pasted cookie name/value is stripped (cannot poison the
    saved session or the request headers)
  * the optional 'browser' keyword routes to the browser-harvest hook, and a
    failing harvest falls back to the normal paste flow
  * the synthesized cURL (browser harvest) is parseable by the project's own
    injector and carries the browser-identity headers as a coherent trio
  * without playwright / without a TTY the harvest hook is a no-op (the manual
    flow stays exactly as before)

Hermetic: no network, no browser; the playwright import is stubbed.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawler import interactive as I  # noqa: E402


class TestSaveCurlPermissions(unittest.TestCase):
    def test_written_0600_and_tightens_existing(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "curl.txt")
            I.save_curl_to_file("curl 'https://x' -H 'cookie: a=b'", path)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

            os.chmod(path, 0o644)              # 历史遗留的宽松权限
            I.save_curl_to_file("curl 'https://x' -H 'cookie: a=c'", path)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            with open(path, encoding="utf-8") as f:
                self.assertIn("a=c", f.read())

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "curl.txt")
            self.assertIsNone(I.load_curl_from_file(path))
            I.save_curl_to_file("curl 'https://y'", path)
            self.assertEqual(I.load_curl_from_file(path), "curl 'https://y'")


class TestValueSanitization(unittest.TestCase):
    """A value with a newline must not reach the session or the saved cURL."""

    def _inject(self, curl_str):
        captured = {}

        class FakeJar:
            def clear(self):
                pass

            def set(self, k, v):
                captured[k] = v

        class FakeSession:
            def __init__(self):
                self.cookies = FakeJar()
                self.headers = {}

        class FakeNav:
            def __init__(self):
                self._session1 = FakeSession()
                self._session2 = FakeSession()
                self.got_403 = True

        nav = FakeNav()
        fake_scholarly = type("S", (), {"_Scholarly__nav": nav})
        with mock.patch.object(I, "scholarly", fake_scholarly):
            n = I.inject_cookies_from_curl(
                curl_str,
                curl_header_allowlist=frozenset(),
                last_scholar_url="https://scholar.google.com/",
                injected_cookies_ref={},
                injected_header_overrides_ref={},
                curl_save_path=None,
            )
        return n, captured

    def test_newline_in_value_is_removed(self):
        n, captured = self._inject("curl 'https://x' -H 'cookie: a=b\nc; d=e'")
        self.assertEqual(n, 2)
        self.assertEqual(captured["a"], "bc")
        self.assertEqual(captured["d"], "e")
        for v in captured.values():
            self.assertNotIn("\n", v)
            self.assertNotIn("\r", v)
            self.assertNotIn("\t", v)


class TestBrowserKeywordRouting(unittest.TestCase):
    """'browser' + Enter triggers the harvest hook instead of parsing a paste."""

    def test_keyword_calls_harvest_and_returns_true(self):
        calls = []
        ok = I._inject_with_cookie_retry(
            inject_fn=lambda s: 0,
            skip_message="skip", retry_message="retry",
            harvest_fn=lambda url: calls.append(url) or True,
            harvest_url="https://scholar.google.com/x",
            reader=lambda: ["browser"],
        )
        self.assertTrue(ok)
        self.assertEqual(calls, ["https://scholar.google.com/x"])

    def test_failed_harvest_falls_back_to_paste(self):
        seq = [["browser"], ["curl 'https://x' -H 'cookie: k=v'"]]

        def reader():
            return seq.pop(0) if seq else []

        seen = []
        ok = I._inject_with_cookie_retry(
            inject_fn=lambda s: seen.append(s) or 1,
            skip_message="skip", retry_message="retry",
            harvest_fn=lambda url: False,
            harvest_url="https://scholar.google.com/",
            reader=reader,
        )
        self.assertTrue(ok)
        self.assertEqual(len(seen), 1)                 # 落回粘贴路径并成功
        self.assertIn("cookie: k=v", seen[0])

    def test_empty_input_skips(self):
        ok = I._inject_with_cookie_retry(
            inject_fn=lambda s: 1, skip_message="skip", retry_message="retry",
            harvest_fn=lambda url: True, harvest_url="u", reader=lambda: [],
        )
        self.assertFalse(ok)


class TestHarvestGating(unittest.TestCase):
    """No playwright / no TTY ⇒ harvest refuses and the manual flow is unchanged."""

    def test_no_playwright_returns_false(self):
        with mock.patch.object(I, "_playwright_available", lambda: False):
            self.assertFalse(I.try_browser_harvest("https://x", inject_fn=lambda s: 1))

    def test_no_tty_returns_false(self):
        with mock.patch.object(I, "_playwright_available", lambda: True), \
             mock.patch.object(I.sys.stdin, "isatty", lambda: False):
            self.assertFalse(I.try_browser_harvest("https://x", inject_fn=lambda s: 1))


class TestSynthesizedCurl(unittest.TestCase):
    """The harvested session must be readable by the project's own parser."""

    class _FakePage:
        def evaluate(self, _js):
            return {
                "ua": "Mozilla/5.0 (Macintosh) Chrome/145.0.0.0 Safari/537.36",
                "brands": '"Chromium";v="145", "Not:A-Brand";v="24"',
                "full": '"Chromium";v="145.0.7632.1"',
                "mobile": "?0",
                "platform": '"macOS"',
            }

    def test_curl_contains_cookie_and_identity_trio(self):
        cookies = [{"name": "GSP", "value": "abc"},
                   {"name": "NID", "value": "def"},
                   {"name": "", "value": "skip-me"}]
        curl = I._build_curl_from_page(self._FakePage(), cookies, "https://scholar.google.com/")
        self.assertIn("cookie: GSP=abc; NID=def", curl)
        self.assertIn("user-agent: Mozilla/5.0", curl)
        self.assertIn("sec-ch-ua:", curl)
        self.assertIn("sec-ch-ua-full-version-list:", curl)
        self.assertNotIn("skip-me", curl)

    def test_identity_trio_survives_the_allowlist(self):
        from crawler.scholarly_session import _CURL_HEADER_ALLOWLIST
        curl = I._build_curl_from_page(self._FakePage(), [{"name": "k", "value": "v"}],
                                       "https://scholar.google.com/")
        headers = {}
        for line in curl.split(" -H ")[1:]:
            body = line.strip().strip("'\"")
            if ":" in body:
                name, value = body.split(":", 1)
                headers[name.strip().lower()] = value.strip()
        for h in ("user-agent", "sec-ch-ua", "sec-ch-ua-full-version-list"):
            self.assertIn(h, headers)
            self.assertIn(h, _CURL_HEADER_ALLOWLIST)


if __name__ == "__main__":
    unittest.main()
