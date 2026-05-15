# Update History

本文档按时间倒序记录每次功能更新、bug 修复和重构的详细内容。

---

## 2026-05-16: PaperState 封装 — 合并 fetch state 与 citations

- 新增 `PaperState` dataclass（`crawler/output_state.py`）：包含 `fetch: PaperFetchState` + `citations: list`
- `_output_fetch_state` + `_output_citations` 两个独立 dict → 单个 `_paper_states: {title: PaperState}`
- `cache_status` 简化为直接读取 `PaperState.fetch` 和 `PaperState.citations`
- 修复 `restore_direct_diag_from_citations` 中对只读 property 的错误赋值

## 2026-05-15: 删除 `restore_from_cache_snapshot`，`cache_status` 直接更新 PaperFetchState

- `restore_from_cache_snapshot` 被删除——其功能完全等同于 `PaperFetchState.from_dict(cache_snapshot)`，存在多余
- `cache_status` 现在直接在 `PaperFetchState` 对象上更新 `num_citations_on_scholar`、`complete_fetch_attempt` 和 diagnostics，不再只修补临时的 synthetic dict
- 设计原则：`_output_fetch_state` 中的 `PaperFetchState` 是唯一权威对象，dict 仅用于接口兼容

## 2026-05-15: 修复 `restore_from_cache_snapshot` 遗漏字段（已被后续重构替换）

- `restore_from_cache_snapshot()` 漏掉了 `complete_fetch_attempt`、`fetch_strategy`、`num_citations_on_scholar` 三个字段
- 导致 fetch 成功后输出 JSON 中 `complete_fetch_attempt` 始终为 false，`fetch_strategy` 不随阈值变化更新
- 修复：从 `cache_snapshot` 中同步这三个字段

---

## 2026-05-14: 代码规范清理 — 删除死代码、消除冗余、修复 Bug、更新文档

- **死代码删除**：`DirectFetchSession`、`Citation`、`YearDiagnostics`、`DirectDiagnostics`、`extract_fetch_state`、`build_profile_payload`/`save_profile_json`、`citation_status`、`refresh_reconciliation_status`、`_resort_publications`、`_effective_scholar_total` 的未使用参数
- **冗余消除**：合并 `_direct_fetch_diagnostics` 包装器、`_direct_fetch_*_message` 函数；`year_records` 解析逻辑提取为 `index_year_records()`
- **Bug 修复**：`year_fetched_citations` → `year_batch.citations`（未定义变量 NameError）
- **封装性改进**：`restore_from_cache_snapshot()` 封装私有字段写入；`to_paper_fetch_state()` 统一类型分派
- **参数精简**：删除 `rehydrated_probed_year_counts`/`rehydrated_probe_complete`/`rehydrated_year_fetch_diagnostics`（始终为 None/False/None）
- **文档修正**：测试数 127→119、删除 test_citation_page_stop.py 引用、PaperFetchState 字段数 10→11、依赖映射补全、版本约束修正
- 测试从 126 精简到 119（删除冗余/死代码相关测试）

---

## 2026-05-12: 代码规范 — 消除 4 处冗余代码 + 1 个 Bug 修复

### Issue 1: 删除 `_direct_fetch_diagnostics` 冗余包装器
- 删除 `citation_fetch.py` 中的 `_direct_fetch_diagnostics()`（只是 `_build_direct_fetch_diagnostics()` 的平凡包装器）
- 删除 `scholar_citation.py` 中对应包装器方法
- `test_citation_status.py` 中相关测试改为直接调用 `_build_direct_fetch_diagnostics`

### Issue 2: 合并 `_direct_fetch_summary_message` 和 `_direct_fetch_log_message`
- 两个函数体完全相同，仅前缀字符串不同
- 合并为 `_direct_fetch_diagnostics_message(diagnostics, prefix=...)`
- `citation_fetch.py` 和 `scholar_citation.py` 中两处调用点改为使用新函数并传入不同 prefix

### Issue 3: 提取 year_records 解析逻辑为共享函数
- `scholar_citation.py` 中相同的 `for rec in yr` 解析模式出现了 3 次
- 提取为 `index_year_records()` 放入 `crawler/output_state.py`
- `scholar_citation.py` 导入为 `_os_index_year_records`，3 处调用点均已替换

### Issue 4: 修复 `year_fetched_citations` 未定义变量 Bug
- `crawler/citation_fetch.py:875,879` 中使用 `year_fetched_citations`，但该变量从未在 `fetch_by_year` 中赋值
- 当 `stop_partial_resume_once_satisfied` 和 `resuming_partial_year` 都为 True 时会引发 NameError
- 替换为 `year_batch.citations`（同一函数体内已在使用的正确变量）

---

## 2026-05-12: 封装性修复 — PaperFetchState 私有字段不再被外部直接赋值

### 问题
`scholar_citation.py` 在 fetch 完成后有 4 处直接赋值 `PaperFetchState` 的私有字段：
- `pst._year_records = yr`
- `pst._direct_fetch_diagnostics = dfd`
- `pst._year_fetch_diagnostics = yfd`
- `pst._fetched_at = cache_snapshot.get('fetched_at') or datetime.now().isoformat()`

这违反了封装原则。

### 修复
- 在 `crawler/output_state.py` 的 `PaperFetchState` 类中添加 `restore_from_cache_snapshot(cache_snapshot)` 方法，将所有私有字段更新集中在一个方法内
- 修改 `scholar_citation.py` 中对应的代码，使用 `pst.restore_from_cache_snapshot(cache_snapshot)` 替换所有 4 处直接赋值
- 新方法遵循 chainable 惯例（返回 self）

### 影响文件
- `crawler/output_state.py`: 添加 `restore_from_cache_snapshot()` 方法
- `scholar_citation.py`: 替换直接私有字段赋值为方法调用

---
## 2026-05-13: 大规模重构 — 删除 cache 文件、rehydrate 函数，强化 PaperFetchState 封装

### 删除 cache 文件体系

- `save_progress` 不再写磁盘，改为存入 `fetcher._mid_paper_state[title]`（内存 dict）
- `_load_citation_cache` 改为从 `_mid_paper_state` 读取，不再访问文件系统
- 删除：`citation_cache_path()`、`cache_dir`、cache 目录创建和清理、`json.dump` 写入
- 所有中间状态通过 `_mid_paper_state` + `_output_fetch_state` + `_output_citations` 管理

### 删除 rehydrate 函数

- 删除 `rehydrate_year_fetch_diagnostics`：`_resolve_refresh_strategy` 直接从 `year_records` 读取 per-year 列表
- 删除 `rehydrate_probe_metadata`：probe 永远在 `fetch_by_year` 中新鲜执行，不再读缓存
- `_resolve_refresh_strategy` 优先从 `PaperFetchState` 属性读取（`year_records`、`direct_fetch_diagnostics`、`num_citations_on_scholar` 等），dict 仅作为 fallback

### Paper Done 日志增强

- 显示 fetch target 比较：`year: seen_total=N ≥ histogram_total=M` 或 `direct: seen_total=N ≥ scholar_total=M`
- 加入 per-paper new 计数

### 其他修复

- `year_new_count` 改为年份桶增量（比较 `old_year_identity_keys` 而非全局 `old_cache_identity_keys`）
- Year fetch 日志显示 `seen`（判断依据）而非 `cached`（不含 dedup）
- `_new_citations_count` 每篇论文重置，避免跨论文累加
- Fetch 完成后将 diagnostics 从 `_mid_paper_state` 同步到 `_output_fetch_state`

---

## 2026-05-12: 日志一致性修正 — 判断依据与打印值统一

- Year `fetching` / `resuming` 日志之前显示 `cached=320`（不含 dedup），但跳过判断使用的是 `seen_total=321`（含 dedup）→ 改为显示 `seen`，与判断逻辑一致
- Paper `Done` 日志加入 `N new this paper`，与 progress 行和 year-done 行形成完整的 per-year → per-paper → per-run 三级新引用计数
- `_format_completeness_diag` 优先使用 PaperFetchState，避免重新从 dict 构建

---

## 2026-05-12: 修复 year 跳过判断失效 — 从 year_records 直接读取 per-year 数据

- 输出文件中 `year_fetch_diagnostics` 是 summary（8 个汇总字段），`year_records` 才是 per-year 条目
- `_resolve_refresh_strategy` 原本调用 `rehydrate_year_fetch_diagnostics(cached)` 从 dict 中提取 per-year 数据，但函数读取了 summary 字段，格式错配返回 None
- skip 逻辑回退到 `cached_year_counts`（丢失 dedup_count），导致 `seen_total >= histogram` 为 True 的年份无法跳过
- 重构：`_resolve_refresh_strategy` 直接从 `cached.get('year_records')`（由 `PaperFetchState.to_dict()` 提供）读取 per-year 列表，转换为 `{year: diag}`，不再绕道 `rehydrate_year_fetch_diagnostics`
- `rehydrate_year_fetch_diagnostics` 保留为 fallback（优先读 `year_records`），供旧格式数据和 retry 路径使用

---

## 2026-05-12: 修复 year_new_count 统计为年份桶增量（而非跨年去重）

- `year_new_count` 之前用 `old_cache_identity_keys`（全局旧缓存）做 `is_new` 判断
- 当某引用从旧缓存的 2024 年"移动"到 2026 年时，`is_new=False`，被错误地排除
- 用户看到 `cached=20, probe=23` 期望 3 个 new，但实际只计入 1 个（跨年去重过滤了 2 个）
- 修复：新增 `old_year_identity_keys`（仅包含该年份旧桶中引用的 identity key），`year_new_count` 计数时使用该年份范围的 key 集合 — `is_new=True`（全局新）或 citation 不在 `old_year_identity_keys` 中（年份桶新）均计入
- `fetcher._new_citations_count` 保留跨年去重语义（真正的新引用），进度行同时显示两种计数
- 同步修复：`_new_citations_count` 在每个 paper 前重置，避免跨 paper 累加

---

## 2026-05-12: 修复 new citations 跨 paper 累加导致进度日志计数偏差

此问题仅为症状。真正的根因是 `year_new_count` 使用了错误的比较范围（全局旧缓存 vs 年份桶），详见上方条目。

旧记录保留如下：

- `_new_citations_count` 之前在整个 run 中只重置一次，导致第 N 篇 paper 的进度日志 `N new across run` 包含了前面 papers 的 new 总数
- 而 `year_new_count` 是每个 paper 每个 year 独立计数 → 两者数值不一致
- 修复：每个 paper 处理前重置 `_new_citations_count`，新增 `_run_new_citations_total` 记录 run 级别总计
- 进度日志文本 `new across run` → `new this paper` 以消除歧义

---

## 2026-05-12: CLAUDE.md 去重 — 精简项目结构为引用

- CLAUDE.md 中冗长的树形项目结构（55 行）替换为引用 `_work_notes.zh.md` 和 `README.md`
- 保留 Architecture overview、dependency map、testing notes 等 CLAUDE.md 独有内容
- 修正 Architecture overview 中过时引用：`FetchContext` → `PaperFetchState`/`YearFetchSession`、`--recheck-citations` → `--fetch-mode`、`publications.json` 缓存 → profile JSON
- 关联：修正前次遗漏的 stale 模块引用和测试计数（fetch_context.py、121/107 → 127）

---

## 2026-05-12: 文档修正 — 过期模块引用和测试计数

- CLAUDE.md、`_work_notes.zh.md` 中 `fetch_context.py` → `fetch_session.py`（FetchContext 已删除）
- 各文档项目结构中补充缺失模块：`fetch_session.py`、`page_visit.py`、`citation_models.py`、`output_state.py`、`pub_info.py`
- 测试计数统一为 127（之前 CLAUDE.md/README 为 107，`_work_notes.zh.md` 为 121）
- CLAUDE.md 移除过时的 `refactor/modularize` 分支引用，更新依赖映射

---

## 2026-05-09: 状态封装 + cache 清除 + scholar_changed 标志

### 字段私有化

- `PaperFetchState` 10 个字段 → `_field` 前缀，只读 property，通过 `from_dict()`/`restore_*()` 修改
- `BatchFetchSession` 8 个字段 → `_field` 前缀，只读 property，内部 `_run_once()` 修改

### 彻底移除 cache 依赖

- `_build_entry` 不再从 cache 合并 diagnostics
- 每次运行结束时 `shutil.rmtree(cache_dir)` 删除所有缓存
- 同次运行 retry 仍读取 cache（`save_progress` 写入的进度）

### scholar_changed 标志

- `PaperFetchState._scholar_changed`：引用总数变化时在 `cache_status` 中设置
- `need_fetch()`：`scholar_changed` 为 True 或 `is_complete()` 为 False 时返回 True
- 抓取成功后 `clear_scholar_changed()`，中断则保留到下次运行

### fix_diag_from_year_record.py

- 使用 `PaperFetchState.from_dict()` → `restore_*()` → `to_dict()` round-trip 修复输出文件
- Year 模式从 `year_records` 累加重建 `year_fetch_diagnostics`
- Direct 模式从 citations 计数重建 `direct_fetch_diagnostics`

### 诊断格式统一

- `year_fetch_diagnostics` 和 `direct_fetch_diagnostics` 本身就是 summary，不再有 `summary` 子键
- `_normalize_year_summary_dict` 兼容三种格式（新扁平格式 + 旧嵌套 summary + records 推导）

---

## 2026-05-09: 彻底移除 cache 文件回退读取

### _citation_status 不再读取 cache 文件

`_citation_status` 和 `cache_status` 在输出文件状态缺失时直接返回 `'missing'`，不再回退读取 per-paper cache 文件。cache 文件仅用于同次运行内的中断恢复。

### force mode 简化

只需清除输出文件的 `_fetch_state` 即可使论文被重新抓取，无需删除 cache 文件。

### 兼容性修复

- `_normalize_direct_diagnostics` 处理旧格式（无 `summary` 子键的扁平 diagnostics），同时识别旧字段名 `reported_total` / `yielded_total`
- `PaperFetchState` 新增 `num_citations_seen` 字段用于旧 cache 的向后兼容
- `is_complete` 推断策略时优先检查已有的 diagnostics，而非仅依赖引用数

---

## 2026-05-08: YearFetchSession 替换 FetchContext + force mode 修复

### YearFetchSession 替换 FetchContext

`crawler/fetch_session.py` 中的 `YearFetchSession` 合并了 `FetchContext` 的全部字段：
- `completed_year_segments`、`partial_year_start`、`dedup_count`
- `probed_year_counts`、`probed_year_count_complete`、`cached_year_counts`、`year_fetch_diagnostics`
- 新增 `baseline: PaperFetchState`（跨运行状态引用）
- `FetchContext` 文件已删除

### 恢复页面访问前随机等待

`patched_get_page` 中 `PageVisit.fetch()` 调用前增加 `rand_delay()`，恢复 45-90s 随机等待。

### force mode 语义变更

`--fetch-mode force` 不再删除 per-paper cache 文件，改为从 output file 的 `_fetch_state` 中清除对应论文的状态。

---

## 2026-05-08: PageVisit + BatchFetchSession 页面访问层重构

### PageVisit — 统一页面访问错误恢复

新增 `crawler/page_visit.py`：

- **`PageVisit(ctx)`**：封装单次 Scholar 页面访问
  - `fetch(fn, url, label)` — 调用 `fn()` 获取页面，自动处理所有错误
  - 三层恢复：captcha 解决 → 自动重试 → proxy 切换
  - KeyboardInterrupt 始终立即传播

- 集成到 `patched_get_page`：所有 scholarly HTTP 请求均通过 `PageVisit`，替换了之前的 `unified_sleep` hack
- 移除 `scholarly_session.py` 中不再需要的 `MaxTriesExceededException` import

### 错误处理层级

```
_run_main_loop        ← 论文级：MAX_RETRIES + 最终 proxy switch 兜底
  └─ YearFetchSession  ← 年份级：年份顺序 + 续传
       └─ BatchFetchSession ← 翻页级：分页迭代 + max_retries
            └─ PageVisit     ← 页面级：captcha / proxy switch / 重试
```

---

## 2026-05-08: BatchFetchSession 引入 + Direct fetch 迁移

### 新增 `crawler/fetch_session.py`

三个 dataclass：

- **`BatchFetchSession`**：封装单次分页抓取。给定 URL，`run(fetcher, ...)` 自动翻页、提取 citation、dedup、track 翻页位置。支持 `start_index` 续传和 `iterator` 注入（供测试 mock）
- **`DirectFetchSession`**：包装一个 `BatchFetchSession` + `PaperFetchState` baseline
- **`YearFetchSession`**：管理年份列表 + per-year `BatchFetchSession`，只需维护 `pending_years` + `current_batch`

### Direct fetch 迁移

`fetch_citations_with_progress` 中 direct 路径已改用 `BatchFetchSession`：
- 创建 `BatchFetchSession(url=...)`
- 通过 `fetcher._iter_direct_citedby()` 获取 iterator（保留测试 mock 路径）
- `batch.run()` 处理翻页 / dedup / 进度回调
- `save_progress` 接受 `batch` 参数，从 batch 同步 citations 和 dedup

---

## 2026-05-08: save_progress 参数清理 + 统一 is_data_complete

### save_progress 变量整理

- 参数 `complete` → `fetch_finished`（抓取循环是否结束，不等同于数据完整）
- 移除 `effective_complete` / `materialize_replace` 中间变量：`materialized_citations(fetch_finished)` 直接决策新旧引用替换/合并
- 移除 `data_complete` 中间变量：改为调用 `_compute_data_complete()` 函数
- 移除 `cache_data['complete']` key（与 `complete_fetch_attempt` 冗余）

### 统一 is_data_complete

`crawler/citation_cache.py` 新增 `is_data_complete(strategy, summary)`：
- Year 模式: `seen_total >= histogram_total`
- Direct 模式: `seen_total >= scholar_total`
- `PaperFetchState.is_complete()` 和 `_compute_data_complete` 均委托此函数

### 命名修正

- `build_materialized_year_fetch_diagnostics` → `build_year_records`（函数实际构建的是 per-year 记录）
- `year_fetch_diagnostics_to_save` → `year_records_to_save`
- `_synced_save_progress(complete)` → `_synced_save_progress(fetch_finished)`

### _build_entry 修复

`fetch_complete` 改为使用 `PaperFetchState.is_complete()`（数据完整性），而非 `complete_fetch_attempt`（过程是否跑完）。

---

## 2026-05-08: 修复 profile 中 author/url 为 N/A

### 根因

Scholarly 的 `_citation_pub()`（用于解析作者页论文列表）不提取 `bib['author']` 和 `pub_url`。它只读取 `gs_gray[1]`（venue），丢弃了 `gs_gray[0]`（authors）；也从标题链接提取 `author_pub_id` 但不保存 href 为 `pub_url`。这些字段只在后续对每篇论文单独调用 `fill()` 时才会被设置，但 `scholarly.fill(author, sections=['publications'])` 并不会对每篇论文调用 `fill()`。

### 修复：monkey-patch `_citation_pub`

在 `crawler/scholarly_session.py` 的 `patch_scholarly()` 中新增 monkey-patch：

- 从 `gs_gray[0]` 提取作者名字符串，写入 `bib['author']`
- 从标题链接 `gsc_a_at` 的 `href` 提取 `pub_url`，补全为绝对 URL（`https://scholar.google.com/...`）

此 patch 不需要额外 HTTP 请求——数据在原有 HTML 解析中已存在，只是被 scholarly 丢弃了。

---

## 2026-05-08: AuthorProfile dataclass + 移除只写缓存

### AuthorProfile dataclass 封装

`crawler/profile_io.py` 新增 `AuthorProfile` dataclass，封装 profile 阶段的所有数据：

- **字段**：`author_info`、`publications`、`fetch_time`、`change_history`
- **计算属性**：`total_publications`、`total_citations`、`citation_count_summary`
- **方法**：`from_dict()` / `to_dict()`（序列化）、`load(path)` / `save_json(path)`（文件 I/O）、`append_history(prev)`（变更追踪）
- Profile JSON 格式不变，`from_dict` 通过 `PubInfo.from_dict().to_dict()` 规范化旧数据

### 移除只写缓存文件

- `basics_cache`（`scholar_cache/.../basics.json`）：只写不读（basics 每次从网络拉取），移除 `save_basics_cache()` / `load_basics_cache()` 方法及文件路径
- `pubs_cache`（`scholar_cache/.../publications.json`）：前次提交已移除读取，本次移除 `save_pubs_cache()` / `load_pubs_cache()` 方法及文件路径
- `AuthorProfileFetcher` 不再创建 `cache_dir`

### profile_io 接口简化

- `save_profile_xlsx()` 接受 `AuthorProfile` 实例替代 5 个独立参数
- `build_profile_payload()`、`save_profile_json()` 逻辑移入 `AuthorProfile`；旧函数保留以兼容 `scholar_citation.py` 的 import
- `scholar_citation.py` 中移除未使用的 profile_io import

---

## 2026-05-08: 移除 --force-refresh-pubs + pubs_cache 依赖清理 + profile Excel 增强

### 移除 --force-refresh-pubs 参数

与 `--fetch-mode force` 功能重叠。`author_fetcher.py` 中已有自动检测引用总数变化并强制刷新 publications 的逻辑，不需要单独的手动参数。涉及 5 个文件。

### 输出文件作为唯一跨运行状态源

Profile 和 citation 阶段统一：跨运行状态只从输出文件（profile JSON / citations JSON）读取，不再依赖中间 cache 文件。

- **`fetch_publications()`**：不再读取 `pubs_cache`，改为接受 `prev_publications` 参数，从 `prev_profile`（来自 profile JSON）获取上一轮的 publications 列表
- **Citation 阶段 `url_map`**：从 profile JSON 的 `publications` 构建，不再读取 `pubs_cache`
- **移除死代码**：`scholar_citation.py` 中 `self.pubs_cache` 属性、`self._pubs_data` 属性、`_save_output` 中的 `pubs_cache` 回退读取

### PubInfo 健壮性增强

- `from_scholarly()` 处理 `bib['author']` 为 list 的情况（scholarly 的 `_citation_pub()` 不设置 author，`_scholar_pub()` 中 `_get_authorlist` 返回 list；只有后续 `fill()` 或 bibtex update 才转为 string）。list 以 `'; '.join()` 转为 string
- venue 增加 `bib.get('journal')` fallback

### Profile Excel 增强

- Publications 表新增 **Authors** 列（E 列），Link 移至 G 列
- 空值显示修复：`pub.get('url') or 'N/A'` 替代 `pub.get('url', 'N/A')`，对 `PubInfo` 产生的空字符串正确显示为 'N/A'。Year、Venue、Authors 同理

---

## 2026-05-07: 命名统一 + 运行时状态封装

### 命名标准化

- `strategy` = `year` / `direct`（`fetch_policy['strategy']`、`fetch_strategy`）
- `mode` = `rough` / `normal` / `force`（`self.fetch_mode`、`--fetch-mode`）
- `fetch_policy['mode']` → `fetch_policy['strategy']`（9 个文件，44 处修改）

### ResumeState 运行时 dataclass

`crawler/citation_models.py` 新增：
- **`ResumeState`**：统一 direct 和 year 模式的断点续传位置（`next_index`, `source_scholar_total`, `citedby_url`），提供 `page_start()` / `in_page_skip()` / `request_url()` / `is_valid()` + `from_dict`/`to_dict`
- **`FetchPolicy`**：替代 `resolve_citation_fetch_policy` 返回的 dict，`strategy` / `pub_year` / `reason` + dict-compat

`citation_fetch.py` 中 5 个 resume 函数改为委托 `ResumeState`（消除重复 dict 构造/校验逻辑，22 行新增 65 行删除）；`citation_strategy.py` 返回 `FetchPolicy` 对象。

### 控制流改为 mode 驱动

`fetch_by_year` 中的 `ctx.probed_year_count_complete`、`bool(probed_year_counts)` 等值存在性检查改为 `is_year` mode 标志驱动。

### Direct mode histogram=0

Direct mode 无 probe，year_records 中 `histogram_count=0`，summary 中 `histogram_total=0`。

---

## 2026-05-07: year_records 独立 + citation_models + PubInfo

### year_records 从 year_fetch_diagnostics 分离

`year_fetch_diagnostics` 现在只包含 8-field summary，per-year 条目移至新顶层字段 `year_records`（按年份排序的列表）。`_FETCH_STATE_KEYS` 新增 `year_records`，共 10 个字段。

### PubInfo dataclass

`crawler/pub_info.py`：封装 `pub` 的 8 个字段（`no`, `title`, `year`, `venue`, `authors`, `num_citations`, `url`, `citedby_url`）。`from_scholarly()` 替代 `bib.get('xxx', 'N/A')` 默认值。

### citation_models.py

`crawler/citation_models.py`：`Citation`、`YearRecord`、`YearDiagnostics`、`DirectDiagnostics` 四个 dataclass，各带 `from_dict`/`to_dict` 和 dict-compat 方法。当前在 I/O 边界使用，内部逐步迁移。

### 移除 profile 阶段间延时 + 论文间延时

Profile Phase 1→2 延时和论文间延时已移除（每页访问前已有 45-90s 等待）。

---

## 2026-05-07: PaperFetchState dataclass 重构

引入 `PaperFetchState` dataclass（`crawler/output_state.py`），封装 `_fetch_state` 的持久化字段，替代裸 dict。

**新增方法**：
- `from_dict()` / `to_dict()` — 序列化，入出两端均规范化 diagnostics 字段
- `is_complete()` — year/direct 统一 completeness 判断
- `completeness_diag()` — 替代 `_format_completeness_diag` 逻辑

**规范化**：
- `direct_fetch_diagnostics.summary` 严格限制为 5 字段
- `year_fetch_diagnostics` 按年份排序，per-year 条目限制为规范 keys，剔除 `underfetched`/`mode`/`underfetch_gap`

**多态兼容**：`_citation_status()`、`cache_status()`、retry logic 同时接受 PaperFetchState 和 dict。

**修复**：`_build_entry` 合并 cache 时补充 `fetched_at`、`complete_fetch_attempt` 字段（之前漏合并导致输出文件保留旧值）。

---

## 2026-05-07: 修复 year 模式 seen_total 错误包含 unyeared

**Bug**：`build_citation_count_summary` 中 year 模式的 `seen_total = diag_seen + cached_unyeared_count`，导致 year summary 的 `seen_total` 多计了 unyeared 引用。

**根因**：Year fetch 中 unyeared 引用在 `_resolve_refresh_strategy` 阶段被故意丢弃（`drop_cached_unyeared`），因为它们无法归入年份桶参与 histogram 对比。因此 `seen_total` 应该只反映有年份的引用（`= diag_seen`，各年份 seen 的累加值），不应该加回 unyeared。

**修复**：year 模式 `seen_total = diag_seen`；direct 模式 `seen_total = cached_total + dedup_count`（不丢弃 unyeared）。

---

## 2026-05-07: 修复 direct_fetch_diagnostics.summary 被 year 字段污染

`fix_output_fetch_state.py` 的 direct summary 同步只更新个别字段，不清理旧 buggy 运行残留的 year 模式字段（`histogram_total`、`cached_year_total`、`cached_unyeared_count`、`scholar_unyeared_count`），且 `seen_total` 只在 `None` 时才修正。

修复：direct summary 完全重建为 5 字段（`scholar_total`、`cached_total`、`seen_total`、`dedup_count`、`termination_reason`），`seen_total` 强制重算为 `cached_total + dedup_count`。

### 中文文档重命名

`update_history.md` → `_update_history.zh.md`、`WORK_NOTES.md` → `_work_notes.zh.md`、`user.md` → `_user.zh.md`。更新了 CLAUDE.md、README.md、approach.md 和 memory 中所有引用。

---

## 2026-05-06: 重试状态修复、冗余字段清理、诊断日志完善

### 重试时缓存状态丢失

重试时 `retry_strategy_cached = latest_output_state if latest_output_state else latest_cache`，当 paper 在 output state 中时直接使用 output state（不含 `citations` 数组），忽略了缓存文件中 `save_progress` 保存的最新引用。

修复：`retry_strategy_cached = latest_cache if latest_cache else latest_output_state`，缓存文件优先。

### direct_resume_state 页对齐

`direct_resume_state.next_index` 保存精确位置（如 7），重试时跳过前 7 个 item，但跳过的 item 可能未在 `old_citations` 中，导致丢失。

修复：`_build_direct_resume_state` 将 `next_index` 对齐到页边界（`_page_aligned_start`），重试从页开头重新抓取，已保存的引用通过 `old_citations` 去重。

### 论文间延时移除

每次页面访问前已有 45-90s 延时，论文之间的额外延时是冗余的。移除 `time.sleep(d)`，只保留状态日志。

### resolve_citation_status_from_state 无 diagnostics 时的 fallback

当 `direct_fetch_diagnostics.summary` 或 `year_fetch_diagnostics.summary` 缺失时，原逻辑直接返回 `partial`，未使用已有的 `current` / `num_seen` 进行判断。

修复：当 diagnostics summary 不可用时，fallback 到 `num_seen >= current`（当前 scholar total）判断 complete/partial。

### fix_output_fetch_state.py 多项修复

- `fetch_strategy` 强制按阈值重新评估（已有 `year` 但引用数 < 50 的论文纠正为 `direct`）
- `direct_fetch_diagnostics.summary` 为 `None` 时触发 repair
- `num_citations_seen` 不再从 `new_summary`（per-year 派生）设置，直接模式从 `direct_fetch_diagnostics.summary.seen_total` 取实际记录值

### Direct fetch 日志缩进统一

Direct fetch 的 item 行从 8 空格改为 10 空格，与 year fetch 一致。

### `num_citations_seen` 和 `cached_year_counts` 从 output 中移除

两个字段均可从 diagnostics summaries 推导，不需要作为顶层字段持久化：

- `num_citations_seen`：直接模式从 `direct_fetch_diagnostics.summary.seen_total`，年份模式从 `year_fetch_diagnostics.summary.seen_total`
- `cached_year_counts`：从 `year_fetch_diagnostics` 每个年份条目的 `cached_total` 累加

修改：`_FETCH_STATE_KEYS` 移除这两个字段；`derive_citation_cache_state` / `_resolve_refresh_strategy` / `_format_completeness_diag` 改为从 diagnostics summary 读取。

### `underfetched`/`underfetch_gap` 清理

这些字段已在日志中临时计算（不持久化），`fix_output_fetch_state.py` 清理了旧数据中残留的字段。

---

## 2026-05-06: 每篇论文抓取判定诊断日志

新增 `_format_completeness_diag(st, cached)`，在每篇论文标题下打印诊断信息：

```
  direct: seen_total=49 ≥ scholar_total=49 → complete
  year: seen_total=1343 ≥ histogram_total=1340 → complete
  direct: seen_total=45 < scholar_total=46 → partial
```

---

## 2026-05-05: Direct/Year summary 分离 + seen_total 使用记录值

### Direct/Year 两种 summary 分离

`save_progress` 中将 `count_summary`（per-year 派生值）直接覆盖到 `direct_fetch_diagnostics.summary`，导致 direct 模式的 summary 包含了 per-year 派生字段。

修复：
- `save_progress`：direct summary 只同步 `scholar_total`、`cached_total`、`seen_total`、`dedup_count` 五个顶层计数器
- `_build_direct_fetch_diagnostics`：新增 `seen_total` 参数，不再内部计算 `cached_total + dedup_count`
- `build_citation_count_summary`：year summary 的 `seen_total` 加上 `cached_unyeared_count`
- `_build_entry`：不再覆盖 summary 中的 `seen_total` 和 `dedup_count`

### seen_total 使用实际记录值

`seen_total` 应该是每次获取过程中记录的值，不应该通过 `cached + dedup` 进行计算。`_build_direct_fetch_diagnostics` 改为接受外部传入的 `seen_total` 参数。

### fix_output_fetch_state.py 多轮增强

- 合成条件从 `not yfd` 改为 `not has_year_entries`，处理只有 `summary` 键的 yfd
- 合成不再要求 `probed_year_counts` 存在
- `fetch_strategy` 基于引用数阈值（50）推断，不因合成 per-year 条目而误判
- 空 `_fetch_state` / `None` 正确初始化
- direct summary 同步时使用顶层计数器

---

## 2026-05-05: 日志优化 + 迁移脚本增强

### 日志去重

删除了三处重复/冗余的日志输出：`Year histogram summary`、`Prior run diagnostics`、`Direction: oldest→newest`。

### `num_citations_seen` 缺失时的 fallback

旧版缓存可能没有 `num_citations_seen` 字段，导致完整性检查失效。修复：从 `year_fetch_diagnostics` 中各年份 `seen_total` 求和来推导。

### captcha URL 显示错误

自动翻页时被阻止的页面 URL 显示为迭代器初始 URL 而非实际被阻止的页面。修复：根据实际位置重新构造 URL。

### `save_output` 中 `_fetch_state` 不更新

`_build_entry` 对已有 output state 的论文直接复制旧的 `year_fetch_diagnostics`，从不更新。修复：从当前运行的 cache 文件合并最新值。

### `citation_count_summary` → `summary` 嵌套

`citation_count_summary` 重命名为 `summary`，嵌套在 `year_fetch_diagnostics` (year mode) 或 `direct_fetch_diagnostics` (direct mode) 下。

### 字段重命名与清理

- `scholar_total` → `histogram_count`（year_fetch_diagnostics 逐年条目）
- `reported_total` → `scholar_total`、`yielded_total` → `cached_total`（direct_fetch_diagnostics）
- `unyeared_count` → `scholar_unyeared_count`
- 移除 `underfetched`/`underfetch_gap`/`completed_years_in_current_run`
- 移除 `probed_year_total`、`probe_complete`
- 新增 `fetch_strategy: "year"` / `"direct"` 顶层标记

---

## 2026-05-05: summary 嵌套 & cites_id fallback 修复

- `citation_count_summary` 重命名为 `summary`，作为 `year_fetch_diagnostics` 或 `direct_fetch_diagnostics` 的嵌套子字段
- **cites_id fallback**: scholarly 的 `_scholar_pub()` 不设置 `cites_id`，导致 73.7% 引用缺 `cites_id`。新增三级回退：`cites_id` → `citedby_url` 解析 → `url_scholarbib` 解析（提取 `cid`）

---

## 2026-05-05: complete/partial 判断简化 & 字段清理

- **complete/partial**: year 模式 `histogram_total <= seen_total` → complete；direct 模式 `scholar_total <= seen_total` → complete
- **年度重新获取**: 仅当本 run 已完成 或 `seen >= histogram_count` 时跳过
- **fetch policy 简化**: 仅用引用数阈值（<50 → direct，≥50 → year）
- **`_fetch_state` 字段清理**: 移除 `num_citations_cached`、`dedup_count`、`complete`、`completed_years`、`probed_year_counts` 等
- **direct→year 过渡**: 从缓存引用合成 year_fetch_diagnostics
- **direct_resume_state**: 仅 cache 文件保留（within-run resume），输出文件不保存

---

## 2026-05-03: 输出状态边界情况修复 + skip/limit 时状态更新

### 彻底修复输出状态读取的边界情况

1. `extract_fetch_state` 不补充缺失字段 → 导致 `actual_cached=0`、`num_seen=None`
2. `promoted_scholar_total` 缺失时回退到 0 → 兜底逻辑永远不触发
3. `load_output_fetch_state` 缺少 `AttributeError` 捕获

### skip/limit 时 _fetch_state 更新

profile 重新获取后，被 skip/limit 排除的论文 `num_citations_on_scholar` 保持旧值。修复：`cache_status` 强制用当前 profile 计数覆盖。

---

## 2026-05-01: 输出文件成为跨运行状态来源

per-paper 缓存文件同时承担"单次运行中断恢复"和"跨运行策略决策"两个职责。解耦：
- 缓存只做 within-run resume
- 输出文件 (`_fetch_state` 字段) 做跨运行决策

新增模块：`crawler/output_state.py`；新增测试：`tests/test_output_state.py`。

---

## 2026-05-01: 修复 cites_id 全为 null

scholarly 的 `_scholar_pub()` 不设置 `cites_id`。修复：fallback 到 `citedby_url` 中 `cites=` 参数提取。

---

## 2026-04-30: Year-based fetch 每页丢失 1 条

两种分页机制冲突：scholarly 的 `_SearchScholarIterator` 自动分页与我们的 `while True` 手动分页。修复：满页时也 break，分页控制权完全交还给 while True 循环。

---

## 2026-04-29: Cookie 跨次持久化 + profile 请求减少

- `output/curl.txt` 自动保存/加载 Scholar session cookie
- `fetch_publications` 复用 `fetch_basics` 的 author stub，每次 profile 抓取从 3 次 HTTP 请求减为 2 次
- Selective refresh 原因日志

---

## 2026-04-28: Direct fetch Progress saved 去重 + Cookie 持久化

- `_WrappedDirectIterator` 新增 `_items_in_current_page`，只在完整页打印 "Progress saved"
- 日志措辞改为 `Progress saved: N fetched this paper, M new across run`
- `author_fetcher.py` citation changes 从最多 5 条改为全量展示

---

## 2026-04-18: 分层缩进 year-based fetch 日志

三层体系：6 格（年份边界）、8 格（页面级）、10 格（页面内引用）。

---

## 2026-04-17: 修复 year_fetch_diagnostics dedup_count 跨次运行丢失

内外两个 FetchContext 不同步：`_fetch_by_year` 创建独立内部 ctx，`save_progress` 闭包读外部 ctx。修复：wrapper 同步内部 ctx → fetcher 属性 → save_progress。

---

## 2026-04-16: Histogram 权威化 + 策略简化

- `probe_complete` 降级、selective_refresh 大幅简化
- resume/update 合并为统一 fetch 策略
- dedup 不再跨 run 累计
- 移除 live citation count promotion 机制
- 移除 Refresh check 和 escalation 逻辑
- `drop_cached_unyeared` 恢复为所有 year-based fetch 都 drop

---

## 2026-04-15: 模块化重构完成 + fetch-mode

- 五轮模块化重构：拆出 12 个 crawler/ 模块 + 9 个测试文件
- `--fetch-mode {rough,normal,force}` 替换 `--recheck-citations`
- 共享 HTTP/2 session（profile + citation 阶段）
- 多项日志修复

---

## 2026-04-14: 模块化重构（第一至五轮）

- 第一轮：拆出 `scholar_common.py`、`scholar_profile_io.py`
- 第二轮：新建 `citation/cache.py`、`citation/strategy.py`
- 第三轮：统一到 `crawler/` 包 + 测试目录拆分（105 tests）
- 第四轮：拆出 citation_identity、citation_io、author_fetcher、cli
- 第五轮：拆出 interactive、scholarly_session、fetch_context、citation_fetch

---

## 2026-04-13: Citation 状态判定改为计数与 diagnostics 优先

`complete` 不再主导运行时流程。direct `seen >= scholar_total` 与 year histogram/diagnostics 满足关系决定 complete。

---

## 2026-04-11: post-fetch retry 限制 + direct early-stop 修复

- post-fetch 异常重试只允许一次
- direct fetch early-stop 计数改用 `fresh_citations` 而非 materialized cache

---

## 2026-04-10: 修复 year-based retry 死循环 + citation completeness 收口

- `_fetch_by_year` 外层的 reconciliation retry 死循环修复
- `_citation_status` 对 promoted totals 的误判修复

---

## 2026-04-08: 输出一致性修复 + cites_id 升级

- JSON / Excel 导出不一致修复
- cites_id 三级 fallback（title+venue/authors）
- 新增运行日志镜像

---

## 2026-04-04: citation accounting 语义切换为 histogram-authoritative

year-based 路径不再以 `cached_total == scholar_total` 作为完成条件，而是以 `cached_year_counts == probed_year_counts` 为准。

---

## 2026-04-01: year probe histogram 完整性语义 + 保守回退

probe 校验 full histogram DOM 的年度计数总和是否等于 Scholar 总数；不一致时回退为保守年份范围。

---

## 2026-03-31: 引用去重键升级 + 持久化年份分布

- 去重键从 title-only → `title + venue` / `title + authors` / `title`
- per-paper cache 新增 `probed_year_counts`、`cached_year_counts`

---

## 2026-03-28: Interactive 模式等待缩短实验参数化

`--accelerate SCALE` 参数，统一作用于所有 deliberate waits。

---

## 2026-03-26: CLI 语义清理

`--force-refresh-citations` → `--recheck-citations`；删除 `--hard`。

---

## 2026-03-25: 原地重试机制重构 + citation year probe 主数据源修正

probe 完整图 DOM 数据源修正：`#gs_md_hist .gs_hist_g_a` 节点。

---

## 2026-03-24: SSH/tmux 粘贴 cURL 卡死修复 + probing year 修复

ICANON-only 方案解决 SSH/tmux 下粘贴 cURL 卡死问题。

---

## 2026-03-23: `--hard` 参数 + `--help` 完善

---

## 2026-03-21: 交互式 captcha + cookie 跨阶段传递

- `--interactive-captcha` 模式
- profile → citation 过渡时复制 cookies

---

## 2026-03-20: httpx 降级至 0.27.2 + 浏览器请求还原 + 强制长休息

- httpx 0.27.2 修复 session 刷新
- 浏览器 headers 完整模拟
- 每 8-12 页强制 3-6 分钟长休息

---

## 2026-03-14: `as_sdt` 参数修正 + 随机化抓取顺序

`as_sdt=0,5` 替代 scholarly 默认的 `,33` 地区过滤。

---

## 2026-03-12: 修复漏抓引用 + captcha 交互改进

---

## 2026-03-10: 引用去重 + early stop 优化 + force refresh 参数

---

## 2026-03-09: 年份级查询 + session 刷新 + 日志增强

---

## 2026-03-08: 心跳超时 + 统一等待 + 项目重组

- 心跳 10s 检测，80s 超时 → `os._exit(1)`
- 所有主动等待 30-60s
- 两个脚本合并为 `scholar_citation.py`

---

## 2026-03-07: 项目初始化

- 第一步：作者主页采集脚本
- 第二步：论文引用采集 + 限流对策
