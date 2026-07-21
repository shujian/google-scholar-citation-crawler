# 更新建议：google-scholar-citation-crawler

> 本文档基于跨项目代码评审生成，提供可直接执行的行动项。参考代码已放入 `reference-only/code-samples/` 目录。
> 行动项按建议的优先级排序（🔴 高 > 🟡 中 > 🟢 低），每个项目应该独立实施。
>
> **最后检查日期**：2026-07-05。所有行动项已完成。

---

## 行动项

### [A] 引入结构化日志系统（优先级：🟡 中）— ✅ 已完成

- `crawler/log_utils.py` 已创建，提供 `setup_logging(verbose, log_file)` + `get_logger()`
- `crawler/cli.py` 已增加 `-v` / `--verbose` 参数
- `_run_main()` 中已调用 `setup_logging()`，关键决策点已增加 logger 调用
- TeeStream 保留（向后兼容），logger FileHandler 与 TeeStream 双路径并存，无重复输出

### [B] 配置集中化（优先级：🟢 低）— ✅ 已完成

- `crawler/config.py` 已创建，所有可调优参数集中管理
- `crawler/common.py` 通过 `from crawler.config import ...` 保持向后兼容
- 所有现有 `from crawler.common import DELAY_MIN, ...` 无需修改

### [C] 确认 `.gitignore` 中的 `CLAUDE.md` 策略（优先级：🟢 低）— ✅ 已完成

- `.gitignore` 中已移除 `CLAUDE.md`，该文件现纳入版本控制

### [D] 考虑增加 `run.sh` 入口（优先级：🟢 低）— ✅ 已完成

- `run.sh` 已创建，自动检测 conda 环境

---

## 后续建议

- **逐步迁移 print() 到 logger**：当前只在 `cli.py` 的关键决策点（启动、跳过判断）使用了 logger。建议在后续迭代中逐步将其他模块的 `print()` 迁移为结构化日志调用。全部迁移完成后可移除 TeeStream。
- **测试中 curl_cffi 依赖**：2 个测试因 `curl_cffi` 未安装而失败（预有问题），建议在 CI 环境安装或对 `_patch_scholarly()` 增加 mock。
