### [代码规范] extract_fetch_state() 被导入但从未调用（死代码）
- 文件: crawler/output_state.py:415, scholar_citation.py:87
- 问题: `extract_fetch_state()` 作为 `_os_extract_fetch_state` 导入但从未被调用
- 建议: 删除函数和导入

====

### [代码规范] build_profile_payload() 和 save_profile_json() 未被使用（死代码）
- 文件: crawler/profile_io.py:165,178
- 问题: 这两个旧版函数已被 AuthorProfile.to_dict() 和 save_json() 替代，不再被调用
- 建议: 删除这两个函数

====

### [代码规范] citation_status() 函数未被使用（死代码）
- 文件: crawler/citation_io.py:123
- 问题: 该函数接受 cache_dir 参数（旧版路径），已不再使用
- 建议: 删除该函数

====

### [代码规范] refresh_reconciliation_status() 函数未被使用（死代码）
- 文件: crawler/citation_strategy.py:159
- 问题: 该函数计算复杂的 reconciliation 状态，但没有任何地方调用
- 建议: 删除该函数

====

### [代码规范] _resort_publications() 方法未被使用（死代码）
- 文件: scholar_citation.py:259
- 问题: 该方法存在 # noqa: F401 注释，未被调用
- 建议: 删除该方法

====

### [代码规范] _effective_scholar_total() 存在未使用的形参 cached
- 文件: scholar_citation.py:255
- 问题: `cached=None` 参数在函数体中完全被忽略
- 建议: 删除 cached 参数

====

### [代码规范] _direct_fetch_diagnostics 和 _build_direct_fetch_diagnostics 重复（冗余）
- 文件: crawler/citation_fetch.py:138-170
- 问题: `_direct_fetch_diagnostics()` 只是 `_build_direct_fetch_diagnostics()` 的平凡包装器
- 建议: 删除包装器，用 `_build_direct_fetch_diagnostics()` 替换唯一的引用

====

### [代码规范] _direct_fetch_summary_message 和 _direct_fetch_log_message 几乎相同（冗余）
- 文件: crawler/citation_fetch.py:182,194
- 问题: 两个函数体完全相同，仅前缀字符串不同
- 建议: 合并为一个函数或内联

====

### [代码规范] year_records 解析逻辑重复 3 次（冗余）
- 文件: scholar_citation.py:438-440, 447-449, 1140-1142
- 问题: 相同的 `for rec in yr: if isinstance(rec, dict) and rec.get('year') is not None: per_year[rec['year']] = rec` 出现了 3 次
- 建议: 提取为共享的辅助函数

====

### [代码规范] isinstance(..., PaperFetchState) 类型检查出现 14 次
- 文件: scholar_citation.py 多处
- 问题: 对 PaperFetchState 的类型检查散落在代码各处，每次都要区分 PaperFetchState 和 dict 两种路径
- 建议: 考虑统一接口，减少类型分派

====

### [Bug] year_fetched_citations 变量未定义
- 文件: crawler/citation_fetch.py:875,879
- 问题: 变量 `year_fetched_citations` 在 `fetch_by_year` 中从未赋值，当 `stop_partial_resume_once_satisfied` 和 `resuming_partial_year` 都为 True 时会引发 NameError
- 建议: 将 `year_fetched_citations` 替换为 `len(year_batch.citations)` 或其他正确变量

====

### [代码规范] _fetch_citations_with_progress 参数过多
- 文件: scholar_citation.py:637（22 个参数）、crawler/citation_fetch.py:206（18 个参数）
- 问题: 参数列表过长，难以维护
- 建议: 将相关参数分组到 dataclass 中

====

### [文档] 测试数量 127 应为 126
- 文件: README.md:186, _work_notes.zh.md:36, CLAUDE.md:36,123
- 问题: 文档声称 127 个测试，删除 `test_rehydrate_probe_metadata_downgrades_legacy_or_stale_complete_flags` 后实际为 126 个
- 建议: 将三处文档中的 "127" 修改为 "126"

====

### [文档] CLAUDE.md 引用了不存在的 test_citation_page_stop.py
- 文件: CLAUDE.md:30,122-123
- 问题: 引用了 `test_citation_page_stop.py` 文件，但该文件在仓库中不存在
- 建议: 删除对这些引用，同时修正测试数量

====

### [文档] PaperFetchState 字段数 10 应为 11
- 文件: _work_notes.zh.md:189
- 问题: 核心 dataclass 一览表称 PaperFetchState 有 10 个字段，实际有 11 个（遗漏了 `scholar_changed`），JSON schema 示例中也缺少该字段
- 建议: 字段数更新为 11，JSON schema 中补上 `scholar_changed`

====

### [文档] CLAUDE.md 模块依赖映射不完整
- 文件: CLAUDE.md:88-107
- 问题: citation_fetch 还依赖 citation_cache、citation_models；fetch_session 依赖 citation_models、output_state、common；output_state 依赖 citation_io、citation_cache；scholarly_session 依赖 page_visit
- 建议: 补全依赖映射

====

### [文档] README.md 依赖描述缺少版本约束
- 文件: README.md:25
- 问题: 只写了 `scholarly`、`openpyxl`，缺少 `>=1.7` 和 `>=3.1` 版本约束
- 建议: 更新为 `scholarly>=1.7, openpyxl>=3.1, httpx==0.27.2` 与 requirements.txt 一致

====
