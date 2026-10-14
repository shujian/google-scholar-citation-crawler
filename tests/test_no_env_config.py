"""Source-level guard: this project takes no configuration from environment
variables.

Configuration priority is **command-line argument > ``crawler_config.json`` >
code default** (see ``code.config``).  The single documented exception is
``code/lib_env.py``: it stages environment values *for third-party libraries*
(selenium telemetry switches, the NO_PROXY rewrite httpx 0.27.2 needs) and reads
the conventional ``https_proxy`` / ``http_proxy`` / ``no_proxy`` pair as the
proxy's last-resort default.

Every other module under ``code/`` must stay free of ``os.getenv``,
``os.environ.get`` and ``os.environ[...]``.
"""

import os
import pathlib
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CODE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "code")

#: The one module allowed to touch ``os.environ`` (documented in its docstring).
EXEMPT_MODULES = {"lib_env.py"}

FORBIDDEN_PATTERNS = (
    re.compile(r"os\.getenv\s*\("),
    re.compile(r"os\.environ\.get\s*\("),
    re.compile(r"os\.environ\s*\["),
)

#: Proxy names that ``lib_env`` may read as the last-resort default.
ALLOWED_ENV_READ_KEYS = {"https_proxy", "http_proxy", "no_proxy", "NO_PROXY"}


def _iter_code_modules():
    """递归遍历 ``code/`` 下的全部 ``.py``（**含子目录**）。

    2026-10-14 起 `code/` 下有了 `data_upgrade/`（数据修复/升级工具）。守卫原先只
    `os.listdir(CODE_DIR)` 顶层，模块一搬进子目录就**不再被扫描** —— 覆盖面静默缩小，
    正是"禁止读环境变量"这条边界最不该出现的情况。这里改为递归。
    """
    for path in sorted(pathlib.Path(CODE_DIR).rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield str(path.relative_to(CODE_DIR)), str(path)


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


class NoEnvConfigTests(unittest.TestCase):
    def test_code_dir_exists(self):
        self.assertTrue(os.path.isdir(CODE_DIR), f"missing package dir: {CODE_DIR}")

    def test_no_environment_reads_outside_lib_env(self):
        offenders = []
        for name, path in _iter_code_modules():
            if name in EXEMPT_MODULES:
                continue
            source = _read(path)
            for pattern in FORBIDDEN_PATTERNS:
                for match in pattern.finditer(source):
                    line = source[:match.start()].count("\n") + 1
                    offenders.append(f"code/{name}:{line}: {match.group(0)}")
        self.assertEqual(
            offenders,
            [],
            "configuration must not be read from environment variables; "
            "use a CLI argument, crawler_config.json (code.config) or a code "
            "default instead:\n  " + "\n  ".join(offenders),
        )

    def test_lib_env_is_the_only_exempt_module_and_documents_why(self):
        for name in EXEMPT_MODULES:
            self.assertTrue(os.path.isfile(os.path.join(CODE_DIR, name)),
                            f"exempt module missing: code/{name}")
        source = _read(os.path.join(CODE_DIR, "lib_env.py"))
        self.assertIn("os.environ", source)
        self.assertNotIn("os.getenv", source)
        # The exemption must be explained where it is granted.
        self.assertIn("ONLY module in ``code/``", source)

    def test_lib_env_only_reads_proxy_related_environment_keys(self):
        source = _read(os.path.join(CODE_DIR, "lib_env.py"))
        # The proxy key tuples are the only source of readable env names.
        self.assertIn("PROXY_ENV_KEYS = ('https_proxy', 'http_proxy')", source)
        self.assertIn("NO_PROXY_ENV_KEYS = ('no_proxy', 'NO_PROXY')", source)
        read_args = {arg.strip() for arg in
                     re.findall(r"os\.environ\.get\(([^)]*)\)", source)}
        self.assertTrue(read_args, "expected the proxy fallback reads in lib_env.py")
        for arg in read_args:
            allowed = arg == "key"  # looped over the tuples asserted above
            allowed = allowed or arg.strip("'\"") in ALLOWED_ENV_READ_KEYS
            self.assertTrue(allowed, f"unexpected environment read in lib_env.py: {arg}")

    def test_cli_exposes_proxy_and_config_flags(self):
        source = _read(os.path.join(CODE_DIR, "cli.py"))
        for flag in ("--proxy-url", "--no-proxy", "--config"):
            self.assertIn(flag, source, f"code/cli.py must expose {flag}")

    def test_config_module_owns_the_config_file_lookup(self):
        source = _read(os.path.join(CODE_DIR, "config.py"))
        self.assertIn('CONFIG_FILENAME = "crawler_config.json"', source)
        self.assertIn("def resolve_proxy_config(", source)
        self.assertIn("def load_config_file(", source)


if __name__ == "__main__":
    unittest.main()


class HostGuardCoverageTest(unittest.TestCase):
    """守卫必须覆盖 `code/` 的**子目录**（2026-10-14 回归：搬进 data_upgrade/ 后一度漏扫）。"""

    def test_subdirectory_modules_are_scanned(self):
        names = [name for name, _ in _iter_code_modules()]
        assert names, "守卫没扫到任何模块"
        assert any(name.startswith("data_upgrade" + os.sep) for name in names), \
            f"子目录模块没被扫到（只扫到顶层？）: {names[:5]}"
        assert any(name.endswith(".py") for name in names)
