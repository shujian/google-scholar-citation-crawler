"""引用编号的持久分配（阶段 1）：只增不改、只消失不复用、身份判据与下游同源。"""

import json
import os
import tempfile
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from code import citation_index as CI  # noqa: E402


def _cite(title, cites_id=None, year="2024", authors="A Wang", url=None):
    return {"title": title, "cites_id": cites_id, "year": year, "authors": authors, "url": url}


class AssignTests(unittest.TestCase):

    def test_first_assignment_numbers_from_one_in_list_order(self):
        state, indices = CI.assign_indices([_cite("A", "1"), _cite("B", "2")])
        self.assertEqual(indices, [1, 2])
        self.assertEqual(state["next"], 3)

    def test_known_citations_keep_their_number_even_when_reordered(self):
        state, _ = CI.assign_indices([_cite("A", "1"), _cite("B", "2")])
        again, indices = CI.assign_indices([_cite("B", "2"), _cite("A", "1")], state)
        self.assertEqual(indices, [2, 1])          # 逐字沿用，不按列表位置重排
        self.assertEqual(again["next"], 3)

    def test_new_citation_takes_the_water_mark(self):
        state, _ = CI.assign_indices([_cite("A", "1"), _cite("B", "2")])
        _, indices = CI.assign_indices([_cite("B", "2"), _cite("C", "3"), _cite("A", "1")], state)
        self.assertEqual(indices, [2, 3, 1])
        self.assertEqual(sorted(indices), [1, 2, 3])

    def test_a_vanished_number_is_never_reused(self):
        state, _ = CI.assign_indices([_cite("A", "1"), _cite("B", "2"), _cite("C", "3")])
        # B 消失：2 号进 tombstone
        state2, _ = CI.assign_indices([_cite("A", "1"), _cite("C", "3")], state)
        self.assertEqual(state2["retired"], [2])
        # 新引文 D 必须拿 4（不能捡回 2）
        _, indices = CI.assign_indices([_cite("A", "1"), _cite("C", "3"), _cite("D", "4")], state2)
        self.assertEqual(indices, [1, 3, 4])

    def test_a_vanished_citation_that_comes_back_gets_a_new_number(self):
        state, _ = CI.assign_indices([_cite("A", "1"), _cite("B", "2")])
        state2, _ = CI.assign_indices([_cite("A", "1")], state)      # B 消失
        _, indices = CI.assign_indices([_cite("A", "1"), _cite("B", "2")], state2)
        self.assertEqual(indices, [1, 3])                            # 不是 2

    def test_cites_id_churn_is_a_new_citation_by_the_shared_rule(self):
        """两侧都有**真实** `cites_id` 且不等 → 共享判据判**不同**（根 AGENTS 既定口径）。

        所以"换代"在这里表现为**新引文拿新号**（旧号进 tombstone）—— 这正是共享判据的语义，
        分配器**不自行发明**更宽松的规则（那会让两侧判断漂移 ✗）。下游遇到换代本来就会重算
        （见根 AGENTS「`cites_id` 会换代」）。
        """
        state, _ = CI.assign_indices([_cite("Same Work", "old-id")])
        state2, indices = CI.assign_indices([_cite("Same Work", "new-id")], state)
        self.assertEqual(indices, [2])
        self.assertEqual(state2["retired"], [1])

    def test_without_real_ids_the_title_year_author_identity_decides(self):
        """没有真实 id 时（合成键/缺失）才走标题+年份+首作者的兜底 —— 同一条要沿用编号。"""
        state, _ = CI.assign_indices([_cite("Same Work", None, year="2020", authors="A Wang")])
        state2, _ = CI.assign_indices([_cite("Same Work", "no_id:x|samework|2020|wang",
                                             year="2020", authors="A Wang")], state)
        self.assertEqual([r["index"] for r in state2["assigned"]], [1])

    def test_a_different_work_with_the_same_title_does_not_share_a_number(self):
        state, _ = CI.assign_indices([_cite("Same Title", "1", year="2020", authors="A Wang")])
        _, indices = CI.assign_indices(
            [_cite("Same Title", "2", year="2021", authors="B Li")], state)
        self.assertEqual(indices, [2])                # 年份/首作者矛盾 → 不同（K123）

    def test_empty_and_broken_state_is_tolerated(self):
        for broken in (None, {}, {"assigned": "junk", "next": "x", "retired": [0, -3, "a"]},
                       {"assigned": [{"index": 2}, {"index": 2}, {"nope": 1}]}):
            with self.subTest(broken=broken):
                state, indices = CI.assign_indices([_cite("A", "1")], broken)
                self.assertEqual(len(indices), 1)
                self.assertGreaterEqual(indices[0], 1)
                self.assertGreater(state["next"], indices[0])

    def test_water_mark_never_goes_backwards(self):
        state = {"assigned": [], "next": 1, "retired": [7]}
        state2, indices = CI.assign_indices([_cite("A", "1")], state)
        self.assertEqual(indices, [8])                # 跳过退役号，且水位已抬到 8+
        self.assertEqual(state2["next"], 9)

    def test_no_citations_at_all_only_retires(self):
        state, _ = CI.assign_indices([_cite("A", "1"), _cite("B", "2")])
        state2, indices = CI.assign_indices([], state)
        self.assertEqual(indices, [])
        self.assertEqual(state2["retired"], [1, 2])
        self.assertEqual(state2["next"], 3)


if __name__ == "__main__":
    unittest.main()


class CiteFileTests(unittest.TestCase):
    """`citations/<NNNN>-<slug>.json`：命名与 PDF **同规则**、只写有 cite 的、内容不变不重写。"""

    def test_basename_matches_the_pdf_rule(self):
        self.assertEqual(CI.citation_basename(37, "R-PRM: Reasoning-driven process reward modeling"),
                         "0037-R-PRM_Reasoning-driven_process_reward_modeling")
        self.assertEqual(CI.citation_basename(7, ""), "0007-untitled")
        self.assertEqual(CI.citation_basename(7, None), "0007-untitled")

    def _root(self, tmp, with_dir=True):
        root = os.path.join(tmp, "papers")
        if with_dir:
            os.makedirs(os.path.join(root, "001-Slug"))
        return root, {"no": 1, "title": "Slug"}

    def test_writes_then_skips_unchanged_and_rewrites_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, pub = self._root(tmp)
            citation = {"index": 37, "cites_id": "c1", "title": "Some Work",
                        "cite": {"chicago": "X", "bibtex": {"title": "Some Work"}}}
            self.assertEqual(CI.write_cite_files(root, pub, [citation], log=lambda *a: None), (1, 0, 0))
            self.assertEqual(CI.write_cite_files(root, pub, [citation], log=lambda *a: None), (0, 1, 0))
            citation["cite"] = {"chicago": "Y"}
            self.assertEqual(CI.write_cite_files(root, pub, [citation], log=lambda *a: None), (1, 0, 0))
            path = os.path.join(root, "001-Slug", "citations", "0037-Some_Work.json")
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["cite"], {"chicago": "Y"})

    def test_citations_without_cite_or_index_produce_no_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, pub = self._root(tmp)
            citations = [{"index": 1, "title": "No cite"}, {"title": "No index", "cite": {"apa": "x"}}]
            self.assertEqual(CI.write_cite_files(root, pub, citations, log=lambda *a: None), (0, 0, 0))
            # 目录本身建不建无所谓（阶段 2 也会建），**关键是没有文件** ✓
            cites_dir = os.path.join(root, "001-Slug", "citations")
            self.assertEqual(os.listdir(cites_dir) if os.path.isdir(cites_dir) else [], [])

    def test_missing_paper_dir_is_reported_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, pub = self._root(tmp, with_dir=False)
            citation = {"index": 1, "title": "T", "cite": {"apa": "x"}}
            self.assertEqual(CI.write_cite_files(root, pub, [citation], log=lambda *a: None), (0, 0, 1))
