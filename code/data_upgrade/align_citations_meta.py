"""把存量 `papers/<NNN>-<slug>/citations_meta.json` 的**主体**对齐到阶段 1（Y-9 ③）。

**背景**（用户 2026-10-15 定，见 `KNOWN_ISSUES.md` Y-9）：`citations_meta.json` 的**主体**
（`index` / `cites_id` / 书目字段 / `venue_key*`）归**阶段 1**（抓取侧）产生，阶段 2 只读。
写主体那半已经落地（`code/citation_index.py::write_citations_meta_for_pub`，接在 `_save_output`
的 `write_cite_files()` 旁边）；**存量** 241 篇里的主体还是旧世代的，本工具是那一次性迁移。

**它做什么**：拿阶段 1 当前的抓取产物（`output/author_<ID>_paper_citations.json`）逐篇重建
目标主体，**只重写与盘上不一致的**（内容一致时 `write_citations_meta()` 自己返回 0、不落盘 ✓）。

**它不做什么**（与 Y-9 的契约一致，逐条有测试）：

* **不动下载字段**：`download_status` / `local_filename` / `fail_reason` / `size_bytes` / `_source` /
  `_page_authors` / `_discovered_pdf_url` / `_reconciled_from` 等**非主体字段原样带回** ✓
  （由 `write_citations_meta()` 的合并逻辑保证 —— 本工具不自己拼 meta ✗）；
* **不删除任何条目**：产物里已没有的编号（上游引文消失/退役）**原样保留** ✓（tombstone）；
* **不另算编号**：`index` 直接取产物条目自己的那份（它由 `code/citation_index.py` 的
  `assign_indices()` 在抓取时分配、**不可变** ✓）；
* **派生字段重算**：`venue_key` / `venue_key_source` 由 `meta_entry()` 用共享件重算，
  并带"**永不降级**"（盘上同一条引用的 `normalized` 不被 raw 顶掉 ✓）。

**默认 dry-run**（只报告）；`--apply` 才写，写前把每个将被改动的文件复制进备份目录 +
`manifest.json`（sha256 前后 + 每篇计数），原子写入、**幂等**（复跑 0 改动）。

用法（项目根）：

    python -m code.data_upgrade.align_citations_meta                 # 预览
    python -m code.data_upgrade.align_citations_meta --apply         # 写 + 备份 + manifest

**没有 `-p`**：这是一次**整库**迁移，而且 `-p` 的解析器 `shared/spec.py` 宿主在**阶段 2**
（阶段 1 引它就是**向后依赖** ✗，Y-7 禁止），本工具不为了一个便利开关去引它 ✗。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import glob
import hashlib
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

#: 主体里的字段（写者 `citation_index.META_FIELDS` 是唯一权威，这里只用于报告差异）
from code.citation_index import META_FIELDS, META_NAME, write_citations_meta  # noqa: E402
from code.data_upgrade import compare_citations_meta as _cmp  # noqa: E402


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def load_output(crawl_root):
    """抓取产物 → `[(pub, [citation, …]), …]`（按 `papers` 顺序；编号取条目自己的 `index`）。"""
    paths = sorted(glob.glob(os.path.join(crawl_root, "output", "author_*_paper_citations.json")))
    result, names = [], []
    for path in paths:
        names.append(os.path.basename(path))
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        for publication in payload.get("papers") or payload.get("publications") or []:
            result.append((publication.get("pub") or {}, publication.get("citations") or []))
    return result, names


def target_rows(citations):
    """产物条目 → 写主体用的行（**编号取条目自己的 `index`** ✓，缺编号的条目跳过）。

    产物里编号就写在条目上（`assign_indices()` 的落点 ✓）；这里**不重算**、也不从
    列表位置推 —— 那会造出第二个编号来源 ✗。
    """
    rows = []
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        index = citation.get("index")
        if not index:
            continue
        rows.append(citation)
    return rows


def plan_papers(entries, papers_root):
    """逐篇算出"**如果现在写主体，会不会有改动**"（纯读，不落盘）。"""
    from code.shared.papers_layout import target_dir_name

    plans = []
    for pub, citations in entries:
        if pub.get("no") is None:
            continue
        dir_name = target_dir_name(pub)
        paper_dir = os.path.join(papers_root, dir_name)
        rows = target_rows(citations)
        plan = {"paper": dir_name, "paper_dir": paper_dir, "rows": rows,
                "count": len(rows),
                "meta": os.path.join(paper_dir, META_NAME), "error": None,
                "existed": os.path.isfile(os.path.join(paper_dir, META_NAME)),
                "empty": not citations, "changed": 0}
        if not citations:
            # 产物里这篇 **0 条引文**（Google 侧 0/N 的空页论文，实测 36 篇）→ 没什么可写 ✓
            plans.append(plan)
            continue
        if not rows:
            plan["error"] = "产物条目都没有编号"
            plans.append(plan)
            continue
        if not os.path.isdir(paper_dir):
            plan["error"] = "论文目录不存在（阶段 1 的目录维护还没建它）"
            plans.append(plan)
            continue
        try:
            # 写者自己就是判据：内容一致 → 返回 0 且不落盘 ✓（绝不自己拼一份 meta ✗）
            plan["changed"] = write_citations_meta(paper_dir, rows, dry_run=True)
        except Exception as exc:                       # 单篇失败不拖垮整轮
            plan["error"] = "%s: %s" % (type(exc).__name__, exc)
        plans.append(plan)
    return plans


def apply_paper(plan, backup_dir):
    """备份 + 重写该篇主体（**只走写者** ✓）；返回 manifest 里那一条记录。"""
    path = plan["meta"]
    before = _sha256(path) if os.path.isfile(path) else None
    dest_dir = os.path.join(backup_dir, plan["paper"])
    os.makedirs(dest_dir, exist_ok=True)
    if before:
        shutil.copy2(path, os.path.join(dest_dir, META_NAME))
    written = write_citations_meta(plan["paper_dir"], plan["rows"], log=None)
    return {"paper": plan["paper"], "path": path,
            "backup": os.path.join(dest_dir, META_NAME) if before else None,
            "sha256_before": before,
            "sha256_after": _sha256(path) if os.path.isfile(path) else None,
            "entries": written, "created": not plan["existed"]}


def main(argv=None):
    from code.config import papers_root as _papers_root

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--papers-root", default=None,
                        help="论文数据树（默认阶段 1 的 papers_root()）")
    parser.add_argument("--crawl-root", default=None, help="抓取产物所在目录（默认项目根）")
    parser.add_argument("--author", default=None, help="作者 ID（默认取 profile 里的那个）")
    parser.add_argument("--backup-dir", default=None,
                        help="改动前的备份目录（默认 <repo>/output/citations_meta_align_<时间戳>）")
    parser.add_argument("--apply", action="store_true", help="真的写（默认只报告）")
    parser.add_argument("--json", dest="as_json", action="store_true", help="机器可读输出")
    args = parser.parse_args(argv)

    crawl_root = args.crawl_root or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    papers_dir = args.papers_root
    if not papers_dir:
        author = args.author or _find_author(crawl_root)
        # `papers_root(output_dir, author)`：第一参是**抓取产物的目录**（`<repo>/output`）
        papers_dir = _papers_root(os.path.join(crawl_root, "output"), author) if author else None
    if not papers_dir or not os.path.isdir(papers_dir):
        print("❌ 论文数据树不存在：%s" % papers_dir, file=sys.stderr)
        return 1

    entries, sources = load_output(crawl_root)
    plans = plan_papers(entries, papers_dir)

    to_change = [p for p in plans if p["changed"] and not p["error"]]
    empty = [p for p in plans if p.get("empty")]
    errors = {p["paper"]: p["error"] for p in plans if p["error"]}
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = args.backup_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "output", "citations_meta_align_%s" % stamp)

    manifest = {"applied": bool(args.apply), "timestamp": stamp,
                "papers_root": papers_dir, "crawl_root": crawl_root,
                "sources": sources, "backup_dir": None, "papers": [],
                "totals": {"papers_in_output": len(plans),
                           "papers_without_citations": len(empty),
                           "papers_to_change": len(to_change),
                           "entries_to_rewrite": sum(p["changed"] for p in to_change),
                           "papers_created": sum(1 for p in to_change if not p["existed"]),
                           "errors": errors}}
    if args.apply and to_change:
        os.makedirs(backup_dir, exist_ok=True)
        for plan in to_change:
            manifest["papers"].append(apply_paper(plan, backup_dir))
        manifest["backup_dir"] = backup_dir
        with open(os.path.join(backup_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

    # 只读对拍（同一份产物 ↔ 盘上的主体）：给出"还剩多少差异、差在哪些字段"。
    # 它只是**报告** —— 缺 `paper_meta.json`（夹具）之类的情况不该让迁移失败 ✓。
    try:
        report = _cmp.compare(papers_dir, crawl_root, log=lambda *a: None)
    except Exception as exc:                           # noqa: BLE001
        report = {"error": "%s: %s" % (type(exc).__name__, exc)}
    manifest["totals"]["compare"] = report

    if args.as_json:
        print(json.dumps({"applied": bool(args.apply),
                          "papers_root": papers_dir, "backup_dir": manifest["backup_dir"],
                          "papers_to_change": len(to_change),
                          "papers_without_citations": len(empty),
                          "entries_to_rewrite": manifest["totals"]["entries_to_rewrite"],
                          "papers_created": manifest["totals"]["papers_created"],
                          "errors": errors, "compare": report},
                         ensure_ascii=False, indent=2))
        return 0

    print("%s：papers = %s" % ("已执行" if args.apply else "预览（未改动任何文件）", papers_dir))
    print("产物：%s" % (", ".join(sources) or "（没有 author_*_paper_citations.json）"))
    for plan in to_change[:10]:
        print("  ✎  %s → 写 %d 条%s" % (plan["paper"][:60], plan["changed"],
                                        "（新建 meta）" if not plan["existed"] else ""))
    if len(to_change) > 10:
        print("  … 另有 %d 篇" % (len(to_change) - 10))
    for name, err in sorted(errors.items()):
        print("  ⚠  %s → 跳过：%s" % (name[:60], err))
    if args.apply and not to_change:
        print("  （没有需要改的主体 —— 幂等，未产生任何写入与备份目录）")
    print("\n合计：产物覆盖 %d 篇（其中 %d 篇产物里 0 条引文，跳过 ✓），需重写 %d 篇 / %d 条"
          "（新建 %d 篇 meta）"
          % (len(plans), len(empty), len(to_change),
             manifest["totals"]["entries_to_rewrite"],
             manifest["totals"]["papers_created"]))
    print("对拍（只读）：%s" % json.dumps(report, ensure_ascii=False, sort_keys=True))
    if report.get("differ"):
        print("  ⚠  仍有 %d 条差异 —— 字段分布见上；`venue_key*` 若差异大，通常是"
              "**产物侧**还没按新规则重算（要等下一次抓取），不是本次迁移的问题 ✓"
              % report["differ"])

    if args.apply:
        print("\n备份与 manifest：%s" % backup_dir)
        print("提示：只动主体字段（%s）；下载字段与退役条目原样保留、原子写入、复跑幂等 ✓"
              % "/".join(META_FIELDS[:6]))
    elif to_change:
        print("\n加 --apply 才会写（写前备份 + manifest，从不删除任何文件）。")
    return 0


def _find_author(crawl_root):
    for path in sorted(glob.glob(os.path.join(crawl_root, "output",
                                              "author_*_paper_citations.json"))):
        base = os.path.basename(path)
        return base[len("author_"):-len("_paper_citations.json")]
    return None


if __name__ == "__main__":
    sys.exit(main())
