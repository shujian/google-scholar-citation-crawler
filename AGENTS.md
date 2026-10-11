# AGENTS.md

请使用中文作为主要工作语言。但是在代码和提交中使用英文。

## 工作管理
- `_work_notes.zh.md`：技术细节、架构决策、踩坑记录，供自己和贡献者参考
- `_update_history.zh.md`：按时间顺序的更新历史
- `_user.zh.md`：用户原始输入历史，展示 AI 辅助开发的完整对话轨迹
- `README.md`：面向外部用户的功能说明，有重大功能更新时同步更新

每次完成功能更新或者bug修复等任务之后，请将用户完整输入记录在 _user.zh.md，将更新历史记录在 _update_history.zh.md，将技术细节记录在 _work_notes.zh.md，并考虑 README.md 是否需要更新。上述事宜都处理完毕之后，提交git更新。

如果用户对某项功能的实现有异议，或者发现了问题需要进一步修复，请同样在完成后以上述流程进行处理。

如果对某些用户要求有异议，比如觉得要求不合理，或者实现有问题，请先跟用户确认。

## 协作与改动流程

1. **先找根因再动手**：读相关代码理解现状。bug 先定位根本原因，不猜测、不盲目改；新功能先评估
   影响范围与最简实现，必要时与用户确认方案后再改。
2. **最小改动**：只改与问题直接相关的部分，不顺手重构，不加多余的注释 / 类型注解 / 错误处理；
   不创建不必要的新文件；不引入新依赖（除非必要）。
3. **改完先验证**：静态检查（`python3 -c "import ast; ast.parse(open(<path>).read())"`）或跑对应单测。
4. **请用户确认**问题已解决，再进入下一步。
5. **收尾**：按「工作管理」更新三份笔记，并考虑 `README.md`；然后提交 —— commit message 用英文，
   写清做了什么**以及为什么**，一个逻辑单元一次提交，不把无关改动混在一起。

调试与特殊情况：

- 一次只改一个假设，确认有效再继续；同一个问题失败三次以上就停下来质疑架构，与用户讨论。
- 三份 `.zh.md` 笔记是历史记录，**只追加，不改写历史段落**。
- 撤销上次提交：`git reset HEAD~1`，改完后重新提交。
- 需要测试但网络不可用：做静态检查 + 代码逻辑审查，并在结论里标注"待网络恢复后验证"。
- 用户打断正在进行的操作：停止当前工作，先处理新指令，再决定是否回到原任务。
- 发现隐私信息：立即替换为占位符，并检查 git 历史是否也需要清理。

This file provides guidance to AI coding agents working with code in this repository.

## Development commands

从项目根目录运行；入口一律是模块形式 `python -m code.scholar_citation`（**不要**用
`python code/scholar_citation.py`：包目录名 `code` 会与标准库 `code` 模块冲突）。

- Install deps: `pip install -r requirements.txt`
- Run the CLI: `./run.sh --author YOUR_AUTHOR_ID` 或 `python -m code.scholar_citation --author YOUR_AUTHOR_ID`
- Run a small targeted crawl: `python -m code.scholar_citation --author YOUR_AUTHOR_ID --skip 2 --limit 3`
- Force re-fetch specific papers: `python -m code.scholar_citation --author YOUR_AUTHOR_ID --fetch-mode force --skip 2 --limit 3`
- Enable debug logging: `python -m code.scholar_citation --author YOUR_AUTHOR_ID -v`
- Use interactive captcha recovery: `python -m code.scholar_citation --author YOUR_AUTHOR_ID --interactive-captcha`
- Speed up waits for debugging: `python -m code.scholar_citation --author YOUR_AUTHOR_ID --interactive-captcha --accelerate 0.1`
- Override the proxy: `python -m code.scholar_citation --author YOUR_AUTHOR_ID --proxy-url http://127.0.0.1:7890`
- Run all tests: `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests`
- Run a single test file: `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_citation_status`
- Run a single test: `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_citation_status.CitationStatusTests.test_citation_status_stays_complete_when_seen_matches_current_total`
- Repair tools (also modules, from the project root):
  `python -m code.fix_diag_from_year_record <output_json>` /
  `python -m code.fix_output_fetch_state [output_dir]`
- Example script: `code/examples/run_example.sh`

## 论文选择：按作者抓取，没有 `--papers`

阶段 1 **不参与** 3a / 3a' / 3b 的 `-p`/`--papers` 约定：crawler 操作的是 Google Scholar
上的论文列表，没有本地 `papers/` 目录，也没有跨仓库稳定的论文编号可选。范围控制只有位置切片：

- `--skip M --limit N`：在「按引用数降序」排序后的 publication list 上取第 M 篇起的 N 篇，
  覆盖"先试几篇"与"从中间继续"两种场景；是否重抓由 `--fetch-mode` 决定。
- **按 `pub.no` 选择论文的 `--papers 1,3,5-8` 已评估并否决**（2026-07）：它只能在 Phase 1
  拿到 publication list 之后、Phase 2 之前过滤，且必须与 `--skip`/`--limit` 互斥，收益不足以
  再加一条 CLI 约定。历史文档里出现过 `--papers` 的用法示例，**那是错的，不要照做、也不要再实现它**。
- 引用级过滤（`-p 12(50,141,300)`）同样不适用：Scholar 的引用列表页无法跳过中间的条目，
  crawler 只能整篇抓取；若将来确实需要缩小输出，只能做"输出裁剪"而非抓取优化
  （评估见 `suggestion.md`）。

## 目录治理与配置（2026-10-10）

仓库根目录只保留文档、依赖清单、薄启动脚本与数据目录；**所有代码在 `code/`**：

```
google-scholar-citation-crawler/
├── AGENTS.md / README.md / issues.md / *_notes.zh.md / *_history.zh.md / *_user.zh.md
├── requirements.txt / environment.yml
├── run.sh / my_citation.sh（本机个人脚本，gitignored）
├── crawler_config.json（本机运行期配置，gitignored）
├── output/                        # 所有运行期产物：抓取产物 + logs/ + 遗留缓存
└── code/                          # 唯一代码目录（包名 code）
    ├── scholar_citation.py        # 入口：python -m code.scholar_citation
    ├── examples/run_example.sh
    └── fix_*.py                   # 一次性数据修复工具（可 python -m code.fix_*）
```

- **产物位置不变**：`output/author_<ID>_paper_citations.json` / `.xlsx`、`author_<ID>_profile.json`
  仍是根 AGENTS.md 数据契约规定的路径，不要移动。
- **日志与运行期中间产物**一律进 `output/`（含 `output/logs/`）。
- **不依赖任何环境变量**：旋钮走命令行参数，成组配置走项目根 `crawler_config.json`，
  优先级 **命令行参数 > 配置文件 > 代码内默认值**。`--config PATH` 可指向别的配置文件；
  默认文件缺失不算错误，显式指定的文件缺失/损坏会直接报错退出。
- `code/lib_env.py` 是 `code/` 下**唯一**接触 `os.environ` 的模块：它只为第三方库摆设环境
  （selenium 遥测开关、httpx 0.27.2 需要的 `NO_PROXY` 规范化），并把解析后的代理写进环境
  供 httpx 使用（httpx 只认环境代理）。`tests/test_no_env_config.py` 会扫描源码守住这条边界。
- `scholar_cache/` 已是历史遗留（无代码再写入）：现存残留移到 `output/legacy_scholar_cache/`，
  `.gitignore` 保留该条目以防旧版本重新生成。

## Project structure

详细项目结构见 `_work_notes.zh.md` 的「项目结构」节（含各模块中文说明），对外简介见 `README.md` 的「Development Notes → Project Structure」节。

简况：`code/` 有 19 个支撑模块（author profile 层、citation data 层、fetch engine 层、CLI、配置与日志，
其中 `lib_env.py` 管第三方库环境）；`tests/` 有 257 个测试，14 个文件。

### 新增模块

- **`code/config.py`**：集中配置。所有可调优常量（延迟、阈值、路径）的单一入口，并负责
  `crawler_config.json` 读取与 `resolve_proxy_config()`（命令行 > 文件 > 系统默认）。
  `code/common.py` 中的常量已迁移至此并保持向后兼容 re-export。
- **`code/log_utils.py`**：结构化日志系统。`setup_logging(verbose, log_file)` 配置 console + 可选 file handler；活入口 `code.scholar_citation.main()` 只启用 console handler（它写 `sys.stdout`，也就是 `TeeStream`），因为再挂一个 FileHandler 会成为同一文件的第二个写者并互相覆盖。现有 `print()` 调用仍通过 `TeeStream` 写入日志文件；新增代码应使用 `get_logger()` 获取 logger 并调用结构化日志方法。
- **`code/lib_env.py`**（2026-10-10）：第三方库环境摆设 + `NO_PROXY` 规范化 + 代理环境桥接，
  是允许出现 `os.environ` 的唯一模块（见上）。
- **`run.sh`**：便捷入口，自动检测 conda 环境后执行 `python -m code.scholar_citation`。

## Architecture overview

The program always runs in two sequential phases from `main()`:

**Phase 1 — Author profile** (`AuthorProfileFetcher` in `code/author_fetcher.py`):
- Fetches author basic info and publication list from Google Scholar every run
- Detects citation-count changes by comparing with the previous profile
- Sorts publications by citation count (descending) before downstream processing
- Maintains `change_history` inside `author_<ID>_profile.json`
- Writes both JSON and formatted Excel outputs

**Phase 2 — Paper citations** (`PaperCitationFetcher` in `scholar_citation.py`):
- Reads the saved profile JSON output (cross-run state) and in-memory mid-paper state (same-run resume)
- Decides per paper whether citation data is `missing`, `partial`, `complete`, or `skip_zero`
- Resumes from in-memory state (`_mid_paper_state`, `_paper_states`)
- Writes consolidated citation outputs to `author_<ID>_paper_citations.json` and `.xlsx`

### Citation fetch strategy

Status is derived from **current counts and diagnostics**, not from persisted `complete` flags:
- Direct mode: skip when `num_citations_seen >= scholar_total`
- `stalled`: still incomplete, but the outcome signature (`fetch_outcome_signature`)
  repeated `STALL_AFTER_ATTEMPTS` runs in a row, so retrying cannot help while
  Scholar's counts stay the same (e.g. Scholar lists fewer citations than it
  reports).  Cleared automatically by any change to the signature; force a retry
  with `--fetch-mode force`.
- Year mode: skip only when **every** year covers its own probe
  (`seen_total >= histogram_count` in the per-year records) *and* the aggregate
  holds.  The aggregate alone is not enough: years whose bucket exceeds the
  probe prop up `histogram_total` and can hide a short year, so a paper with
  unfetched citations would be skipped forever.
- `--fetch-mode {rough,normal,exact,force}` controls re-fetch aggressiveness
- `scholar_changed` flag triggers re-fetch when Scholar count changes even for complete papers
- `is_complete()` and `completeness_diag()` share `_infer_strategy_and_summary()` (`code/output_state.py`):
  when `fetch_strategy` is absent it is inferred from `year_fetch_diagnostics` / `year_records`, and a
  missing diagnostics summary is rebuilt from `year_records`.  They must never disagree — a `direct`
  fallback in one and a `year` inference in the other used to produce the misleading
  "direct: diagnostics summary absent — will re-fetch" log line.

Papers with `>= YEAR_BASED_THRESHOLD (50)` citations switch to year-by-year fetch mode:
- Supports resume via `partial_year_start` offsets, which are persisted with the
  paper's fetch state (a `completed_year_segments` set only avoids re-walking a
  year inside the same run — it is deliberately not carried across runs)
- Oldest→newest year order
- Year range is determined by `probe_citation_start_year()` via the Scholar histogram DOM
- An interrupted fetch persists the in-run snapshot (citations fetched so far +
  the live probe) when the output is written, so a short year stays visible on
  the next run instead of the paper looking complete

### scholarly patch layer (`code/scholarly_session.py`)

Patches `scholarly` internals rather than treating it as a black box:
- Replaces HTTP/1.1 sessions with `httpx` HTTP/2 clients.  TLS-impersonation libraries
  (`curl_cffi`) were **dropped**: Scholar's `/scholar?cites=` answers 429 for every impersonation
  target, so `curl_cffi` is deliberately not a dependency of this project.
- Injects browser-like headers (Chrome/145, sec-ch-ua-*, Referer chain)
- Enforces randomised 45–90s waits before every request — **except the first page of a run and the
  first page after fresh cookies are injected** (`total_page_count == 1` / `curl_page_count <= 1`):
  no earlier request with those credentials exists to space apart from, so waiting there only wastes time
- Takes mandatory long breaks every 8–12 pages to reset Scholar's rate-limit window
- Performs soft session refresh every 10–20 pages
- Verifies every patch hook point at the end of `patch_scholarly()` and raises
  when a scholarly upgrade made one of them ineffective (see
  `assert_scholarly_patches_active`)

All patch state is held in `SessionContext`; per-paper cross-run state is in `PaperFetchState` (`code/output_state.py`), and runtime fetch state is in `YearFetchSession` (`code/fetch_session.py`).  Dispatch on that type through `to_paper_fetch_state()` instead of scattering `isinstance(..., PaperFetchState)` checks around the caller.

### Interactive recovery (`code/interactive.py`)

- `--interactive-captcha`: user pastes a browser "Copy as cURL"; cookies and
  allowlisted headers are injected into the patched scholarly sessions
- In interactive mode: retries indefinitely
- In non-interactive mode: waits up to 24h prompting the user to switch proxy and type `ok`

## Module dependency map

```
code/scholar_citation.py  (orchestrator + PaperCitationFetcher — python -m code.scholar_citation)
  ├── code.config                 (centralised constants — all tunables)
  ├── code.lib_env                (env staging for third-party libs + NO_PROXY normalise + proxy bridge)
  ├── code.common                 (utilities, backward-compat re-exports from config)
  ├── code.log_utils              (structured logging — setup_logging, get_logger)
  ├── code.author_fetcher         ← code.profile_io
  ├── code.profile_io             ← (no intra-package deps)
  ├── code.citation_cache         (pure year/diagnostics functions)
  ├── code.citation_strategy      ← code.citation_cache
  ├── code.citation_identity      (pure dedup / info extract)
  ├── code.citation_io            ← code.citation_cache, code.citation_strategy
  ├── code.citation_models        (dataclasses for citations, diagnostics, resume state)
  ├── code.output_state           ← code.citation_io, code.citation_cache
  ├── code.pub_info               (PubInfo dataclass)
  ├── code.citation_fetch         ← code.common, code.fetch_session, code.citation_cache, code.citation_models
  ├── code.fetch_session          ← code.citation_models, code.output_state, code.common
  ├── code.page_visit             (per-page captcha/proxy/retry recovery)
  ├── code.scholarly_session      ← code.common, code.page_visit
  ├── code.interactive            (no intra-package deps)
  └── code.cli  [lazy import]     ← code.common, code.author_fetcher
                                     (parse_args only; the entry point is code.scholar_citation:main)
```

No circular imports. `code/scholar_citation.py` imports `parse_args` from
`code.cli`, which therefore contains only the argparse definitions (no
scholarly import) and no second copy of the workflow.

## Testing notes

Tests are in `tests/` and require no network or real Excel library (all external
dependencies are stubbed in `tests/conftest.py`).

- `FetcherTestCase` base class provides a fully-initialised `self.fetcher` with all
  runtime attributes zeroed out and common method stubs in place.
- Each test file covers one functional area; see `_work_notes.zh.md` 项目结构 for the mapping.
- `tests/test_no_env_config.py` is a **source-level guard**: with the sole exception of
  `code/lib_env.py` it fails if any module under `code/` reads configuration through
  `os.getenv` / `os.environ.get` / `os.environ[...]`.
- `tests/test_proxy_config.py` covers config-file loading and the CLI > file > system-default
  proxy resolution (temporary files + patched environment, never the developer's real
  `crawler_config.json`).
- When changing year-based fetching, resume behavior, or CLI flags, update the
  corresponding test file first rather than adding integration coverage.

## Dependencies and runtime assumptions

- `httpx==0.27.2` is pinned for compatibility with the current scholarly integration.
- **本程序不要求任何环境变量**。代理优先来自 `--proxy-url` / `--no-proxy`，其次
  `crawler_config.json` 的 `proxy_url` / `no_proxy`（本机文件，gitignored），最后才回退到系统的
  `https_proxy` / `http_proxy` / `no_proxy`（HTTP 生态通用约定，`httpx` / `requests` 自身也读）。
  解析后的值会写回进程环境，因为 httpx 只认环境代理。`scholarly` 自带的 proxy API 有意不使用
  （与 httpx 0.27.x 不兼容）。
- `NO_PROXY` / `no_proxy` 在 `scholarly` 导入前被规范化（`[::1]` → `::1`）：httpx 0.27.2 在有
  括号 IPv6 条目时连客户端都构造不出来。见 `code.lib_env.sanitize_no_proxy`（`code.common`
  仍按旧路径 re-export）。
- `code/` 下除 `code/lib_env.py` 外不得出现 `os.getenv` / `os.environ.get` / `os.environ[...]`，
  由 `tests/test_no_env_config.py` 守卫。
- The default output directory is `./output`; state is maintained in memory during runs.
- All development happens on `main` branch.
