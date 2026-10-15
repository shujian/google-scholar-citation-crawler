"""守卫：**阶段 1 宿主的共享件不得引用阶段 2 的模块**（Y-7，2026-10-15 用户裁定）。

Y-7 的规则 2：依赖只能**向前** —— 后阶段可以读前阶段的产物/共享件，**前阶段不得引用后阶段的模块、
符号或测试接缝**（含"运行到那一步按名字去消费方模块里取"这种隐式回指）。所有权跟着**数据产生者**走，
所以"能搬就搬"是错的：一段逻辑若改的是更晚阶段产生的数据，它就不该被前移。

本文件是**源码级**守卫（写法照 `tests/test_no_env_config.py`）：扫 `code/shared/*.py`，
出现阶段 2 的模块名即失败。`papers_repairs.py`（2026-10-15 曾把两个 `repair_*` 前移，靠
`_CONSUMER`/`_STAGE2_MODULES` 回指阶段 2）已按 Y-7 删除并回迁，这里同时钉住它不再出现。
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARED = os.path.join(ROOT, "code", "shared")

#: 阶段 2 的模块（阶段 1 的共享件**不得**引用）
FORBIDDEN = ("code.citation_downloader", "code.progress", "code.utils", "code.tool_log")


class SharedCodeHasNoBackwardDependency(unittest.TestCase):

    def _shared_files(self):
        for name in sorted(os.listdir(SHARED)):
            if name.endswith(".py"):
                yield name, os.path.join(SHARED, name)

    def test_no_stage2_module_is_referenced(self):
        for name, path in self._shared_files():
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            for module in FORBIDDEN:
                with self.subTest(shared=name, module=module):
                    self.assertNotIn(module, source,
                                     f"{name} 引用了阶段 2 的 {module}（违反 Y-7 规则 2）")

    def test_no_consumer_lookup_helper(self):
        """隐式回指的两个标志：按名字去消费方模块取符号 / 记录消费方模块。"""
        for name, path in self._shared_files():
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            for pattern in (r"_STAGE2_MODULES", r"_CONSUMER\b", r"def _resolve\("):
                with self.subTest(shared=name, pattern=pattern):
                    self.assertIsNone(re.search(pattern, source), f"{name} 出现 {pattern}")

    def test_the_reverted_module_is_gone(self):
        """`papers_repairs.py` 已按 Y-7 回迁阶段 2；它若回来，说明又"能搬就搬"了。"""
        self.assertFalse(os.path.exists(os.path.join(SHARED, "papers_repairs.py")))
        for name in ("repair_progress_locations.py", "repair_citation_filenames.py"):
            self.assertFalse(os.path.exists(os.path.join(ROOT, "code", name)),
                             f"{name} 应只存在于阶段 2")


if __name__ == "__main__":
    unittest.main()
