"""论文目录的布局与编号（`papers_layout`）—— **跨仓库共享件，宿主 = 阶段 1**。

**为什么宿主在阶段 1**（2026-10-15 用户定，见 `KNOWN_ISSUES.md` Y-6）：`papers/` 的物理位置
已搬到阶段 1 的 `output/author_<ID>_papers/`，"论文目录的创建 / 编号 / 改名 / 归档"随之前移。
按根 `AGENTS.md`「跨仓库共享代码」，唯一实体放**最早需要它的阶段**，阶段 2 经 `code/_shared.py`
注入后消费同一份（`python -m code.renumber_papers` 的 CLI 仍留在阶段 2，见那边的薄模块）。

**内容 = 原阶段 2 `code/renumber_papers.py` 的库部分，逐字搬移、语义未改**：
`plan()` 算编号重排计划（纯函数、不碰磁盘）、`apply_plan()` 两阶段改名 + `manifest.json`、
`merge_into()` 同作品两目录合并、`remap_progress()` 改 `progress.json` 的派生编号、
`reconcile()` 串起来，以及 `target_dir_name()` / `norm_title()` / `dir_titles()` 等助手。

**为什么需要重排**：`papers/<NNN>-<slug>/` 里的 `NNN` 不是稳定标识 —— 阶段 1 每次抓取按
**引用总量重新排名**，同一篇论文的 `no` 会变（实测 2026-10-08：245 个目录里 213 个的编号与
当前输入不一致，且是互相占位的置换，如 `7<->8`、`11<->12`）。编号不跟着变会造成：`-p N` 解析到
**另一篇论文**；阶段 2 为"新编号"新建空目录、把已下载的 PDF 白下一遍；孤儿目录越积越多。
**身份用标题**（编号既然是排名派生就不能当身份）：归一化标题 -> 当前输入的 `no`；标题重复时用
目录 `citations_meta.json` 的 `cites_id` 集合与候选的重叠度定夺，定不下来报歧义、不动。

**安全**：默认 dry-run；`--apply` 才动，且只做同文件系统改名、绝不删除/覆盖；置换走两阶段
（先 `.renumber_tmp_<ts>/` 再落目标名，失败回滚）；冲突条目不执行只报告；写 `manifest.json`
（from/to、文件数、字节数、清单 sha1）；对不上当前输入的旧世代目录只报告不动。

**依赖**：只用标准库 + 本模块内的文件名消毒函数 `sanitize_filename()`（其 `max_len` 默认值
优先取消费方的 `code.config.MAX_FILENAME_LEN`，取不到用 `DEFAULT_MAX_FILENAME_LEN`）。
**不 import 任何项目模块** —— 这是它能被阶段 1 与阶段 2 同时消费的前提。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re

#: 文件名消毒的默认上限（消费方 `code.config.MAX_FILENAME_LEN` 优先，见 `_default_max_filename_len()`）
DEFAULT_MAX_FILENAME_LEN = 120


def _default_max_filename_len():
    """调用时解析的文件名上限：消费方的 `code.config` 优先，否则本模块默认值。"""
    try:
        from code.config import MAX_FILENAME_LEN as value  # 消费方（阶段 2）的配置
    except Exception:
        return DEFAULT_MAX_FILENAME_LEN
    if isinstance(value, int) and value > 0:
        return value
    return DEFAULT_MAX_FILENAME_LEN


def _default_papers_base_dir():
    """调用时解析的 `papers/` 根：消费方的 `code.config.BASE_DIR` 优先，否则 `None`。

    本项目（阶段 1）自己跑时不需要它 —— 调用方会显式传入路径。
    """
    try:
        from code.config import BASE_DIR as value
    except Exception:
        return None
    return value or None


def _default_output_dir():
    """调用时解析的 output 目录：消费方的 `code.config.OUTPUT_DIR` 优先，否则 `cwd/output`。

    `renumber` 的 manifest 默认落在这里（`output/renumber_<stamp>/`）。**共享件不能 import
    消费方的 config**，所以按需在调用时取；取不到就退化成当前工作目录下的 `output/`。
    """
    try:
        from code.config import OUTPUT_DIR as value  # 消费方（阶段 2）的配置
    except Exception:
        value = None
    return value or os.path.join(os.getcwd(), "output")


def sanitize_filename(name, max_len=None):
    """把字符串清成可用作文件名/目录名的形式（**唯一实现**，阶段 1 / 阶段 2 共用）。

    逐字搬自阶段 2 的 `code/utils.py`（那里现在只做一层委托，见其 docstring）。
    """
    if max_len is None:
        max_len = _default_max_filename_len()
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"[\n\r\t]+", " ", name)
    name = re.sub(r"[_\s]+", "_", name)
    name = re.sub(r"\.{2,}", ".", name)
    name = name.strip("._ ")
    if len(name) > max_len:
        name = name[:max_len].rsplit("_", 1)[0]
    return name if name else "untitled"


DIR_RE = re.compile(r"^(\d{3})-(.+)$")
TMP_PREFIX = ".renumber_tmp_"

#: 归档目录：当前输入里已不存在的论文目录移到这里（阶段 4 只认"名字以数字开头"的目录，
#: 不会扫到它；`check_consistency` 与 `preflight.sh` 显式跳过）
ARCHIVE_DIRNAME = "old"


def _metadata_score(paper_dir: str, pub: dict, input_citations: list) -> int:
    """同名论文的定夺打分：年份 3、发表地 2、作者 2、引用总数 1、引用条数 1。

    同名不同篇最常见的情形是**同一标题的不同版本/年份**（实测：`一种适用于机器翻译的汉语分词方法`
    在输入里既是 2012 中文信息学报那篇、又是 2011 前沿进展那篇）。年份是这里最强的判别信号，
    发表地/作者次之；引用总数与引用条数用于打破同年的僵局。
    """
    meta = _read_json(os.path.join(paper_dir, "paper_meta.json")) or {}
    cm = _read_json(os.path.join(paper_dir, "citations_meta.json"))
    n_dir = len(cm) if isinstance(cm, list) else None
    score = 0
    if str(meta.get("year") or "") == str(pub.get("year") or "") and meta.get("year"):
        score += 3
    if norm_title(meta.get("venue")) and norm_title(meta.get("venue")) == norm_title(pub.get("venue")):
        score += 2
    if norm_title(meta.get("authors")) and norm_title(meta.get("authors")) == norm_title(pub.get("authors")):
        score += 2
    if meta.get("num_citations") is not None and meta.get("num_citations") == pub.get("num_citations"):
        score += 1
    if n_dir is not None and n_dir == len(input_citations):
        score += 1
    return score


def _candidate_match(paper_dir: str, title: str, papers: list) -> dict:
    """这条孤儿目录是否其实还在当前输入里（只是标题被改了）。

    先比 **URL**（Google Scholar 的 citation_for_view 链接，实测有时跨世代不变，是很硬的证据），
    再比标题相似度：需 ≥ 0.90 且**唯一**（与次优差 ≥ 0.05），否则不猜。
    """
    import difflib
    meta = _read_json(os.path.join(paper_dir, "paper_meta.json")) or {}
    url = (meta.get("url") or "").strip()
    if url:
        same = [p["pub"] for p in papers if (p["pub"].get("url") or "").strip() == url]
        if len(same) == 1:
            return {"no": same[0].get("no"), "by": "url", "similarity": 1.0}
    mine = norm_title(title)
    if not mine:
        return None
    scored = sorted(
        ((difflib.SequenceMatcher(None, mine, norm_title(p["pub"].get("title"))).ratio(), p["pub"])
         for p in papers), key=lambda x: -x[0])
    if not scored:
        return None
    best, second = scored[0][0], (scored[1][0] if len(scored) > 1 else 0.0)
    if best >= 0.90 and (best - second) >= 0.05:
        return {"no": scored[0][1].get("no"), "by": "title", "similarity": round(best, 3)}
    return None


def norm_title(text: str) -> str:
    """标题归一化（大小写/标点/空白不敏感）——跨世代匹配的唯一可靠键。"""
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", (text or "").lower()).strip()


def target_dir_name(pub: dict) -> str:
    """与阶段 2 `paper_dir_for()` 完全一致的目录名（保证之后不再产生重复目录）。"""
    no = int(pub.get("no"))
    return f"{no:03d}-{sanitize_filename(pub.get('title') or f'paper_{no}', max_len=100)}"


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def dir_titles(paper_dir: str) -> tuple:
    """目录里的标题：(paper_meta.title, citations_meta.title)，缺失返回空串。"""
    meta = _read_json(os.path.join(paper_dir, "paper_meta.json")) or {}
    cits = _read_json(os.path.join(paper_dir, "citations_meta.json"))
    cit_title = ""
    if isinstance(cits, list) and cits and isinstance(cits[0], dict):
        cit_title = cits[0].get("_paper_title") or ""
    return (meta.get("title") or "", cit_title)


def dir_cites_ids(paper_dir: str) -> set:
    """目录已下载/已记录的引用身份集合（用于标题歧义时的定夺）。"""
    cits = _read_json(os.path.join(paper_dir, "citations_meta.json"))
    if not isinstance(cits, list):
        return set()
    return {c.get("cites_id") for c in cits if isinstance(c, dict) and c.get("cites_id")}


def _dir_stats(paper_dir: str) -> dict:
    pdfs = bytes_ = files = 0
    for root, _dirs, names in os.walk(paper_dir):
        for n in names:
            p = os.path.join(root, n)
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            files += 1
            bytes_ += size
            if n.lower().endswith(".pdf"):
                pdfs += 1
    return {"files": files, "pdfs": pdfs, "bytes": bytes_}


def plan(base_dir: str, papers: list) -> dict:
    """算出编号重排计划（不触碰磁盘）。"""
    by_title = {}
    for p in papers:
        pub = p.get("pub") or {}
        key = norm_title(pub.get("title"))
        if key:
            by_title.setdefault(key, []).append(p)

    ok, moves, orphans, ambiguous, conflicts, merges = 0, [], [], [], [], []
    claimed = {}                       # 目标号 -> 目录名（检测两个目录抢同一目标）
    dirs = sorted(d for d in os.listdir(base_dir)
                  if DIR_RE.match(d) and os.path.isdir(os.path.join(base_dir, d)))
    for name in dirs:
        paper_dir = os.path.join(base_dir, name)
        old_no = int(name[:3])
        title, _ = dir_titles(paper_dir)
        if not title:
            meta = _read_json(os.path.join(paper_dir, "paper_meta.json")) or {}
            if not meta:
                orphans.append({"dir": name, "old_no": old_no, "title": "",
                                "path": paper_dir, "match": None,
                                "reason": "既无 paper_meta.json 也读不到标题"})
                continue
        hits = by_title.get(norm_title(title), [])
        retitle = None
        if not hits:
            match = _candidate_match(paper_dir, title, papers)
            if not match:
                orphans.append({
                    "dir": name, "old_no": old_no, "title": title, "path": paper_dir,
                    "match": None,
                    "reason": "标题不在当前输入里（该论文已从本次抓取中消失）→ 归档到 old/",
                })
                continue
            # 标题被改过（URL 精确命中或相似度 >= 0.90 且唯一）→ 按匹配到的论文正常改名/合并，
            # 这样它已下载的 PDF 与下游结果都能被复用，而不是被归档后重下
            hits = [p2 for p2 in papers if (p2.get("pub") or {}).get("no") == match["no"]]
            if not hits:
                continue
            retitle = match
        if len(hits) > 1:
            # 标题歧义 → 用 cites_id 交集定夺（唯一严格最大才算）
            mine = dir_cites_ids(paper_dir)
            scored = sorted(((len(mine & {c.get("cites_id") for c in (p.get("citations") or [])
                                     if isinstance(c, dict)}), p) for p in hits),
                            key=lambda x: -x[0])
            if len(scored) > 1 and scored[0][0] > 0 and scored[0][0] > scored[1][0]:
                hits = [scored[0][1]]
            else:
                # cites_id 交集定夺不了（很多中文文献没有 cites_id）→ 用元数据打分：
                # 年份/发表地/作者/引用总数/引用条数，需唯一且与次优拉开 >= 2 分
                meta_scored = sorted(((_metadata_score(paper_dir, h.get("pub") or {},
                                                        h.get("citations") or []), h) for h in hits),
                                     key=lambda x: -x[0])
                if (len(meta_scored) > 1 and meta_scored[0][0] >= 4
                        and meta_scored[0][0] - meta_scored[1][0] >= 2):
                    hits = [meta_scored[0][1]]
                else:
                    ambiguous.append({"dir": name, "old_no": old_no, "title": title,
                                      "candidates": [(h.get("pub") or {}).get("no") for h in hits],
                                      "scores": [s0 for s0, _h in meta_scored],
                                      "reason": "标题在当前输入里重复，cites_id 与元数据评分都无法定夺"})
                    continue
        pub = hits[0].get("pub") or {}
        new_no = int(pub["no"])
        if new_no == old_no and name == target_dir_name(pub):
            ok += 1
            continue
        target_name = target_dir_name(pub)
        entry = {"dir": name, "old_no": old_no, "new_no": new_no,
                 "from": paper_dir, "to": os.path.join(base_dir, target_name),
                 "title": title, "target_dir": target_name}
        entry.update(_dir_stats(paper_dir))
        if retitle:
            entry["retitled"] = retitle
        # 目标“存在”可能只是大小写差异（macOS 大小写不敏感）→ 同一个目录，不用动
        if os.path.exists(entry["to"]):
            try:
                same = os.path.samefile(entry["to"], paper_dir)
            except OSError:
                same = False
            if same:
                ok += 1
                continue
        if os.path.exists(entry["to"]) and target_name not in {m["dir"] for m in moves}:
            # 目标目录已存在（另一编号下的**同一篇论文**）→ 记合并，而不是冲突
            t_title, _ = dir_titles(entry["to"])
            if norm_title(t_title) == norm_title(pub.get("title") or title):
                merges.append(dict(entry, reason="同一篇论文的另一编号目录已存在 → 并入"))
            else:
                conflicts.append(dict(entry, reason="目标目录已存在且标题不同（不覆盖）"))
            continue
        if new_no in claimed:
            # 目标号已被另一目录认领（它会被改名到该编号）→ 也要并入，等改名完成后再合
            merges.append(dict(entry, reason=f"目标号 {new_no:03d} 由 {claimed[new_no]} 改名占用 → 并入"))
            continue
        claimed[new_no] = name
        moves.append(entry)
    input_nos = {int((p.get("pub") or {}).get("no")) for p in papers}
    dir_nos = {int(d[:3]) for d in dirs}
    return {"base_dir": base_dir, "dirs": len(dirs), "ok": ok, "moves": moves,
            "orphans": orphans, "ambiguous": ambiguous, "conflicts": conflicts,
            "merges": merges,
            "input_papers": len(papers), "input_without_dir": sorted(input_nos - dir_nos),
            "dir_without_input": sorted(dir_nos - input_nos)}


#: 合并时按类型处理的子目录（论文级 + 三路下游 per-citation）
_MERGE_JSON_DIRS = ("enrich_info", "analysis_info", "analysis_content")


def merge_into(src: str, dst: str, quarantine_root: str, log=print) -> dict:
    """把 ``src`` 目录里 ``dst`` 缺失的内容并入 ``dst``（同作品的两个编号目录）。

    只**移动缺失的文件**，绝不覆盖、绝不删除：

    * ``citations/*.pdf``：``dst`` 没有的同名文件 → 移过去（已下载的努力不浪费）；
    * ``citations_meta.json`` / ``paper_meta.json``：``dst`` 缺失才移过去（``paper_meta.no`` 改成
      ``dst`` 的编号）；两边都有 → 保留 ``dst`` 的，``src`` 那份移进隔离区并记为 superseded；
    * ``enrich_info`` / ``analysis_info`` / ``analysis_content`` 下的文件：按文件名逐个合并，
      两边同名保留 ``dst`` 的（编号是引用级的，同名即同一引用，不该覆盖已有结果）；
    * 其余内容（AppleDouble、备份等）与最后剩下的 ``src`` 目录本身 → 移进隔离区。

    返回记录（进 manifest）。
    """
    rec = {"from": src, "to": dst, "pdfs_moved": [], "files_moved": [],
           "superseded": [], "quarantined": []}
    src_name = os.path.basename(src)
    qdir = os.path.join(quarantine_root, src_name)
    os.makedirs(os.path.join(dst, "citations"), exist_ok=True)

    def _quarantine(path, why):
        rel = os.path.relpath(path, src)
        dest = os.path.join(qdir, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        os.rename(path, dest)
        rec["quarantined"].append({"path": rel, "why": why, "to": dest})

    # 1) PDF
    src_cit = os.path.join(src, "citations")
    if os.path.isdir(src_cit):
        for name in sorted(os.listdir(src_cit)):
            s_path = os.path.join(src_cit, name)
            d_path = os.path.join(dst, "citations", name)
            if not os.path.isfile(s_path):
                continue
            if os.path.exists(d_path):
                _quarantine(s_path, "目标已有同名文件（保留目标的）")
            else:
                os.rename(s_path, d_path)
                rec["pdfs_moved"].append(name)

    # 2) 论文级元数据
    for meta_name in ("citations_meta.json", "paper_meta.json"):
        s_path = os.path.join(src, meta_name)
        d_path = os.path.join(dst, meta_name)
        if not os.path.isfile(s_path):
            continue
        if os.path.exists(d_path):
            _quarantine(s_path, "目标已有该元数据（保留目标的那份）")
            rec["superseded"].append(meta_name)
            continue
        if meta_name == "paper_meta.json":
            data = _read_json(s_path)
            if isinstance(data, dict):
                data["no"] = int(os.path.basename(dst)[:3])
                tmp = s_path + ".merge.tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, s_path)
        os.rename(s_path, d_path)
        rec["files_moved"].append(meta_name)

    # 3) 三路下游 per-citation（按文件名，同名保留目标）
    for sub in _MERGE_JSON_DIRS:
        s_sub = os.path.join(src, sub)
        if not os.path.isdir(s_sub):
            continue
        os.makedirs(os.path.join(dst, sub), exist_ok=True)
        for name in sorted(os.listdir(s_sub)):
            s_path = os.path.join(s_sub, name)
            if not os.path.isfile(s_path):
                continue
            d_path = os.path.join(dst, sub, name)
            if os.path.exists(d_path):
                _quarantine(s_path, f"{sub} 下同名文件（保留目标的）")
            else:
                os.rename(s_path, d_path)
                rec["files_moved"].append(f"{sub}/{name}")

    # 4) 余下的一切 + 目录本身 → 隔离区
    for root, _dirs, names in os.walk(src):
        for n in names:
            _quarantine(os.path.join(root, n), "合并后剩余的杂项")
    leftovers = []
    for root, dirs, names in os.walk(src, topdown=False):
        if not dirs and not names:
            leftovers.append(root)
    for d in leftovers:
        try:
            os.rmdir(d)          # 只删**空**目录
        except OSError:
            pass
    if os.path.isdir(src):
        dest = os.path.join(quarantine_root, src_name + ".leftover")
        os.makedirs(quarantine_root, exist_ok=True)
        os.rename(src, dest)
        rec["quarantined"].append({"path": ".", "why": "合并后剩下的目录（不删除）", "to": dest})
    log(f"  ⇄ {src_name} → {os.path.basename(dst)}"
        f"（并入 {len(rec['pdfs_moved'])} 个 PDF、{len(rec['files_moved'])} 个文件，"
        f"隔离 {len(rec['quarantined'])} 项）")
    return rec


def remap_progress(plan_dict: dict, progress_path: str, backup_dir: str,
                   stamp: str = None, log=print) -> dict:
    """把 ``progress.json`` 里的论文编号跟着目录改名一起改。

    为什么必须做：`progress.json` 用 ``papers[str(paper_index)]["paper_no"]`` 解析论文目录
    （运行期的 ``ProgressTracker.paper_dir_for_index`` 与巡检的 ``_paper_dir_for_index`` 同口径）。
    目录改名后这些编号就过时了 —— 补齐会去**别的论文**目录找文件（白重下），巡检也会报上千条
    `progress_missing`（实测 126 → 1847）。引用级编号不受影响：跳过下载判定按 ``cites_id``。

    **逐条判定，不做全局映射**（这是踩坑后的结论）：映射是按**旧目录编号**建的关键字，而
    `progress.papers[*].paper_no` 在上一轮同步后已经可能是**新编号**（同一个数字在旧/新语义下
    是不同的论文）。全局套用会误改别人的条目、或在键碰撞时整体放弃（实测两者都发生过）。
    现在的规则：

    * 计划里的每条改名/合并都带 ``title``（旧目录的标题）与 ``old_no → new_no``；
    * 只有 ``title`` 归一化相同**且** ``paper_no == old_no`` 的条目才改（精确）；
    * 条目没有 title 时退回只比编号，但**目标键被别的条目占用就跳过这一条**并报告（不再整体拒绝）；
    * 引用条目的 ``paper_index`` 跟着它所属的条目一起改。

    天然幂等：改过的条目其 ``paper_no`` 已是新号，再跑不会命中。写盘前把原文件复制进 manifest 目录
    （名字带 stamp），写盘走临时文件 + ``os.replace``。
    """
    data = _read_json(progress_path)
    if not isinstance(data, dict):
        return {"remapped_papers": 0, "remapped_citations": 0, "reason": "progress.json 读不了"}
    stamp = stamp or "unknown"
    if stamp in (data.get("renumber_applied") or []):
        return {"remapped_papers": 0, "remapped_citations": 0, "reason": "该次重排已应用过，跳过"}

    by_title, by_no = {}, {}
    for m in (plan_dict.get("moves") or []) + (plan_dict.get("merges") or []):
        old_no = m.get("old_no")
        if old_no is None:
            base = os.path.basename(m.get("from") or "")
            old_no = int(base[:3]) if base[:3].isdigit() else None
        new_no = m.get("new_no")
        if new_no is None:
            base = os.path.basename(m.get("to") or "")
            new_no = int(base[:3]) if base[:3].isdigit() else None
        if not (isinstance(old_no, int) and isinstance(new_no, int)):
            continue
        title = (m.get("title") or "").strip()
        if title:
            by_title[(norm_title(title), old_no)] = new_no
        by_no[old_no] = new_no
    if not by_no:
        return {"remapped_papers": 0, "remapped_citations": 0, "reason": "计划里没有编号变更"}

    papers_meta = data.get("papers") or {}
    new_papers, remapped_papers, skipped, index_map = {}, 0, [], {}
    for pi, entry in papers_meta.items():
        if not isinstance(entry, dict):
            new_papers[pi] = entry
            continue
        old_no = entry.get("paper_no")
        title = (entry.get("title") or "").strip()
        new_no = by_title.get((norm_title(title), old_no)) if title else None
        if new_no is None and old_no in by_no and not title:
            new_no = by_no[old_no]
        if new_no is None or new_no == old_no:
            new_papers[pi] = entry
            continue
        new_key = str(new_no - 1)
        if new_key in papers_meta and new_key != pi:
            skipped.append({"key": pi, "paper_no": old_no, "title": title[:40],
                            "reason": f"目标索引 {new_key} 已被别的条目占用"})
            new_papers[pi] = entry
            continue
        new_papers[new_key] = dict(entry, paper_no=new_no)
        index_map[pi] = new_key
        remapped_papers += 1

    remapped_citations = 0
    for _cid, c in (data.get("citations") or {}).items():
        if not isinstance(c, dict):
            continue
        new_key = index_map.get(str(c.get("paper_index")))
        if new_key is not None:
            c["paper_index"] = int(new_key)
            remapped_citations += 1

    if not (remapped_papers or remapped_citations):
        return {"remapped_papers": 0, "remapped_citations": 0,
                "reason": "编号已是最新，无需改动", "skipped": skipped}
    os.makedirs(backup_dir, exist_ok=True)
    backup = os.path.join(backup_dir, f"progress.json.bak.{stamp}")
    if os.path.exists(backup):
        backup = f"{backup}.{int(_dt.datetime.now().timestamp())}"
    with open(progress_path, "rb") as src, open(backup, "wb") as dst:
        dst.write(src.read())
    data["papers"] = new_papers
    applied = list(data.get("renumber_applied") or [])
    applied.append(stamp)
    data["renumber_applied"] = applied
    tmp = progress_path + ".renumber.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, progress_path)
    log(f"  🧭 progress.json 同步编号：papers {remapped_papers} 条、citations {remapped_citations} 条"
        + (f"，跳过 {len(skipped)} 条（目标索引被占）" if skipped else "")
        + f"（原文件备份 {backup}）")
    return {"remapped_papers": remapped_papers, "remapped_citations": remapped_citations,
            "backup": backup, "skipped": skipped}


def _listing_sha1(paper_dir: str) -> str:
    h = hashlib.sha1()
    for root, dirs, names in os.walk(paper_dir):
        dirs.sort()
        for n in sorted(names):
            rel = os.path.relpath(os.path.join(root, n), paper_dir)
            h.update(rel.encode("utf-8", "surrogateescape"))
            h.update(b"\x00")
    return h.hexdigest()


def apply_plan(plan_dict: dict, manifest_dir: str = None, log=print) -> dict:
    """执行计划：两阶段改名 + 更新 paper_meta.no + 写 manifest。返回执行记录。"""
    moves = plan_dict["moves"]
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    manifest_dir = manifest_dir or os.path.join(
        _default_output_dir(), f"renumber_{stamp}")
    manifest = {"timestamp": stamp, "base_dir": plan_dict["base_dir"],
                "moved": [], "merged": [], "archived": [], "progress": {},
                "skipped_conflicts": plan_dict["conflicts"],
                "orphans": plan_dict["orphans"], "ambiguous": plan_dict["ambiguous"]}
    archive_pending = [o for o in plan_dict.get("orphans", [])
                       if not o.get("match") and o.get("path")
                       and os.path.isdir(o.get("path") or "")]
    if not moves and not plan_dict.get("merges") and not archive_pending:
        return {"moved": 0, "merged": 0, "archived": 0,
                "manifest": None, "manifest_dir": None}

    os.makedirs(manifest_dir, exist_ok=True)
    tmp_root = os.path.join(plan_dict["base_dir"], TMP_PREFIX + stamp)
    os.makedirs(tmp_root, exist_ok=True)
    staged, done = [], []
    try:
        # 阶段 1：全部移入临时区（同文件系统 rename，原子）
        for m in moves:
            rec = dict(m, files_sha1=_listing_sha1(m["from"]))
            tmp = os.path.join(tmp_root, m["dir"])
            os.rename(m["from"], tmp)
            rec["tmp"] = tmp
            staged.append(rec)
        # 阶段 2：落到目标名（先改完名，合并才有确定的目标目录）
        for rec in staged:
            if os.path.exists(rec["to"]):
                raise RuntimeError(f"目标已被占用，放弃：{rec['to']}")
            os.rename(rec["tmp"], rec["to"])
            meta_path = os.path.join(rec["to"], "paper_meta.json")
            meta = _read_json(meta_path)
            if isinstance(meta, dict) and meta.get("no") != rec["new_no"]:
                meta["no"] = rec["new_no"]
                tmp_meta = meta_path + ".renumber.tmp"
                with open(tmp_meta, "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False, indent=2)
                os.replace(tmp_meta, meta_path)
            rec.pop("tmp", None)
            done.append(rec)
            log(f"  ↻ {rec['dir']} → {rec['target_dir']}"
                f"（#{rec['old_no']} → #{rec['new_no']}，{rec['pdfs']} PDF / {rec['bytes']/1e6:.0f}MB）")
        # 阶段 3：同作品目录合并（目标目录此时才确定）
        for m in plan_dict.get("merges", []):
            if not os.path.isdir(m["from"]):
                continue
            if not os.path.isdir(m["to"]):
                # 目标还没出现（例如目标号那篇本次没有被改名建出来）→ 退化为一次改名
                os.makedirs(os.path.dirname(m["to"]) or ".", exist_ok=True)
                os.rename(m["from"], m["to"])
                done.append(dict(m, target_dir=os.path.basename(m["to"]),
                                 note="目标目录原本不存在，直接改名"))
                log(f"  ↻ {m['dir']} → {os.path.basename(m['to'])}（无目标可并入，直接改名）")
                continue
            manifest["merged"].append(dict(
                merge_into(m["from"], m["to"], manifest_dir, log=log),
                old_no=m.get("old_no"), new_no=m.get("new_no")))

        # 阶段 4：归档"当前输入里已不存在"的论文目录（疑似改标题的不动，等匹配/人工确认）
        archive_root = os.path.join(plan_dict["base_dir"], ARCHIVE_DIRNAME)
        for o in plan_dict.get("orphans", []):
            if o.get("match") or not o.get("path") or not os.path.isdir(o["path"]):
                continue
            os.makedirs(archive_root, exist_ok=True)
            dest = os.path.join(archive_root, o["dir"])
            if os.path.exists(dest):
                dest = f"{dest}.{stamp}"
            os.rename(o["path"], dest)
            manifest["archived"].append({"from": o["path"], "to": dest,
                                        "reason": o["reason"]})
            log(f"  📦 {o['dir'][:56]} → {ARCHIVE_DIRNAME}/（当前输入里已不存在，归档保存）")

        # 阶段 5：progress.json 的论文编号跟着改（否则运行期补齐/巡检会指到别的论文）
        prog = os.path.join(plan_dict["base_dir"], "progress.json")
        if os.path.isfile(prog):
            manifest["progress"] = remap_progress(plan_dict, prog, manifest_dir,
                                                  stamp=stamp, log=log)
    except Exception:
        # 尽力回滚：临时区里的搬回原名，已落位的搬回临时区再搬回原名
        for rec in staged:
            for cur, back in ((rec.get("tmp"), rec["from"]), (rec["to"], rec["from"])):
                if cur and os.path.isdir(cur) and not os.path.exists(back):
                    try:
                        os.rename(cur, back)
                    except OSError:
                        pass
        raise
    finally:
        try:
            os.rmdir(tmp_root)
        except OSError:
            pass
        manifest["moved"] = done
        with open(os.path.join(manifest_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
    return {"moved": len(done), "merged": len(manifest["merged"]),
            "archived": len(manifest["archived"]),
            "progress_remapped": (manifest.get("progress") or {}).get("remapped_citations", 0),
            "manifest": os.path.join(manifest_dir, "manifest.json"),
            "manifest_dir": manifest_dir}


def reconcile(papers: list, base_dir: str = None, apply: bool = False, log=print) -> dict:
    """流程入口：算计划（+可选执行）。返回计划字典（含 applied 字段）。"""
    base_dir = base_dir or _default_papers_base_dir()
    if not os.path.isdir(base_dir):
        return {"base_dir": base_dir, "moves": [], "orphans": [], "ambiguous": [],
                "conflicts": [], "ok": 0, "dirs": 0, "applied": False,
                "error": f"papers 目录不存在：{base_dir}"}
    p = plan(base_dir, papers)
    p["applied"] = False
    p["moved"] = 0
    p["merged"] = 0
    p["archived"] = 0
    p["manifest"] = None
    _pending_archive = [o for o in p.get("orphans", [])
                        if not o.get("match") and o.get("path")
                        and os.path.isdir(o.get("path") or "")]
    if (p["moves"] or p.get("merges") or _pending_archive) and apply:
        result = apply_plan(p, log=log)
        p["applied"] = ((result["moved"] > 0) or (result.get("merged", 0) > 0)
                        or (result.get("archived", 0) > 0))
        p["manifest"] = result["manifest"]
        p["moved"] = result["moved"]
        p["merged"] = result.get("merged", 0)
        p["archived"] = result.get("archived", 0)
    return p
