"""对拍：阶段 1 的抓取产物 ↔ 现有 `papers/<NNN>-<slug>/citations_meta.json` 的**非下载字段**。

**用途（Y-9 的验收手段）**：`citations_meta.json` 的**主体**（`index` / `cites_id` / 书目字段 /
`venue_key*` ✓）应改由阶段 1 生成 ✓；下载字段已拆出 ✓（不在本工具的比较范围 ✓）。
本工具**只读** ✓，逐篇逐条对拍，回答"若阶段 1 现在写主体，与现状差多少" ✓ —— 这是迁移前
必须看到的数字 ✓（判据：**0 差异** 或差异逐条可解释 ✓）。

用法：`python -m code.data_upgrade.compare_citations_meta [--author <ID>] [--limit N] [--examples 5]`
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

#: 比较的**非下载**字段（下载字段已搬出 → 不参与 ✓）
FIELDS = ("cites_id", "title", "year", "authors", "authors_raw", "url", "venue",
          "venue_key", "venue_key_source")
DOWNLOAD_FIELDS = ("download_status", "local_filename", "fail_reason")


def _norm(value):
    return "" if value is None else str(value).strip()


def _load_output(root):
    """抓取产物 → {pub_title: {index: citation_dict}}（编号取条目级 `citation_index` ✓）。"""
    paths = sorted(glob.glob(os.path.join(root, "output", "author_*_paper_citations.json")))
    result = {}
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        # 抓取产物顶层键是 `papers` ✓（不是 `publications` ✗）；编号是**条目自身的 `index`** ✓，
        # 论文条目上的 `citation_index` 只是同一份映射的副本 ✓（兜底用 ✓）。
        for publication in payload.get("papers") or payload.get("publications") or []:
            pub = publication.get("pub") or {}
            index_map = {}
            for citation in publication.get("citations") or []:
                index = citation.get("citation_index") or citation.get("index")
                if index:
                    index_map[int(index)] = citation
            key = _norm(pub.get("title")) or _norm(pub.get("url"))
            if key:
                result[key] = index_map
    return result, [os.path.basename(p) for p in paths]


def compare(papers_root, crawl_root, limit=None, examples=5, log=print):
    output, sources = _load_output(crawl_root)
    log("抓取产物: %s（%d 篇）" % (", ".join(sources) or "无", len(output)))
    totals = {"papers": 0, "unmatched_paper": 0, "citations": 0, "same": 0, "differ": 0,
              "only_meta": 0, "only_output": 0, "meta_without_download_field": 0}
    field_hits, shown = {}, 0
    paper_dirs = sorted(glob.glob(os.path.join(papers_root, "[0-9][0-9][0-9]-*")))
    for paper_dir in paper_dirs[:limit] if limit else paper_dirs:
        meta_path = os.path.join(paper_dir, "citations_meta.json")
        if not os.path.isfile(meta_path):
            continue
        with open(meta_path, encoding="utf-8") as handle:
            meta = json.load(handle)
        with open(os.path.join(paper_dir, "paper_meta.json"), encoding="utf-8") as handle:
            pub_meta = json.load(handle)
        key = _norm(pub_meta.get("title")) or _norm(pub_meta.get("url"))
        expected = output.get(key)
        totals["papers"] += 1
        if expected is None:
            totals["unmatched_paper"] += 1
            if shown < examples:
                log("  ⚠️ 抓取产物里找不到该论文: %s" % os.path.basename(paper_dir)[:44]); shown += 1
            continue
        meta_by_index = {}
        for entry in meta:
            if isinstance(entry, dict) and entry.get("index"):
                meta_by_index[int(entry["index"])] = entry
                if not any(f in entry for f in DOWNLOAD_FIELDS):
                    totals["meta_without_download_field"] += 1
        for index, entry in meta_by_index.items():
            totals["citations"] += 1
            other = expected.get(index)
            if other is None:
                totals["only_meta"] += 1
                continue
            diffs = [f for f in FIELDS if _norm(entry.get(f)) != _norm(other.get(f))]
            if diffs:
                totals["differ"] += 1
                for f in diffs:
                    field_hits[f] = field_hits.get(f, 0) + 1
                if shown < examples:
                    log("  ✗ %s #%d 差异字段 %s" % (os.path.basename(paper_dir)[:30], index, diffs))
                    for f in diffs[:2]:
                        log("      meta=%r out=%r" % (_norm(entry.get(f))[:60], _norm(other.get(f))[:60]))
                    shown += 1
            else:
                totals["same"] += 1
        totals["only_output"] += sum(1 for i in expected if i not in meta_by_index)
    log("\n=== 对拍结果 ===")
    log(json.dumps(totals, ensure_ascii=False, sort_keys=True))
    log("差异字段分布: %s" % json.dumps(dict(sorted(field_hits.items(), key=lambda kv: -kv[1])),
                                        ensure_ascii=False))
    log("判据：`differ` + `only_meta` + `only_output` 应为 0 ✓（否则逐条解释 ✓）")
    return totals


def main(argv=None):
    from code.config import OUTPUT_DIR

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--author", default=None, help="作者 ID（默认取唯一的 author_*_papers ✓）")
    parser.add_argument("--limit", type=int, default=None, help="只比前 N 篇（调试用 ✓）")
    parser.add_argument("--examples", type=int, default=5)
    args = parser.parse_args(argv)

    root = os.path.dirname(os.path.abspath(OUTPUT_DIR.rstrip("/")))
    dirs = sorted(glob.glob(os.path.join(OUTPUT_DIR, "author_*_papers")))
    if args.author:
        dirs = [d for d in dirs if args.author in d]
    if not dirs:
        print("找不到论文目录（output/author_*_papers）")
        return 1
    return 0 if compare(dirs[0], root, limit=args.limit, examples=args.examples) else 0


if __name__ == "__main__":
    raise SystemExit(main())
