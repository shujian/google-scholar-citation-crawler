### ✅ 已处理 [2026-06-02]

> 第一个页面访问等待没有意义，请在访问第一个页面时不需要等待。
> 文件: crawler/scholarly_session.py:236-240
> 问题: `patched_get_page` 中每次页面访问前都有 45-90s 随机等待，但第一个页面之前没有任何历史请求，等待没有意义

- `rand_delay()` 调用改为条件执行：`if ctx.total_page_count > 1`
- 第一个页面直接发起请求，后续页面保持原有等待逻辑
- 所有 119 个测试通过

----

### ✅ 已处理 [2026-05-21]

> [Bug] scholar_changed 同次运行不生效 + 引用变化论文未被计入 Need fetch
> 问题: 3 篇论文引用数变化（974→979、309→310、2→16），但 Need fetch/resume 只显示了 1。原因是 `cache_status` 中 `_citation_status()` 在 `mark_scholar_changed()` 之前调用，导致本次检测到引用变化的论文状态仍为 `complete`，要等到下一次运行才会重新抓取。
> 附带修复: Year done 日志只显示 year_new_count（年份桶新增），与 Paper Done / wait status 的 _new_citations_count（真正的新引用）不一致。

- 重构 `cache_status()`：先更新 `PaperFetchState`（`num_citations_on_scholar` + `scholar_changed`）再调用 `_citation_status()`
- Year done 日志改为同时显示两个值：`N new in year, M truly new this paper`
- 所有 119 个测试通过

----

### ✅ 已处理 [2026-05-12]

> [Bug] year_fetched_citations 变量未定义
> 文件: crawler/citation_fetch.py:875,879
> 问题: 变量 `year_fetched_citations` 从未赋值，当条件为 True 时会引发 NameError

- 将 `year_fetched_citations` 替换为 `year_batch.citations`（同一函数体内已在使用的正确变量）
- 所有 119 个测试通过

----

### ✅ 已处理 [2026-05-12]

> [代码规范] year_records 解析逻辑重复 3 次（冗余）
> 文件: scholar_citation.py:438-440, 447-449, 1140-1142
> 问题: 相同的 for 循环提取 year 记录的模式出现了 3 次

- 提取为共享函数 `index_year_records()` 放入 `crawler/output_state.py`
- `scholar_citation.py` 中导入为 `_os_index_year_records`，3 处调用点均已替换
- 所有 119 个测试通过

----

### ✅ 已处理 [2026-05-12]

> [代码规范] _direct_fetch_summary_message 和 _direct_fetch_log_message 几乎相同（冗余）
> 文件: crawler/citation_fetch.py:182,194
> 问题: 两个函数体完全相同，仅前缀字符串不同

- 合并为 `_direct_fetch_diagnostics_message(diagnostics, prefix="Direct fetch summary ")`
- 原两处调用点改为使用新函数并传入不同 prefix
- `scholar_citation.py` 包装器和方法调用同步更新
- 所有 119 个测试通过

----

### ✅ 已处理 [2026-05-12]

> [代码规范] _direct_fetch_diagnostics 和 _build_direct_fetch_diagnostics 重复（冗余）
> 文件: crawler/citation_fetch.py:138-170
> 问题: `_direct_fetch_diagnostics()` 只是 `_build_direct_fetch_diagnostics()` 的平凡包装器

- 删除 `_direct_fetch_diagnostics()` 包装器（citation_fetch.py）
- 删除 `scholar_citation.py` 中对应包装器
- `test_citation_status.py` 中测试改为调用 `_build_direct_fetch_diagnostics`
- 所有 119 个测试通过

----

### ✅ 已处理 [2026-05-13]

> [代码规范] _effective_scholar_total() 存在未使用的形参 cached
> 文件: scholar_citation.py:255
> 问题: `cached=None` 参数在函数体中完全被忽略

- 删除 `_effective_scholar_total()` 的 `cached=None` 形参（`scholar_citation.py`）
- 更新 `citation_fetch.py` 中 2 处调用（已无需修改，原来就不传 cached）
- 更新 `tests/test_fetch_policy.py` 中测试方法（重命名为 `test_effective_scholar_total_returns_pub_num_citations`，移除 cached 参数）
- 所有 119 个测试通过

----

### ✅ 已处理 [2026-05-13]

> [代码规范] _resort_publications() 方法未被使用（死代码）
> 文件: scholar_citation.py:259
> 问题: 该方法存在 # noqa: F401 注释，未被调用

- 删除 `_resort_publications()` 方法定义（`scholar_citation.py`）
- 所有 119 个测试通过

----

### ✅ 已处理 [2026-05-13]

> [代码规范] refresh_reconciliation_status() 函数未被使用（死代码）
> 文件: crawler/citation_strategy.py:159
> 问题: 该函数计算复杂的 reconciliation 状态，但没有任何地方调用

- 删除 `refresh_reconciliation_status()` 函数定义（`crawler/citation_strategy.py`）
- 删除 `tests/test_output.py` 中 6 处调用（1 处在 `test_save_output_writes_excel_run_metadata_from_json_payload` 中，5 个独立测试方法）
- 删除 `tests/test_citation_status.py` 中 `test_refresh_reconciliation_uses_seen_total_completion_for_year_diagnostics` 测试
- 所有 119 个测试通过

----

### ✅ 已处理 [2026-05-13]

> [代码规范] citation_status() 函数未被使用（死代码）
> 文件: crawler/citation_io.py:123
> 问题: 该函数接受 cache_dir 参数（旧版路径），已不再使用

- 删除 `citation_status()` 函数定义（`crawler/citation_io.py`）
- 所有 125 个测试通过

----

### ✅ 已处理 [2026-05-13]

> [代码规范] build_profile_payload() 和 save_profile_json() 未被使用（死代码）
> 文件: crawler/profile_io.py:165,178
> 问题: 这两个旧版函数已被 AuthorProfile.to_dict() 和 save_json() 替代，不再被调用

- 删除 `build_profile_payload()` 函数定义（`crawler/profile_io.py`）
- 删除 `save_profile_json()` 函数定义（`crawler/profile_io.py`）
- 所有 125 个测试通过

----

### ✅ 已处理 [2026-05-12]

> [代码规范] extract_fetch_state() 被导入但从未调用（死代码）
> 文件: crawler/output_state.py:447, scholar_citation.py:87
> 问题: `extract_fetch_state()` 作为 `_os_extract_fetch_state` 导入但从未被调用

- 删除 `extract_fetch_state()` 函数定义（`crawler/output_state.py`）
- 删除 `scholar_citation.py` 中的 `extract_fetch_state as _os_extract_fetch_state` 导入
- 删除 `tests/test_output_state.py` 中对应的导入和 `test_extract_fetch_state_excludes_citations` 测试用例
- 所有 125 个测试通过

### ✅ 已处理

> direct: diagnostics summary absent — will re-fetch
> 怎么还有这样的日志？这里说会re-fetch，但是实际也并没有发生re-fetch，这是为什么？
> 如果我没记错的话，我们之前约定是可以从year_record重新构建summary，然后决定下一步的获取行动。

- `is_complete()` 和 `completeness_diag()` 统一使用 `_infer_strategy_and_summary()`
- 新逻辑：当 `fetch_strategy=None` 时，优先检查 `year_fetch_diagnostics` 或 `year_records` 来推断为 `year` 模式
- 如果 `year_records` 存在但 `year_fetch_diagnostics` 缺失，会从 `year_records` 重建 summary
- 避免 `completeness_diag` fallback 到 `direct` 而 `is_complete` 正确推断为 `year` 的不一致

### ✅ 已处理

> 我发现fetched at对应的时间并没有被记录在日志里。请检查一下。

- fetch 完成后同步 `_fetched_at` 到 `PaperFetchState`

----

### ✅ 已处理 [2026-05-12]

> [代码规范] DirectFetchSession 类未被使用（死代码）
> 文件: crawler/fetch_session.py:203-227
> 问题: DirectFetchSession dataclass 定义了但从未被实例化或导入

- 删除 `DirectFetchSession` 类定义（`crawler/fetch_session.py`）
- 更新模块 docstring，移除 `DirectFetchSession` 的提及
- `crawler/__init__.py` 无导出需要删除（该文件为空）
- 所有 126 个测试通过

----

### ✅ 已处理 [2026-05-12]

> [代码规范] Citation、YearDiagnostics、DirectDiagnostics dataclass 未被使用（死代码）
> 文件: crawler/citation_models.py
> 问题: Citation、YearDiagnostics、DirectDiagnostics 三个 dataclass 定义了完整的 from_dict/to_dict 但从未被使用

- 删除 `Citation` 类定义
- 删除 `YearDiagnostics` 类定义
- 删除 `DirectDiagnostics` 类定义
- 更新模块 docstring，移除死代码的提及
- 所有 126 个测试通过

----

### ✅ 已处理 [2026-05-12]

> [代码规范] 直接访问 PaperFetchState 的 _private 字段
> 文件: scholar_citation.py:1193,1197,1200,1202
> 问题: `pst._year_records = yr`、`pst._direct_fetch_diagnostics = dfd`、`pst._year_fetch_diagnostics = yfd`、`pst._fetched_at = ...` 直接赋值私有字段

- 在 `crawler/output_state.py` 的 `PaperFetchState` 中添加 `restore_from_cache_snapshot(cache_snapshot)` 方法，封装所有 4 个私有字段的更新
- `scholar_citation.py` 中替换为 `pst.restore_from_cache_snapshot(cache_snapshot)` 调用
- 所有 126 个测试通过

----

====

### ✅ 已处理 [2026-05-13] _fetch_citations_with_progress 参数过多
- 文件: scholar_citation.py:637 和 crawler/citation_fetch.py:185
- 处理: 从 citation_fetch.py:fetch_citations_with_progress 删除 `rehydrated_probed_year_counts`、`rehydrated_probe_complete`、`rehydrated_year_fetch_diagnostics` 三个参数（它们始终被 None/False/None 覆盖），同时移除内部使用。从 scholar_citation.py 的方法签名和 pass-through 调用中同步删除。

====

### ✅ 已处理 [2026-05-13] 测试数量 127 → 119
- 文件: README.md:186, _work_notes.zh.md:36, CLAUDE.md:36
- 处理: 将三处文档中的 "127" 修改为 "119"（当前实际测试数）

====

### ✅ 已处理 [2026-05-13] CLAUDE.md 引用了不存在的 test_citation_page_stop.py
- 文件: CLAUDE.md:30,122-123
- 处理: 删除开发命令中的 test_citation_page_stop.py 命令和 Testing notes 中的遗留说明

====

### ✅ 已处理 [2026-05-13] PaperFetchState 字段数 10 → 11
- 文件: _work_notes.zh.md:189
- 处理: 字段数更新为 11，JSON schema 中补上 `scholar_changed` 字段

====

### ✅ 已处理 [2026-05-13] CLAUDE.md 模块依赖映射不完整
- 文件: CLAUDE.md:88-107
- 处理: 补全依赖：citation_fetch ← citation_cache, citation_models；fetch_session ← citation_models, output_state, common；output_state ← citation_io, citation_cache；scholarly_session ← page_visit

====

### ✅ 已处理 [2026-05-13] README.md 依赖描述缺少版本约束
- 文件: README.md:25
- 处理: 更新为 `scholarly>=1.7, openpyxl>=3.1, httpx==0.27.2` 与 requirements.txt 一致

====

### ✅ 已处理 [2026-05-13] isinstance(PaperFetchState) 类型检查散落各处 + CLAUDE.md DirectFetchSession 引用
- 文件: scholar_citation.py (14处), crawler/output_state.py (1处), CLAUDE.md (1处)
- 处理: 
  - 在 crawler/output_state.py 新增 `to_paper_fetch_state(obj)` 辅助函数，统一 PaperFetchState 类型分派逻辑
  - 替换 scholar_citation.py 中所有 14 处 `isinstance(..., PaperFetchState)` 为辅助函数调用
  - 替换 crawler/output_state.py 中 1 处 `isinstance(state, PaperFetchState)` 为辅助函数调用
  - 修正 CLAUDE.md 第 76 行：移除已删除的 DirectFetchSession 引用，仅保留 YearFetchSession
  - 运行 `python -m unittest discover` 全部 119 测试通过
