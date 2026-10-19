"""把阶段 1 的**抓取产物**（`output/author_<ID>_paper_citations.json`）补齐两种派生键。

**为什么要它**（2026-10-18 一致性审计）：`citations_meta.json` 的主体由阶段 1 写（Y-9），
写入器会**重算** `venue_key` / `venue_key_source` 并给没有真实 id 的行补**合成键**（K144）；
但**产物文件**本身还是抓取那天的版本 —— 与 meta 对拍 `differ 4,608`
（`venue_key*` 3,294 + `cites_id` 2,136）✗。代码路径是重算的所以正确性不受影响 ✓，
但这个"输入文件"与代码/meta 不一致，凡是拿产物当输入的地方（阶段 2 的输入快照）都要现算一遍 ✓。

**做什么**：逐条按 `citation_venue_key()`（venue_key）与 `synthetic_keys_for()`（合成键，整篇算一次）
重算 → 写回产物；**只改这两个（三个）键** ✓，其它字段一字不动 ✗。

* 默认 **dry-run** ✓；`--apply` 才写（原子写 + 备份 + `manifest.json`）✓；
* **幂等**：内容不变就不写 ✓；
* 有真实 `cites_id` 的行**不被改写** ✓；`venue_key` 按口径"**永不降级**"（盘上 normalized 不被 raw 顶掉 ✓）。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _products(root):
    return sorted(glob.glob(os.path.join(root, "output", "author_*_paper_citations.json")))


def refresh(payload, dry_run=True, log=print):
    """就地（或只计数）补齐两种键，返回 `(changed_rows, written)`。"""
    from code.common import write_json_atomic
    from code.shared.citation_key import is_real_cites_id, synthetic_keys_for
    from code.shared.venue_key import citation_venue_key

    changed = 0
    for publication in payload.get("papers") or []:
        citations = publication.get("citations") or []
        keys = synthetic_keys_for(citations, None)
        for row, key in zip(citations, keys):
            if not isinstance(row, dict):
                continue
            touched = False
            if key and not is_real_cites_id(row.get("cites_id")):
                if row.get("cites_id") != key:
                    row["cites_id"] = key
                    touched = True
            value, source = citation_venue_key(row)
            if value:
                if row.get("venue_key") != value:
                    # 永不降级：盘上 normalized、重算只到 raw → 保留盘上那份 ✓
                    if not (str(row.get("venue_key_source") or "").lower() == "normalized"
                            and str(source or "").lower() != "normalized"):
                        row["venue_key"] = value
                        touched = True
                if source and row.get("venue_key_source") != source and row.get("venue_key") == value:
                    row["venue_key_source"] = source
                    touched = True
            if touched:
                changed += 1
    return changed


def main(argv=None):
    from code.common import write_json_atomic

    parser = argparse.ArgumentParser(description="给抓取产物补齐 venue_key / cites_id（只改这几个键）")
    parser.add_argument("--root", default=REPO_ROOT, help="阶段 1 仓库根（默认本仓库）")
    parser.add_argument("--apply", action="store_true", help="真的写（默认只报告）")
    parser.add_argument("--backup-dir", default=None,
                        help="备份目录（默认 <repo>/output/refresh_product_keys_<时间戳>）")
    args = parser.parse_args(argv)

    paths = _products(args.root)
    if not paths:
        print("❌ 没找到 output/author_*_paper_citations.json", file=sys.stderr)
        return 1
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = args.backup_dir or os.path.join(args.root, "output", "refresh_product_keys_%s" % stamp)
    total = 0
    touched_files = []
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        changed = refresh(payload)
        total += changed
        print("  %s：%d 条要补键" % (os.path.basename(path), changed))
        if changed and args.apply:
            os.makedirs(backup_dir, exist_ok=True)
            shutil.copy2(path, os.path.join(backup_dir, os.path.basename(path)))
            write_json_atomic(path, payload)
            touched_files.append(os.path.basename(path))

    print("%s：共 %d 条%s" % ("已执行" if args.apply else "预览（未改动任何文件）", total,
                             ("  写回 " + ", ".join(touched_files)) if touched_files else ""))
    if args.apply:
        os.makedirs(backup_dir, exist_ok=True)
        with open(os.path.join(backup_dir, "manifest.json"), "w", encoding="utf-8") as handle:
            json.dump({"timestamp": stamp, "files": touched_files, "rows": total},
                      handle, ensure_ascii=False, indent=2)
        print("备份与 manifest：%s" % backup_dir)
        print("提示：之后把产物复制成阶段 2 两个 worktree 的 paper_citations.json，对拍才会 differ 0 ✓")
    elif total:
        print("加 --apply 才会写（写前备份 + manifest；只改 venue_key/venue_key_source/cites_id）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
