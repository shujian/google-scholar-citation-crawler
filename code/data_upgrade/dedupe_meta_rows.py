"""清掉 `citations_meta.json` 里"同一 `cites_id` 两行"的残留（数据修复，2026-10-19）。

**背景**：阶段 1 的写者以前按 **`index`** 合并旧行：同一作品换号后（例如 `1388 → 1434`），
旧号那行被当成"退役条目"原样保留 → meta 里同一个 `cites_id` 出现**两行** ✗（实测 2 例：
`1388/1434`、`1421/1435`，都在 `001-…`）。后果：`meta 行数 − 唯一作品数` 比跨论文重复多 2；
阶段 2 的下载字段（`local_filename` / `fail_reason`）还挂在**没有文件**的那行上。

写者已修（同作品换号时不再保留旧行、把阶段 2 字段交给新行）；本工具修**存量**。

**做什么**：按 `cites_id` 分组，只处理"一组多行"的：

* **保留**编号出现在阶段 1 当前产物里的那一行（它才是活行 ✓）；都找不到时保留字段最多的那行；
* 把被删行里**非主体字段**（`META_FIELDS` 之外的下载字段/运行期事实）搬给保留行 ✓；
* 主体字段一律以保留行为准（不改写）✓。

* 默认 **dry-run** ✓；`--apply` 才写（逐篇备份 + `manifest.json`，被删行整条留档 ✓）；
* 原子写、幂等 ✓。
"""

from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from code.citation_index import META_FIELDS, META_NAME  # noqa: E402


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def live_indices(entries):
    """`pub.no → 当前产物里的 index 集合`（`entries` 即 `align_citations_meta.load_output()`）。"""
    from code.data_upgrade.align_citations_meta import target_rows

    out = {}
    for pub, citations in entries:
        no = (pub or {}).get("no")
        if no is None:
            continue
        out[no] = {int(row["index"]) for row in target_rows(citations) if row.get("index")}
    return out


def plan_paper(paper_dir, live):
    """返回 `(新行列表, 被删行列表)`；没有重复时新行列表为 None。"""
    rows = _read(os.path.join(paper_dir, META_NAME))
    if not isinstance(rows, list):
        return None, []
    groups = collections.defaultdict(list)
    for row in rows:
        if isinstance(row, dict) and row.get("cites_id"):
            groups[row["cites_id"]].append(row)
    drop, dropped = set(), []
    for cid, group in groups.items():
        if len(group) < 2:
            continue
        keep = next((row for row in group if int(row.get("index") or 0) in live), None)
        if keep is None:
            keep = max(group, key=lambda row: (len(row), int(row.get("index") or 0)))
        for row in group:
            if row is keep:
                continue
            for field, value in row.items():
                if field not in META_FIELDS and field not in keep:
                    keep[field] = value
            drop.add(id(row))
            dropped.append({"cites_id": cid, "kept_index": keep.get("index"),
                            "dropped_index": row.get("index"), "record": row})
    if not drop:
        return None, []
    new_rows = sorted((row for row in rows if id(row) not in drop),
                      key=lambda row: int(row.get("index") or 0))
    return new_rows, dropped


def plan(papers_root, live):
    """`{论文目录名: {"rows":…, "dropped":…}}`（只含有重复的篇）。

    逐**目录**扫（不是逐产物）—— 产物里已经消失的论文目录若有重复行，也该被清掉 ✓；
    它不在产物里时 `live` 为空集，保留字段最多的那行（见 `plan_paper`）。
    """
    out = {}
    for name in sorted(os.listdir(papers_root)):
        if not (len(name) >= 3 and name[:3].isdigit()
                and os.path.isdir(os.path.join(papers_root, name))):
            continue
        indices = live.get(int(name[:3]), set())
        rows, dropped = plan_paper(os.path.join(papers_root, name), indices)
        if rows is not None:
            out[name] = {"rows": rows, "dropped": dropped}
    return out


def main(argv=None):
    from code.config import papers_root as _papers_root

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--papers-root", default=None, help="论文数据树（默认阶段 1 的 papers_root()）")
    parser.add_argument("--crawl-root", default=None, help="抓取产物目录（默认项目根）")
    parser.add_argument("--author", default=None, help="作者 ID（默认取 profile 里的）")
    parser.add_argument("--backup-dir", default=None)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    from code.data_upgrade.align_citations_meta import _find_author, load_output

    crawl_root = args.crawl_root or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    papers_dir = args.papers_root
    if not papers_dir:
        author = args.author or _find_author(crawl_root)
        papers_dir = _papers_root(os.path.join(crawl_root, "output"), author) if author else None
    if not papers_dir or not os.path.isdir(papers_dir):
        print("❌ 论文数据树不存在：%s" % papers_dir, file=sys.stderr)
        return 1

    entries, _sources = load_output(crawl_root)
    live = live_indices(entries)
    planned = plan(papers_dir, live)
    total = sum(len(p["dropped"]) for p in planned.values())
    print("%s：%d 篇有同一 cites_id 的重复行，共 %d 行"
          % ("已执行" if args.apply else "预览（未改动任何文件）", len(planned), total))
    for name, payload in sorted(planned.items()):
        for item in payload["dropped"]:
            print("  %s：保留 index=%s，删 index=%s（%s）"
                  % (name[:52], item["kept_index"], item["dropped_index"],
                     str(item["cites_id"])[:32]))
    if not args.apply:
        if total:
            print("加 --apply 才会写（逐篇备份 + manifest，被删行整条留档）。")
        return 0

    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = args.backup_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "output", "dedupe_meta_rows_%s" % stamp)
    manifest = []
    for name, payload in sorted(planned.items()):
        path = os.path.join(papers_dir, name, META_NAME)
        os.makedirs(os.path.join(backup_dir, name), exist_ok=True)
        shutil.copy2(path, os.path.join(backup_dir, name, META_NAME))
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload["rows"], handle, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        manifest.append({"paper": name, "dropped": payload["dropped"],
                         "rows_after": len(payload["rows"])})
    os.makedirs(backup_dir, exist_ok=True)
    with open(os.path.join(backup_dir, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump({"timestamp": stamp, "papers": manifest}, handle, ensure_ascii=False, indent=2)
    print("备份与 manifest：%s" % backup_dir)
    print("复核：重跑应报 0 行（幂等）✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
