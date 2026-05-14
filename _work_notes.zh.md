# Work Notes: Google Scholar Citation Crawler

本文档记录开发中的关键技术细节、架构决策和踩坑记录。按时间顺序的更新历史见 [_update_history.zh.md](_update_history.zh.md)。

## 2026-05-12: 代码规范修复

- **`index_year_records()`**: 从 `scholar_citation.py` 中重复 3 次的 year_records 解析逻辑提取的共享函数，位于 `crawler/output_state.py`。接受一个 year records 列表，返回 `{year: record}` 字典。
- **`_direct_fetch_diagnostics_message()`**: 合并了原 `_direct_fetch_summary_message` 和 `_direct_fetch_log_message`，通过 `prefix` 参数区分不同场景。
- **Bug**: `fetch_by_year` 中的 `year_fetched_citations` 变量从未赋值，已替换为 `year_batch.citations`。

## 开发环境

- **Conda 环境**: `scholar` (`/Users/huangshujian/miniforge3/envs/scholar`)
- **Python**: 3.11
- **scholarly package**: `/Users/huangshujian/miniforge3/envs/scholar/lib/python3.11/site-packages/scholarly`
  - 关键文件: `publication_parser.py` (PublicationParser, _SearchScholarIterator)
  - 关键文件: `_scholarly.py` (scholarly core)

## 项目结构

```
google-scholar-citation-crawler/
├── scholar_citation.py          # CLI 入口 + PaperCitationFetcher 编排器
├── crawler/                     # 所有支撑模块
│   ├── __init__.py
│   ├── common.py                # 常量 + 无状态工具函数
│   ├── author_fetcher.py        # AuthorProfileFetcher
│   ├── profile_io.py            # profile JSON / Excel 输出
│   ├── citation_cache.py        # year-count / diagnostics 纯函数
│   ├── citation_strategy.py     # fetch policy、refresh 策略、reconciliation
│   ├── citation_identity.py     # citation 去重键与信息提取
│   ├── citation_io.py           # cache I/O、status 推导、citations Excel 输出
│   ├── citation_fetch.py        # fetch_citations_with_progress + fetch_by_year
│   ├── fetch_session.py         # BatchFetchSession, YearFetchSession
│   ├── page_visit.py            # PageVisit — 每页 captcha/proxy/retry 恢复
│   ├── scholarly_session.py     # SessionContext + scholarly monkey-patch + year probe
│   ├── interactive.py           # cURL cookie 注入、captcha 提示、proxy-switch 等待
│   ├── citation_models.py       # YearRecord, ResumeState, FetchPolicy
│   ├── output_state.py          # PaperFetchState dataclass + 输出文件 _fetch_state 读写
│   ├── pub_info.py              # PubInfo dataclass（pub 字段规范化）
│   └── cli.py                   # parse_args() + _run_main(args)
├── tests/                       # 单元测试（119 个，不需要网络）
│   ├── conftest.py              # 共享 stubs + FetcherTestCase 基类
│   ├── test_scholar_patch.py    # scholarly patch URL 日志、cookie 注入、CLI 解析
│   ├── test_year_fetch_early.py # year fetch early-stop / histogram-authoritative
│   ├── test_fetch_policy.py     # Fetch policy 选择、refresh 策略
│   ├── test_direct_fetch.py     # Direct fetch：progress save、early-stop、resume、dedup
│   ├── test_year_fetch_main.py  # Year fetch：materialize、selective refresh
│   ├── test_output.py           # save_output、flush promotion、reconciliation
│   ├── test_citation_status.py  # _citation_status、rehydrate、diagnostics 边界
│   ├── test_main_loop.py        # _run_main_loop retry、main() CLI 集成
│   ├── test_output_state.py     # 输出状态读写、citation_status 优先级
│   └── test_profile.py          # AuthorProfileFetcher 计数汇总和 JSON/Excel 输出
├── fix_output_fetch_state.py    # 输出文件 _fetch_state 迁移/修复脚本
├── _update_history.zh.md        # 按时间顺序的更新历史
├── _user.zh.md                  # 用户输入记录
├── requirements.txt             # scholarly>=1.7, openpyxl>=3.1, httpx==0.27.2
└── README.md                    # 对外功能说明
```

### 输出文件

- `author_{id}_profile.json` / `.xlsx` — 作者信息 + 论文列表
- `author_{id}_paper_citations.json` / `.xlsx` — 逐篇引用列表
- `output/curl.txt` — Cookie 持久化

---

## 快速使用

```bash
# 安装依赖
pip install -r requirements.txt

# 日常使用
python scholar_citation.py --author YOUR_AUTHOR_ID

# 测试少量
python scholar_citation.py --author YOUR_AUTHOR_ID --limit 2
python scholar_citation.py --author YOUR_AUTHOR_ID --limit 1 --skip 1
```

### CLI 参数

| 参数 | 说明 |
|------|------|
| `--author` (必填) | Scholar author ID 或完整 URL |
| `--output-dir` | 输出目录，默认 `./output` |
| `--skip M` | 跳过前 M 篇论文（按引用数降序） |
| `--limit N` | 在 skip 之后处理 N 篇 |
| `--fetch-mode` | `rough` / `normal` / `force`（默认 normal） |
| `--interactive-captcha` | 交互式验证码恢复 |
| `--accelerate SCALE` | 等待倍率（默认 1.0，0.1 = 10× 快） |

---

## 运行流程

**跨运行状态原则**：每次运行只从**输出文件**（profile JSON、citations JSON）读取上一轮状态。同次运行内的中断恢复通过内存 (`_mid_paper_state`、`_output_fetch_state`、`_output_citations`) 实现，不再使用磁盘缓存文件。

1. **Profile 阶段**（每次都运行）
   - 阶段1：获取作者基本信息（name, affiliation, citation stats）
   - 阶段2：获取所有论文列表（含 citedby_url）。引用数未变时复用 `prev_profile['publications']`（来自输出文件，不读 `pubs_cache`）
   - 增量比较：与上次 profile 对比，发现新增论文、引用数变化
   - 保存 JSON + Excel + 追加 history
   - 保存 JSON + Excel + 追加 history

2. **智能跳过判断**
   - 比较本次与上次的 `total_citations` 和 `total_publications`
   - 都没变 → 跳过 citation 抓取

3. **Citation 阶段**（有变化时才运行）
   - 逐篇获取引用来源列表
   - 增量缓存 + 中断续传
   - 保存 JSON + Excel

---

## 等待与超时机制

### 统一等待策略

所有主动等待统一使用 45-90 秒随机值（`rand_delay()`）：
- Profile 阶段：搜索作者后、两阶段之间
- Citation 阶段：翻页时、年份段切换时

每次主动等待都会输出等待信息。论文间没有额外延时（每页请求前已有足够延时）。

### 心跳超时机制

- **心跳间隔**：10 秒检查一次（`HEARTBEAT_INTERVAL = 10`）
- **超时阈值**：80 秒无响应（`HEARTBEAT_TIMEOUT = 80`）
- **超时处理**：保存进度后 `os._exit(1)` 强制终止

### 主动等待与超时计时的关系

关键设计：主动等待不计入超时计时。
- 进入主动等待前设 `_deliberately_waiting = True`，心跳线程跳过检查
- 主动等待结束后设 `_deliberately_waiting = False`，**同时重置 `_last_activity = time.time()`**

---

## scholarly 限流对策

### Monkey-patch（运行时替换，不修改 scholarly 源码）

1. **翻页随机延迟**：替换 `_SearchScholarIterator._load_url`，每次翻页前等待 45-90 秒
2. **年份切换随机延迟**：替换 `scholarly._citedby_long`，按年份分段时加延迟
3. **Session 刷新**：随机 10-20 页刷新 scholarly 内部 httpx session

### citedby_url 前缀修正

scholarly 的 `_get_soup` 会在 URL 前拼接 `https://scholar.google.com`，但缓存中的 `citedby_url` 已是完整 URL，需要先剥掉前缀。

### scholarly pub_obj 必需字段

```python
pub_obj = {
    'citedby_url': citedby_url,
    'container_type': 'Publication',
    'num_citations': num_citations,
    'filled': True,
    'source': 'PUBLICATION_SEARCH_SNIPPET',
    'bib': {'title': title, 'pub_year': year},
}
```
- `num_citations <= 1000`：走 `PublicationParser.citedby()` → 只用 `citedby_url` + `filled`
- `num_citations > 1000`：走 `_citedby_long()` → 按年份分批抓取

---

## 运行状态管理

### 三层内存结构

所有中间状态通过三个内存结构管理，不使用磁盘缓存文件：

| 结构 | 类型 | 内容 | 生命周期 |
|------|------|------|---------|
| `_output_fetch_state` | `{title: PaperFetchState}` | 每篇论文的 diagnostics（`seen_total`、`year_records` 等） | 从输出 JSON 加载，运行中逐篇更新，结束时写回 JSON |
| `_output_citations` | `{title: [citation]}` | 每篇论文的引用列表 | 从输出 JSON 加载，fetch 完成后更新 |
| `_mid_paper_state` | `{title: cache_dict}` | 当前论文的中间进度（`save_progress` 写入） | 每页更新，retry 时读取，运行结束时清空 |

### 命名约定

- **`strategy`** = `year` / `direct`（`fetch_policy['strategy']`、`fetch_strategy`、`PaperFetchState.fetch_strategy`）
- **`mode`** = `rough` / `normal` / `force`（`--fetch-mode` CLI 参数、`self.fetch_mode`）
- `direct_resume_state` 中的 `'mode': 'direct'` 已移除，改用 `ResumeState` 对象

### 核心 dataclass 一览

| 类 | 位置 | 用途 | 字段数 |
|-----|------|------|--------|
| `AuthorProfile` | `profile_io.py` | 输出文件 author profile | 4 + 3 计算属性 |
| `PaperFetchState` | `output_state.py` | 输出文件 `_fetch_state` | 11 |
| `PubInfo` | `pub_info.py` | 输出文件 `pub` | 8 |
| `YearRecord` | `citation_models.py` | 单年抓取记录（I/O） | 6 |
| `ResumeState` | `citation_models.py` | 断点续传位置（运行时） | 3 |
| `FetchPolicy` | `citation_models.py` | 抓取策略决策（运行时） | 3 |

### 输出文件 `_fetch_state` 字段（11 个）

```json
{
  "title": "...",
  "pub_url": "...",
  "citedby_url": "...",
  "fetch_strategy": "year" | "direct",
  "num_citations_on_scholar": 1200,
  "scholar_changed": false,
  "complete_fetch_attempt": true,
  "year_fetch_diagnostics": {
    "histogram_total": 1200, "scholar_total": 1210,
    "cached_total": 1195, "cached_year_total": 1195,
    "seen_total": 1205, "cached_unyeared_count": 0,
    "dedup_count": 1, "scholar_unyeared_count": 10
  },
  "direct_fetch_diagnostics": {
    "summary": {
      "scholar_total": 46, "cached_total": 46,
      "seen_total": 46, "dedup_count": 0,
      "termination_reason": "iterator_exhausted"
    }
  },
  "year_records": [
    {"year": 2024, "histogram_count": 50, "cached_total": 49,
     "seen_total": 50, "dedup_count": 0, "termination_reason": "short_page_stop"},
    {"year": 2025, ...}
  ],
  "fetched_at": "2026-05-07T12:00:00"
}
```

**关键设计**：`year_fetch_diagnostics` 是纯 summary（8 字段），per-year 条目在 `year_records`（独立顶层列表）。`direct_fetch_diagnostics` 只有 `summary`（5 字段）。`direct_summary` 不含 `histogram_total` 等 year 字段。

### 不在 output 中的字段

`num_citations_seen`、`cached_year_counts`、`dedup_count`、`complete` — 均可从 diagnostics 推导。

### 数据流：year_records → diagnostics

```
fetch 过程:
  year mode: per-year fetch → YearRecord (histogram_count 来自 probe)
  direct mode: 引用抓取 → YearRecord (histogram_count=0, 无 probe)

save_progress:
  YearRecord 列表 → build_citation_count_summary → year_fetch_diagnostics (summary)
  direct fetch → _build_direct_fetch_diagnostics → direct_fetch_diagnostics

输出:
  PaperFetchState.to_dict() → 规范化所有字段 → 写入 output JSON
```

### normalize 调用链

`from_dict` 入口 → `_normalize_year_summary_dict`（year summary）→ `_normalize_direct_diagnostics`（direct summary）→ `_normalize_year_records`（per-year 列表）。`to_dict` 出口同样调用这些函数，入出两端规范化。

### partial_year_start 的语义

- **只在同一次运行内有效**，不写入缓存文件
- 当某一年处理到一半被中断，记录已处理位置
- 必须是**页面对齐的**（10 的倍数）
- 跨运行时永远为空 `{}`

### PaperFetchState 封装原则

`PaperFetchState` 所有字段以 `_` 前缀实现为私有字段，外部代码**禁止**直接赋值。访问和修改只能通过以下途径：

- **初始化**: `from_dict()` 类方法
- **更新**: `restore_from_cache_snapshot()` / `restore_year_diag_from_year_records()` / `restore_direct_diag_from_citations()`
- **标志位**: `mark_scholar_changed()` / `clear_scholar_changed()`
- **查询**: `need_fetch()` / `is_complete()` / 只读 property

`restore_from_cache_snapshot(cache_snapshot)` 是统一入口，封装了所有运行时状态更新：
`_year_records`, `_year_fetch_diagnostics`, `_direct_fetch_diagnostics`, `_complete_fetch_attempt`,
`_fetch_strategy`, `_num_citations_on_scholar`, `_fetched_at`（共 7 个字段）。

所有 restore 方法返回 `self` 以支持链式调用。

---

## Citation 状态判定

### complete/partial 规则

- **Year mode**: `year_fetch_diagnostics.histogram_total <= seen_total` → complete（`year_fetch_diagnostics` 自身就是 summary）
- **Direct mode**: `direct_fetch_diagnostics.scholar_total <= seen_total` → complete
- 无 diagnostics → `PaperFetchState.is_complete()` 已封装，内部 fallback

### Fetch policy 选择

- 引用数 < 50 → `FetchPolicy(strategy='direct')`
- 引用数 >= 50 → `FetchPolicy(strategy='year')`
- `FetchPolicy.is_year()` / `is_direct()` 辅助方法；`__getitem__`/`get()` 兼容 dict 访问

### 断点续传

- 统一用 `ResumeState`（`next_index`, `source_scholar_total`, `citedby_url`）
- `page_start()` 返回页对齐位置，`in_page_skip()` 返回页内偏移
- Direct 模式：单个 `ResumeState` → 内存 `_mid_paper_state`
- Year 模式：`completed_year_segments`（已完成年份集）+ `partial_year_start`（`{year: int}`，年份内断点位置）

---

## Excel 工作表说明

### Profile Excel
| 工作表 | 内容 |
|--------|------|
| Author Overview | 基本信息、引用统计、历年引用趋势 |
| Publications | 所有论文（按引用数降序），含可点击链接 |
| Change History | 每次运行的快照记录 |

### Citations Excel
| 工作表 | 内容 |
|--------|------|
| Summary | 论文概览，含 Scholar 引用数 vs 已采集引用数 |
| All Citations | 所有引用来源的扁平列表 |
| Run Metadata | 运行级元数据（author_id, fetch_time, totals） |

---

## scholarly 内部机制

### 源码位置
`<python-env>/lib/python3.11/site-packages/scholarly/`

### citedby() 的两条路径

| 条件 | 路径 | 实现 |
|------|------|------|
| `num_citations <= 1000` | 简单路径 | `PublicationParser.citedby()` → `_SearchScholarIterator` 直接翻页 |
| `num_citations > 1000` | 长路径 | `_citedby_long()` → 按年份分段，每段用 `search_citedby(id, year_low, year_high)` |

### _SearchScholarIterator 翻页

- 每页 10 条结果
- `__next__()` 检查是否有 `gs_ico_nav_next` 按钮决定是否翻页
- `_load_url()` 调用 `_nav._get_soup(url)` → `_get_page()` 发起 HTTP 请求

### _citedby_long 按年份分段策略

- 从 `citedby_url` 中提取 publication ID（`cites=[\d+,]*`）
- `source == AUTHOR_PUBLICATION_ENTRY` 时：先 `fill()` 获取 `cites_per_year`，用 `_bin_citations_by_year()` 分组
- `source == PUBLICATION_SEARCH_SNIPPET` 时：逐年遍历（当前年 → 最早年）
- 单年仍超 1000 时只能拿到 1000 条（Scholar 硬限制）

### 403 / 限流处理（_navigator._get_page）

- **每次请求前**：`random.uniform(1, 2)` 等 1-2 秒
- **首次 403**：立即重试，新 session
- **后续 403**：`random.uniform(60, 120)` 等 60-120 秒
- **最多重试**：`_max_retries` 次（默认 5）
- scholarly 的 60-120 秒等待会超过心跳 80 秒超时 → 已通过 monkey-patch 解决

### `as_sdt` 参数说明

`as_sdt` 格式：`as_sdt=<类型>,<地区码>`
- 类型：`0`=排除专利，`7`=包含专利，`2005`=citation 搜索专用内部标志
- 地区码：`5`=全球默认，`33`=特定地区（会过滤部分结果）

当前方案：使用 `as_sdt=0,5`（排除专利 + 全球地区），配合 `sciodt=0,5`。

### 续传时 >1000 引用的问题

- `_citedby_long` 无法用 `&start=N` 跳页（按年份重新查询）
- 续传时只能逐条 skip 已有的引用，需要重新翻过所有已缓存的页面
- 大量重复请求极易触发 Scholar 限流

---

## `_scholar_pub` 不设置 `cites_id`

scholarly 的 `_scholar_pub()` 方法（用于 _SearchScholarIterator 解析 citedby 结果）从不设置 `cites_id` 字段，只设置 `citedby_url`（仅在引用有 "Cited by N" 链接时）。解决方案：三级 fallback — `pub['cites_id']` → `pub['citedby_url']` 解析 `cites=` → `pub['url_scholarbib']` 解析 `cid`。

---

## 关键 Bug 记录

### year_fetch_diagnostics dedup_count 跨次运行丢失（2026-04-17）

内外两个 FetchContext 不同步：`_fetch_by_year` 创建独立内部 ctx，`save_progress` 闭包读外部 ctx。修复：wrapper 同步内部 ctx → fetcher 属性 → save_progress。

### 分页机制冲突导致每页丢失 1 条（2026-04-30）

scholarly 的自动分页与手动分页冲突，`original_next` 自动加载下一页时丢弃当前页第 1 条。修复：满页时也 break，分页控制权完全交还给 while True 循环。

### cites_id 全为 null（2026-05-01）

`_scholar_pub()` 不设置 `cites_id`。修复：从 `citedby_url` 提取。

### 重试时 output state 覆盖 cache 导致丢失引用（2026-05-06）

`retry_strategy_cached` 优先使用 output state（不含 citations）。修复：缓存文件优先。

---

## 环境说明

- **Conda 环境**：`scholar`
- **关键包**：`scholarly==1.7.11`、`openpyxl==3.1.5`、`httpx==0.27.2`
- **代理**：scholarly 自身代理 API 与 httpx 0.28+ 不兼容，通过系统 `https_proxy` 环境变量生效
