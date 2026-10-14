"""Source-level guard: this project takes no configuration from environment
variables.

Configuration priority is **command-line argument > ``crawler_config.json`` >
code default** (see ``code.config``).  The single documented exception is
``code/lib_env.py``: it stages environment values *for third-party libraries*
(selenium telemetry switches, the NO_PROXY rewrite httpx 0.27.2 needs) and reads
the conventional ``https_proxy`` / ``http_proxy`` / ``no_proxy`` pair as the
proxy's last-resort default.

Every other module under ``code/`` must stay free of ``os.getenv``,
``os.environ.get`` and ``os.environ[...]``.  The scan is **recursive**: subpackages
such as ``code/data_upgrade/`` (one-off data repair/upgrade tools) and
``code/shared/`` are covered too, so moving a module into a subdirectory cannot
quietly retire it from this boundary check.
"""

import os
import pathlib
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CODE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "code")

#: The one module allowed to touch ``os.environ`` (documented in its docstring).
#: Keys are paths **relative to ``code/``**: the exemption is path-based, so it
#: stays exactly ``code/lib_env.py`` even though the scan below recurses.  A
#: same-named module in a subdirectory (e.g. the shared engine
#: ``code/shared/lib_env.py``) is *not* spared -- it must keep to the bare
#: ``os.environ`` default mapping and stay clear of the forbidden forms.
EXEMPT_MODULES = {"lib_env.py"}

FORBIDDEN_PATTERNS = (
    re.compile(r"os\.getenv\s*\("),
    re.compile(r"os\.environ\.get\s*\("),
    re.compile(r"os\.environ\s*\["),
)

#: Proxy names that ``lib_env`` may read as the last-resort default.
ALLOWED_ENV_READ_KEYS = {"https_proxy", "http_proxy", "no_proxy", "NO_PROXY"}


def _iter_code_modules(root=None):
    """递归遍历代码根下的全部 ``.py``（**含子目录**），产出 ``(相对路径, 绝对路径)``。

    ``root`` 默认是 ``code/``；测试会传入临时目录以钉住"子目录也被扫描"。
    相对路径一律用 POSIX 分隔符，断言与报错信息因此与平台无关。
    """
    base = pathlib.Path(CODE_DIR if root is None else root)
    for path in sorted(base.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path.relative_to(base).as_posix(), str(path)


def _find_env_reads(root=None):
    """返回每处违规读取的 ``code/<相对路径>:<行号>: <命中串>``（豁免模块除外）。"""
    offenders = []
    for name, path in _iter_code_modules(root):
        if name in EXEMPT_MODULES:
            continue
        source = _read(path)
        for pattern in FORBIDDEN_PATTERNS:
            for match in pattern.finditer(source):
                line = source[:match.start()].count("\n") + 1
                offenders.append(f"code/{name}:{line}: {match.group(0)}")
    return offenders


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


class NoEnvConfigTests(unittest.TestCase):
    def test_code_dir_exists(self):
        self.assertTrue(os.path.isdir(CODE_DIR), f"missing package dir: {CODE_DIR}")

    def test_no_environment_reads_outside_lib_env(self):
        offenders = _find_env_reads()
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


class SubdirectoryCoverageTests(unittest.TestCase):
    """守卫必须真的**报出**子目录里的违规，而不只是把它们枚举出来。

    回归（2026-10-14）：`code/` 下新增了 `data_upgrade/`（一次性数据修复/升级工具）。
    守卫原先只 `os.listdir(CODE_DIR)` 顶层，模块一搬进子目录就**不再被扫描** ——
    覆盖面静默缩小，正是"禁止读环境变量"这条边界最不该出现的情况。
    这里在**临时目录**上钉住递归行为（不留长期夹具文件）。
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)

    def _write(self, relative, source):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    def test_a_reading_module_in_a_subdirectory_is_reported(self):
        self._write("data_upgrade/fake_repair.py",
                    "import os\n\nTARGET = os.environ.get('OUTPUT_DIR')\n")
        self.assertEqual(
            _find_env_reads(self.root),
            ["code/data_upgrade/fake_repair.py:3: os.environ.get("])

    def test_a_deeply_nested_reading_module_is_reported(self):
        self._write("a/b/fake.py", "import os\nos.environ['TARGET'] = 'x'\n")
        self.assertEqual(
            _find_env_reads(self.root), ["code/a/b/fake.py:2: os.environ["])

    def test_pycache_is_skipped(self):
        self._write("__pycache__/stale.py", "import os\nos.environ.get('X')\n")
        self._write("sub/__pycache__/stale.py", "import os\nos.environ.get('X')\n")
        self.assertEqual(_find_env_reads(self.root), [])

    def test_only_the_top_level_lib_env_is_exempt(self):
        self._write("lib_env.py", "import os\nos.environ.get('https_proxy')\n")
        self.assertEqual(_find_env_reads(self.root), [])
        # 豁免按路径而非文件名：子目录里的同名模块**不**被放过。
        self._write("shared/lib_env.py", "import os\nos.environ.get('NO_PROXY')\n")
        self.assertEqual(
            _find_env_reads(self.root),
            ["code/shared/lib_env.py:2: os.environ.get("])

    def test_the_real_code_tree_includes_the_subdirectory(self):
        names = [name for name, _ in _iter_code_modules()]
        self.assertIn("data_upgrade/__init__.py", names)
        self.assertIn("data_upgrade/fix_output_fetch_state.py", names)
        self.assertIn("shared/lib_env.py", names)
        self.assertIn("lib_env.py", names)


if __name__ == "__main__":
    unittest.main()
