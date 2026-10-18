"""把存量 per-citation 产物**改名**到统一新名 `output_NNNN.json`（Y-8 ③ 的存量部分）。

**背景**（`KNOWN_ISSUES.md` Y-8）：三阶段的 per-citation 产物统一叫 `output_NNNN.json`，
命名权威是**本阶段**的 `code/shared/artifact_sync.py`（`output_name()`）。三个**写者**早已
只写新名 ✓、读取方也早已"新名优先、旧名回落"✓；但**盘上存量**仍是旧名
（`enrich_info_NNNN.json` / `analysis_info_NNNN.json` / `citation_analysis_NNNN.json`，
2026-10-16 实测 **3,315** 个 ✗）。本工具是那一步：**只改名、不改内容** ✓。

**为什么宿主在本阶段**：改名用的是 `shared/artifact_sync.py` 的命名规则（本阶段是宿主 ✓），
与 `archive_artifacts()` 同一族 —— 它也只按"名字"动别人的目录 ✓。**内容一个字节都不碰** ✗。

**边界（逐条有测试）**：

* **只改名**：`shutil.move` 前后校验 sha256 一致 ✓（失败就回滚该文件并报告 ✗）；
* **绝不覆盖**：目标名已存在 → 只报告、跳过 ✓；
* **只动在册编号**：文件名里的编号必须在该篇 `citations_meta.json` 的 `index` 集合里 ✓
  （不在册的交给巡检报告 / 隔离工具，不当成"改名"的对象 ✗）；
* 默认 **dry-run** ✓；`--apply` 写 `manifest.json`（旧名 / 新名 / sha256）✓；**幂等** ✓。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

#: 三个阶段目录 → 旧名前缀（新名统一 `output_NNNN.json` ✓）
STAGES = (("enrich_info", "enrich_info_"),
          ("analysis_info", "analysis_info_"),
          ("analysis_content", "citation_analysis_"))


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def authoritative_indices(paper_dir):
    """该篇在册的编号集合（`citations_meta.json` 的 `index` ✓；读不到则空 ✓）。"""
    try:
        with open(os.path.join(paper_dir, "citations_meta.json"), encoding="utf-8") as handle:
            rows = json.load(handle)
    except (OSError, ValueError):
        return set()
    if not isinstance(rows, list):
        return set()
    return {int(row["index"]) for row in rows
            if isinstance(row, dict) and str(row.get("index", "")).isdigit()}


def plan(paper_dir, indices):
    """该篇要改名的文件：`[(旧名, 新名), …]`（只算在册编号、目标不存在的 ✓）。"""
    from code.shared.artifact_sync import output_name

    moves = []
    for stage, prefix in STAGES:
        stage_dir = os.path.join(paper_dir, stage)
        if not os.path.isdir(stage_dir):
            continue
        for name in sorted(os.listdir(stage_dir)):
            match = re.fullmatch(re.escape(prefix) + r"(\d+)\.json", name)
            if not match:
                continue
            index = int(match.group(1))
            if index not in indices:
                continue
            target = output_name(index)
            if target == name or os.path.exists(os.path.join(stage_dir, target)):
                continue
            moves.append((stage, name, target))
    return moves


def apply_moves(paper_dir, moves, backup_dir, log=print):
    """执行改名（sha256 前后一致 ✓），返回 manifest 条目。"""
    records = []
    for stage, old_name, new_name in moves:
        stage_dir = os.path.join(paper_dir, stage)
        src = os.path.join(stage_dir, old_name)
        dst = os.path.join(stage_dir, new_name)
        before = _sha256(src)
        os.makedirs(backup_dir, exist_ok=True)
        shutil.copy2(src, os.path.join(backup_dir, "%s__%s" % (stage, old_name)))  # 备份「移走前」的副本 ✓
        shutil.move(src, dst)
        after = _sha256(dst)
        if before != after:                       # 内容变了 → 回滚这一条并报告 ✗
            shutil.move(dst, src)
            log("  ✗ %s/%s 改名后内容不一致，已回滚" % (stage, old_name))
            continue
        records.append({"paper": os.path.basename(paper_dir), "stage": stage,
                        "old": old_name, "new": new_name, "sha256": before})
    return records


def _default_papers_root():
    """默认的论文数据树：`output/author_<ID>_papers`（从抓取产物名里取作者 ID ✓）。"""
    import glob

    from code.config import papers_root

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    found = sorted(glob.glob(os.path.join(repo_root, "output", "author_*_paper_citations.json")))
    if not found:
        return ""
    author = os.path.basename(found[0])[len("author_"):-len("_paper_citations.json")]
    return str(papers_root(os.path.join(repo_root, "output"), author))


def main(argv=None):

    parser = argparse.ArgumentParser(description="把旧名 per-citation 产物改名成 output_NNNN.json")
    parser.add_argument("--papers-root", default=None, help="论文数据树（默认取阶段 1 的 papers 根）")
    parser.add_argument("--apply", action="store_true", help="真的改名（默认只报告）")
    parser.add_argument("--backup-dir", default=None,
                        help="改名前的副本目录（默认 <repo>/output/rename_artifacts_<时间戳>）")
    args = parser.parse_args(argv)

    root = args.papers_root or _default_papers_root()
    if not root or not os.path.isdir(root):
        print("❌ 论文数据树不存在：%s" % root, file=sys.stderr)
        return 1
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = args.backup_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "output", "rename_artifacts_%s" % stamp)

    papers = sorted(d for d in os.listdir(root)
                    if d[:3].isdigit() and os.path.isdir(os.path.join(root, d)))
    total = 0
    records = []
    per_stage = {}
    for paper in papers:
        paper_dir = os.path.join(root, paper)
        moves = plan(paper_dir, authoritative_indices(paper_dir))
        if not moves:
            continue
        total += len(moves)
        for stage, _old, _new in moves:
            per_stage[stage] = per_stage.get(stage, 0) + 1
        if args.apply:
            records.extend(apply_moves(paper_dir, moves, backup_dir))
    print("%s：扫描 %d 篇，需改名 %d 个%s"
          % ("已执行" if args.apply else "预览（未改动任何文件）", len(papers), total,
             ("  " + json.dumps(per_stage, ensure_ascii=False)) if per_stage else ""))
    if args.apply:
        os.makedirs(backup_dir, exist_ok=True)
        with open(os.path.join(backup_dir, "manifest.json"), "w", encoding="utf-8") as handle:
            json.dump({"timestamp": stamp, "renamed": records, "per_stage": per_stage},
                      handle, ensure_ascii=False, indent=2)
        print("备份与 manifest：%s" % backup_dir)
        print("提示：只改名、内容 sha256 前后一致 ✓；目标已存在的一律跳过（绝不覆盖）✓")
    elif total:
        print("加 --apply 才会改名（写前把每个文件复制进备份目录 + manifest，绝不删除/覆盖）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
