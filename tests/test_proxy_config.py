"""Proxy / runtime-config resolution: CLI argument > config file > system default.

``crawler_config.json`` is gitignored and machine-local, so every test here uses
a temporary file (or no file at all) and patches the ambient environment rather
than reading the developer's real settings.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from code import config as config_module  # noqa: E402
from code.config import (  # noqa: E402
    resolve_cite_formats,
    CONFIG_FILENAME,
    ConfigError,
    ProxyConfig,
    load_config_file,
    resolve_proxy_config,
)
from code.lib_env import export_proxy_env, sanitize_no_proxy, system_proxy_env  # noqa: E402

EMPTY_ENV = {"https_proxy": "", "http_proxy": "", "no_proxy": "", "NO_PROXY": ""}


def _write_config(directory, payload):
    path = os.path.join(directory, CONFIG_FILENAME)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return path


class LoadConfigFileTests(unittest.TestCase):
    def test_missing_default_file_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = os.path.join(tmpdir, CONFIG_FILENAME)
            with patch.object(config_module, "DEFAULT_CONFIG_PATH", missing):
                self.assertEqual(load_config_file(), {})

    def test_missing_explicit_file_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            explicit = os.path.join(tmpdir, "explicit.json")
            with self.assertRaises(ConfigError):
                load_config_file(explicit)

    def test_malformed_json_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, CONFIG_FILENAME)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{not json")
            with self.assertRaises(ConfigError):
                load_config_file(path)

    def test_non_object_json_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, ["nope"])
            with self.assertRaises(ConfigError):
                load_config_file(path)

    def test_valid_file_is_returned(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {"proxy_url": "http://127.0.0.1:7890",
                                          "no_proxy": "localhost"})
            cfg = load_config_file(path)
            self.assertEqual(cfg["proxy_url"], "http://127.0.0.1:7890")


class ResolveProxyConfigTests(unittest.TestCase):
    """``config_path=None`` means "use the project default file"."""

    def _resolve(self, tmpdir, payload=None, cli_proxy=None, cli_no_proxy=None):
        path = os.path.join(tmpdir, CONFIG_FILENAME)
        if payload is not None:
            _write_config(tmpdir, payload)
        with patch.object(config_module, "DEFAULT_CONFIG_PATH", path):
            return resolve_proxy_config(cli_proxy, cli_no_proxy)

    def test_config_file_wins_over_environment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"https_proxy": "http://env:2",
                                         "no_proxy": "env.tld"}, clear=False):
                resolved = self._resolve(tmpdir, {"proxy_url": "http://config:1",
                                                  "no_proxy": "config.tld"})
            self.assertEqual(resolved.proxy_url, "http://config:1")
            self.assertEqual(resolved.no_proxy, "config.tld")
            self.assertEqual(resolved.proxy_source, "config")
            self.assertEqual(resolved.no_proxy_source, "config")

    def test_cli_wins_over_config_file_and_environment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"https_proxy": "http://env:2",
                                         "no_proxy": "env.tld"}, clear=False):
                resolved = self._resolve(tmpdir,
                                         {"proxy_url": "http://config:1",
                                          "no_proxy": "config.tld"},
                                         cli_proxy="http://cli:3",
                                         cli_no_proxy="cli.tld")
            self.assertEqual((resolved.proxy_url, resolved.no_proxy),
                             ("http://cli:3", "cli.tld"))
            self.assertEqual((resolved.proxy_source, resolved.no_proxy_source),
                             ("cli", "cli"))

    def test_each_key_falls_back_independently(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"https_proxy": "http://env:2",
                                         "no_proxy": "env.tld"}, clear=False):
                resolved = self._resolve(tmpdir, {"proxy_url": "http://config:1"},
                                         cli_no_proxy="cli.tld")
            self.assertEqual(resolved.proxy_url, "http://config:1")
            self.assertEqual(resolved.proxy_source, "config")
            self.assertEqual(resolved.no_proxy, "cli.tld")
            self.assertEqual(resolved.no_proxy_source, "cli")

    def test_environment_is_the_last_resort_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"https_proxy": "http://env:2",
                                         "no_proxy": "env.tld"}, clear=False):
                resolved = self._resolve(tmpdir)
            self.assertEqual(resolved.proxy_url, "http://env:2")
            self.assertEqual(resolved.no_proxy, "env.tld")
            self.assertEqual((resolved.proxy_source, resolved.no_proxy_source),
                             ("env", "env"))

    def test_no_source_configured_yields_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, EMPTY_ENV, clear=False):
                resolved = self._resolve(tmpdir)
            self.assertEqual((resolved.proxy_url, resolved.no_proxy), (None, None))
            self.assertEqual((resolved.proxy_source, resolved.no_proxy_source),
                             ("none", "none"))

    def test_empty_string_is_an_explicit_value_not_a_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"https_proxy": "http://env:2"}, clear=False):
                resolved = self._resolve(tmpdir, cli_proxy="", cli_no_proxy="")
            self.assertEqual((resolved.proxy_url, resolved.no_proxy),
                             ("", ""))
            self.assertEqual(resolved.proxy_source, "cli")

    def test_explicit_config_path_is_honoured(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_config(tmpdir, {"proxy_url": "http://explicit:1"})
            with patch.dict(os.environ, EMPTY_ENV, clear=False):
                resolved = resolve_proxy_config(config_path=path)
            self.assertEqual(resolved.proxy_url, "http://explicit:1")
            self.assertEqual(resolved.proxy_source, "config")

    def test_http_proxy_is_used_when_https_proxy_is_absent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = dict(EMPTY_ENV)
            env["http_proxy"] = "http://env-http:9"
            with patch.dict(os.environ, env, clear=False):
                resolved = self._resolve(tmpdir)
            self.assertEqual(resolved.proxy_url, "http://env-http:9")


class ResolveCiteFormatsTests(unittest.TestCase):
    """The Cite-dialog step: CLI > config file > default (on)."""

    def _resolve(self, tmpdir, payload=None, cli=None):
        path = os.path.join(tmpdir, CONFIG_FILENAME)
        if payload is not None:
            _write_config(tmpdir, payload)
        with patch.object(config_module, "DEFAULT_CONFIG_PATH", path):
            return resolve_cite_formats(cli)

    def test_on_by_default(self):
        """New data must carry citation["cite"], so the step is opt-out."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertEqual(self._resolve(tmpdir), (True, "default"))

    def test_config_file_can_turn_it_off(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertEqual(self._resolve(tmpdir, {"cite_formats": False}),
                             (False, "config"))

    def test_cli_wins_over_the_config_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertEqual(self._resolve(tmpdir, {"cite_formats": False}, cli=True),
                             (True, "cli"))
            self.assertEqual(self._resolve(tmpdir, {"cite_formats": True}, cli=False),
                             (False, "cli"))

    def test_other_config_keys_do_not_affect_it(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertEqual(self._resolve(tmpdir, {"proxy_url": "http://x:1"}),
                             (True, "default"))


class SystemProxyEnvTests(unittest.TestCase):
    def test_reads_conventional_keys(self):
        with patch.dict(os.environ, {"https_proxy": "http://p:1",
                                     "no_proxy": "a,b"}, clear=False):
            self.assertEqual(system_proxy_env(), ("http://p:1", "a,b"))


class ExportProxyEnvTests(unittest.TestCase):
    KEYS = ("https_proxy", "http_proxy", "no_proxy", "NO_PROXY")

    def setUp(self):
        self.snapshot = {key: os.environ.get(key) for key in self.KEYS}

    def tearDown(self):
        for key, value in self.snapshot.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_none_leaves_environment_untouched(self):
        os.environ["https_proxy"] = "http://keep:1"
        export_proxy_env(None, None)
        self.assertEqual(os.environ["https_proxy"], "http://keep:1")

    def test_values_are_staged_for_httpx(self):
        export_proxy_env("http://config:1", "localhost,[::1]")
        self.assertEqual(os.environ["https_proxy"], "http://config:1")
        self.assertEqual(os.environ["http_proxy"], "http://config:1")
        self.assertEqual(os.environ["no_proxy"], "localhost,::1")
        self.assertEqual(os.environ["NO_PROXY"], "localhost,::1")

    def test_empty_proxy_url_clears_the_variables(self):
        os.environ["https_proxy"] = "http://stale:1"
        export_proxy_env("", None)
        self.assertNotIn("https_proxy", os.environ)
        self.assertNotIn("http_proxy", os.environ)


class SanitizeNoProxyTests(unittest.TestCase):
    def test_bracketed_ipv6_is_rewritten(self):
        env = {"NO_PROXY": "localhost,[::1]", "no_proxy": "localhost,[2001:db8::1]:8080"}
        self.assertEqual(sanitize_no_proxy(env), 2)
        self.assertEqual(env["NO_PROXY"], "localhost,::1")
        self.assertEqual(env["no_proxy"], "localhost,2001:db8::1")

    def test_idempotent_and_tolerant_of_missing_entries(self):
        env = {"NO_PROXY": "localhost,::1"}
        self.assertEqual(sanitize_no_proxy(env), 0)
        self.assertEqual(sanitize_no_proxy({}), 0)


class ProxyConfigDescribeTests(unittest.TestCase):
    def test_describe_mentions_direct_when_unset(self):
        self.assertIn("direct", ProxyConfig().describe())


if __name__ == "__main__":
    unittest.main()
