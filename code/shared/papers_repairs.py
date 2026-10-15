"""论文目录的**存量修正**（`papers_repairs`）—— **跨仓库共享件，宿主 = 阶段 1**。

2026-10-15 按根 `AGENTS.md`「跨仓库共享代码」搬移（`KNOWN_ISSUES.md` Y-6）：`papers/` 的物理位置
在阶段 1，所以"目录编号重排 / `progress.json` 位置修正 / `citations/*.pdf` 文件名对齐"的判据也归阶段 1。
**内容 = 阶段 2 两个模块的库部分逐字搬移、语义未改**（两个原本都叫 `repair` 的函数去歧义改名）：
`repair_progress_locations()` 与 `repair_citation_filenames()`。

**符号解析 = 消费方模块优先**（工作区既有约定，同 3.a.1 `work_identity` 解 `papers/` 根）：
两个入口都收一个私有的 `_consumer`（薄模块把自己 `sys.modules[__name__]` 传进来），
`_resolve(name, _consumer)` 依次看 ①消费方模块的属性、②本模块的同名定义、③阶段 2 的候选模块。
**为什么必须消费方优先**：阶段 2 的测试用 `patch.object(<薄模块>, "_is_valid_pdf_file")` /
`patch.object(<薄模块>, "sha256")` 打桩 —— 只有先读消费方模块的属性，补丁才继续生效（第一次实现
漏了这点，阶段 2 测试全红，见阶段 1 的 `_work_notes.zh.md`）。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import time

#: 消费方（阶段 2）的候选模块（消费方模块属性与本模块都没有该名字时才来这里找）
_STAGE2_MODULES = ("code.citation_downloader", "code.progress", "code.utils", "code.tool_log")


#: 当前调用的消费方模块（入口函数设置它，内部助手不必层层传 `_consumer`）
_CONSUMER = None


def _resolve(name, _consumer=None):
    """按"消费方模块 → 本模块 → 阶段 2 候选模块"取值（见模块 docstring）。"""
    consumer = _consumer if _consumer is not None else _CONSUMER
    if consumer is not None:
        value = getattr(consumer, name, None)
        if value is not None:
            return value
    if name in globals():
        return globals()[name]
    import importlib
    for module_name in _STAGE2_MODULES:
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        if hasattr(module, name):
            return getattr(module, name)
    raise ImportError(
        f"共享件需要符号 {name!r}，但消费方模块、本模块与 {_STAGE2_MODULES} 里都没有 —— "
        "阶段 2 改名/搬移时要同步这里"
    )

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性处置：把 `progress.json` 里"已下载"条目的**记录位置**改成文件的真实所在。

为什么需要它
------------
`progress.json` 以 `cites_id` 为主键，但每条引用只记**一个**落盘位置
（`paper_index` / `paper_no` / `local_filename`）。跨论文重复引用（同一篇作品被多篇源论文引用）
只会落在**一篇**源论文的目录里，其余论文目录里没有 —— 这本来由下载器的 `reconcile`（离线补齐）
（copy/hardlink）兜住。但离线补齐的第一条路是"按记录位置去找"，若记录不准就白重下：

* 文件名前缀是 `citations_meta.json` 的**论文内 index**，同一篇作品在不同源论文里**前缀不同**，
  所以文件名不是跨论文的身份（身份是 `cites_id`）；
* 实测有 28 条 `paper_no` 为 `None`（只有 `paper_index`），记录位置解析不出来 → 每次都重新下载。

本工具用**全库索引**（`code.citation_downloader.corpus_pdf_index()`：`cites_id` 优先、
"去掉前缀的标题"兜底）把这些条目的 `paper_no` / `paper_index` / `local_filename` 改写成
文件的真实所在，让离线补齐的"记录位置"那条路重新可靠。

行为约定
--------
* **默认 dry-run**：只报告，不写任何文件；
* `--apply` 才写；写前把原文件备份成 `<progress>.bak.<stamp>`（同 stamp 只写一次）；
* **只改 `progress.json` 里的字段**：不动论文目录、不移动/删除任何文件；
* **幂等**：再跑一次是 0 改动（改过的条目其位置已能解析到真实文件）。
"""

import argparse
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))



def _paper_no_of_dir(path):
    """`.../papers/117-Some_Title` → 117（取不到返回 None）。"""
    name = os.path.basename(os.path.normpath(path))
    head = name.split("-", 1)[0]
    return int(head) if head.isdigit() else None


def repair_progress_locations(progress_path, papers_root, apply=False, log=print, _consumer=None):
    """返回统计字典；`apply=False` 时不写盘。"""
    global _CONSUMER
    _CONSUMER = _consumer
    tracker = _resolve("ProgressTracker")(filepath=progress_path)
    tracker.load()
    # 全库索引**惰性获取**（K133，2026-10-14）：它只在"记录位置查不到文件、要去别处找"时才用得上，
    # 而实测 5,541 条记录位置**全对**（无一 unresolved）—— 原先无条件建索引要 9,413 次
    # `os.path.isfile`（远端 ≈13 分钟）纯属白干。改成第一次真需要时再建，决策完全不变。
    index_cache = {}

    def _corpus_index():
        if "idx" not in index_cache:
            index_cache["idx"] = _resolve("corpus_pdf_index")(papers_root)
        return index_cache["idx"]

    # paper_no → progress.papers 的键（用于把 paper_index 也改对）
    index_of_no = {}
    for key, entry in (tracker.data.get("papers") or {}).items():
        if isinstance(entry, dict) and entry.get("paper_no") is not None:
            index_of_no.setdefault(entry["paper_no"], key)

    stats = {"checked": 0, "ok": 0, "fixed": 0, "fixed_by_cites": 0, "fixed_by_slug": 0,
             "filled_no": 0, "unresolved": 0}
    changes, unresolved = [], []
    snaps = {}          # citations 目录 → DirSnapshot（每个目录一次扫描，代替逐条 isfile）

    def _snap_of(dir_path):
        if dir_path not in snaps:
            snaps[dir_path] = _resolve("snapshot_files")(os.path.join(dir_path, "citations"))
        return snaps[dir_path]

    for cites_id, entry in (tracker.data.get("citations") or {}).items():
        if not isinstance(entry, dict) or entry.get("status") != _resolve("STATUS_DOWNLOADED"):
            continue
        stats["checked"] += 1

        # 当前记录位置：paper_index（优先）或 paper_no → 目录
        cur_index = entry.get("paper_index")
        cur_dir = None
        if cur_index is not None:
            cur_dir = (_resolve("paper_dirs_from_progress")(tracker).get(cur_index)
                       or tracker.paper_dir_for_index(cur_index))
        if not cur_dir and entry.get("paper_no") is not None:
            cur_dir = tracker.paper_dir_for_index(entry.get("paper_no"))
        cur_name = entry.get("local_filename") or ""
        cur_path = os.path.join(cur_dir, "citations", cur_name) if (cur_dir and cur_name) else ""
        if cur_path and _snap_of(cur_dir).is_file(cur_name):
            stats["ok"] += 1
            continue

        # 找真实所在：cites_id 优先，其次"去掉前缀的标题"
        # ① 身份命中（cites_id 相等）——可靠，改；
        # ② 标题 slug 命中——弱一些（没有真实 cites_id 的引用只能这样，与下载器 `reconcile` 的
        #    "同目录同标题不同前缀"那条路同源），单独计数便于复核。
        by_cites, by_slug = _corpus_index()
        how = "cites_id"
        target = None
        for cand in by_cites.get(cites_id) or []:
            if os.path.isfile(cand) and _resolve("_is_valid_pdf_file")(cand):
                target = cand
                break
        if target is None and cur_name:
            slug = _resolve("slug_without_prefix")(cur_name)
            for cand in by_slug.get(slug) or []:
                if os.path.isfile(cand) and _resolve("_is_valid_pdf_file")(cand):
                    target, how = cand, "title slug"
                    break
        if not target:
            stats["unresolved"] += 1
            unresolved.append({"cites_id": cites_id, "recorded": cur_path})
            continue

        new_dir = os.path.dirname(os.path.dirname(target))
        new_name = os.path.basename(target)
        new_no = _resolve("_paper_no_of_dir")(new_dir)
        patch = {"local_filename": new_name}
        if new_no is not None:
            patch["paper_no"] = new_no
            if index_of_no.get(new_no) is not None:
                patch["paper_index"] = int(index_of_no[new_no])
        changed = {k: v for k, v in patch.items() if entry.get(k) != v}
        if not changed:
            stats["ok"] += 1
            continue
        entry.update(changed)
        stats["fixed"] += 1
        stats["fixed_by_cites" if how == "cites_id" else "fixed_by_slug"] += 1
        if entry.get("paper_no") is None or "paper_no" in changed:
            stats["filled_no"] += 1
        changes.append({"cites_id": cites_id, "by": how, "from": cur_path, "to": target,
                        "set": changed})

    if apply and changes:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = f"{progress_path}.bak.{stamp}"
        if os.path.exists(backup):
            backup = f"{backup}.{int(time.time())}"
        shutil.copy2(progress_path, backup)
        with open(progress_path, "w", encoding="utf-8") as f:
            json.dump(tracker.data, f, ensure_ascii=False, indent=2)
        stats["backup"] = backup
        log(f"  💾 已写入（原文件备份 {backup}）")
    return {"stats": stats, "changes": changes, "unresolved": unresolved}


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性修复：把 `citations/*.pdf` 的**文件名对齐到当前 `citations_meta.json`**（K122）。

背景（用户 2026-10-13 指出的现象 + 做出的原则）：引用编号历史上漂移过（K108），于是同一目录里
出现了"meta 声明 `082-…pdf`、磁盘上却是 `104-…pdf`"这种**同标题不同前缀**的旧残留（实测 67 条，
整齐 +22 平移）。这类不一致会让下游 3.a.1/3.a.2/3.b 按 meta 的编号去找 PDF → **找不到**。

**对齐方向**：以**当前输入派生出来的 meta/编号为准，旧数据向新数据对齐**（用户定下的原则）。
因此这里**改文件名**（不动 `index` —— 它是下游 `enrich_info_NNNN.json` 等产物的配对键，
改它会制造孤儿并触发重算）。

匹配键是**作品身份**，不是编号：同目录内"去掉数字前缀后标题相同"的候选，且要求**双向唯一**；
有歧义、目标被别的引用占用、文件本身不是有效 PDF → **只报告，不动手**。

安全线（与 K109 的 `repair_stale_citations.py` 一致）：默认 **dry-run**，`--apply` 才动；
**只改名、从不删除、绝不覆盖**；两阶段改名（先全部进临时目录，避免互换互相覆盖）；
写 `manifest.json`（from/to/cites_id/index/sha256/reason）；可选 `--update-progress` 同步
`progress.json` 里指向旧名字的记录位置（写前备份）。

**规划快慢两档（`validate`，K130 —— 内部实现细节，不是用户参数）**：默认 `validate=True` 会对
**每个"声明的文件名在磁盘上存在"的条目** `open()` + 读 4 字节校验 `%PDF`（全库实测 **5,821 次
打开**：本机约 0.18s，但 SSHFS/共享目录下每次文件操作约 346ms → **约 34 分钟**，用户实测 36.5
分钟）。`validate=False` 时**只信 `os.listdir` 的结果**、完全不 open —— 名字对齐的正确性与头部
校验无关（坏 PDF 由下载器逐条路径与 `check_consistency --deep` 负责），所以启动步骤
`preflight_repairs` 内部走这条 fast 路径。**工具自身的 CLI 不暴露这个开关**：独立跑本工具时
一律默认逐个校验。
"""

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


#: 待改名文件的临时目录前缀（两阶段改名用）
TMP_PREFIX = ".citefix_tmp_"

#: macOS 资源叉等垃圾，永远不参与匹配
JUNK_RE = re.compile(r"^(\._|\.DS_Store|Thumbs\.db)")


def _items_of(meta):
    """`citations_meta.json` 可能是 list，也可能是 {"citations": [...]}。"""
    if isinstance(meta, list):
        return meta
    if isinstance(meta, dict):
        return meta.get("citations") or []
    return []


def _norm_slug(text):
    """去掉数字前缀后的标题 → 比较用 slug。

    **必须保留非 ASCII 字母**（中文标题！）：早先用 `[^0-9a-z]` 过滤会把中文标题清成空串，
    于是"所有中文标题彼此相等" → 会把 A 标题的文件改名成 B 标题的名字（dry-run 实测抓到
    `027-相似度增强…` → `025-句子级别…` 这种跨标题误配）。现在只去标点/空白/下划线：
    `\W` 在 Unicode 语义下保留 CJK 字母与数字。
    """
    slug = _resolve("slug_without_prefix")(text or "")
    slug = re.sub(r"\.pdf$", "", slug, flags=re.I)
    return re.sub(r"[\W_]+", "", slug.lower(), flags=re.UNICODE)


def sha256(path, limit=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        remaining = limit
        while remaining > 0:
            chunk = f.read(min(65536, remaining))
            if not chunk:
                break
            h.update(chunk)
            remaining -= len(chunk)
    return h.hexdigest()


def plan_paper(paper_dir, validate=True):
    """一个论文目录的改名计划：返回 (renames, reports)。

    renames: [{"cites_id","index","from","to","sha256","reason"}]
    reports: [{"cites_id","declared","reason"}]  —— 只报告、不动手的情况

    `validate=False`（`preflight_repairs` 内部用的 fast 路径，K130）：**只信 `os.listdir` 的结果**，
    不逐个 `open()` 校验 `%PDF` 头 —— 因此"声明名存在但不是有效 PDF"那条**报告分支也跳过**
    （不 open 就无从判断）。这不影响"文件名对齐"的正确性：坏 PDF 由下载器逐条路径与
    `check_consistency --deep` 负责。
    """
    citations_dir = os.path.join(paper_dir, "citations")
    meta_path = os.path.join(paper_dir, "citations_meta.json")
    if not os.path.isdir(citations_dir) or not os.path.exists(meta_path):
        return [], []
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
    except Exception as e:                                  # noqa: BLE001
        return [], [{"cites_id": "", "declared": "", "reason": f"meta 读不了：{e}"}]

    files = [f for f in sorted(os.listdir(citations_dir))
             if f.lower().endswith(".pdf") and not JUNK_RE.match(f)]
    on_disk = set(files)
    by_slug = {}
    for f in files:
        by_slug.setdefault(_resolve("_norm_slug")(f), []).append(f)

    renames, reports = [], []
    claimed = set()                                          # 已被别的条目认领的磁盘文件
    wanted = []
    for c in _resolve("_items_of")(meta):
        status = c.get("download_status") or c.get("status")
        if status != "downloaded":
            continue
        declared = (c.get("local_filename") or c.get("filename") or "").strip()
        if not declared:
            continue
        target = os.path.join(citations_dir, declared)
        if declared in on_disk and (not validate or _resolve("_is_valid_pdf_file")(target)):
            continue                                        # 已在正确名字下且有效 → 不动
        wanted.append((c, declared))

    # 同一目录里**同一标题被多条引用声明**（同一作品被本篇引了两次，各自一个 index）：
    # 这类绝不能改名 —— 缺的那条会把存在的那条认领走，下一轮又被认领回来，**来回振荡**
    # （用户 2026-10-13 实测"连续跑几轮像在循环"）。只报告：缺的那条应由下载器补下一份。
    declared_slugs = {}
    for c in _resolve("_items_of")(meta):
        if (c.get("download_status") or c.get("status")) != "downloaded":
            continue
        f = (c.get("local_filename") or c.get("filename") or "").strip()
        if f:
            declared_slugs.setdefault(_resolve("_norm_slug")(f), []).append(f)

    for c, declared in wanted:
        cid = c.get("cites_id") or ""
        idx = c.get("index")
        if len(declared_slugs.get(_resolve("_norm_slug")(declared), [])) > 1:
            reports.append({"cites_id": cid, "declared": declared,
                            "reason": "同目录里同一标题对应多个 index（同一作品被重复引用）→ "
                                      "不改名（避免来回振荡）；缺失的那份交给下载器补下"})
            continue
        if (validate and declared in on_disk
                and not _resolve("_is_valid_pdf_file")(os.path.join(citations_dir, declared))):
            reports.append({"cites_id": cid, "declared": declared,
                            "reason": "声明名存在但不是有效 PDF（截断/HTML）→ 该条应重下，改名无意义"})
            continue
        cands = [f for f in by_slug.get(_resolve("_norm_slug")(declared), []) if f not in claimed]
        if not cands:
            reports.append({"cites_id": cid, "declared": declared,
                            "reason": "同目录里找不到同标题的候选（该条需下载/离线补齐）"})
            continue
        if len(cands) > 1:
            reports.append({"cites_id": cid, "declared": declared,
                            "reason": f"同标题候选不唯一（{cands}）→ 不猜"})
            continue
        src = cands[0]
        claimed.add(src)
        renames.append({"cites_id": cid, "index": idx, "from": src, "to": declared,
                        "sha256": _resolve("sha256")(os.path.join(citations_dir, src)),
                        "reason": "旧一代编号的文件名 → 对齐当前 meta 声明的名字"})
    return renames, reports


def _apply_renames(paper_dir, renames, apply=False, log=print):
    """两阶段改名：先全部进临时目录，再落最终名（避免互换/链式互相覆盖）。"""
    citations_dir = os.path.join(paper_dir, "citations")
    if not renames:
        return 0
    tmp = os.path.join(citations_dir, f"{TMP_PREFIX}{_dt.datetime.now():%Y%m%d-%H%M%S}")
    if not apply:
        for r in renames:
            log(f"    [dry-run] {r['from']}  →  {r['to']}")
        return 0
    os.makedirs(tmp, exist_ok=True)
    moved = []
    try:
        for r in renames:
            src = os.path.join(citations_dir, r["from"])
            stage = os.path.join(tmp, r["from"])
            shutil.move(src, stage)                          # 阶段 1：全部挪走
            moved.append((r, stage))
        done = 0
        for r, stage in moved:
            dst = os.path.join(citations_dir, r["to"])
            if os.path.exists(dst):                          # 绝不覆盖
                shutil.move(stage, os.path.join(citations_dir, r["from"]))
                log(f"    ⚠️ 目标已存在，撤销该条：{r['to']}")
                continue
            shutil.move(stage, dst)                          # 阶段 2：落到最终名
            after = _resolve("sha256")(dst)
            if after != r["sha256"]:
                log(f"    ❌ 校验不一致（{r['from']}）→ 内容变了，请人工检查")
                continue
            done += 1
        return done
    finally:
        try:
            os.rmdir(tmp)
        except OSError:
            pass


def repair_citation_filenames(papers_dir, apply=False, update_progress=False, log=print, progress_every=25,
           validate=True, _consumer=None):
    """对所有论文目录做一遍；返回汇总 dict（dry-run 也返回同一形状）。

    **进度可见**（用户 2026-10-13：跑在 SSHFS 上十几分钟毫无输出）：开跑先打一行、之后每
    `progress_every` 个目录报一次"已扫 M/N + 已发现多少"。调用方的 `log` 需自行 `flush`
    （`main()` 里的 log 已带 `flush=True`）。

    `validate=False` 时规划**不逐个 `open()` 校验 PDF 头**（只信 `os.listdir`，见 `plan_paper`）
    —— 启动步骤 `preflight_repairs` 内部走这条 fast 路径（K130）：远端挂载下省掉全库 5,821 次
    打开。改名的**真路径**（`apply=True` 的两阶段改名 + sha256 前后校验）一个字都不受影响。
    """
    global _CONSUMER
    _CONSUMER = _consumer
    plan = {"renames": [], "reports": [], "papers": 0}
    names = [n for n in sorted(os.listdir(papers_dir))
             if n[:1].isdigit() and n != "old"
             and os.path.isdir(os.path.join(papers_dir, n))]
    log(f"  开始扫描 {papers_dir}（{len(names)} 个论文目录）…  每个目录要读 meta 并逐个核对文件，"
        f"远端挂载下可能较慢；中断（Ctrl-C）是安全的：dry-run 不写任何文件。")
    for i, name in enumerate(names, 1):
        d = os.path.join(papers_dir, name)
        if i % max(1, progress_every) == 0:
            log(f"  进度 {i}/{len(names)}：可改名 {len(plan['renames'])}、只报告 {len(plan['reports'])}")
        if not os.path.isdir(d):
            continue
        renames, reports = _resolve("plan_paper")(d, validate=validate)
        if not renames and not reports:
            continue
        plan["papers"] += 1
        for r in renames:
            r["paper"] = name
        for r in reports:
            r["paper"] = name
        plan["renames"] += renames
        plan["reports"] += reports

    log(f"  论文目录 {plan['papers']} 个需要处理：可改名 {len(plan['renames'])} 个文件、"
        f"只报告 {len(plan['reports'])} 条")
    by_reason = {}
    for r in plan["reports"]:
        by_reason[r["reason"].split("（")[0]] = by_reason.get(r["reason"].split("（")[0], 0) + 1
    for k, v in sorted(by_reason.items(), key=lambda kv: -kv[1]):
        log(f"    - {v:4} 条：{k}")

    done = 0
    for name in sorted({r["paper"] for r in plan["renames"]}):
        d = os.path.join(papers_dir, name)
        rs = [r for r in plan["renames"] if r["paper"] == name]
        log(f"  {name}：{len(rs)} 个文件")
        done += _resolve("_apply_renames")(d, rs, apply=apply, log=log)
    plan["renamed"] = done

    if apply and update_progress and plan["renames"]:
        plan["progress_updated"] = _resolve("_update_progress")(papers_dir, plan["renames"], log)
    return plan


def _update_progress(papers_dir, renames, log):
    """把 `progress.json` 里指向旧文件名的记录位置改成新名字（写前备份）。"""
    path = os.path.join(papers_dir, "progress.json")
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    cits = data.get("citations") or {}
    changed = 0
    by_cid = {}
    for r in renames:
        by_cid.setdefault(r["cites_id"], []).append(r)
    for cid, rs in by_cid.items():
        e = cits.get(cid)
        if not e:
            continue
        for r in rs:
            if e.get("local_filename") == r["from"]:
                e["local_filename"] = r["to"]
                changed += 1
    if changed:
        backup = f"{path}.bak.{_dt.datetime.now():%Y%m%d-%H%M%S}"
        shutil.copy2(path, backup)
        _resolve("atomic_write_json")(path, data)
        log(f"  progress.json：更新 {changed} 条记录位置（备份 {os.path.basename(backup)}）")
    return changed
