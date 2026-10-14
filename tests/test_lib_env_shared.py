"""`NO_PROXY` 消毒的**引擎**只有一份（阶段 1 的 `code/shared/lib_env.py`）。

本仓库是那个引擎的宿主：`code/lib_env.py` 只保留自己的**策略**（解括号 `[::1]` → `::1`、
返回被改写的条目数、含 `[` 就回写），迭代/拆分/幂等/回写那一半逻辑调共享引擎。
另外三个项目（3.a.1 / 3.a.2 / 3.b）通过各自的 `code/_shared.py` 消费同一个引擎，
但传的是**它们自己的策略**（丢弃方括号条目、返回 bool）—— 两侧行为都由各自的测试钉死，
所以这次共享的是引擎而不是策略。见根 AGENTS.md「跨仓库共享代码」。
"""

import unittest
from unittest import mock

from code import lib_env
from code.shared import lib_env as shared_lib_env


class SharedLibEnvTest(unittest.TestCase):
    def test_the_engine_is_the_shared_one(self):
        self.assertIs(lib_env.sanitize_entries, shared_lib_env.sanitize_entries)

    def test_wrapper_delegates_with_this_projects_policy(self):
        env = {"NO_PROXY": "[::1],localhost"}
        with mock.patch.object(lib_env, "sanitize_entries", return_value=1) as engine:
            self.assertEqual(lib_env.sanitize_no_proxy(env), 1)
        self.assertTrue(engine.called, "wrapper 必须把活交给共享引擎")
        self.assertTrue(engine.call_args.kwargs["unwrap"], "本侧策略：解括号而不是丢弃")
        self.assertTrue(engine.call_args.kwargs["count"], "本侧策略：返回计数而不是 bool")

    def test_behaviour_is_unchanged(self):
        """回归：`[::1]` 解成 `::1`、带端口的去掉端口、返回被改写的条目数、幂等。"""
        env = {"NO_PROXY": "localhost,[::1],127.0.0.1,[2001:db8::1]:8080"}
        self.assertEqual(lib_env.sanitize_no_proxy(env), 2)
        self.assertEqual(env["NO_PROXY"], "localhost,::1,127.0.0.1,2001:db8::1")
        self.assertEqual(lib_env.sanitize_no_proxy(env), 0)


if __name__ == "__main__":
    unittest.main()
