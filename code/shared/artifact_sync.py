"""按编号对账的**唯一判据**（Y-8，2026-10-15 用户定 ✓；宿主 3.a.1 = 最早的消费方 ✓）。

用户原话："每次处理的时候对照阶段 1、2 的输出，如果其目录下有些编号不用了，就移除对应的 json，
新增的就重新处理。" —— 本模块是这句话的**纯函数**落点 ✓（不碰磁盘 ✗，落盘/归档由调用方做 ✓）：

* **权威编号集合**来自阶段 1/2 的输出 ✓（`citations_meta.json` 主体里的 `index` ✓）；
* 本阶段目录里每个文件都叫 `output_NNNN.json` ✓（命名见 `shared.papers.output_name()` ✓）；
* **归档而非删除** ✓（与 `reconcile` 归档论文目录同一口径 ✓）；
* **身份不符要重算** ✓（编号在、但文件里声明的不是这条引用 → 覆盖重算 ✓，不是归档 ✗）；
* **幂等** ✓：连跑两次，第二次 `process`/`archive` 都应为空 ✓。

**与 `-p` 过滤的作用域（必须写清 ✗）**：过滤只决定"**这次处理谁**" ✓；对账决定"**哪些该归档/补做**" ✓
—— 两者作用域不同 ✓，所以**过滤过的运行不得归档**未在过滤范围内的编号 ✗✗（调用方按
`authoritative_indices` 取"该篇的完整编号集合" ✓，而不是"本次处理的子集" ✗）。
"""

from __future__ import annotations

import re

#: 本阶段产物文件名 → 编号（只认 `output_NNNN.json` ✓，别的文件一概不管 ✓）
_NAME_RE = re.compile(r"^output_(\d{4,})\.json$")


def index_of_artifact(name):
    """`output_NNNN.json` → `NNNN`（整数 ✓）；不是这个形状的文件返回 `None` ✓。"""
    if not isinstance(name, str):
        return None
    matched = _NAME_RE.match(name)
    return int(matched.group(1)) if matched else None


def plan_reconciliation(authoritative_indices, existing_names, identity_ok=None):
    """对账计划：返回 `(process, archive, keep)` 三个**升序**编号列表 ✓。

    * `authoritative_indices`：该篇**完整**的权威编号集合 ✓（不是本次过滤的子集 ✗）；
    * `existing_names`：本阶段目录里已有的文件名 ✓（非 `output_NNNN.json` 的忽略 ✓）；
    * `identity_ok(index)`：可选回调 ✓ —— 编号两侧都有时，用它判断文件里声明的身份
      是不是这条引用 ✓（`False` → 归入 `process` 重算 ✓；默认视为相符 ✓）。

    * `process`：权威有、目录里没有 ✓ **或** 身份不符 ✓ → 要处理/重算；
    * `archive`：目录里有、权威没有 ✓（退役 ✓）→ 要**归档**（不是删除 ✗）；
    * `keep`：两侧都有且身份相符 ✓ → 不动 ✓。
    """
    authoritative = sorted({int(i) for i in (authoritative_indices or [])})
    present = {}
    for name in existing_names or []:
        index = index_of_artifact(name)
        if index is not None:
            present[index] = name

    process, archive, keep = [], [], []
    for index in authoritative:
        if index not in present:
            process.append(index)
        elif identity_ok is not None and not identity_ok(index):
            process.append(index)          # 身份不符 → 重算覆盖 ✓（不是归档 ✗）
        else:
            keep.append(index)
    for index in sorted(present):
        if index not in set(authoritative):
            archive.append(index)
    return process, archive, keep


def paper_indices(paper_dir):
    """该篇的**权威编号集合**：读 `<paper_dir>/citations_meta.json` 的 `index` ✓（读不到 → 空集 ✓）。

    这就是"对照阶段 1/2 的输出"那一步 ✓；只读 ✓、无副作用 ✓。
    """
    import json
    import os

    try:
        with open(os.path.join(paper_dir, "citations_meta.json"), encoding="utf-8") as handle:
            entries = json.load(handle)
    except (OSError, ValueError):
        return set()
    indices = set()
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and str(entry.get("index", "")).isdigit():
            indices.add(int(entry["index"]))
    return indices


def plan_for_paper(paper_dir, stage_dir, identity_ok=None):
    """对某篇论文的某阶段目录**直接给出对账计划** ✓（`plan_reconciliation()` 的编排层 ✓）。

    **签名是刻意的（别加"只看某一个编号"的参数 ✗✗）**：权威编号**永远取整篇的**
    `citations_meta.json` ✓ —— 若让权威集合缩小成本次过滤的子集，目录里其余文件会被判成
    "退役 → 要归档" ✗，那正是 `-p` 过滤会造成静默数据丢失的场景 ✓。
    **逐条过滤只能作用于 `process` 列表** ✓（"这次处理谁" ✓），**绝不能作用于权威集合** ✗
    （"什么该归档" ✓）。`tests/test_artifact_sync_orchestration.py` 有一条**签名断言**把这条钉住 ✓。

    目录不存在 = "什么都还没写" ✓（`process` = 全部权威编号 ✓、`archive` 空 ✓）；
    **只读** ✓：不建目录、不写文件、不移动任何东西 ✓。
    """
    import os

    try:
        names = sorted(os.listdir(stage_dir))
    except OSError:
        names = []
    return plan_reconciliation(paper_indices(paper_dir), names, identity_ok=identity_ok)


#: 归档目录名与 manifest 名（与既有的 `papers/old/` 同一口径 ✓ —— **归档不是删除** ✗）
ARCHIVE_DIRNAME = "old"
MANIFEST_NAME = "manifest.json"


def archive_artifacts(stage_dir, indices, stamp=None, log=None, dry_run=False):
    """把退役编号的产物**归档**到 `<stage_dir>/old/` ✓，并把动作追加进 `manifest.json` ✓。

    * **绝不删除** ✗（与 `reconcile()` 归档论文目录同口径 ✓）；
    * **幂等** ✓：源文件已不在（早先归档过 ✓）就跳过 ✓，manifest 只**追加** ✗ 不重写历史 ✓；
    * 目标名冲突时加 `.stamp` 后缀 ✓（同名再归档不覆盖 ✓）；
    * `dry_run=True` 只计算不搬 ✓（用于预演 ✓）。

    返回实际归档的 `[(index, 目标文件名)]` ✓（`dry_run` 时返回"将会归档"的清单 ✓）。
    """
    import json
    import os


    moved = []
    manifest_path = os.path.join(stage_dir, MANIFEST_NAME)
    for index in sorted({int(i) for i in (indices or [])}):
        name = output_name(index)
        source = os.path.join(stage_dir, name)
        if not os.path.isfile(source):
            continue                      # 已归档过 / 本来就没有 → 不动 ✓（幂等 ✓）
        target_name = name
        target = os.path.join(stage_dir, ARCHIVE_DIRNAME, target_name)
        if os.path.exists(target) and stamp:
            target_name = "%s.%s" % (name, stamp)
            target = os.path.join(stage_dir, ARCHIVE_DIRNAME, target_name)
        moved.append((index, target_name))
    if dry_run or not moved:
        if log is not None and moved:
            log("  预演：将归档 %d 个产物到 %s/" % (len(moved), ARCHIVE_DIRNAME))
        return moved

    os.makedirs(os.path.join(stage_dir, ARCHIVE_DIRNAME), exist_ok=True)
    manifest = {}
    try:
        with open(manifest_path, encoding="utf-8") as handle:
            loaded = json.load(handle)
            manifest = loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        manifest = {}
    archived = manifest.setdefault("archived", [])
    for index, target_name in moved:
        name = output_name(index)
        os.replace(os.path.join(stage_dir, name),
                   os.path.join(stage_dir, ARCHIVE_DIRNAME, target_name))
        archived.append({"index": index, "from": name, "to": "%s/%s" % (ARCHIVE_DIRNAME, target_name),
                         "stamp": stamp})
    tmp = manifest_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=1)
    os.replace(tmp, manifest_path)
    if log is not None:
        log("  已归档 %d 个产物到 %s/（manifest 已追加 ✓）" % (len(moved), ARCHIVE_DIRNAME))
    return moved

#: 三阶段旧的 per-citation 产物前缀（Y-8 第 2 步的回落用 ✓）
LEGACY_PREFIXES = ("enrich_info", "analysis_info", "citation_analysis")


def iter_artifact_files(stage_dir, legacy_prefix=None):
    """列出某阶段目录里的 per-citation 产物 ✓：**新名 `output_NNNN.json` 优先** ✓。

    返回按编号升序的 `[(index, 路径)]` ✓。规则（Y-8 第 2 步 ✓）：目录不存在 → 空 ✓；
    **同编号两代并存 → 只返回新名** ✓（绝不重复计数 ✗）；只有旧名 → 回落旧名 ✓
    （混合目录不漏读 ✓；同一编号有多个旧名属异常态 ✓，取先见者即可 ✓）；其它文件忽略 ✓。
    """
    import os
    import re

    try:
        names = sorted(os.listdir(stage_dir))
    except OSError:
        return []
    prefixes = (legacy_prefix,) if legacy_prefix else LEGACY_PREFIXES
    by_index = {}
    for name in names:
        index = index_of_artifact(name)
        source = "new"
        if index is None:
            for prefix in prefixes:
                matched = re.fullmatch(re.escape(prefix) + r"_(\d{4,})\.json", name)
                if matched:
                    index, source = int(matched.group(1)), "legacy"
                    break
        if index is None:
            continue
        if index not in by_index or source == "new":
            by_index[index] = os.path.join(stage_dir, name)
    return sorted(by_index.items())

# --------------------------------------------------------------------------
# 按编号产物的命名（自 3.a.1 的 `shared/papers.py` 迁入 ✓ —— 宿主＝**阶段 1**，
# 因为 `index` 是阶段 1 分配且不可变的数据 ✓）。本文件**只用标准库** ✓，
# **导入期不读 config** ✗（前一个宿主 `papers.py` 正是因此在消费方里导入失败 ✓）。
# --------------------------------------------------------------------------

OUTPUT_NAME_TEMPLATE = "output_%04d.json"

def output_name(index):
    """`index` → `output_NNNN.json`（编号必须是正整数 ✓；不合法时抛 `ValueError` ✓）。"""
    number = int(index)
    if number <= 0:
        raise ValueError("citation index 必须是正整数，收到 %r" % (index,))
    return OUTPUT_NAME_TEMPLATE % number

def output_path(paper_dir, index):
    """`<论文目录>/output_NNNN.json` 的完整路径（**不创建目录** ✗ —— 落盘由调用方决定 ✓）。"""
    return os.path.join(paper_dir, output_name(index))

def output_path_for_legacy(legacy_path):
    """把旧的 per-citation 产物路径映射成新的 `output_NNNN.json` 路径 ✓（Y-8 双写期用 ✓）。

    只从**文件名尾部**取编号 ✓（`..._NNNN.json` ✓），目录原样保留 ✓，名字仍由
    `output_path()` 产出 ✓（**不在别处再拼一次字符串** ✗）。
    取不到编号（不是按编号的产物 ✓）时返回 `None` ✓ —— 调用方据此**不写** ✓，绝不猜 ✗。
    """
    import os
    import re

    matched = re.search(r"(\d{4,})\.json$", os.path.basename(legacy_path or ""))
    if not matched:
        return None
    return output_path(os.path.dirname(legacy_path), int(matched.group(1)))
