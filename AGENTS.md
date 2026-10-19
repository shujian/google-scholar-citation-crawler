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
- Every citing entry's five citation formats are resolved by default (one request per entry). **BibTeX is no longer fetched (abandoned 2026-10-15)**: its signed link is minted per dialog render and is unusable from a cache. `--no-cite-formats` skips the whole step: `python -m code.scholar_citation --author YOUR_AUTHOR_ID --skip 0 --limit 1`
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
  （Y-9 ③：把存量 `citations_meta.json` 的**主体**对齐到抓取产物 —— 只重写不一致的、
  走 `write_citations_meta()` 这一个写者、下载字段与退役编号原样保留、默认 dry-run）
- Example script: `code/examples/run_example.sh`

## 论文选择：按作者抓取，没有 `--papers`

阶段 1 **不参与** 3.a.1 / 3.a.2 / 3.b 的 `-p`/`--papers` 约定：crawler 操作的是 Google Scholar
上的论文列表，没有本地 `papers/` 目录，也没有跨仓库稳定的论文编号可选。范围控制只有位置切片：

- `--skip M --limit N`：在「按引用数降序」排序后的 publication list 上取第 M 篇起的 N 篇，
  覆盖"先试几篇"与"从中间继续"两种场景；是否重抓由 `--fetch-mode` 决定。
- **按 `pub.no` 选择论文的 `--papers 1,3,5-8` 已评估并否决**（2026-07）：它只能在 Phase 1
  拿到 publication list 之后、Phase 2 之前过滤，且必须与 `--skip`/`--limit` 互斥，收益不足以
  再加一条 CLI 约定。历史文档里出现过 `--papers` 的用法示例，**那是错的，不要照做、也不要再实现它**。
- 引用级过滤（`-p 12(50,141,300)`）同样不适用：Scholar 的引用列表页无法跳过中间的条目，
  crawler 只能整篇抓取；若将来确实需要缩小输出，只能做"输出裁剪"而非抓取优化
  （评估已并入 `issues.md` 第二节 3b —— 定案**不实现**，原 `suggestion.md` 已按约定删除）。

## 目录治理与配置（2026-10-10）

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
    ├── examples/run_example.sh
    └── data_upgrade/              # 一次性数据修复/升级工具（**与主线入口分开**）
        ├── __init__.py
        ├── fix_*.py               # python -m code.data_upgrade.fix_*
        └── normalize_venue_key.py # python -m code.data_upgrade.normalize_venue_key
```

- **产物位置不变**：`output/author_<ID>_paper_citations.json` / `.xlsx`、`author_<ID>_profile.json`
  仍是根 AGENTS.md 数据契约规定的路径，不要移动。
- **`output/author_<ID>_papers/` 是整条流水线共用的论文数据树（2026-10-15 起）**：物理位置在本仓库
  的 output 下（27 GB 级，gitignore），阶段 2 / 3.a.1 / 3.a.2 / 3.b / 阶段 4 **都不直接拼这个路径**，
  而是经各自的 `papers` 符号链接访问（整条链见根 `AGENTS.md`「仓库地图」的共享数据段）。
  本阶段负责**创建**它（`code.config.papers_root()` / `ensure_papers_root()`，`main()` 每次运行都
  幂等确保存在，只建不删）—— 这是"谁拥有物理位置谁负责建"的落点；论文子目录与 `cite` 文件的
  创建/编号按 `KNOWN_ISSUES.md` Y-6 的分步计划**后续**搬过来（当前仍在阶段 2 的 `preflight_repairs`）。
- **日志与运行期中间产物**一律进 `output/`（含 `output/logs/`）。
- **不依赖任何环境变量**：旋钮走命令行参数，成组配置走项目根 `crawler_config.json`，
  优先级 **命令行参数 > 配置文件 > 代码内默认值**。布尔开关同理，例如 Cite 对话框那一步：
  `--cite-formats` / `--no-cite-formats` > 配置键 `cite_formats` > 默认 **开启**。`--config PATH` 可指向别的配置文件；
  默认文件缺失不算错误，显式指定的文件缺失/损坏会直接报错退出。
- `code/lib_env.py` 是 `code/` 下**唯一**接触 `os.environ` 的模块：它只为第三方库摆设环境
  （selenium 遥测开关、httpx 0.27.2 需要的 `NO_PROXY` 规范化），并把解析后的代理写进环境
  供 httpx 使用（httpx 只认环境代理）。`tests/test_no_env_config.py` 会扫描源码守住这条边界。
- `scholar_cache/` 已是历史遗留（无代码再写入）：现存残留移到 `output/legacy_scholar_cache/`，
  `.gitignore` 保留该条目以防旧版本重新生成。

## Project structure

详细项目结构见 `_work_notes.zh.md` 的「项目结构」节（含各模块中文说明），对外简介见 `README.md` 的「Development Notes → Project Structure」节。

简况：`code/` 有 23 个支撑模块（author profile 层、citation data 层、fetch engine 层、CLI、配置与日志，
其中 `lib_env.py` 管第三方库环境、`venue_detail.py` 管出版详情页的完整 venue、`cite_format.py` 管施引
条目的五种引用格式（**BibTeX 已弃用 2026-10-15**）、`venue_quality.py` 判「venue 是否已经完整/是否只是预印本源」、
`venue_key.py`（**薄再导出**）把 venue 归一成身份键（会议 = `名+年份`、期刊 = 缩写名、
预印本 = 服务器名）—— 规则的**唯一实体**在 `code/shared/venue_key.py`（本仓库是宿主），
阶段 2 / 3.a.1 / 3.a.2 / 阶段 4 经各自的 `code/_shared.py` 消费同一份）；
`tests/` 里有跨仓库守卫（`test_lib_env_shared.py` 守住「NO_PROXY 消毒引擎只有一份，在 `code/shared/lib_env.py`」；
`test_shared_venue_key.py` 守住「venue_key 判据只有一份，在 `code/shared/venue_key.py`」）。

### 新增模块

- **`code/config.py`**：集中配置。所有可调优常量（延迟、阈值、路径）的单一入口，并负责
  `crawler_config.json` 读取与 `resolve_proxy_config()`（命令行 > 文件 > 系统默认）。
  `code/common.py` 中的常量已迁移至此并保持向后兼容 re-export。
- **`code/log_utils.py`**：结构化日志系统。`setup_logging(verbose, log_file)` 配置 console + 可选 file handler；活入口 `code.scholar_citation.main()` 只启用 console handler（它写 `sys.stdout`，也就是 `TeeStream`），因为再挂一个 FileHandler 会成为同一文件的第二个写者并互相覆盖。现有 `print()` 调用仍通过 `TeeStream` 写入日志文件；新增代码应使用 `run_log`（见下）。

### 运行日志的层次与级别（2026-10-16）

**问题**（实测 6 个日志）：一次 run 打 21 条 `=`×70 横幅（Phase 1 区域连续 6 条）；同一个 URL **相邻打两遍**
（`citation_fetch` 的 `URL:` + session 层的 `Request URL:`，单日志 26 对）；同一件事三行
（`Year N done` / `compare:` / `status:`）；`Author ID:` ×4；缩进手写 2/4/6/8 空格；时间戳只出现在
部分行上；而 **`-v/--verbose` 完全无效**（它配置的 `logging` logger 没有任何抓取代码在用）。

**现在**：`code/log_utils.py` 的 `run_log`（`RunLog` 实例）是**所有结构性日志的唯一出口**，每行统一为

```
11:40:13 INFO  fetch     Year 1996 status: year_total=0, year_new=0, … | histogram=0, seen=0, …
└─ 时刻  └─ 级别  └─ 模块（左侧对齐）  └─ 每层 2 空格，层级 = run → phase → paper → year/page → 细节
```

- **级别**：`INFO` = 状态变化与结论；`DEBUG`（`-v`）= 每次请求/每页/每年明细；`TRACE`（预留）。
  `setup_logging(verbose=True)` 把 `run_log` 的阈值降到 DEBUG，所以 `--verbose` 现在真的改变日志内容。
  **唯一的例外是"等待"行（2026-10-19 用户定）**：`waiting 74s · elapsed 20s, 0 new citations,
  2 pages (3 this curl), 0 captcha solves` 是 INFO —— 两次请求之间的 45–90 秒是 run 里最长的静默，
  这行同时是**心跳**（带 elapsed/新增/页数/验证码次数）。压到 DEBUG 会造成"每次请求静默一分半、
  5–10 分钟长休反而有字"的错位。重试等待同口径：`waiting 74s (retry 1/2) · …`（INFO），
  而重试的**原因** `Error fetching … (retry 1/2)` 仍留 DEBUG。URL 也仍是 DEBUG。
- **模块标签**：`run / phase / identity / profile / venue / http / fetch / year / paper / plan / output / cite / summary`，
  宽度固定，消息列对齐。
- **`run_log.section(title, module=…)`** 取代横幅：一个阶段/步骤一行 `── title ────…`。
- **一个事实一行**：`URL:` 那行已删（URL 只在 session 层的 `Request URL:` 出现一次）；
  `Year N compare:` 的字段并入 `Year N status:`；`Author ID:` 只在 run 头出现（phase 标题里带作者）；
  零行的 `Page items:` 保留原文但升为 WARN（异常），正常页改由下面的**逐页进度行**承担。
- **逐页进度行（2026-10-19，用户要求）**：抓引用列表时每页一行 INFO
  `11:40:13 INFO  year      2025 · page 3 · 10 items`（direct 模式无年份 → `page 2 · 7 items`）。
  这是"现在抓到哪一年、第几页"的唯一来源 —— 上一版把 `Pagination (page N)` / `Page items: N`
  降到 DEBUG 后，默认级别下**看不到进度**了 ✗。判据在 `citation_page_progress()` /
  `citation_page_module()`（`code/scholarly_session.py`），页码取 URL 的 `start=`、年份取 `as_ylo=`。
- **迁移已完成（2026-10-19）**：`code/` 下**只有 `interactive.py` 还写 `print()`**（它是交互提示，
  不是日志），其余模块全部经 `run_log`；`log_utils.py` 里那一个 `print()` 是 `RunLog.write` 本身。
  原来 205 处 `print` / 62 处 `run_log` 的并存状态结束。
  `code/venue_detail.py` / `code/cite_format.py` / `code/profile_io.py` 保留 `print_fn` 接缝给测试，
  但默认值已换成 `line_logger(...)` / 各自的 `_log_*_line` 适配器（经 `run_log` 输出），
  所以"默认打印"也是同一种行形状。
- **测试**：`tests/conftest.py::RunLogDebugCase` 把阈值降到 DEBUG（测试会断言请求/页级明细），
  `FetcherTestCase` 已继承它；`setup_logging()` 会把阈值改回 INFO，所以**每个测试自己声明**，
  不能只在模块级设一次。
- **日志文件治理**：`output/logs/` 只保留最近 **`LOG_KEEP_RUNS`（30）** 次，更早的由
  `prune_run_logs()` 在 run 开始时**移动**（绝不删除）到 `output/logs/old/`；当前 run 的日志永远保留。
  2026-10-16 首次执行：87 → 30 个（57 个进 `old/`）。

- **`code/lib_env.py`**（2026-10-10）：第三方库环境摆设 + `NO_PROXY` 规范化 + 代理环境桥接，
  是允许出现 `os.environ` 的唯一模块（见上）。**2026-10-14 起它的 `sanitize_no_proxy()` 只是一层
  策略包装**：迭代/拆分/幂等/回写的引擎 `code/shared/lib_env.py::sanitize_entries()` 由阶段 1 提供，
  3.a.1 / 3.a.2 / 3.b 经各自的 `code/_shared.py` 消费同一个引擎（它们传「丢弃 + bool」策略，
  本仓库传「解括号 + 计数」，两侧行为与测试一字未改）。见根 AGENTS.md「跨仓库共享代码」。
- **`code/venue_detail.py`**（2026-10-14）：从出版**详情页**
  （`/citations?hl=en&view_op=view_citation&citation_for_view=<author>:<token>`）读**完整 venue**，
  写进 `pub['venue_full']`（+ `venue_full_source: "scholar_detail"`）。引用列表页给的 venue 是
  **截断**的；详情页字段是成对的 `gsc_oci_field`(标签) + `gsc_oci_value`(值)，解析按 DOM 配对
  （页面开头有一段 `<style>` 提到 `.gsc_oci_value{…}`，朴素正则会把 CSS 当值）。**绝不覆盖
  `pub['venue']`**：它参与 `code/citation_identity.py` 的元数据身份键 `meta\t{title}\t{venue}`
  与下游身份校验（K123/K125 同类教训），改写会让历史身份键漂移。抓取走**既有的 patched session**
  （`scholarly._Scholarly__nav._get_soup()`，与 year probe 同一入口），所以延迟/`--accelerate`/
  captcha/代理等待照旧；缓存 `output/venue_detail_cache.json` 按 token 记录已解析页面，可续跑。
  **`venue` 已经完整就不抓**（`code/venue_quality.is_complete_venue`：非空 + 不含 `…`/`...` + 不是
  预印本源）：跳过时**零请求、不写缓存**，记在 `venue_full resolved: … already complete N`。
- **`code/cite_format.py`**（2026-10-14）：从 Scholar 的 **Cite 对话框**
  （`/scholar?q=info:<cid>:scholar.google.com/&output=cite&scirp=0&hl=en`）读**五种引用格式**
  （MLA / APA / Chicago / Harvard / Vancouver），写进施引条目的
  `citation['cite']`（`{'mla': …, 'chicago': …, 'vancouver': …, 'venue_text': …, 'authors_full': …}`；**BibTeX 已弃用**）。**Chicago 给全作者
  全名、Vancouver 给 venue 全称 + 卷期页码**，正是引文列表页给不了的。`cid`（= 结果行的
  `data-cid`，scholarly 放在 `url_scholarbib`）由 `citation_identity.extract_citation_info()` **纯解析**
  （不发请求）随条目落盘，**与 `cites_id` 是两个概念**（后者是被引作品的 cluster，参与身份键）。
  解析按 DOM 配对（`th.gs_cith` + `td > div.gs_citr`），页面开头的 `<style>` 提到 `.gs_citr{…}`，
  朴素正则会把 CSS 当引用；**BibTeX 已弃用**：`scholar.bib` 的签名按渲染现铸、缓存里必然作废，不再下载。
  **字段改动只有一处**：写 `cite` 本身是增量；`title`/`authors`/`venue`/`year` 只会被
  `apply_chicago_correction()` 按 Chicago **修正**（审计在 `cite.corrected`），而 `url`/`cites_id`/`cid`
  **永远一字不动**（身份键，同 `venue_detail.py` 的教训）。**默认开启**（`--no-cite-formats` 可关）：每个施引条目一次请求，
  全库是数千次；缓存 `output/cite_format_cache.json` 按 `cid` 记录已解析页面，可续跑，失败不写缓存。
  写进 `citation['cite']` 的同时**顺手刷新** `venue_key` / `venue_key_source`（用 **Chicago** 解析出的
  未截断 `cite.venue_text`；对话框没给 venue 段时退回列表页 venue），所以"新数据的 venue_key 也是完整的"。
  **三种"不用抓"**（`code/venue_quality.citation_needs_cite_dialog`）：①**已保存的 venue 与本次线上
  venue 一致** → **零请求**（用户 2026-10-15：这条路径访问量大；比对见
  `venue_consistent_with_saved_bib()`：折叠后相等、或短的一侧（≥12 字符）是另一侧的前缀 ——
  线上串常被 Scholar 截断，保守起见宁可多抓一次）；②venue 已完整**并且** `authors` 已是全名形态；
  ③其余才抓。跳过零请求、不写缓存；
  但**已缓存的对话框仍会复用**，以免重跑把上一轮付过钱的 `cite` 从输出里抹掉。
  **第四种：Scholar 说"没有这个对话框"**（2026-10-16）—— `output=cite` 对某个 `cid` 回 **404/410**
  时（实测 `info:44BcOmzPt1MJ:scholar.google.com/` 返回 Google 自己的 404 页，而同分钟两条兄弟
  `cid` 都是 200 + 5 种格式；`scholar.google.com` 当时还对整条出口做 TLS 级拒绝，**两件事必须分开**），
  `code/page_visit.py::RecordUnavailable` 会**直接放弃这一条**：不重试、不冷却、不弹验证码提示
  （此前它和限流长得一样，每条白烧 20–35 分钟，或把整轮停在等人解一个不存在的验证码）。
  `cite_format` 把它记成**带时间戳的否定条目** `{'unavailable': true, 'status': 404, 'fetched_at': …}`，
  `UNAVAILABLE_RETRY_DAYS`（7 天）内不再问；过期后自动重问一次 —— **刻意不是永久判决**：句柄是
  Scholar 的，它对 cluster 的组织我们**还没有完全理解**（见 `issues.md`）。
- **`code/venue_quality.py`**（2026-10-14）：**「这条记录还值得再抓一次吗」的唯一判据**，被
  `venue_detail.py`（pub 级）与 `cite_format.py`（施引条目级）共用：`is_complete_venue()`
  （非空 + 无 `…`/`...` + 不是预印本源；缺失 ≠ 完整）、`is_preprint_venue()`（`arXiv` / `arXiv preprint
  arXiv:…` / `arxiv.org` / `Preprint` / `bioRxiv` / `medRxiv` / `SSRN` / `TechRxiv` / `Research Square` /
  `preprints.org`，以及空值/`NA`/`N/A`）、`has_full_author_names()`（作者串里至少一位的 given name
  长度 ≥ 3 且不是 `van`/`von`/`de` 之类介词 —— 列表页的形态是 `<首字母> <姓>`）。判据是**纯函数**，
  同一处定义、无环境变量；`venue_needs_detail()` 与 `citation_needs_cite_dialog()` 是两个调用点的
  薄包装。取舍见 `issues.md`：跳过的条目放弃了 Cite 对话框本可补上的卷期/页码。
- **`run.sh`**：便捷入口，自动检测 conda 环境后执行 `python -m code.scholar_citation`。

- **`code/shared/venue_key.py`**（2026-10-15，**跨仓库共享宿主**；`code/venue_key.py` 只是
  `from code.shared.venue_key import *` 的**薄再导出**，保住历史 import 路径）：
  **会议 = 规范名 + 年份**（`ACL2026`、`Findings of ACL2026`、
  `ICASSP2025`），**期刊 = 缩写名、不带年份**（`TACL`、`TASLP`、`Nature Machine Intelligence` ——
  期刊是连续出版物、不是年度届次；2026-10-15 用户定），**预印本 = 服务器名**（`arXiv`、`bioRxiv`、
  `medRxiv`、`SSRN`、`TechRxiv`、`Research Square`、`preprints.org`，不带年份；大小写不敏感子串匹配，
  所以 `arXiv preprint arXiv:…`、被截断的 `arXiv preprint arXiv …`、`arxiv.org/abs/…`、
  `researchsquare.com/…` 各落到自己服务器的 key；只写 `Preprint` 而没点出服务器的仍是 raw），
  **没有裸的 `Findings` venue**（2026-10-15 用户：Findings 一定挂在某个主会下；未注明主会的
  `Findings of the Association for Computational Linguistics` 归 **`Findings of ACL`**，
  注明的各归其主），供下游做论文去重/唯一性判断。
  词表起点是 3.a.2 已有的 `VENUE_PRESTIGE` + `VENUE_ALIASES`（51 会议 + 30 期刊），
  2026-10-15 按"EACL、ICASSP 等，同理类推"扩到 **167 条（84 会议 + 83 期刊）** ——
  新增的正是真实数据里未识别的那批（语音/机器翻译/NLP/ML 会议与常见期刊，含中文期刊与
  `中文信息学报` ← `Journal of Chinese Information Processing` 这类**同一刊两种写法**）。
  匹配算法照抄 3.a.2 的 `_venue_match`（多词子串 / 短全大写缩写要词边界 / 其它单名要整名），
  **但短缩写额外拒绝相邻的点**（否则 `https://www.researchsquare.com/…` 会被当成 `WWW` 会议、
  `kdd.org` 当成 `KDD`）—— 域名标签不是 venue；
  **`&` 与 `and` 视为同一写法**（2026-10-16）：匹配前两边都把 `&` 折成 `and` ✓ ——
  真库里 `IEEE Transactions on Knowledge & Data Engineering …` 本该命中 `TKDE`（词表就是
  `… Knowledge and Data Engineering`）却落到 `raw` ✗；**只折叠匹配用的两个操作数，调用方拿到的
  原串与 `raw` 键里的字面量一字不改** ✗。
  按文本长度降序尝试，所以 TACL 期刊不会被 ACL 会议吞掉、`CCMT`/`CWMT`/`WMT` 各归其位、
  Findings 是**独立于主会**的 venue。两处刻意的合并：`NIPS` → `NeurIPS`、`ACMMM` → `ACM MM`
  （同一会议不能有两个 key）。年份取 venue 串里第一个 4 位年份（会议届次），没有才退回记录自身的 year。
  **引文侧优先用 Cite 对话框里的 venue**：优先级 **存量 `cite.bibtex`（只服务老数据）→ `cite.venue_text`
  （**Chicago** 解析出的未截断 venue）→ 列表页 `venue`（被截到 ~25 字符）**；
  `code.shared.venue_key.citation_venue_key()` 是唯一入口。**只解析 Chicago 的带引号格式**：
  APA / Vancouver 没有确定边界，故意不解析（venue 夹在作者/标题/卷期之间，解析有把标题当 venue 的风险，
  而 key 要参与去重）。抓取时在写完 `citation['cite']` 之后立即刷新这两个派生字段，
  数据修复工具用的是同一个函数，所以"先抓的没有对话框、后补上对话框"的行在下次跑脚本时会自动升级。
  **认不出的 venue 先用它自己的串当 key**（`venue_key_source='raw'`；认出来的写 `'normalized'`）—— 这是可审阅的临时状态，
  数据修复工具会把所有不同取值连计数导出来，再由人决定往词表里补什么；占位值（空/`NA`/`N/A`）
  **不给 key**（"没有 venue" 不是 venue，`NA` 当 key 会把无关记录并成一条）。
  **与 3.a.2 的关系（2026-10-15 已落地）**：按工作区的共享代码约定，唯一实体就在本文件
  （宿主 = 阶段 1），四个消费方经各自的 `code/_shared.py` 注入后 `from shared.venue_key import ...`
  消费同一份 —— **不再有第二份副本**（各消费方有 `tests/test_shared_venue_key.py` 守卫）。
  **`venue_key_name(key)`**（本次新增）：从 key 取「发表地名称」——只去掉尾部 4 位年份
  （`ACL2026` → `ACL`、`Findings of ACL2026` → `Findings of ACL`；期刊 / 预印本 / 裸会议名原样），
  是「剥年份」的**唯一实现**：3.a.2 的 tier 查表与阶段 4 的展示名都调它。

- **`code/shared/papers_layout.py`**（2026-10-15，**跨仓库共享宿主**）：论文目录的**布局与编号** ——
  `plan()`（纯函数，算 `<NNN>-<slug>` 重排计划）/ `apply_plan()`（两阶段改名 + `manifest.json`，
  置换失败回滚）/ `merge_into()`（同作品两目录合并）/ `remap_progress()`（改 `progress.json` 的
  派生编号）/ `reconcile()` / `target_dir_name()` / `sanitize_filename()`（**文件名消毒的唯一实现**）。
  **内容 = 阶段 2 `code/renumber_papers.py` 的库部分逐字搬移**，本仓库是宿主（`papers/` 的物理位置
  在阶段 1，见根 AGENTS「仓库地图」）；阶段 2 的 `code/renumber_papers.py` 现在只是"再导出 + CLI"的
  薄模块。**只用标准库**：`MAX_FILENAME_LEN` / `OUTPUT_DIR` / `BASE_DIR` 这些消费方配置一律
  **调用时按需解析**（`_default_max_filename_len()` / `_default_output_dir()` / `_default_papers_base_dir()`），
  否则它无法被两个阶段同时消费。**远程可见性**：阶段 2 在 SSHFS 下靠 `<容器根>/stage1_code` 这条
  符号链接看到本目录（`code/_shared.py` 两处都试）。

- **`code/shared/citation_key.py`**（2026-10-16，**跨仓库共享宿主**，K144）：引用**身份键**的
  唯一实体 —— `paper_identity_key()`（源论文稳定标识）/ `synthetic_cites_id()`（
  `no_id:<归一化被引标题>|<年份>|<首作者姓>`）/ `synthetic_keys_for()`（同基础键多条追加
  `#doi:`/`#arxiv:`/`#url:<sha8>`，全缺才 `#pos<n>` 并告警）/ `resolve_cites_id()` /
  `is_real_cites_id()` / `normalize_url_for_key()` / `url_fingerprint()` /
  `normalize_title_for_key()` / `first_author_surname()` / `citation_disambiguator()` /
  `paper_dir_slug()` / `extract_doi()` / `extract_arxiv_id()`。**逐字搬自阶段 2 的
  `code/utils.py`**（键的每个字节都写进了 `download_progress.json` 与下游产物，所以一个字都不改 ✗，
  搬迁后对全库 9,244 行算出的键 sha256 **两边完全相同** ✓）。**为什么宿主在这里**：`citations_meta.json`
  的**主体**（含 `cites_id`）自 2026-10-16 起由阶段 1 写（Y-9），而"没有真实 `cites_id` 的条目用什么键"
  正是这份规则 —— 所有权跟着数据产生者走（Y-7）✓。**`code/citation_index.py` 写主体时对 id-less 行补
  这个键** ✓（消歧要看整篇条目，所以在 `write_citations_meta()` 里整篇算一次）；阶段 2 的
  `code/utils.py` 只再导出（调用点零改动 ✓，`tests/test_shared_citation_key.py` 守卫）。

- **`code/shared/identity.py`**（2026-10-15，**跨仓库共享宿主**）：引用**身份判定**的唯一实体 ——
  `same_citation_identity()`（真实 `cites_id` → **DOI / arXiv 强标识** → **弱 URL 键**（IEEE / OSF /
  Research Square / DSpace 的形状折叠）→ 归一化标题 + 年份 + 首作者姓）及其助手。**为什么宿主在本
  阶段**：按 `KNOWN_ISSUES.md` **Y-7** 的"所有权跟着**数据产生者**走"，判据作用的数据（`cites_id`、
  标题、年份、作者）都是本阶段抓取产生的 ✓（此前宿主在阶段 2，是因为阶段 2 最早需要它 —— 那是旧判据）。
  消费方：阶段 2（`code/data_upgrade/repair_identity_fields.py` 经 `code/_shared.py`）、
  3.a.1 / 3.a.2 / 3.b / 阶段 4（各经自己的 `code/_shared.py`）。**本地是宿主，直接
  `from code.shared.identity import …`**；阶段 2 侧**不留副本、也不留薄壳**（同一 PEP 420 命名空间
  包里放同名文件会遮蔽宿主，`from shared.identity import *` 还会自我递归）。

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
- Reads the saved profile JSON output (cross-run state) and in-memory mid-paper state (same-run resume)
- Decides per paper whether citation data is `missing`, `partial`, `complete`, or `skip_zero`
- Resumes from in-memory state (`_mid_paper_state`, `_paper_states`)
- Persists every citing entry's Scholar cluster id as the additive `cid` field
  (`extract_citation_info`; pure parsing, no request)
- Writes `venue_key` / `venue_key_source` on every citing entry the same way
  (list-page venues are often truncated by Scholar, so those rows keep raw keys
  until the table is extended — `venue_key_source` tells the two apart)
- Resolves each citing entry's five citation formats from Scholar's Cite dialog
  before writing the output (`code/cite_format.py`, cached in
  `output/cite_format_cache.json`).  **On by default** — the Chicago string is the
  source of the citing paper's full author list and untruncated venue (BibTeX was
  abandoned on 2026-10-15) — and `--no-cite-formats` skips it; an entry without a `cid`
  cannot be addressed, so rows written before that field existed stay untouched
  until their paper is re-fetched
- Writes consolidated citation outputs to `author_<ID>_paper_citations.json` and `.xlsx`
- Writes each paper's `papers/<NNN>-<slug>/citations_meta.json` **main body** (Y-9 —
  `code/citation_index.py::write_citations_meta_for_pub`, called from `_save_output`):
  stage-1 fields only, index-ascending, idempotent, stage-2 download fields and retired
  indices carried over untouched.  `venue_key` / `venue_key_source` are **derived**: the
  writer **recomputes** them with `shared.venue_key.citation_venue_key()` instead of
  copying whatever the row carries (cached rows have no key, so copying silently wiped
  a 7,901-row backfill on 2026-10-15) and **never downgrades** — a stored `normalized`
  key survives when the recompute only reaches `raw`, as long as
  `same_citation_identity()` says it is the same citation
- Recomputes those derived keys for **every** row right before serialising
  (`citation_identity.refresh_venue_keys`, called from `_save_output`), so a row
  restored from the previous output still gets its key back.  A venue Scholar
  **truncated** (``…``) gets a key **only** through the explicit
  `TRUNCATED_VENUE_PREFIXES` table (`Advances in Neural …` → NeurIPS,
  `Proceedings of the AAAI …` → AAAI, `Expert Systems with …` → its journal);
  everything else truncated gets **no key** — `Proceedings of the …` alone covers
  564 different conferences (measured 2026-10-15: 5,690 of 9,244 citing rows are
  truncated across 1,781 distinct strings)

### Citation fetch strategy

Status is derived from **current counts and diagnostics**, not from persisted `complete` flags:
- Direct mode: skip when `num_citations_seen >= scholar_total`
- `stalled`: still incomplete, but `STALL_AFTER_ATTEMPTS` **completed**
  traversals in a row obtained the same number of rows
  (`fetch_outcome_signature` counts the rows seen and deliberately ignores
  Scholar's targets, which drift between runs even when the listing does not),
  so retrying cannot help — the list does not contain what Scholar claims
  (e.g. Scholar lists fewer citations than it reports).  Only an attempt that
  **walked every page** is folded into the counter (`complete_fetch_attempt`,
  plus no year record reporting `partial_resume_completed`), so a blocked or
  intentionally shortened run neither counts as a repeat nor wipes one.
  Cleared when the paper's Scholar count grows (`scholar_changed`); force a
  retry with `--fetch-mode force`.
- A year whose re-scan returns **fewer rows than the cache** keeps the cached rows and only
  appends identities it has not seen ✓ — but for a row present on both sides it now **merges the
  fresh row's `cite` / `cid` into the cached one** (`_merge_cite_into()`, 2026-10-19): the Cite
  dialog is fetched **per page**, i.e. for the freshly fetched rows, so dropping those rows
  outright threw that data away ✗.  Only `cite` (when the cache has none or fewer formats) and
  `cid` (when missing) are taken; identity fields and every other field stay as the cache has
  them, and nothing is ever downgraded.
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
  — but an offset is a **position in a mutable list**, so if Scholar inserts rows above it between
  runs the resume can walk past them and the year stays "N short" forever, always resuming from the
  same offset.  A year whose `short_attempts` (how many consecutive attempts came back under
  Scholar's own count, kept in `year_fetch_diagnostics`) reaches `RESCAN_AFTER_SHORT_ATTEMPTS`
  is therefore re-scanned **from page 1** once, relying on identity-key dedup instead of the
  offset (`_resume_start_after_short_attempts()`, 2026-10-19); a full attempt resets the counter.  If that re-scan comes back with the **same** count, the listing and Scholar's histogram
  simply disagree, so the year is marked `stalled` (with `stalled_probe`, the claim it disagreed
  with): it stops being re-scanned, its gap counts as **0** (`year_record_effective_histogram()`
  also keeps it out of the summary's `histogram_total`, otherwise the paper would stay `partial`
  forever) and it is skipped on later runs — until the probe grows past `stalled_probe` or a
  `--fetch-mode force` retry
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
  — and **a successfully solved captcha resets that counter** (`reset_break_counter()`, 2026-10-19):
  solving one is itself a multi-minute interruption, so without the reset the very next page often hit
  another 3–6 min break.  Only the break window moves; the session-refresh counter is left alone
  (freshly injected cookies are meant to be used right away).
- Charges **1/10 of the normal delay on the detail-style paths**:
  `patched_get_page()` sleeps `rand_delay(ctx.delay_scale * detail_path_delay_factor(url))`,
  where the factor is `code.config.DETAIL_PAGE_DELAY_FACTOR` and
  `detail_path_delay_factor()` (`code/scholarly_session.py`) is the **single**
  classifier deciding which URLs are "detail-style" — the publication detail page
  (`view_op=view_citation`) and a citing entry's Cite dialog (`output=cite`)
  — the signed BibTeX download was dropped from the list on 2026-10-15 — versus everything
  else, notably citation-list pagination, which keeps the full 45–90 s.  `--accelerate`
  multiplies on top; neither `venue_detail.py` nor `cite_format.py` may define a
  delay of its own (guarded by `tests/test_scholar_patch.py`)
- Performs soft session refresh every 10–20 pages
- Records the **HTTP status** of every response (`SessionContext.last_response_status`, from an
  httpx `response` event hook; cleared before each request).  scholarly throws the status away —
  it treats 404 as "retry with the same session" and ends in the same
  `MaxTriesExceededException` as a captcha or a 429 — so this hook is the only place it survives
  for `PageVisit` (2026-10-16).
- Verifies every patch hook point at the end of `patch_scholarly()` and raises
  when a scholarly upgrade made one of them ineffective (see
  `assert_scholarly_patches_active`)

All patch state is held in `SessionContext`; per-paper cross-run state is in `PaperFetchState` (`code/output_state.py`), and runtime fetch state is in `YearFetchSession` (`code/fetch_session.py`).  Dispatch on that type through `to_paper_fetch_state()` instead of scattering `isinstance(..., PaperFetchState)` checks around the caller.

### Interactive recovery (`code/interactive.py`)

- `--interactive-captcha`: user pastes a browser "Copy as cURL"; cookies and
  allowlisted headers are injected into the patched scholarly sessions
- In interactive mode: retries indefinitely
- In non-interactive mode: waits up to 24h prompting the user to switch proxy and type `ok`
  (that window is scaled by `--accelerate` like every other wait)

## Module dependency map

```
code/scholar_citation.py  (orchestrator + PaperCitationFetcher — python -m code.scholar_citation)
  ├── code.config                 (centralised constants — all tunables)
  ├── code.lib_env                (env staging for third-party libs + NO_PROXY normalise + proxy bridge)
  ├── code.common                 (utilities, backward-compat re-exports from config)
  ├── code.log_utils              (structured logging — setup_logging, get_logger)
  ├── code.author_fetcher         ← code.profile_io, code.venue_detail
  ├── code.profile_io             ← code.pub_info
  ├── code.venue_quality          (pure: is_complete_venue / is_preprint_venue / full-name check)
  ├── code.venue_detail           ← code.common, code.venue_quality (detail page + venue cache; bs4)
  ├── code.cite_format            ← code.common, code.venue_quality, code.page_visit (Cite dialog + format cache; bs4)
  ├── code.pub_info               ← code.citation_cache, code.venue_detail
  ├── code.citation_cache         (pure year/diagnostics functions)
  ├── code.citation_strategy      ← code.citation_cache
  ├── code.citation_identity      (pure dedup / info extract; hands out the row cid)
  ├── code.citation_io            ← code.citation_cache, code.citation_strategy
  ├── code.citation_models        (dataclasses for citations, diagnostics, resume state)
  ├── code.output_state           ← code.citation_io, code.citation_cache
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
- `tests/test_direct_fetch.py` covers direct-mode fetching (progress saves, early stop,
  resume offsets, prefix merging, dedup) and **K78**: a mid-scan snapshot must persist
  `fetch_strategy: "direct"` **and** its `direct_fetch_diagnostics`, otherwise the
  reloaded state infers `year` from the year diagnostics every paper carries (whose
  direct-mode `histogram_total` is 0) and an interrupted 2/25 fetch reads as
  **complete** — the remaining citations would never be fetched.  The test asserts the
  snapshot's fields *and* that `PaperFetchState.from_dict(...).need_fetch()` still says
  yes.
- `tests/test_venue_detail.py` covers the `venue_full` step hermetically: token parsing
  (stored field / legacy URL / no link at all), detail-page field pairing (the `<style>`
  trap, reordered rows, missing venue label, whitespace collapsing), the new fields
  surviving a profile round trip with `venue` byte-identical, graceful degradation
  (request error, page without a field table, interrupt) and cache/resume behaviour.
- `tests/test_cite_format.py` covers the Cite-dialog step hermetically: `cid` persistence
  from `url_scholarbib` (including the `info:None:` placeholder and rows written before
  the field existed) with `title`/`authors`/`venue`/`year`/`url`/`cites_id` byte-identical,
  label↔cell pairing for the five formats (the `<style>` trap, reordered/duplicate/unknown
  labels, missing cells, `&quot;`/`&amp;` decoding, inline `<i>` spacing),
  the Chicago parser that yields `venue_text` / `authors_full` and corrects the entry
  (`tests/test_cite_format_parts.py`), graceful degradation (request error, captcha page,
  interrupt) and cache/resume behaviour, plus the wiring: the CLI flag
  is unspecified by default (`None`) and resolves through
  `code.config.resolve_cite_formats()` (CLI > `crawler_config.json` > default on),
  the fetcher defaults to on, `_save_output` writes the field unless the step was
  opted out, and that write also refreshes `venue_key` from the dialog's Chicago
  venue (`cite.venue_text`: a known conference becomes `EMNLP2025`-style normalized).
- `tests/test_venue_quality.py` covers the "do not fetch it again" rule hermetically:
  the preprint/truncation/missing predicates, the full-name check (including name
  particles and initial-only lists), the four citation-level combinations, and both
  paths' skip behaviour — a skipped item issues **zero** requests, writes **no** cache
  entry and leaves `venue`/`authors`/`cites_id` byte-identical, while a mixed list still
  requests exactly the item that needs it (a cached dialog is reused so a re-run cannot
  drop `citation['cite']`).
- `tests/test_scholar_patch.py` pins the pre-request pacing with a stubbed sleep:
  the scale handed to `rand_delay` is `delay_scale * DETAIL_PAGE_DELAY_FACTOR` for the
  detail/Cite URLs and plain `delay_scale` for citation-list and profile pages
  (`--accelerate` stacking included), and a source-level guard asserts the two new
  modules define no delay of their own.  It also pins the **Referer of a Cite
  dialog**: the dialog must be sent with the page its row was seen on
  (`ctx.last_scholar_url`), not with the previously loaded list page, while other
  requests keep whatever referer they had.
- `tests/test_venue_key.py` covers the normalized venue identity: acronyms and
  spelled-out proceedings forms, Findings as its **own** venue (different from its
  host conference), TACL-not-ACL, longest-match-wins (Nature sub-journals, CL vs
  ACL), the two deliberate spelling merges (`NIPS` → `NeurIPS`, `ACMMM` → `ACM MM`),
  **journals carrying no year while conferences do** (`TACL`/`TASLP` vs
  `ICASSP2025`), **every preprint server keying on its own name** (arXiv forms
  including the truncated one, plus bioRxiv / medRxiv / SSRN / TechRxiv /
  Research Square / preprints.org, while a bare "Preprint" stays raw),
  **a host name not matching an acronym** (`kdd.org` / `iclr.cc` stay raw),
  **no bare "Findings" venue** (an unnamed host resolves to Findings of ACL while
  a named one keeps its host), **the Cite dialog's Chicago venue winning over the
  truncated list page** (`citation_venue_key`: legacy bibtex fields, then `cite.venue_text`, then the list page; field
  precedence, placeholder and unrelated fields ignored, the five formatted
  strings deliberately not parsed, no regression without a `cite`),
  the extended table (ICASSP / IJCNN / PAKDD / LREC / NLPCC),
  `CCMT`/`CWMT`/`WMT` staying apart, the two spellings of one Chinese journal
  collapsing onto one key, the year rule (venue string first, record year as
  fallback), unknown venues keeping their raw text, missing venues getting **no**
  key, plus the two writers (`PubInfo` and `extract_citation_info`) and that
  `PubInfo` prefers `venue_full` and never reads a stale stored key back.
- `tests/test_shared_venue_key.py`（9 例，2026-10-15）守住 venue_key 判据的**唯一实体**：
  `code/venue_key.py` 是薄再导出（逐个 `assertIs` 共享件、源码级断言没有实现副本、体量 < 60 行）、
  `code/shared/` 无 `__init__.py`（PEP 420 命名空间包）、功能测试走共享路径；
  以及 `venue_key_name()` 的去年份规则（会议剥年份 / 期刊·预印本·裸名原样 / 非字符串与空 → `''` /
  越界年份与数字粘连不动 / 与 `venue_key()` 的往返）。
- `tests/test_normalize_venue_key.py` covers the data-upgrade tool: dry-run writes
  nothing (no backup directory either), `--apply` fills both fields while every
  other key stays byte-identical, the previous version is copied into the backup
  directory with a manifest, a second run is a no-op, the review report lists each
  distinct unrecognised venue with its count, `--author` scans only that author,
  a missing/empty output directory is an error rather than a silent pass, and a
  re-run that finds nothing to do leaves no empty backup directory behind.
- When changing year-based fetching, resume behavior, or CLI flags, update the
  corresponding test file first rather than adding integration coverage.

## Dependencies and runtime assumptions

- `httpx[http2]==0.27.2` is pinned for compatibility with the current scholarly integration; the
  `[http2]` extra is what installs `h2`, without which `httpx.Client(http2=True)` raises `ImportError`.
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
