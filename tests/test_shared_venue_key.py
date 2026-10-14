"""`venue_key` 判据的**唯一实体**在阶段 1 的 `code/shared/venue_key.py`（本仓库是宿主）。

用户 2026-10-15 定：**一切 venue 判定以 `venue_key` 为唯一权威**。这一份实现（词表 +
匹配 + `<规范名><年份>` 归一 + 预印本服务器 + `venue_key_name()` 去年份）只有一份实体，
阶段 2 / 3.a.1 / 3.a.2 / 阶段 4 经各自的 `code/_shared.py` 注入后
`from shared.venue_key import ...` 消费同一份。

`code/venue_key.py` 只是**薄再导出**（保住历史 import 路径）——**不许再写第二份实现**。
见根 `AGENTS.md`「跨仓库共享代码」。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from code import venue_key as thin  # noqa: E402
from code.shared import venue_key as shared  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 对外 API（含本次新增的 `venue_key_name`）——薄模块必须逐个指向共享实体
PUBLIC_API = (
    "venue_match", "normalize_venue_text", "is_missing_venue", "canonical_venue",
    "preprint_venue_key", "venue_type", "pick_venue_year", "resolve_venue_key",
    "cite_dialog_venue", "citation_venue_key", "venue_key", "venue_key_source",
    "describe_venue_key", "venue_key_name",
)


class SharedVenueKeyTest(unittest.TestCase):
    def test_the_rule_is_the_shared_one(self):
        for name in PUBLIC_API:
            self.assertIs(getattr(thin, name), getattr(shared, name),
                          f"{name} 必须是共享件里的同一个对象")
        self.assertIs(thin.VENUE_CANONICAL, shared.VENUE_CANONICAL)
        self.assertIs(thin.PREPRINT_SERVER_KEYS, shared.PREPRINT_SERVER_KEYS)

    def test_thin_module_has_no_implementation_copy(self):
        path = os.path.join(REPO, "code", "venue_key.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("from code.shared.venue_key import", src)
        for marker in ("VENUE_CANONICAL = {", "def resolve_venue_key", "def venue_key_name",
                       "def canonical_venue", "PREPRINT_SERVER_KEYS = ("):
            self.assertNotIn(marker, src, "薄模块里不许有实现副本")
        # 体量粗守卫：薄模块只有 import，今天约 25 行
        self.assertLess(len(src.splitlines()), 60, "薄模块不该长大")

    def test_shared_dir_is_a_namespace_package(self):
        """`shared` 必须是 PEP 420 命名空间包：几个宿主共同贡献同一个 `shared.*`。"""
        self.assertFalse(
            os.path.isfile(os.path.join(REPO, "code", "shared", "__init__.py")),
            "code/shared/ 下不许放 __init__.py（否则别的宿主的共享模块就导入不到了）",
        )

    def test_the_main_test_file_exercises_the_shared_path(self):
        """功能测试直接测共享件，避免只测到薄模块这一层。"""
        path = os.path.join(REPO, "tests", "test_venue_key.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("from code.shared.venue_key import", src)


class VenueKeyNameTest(unittest.TestCase):
    """`venue_key_name()`：从规范化 key 取发表地名（**只去掉尾部年份**）。"""

    def test_conference_keys_lose_their_year(self):
        for key, expected in (("ACL2026", "ACL"),
                              ("Findings of ACL2026", "Findings of ACL"),
                              ("ICASSP2025", "ICASSP"),
                              ("NeurIPS2024", "NeurIPS"),
                              ("EMNLP2025", "EMNLP")):
            self.assertEqual(shared.venue_key_name(key), expected, key)

    def test_year_less_keys_pass_through(self):
        for key in ("TACL", "TASLP", "Nature Machine Intelligence", "arXiv",
                    "bioRxiv", "ACL", "Findings of ACL", "自然言語処理", "中文信息学报"):
            self.assertEqual(shared.venue_key_name(key), key)

    def test_non_string_and_empty_are_empty(self):
        for value in (None, "", "   ", 2024, ["ACL2026"], {}):
            self.assertEqual(shared.venue_key_name(value), "", repr(value))

    def test_out_of_range_or_glued_digits_are_left_alone(self):
        for key in ("ACL1899", "ACL2036", "SIGIR20245"):
            self.assertEqual(shared.venue_key_name(key), key)

    def test_round_trip_with_the_keyer(self):
        """真实 key → name：会议剥年份后仍是同一个规范名，期刊/预印本原样。"""
        self.assertEqual(shared.venue_key_name(shared.venue_key("ACL 2024")), "ACL")
        self.assertEqual(shared.venue_key_name(shared.venue_key("TACL")), "TACL")
        self.assertEqual(shared.venue_key_name(shared.venue_key("arXiv preprint arXiv:2402.1")),
                         "arXiv")
        self.assertEqual(
            shared.venue_key_name(shared.venue_key(
                "Findings of the Association for Computational Linguistics: EMNLP 2024")),
            "Findings of EMNLP")


if __name__ == "__main__":
    unittest.main()
