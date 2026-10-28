"""论文目录的维护（创建 / 编号 / 改名 / 归档）—— **阶段 1 是唯一负责方**。

`papers/` 的物理位置在本仓库的 `output/author_<ID>_papers/` 下，
所以"按最新 JSON 的排名创建目录、写 `paper_meta.json`、把已有目录改名对齐、把已消失的论文归档"
也归本阶段 —— 判据与构造器是跨仓库共享件 `code/shared/papers_layout.py`
（阶段 2 的 `code/renumber_papers.py` 现在只是它的薄壳）。

**三种模式**（`crawler_config.json` 的 `renumber_papers` 或 `--renumber-papers`）：

* `off` —— 什么都不做；
* `dry-run` —— **只报告**会做什么（不动任何文件）；
* `apply` —— 真正建目录（+ 写 `paper_meta.json`）、改名/归档；入口默认 `apply`，见
  `code.config.RENUMBER_PAPERS`。

**为什么建目录时必须同时写 `paper_meta.json`**：
共享件的认领逻辑靠目录内的 meta（url / 标题），空目录认领不上 → 下一轮 `plan()` 会把它当成
"当前输入里已消失的论文"归档进 `papers/old/`，紧接着又被建回来（**每轮归档+重建一次**）。
写上 meta 之后目录即可被认领，整条链幂等。

**只建不删、绝不覆盖**：`mkdir` 只对"目标目录不存在"的论文做；meta 只在**新建**时写
（已存在的目录绝不改写它的 meta —— 那是阶段 2 的地盘，它要写 `total_collected_citations`）；
改名/归档全走共享件的两阶段改名（置换失败回滚、写 `manifest.json`、冲突条目不执行）。
**任何异常都不许影响抓取** —— 整个维护过程被包在 try/except 里，失败只打印告警。
"""

from __future__ import annotations

import datetime as _dt
import json
import os

#: 允许的模式（顺序即文档顺序）
#: `force` = 即使阶段 2 的锁还活着也照改（默认行为是"看到活锁就只报告不动盘"）
MODES = ("off", "dry-run", "apply", "force")

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


def _plan_has_work(plan_summary):
    """计划里是否真有会改动磁盘的动作（改名 / 合并 / 归档 / 冲突）。"""
    return any((plan_summary or {}).get(k) for k in
               ("moves", "merges", "orphans", "conflicts"))


def report_applied(result, log=print):
    """把"这一轮对目录做了什么"总结成一条日志（没有动作就什么都不说）。

    为什么要单独一条：`log_paper_order_changes()` 比较的是**上一版 profile 与本轮**，
    所以它说"没有位置变化"时，磁盘上仍可能有**更早**的编号变化等着落地（上一轮被阶段 2
    的锁挡住、或用 dry-run 跑过）。这时只报"没有位置变化"会让人以为目录不用改。
    """
    if not result or result.get("mode") not in ("apply", "force"):
        return None
    plan = result.get("plan") or {}
    moves, merges = plan.get("moves", 0), plan.get("merges", 0)
    orphans, created = plan.get("orphans", 0), result.get("created", 0) or 0
    if not (moves or merges or orphans):
        return None
    parts = []
    if moves:
        parts.append(f"{moves} directory rename(s)")
    if merges:
        parts.append(f"{merges} merge(s)")
    if orphans:
        parts.append(f"{orphans} archived to papers/old/")
    if created:
        parts.append(f"{created} directory(ies) created")
    line = ("Papers maintenance: " + ", ".join(parts)
            + " — from order changes that had not been applied to the tree yet")
    log(line)
    return line


def report_leftovers(result, log=print):
    """把"想动却没动"的目录报出来（歧义 / 冲突），并指向 manifest。

    这两类**不会被自动处理**：`ambiguous` 是两个候选判据都定夺不了（同标题多条），
    `conflicts` 是目标目录已存在且标题不同（绝不覆盖）。它们只写进 manifest，如果日志里
    也不提，磁盘上就会留下一个没人知道的旧目录。
    """
    plan = (result or {}).get("plan") or {}
    ambiguous, conflicts = plan.get("ambiguous", 0), plan.get("conflicts", 0)
    if not (ambiguous or conflicts):
        return None
    parts = []
    if ambiguous:
        parts.append(f"{ambiguous} directory(ies) left untouched (title shared by several "
                     f"papers, none of the tie-breakers decided)")
    if conflicts:
        parts.append(f"{conflicts} target name(s) already taken by a different paper")
    manifest = result.get("manifest")
    where = f" — see {manifest}" if manifest else ""
    line = ("Papers maintenance: " + "; ".join(parts)
            + f"; nothing was moved for them{where}")
    log(line)
    return line


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


def stage2_lock_note(papers_root):
    """阶段 2 的 papers 使用锁是否还活着；活的返回描述串，否则 ``None``。

    阶段 1 会改论文目录名，阶段 2 拼路径只认输入快照，两者同时跑就会
    把旧名字的目录**重建**出来（`paper_dir_for()` + `makedirs`），数据被拆成两套。
    锁是提醒机制：看到活锁就只报告计划、不动盘。
    """
    try:
        from code.shared.stage_lock import describe, is_live, read
    except Exception:      # noqa: BLE001 —— 锁的可用性绝不该挡住维护
        return None
    info = read(papers_root)
    if info and is_live(info, papers_root):
        return describe(info)
    return None


def run(papers_root, papers, mode=DEFAULT_MODE, output_dir=None, log=print):
    """按模式跑一次维护。返回结果字典（含 `mode`，出错时含 `error`）。"""
    if mode not in MODES:
        raise ValueError("未知的 renumber 模式：%r（可选 %s）" % (mode, "/".join(MODES)))
    result = {"mode": mode, "papers_root": papers_root, "papers": len(papers or [])}
    if mode == "off" or not papers:
        return result
    # 锁只在与阶段 2 抢目录名时才有意义，所以先算计划再提示：空计划下说
    # "本轮不改名"只会让人误以为哪里出了问题。
    held = None
    was_apply = mode == "apply"
    if mode in ("apply", "force"):
        held = stage2_lock_note(papers_root)
        if held and was_apply:
            mode = "dry-run"       # 先按只算不改走；看到计划后再决定要不要提示
    result["mode"] = mode
    try:
        from code.shared.papers_layout import reconcile

        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")

        # 顺序很重要：**先**改名对齐，**再**建缺的目录。
        # 反过来的话，新建的目标目录会与"待改名"的旧目录同时存在，共享件会把它们看成
        # "同一篇作品的两个目录"（合并/歧义），于是这一次改名反而做不成。
        # `manifest_dir` 必须跟着调用方给的 output 目录走 ✓；否则"给了 output_dir 的
        # 调用方"（测试、独立工具）会把 manifest 写进默认 output/。
        plan = reconcile(papers, base_dir=papers_root, apply=(mode == "apply"), log=log,
                         manifest_dir=(os.path.join(output_dir, "renumber_%s" % stamp)
                                       if output_dir else None))
        result["plan"] = summarize(plan)
        result["manifest"] = plan.get("manifest")   # 供日志指出"详见哪个 manifest"
        work = _plan_has_work(result["plan"])
        if work and held:
            if was_apply:
                log("[papers] 阶段 2 正在使用论文目录（%s）—— 本轮**不改名/不归档**，"
                    "只把计划报告出来（避免与阶段 2 抢目录名）。" % held)
                log("[papers] 等阶段 2 结束后重跑阶段 1 即可；确实要现在改，用 "
                    "--renumber-papers force。")
                result["blocked_by"] = held
            else:
                log("[papers] 注意：阶段 2 的锁还活着（%s），但模式是 force —— 照改不误。"
                    % held)
                result["forced_past"] = held
        if mode == "dry-run":
            # 只有**用户显式要的** dry-run 才报这一行；被锁降级的情况要么已经说了锁，
            # 要么根本没有活（那就什么都不用说）。
            if not (held and was_apply):
                log("[papers] dry-run：%s（用 --renumber-papers apply 落盘）"
                    % result["plan"])
            return result
        result["created"] = ensure_paper_dirs(papers_root, papers, log=log)
        # `progress.json` / `download_summary.json` 是阶段 2 的数据，本阶段不碰；
        # 阶段 2 读本阶段写的 manifest（向前读 ✓）后自行改它自己的进度文件。
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
