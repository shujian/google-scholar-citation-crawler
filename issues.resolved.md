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
