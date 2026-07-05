# 更新建议：google-scholar-citation-crawler

> 本文档基于跨项目代码评审生成，提供可直接执行的行动项。参考代码已放入 `reference-only/code-samples/` 目录。
> 行动项按建议的优先级排序（🔴 高 > 🟡 中 > 🟢 低），每个项目应该独立实施。

---

## 行动项

### [A] 引入结构化日志系统（优先级：🟡 中）

**当前**：使用 `TeeStream` 类 + `sys.stdout` 替换来同时输出到控制台和文件。
**目标**：迁移到 Python `logging` 模块，支持日志级别和 `--verbose` 控制。

**步骤**：
1. 新建 `crawler/log_utils.py`，参考 `reference-only/code-samples/analysis_content/logging_setup.py` 实现 `setup_logging()`
2. 在 `crawler/cli.py` 的 `parse_args()` 中增加 `-v` / `--verbose` 参数
3. 在 `_run_main()` 中调用 `setup_logging(args.verbose)`，逐步将关键路径的 `print()` 改为 `logger.info()` / `logger.debug()` / `logger.warning()` / `logger.error()`
4. 保留 `TeeStream` 作为可选的日志文件输出方式（通过 logging 的 `FileHandler` 替代），或继续用 TeeStream 兜底

**预期效果**：
- 外部工具可通过日志级别过滤输出
- `--verbose` 可开启 DEBUG 级别（目前只能靠 `--accelerate` 间接控制输出量）
- 与 analysis_content 的日志风格统一

---

### [B] 配置集中化（优先级：🟢 低）

**当前**：配置常量散落在 `crawler/common.py` 和多个模块顶部。
**目标**：将所有调优参数集中到 `crawler/config.py`。

**步骤**：
1. 参考 `reference-only/code-samples/analysis_info/config_pattern.py`，创建 `crawler/config.py`
2. 从 `crawler/common.py` 和其他模块顶部将可调优常量迁移过去
3. `crawler/common.py` 保留纯工具函数，移除常量定义

**注意**：本项目的架构已比较清晰，此项非必须，但能为未来维护者提供单一配置入口。

---

### [C] 确认 `.gitignore` 中的 `CLAUDE.md` 策略（优先级：🟢 低）

**当前行为**：`.gitignore` 包含 `CLAUDE.md`，这是四个项目中唯一忽略 CLAUDE.md 的。

**建议行动**：
- 如果 CLAUDE.md 包含工作流程说明，建议将其版本化（从 `.gitignore` 移除 `CLAUDE.md`）
- `.claude/` 目录已经忽略，足以保护敏感配置
- 如果不想公开 CLAUDE.md，请在文件首行注释说明原因

---

### [D] 考虑增加 `run.sh` 入口（优先级：🟢 低）

**当前**：通过 `python scholar_citation.py --author ...` 直接运行。
**建议**：增加 `run.sh` 自动检测 conda/pip 环境，方便不熟悉 Python 环境的用户。

参考模板：
```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if command -v conda &>/dev/null; then
    exec conda run -n scholar python scholar_citation.py "$@"
else
    exec python scholar_citation.py "$@"
fi
```

---

## 本次提供的参考代码

| 来源项目 | 文件位置 | 内容 |
|---------|---------|------|
| analysis_content | `reference-only/code-samples/analysis_content/logging_setup.py` | 结构化日志模式 |
| analysis_info | `reference-only/code-samples/analysis_info/config_pattern.py` | 集中配置模式 |
