"""一次性种子：把**既有的引用编号分配**登记为阶段 1 的初始状态（用户 2026-10-15）。

**背景**：引用编号从 2026-10-15 起由阶段 1 分配且**不可变**（见 `code/citation_index.py`）。
但此前编号一直是阶段 2 分配的、结果已物化在磁盘上（`citations/<NNNN>-*.pdf`、
`enrich_info_NNNN.json` 等）。若阶段 1 从零发号，既有产物会**全部对不上** ✗。

**这个工具做什么**：读阶段 2 的 `citations_meta.json`（**只读** ✓ —— Y-7 只禁止写别人的数据），
把每篇论文的 `index` + 身份字段抄成阶段 1 产物里的 `citation_index` 初始状态：

```jsonc
"citation_index": {"assigned": [{"index": 37, "cites_id": …, "title": …, "year": …,
                                 "authors": …, "url": …}, …], "next": N, "retired": [...]}
```

**论文按身份配对**（url → 标题），**绝不按 `pub.no`** —— 输入里的 `pub.no` 是上次抓取的旧排名，
而目录已被重编号（实测按编号配对会错 85 篇 ✗）。

**默认 dry-run**（只报告）；`--apply` 才写，写前把原文件备份到 `output/citation_index_seed_<stamp>/`
并记 sha256。**幂等**：已种过再跑 = 0 改动。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import glob
import hashlib
import json
import os
import re
import shutil
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _norm(text):
    value = unicodedata.normalize("NFKD", str(text or "")).casefold()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value)


def _identity_fields(entry):
    """从 `citations_meta.json` 的条目里取身份字段（与共享判据读取的字段一致）。"""
    record = {"index": entry.get("index")}
    for field in ("cites_id", "title", "year", "authors", "url"):
        value = entry.get(field)
        if value is not None:
            record[field] = value
    return record


def load_papers(profile_json):
    with open(profile_json, encoding="utf-8") as handle:
        data = json.load(handle)
    return data.get("papers") or []


def find_paper_dir(papers_root, pub):
    """按身份（url → 标题）配对论文目录 —— **不按编号**（`pub.no` 是旧排名）。"""
    url = (pub.get("url") or "").strip()
    title = _norm(pub.get("title"))
    fallback = None
    for meta_path in sorted(glob.glob(os.path.join(papers_root, "[0-9][0-9][0-9]-*", "paper_meta.json"))):
        try:
            with open(meta_path, encoding="utf-8") as handle:
                meta = json.load(handle)
        except (OSError, ValueError):
            continue
        if url and (meta.get("url") or "").strip() == url:
            return os.path.dirname(meta_path), "url"
        if title and _norm(meta.get("title")) == title and fallback is None:
            fallback = (os.path.dirname(meta_path), "title")
    return fallback or (None, None)


def build_state(meta_entries):
    """从一份 `citations_meta.json` 造出初始状态（assigned / next / retired）。"""
    assigned, seen = [], set()
    for entry in meta_entries:
        if not isinstance(entry, dict):
            continue
        number = entry.get("index")
        if not isinstance(number, int) or number < 1 or number in seen:
            continue
        seen.add(number)
        assigned.append(_identity_fields(entry))
    highest = max(seen, default=0)
    return {
        "assigned": sorted(assigned, key=lambda r: r["index"]),
        "next": highest + 1,
        "retired": [n for n in range(1, highest + 1) if n not in seen],
    }


def plan_seed(profile_json, papers_root):
    """算出"要种什么"（不写盘）。返回 (plan, stats)。"""
    papers, stats = [], {"papers": 0, "matched_url": 0, "matched_title": 0, "unmatched": 0,
                         "entries": 0, "already_seeded": 0, "no_meta": 0}
    for entry in load_papers(profile_json):
        pub = entry.get("pub") or {}
        paper_dir, how = find_paper_dir(papers_root, pub)
        if not paper_dir:
            stats["unmatched"] += 1
            continue
        stats["papers"] += 1
        stats["matched_" + how] += 1
        meta_path = os.path.join(paper_dir, "citations_meta.json")
        if not os.path.isfile(meta_path):
            stats["no_meta"] += 1
            state = {"assigned": [], "next": 1, "retired": []}
        else:
            with open(meta_path, encoding="utf-8") as handle:
                state = build_state(json.load(handle))
        stats["entries"] += len(state["assigned"])
        if isinstance(entry.get("citation_index"), dict):
            stats["already_seeded"] += 1
        papers.append({"identity": {"url": pub.get("url", ""), "title": pub.get("title", "")},
                       "paper_dir": os.path.relpath(paper_dir, papers_root),
                       "state": state, "previous": entry.get("citation_index")})
    return papers, stats


def apply_seed(profile_json, papers, output_dir, log=print):
    """把状态写回阶段 1 的产物（先备份原文件）。返回写入条数。"""
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = os.path.join(output_dir, "citation_index_seed_%s" % stamp)
    os.makedirs(backup_dir, exist_ok=True)
    with open(profile_json, "rb") as handle:
        raw = handle.read()
    shutil.copyfile(profile_json, os.path.join(backup_dir, os.path.basename(profile_json)))
    manifest = {"timestamp": stamp, "profile_json": profile_json,
                "sha256": hashlib.sha256(raw).hexdigest(), "seeded": []}

    data = json.loads(raw.decode("utf-8"))
    by_key = {(p["identity"]["url"], p["identity"]["title"]): p for p in papers}
    written = 0
    for entry in data.get("papers") or []:
        pub = entry.get("pub") or {}
        item = by_key.get((pub.get("url", ""), pub.get("title", "")))
        if not item:
            continue
        entry["citation_index"] = item["state"]
        manifest["seeded"].append({"paper_dir": item["paper_dir"],
                                   "assigned": len(item["state"]["assigned"]),
                                   "next": item["state"]["next"],
                                   "retired": len(item["state"]["retired"])})
        written += 1
    tmp = profile_json + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, profile_json)
    with open(os.path.join(backup_dir, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    log("[seed] 已种入 %d 篇；备份与 manifest 在 %s" % (written, backup_dir))
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", default="output", help="阶段 1 的 output 目录")
    parser.add_argument("--author", required=True, help="作者 ID")
    parser.add_argument("--papers-root", default=None, help="论文数据树（默认按 config 推导）")
    parser.add_argument("--apply", action="store_true", help="真正写入（默认 dry-run）")
    args = parser.parse_args(argv)

    from code.config import papers_root as papers_root_of

    profile_json = os.path.join(args.output, "author_%s_paper_citations.json" % args.author)
    if not os.path.isfile(profile_json):
        print("[seed] 找不到 %s" % profile_json)
        return 1
    root = args.papers_root or papers_root_of(args.output, args.author)
    papers, stats = plan_seed(profile_json, root)
    print("[seed] dry-run 摘要：%s" % json.dumps(stats, ensure_ascii=False))
    print("[seed] 前 3 篇：%s" % json.dumps(
        [{"dir": p["paper_dir"], "assigned": len(p["state"]["assigned"]),
          "next": p["state"]["next"], "retired": len(p["state"]["retired"])} for p in papers[:3]],
        ensure_ascii=False))
    if stats["already_seeded"]:
        print("[seed] 注意：有 %d 篇**已经种过**（重跑应显示 0 改动）" % stats["already_seeded"])
    if not args.apply:
        print("[seed] 这是 dry-run（加 --apply 才写入）")
        return 0
    apply_seed(profile_json, papers, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
