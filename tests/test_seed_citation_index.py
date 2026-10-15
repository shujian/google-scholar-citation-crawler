"""一次性种子工具（`code/data_upgrade/seed_citation_index.py`）的 hermetic 测试。

钉住三件事：① 论文**按身份配对**（url → 标题），绝不按 `pub.no`；② `assigned`/`next`/`retired`
的构造（退役号 = 历史空洞，水位 = max+1）；③ `--apply` 先备份、可幂等重跑。
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from code.data_upgrade import seed_citation_index as S  # noqa: E402


def _cite(index, title="T", cites_id=None, year="2024", authors="A Wang", url=None):
    return {"index": index, "title": title, "cites_id": cites_id, "year": year,
            "authors": authors, "url": url}


class BuildStateTests(unittest.TestCase):

    def test_holes_become_retired_and_next_is_max_plus_one(self):
        state = S.build_state([_cite(1), _cite(3)])
        self.assertEqual([r["index"] for r in state["assigned"]], [1, 3])
        self.assertEqual(state["next"], 4)
        self.assertEqual(state["retired"], [2])

    def test_broken_entries_are_skipped(self):
        state = S.build_state([_cite(1), {"index": 1}, {"index": "x"}, "junk", {"index": 0}])
        self.assertEqual([r["index"] for r in state["assigned"]], [1])
        self.assertEqual(state["next"], 2)

    def test_identity_fields_are_kept_for_later_matching(self):
        state = S.build_state([_cite(1, title="Some Work", cites_id="42")])
        record = state["assigned"][0]
        self.assertEqual(record["title"], "Some Work")
        self.assertEqual(record["cites_id"], "42")


class PairingTests(unittest.TestCase):

    def _tree(self, tmp, meta):
        root = os.path.join(tmp, "papers")
        os.makedirs(os.path.join(root, "001-Slug"))
        with open(os.path.join(root, "001-Slug", "paper_meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f)
        return root

    def test_pairs_by_url_not_by_number(self):
        """目录编号已被重排（这里是 001），而 pub 里可能是别的旧编号 —— 必须按 url 配。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, {"no": 7, "url": "https://x/1", "title": "Slug"})
            paper_dir, how = S.find_paper_dir(root, {"no": 1, "url": "https://x/1", "title": "Slug"})
            self.assertEqual(how, "url")
            self.assertTrue(paper_dir.endswith("001-Slug"))

    def test_falls_back_to_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, {"no": 7, "url": "", "title": "Some Title!"})
            _, how = S.find_paper_dir(root, {"no": 1, "url": "", "title": "some title"})
            self.assertEqual(how, "title")


class PlanAndApplyTests(unittest.TestCase):

    def _fixture(self, tmp):
        output = os.path.join(tmp, "output")
        os.makedirs(output)
        profile = os.path.join(output, "author_X_paper_citations.json")
        with open(profile, "w", encoding="utf-8") as f:
            json.dump({"papers": [{"pub": {"no": 1, "url": "https://x/1", "title": "Slug"},
                                   "citations": [{"title": "A", "cites_id": "1"}]}]}, f)
        root = os.path.join(tmp, "papers")
        os.makedirs(os.path.join(root, "001-Slug"))
        with open(os.path.join(root, "001-Slug", "paper_meta.json"), "w", encoding="utf-8") as f:
            json.dump({"no": 1, "url": "https://x/1", "title": "Slug"}, f)
        with open(os.path.join(root, "001-Slug", "citations_meta.json"), "w", encoding="utf-8") as f:
            json.dump([_cite(1, "A", "1"), _cite(4, "B", "2")], f)
        return profile, root

    def test_plan_reports_and_apply_writes_with_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile, root = self._fixture(tmp)
            papers, stats = S.plan_seed(profile, root)
            self.assertEqual((stats["papers"], stats["unmatched"], stats["entries"]), (1, 0, 2))
            self.assertEqual((stats["matched_url"], stats["already_seeded"]), (1, 0))
            self.assertEqual(papers[0]["state"]["next"], 5)
            self.assertEqual(papers[0]["state"]["retired"], [2, 3])

            written = S.apply_seed(profile, papers, os.path.join(tmp, "output"), log=lambda *a: None)
            self.assertEqual(written, 1)
            with open(profile, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["papers"][0]["citation_index"]["next"], 5)
            backups = [d for d in os.listdir(os.path.join(tmp, "output"))
                       if d.startswith("citation_index_seed_")]
            self.assertEqual(len(backups), 1)
            self.assertTrue(os.path.isfile(os.path.join(
                tmp, "output", backups[0], "manifest.json")))

    def test_second_run_is_idempotent_and_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile, root = self._fixture(tmp)
            papers, _ = S.plan_seed(profile, root)
            S.apply_seed(profile, papers, os.path.join(tmp, "output"), log=lambda *a: None)
            again, stats = S.plan_seed(profile, root)
            self.assertEqual(stats["already_seeded"], 1)
            self.assertEqual(again[0]["previous"]["next"], 5)   # 状态就是刚才写下的那份

    def test_unmatched_paper_is_reported_not_crashed(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile, root = self._fixture(tmp)
            with open(profile, encoding="utf-8") as f:
                data = json.load(f)
            data["papers"].append({"pub": {"no": 9, "url": "https://nowhere", "title": "Nope"},
                                   "citations": []})
            with open(profile, "w", encoding="utf-8") as f:
                json.dump(data, f)
            _, stats = S.plan_seed(profile, root)
            self.assertEqual(stats["unmatched"], 1)


if __name__ == "__main__":
    unittest.main()
