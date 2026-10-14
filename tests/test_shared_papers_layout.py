"""`code/shared/papers_layout.py`：本仓库是**宿主**，须能脱离任何消费方独立工作。

2026-10-15 从阶段 2 的 `code/renumber_papers.py` 整体搬来（见 `KNOWN_ISSUES.md` Y-6）：
`papers/` 的物理位置在阶段 1，所以"论文目录的创建/编号/改名/归档"的判据也归阶段 1。
本文件钉住：① 只用标准库（不 import 任何项目模块）；② 路径注入的入口能用（`plan` 是纯函数）；
③ 文件名消毒与阶段 2 的旧实现行为一致（默认上限走调用时解析）。
"""

import ast
import inspect
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from code.shared import papers_layout as L  # noqa: E402


class SharedPapersLayoutHostTests(unittest.TestCase):

    def test_only_stdlib_imports_at_module_level(self):
        """宿主必须是自足的：模块级别只能 import 标准库。"""
        tree = ast.parse(inspect.getsource(L))
        allowed = {'__future__', 'datetime', 'hashlib', 'json', 'os', 're'}
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertIn(alias.name.split('.')[0], allowed, alias.name)
            elif isinstance(node, ast.ImportFrom):
                self.assertIn((node.module or '').split('.')[0], allowed, node.module)

    def test_paths_are_injected_not_hardcoded(self):
        """`plan(base_dir, papers)` 是纯函数：给临时目录就能用，不需要任何配置。"""
        with tempfile.TemporaryDirectory() as tmp:
            plan = L.plan(tmp, [{'pub': {'no': 1, 'title': 'Some Paper'}}])
            self.assertEqual(plan['base_dir'], tmp)
            self.assertEqual(plan['moves'], [])

    def test_sanitize_filename_matches_the_moved_implementation(self):
        self.assertEqual(L.sanitize_filename('a/b:c*d'), 'a_b_c_d')
        self.assertEqual(L.sanitize_filename('  ._name_  '), 'name')
        self.assertEqual(L.sanitize_filename(''), 'untitled')
        self.assertEqual(len(L.sanitize_filename('a' * 300)), 120)  # 默认上限

    def test_target_dir_name_uses_the_index_and_a_slug(self):
        name = L.target_dir_name({'no': 7, 'title': 'R-PRM: Reasoning-driven process reward modeling'})
        self.assertTrue(name.startswith('007-'), name)
        self.assertNotIn('/', name)
        self.assertNotIn(':', name)


if __name__ == '__main__':
    unittest.main()
