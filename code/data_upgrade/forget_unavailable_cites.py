"""忘掉 Cite 缓存里"这条记录没有对话框"的否证（`cite_format_cache.json`）。

**为什么需要它**：`cite_format` 把详情页的 404 记成
``{'unavailable': True, 'status': 404, 'fetched_at': …}``，7 天内不再问。这个判据的前提是
"这条记录没有对话框"，但 `cid` 其实是 Scholar **按每次渲染发放的临时句柄**：同一条列表 URL
隔一分钟再取，前几行的 `data-cid` 就换了；拿几天前的句柄去问 `output=cite`，得到的同样是
Google 的 404 页（实测 2026-10-23：一个两天前成功抓过对话框的 cid、以及缓存里另一个成功过的
cid，全部 404，而同一时刻刚从列表页取到的新句柄返回 200 + `gs_citr`）。

于是"过期句柄"被当成了"记录不存在"，把一批本来能补的行停用了 7 天。这个工具把那些否证
清掉，让下一次**重走列表页**（`--fetch-mode force`）时用新句柄重新问一遍。

**做什么**：只删 `unavailable` 为真的条目 ✓，成功抓到的对话框条目一字不动 ✗。

* 默认 **dry-run** ✓；`--apply` 才写（原子写 + 备份 + `manifest.json`）✓；
* **幂等**：没有可删的条目就不写、也不留备份目录 ✓；
* `--before YYYY-MM-DD` 只清理这段时间之前的否证（默认全清）。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CACHE_NAME = "cite_format_cache.json"


def forget(cache, before=None):
    """返回 (保留的缓存, 被删掉的 cid 列表)。纯函数，不改入参。"""
    kept = {}
    dropped = []
    for cid, entry in (cache or {}).items():
        stale = (isinstance(entry, dict) and entry.get("unavailable")
                 and (before is None
                      or str(entry.get("fetched_at") or "") < before))
        if stale:
            dropped.append(cid)
        else:
            kept[cid] = entry
    return kept, sorted(dropped)


def main(argv=None):
    from code.common import write_json_atomic

    parser = argparse.ArgumentParser(
        description="清掉 Cite 缓存里'这条记录没有对话框'的否证（默认只报告）")
    parser.add_argument("--root", default=REPO_ROOT, help="阶段 1 仓库根（默认本仓库）")
    parser.add_argument("--cache", default=None,
                        help="缓存文件路径（默认 <root>/output/%s）" % CACHE_NAME)
    parser.add_argument("--before", default=None,
                        help="只清理这个日期之前记录的否证（ISO 时间前缀，如 2026-10-22）")
    parser.add_argument("--apply", action="store_true", help="真的写（默认只报告）")
    parser.add_argument("--backup-dir", default=None,
                        help="备份目录（默认 <root>/output/forget_unavailable_<时间戳>）")
    args = parser.parse_args(argv)

    path = args.cache or os.path.join(args.root, "output", CACHE_NAME)
    if not os.path.exists(path):
        print("❌ 没找到 %s" % path, file=sys.stderr)
        return 1
    with open(path, encoding="utf-8") as handle:
        cache = json.load(handle)
    if not isinstance(cache, dict):
        print("❌ 缓存文件不是 {cid: entry} 结构：%s" % path, file=sys.stderr)
        return 1

    kept, dropped = forget(cache, before=args.before)
    print("  条目 %d → 保留 %d，清掉 %d 条否证%s"
          % (len(cache), len(kept), len(dropped),
             ("（%s 之前）" % args.before) if args.before else ""))
    if dropped:
        print("  例如：" + ", ".join(dropped[:8]) + (" …" if len(dropped) > 8 else ""))
    if not dropped:
        print("没有要清理的条目 ✓（未改动任何文件）")
        return 0
    if not args.apply:
        print("预览（未改动任何文件）。加 --apply 才会写（写前备份 + manifest）。")
        return 0

    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = args.backup_dir or os.path.join(args.root, "output",
                                                 "forget_unavailable_%s" % stamp)
    os.makedirs(backup_dir, exist_ok=True)
    backup = os.path.join(backup_dir, os.path.basename(path))
    shutil.copy2(path, backup)
    write_json_atomic(path, kept)
    with open(os.path.join(backup_dir, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump({"timestamp": stamp, "cache": os.path.basename(path),
                   "entries_before": len(cache), "entries_after": len(kept),
                   "dropped": dropped}, handle, ensure_ascii=False, indent=2)
    print("已写回 %s；备份与 manifest：%s" % (path, backup_dir))
    print("提示：这些 cid 的对话框要在**重走列表页**时用新句柄重新问（--fetch-mode force）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
