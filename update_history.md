# Update History

本文档按时间倒序记录每次功能更新、bug 修复和重构的详细内容。

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
