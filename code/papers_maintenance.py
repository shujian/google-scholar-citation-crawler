"""论文目录的维护（创建 / 编号 / 改名 / 归档）—— **阶段 1 是唯一负责方**（Y-6 步骤 3）。

`papers/` 的物理位置在本仓库的 `output/author_<ID>_papers/` 下（2026-10-15 用户搬入），
所以"按最新 JSON 的排名创建目录、写 `paper_meta.json`、把已有目录改名对齐、把已消失的论文归档"
也归本阶段 —— 判据与构造器是跨仓库共享件 `code/shared/papers_layout.py`
（阶段 2 的 `code/renumber_papers.py` 现在只是它的薄壳）。

**三种模式**（`crawler_config.json` 的 `renumber_papers` 或 `--renumber-papers`）：

* `off` —— 什么都不做；
* `dry-run`（默认）—— **只报告**会做什么（不动任何文件）；
* `apply` —— 真正建目录（+ 写 `paper_meta.json`）、改名/归档，并同步 `progress.json` 的派生编号。

**为什么建目录时必须同时写 `paper_meta.json`**（2026-10-15 用户定：创建交给阶段 1）：
共享件的认领逻辑靠目录内的 meta（url / 标题），空目录认领不上 → 下一轮 `plan()` 会把它当成
"当前输入里已消失的论文"归档进 `papers/old/`，紧接着又被建回来（**每轮归档+重建一次**，实测过）。
写上 meta 之后目录即可被认领，整条链幂等。

**只建不删、绝不覆盖**：`mkdir` 只对"目标目录不存在"的论文做；meta 只在**新建**时写
（已存在的目录绝不改写它的 meta —— 那是阶段 2 的地盘，它要写 `total_collected_citations`）；
改名/归档全走共享件的两阶段改名（置换失败回滚、写 `manifest.json`、冲突条目不执行）。
**任何异常都不许影响抓取** —— 整个维护过程被包在 try/except 里，失败只打印告警。
"""

from __future__ import annotations

import json
import os

#: 允许的模式（顺序即文档顺序）
MODES = ("off", "dry-run", "apply")

#: 默认模式：只报告不动盘（用户可见地暴露"目录与最新排名不一致"，但零风险）
DEFAULT_MODE = "dry-run"


def load_publications(output_dir, author_id):
    """读本阶段自己的 profile 产物，返回 `papers_layout` 要的形状（`[{"pub": …}, …]`）。

    顺序即"按引用量降序"的排名，`pub["no"]` 就是目标目录编号 —— 与阶段 2 消费的同一份数据。
    """
    path = os.path.join(output_dir, "author_%s_profile.json" % author_id)
    try:
        with open(path, encoding="utf-8") as handle:
            profile = json.load(handle)
    except (OSError, ValueError):
        return []
    publications = profile.get("publications") or []
    return [{"pub": pub} for pub in publications if isinstance(pub, dict)]


def summarize(plan):
    """把计划压成几行日志用的计数。"""
    return {
        "moves": len(plan.get("moves") or []),
        "merges": len(plan.get("merges") or []),
        "orphans": len(plan.get("orphans") or []),
        "ambiguous": len(plan.get("ambiguous") or []),
        "conflicts": len(plan.get("conflicts") or []),
    }


def ensure_paper_dirs(papers_root, papers, log=print):
    """给每篇论文建目标目录**并写 `paper_meta.json`**（只建不删；已存在就跳过）。

    meta 走共享件的唯一构造器（含 `venue_full` / `venue_key` / `venue_key_source`），
    阶段 2 之后只会补 `total_collected_citations`，所以这里写下的字段不会被覆盖。
    返回新建的目录名列表。
    """
    import os as _os

    from code.shared.papers_layout import paper_meta_from_pub, target_dir_name
    from code.common import write_json_atomic

    created = []
    for entry in papers:
        pub = entry.get("pub") or {}
        if pub.get("no") is None:
            continue
        name = target_dir_name(pub)
        path = _os.path.join(papers_root, name)
        if _os.path.isdir(path):
            continue
        _os.makedirs(path, exist_ok=True)
        write_json_atomic(_os.path.join(path, "paper_meta.json"), paper_meta_from_pub(pub))
        created.append(name)
    if created:
        log("[papers] 新建论文目录 %d 个（含 paper_meta.json）" % len(created))
    return created


def run(papers_root, papers, mode=DEFAULT_MODE, output_dir=None, log=print):
    """按模式跑一次维护。返回结果字典（含 `mode`，出错时含 `error`）。"""
    if mode not in MODES:
        raise ValueError("未知的 renumber 模式：%r（可选 %s）" % (mode, "/".join(MODES)))
    result = {"mode": mode, "papers_root": papers_root, "papers": len(papers or [])}
    if mode == "off" or not papers:
        return result
    try:
        from code.shared.papers_layout import reconcile, remap_progress

        # 顺序很重要：**先**改名对齐，**再**建缺的目录。
        # 反过来的话，新建的目标目录会与"待改名"的旧目录同时存在，共享件会把它们看成
        # "同一篇作品的两个目录"（合并/歧义），于是这一次改名反而做不成。
        plan = reconcile(papers, base_dir=papers_root, apply=(mode == "apply"), log=log)
        result["plan"] = summarize(plan)
        if mode == "dry-run":
            log("[papers] dry-run：%s（用 --renumber-papers apply 落盘）" % result["plan"])
            return result
        result["created"] = ensure_paper_dirs(papers_root, papers, log=log)
        if os.path.isfile(os.path.join(papers_root, "progress.json")):
            result["progress"] = remap_progress(
                plan,
                os.path.join(papers_root, "progress.json"),
                os.path.join(output_dir or papers_root, "renumber_progress_backup"),
                log=log,
            )
        return result
    except Exception as exc:  # noqa: BLE001 —— 维护失败绝不能影响抓取
        log("[papers] 目录维护失败，已跳过（不影响抓取）：%s: %s" % (type(exc).__name__, exc))
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
        return result


def run_for_output(output_dir, author_id, mode=DEFAULT_MODE, log=print):
    """便利入口：从 `output_dir` 解析 papers 根与论文列表，然后 `run()`。"""
    from code.config import papers_root

    return run(papers_root(output_dir, author_id), load_publications(output_dir, author_id),
               mode=mode, output_dir=output_dir, log=log)
