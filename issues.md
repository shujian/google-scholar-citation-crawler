### [代码规范] isinstance(..., PaperFetchState) 类型检查出现 14 次
- 文件: scholar_citation.py 多处
- 问题: 对 PaperFetchState 的类型检查散落在代码各处，每次都要区分 PaperFetchState 和 dict 两种路径
- 建议: 考虑统一接口，减少类型分派

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
