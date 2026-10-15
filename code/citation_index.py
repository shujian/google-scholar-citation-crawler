"""引用编号的**唯一分配者**（阶段 1 产生；编号不可变）—— 用户 2026-10-15 定案。

**规则**（写入 `KNOWN_ISSUES.md` Y-6/Y-7 的落点）：

* **首次分配后永不改变**：身份命中既有记录 → **逐字沿用**；
* 新引文 → `max(已用 ∪ 已退役) + 1`（**水位只增不减**）；
* 引文消失（上游删除/合并）→ 编号进 **tombstone**，**永不复用**。

**为什么必须不可变**：引用编号写在**下游所有产物**的名字里（`citations/<NNNN>-<slug>.pdf`、
`enrich_info_NNNN.json`、`analysis_info_NNNN.json`、`citation_analysis_NNNN.json`、`download_progress`
的条目）。只要编号**可能变化**，阶段 2/3/4 就得跟着迁移 —— 那套"广播变更"的机制（manifest / 账本 /
remap）本身就是复杂度。把编号做成**身份**而不是**位置**，迁移这件事就**不存在**了 ✓。

**身份判定委托共享件** `code/shared/identity.py` 的 `same_citation_identity()` —— 与下游四处
（3.a.1 / 3.a.2 / 3.b / 阶段 4）**同一份判据**，所以上游 `cites_id` 换代（根 AGENTS T）或
Google 反向合并（K128）时，两侧的"同一条"判断不会漂移 ✓。

**状态形状**（持久化在阶段 1 自己的产物里；记录带全身份字段，便于按身份回溯匹配）：

```jsonc
"citation_index": {
  "assigned": [ {"index": 37, "cites_id": "…", "title": "…", "year": "…",
                 "authors": "…", "url": "…"}, … ],
  "next": 105,
  "retired": [5, 9, 23]
}
```
"""

from __future__ import annotations

#: 身份记录里要保留的字段（与共享判据读取的字段一致；`authors` 优先 `authors_raw`，K47）
IDENTITY_FIELDS = ("cites_id", "title", "year", "authors_raw", "authors", "url")

#: 状态里 `assigned` 记录的字段（= 编号 + 上表）
RECORD_FIELDS = ("index",) + IDENTITY_FIELDS


def empty_state():
    """全新状态（没有任何历史分配时用）。"""
    return {"assigned": [], "next": 1, "retired": []}


def normalize_state(state):
    """容错读取：缺键、类型不对、编号重复/非法都收敛成合法状态（不抛异常）。"""
    state = state if isinstance(state, dict) else {}
    records, seen = [], set()
    for record in state.get("assigned") or []:
        if not isinstance(record, dict):
            continue
        number = record.get("index")
        if not isinstance(number, int) or number < 1 or number in seen:
            continue
        seen.add(number)
        records.append({field: record.get(field) for field in RECORD_FIELDS})
    retired = {n for n in (state.get("retired") or [])
               if isinstance(n, int) and n >= 1 and n not in seen}
    next_number = state.get("next")
    if not isinstance(next_number, int) or next_number < 1:
        next_number = 1
    highest = max(seen | retired, default=0)
    if next_number <= highest:          # 水位只增不减：损坏/回退的 next 也要抬起来
        next_number = highest + 1
    return {"assigned": records, "next": next_number, "retired": sorted(retired)}


def _record_for(citation, number):
    record = {"index": number}
    for field in IDENTITY_FIELDS:
        value = citation.get(field)
        if value is not None:
            record[field] = value
    return record


def _find_record(citation, records):
    """按**共享身份判据**在既有记录里找同一条引用；找不到返回 None。"""
    from code.shared.identity import same_citation_identity

    for record in records:
        try:
            if same_citation_identity(record, citation):
                return record
        except Exception:      # 判据内部对畸形输入应已容错；这里兜底不让它中断分配
            continue
    return None


def assign_indices(citations, state=None):
    """给一批引文分配编号。返回 ``(new_state, indices)``（`indices` 与 `citations` 等长）。

    * 命中既有记录（共享身份判据）→ **沿用其编号**；
    * 新引文 → 取水位（`max(已用 ∪ 已退役) + 1`）；
    * 这次没出现的编号 → 进 `retired`（**永不复用**）。
    """
    citations = list(citations or [])
    state = normalize_state(state)
    previous, next_number, retired = state["assigned"], state["next"], set(state["retired"])
    used = {r["index"] for r in previous} | retired
    records, indices = [], []

    for citation in citations:
        citation = citation if isinstance(citation, dict) else {}
        record = _find_record(citation, previous)
        if record is not None:                       # 沿用（编号不可变）
            number = record["index"]
            previous = [r for r in previous if r is not record]
        else:                                        # 新引文：水位 +1（跳过退役号）
            while next_number in used:
                next_number += 1
            number = next_number
            next_number += 1
        used.add(number)
        records.append(_record_for(citation, number))
        indices.append(number)

    retired |= {r["index"] for r in previous}        # 消失的 → tombstone，绝不复用
    return {"assigned": records, "next": next_number, "retired": sorted(retired)}, indices


def assign_entry_indices(entries, previous_by_title=None):
    """给**整份产物**的 entries 分配编号（阶段 1 写出前的最后一步，用户 2026-10-15）。

    * `entries` 是产物里的 `papers` 列表（每项 `{"pub": …, "citations": [...]}`）；
    * `previous_by_title` 是**上一版产物**按标题索引的同一结构（没有就传 `None`）；
    * 对每个 entry：用 `assign_indices()` 算编号 → 写回 `citation["index"]` 与
      `entry["citation_index"]`（**entry 层**，与 `pub` 平级 —— `pub` 会被 `PubInfo` 归一化，
      放在里面会丢 ✗）。

    **调用时机**：必须在 `_dedup_citations_for_output()` **之后**（对最终列表分配 ✓）。
    **幂等**：本次没抓到的论文，其 citations 与上一轮完全相同 → 身份命中 → 编号原样沿用 ✓。
    返回分配过的 entry 数。
    """
    previous_by_title = previous_by_title or {}
    assigned = 0
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        citations = entry.get("citations") or []
        pub = entry.get("pub") or {}
        title = pub.get("title", "") if isinstance(pub, dict) else ""
        previous = (previous_by_title.get(title) or {}).get("citation_index")
        state, indices = assign_indices(citations, previous)
        for citation, number in zip(citations, indices):
            if isinstance(citation, dict):
                citation["index"] = number
        entry["citation_index"] = state
        assigned += 1
    return assigned
