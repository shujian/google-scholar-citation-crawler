# AGENTS.md

请使用中文作为主要工作语言。但是在代码和提交中使用英文。

## 工作管理
- `_work_notes.zh.md`：技术细节、架构决策、踩坑记录，供自己和贡献者参考
- `_update_history.zh.md`：按时间顺序的更新历史
- `_user.zh.md`：用户原始输入历史，展示 AI 辅助开发的完整对话轨迹
- `README.md`：面向外部用户的功能说明，有重大功能更新时同步更新

每次完成功能更新或者 bug 修复等任务之后，请将用户完整输入记录在 `_user.zh.md`，将更新历史记录在
`_update_history.zh.md`，将技术细节记录在 `_work_notes.zh.md`，并考虑 `README.md` 是否需要更新。
上述事宜都处理完毕之后，提交 git 更新。

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
- **Cite 对话框默认关闭**（`--cite-dialog` 才抓）：一条施引条目一个请求，实测会极大加剧代理被判定的风险；
  下游不依赖它（3.a.1 只把它当首选来源，没有就退回 PDF / DBLP / HTML / LLM）。
  **BibTeX is not fetched**: its signed link is minted per dialog render and is unusable from a
  cache. The step is opt-in (`--cite-dialog`), e.g.
  `python -m code.scholar_citation --author YOUR_AUTHOR_ID --cite-dialog --skip 0 --limit 1`
- Papers-directory maintenance:
  `python -m code.scholar_citation --author YOUR_AUTHOR_ID --renumber-papers {off,dry-run,apply,force}`
  （默认 `apply`；`dry-run` 只报告，`force` 忽略阶段 2 的使用锁；也可在 `crawler_config.json`
  写 `renumber_papers`）
- Speed up waits for debugging: `python -m code.scholar_citation --author YOUR_AUTHOR_ID --interactive-captcha --accelerate 0.1`
- Override the proxy: `python -m code.scholar_citation --author YOUR_AUTHOR_ID --proxy-url http://127.0.0.1:7890`
- Run all tests: `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests`
- Run a single test file: `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_citation_status`
- Run a single test: `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_citation_status.CitationStatusTests.test_citation_status_stays_complete_when_seen_matches_current_total`
- Repair tools (data repair/upgrade only — kept apart from the main entry point;
  also modules, from the project root):
  `python -m code.data_upgrade.fix_diag_from_year_record <output_json>` /
  `python -m code.data_upgrade.fix_output_fetch_state [output_dir]` /
  `python -m code.data_upgrade.normalize_venue_key [--apply] [--author ID]` /
  `python -m code.data_upgrade.compare_citations_meta`（只读对拍）/
  `python -m code.data_upgrade.align_citations_meta [--apply]`
  （把存量 `citations_meta.json` 的**主体**对齐到抓取产物 —— 只重写不一致的、
  走 `write_citations_meta()` 这一个写者、下载字段与退役编号原样保留、默认 dry-run）/
  `python -m code.data_upgrade.repair_chicago_titles [--apply]`
  （用对话框缓存的原文重算标题，不联网；只修"当前值恰好等于旧解析规则算出来那个值"的行，
  同步 `cite.venue_text` / `venue_key` 并刷新缓存条目，默认 dry-run；修完再跑
  `align_citations_meta --apply` 让 meta 跟上）/
  `python -m code.data_upgrade.dedupe_meta_rows [--apply]` /
  `python -m code.data_upgrade.refresh_product_keys [--apply]` /
  `python -m code.data_upgrade.rename_artifacts [--apply]` /
  `python -m code.data_upgrade.seed_citation_index --author ID [--apply]`
- Example script: `code/examples/run_example.sh`

## 论文选择：按作者抓取，没有 `--papers`

阶段 1 **不参与** 3.a.1 / 3.a.2 / 3.b 的 `-p`/`--papers` 约定：crawler 操作的是 Google Scholar
上的论文列表，没有跨仓库稳定的论文编号可选。范围控制只有位置切片：

- `--skip M --limit N`：在「按引用数降序」排序后的 publication list 上取第 M 篇起的 N 篇，
  覆盖"先试几篇"与"从中间继续"两种场景；是否重抓由 `--fetch-mode` 决定。重复运行不会重置
  已有编号或缓存。
- **本阶段没有按 `pub.no` 选择论文的 `--papers`**，也不要实现它：它只能在 Phase 1 拿到
  publication list 之后、Phase 2 之前过滤，且必须与 `--skip`/`--limit` 互斥，收益不足以再加
  一条 CLI 约定。
- 引用级过滤（`-p 12(50,141,300)`）同样不适用：Scholar 的引用列表页无法跳过中间的条目，
  crawler 只能整篇抓取；若确实需要缩小输出，只能做"输出裁剪"而非抓取优化。

## 目录治理与配置

仓库根目录只保留文档、依赖清单、薄启动脚本与数据目录；**所有代码在 `code/`**：

```
google-scholar-citation-crawler/
├── AGENTS.md / README.md / issues.md / *_notes.zh.md / *_history.zh.md / *_user.zh.md
├── requirements.txt
├── run.sh / my_citation.sh（本机个人脚本，gitignored）
├── crawler_config.json（本机运行期配置，gitignored）
├── output/                        # 所有运行期产物：抓取产物 + logs/ + 遗留缓存
└── code/                          # 唯一代码目录（包名 code）
    ├── scholar_citation.py        # 入口：python -m code.scholar_citation
    ├── shared/                    # 跨仓库共享件的宿主（PEP 420，无 __init__.py）
    ├── examples/run_example.sh
    └── data_upgrade/              # 一次性数据修复/升级工具（**与主线入口分开**）
        ├── __init__.py
        ├── fix_*.py               # python -m code.data_upgrade.fix_*
        ├── normalize_venue_key.py # python -m code.data_upgrade.normalize_venue_key
        └── ...                    # align / dedupe / refresh / rename / seed / repair
```

- **产物位置不变**：`output/author_<ID>_paper_citations.json` / `.xlsx`、`author_<ID>_profile.json`
  是根 `AGENTS.md` 数据契约规定的路径，不要移动。
- **`output/author_<ID>_papers/` 是整条流水线共用的论文数据树**：物理位置在本仓库的 output 下
  （27 GB 级，gitignore），阶段 2 / 3.a.1 / 3.a.2 / 3.b / 阶段 4 **都不直接拼这个路径**，
  而是经各自的 `papers` 符号链接访问（整条链见根 `AGENTS.md`「仓库地图」的共享数据段）。
- 本阶段负责**创建与维护**这棵树：`code.config.papers_root()` / `ensure_papers_root()` 幂等确保
  根存在（只建不删）；`code/papers_maintenance.py` 按最新排名**创建论文目录并写
  `paper_meta.json`**、把已有目录**改名对齐**、同作品目录合并、已消失论文归档到 `papers/old/`；
  `code/citation_index.py` 写每篇的 `citations_meta.json` 主体与 `citations/<NNNN>-<slug>.json`。
  阶段 2 只补下载字段，不创建、不改名、不归档。
- **`papers/` 的使用锁**：本阶段每轮都会按最新排名改论文目录名，而阶段 2 拼路径只认输入快照的
  `pub.no` + 标题、写完还会 `makedirs` —— 两者同时跑会把旧名字的目录**重新建出来**，PDF / 进度 /
  meta 被拆成两套。所以阶段 2 在通过启动校验后往 `<papers 根>/.stage2.lock` 写一把提示锁
  （`{"stage","pid","host","started_at"}`，退出/中断时删），本阶段的目录维护在 `apply` 之前读它：
  **活着就不改名、只报告计划**，并提示"等阶段 2 结束后重跑阶段 1；确实要现在改就用
  `--renumber-papers force`"。锁判据是同主机看 pid 是否还在、跨主机（SSHFS）看文件年龄
  （`STALE_AFTER_HOURS`=24），陈旧锁不挡路。**提示只看"有没有活"**：先按只算不改算出计划，
  计划里真有条目（`moves` / `merges` / `orphans` / `conflicts`）才提示"阶段 2 正在使用、
  本轮不改名"；计划为空时一个字都不打 —— 没有编号变化就没有可争的目录名。共享件是 `code/shared/stage_lock.py`（**只用标准库**，
  阶段 2 经 `code/_shared.py` 消费）。
- **日志与运行期中间产物**一律进 `output/`（含 `output/logs/`）。日志文件只保留最近
  `LOG_KEEP_RUNS`（30）次，更早的由 `prune_run_logs()` 在 run 开始时**移动**（绝不删除）到
  `output/logs/old/`；当前 run 的日志永远保留。
- **不依赖任何环境变量**：旋钮走命令行参数，成组配置走项目根 `crawler_config.json`，
  优先级 **命令行参数 > 配置文件 > 代码内默认值**。布尔开关同理，例如 Cite 对话框那一步：
  `--cite-dialog`（旧别名 `--cite-formats`）/ `--no-cite-formats` > 配置键 `cite_dialog`
  （旧键 `cite_formats` 仍认）> 默认 **关闭**。`--config PATH`
  可指向别的配置文件；默认文件缺失不算错误，显式指定的文件缺失/损坏会直接报错退出。
- `code/lib_env.py` 是 `code/` 下**唯一**接触 `os.environ` 的模块：它只为第三方库摆设环境
  （selenium 遥测开关、httpx 需要的 `NO_PROXY` 规范化），并把解析后的代理写进环境供 httpx 使用
  （httpx 只认环境代理）。`tests/test_no_env_config.py` 会扫描源码守住这条边界。
- `scholar_cache/` 已弃用：没有代码再写入；残留归档在 `output/legacy_scholar_cache/`，
  `.gitignore` 保留该条目，防止更早的检出重新生成它。

## Project structure

详细项目结构见 `_work_notes.zh.md` 的「项目结构」节（含各模块中文说明），对外简介见 `README.md`
的「Development Notes → Project Structure」节。

`code/` 的支撑模块按职责分层：author profile 层、citation data 层、fetch engine 层、CLI / 配置 /
日志层；跨仓库共享件在 `code/shared/`，一次性数据工具在 `code/data_upgrade/`。

### 模块职责

**入口与编排**

- `code/scholar_citation.py`：CLI 入口 `main()` 与 Phase 2 编排器 `PaperCitationFetcher`。
  负责抓取前写 baseline 产物、每篇 checkpoint、中断后的保存状态报告、最终写
  `author_<ID>_paper_citations.json` / `.xlsx`。
- `code/cli.py`：只放 `parse_args()`；不 import `scholarly`，工作流不在别处重复。
- `code/citation_fetch.py`：direct / year 两种模式的抓取引擎（`fetch_citations_with_progress`、
  `fetch_by_year`）。
- `code/fetch_session.py`：`BatchFetchSession`（单次分页抓取）与 `YearFetchSession`
  （跨年运行的抓取状态）。

**作者与出版信息**

- `code/author_fetcher.py`：`AuthorProfileFetcher`，抓作者信息与 publication list。
- `code/profile_io.py`：profile JSON / Excel 输出与读取。
- `code/pub_info.py`：`PubInfo`，归一化 `pub` 字段；`venue_key` 优先取 `venue_full or venue`。
- `code/venue_detail.py`：从出版详情页读未截断 venue，写 `venue_full` / `venue_full_source` /
  `citation_for_view`；缓存 `output/venue_detail_cache.json`。
- `code/venue_quality.py`：「这条记录还值不值得再抓一次」的唯一判据：`is_complete_venue()` /
  `is_preprint_venue()` / `has_full_author_names()`，以及两个调用点的薄包装
  `venue_needs_detail()` / `citation_needs_cite_dialog()`。
- `code/cite_format.py`：从施引条目的 Cite 对话框读五种格式（MLA / APA / Chicago / Harvard /
  Vancouver），解析 `venue_text` / `authors_full`，按 Chicago 修正条目字段并把审计写进
  `cite.corrected`；缓存 `output/cite_format_cache.json`。

**引用数据与状态**

- `code/citation_cache.py`：year 计数与 diagnostics 的纯函数。
- `code/citation_strategy.py`：抓取策略、刷新策略、reconciliation。
- `code/citation_identity.py`：引用去重键、`extract_citation_info()`（含行 `cid`）、
  `refresh_venue_keys()`；截断 venue 只经 `TRUNCATED_VENUE_PREFIXES` 给 key。
- `code/citation_io.py`：缓存 I/O、完成状态推导、引用 Excel 输出。
- `code/citation_models.py`：`YearRecord` / `ResumeState` / `FetchPolicy` 等 dataclass。
- `code/output_state.py`：`PaperFetchState` / `PaperState`，以及输出文件 `_fetch_state` 的读写。
- `code/citation_index.py`：引用编号的**唯一分配者**（首次分配后不可变；新引文
  `max(已用 ∪ 已退役) + 1`；消失的编号进 tombstone 永不复用）；写
  `citations_meta.json` 主体与带 `cite` 的 `citations/<NNNN>-<slug>.json`。
- `code/papers_maintenance.py`：论文目录的创建 / 改名 / 合并 / 归档，以及阶段 2 使用锁检查。

**基础设施**

- `code/config.py`：集中配置与可调常量、`crawler_config.json` 读取、
  `resolve_proxy_config()` / `resolve_cite_formats()` / `resolve_renumber_papers()`。
- `code/common.py`：通用工具与向后兼容 re-export（常量本体在 `config.py`）。
- `code/lib_env.py`：第三方库环境摆设、`NO_PROXY` 规范化、代理环境桥接（唯一接触
  `os.environ` 的模块）。
- `code/log_utils.py`：结构化日志；`run_log` 是结构性日志的唯一出口。
- `code/page_visit.py`：逐页错误恢复（重试、冷却、验证码提示、代理切换）；对 detail 类页面的
  404/410 抛 `RecordUnavailable`。
- `code/scholarly_session.py`：scholarly monkey-patch、延迟与强制长休、year probe、响应状态码
  记录、`/sorry/` 拦截识别。
- `code/interactive.py`：浏览器 cURL / cookies 注入、验证码提示、代理切换等待。
- `code/venue_key.py`：`code.shared.venue_key` 的薄再导出，保住既有 import 路径。

### 跨仓库共享件（本仓库是宿主）

- **`code/shared/venue_key.py`**：venue 身份键的唯一实体。口径见下文「venue_key」；`venue_key.py`
  只是薄再导出，消费方经各自的 `code/_shared.py` 导入，各仓库有 `tests/test_shared_venue_key.py`
  守卫。
- **`code/shared/identity.py`**：引用身份判定 `same_citation_identity()` 与助手（真实 `cites_id`
  → DOI / arXiv 强标识 → 弱 URL 键 → 归一化标题 + 年份 + 首作者姓）。阶段 2 / 3.a.1 / 3.a.2 /
  3.b / 阶段 4 都消费同一份。
- **`code/shared/citation_key.py`**：引用身份键。`synthetic_cites_id()` 生成
  `no_id:<归一化被引标题>|<年份>|<首作者姓>`；`synthetic_keys_for()` 对同基础键追加
  `#doi:` / `#arxiv:` / `#url:<sha8>`，全缺才退 `#pos<n>`。`code/citation_index.py` 写
  `citations_meta.json` 主体时给无真实 id 的行补这个键；阶段 2 只再导出。
- **`code/shared/papers_layout.py`**：论文目录布局与编号的纯逻辑：`plan()` / `apply_plan()` /
  `merge_into()` / `reconcile()` / `target_dir_name()` / `sanitize_filename()`。**只用标准库**，
  消费方配置在调用时按需解析；阶段 2 的 `code/renumber_papers.py` 只是"再导出 + CLI"。
- **`code/shared/stage_lock.py`**：`papers/` 使用锁的读写与陈旧判定；阶段 2 写，阶段 1 读。
- **`code/shared/artifact_sync.py`**：per-citation 产物命名 `output_NNNN.json` 的唯一实现
  （`output_name()` / `iter_artifact_files()` / 既有命名回落与归档），3.a.1 / 3.a.2 / 3.b / 阶段 4
  消费。
- **`code/shared/lib_env.py`**：`NO_PROXY` 消毒引擎 `sanitize_entries()`；策略留各仓库
  （本仓库传「解括号 + 计数」，3.x 传「丢弃 + bool」）。

`code/shared/` 是 **PEP 420 命名空间包**：各宿主 `code/shared/` 下**不放** `__init__.py`，所以多个
宿主阶段可以共同贡献同一个 `shared.*`。消费方的 `code/_shared.py` 负责注入 `sys.path`。

### 运行日志

`code/log_utils.py` 的 `run_log`（`RunLog` 实例）是**所有结构性日志的唯一出口**，每行统一为

```
11:40:13 INFO  fetch     Year 1996 status: year_total=0, year_new=0, … | histogram=0, seen=0, …
└─ 时刻  └─ 级别  └─ 模块（左侧对齐）  └─ 每层 2 空格，层级 = run → phase → paper → year/page → 细节
```

- **级别**：`INFO` = 状态变化与结论；`DEBUG`（`-v`）= 每次请求 / 每页 / 每年明细；
  `WARN` = 需要人看一眼的异常；`TRACE` 预留。
- **两个阈值**：`threshold`（屏幕，默认 INFO）与 `file_threshold`（日志文件，默认 **DEBUG**）。
  `main()` 把日志文件 `attach_file()` 给 `run_log`：屏幕过滤掉的行由它**直接写文件**，打印出来的
  行仍由 `TeeStream` 镜像，所以两边各一份、不重复、不丢行。屏幕显示一整篇的推进过程
  （探针/缓存汇总、每年 `skip` / `fetching` / `status`、页进度、`Request URL`、`waiting …`），
  日志文件再保留逐条明细（`[n] <title>`、`Progress saved`、dedup、重试原因等）。
  `-v` 只是把**屏幕**也降到 DEBUG。
- **模块标签**：`run / phase / identity / profile / venue / http / fetch / year / paper / plan /
  output / cite / summary`，宽度固定，消息列对齐。
- **`run_log.section(title, module=…)`** 取代横幅：一个阶段/步骤一行 `── title ────…`。
- **一个事实一行**：URL 只在 session 层的 `Request URL:` 出现一次；`Year N compare:` 的字段并入
  `Year N status:`；`Author ID:` 只在 run 头出现。
- **名字只说一次**：`RunLog.set_entry(label)` 记录"这一次请求为哪条施引论文"，但**不是行首前缀**；
  只有 `scholarly_session.patched_get_page` 把它拼在那次请求的 `Request URL: …` 行尾。
  `code/cite_format.py` 在 `get_soup()` 前后 `set_entry()` / `clear_entry()`（`finally` 里清，
  失败也一样）。判据是**一次请求里，同一条信息只出现一次**。
- **一次请求只描述一次，且都写全**：Cite 对话框的 404 过去会打三条（`Request URL` +
  一条把 URL 截断成 80 字符的 WARN + 逐行结论），标题也被截到 50/60 字符。
  现在：`page_visit` **不再**为"这条记录不存在"打 WARN（它只抛 `RecordUnavailable`），
  由调用方那一行说（`cite` 的逐行结论、`venue_detail` 的逐篇结论，都带**完整标题**）；
  `Request URL` / WARN 里的 URL 不再截断，逐行结论的标题也不截断。一条对话框 = 两行：
  `Request URL: …· "完整标题"` 与 `[n/N] <cid> "完整标题" -> no Cite dialog (HTTP 404); …`。
- **成功也留证据**：逐行的 Cite 结论只打**失败**（成功静默），所以另有两处说明"抓到了没有"：
  ①每取回一条（含缓存命中的）打一行 DEBUG
  `[3/10] <cid> "完整标题" -> 5 formats (mla, apa, chicago, harvard, vancouver)`（缓存命中时
  尾巴带 `· cached`，只进日志文件）；②逐页钩子每页一条 INFO
  `cite on this page: 8/10 resolved (fetched 6, cached 2), 2 skipped (404), 0 failed`
  —— 屏幕上能直接看出这一页成了几条。保存时的 `cite dialogs:` 行仍是整轮口径。
- **Cite 统计只留一条结论行**：保存时先算"这一遍会真正改动多少行"（`_cite_work()`：纯函数、
  零请求），候选为 0 就整段静默；有活才打 `── Cite dialogs · N row(s) to resolve ──`，段尾只有
  一条 INFO `cite dialogs: X/Y rows now carry formats (Z with all five) | …`；这一遍的明细
  （`resolved/fetched/cached/…`）降为 DEBUG 的 `cite pass:`。写文件的计数回到发生的位置：
  `INFO output  cite files: N written, M unchanged`（`N == 0` 时不打）。
- **Run summary 收尾必报"cite 覆盖率"**：最后一行固定为
  `cite coverage: 1903/9197 rows carry a cid (and 1526 of those carry a Cite dialog) | papers: 1/241
  fully cid'd, 1/241 fully cid'd + dialogued`。口径写在 `citation_id_totals(results)` 一处：
  **行**看 `cid` / `cite`；**篇**只有在它**每一条**引用都带 cid（或 cid + cite）时才算"全有"，
  没有引用行的论文不计入分子。缺 cid 的行就是"这轮寻址不到对话框"的行 —— 要补只能重走列表页。
- **逐页进度行**：抓引用列表时每页一行 `2025 · page 3 · 10 items`（direct 模式无年份 →
  `page 2 · 7 items`）。这是"现在抓到哪一年、第几页"的唯一来源。判据在
  `citation_page_progress()` / `citation_page_module()`（`code/scholarly_session.py`），页码取
  URL 的 `start=`、年份取 `as_ylo=`。
- **第三方 logger 一律压到 WARNING**：`httpx` / `httpcore` / `scholarly` 的逐请求日志绕过
  `run_log`，且从不进日志文件（root handler 写 stderr，run log 是 stdout 的 TeeStream）。
  `setup_logging()` 把它们设为 WARNING（`-v` 给回 DEBUG）。
- **测试**：`tests/conftest.py::RunLogDebugCase` 把阈值降到 DEBUG（测试会断言请求 / 页级明细），
  `FetcherTestCase` 已继承它；`setup_logging()` 会把阈值改回 INFO，所以每个测试自己声明。
- **中断时如实报告**：`_save_output` **先**写 `author_<ID>_paper_citations.json`，**再**跑 Cite
  对话框那一步（只给已有引用补字段），完成后才重写一次带对话框的版本；`run()` 用
  `_save_completed` / `_save_stage`（`json` → `xlsx` → `done`）记录保存进度，中断后
  `_report_saved_state()` 打印**磁盘上实际有什么**（产物与缓存的时间戳、保存停在哪一步）。
  报告措辞按"产物写没写"分开：`_save_json_written` 为真时说"产物已写好，停在 Cite 对话框的
  重写那一步"，只有真的没写才说"可能落后一轮"。
- **续跑只读正式产物**（而且它每篇都更新）：`author_<ID>_paper_citations.json` 是唯一的续跑状态来源
  （`_paper_states`、索引分配、`has_cite_format_work()`、checkpoint 索引全用它）。没有
  `*.partial.json` 全量快照：它不带 `_fetch_state`，当不了续跑账本，而每篇重写代价很大。
  硬中断（`kill -9` / 断电）也不会丢进度：每篇抓完都把该篇写进产物（含 `_fetch_state`），
  所以下一轮读到的是"每篇都最新"的状态。

## Architecture overview

The program always runs in two sequential phases from `main()`:

**Phase 1 — Author profile** (`AuthorProfileFetcher` in `code/author_fetcher.py`):
- Fetches author basic info and publication list from Google Scholar every run
- Detects citation-count changes by comparing with the previous profile
- Sorts publications by citation count (descending) before downstream processing
- Fetches each publication's Scholar **detail page** once and stores the untruncated
  venue in the additive `pub['venue_full']` field (`code/venue_detail.py`); the
  truncated `pub['venue']` is never rewritten
- Derives `pub['venue_key']` / `pub['venue_key_source']` from `venue_full or venue` +
  year (`code/venue_key.py`); both are recomputed on every serialisation, so they
  can never go stale
- Maintains `change_history` inside `author_<ID>_profile.json`
- Writes both JSON and formatted Excel outputs

**Phase 2 — Paper citations** (`PaperCitationFetcher` in `scholar_citation.py`):
- Writes a **baseline** `author_<ID>_paper_citations.json` before fetching (all papers, previous
  citations, indices already assigned), so stage 2 has a complete, index-aligned input while a long
  crawl is still running
- Reads the saved profile JSON output (cross-run state) and in-memory mid-paper state (same-run
  resume)
- Decides per paper whether citation data is `missing`, `partial`, `complete`, or `skip_zero`
- Resumes from in-memory state (`_mid_paper_state`, `_paper_states`)
- Persists every citing entry's Scholar cluster id as the additive `cid` field
  (`extract_citation_info`; pure parsing, no request)
- Writes `venue_key` / `venue_key_source` on every citing entry the same way
  (list-page venues are often truncated by Scholar, so those rows keep raw keys
  until the table is extended — `venue_key_source` tells the two apart)
- Resolves each citing entry's five citation formats from Scholar's Cite dialog
  before writing the output (`code/cite_format.py`, cached in
  `output/cite_format_cache.json`）。Chicago 串是施引论文**全作者全名**与
  **未截断 venue** 的来源，但这一步**默认关闭**：`--cite-dialog` 才抓（`--cite-formats` 是它的
  旧别名，`--no-cite-formats` / `--no-cite-dialog` 显式关闭；配置文件键 `cite_dialog`，
  旧键 `cite_formats` 仍认）。
  一个施引条目一个请求，实测会极大加剧代理被封的风险 —— 用户 2026-10-28 定。下游不依赖它：
  3.a.1 只把 `cite` 当**首选**来源（全名 + 未截断 venue），缺了会自动退回 PDF / DBLP / HTML / LLM；
  3.a.2 / 3.b / 阶段 4 不读它。没有 cite 时 `venue_key` 退回列表页 venue（多为截断串、落到 `raw`）。
  一个没有 `cid` 的条目本来就寻址不到对话框。
  **`cid` 是按每次渲染发放的临时句柄，不是稳定标识**：同一条列表 URL 隔一分钟再取，
  前几行的 `data-cid` 就换了一批；拿几天前的句柄去问 `output=cite` 得到的是 Google 的 404 页
  （一个两天前成功抓过对话框的 cid 也 404），而同一时刻刚从列表页取到的新句柄返回 200。
  **404 是瞬时的，既不是"记录不存在"也不是封锁**：同一个 cid、同一个 URL 隔一秒重发就从 404
  变 200（2026-10-23 实测；4 个新 cid × 3 次 = 12/12 成功）。所以：
  ①对话框只能"一边读页、一边立刻抓"（逐页钩子），补 Chicago 只能靠 `--fetch-mode force`
  重走列表页；②`patched_get_page` 对 Cite 对话框的 404 **再问一次**（重试仍走正常等待）；
  ③**不写**"7 天不可用"缓存——只跳过该行，下次再问（旧版记下的已用
  `python -m code.data_upgrade.forget_unavailable_cites --apply` 清掉，默认 dry-run、带备份；
  读取侧仍认这些旧条目，过期后自动重问）；④**连续 404 有刹车**：连续
  `CITE_404_STREAK_PAUSE`(3) 个对话框重试后仍 404 → 暂停 `CITE_PAUSE_SECONDS`(15 min)
  再试；暂停 `CITE_PAUSE_LIMIT`(3) 次仍未恢复 → 抛 `CiteDialogsStalled`，`run()` 保存收尾、
  结束本轮（"现在不是抓它的时机"，而不是继续在坏窗口里打）。
- Writes consolidated citation outputs to `author_<ID>_paper_citations.json` and `.xlsx`
- **Writes the product per paper**（用户定"按论文条目写入"）：每篇抓完，`_checkpoint_paper`
  就把这一篇的 `pub` / `citations` / `_fetch_state`（以及 `citation_index`）写进
  `author_<ID>_paper_citations.json` 本身（原子写，其余论文的条目一个字节不动）。所以中断
  （含 `kill -9` / 断电）之后产物里每篇都是最新的，下一轮直接续跑，不必重抓。
  它与被删除的 `*.partial.json` 不同：**写的是下游真正读的那份产物**，不是第二份副本。
- Writes each paper's `papers/<NNN>-<slug>/citations_meta.json` **main body**
  (`code/citation_index.py::write_citations_meta_for_pub`, called from checkpoint and
  `_save_output`): stage-1 fields only, index-ascending, idempotent, stage-2 download fields and
  retired indices carried over untouched. `venue_key` / `venue_key_source` are **derived**: the
  writer **recomputes** them with `shared.venue_key.citation_venue_key()` instead of copying
  whatever the row carries, and **never downgrades** — a stored `normalized` key survives when the
  recompute only reaches `raw`, as long as `same_citation_identity()` says it is the same citation
- Writes `citations/<NNNN>-<slug>.json` for every citing entry that carries `cite`
  (`write_cite_files`; same numeric prefix as the PDF in the same directory). Entries without a
  dialog produce no file; an unchanged payload is not rewritten
- Recomputes the derived keys for **every** row right before serialising
  (`citation_identity.refresh_venue_keys`), so a row restored from the previous output still gets
  its key back. A venue Scholar **truncated** (`…`) gets a key **only** through the explicit
  `TRUNCATED_VENUE_PREFIXES` table (`Advances in Neural …` → NeurIPS, `Proceedings of the AAAI …` →
  AAAI, `Expert Systems with …` → its journal); everything else truncated gets **no key**
- Runs the papers-directory maintenance after the profile step and before fetching (create
  directories + `paper_meta.json`, rename by the new ranking, merge same-work directories, archive
  vanished papers), then checkpoints each paper and saves the final product

### 引用编号与 `citations_meta.json`

- 编号在**首次分配后永不改变**：身份命中既有记录逐字沿用；新引文取
  `max(已用 ∪ 已退役) + 1`；消失的引用进 tombstone，编号永不复用。
- 身份判定委托 `shared.identity.same_citation_identity()`，与下游四处同一份判据；上游
  `cites_id` 换代或 Google 合并 cluster 时，两侧的"同一条"判断不会漂移。
- `citations_meta.json` 的主体只含阶段 1 的字段（`index` / `cites_id` / 书目字段 / `venue_key*`），
  按 `index` 升序；阶段 2 的下载字段、`fail_reason`、运行期 `_` 前缀事实与退役编号原样保留。
- 无真实 `cites_id` 的行在写主体时补合成键（见「跨仓库共享件」）。

### Citation fetch strategy

Status is derived from **current counts and diagnostics**, not from persisted `complete` flags:
- Direct mode: skip when `num_citations_seen >= scholar_total`
- `stalled`: still incomplete, but `STALL_AFTER_ATTEMPTS` **completed** traversals in a row
  obtained the same number of rows (`fetch_outcome_signature` counts the rows seen and deliberately
  ignores Scholar's targets, which drift between runs even when the listing does not), so retrying
  cannot help — the list does not contain what Scholar claims (e.g. Scholar lists fewer citations
  than it reports). The threshold is **1**: one full walk that lands short parks the paper, so
  "the paper that was just fetched" is not walked again. Only an attempt that **walked every page**
  is folded into the counter (`complete_fetch_attempt`, plus no year record reporting
  `partial_resume_completed`), so a blocked or intentionally shortened run neither counts as a
  repeat nor wipes one. Cleared when the paper's Scholar count grows (`scholar_changed`); force a
  retry with `--fetch-mode force`.
- Per-year bookkeeping (`short_attempts` / `stalled` / `stalled_probe`) must survive
  `PaperFetchState.to_dict()` → `_normalize_year_records()`: that rebuild is the **second writer**
  of every year record, and dropping these fields there would make the "rescan once, then treat the
  probe as stale" rule write-only. `cached_total` in a year record is the number of rows **on disk**
  (`output_state.resync_year_record_counts()`, called from `_build_entry`), while `seen_total` stays
  the fetch-time fact the completeness rule reads.
- A year whose re-scan returns **fewer rows than the cache** keeps the cached rows and only appends
  identities it has not seen — but for a row present on both sides it **merges the fresh row's
  `cite` / `cid` into the cached one** (`_merge_cite_into()`): the Cite dialog is fetched **per
  page**, i.e. for the freshly fetched rows, so dropping those rows outright would throw that data
  away. Only `cite` (when the cache has none or fewer formats) and `cid` (when missing) are taken;
  identity fields and every other field stay as the cache has them, and nothing is ever downgraded.
- Year mode: skip only when **every** year covers its own probe (`seen_total >= histogram_count` in
  the per-year records) *and* the aggregate holds. The aggregate alone is not enough: years whose
  bucket exceeds the probe prop up `histogram_total` and can hide a short year, so a paper with
  unfetched citations would be skipped forever.
- `--fetch-mode {rough,normal,exact,force}` controls re-fetch aggressiveness
- `scholar_changed` flag triggers re-fetch when Scholar count changes even for complete papers
- `is_complete()` and `completeness_diag()` share `_infer_strategy_and_summary()`
  (`code/output_state.py`): when `fetch_strategy` is absent it is inferred from
  `year_fetch_diagnostics` / `year_records`, and a missing diagnostics summary is rebuilt from
  `year_records`. They must never disagree — a `direct` fallback in one and a `year` inference in
  the other would produce a misleading "direct: diagnostics summary absent — will re-fetch" line.

Papers with `>= YEAR_BASED_THRESHOLD (50)` citations switch to year-by-year fetch mode:
- Supports resume via `partial_year_start` offsets, which are persisted with the paper's fetch
  state (a `completed_year_segments` set only avoids re-walking a year inside the same run — it is
  deliberately not carried across runs). An offset is a **position in a mutable list**, so if
  Scholar inserts rows above it between runs the resume can walk past them and the year stays
  "N short" forever. A year whose `short_attempts` (how many consecutive attempts came back under
  Scholar's own count, kept in `year_fetch_diagnostics`) reaches `RESCAN_AFTER_SHORT_ATTEMPTS` is
  therefore re-scanned **from page 1** once, relying on identity-key dedup instead of the offset
  (`_resume_start_after_short_attempts()`); a full attempt resets the counter. If that re-scan comes
  back with the **same** count, the listing and Scholar's histogram simply disagree, so the year is
  marked `stalled` (with `stalled_probe`, the claim it disagreed with): it stops being re-scanned,
  its gap counts as **0** (`year_record_effective_histogram()` also keeps it out of the summary's
  `histogram_total`, otherwise the paper would stay `partial` forever) and it is skipped on later
  runs — until the probe grows past `stalled_probe` or a `--fetch-mode force` retry
- **The plan's start year only widens to cache years the probe covers**: the cache can contain
  dirty years (e.g. `1907` / `2050` / `2090`), and widening to them would emit one `as_ylo=` request
  per year while the histogram has no rows for them. `cache_year_floor()` therefore only widens
  inside `[min(histogram), max(histogram)]`; years outside are counted by `out_of_range_cache_years()`
  and reported as WARN. When the probe is unavailable, the fallback is the oldest cached year.
  The floor must be computed from the counts the probe has **just** written into `year_ctx`
  (`fresh_probe_counts()`): the local variable captured at the top of `fetch_by_year()` is the
  pre-probe snapshot and would silently restore the old behaviour. Regression coverage:
  `PlanUsesTheFreshProbeCountsTests`.
- Oldest→newest year order
- Year range is determined by `probe_citation_start_year()` via the Scholar histogram DOM
- **Every citation-list page records its response shape** at DEBUG:
  `page shape: status=200 bytes=195.6 KB tail_ok=yes next=yes rows=10 markers=none`.
  `bytes` clearly small / `tail_ok=no` → the response was truncated (network/proxy, worth
  re-fetching); `markers` containing `captcha` / `unusual_traffic` → Scholar is rate-limiting (cool
  down, do not re-fetch); everything normal but too few rows → Scholar really returned a short list.
- **A walk that comes back clearly short is retried once, and only once — from the tail**: in year
  mode `BatchFetchSession.run(min_rows=probe - start)`; if `walked < min_rows`, the session keeps every
  row it already has and resumes from `walked - SHORT_WALK_REWIND_ROWS` (20 = the last two pages,
  `_resume_offset`), skipping those re-fetched rows (`_resume_skip`) instead of appending them twice.
  A premature end usually means one page in the tail came back empty without a "no results" banner
  (a 100/213 walk ends that way), so restarting from page 2 would re-download hundreds of pages to fix
  one empty response. Only a walk that collected nothing starts over. Two identical results mean the
  end; the existing `short_attempts → rescan from page 1 → stalled` machinery decides when to park.
  Direct mode passes no `min_rows` (a short page there is the normal end of the list).
- **A zero-row page without a "no results" marker is fetched once more**: `patched_load_url` retries
  the same URL when `empty_page_worth_retrying()` is true (results shell present, zero rows, no
  `no_results` marker); a second page with content is used, and a second zero-row page is taken as
  the end of the list (with an explicit log line). Only one retry, never a loop. Pages Google
  explicitly reports as having no results are not retried.
- An interrupted fetch persists the in-run snapshot (citations fetched so far + the live probe) when
  the output is written, so a short year stays visible on the next run instead of the paper looking
  complete.

### scholarly patch layer (`code/scholarly_session.py`)

Patches `scholarly` internals rather than treating it as a black box:
- Replaces HTTP/1.1 sessions with `httpx` HTTP/2 clients. TLS-impersonation libraries (`curl_cffi`)
  are deliberately not a dependency: Scholar's `/scholar?cites=` answers 429 for every
  impersonation target, while `httpx` works.
- Injects browser-like headers (Chrome/145, sec-ch-ua-*, Referer chain). When a browser cURL is
  injected, the whole browser fingerprint (`user-agent` + `sec-ch-ua` + `sec-ch-ua-full-version-list`)
  is adopted from that cURL as one coherent unit, so it always matches the browser that issued the
  cookies.
- Enforces randomised 45–90s waits before every request — **except the first page of a run and the
  first page after fresh cookies are injected** (`total_page_count == 1` / `curl_page_count <= 1`):
  no earlier request with those credentials exists to space apart from, so waiting there only wastes
  time.
- **Every 100 requests it rests for 2 hours**（用户定）：口径是**请求总数**
  （列表页 + Cite 对话框 + 详情页），不是页数 —— 对话框占一次抓取 60–80% 的请求量，按页算
  等于放过它们。`long_rest_due()` 判 `total_page_count % LONG_REST_EVERY_REQUESTS == 0`，
  按 `delay_scale` 缩放（`--accelerate` 照样生效），日志一行
  `Long rest after N requests (2.0 h)...`。`LONG_REST_EVERY_REQUESTS = 0` 可关。
- Takes mandatory short breaks every 8–12 **citation-list pages** to reset Scholar's rate-limit
  window. The counter is `ctx.citation_page_count`: `total_page_count` counts **every request** (a
  list page is followed by 0–10 Cite-dialog / detail-page requests), so using it for breaks would
  collapse to "a break every 1–2 list pages". The no-wait-after-injection rule still uses
  `total_page_count` (there the raw request count is what matters). A successfully solved captcha
  resets the break counter (`reset_break_counter()`); only the break window moves, the
  session-refresh counter is left alone.
- **Charges 30% of the normal delay on the detail-style paths**（用户定：45–90 s 的列表页等待
  对应 13.5–27 s 的 Cite 对话框 / 详情页等待）。这些路径一次抓取要打几千次，是"被拉黑"的
  主要来源，所以比例从 10% 提到 30%。
  `patched_get_page()` sleeps `rand_delay(ctx.delay_scale * detail_path_delay_factor(url))`, where
  the factor is `code.config.DETAIL_PAGE_DELAY_FACTOR` and `detail_path_delay_factor()`
  (`code/scholarly_session.py`) is the **single** classifier deciding which URLs are "detail-style" —
  the publication detail page (`view_op=view_citation`) and a citing entry's Cite dialog
  (`output=cite`) — versus everything else, notably citation-list pagination, which keeps the full
  45–90s. `--accelerate` multiplies on top; neither `venue_detail.py` nor `cite_format.py` may define
  a delay of its own (guarded by `tests/test_scholar_patch.py`).
- Performs soft session refresh every 10–20 pages (preserves cookies, clears the `got_403` flag).
- **Google's JavaScript robot check counts as a block, not an empty list**: the page
  answers **200** with no result rows at all — it carries `#gs_captcha_ccl` /
  `#gs_captcha_f` (a reCAPTCHA form) and the text "Please show you're not a robot".
  `is_js_robot_check()` detects it (DOM anchor first, text phrases as fallback) and
  `patched_get_page` raises `ScholarBlockedRedirect` with an explanatory message, so
  `PageVisit` cools down — and in interactive mode asks for cookies from a browser
  that passed the check. Without this the parser saw "a citation list with zero
  rows", the year was taken as finished, and no prompt was ever shown.
- Records the **HTTP status** of every response (`SessionContext.last_response_status`, from an
  httpx `response` event hook; cleared before each request). scholarly throws the status away — it
  treats 404 as "retry with the same session" and ends in the same `MaxTriesExceededException` as a
  captcha or a 429 — so this hook is the only place it survives for `PageVisit`. The same hook also
  records **where the request landed** when Google bounces it to its `/sorry/` block page:
  `block_redirect_target()` keeps scheme+host+path only (the interstitial's `q=` token is dropped),
  and `patched_get_page` raises `ScholarBlockedRedirect` after the call. The redirect is treated as
  the block even when the interstitial answers **200** — otherwise a CAPTCHA page reaches the parser
  as "a citation list with no rows" (or as "no such record" on a detail page) and the run keeps
  pacing a flagged session.
- **The "retry via the primary session" hop is short-circuited**: scholarly's `_get_page` ends with
  `return self._get_page(pagerequest, True)` when the `citations?` session fails — a proxy-pool
  fallback. Both of our sessions share one proxy, one cookie jar and one header set (scholarly's own
  proxy API is not used, because it does not work with httpx 0.27), so that hop only re-sent the same
  URL: an extra request, an extra full wait, and the log showed the same `Request URL` twice.
  `patched_get_page(premium=True)` now raises `MaxTriesExceededException` immediately, handing the
  failure to `PageVisit`'s retries / cooldowns / cURL prompt.
- Verifies every patch hook point at the end of `patch_scholarly()` and raises when a scholarly
  upgrade made one of them ineffective (see `assert_scholarly_patches_active`).

All patch state is held in `SessionContext`; per-paper cross-run state is in `PaperFetchState`
(`code/output_state.py`), and runtime fetch state is in `YearFetchSession` (`code/fetch_session.py`).
Dispatch on that type through `to_paper_fetch_state()` instead of scattering
`isinstance(..., PaperFetchState)` checks around the caller.

### Interactive recovery (`code/interactive.py`)

- `--interactive-captcha`: user pastes a browser "Copy as cURL"; cookies and allowlisted headers are
  injected into the patched scholarly sessions
- In interactive mode: retries indefinitely
- In non-interactive mode: waits up to 24h prompting the user to switch proxy and type `ok`
  (that window is scaled by `--accelerate` like every other wait)

## Module dependency map

```
code/scholar_citation.py  (orchestrator + PaperCitationFetcher — python -m code.scholar_citation)
  ├── code.config                 (centralised constants — all tunables)
  ├── code.lib_env                (env staging for third-party libs + NO_PROXY normalise + proxy bridge)
  ├── code.common                 (utilities, backward-compat re-exports from config)
  ├── code.log_utils              (structured logging — setup_logging, run_log)
  ├── code.author_fetcher         ← code.common, code.log_utils, code.profile_io, code.pub_info, code.venue_detail
  ├── code.profile_io             ← code.common, code.log_utils, code.pub_info
  ├── code.pub_info               ← code.citation_cache, code.venue_detail, code.shared.venue_key
  ├── code.venue_quality          (pure: is_complete_venue / is_preprint_venue / full-name check)
  ├── code.venue_detail           ← code.common, code.log_utils, code.venue_quality
  ├── code.cite_format            ← code.common, code.log_utils, code.page_visit, code.venue_quality,
  │                                  code.shared.venue_key, code.shared.identity
  ├── code.citation_cache         (pure year/diagnostics functions)
  ├── code.citation_strategy      ← code.citation_cache
  ├── code.citation_identity      ← code.citation_cache, code.shared.venue_key
  ├── code.citation_io            ← code.citation_cache, code.citation_strategy
  ├── code.citation_models        (dataclasses for citations, diagnostics, resume state)
  ├── code.output_state           ← code.citation_io, code.log_utils
  ├── code.citation_fetch         ← code.citation_cache, code.citation_models, code.common,
  │                                  code.fetch_session, code.log_utils
  ├── code.fetch_session          ← code.citation_models, code.output_state, code.common
  ├── code.page_visit             ← code.common, code.log_utils
  ├── code.scholarly_session      ← code.common, code.config, code.log_utils, code.page_visit
  ├── code.interactive            ← code.scholarly_session
  ├── code.citation_index         (index allocation; citations_meta / cite files)
  ├── code.papers_maintenance     ← code.shared.papers_layout, code.shared.stage_lock
  └── code.cli  [lazy import]     ← code.common, code.author_fetcher
                                     (parse_args only; the entry point is code.scholar_citation:main)
```

No circular imports. `code/scholar_citation.py` imports `parse_args` from `code.cli`, which therefore
contains only the argparse definitions (no scholarly import) and no second copy of the workflow.

## Testing notes

Tests are in `tests/` and require no network or real Excel library (all external dependencies are
stubbed in `tests/conftest.py`).

- **`tests/conftest.py` 必须先于任何 `code.*` 被 import**：它用 `sys.modules.setdefault` 安装
  `scholarly` / `openpyxl` 的桩。某个测试模块若先 import 了 `code.*`，真包就赢了 `sys.modules` ——
  `FetcherTestCase` 把 `_DummyNav` 写进没人读的对象、xlsx 断言拿到真 `Workbook`，于是整套断言失败，
  而每个模块单独跑却全绿。conftest 检测到这种顺序会**直接抛 `RuntimeError`** 并指名那个模块。
- `FetcherTestCase` base class provides a fully-initialised `self.fetcher` with all runtime
  attributes zeroed out and common method stubs in place.
- Each test file covers one functional area; see `_work_notes.zh.md` 项目结构 for the mapping.
  `tests/conftest.py::RunLogDebugCase` lowers the log threshold to DEBUG for tests that assert
  request/page detail.
- `tests/test_no_env_config.py` is a **source-level guard**: with the sole exception of
  `code/lib_env.py` it fails if any module under `code/` reads configuration through `os.getenv` /
  `os.environ.get` / `os.environ[...]`.
- `tests/test_proxy_config.py` covers config-file loading and the CLI > file > system-default proxy
  resolution (temporary files + patched environment, never the developer's real
  `crawler_config.json`).
- `tests/test_direct_fetch.py` covers direct-mode fetching (progress saves, early stop, resume
  offsets, prefix merging, dedup) and that a mid-scan snapshot persists `fetch_strategy: "direct"`
  **and** its `direct_fetch_diagnostics`, otherwise the reloaded state infers `year` from the year
  diagnostics every paper carries (whose direct-mode `histogram_total` is 0) and an interrupted fetch
  reads as **complete** — the remaining citations would never be fetched. The test asserts the
  snapshot's fields *and* that `PaperFetchState.from_dict(...).need_fetch()` still says yes.
- `tests/test_venue_detail.py` covers the `venue_full` step hermetically: token parsing (stored field
  / legacy URL / no link at all), detail-page field pairing (the `<style>` trap, reordered rows,
  missing venue label, whitespace collapsing), the new fields surviving a profile round trip with
  `venue` byte-identical, graceful degradation (request error, page without a field table, interrupt)
  and cache/resume behaviour.
- `tests/test_cite_format.py` covers the Cite-dialog step hermetically: `cid` persistence from
  `url_scholarbib` (including the `info:None:` placeholder and rows written before the field existed)
  with `title`/`authors`/`venue`/`year`/`url`/`cites_id` byte-identical, label↔cell pairing for the
  five formats (the `<style>` trap, reordered/duplicate/unknown labels, missing cells,
  `&quot;`/`&amp;` decoding, inline `<i>` spacing), the Chicago parser that yields `venue_text` /
  `authors_full` and corrects the entry (`tests/test_cite_format_parts.py`), graceful degradation
  (request error, captcha page, interrupt) and cache/resume behaviour, plus the wiring: the CLI flag
  is unspecified by default (`None`) and resolves through `code.config.resolve_cite_formats()`
  (CLI > `crawler_config.json` > default on), the fetcher defaults to on, `_save_output` writes the
  field unless the step was opted out, and that write also refreshes `venue_key` from the dialog's
  Chicago venue (`cite.venue_text`: a known conference becomes `EMNLP2025`-style normalized).
- `tests/test_venue_quality.py` covers the "do not fetch it again" rule hermetically: the
  preprint/truncation/missing predicates, the full-name check (including name particles and
  initial-only lists), the four citation-level combinations, and both paths' skip behaviour — a
  skipped item issues **zero** requests, writes **no** cache entry and leaves
  `venue`/`authors`/`cites_id` byte-identical, while a mixed list still requests exactly the item
  that needs it (a cached dialog is reused so a re-run cannot drop `citation['cite']`).
- `tests/test_scholar_patch.py` pins the pre-request pacing with a stubbed sleep: the scale handed to
  `rand_delay` is `delay_scale * DETAIL_PAGE_DELAY_FACTOR` for the detail/Cite URLs and plain
  `delay_scale` for citation-list and profile pages (`--accelerate` stacking included), and a
  source-level guard asserts the two modules define no delay of their own. It also pins the
  **Referer of a Cite dialog**: the dialog must be sent with the page its row was seen on
  (`ctx.last_scholar_url`), not with the previously loaded list page, while other requests keep
  whatever referer they had.
- `tests/test_venue_key.py` covers the normalized venue identity: acronyms and spelled-out
  proceedings forms, Findings as its **own** venue (different from its host conference),
  TACL-not-ACL, longest-match-wins (Nature sub-journals, CL vs ACL), the two deliberate spelling
  merges (`NIPS` → `NeurIPS`, `ACMMM` → `ACM MM`), **journals carrying no year while conferences
  do** (`TACL`/`TASLP` vs `ICASSP2025`), **every preprint server keying on its own name** (arXiv
  forms including the truncated one, plus bioRxiv / medRxiv / SSRN / TechRxiv / Research Square /
  preprints.org, while a bare "Preprint" stays raw), **a host name not matching an acronym**
  (`kdd.org` / `iclr.cc` stay raw), **no bare "Findings" venue** (an unnamed host resolves to
  Findings of ACL while a named one keeps its host), **the Cite dialog's Chicago venue winning over
  the truncated list page** (`citation_venue_key`: legacy bibtex fields, then `cite.venue_text`,
  then the list page; field precedence, placeholder and unrelated fields ignored, the five formatted
  strings deliberately not parsed, no regression without a `cite`), the extended table (ICASSP /
  IJCNN / PAKDD / LREC / NLPCC), `CCMT`/`CWMT`/`WMT` staying apart, the two spellings of one Chinese
  journal collapsing onto one key, the year rule (venue string first, record year as fallback),
  unknown venues keeping their raw text, missing venues getting **no** key, plus the two writers
  (`PubInfo` and `extract_citation_info`) and that `PubInfo` prefers `venue_full` and never reads a
  stale stored key back.
- `tests/test_shared_venue_key.py` 守住 venue_key 判据的**唯一实体**：`code/venue_key.py` 是薄再导出
  （逐个 `assertIs` 共享件、源码级断言没有实现副本、体量 < 60 行）、`code/shared/` 无
  `__init__.py`（PEP 420 命名空间包）、功能测试走共享路径；以及 `venue_key_name()` 的去年份规则
  （会议剥年份 / 期刊·预印本·裸名原样 / 非字符串与空 → `''` / 越界年份与数字粘连不动 / 与
  `venue_key()` 的往返）。
- `tests/test_normalize_venue_key.py` covers the data-upgrade tool: dry-run writes nothing (no backup
  directory either), `--apply` fills both fields while every other key stays byte-identical, the
  previous version is copied into the backup directory with a manifest, a second run is a no-op, the
  review report lists each distinct unrecognised venue with its count, `--author` scans only that
  author, a missing/empty output directory is an error rather than a silent pass, and a re-run that
  finds nothing to do leaves no empty backup directory behind.
- When changing year-based fetching, resume behavior, or CLI flags, update the corresponding test
  file first rather than adding integration coverage.

## venue_key

- **会议 = 规范名 + 年份**（`ACL2026`、`Findings of ACL2026`、`ICASSP2025`）；**期刊 = 缩写名、
  不带年份**（`TACL`、`TASLP`、`Nature Machine Intelligence`，期刊是连续出版物、不是年度届次）；
  **预印本 = 服务器名**（`arXiv` / `bioRxiv` / `medRxiv` / `SSRN` / `TechRxiv` / `Research Square` /
  `preprints.org`，不带年份；与已知会议同时出现时会议优先；只写 `Preprint` 的仍是 raw）。
- **没有裸的 `Findings` venue**：未注明主会的
  `Findings of the Association for Computational Linguistics` 归 `Findings of ACL`，注明的各归其主。
- **宿主名不算 venue**（`kdd.org` / `iclr.cc` 不命中 `KDD` / `ICLR`）。
- 词表 `VENUE_CANONICAL` 共 **167 条（83 会议 + 84 期刊）**；匹配按文本长度降序，所以 TACL 不会被
  ACL 吞掉、`CCMT` / `CWMT` / `WMT` 各归其位；`NIPS` → `NeurIPS`、`ACMMM` → `ACM MM`；`&` 与
  `and` 在匹配时视为同一写法（调用方拿到的原串与 raw 键一字不改）。
- 年份取 venue 串里第一个 4 位年份（会议届次），没有才退回记录自身的 year。
- **引文侧优先用 Cite 对话框里的 venue**：优先级 **存量 `cite.bibtex` 字段（只服务老数据）→
  `cite.venue_text`（Chicago 解析出的未截断 venue）→ 列表页 `venue`（被截到约 25 字符）**；
  `code.shared.venue_key.citation_venue_key()` 是唯一入口。只解析 Chicago 的带引号格式（APA /
  Vancouver 没有确定边界，故意不解析）。
- **认不出的 venue 先用它自己的串当 key**（`venue_key_source='raw'`；认出来的写
  `'normalized'`）；占位值（空 / `NA` / `N/A`）**不给 key**（"没有 venue" 不是 venue）。
- `venue_key_name(key)` 从 key 取「发表地名称」——只去掉尾部 4 位年份（`ACL2026` → `ACL`、
  `Findings of ACL2026` → `Findings of ACL`；期刊 / 预印本 / 裸会议名原样），是「剥年份」的唯一
  实现。

## Dependencies and runtime assumptions

- `httpx[http2]==0.27.2` is pinned for compatibility with the current scholarly integration; the
  `[http2]` extra is what installs `h2`, without which `httpx.Client(http2=True)` raises
  `ImportError`.
- **本程序不要求任何环境变量**。代理优先来自 `--proxy-url` / `--no-proxy`，其次
  `crawler_config.json` 的 `proxy_url` / `no_proxy`（本机文件，gitignored），最后才回退到系统的
  `https_proxy` / `http_proxy` / `no_proxy`（HTTP 生态通用约定，`httpx` / `requests` 自身也读）。
  解析后的值会写回进程环境，因为 httpx 只认环境代理。`scholarly` 自带的 proxy API 有意不使用
  （与 httpx 0.27.x 不兼容）。
- `NO_PROXY` / `no_proxy` 在 `scholarly` 导入前被规范化（`[::1]` → `::1`）：httpx 0.27.2 在有
  括号 IPv6 条目时连客户端都构造不出来。见 `code.lib_env.sanitize_no_proxy`（`code.common`
  仍按原路径 re-export）。
- `code/` 下除 `code/lib_env.py` 外不得出现 `os.getenv` / `os.environ.get` / `os.environ[...]`，
  由 `tests/test_no_env_config.py` 守卫。
- The default output directory is `./output`; state is maintained in memory during runs.
- All development happens on `main` branch.
