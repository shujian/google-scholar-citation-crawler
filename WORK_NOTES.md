# Work Notes: Google Scholar Citation Crawler

## 项目结构

```
google-scholar-citation-crawler/
├── scholar_citation.py       # 合并后的主脚本（profile + citations 一体化）
├── requirements.txt          # scholarly>=1.7, openpyxl>=3.1
├── README.md                 # 公开文档（英文）
├── LICENSE                   # MIT
├── .gitignore                # output/, scholar_cache/, WORK_NOTES.md, examples/run_my_author.sh
├── WORK_NOTES.md             # 本文件（gitignored，个人笔记）
└── examples/
    ├── run_example.sh        # 占位示例（tracked）
    └── run_my_author.sh      # 个人脚本（gitignored）
```

### 输出文件
- `author_{id}_profile.json` / `.xlsx` — 作者信息 + 论文列表
- `author_{id}_history.json` — 变更历史（追加写入）
- `author_{id}_paper_citations.json` / `.xlsx` — 逐篇引用列表
- `scholar_cache/` — 增量缓存

---

## 快速使用

```bash
# 安装依赖
pip install -r requirements.txt

# 日常使用（个人脚本）
./examples/run_my_author.sh

# 或直接运行
python scholar_citation.py --author YOUR_AUTHOR_ID

# 测试少量
python scholar_citation.py --author YOUR_AUTHOR_ID --limit 2
python scholar_citation.py --author YOUR_AUTHOR_ID --limit 1 --skip 1
```

### CLI 参数

| 参数 | 说明 |
|------|------|
| `--author` (必填) | Scholar author ID 或完整 URL |
| `--output-dir` | 输出目录，默认 `./output` |
| `--skip M` | 跳过所有论文列表中的前 M 篇（按引用数降序），不计入 limit |
| `--limit N` | 在 skip 之后处理 N 篇（第 M+1 到 M+N 篇），不管状态是否需要抓取 |
| `--force-refresh-pubs` | 忽略缓存重新抓取论文列表 |
| `--force-refresh-citations` | 按缓存条数 vs Scholar 数重新检查，不匹配的重新抓取 |

---

## 运行流程

1. **Profile 阶段**（每次都运行）
   - 阶段1：获取作者基本信息（name, affiliation, citation stats）
   - 阶段2：获取所有论文列表（含 citedby_url）
   - 增量比较：与上次 profile 对比，发现新增论文、引用数变化
   - 保存 JSON + Excel + 追加 history

2. **智能跳过判断**
   - 比较本次与上次的 `total_citations` 和 `total_publications`
   - 都没变 → 跳过 citation 抓取，直接结束

3. **Citation 阶段**（有变化时才运行）
   - 逐篇获取引用来源列表
   - 增量缓存 + 中断续传
   - 保存 JSON + Excel

---

## 等待与超时机制

### 统一等待策略（2026-03-08 更新）

所有主动等待统一使用 30-60 秒随机值（`rand_delay()`），不再区分 base delay / page delay：
- Profile 阶段：搜索作者后、两阶段之间
- Citation 阶段：翻页时、年份段切换时、论文之间

每次主动等待都会输出等待信息（秒数）。

### 心跳超时机制

- **心跳间隔**：10 秒检查一次（`HEARTBEAT_INTERVAL = 10`）
- **超时阈值**：80 秒无响应（`HEARTBEAT_TIMEOUT = 80`）
- **超时处理**：保存进度后 `os._exit(1)` 强制终止整个程序

### 主动等待与超时计时的关系

关键设计：主动等待不计入超时计时。

- 进入主动等待前设 `_deliberately_waiting = True`，心跳线程 `continue` 跳过检查
- 主动等待结束后设 `_deliberately_waiting = False`，**同时重置 `_last_activity = time.time()`**
- 这样超时计时从主动等待结束后才开始，不会把等待时间误算
- `_last_activity` 是实例变量，monkey-patch 中的闭包和 heartbeat 线程都能访问

---

## scholarly 限流对策

### Monkey-patch（运行时替换，不修改 scholarly 源码）

1. **翻页随机延迟**：替换 `_SearchScholarIterator._load_url`，每次翻页前等待 30-60 秒
2. **年份切换随机延迟**：替换 `scholarly._citedby_long`，>1000 引用按年份分段时加延迟
3. **session 刷新**：每篇论文开始前刷新 scholarly 内部 httpx session，清除被标记的 cookie

### citedby_url 前缀修正

scholarly 的 `_get_soup` 会在 URL 前拼接 `https://scholar.google.com`，但缓存中的 `citedby_url` 已是完整 URL，需要先剥掉前缀，否则双重前缀导致请求失败。

### scholarly pub_obj 必需字段

```python
pub_obj = {
    'citedby_url': citedby_url,
    'container_type': 'Publication',
    'num_citations': num_citations,
    'filled': True,
    'source': 'PUBLICATION_SEARCH_SNIPPET',
    'bib': {'title': title, 'pub_year': year},
}
```
- `num_citations <= 1000`：走 `PublicationParser.citedby()` → 只用 `citedby_url` + `filled`
- `num_citations > 1000`：走 `_citedby_long()` → 按年份分批抓取，额外需要 `bib.title`、`bib.pub_year`、`source`

---

## 增量缓存与续传

### 缓存结构
- `scholar_cache/author_{id}/basics.json` — 基本信息
- `scholar_cache/author_{id}/publications.json` — 论文列表
- `scholar_cache/author_{id}/citations/{md5_16}.json` — 每篇论文的引用列表

### 缓存有效条件
- `complete=True` 且 `num_citations_cached == 当前引用数` → 跳过
- 引用数变化或 `complete=False` → 重新抓取（或续传）

### 中断续传
- 每 10 条引用写一次进度（`complete=False`）
- 重跑时检测到未完成 → 从已有条数处续传
- 续传优化：≤1000 引用时在 `citedby_url` 后追加 `&start=N`，直接跳到断点页

---

## Excel 工作表说明

### Profile Excel
| 工作表 | 内容 |
|--------|------|
| Author Overview | 基本信息、引用统计、历年引用趋势 |
| Publications | 所有论文（按引用数降序），含可点击链接 |
| Change History | 每次运行的快照记录 |

### Citations Excel
| 工作表 | 内容 |
|--------|------|
| Summary | 论文概览，含 Scholar 引用数 vs 已采集引用数 |
| All Citations | 所有引用来源的扁平列表 |

---

## venue 字段说明

- **论文列表**（profile 阶段）：bib 只含 `title`、`pub_year`、`citation`，venue 取自 `bib.citation`
- **引用来源**（citation 阶段）：publication search snippet 的 bib 含 `venue` 字段，直接使用

---

## 环境说明

- conda 环境：`scholar`
- 关键包：`scholarly==1.7.11`、`openpyxl==3.1.5`、`httpx==0.28.1`
- 代理：scholarly 自身代理 API 与 httpx 0.28+ 不兼容，通过系统 `https_proxy` 环境变量生效

---

## 变更历史

- **2026-03-07** — 第一步：作者主页采集脚本（fetch_author_profile.py）
- **2026-03-08** — 第二步：论文引用采集 + 限流对策（fetch_paper_citations.py）
- **2026-03-08** — 项目重组：合并两个脚本为 scholar_citation.py，清理个人信息，准备 GitHub 发布
- **2026-03-08** — 去掉 `--step` 参数，profile 每次都运行，无变化时自动跳过 citation 抓取
- **2026-03-08** — 统一等待策略：所有主动等待 30-60 秒随机值，去掉 `--delay`/`--page-delay-min`/`--page-delay-max`；心跳间隔 10 秒、超时 80 秒后 `os._exit(1)` 强制终止；主动等待结束后重置 `_last_activity`，确保超时计时不误算主动等待时间
- **2026-03-08** — 修复 skip 逻辑：即使 citations 总数和 publications 数没变，也检查是否所有论文的 citation 缓存都已完整（`has_pending_work()`），有未完成的则继续抓取；重构 `_citation_status` 和 `_load_citation_cache` 为实例方法复用
- **2026-03-08** — 添加论文级别重试机制（`MAX_RETRIES=3`）：抓取被阻止时刷新 session + 等待 + 重试，重试前重新加载缓存利用上次保存的部分进度；超时重试耗尽后保存已有结果并退出
- **2026-03-08** — Monkey-patch scholarly `_navigator._get_page` 的 403/DOS 重试等待：原始代码用 `random.uniform(60, 120)` 等 60-120 秒，会超过心跳 80 秒超时导致误杀进程。现在拦截 `_get_page` 内部的 `time.sleep`，>5s 的等待统一替换为 30-60s 并标记 `_deliberately_waiting`，心跳不会误判。同时将超时处理从 `os._exit(1)` 改为 `_thread.interrupt_main()` + `TimeoutError`，使重试逻辑能正常工作
- **2026-03-08** — 优化重试策略：scholarly 内部 `_max_retries` 从 5 降为 2（`nav._set_retries(2)`），快速失败；外层采用渐进式重试（失败→等3h→重试→失败→保存进度→等6h→重试→失败→打印时间→终止）；添加全局页面访问计数器 `_total_page_count`，出错时输出已访问页数用于诊断
- **2026-03-09** — 年份级查询从一开始就使用：引用数 >=50 的论文直接按年份用 `scholarly.search_citedby(pub_id, year_low=Y, year_high=Y, start_index=N)` 查询；每完成一个年份立即记录 `completed_years`，中断后跳过已完成年份；Session refresh 间隔改为随机 10-20 页（原固定 5 页），切换论文和年份时也刷新 session 并重置计数；scholarly 内部 403 重试通过 `MAX_SLEEPS_PER_PAGE=3` 限制，`_max_retries` 降为 1
- **2026-03-09** — 日志增强：所有 retry/error 输出加时间戳；添加 `_new_citations_count` 本次运行新增引用计数，在 progress saved 和最终输出中显示
- **2026-03-09** — 缓存与中断保护：主循环提取为 `_run_main_loop`，Ctrl+C 中断时也保存最终 JSON/Excel 输出；所有 Waiting 消息增加上下文（位置、累计运行时间、新增 citation 数、总页数）
- **2026-03-10** — 修复引用变化检测：`fetch_basics()` 不再使用缓存，每次从网络获取（只需 1 次请求）以可靠检测引用数变化；当 `total_citations` 与上次不同时自动刷新 publications 列表，捕获每篇论文的引用数更新
- **2026-03-10** — 合并 history 到 profile：`change_history` 作为 `profile.json` 的字段存储，去掉独立的 `history.json`；旧数据通过 `load_prev_profile()` 自动迁移；去掉 `--force-refresh-basics` 参数（basics 每次网络获取）
- **2026-03-10** — 修复 citation 完成判断：`_citation_status()` 改为比较实际缓存条数 `num_citations_cached` 与当前 `pub['num_citations']`，而非比较 `num_citations_on_scholar`（抓取时快照）；逻辑简化为：缓存数 >= Scholar 数 → complete，缓存数 < Scholar 数 → partial
- **2026-03-10** — 年份查询提前终止：`_fetch_by_year` 每完成一个年份后检查 `len(citations) >= num_citations`，满足则跳过更早年份（新增引用通常集中在最近年份）
- **2026-03-10** — 加回 `--force-refresh-pubs`：用于 profile 更新后因断线导致 publications 没刷新的情况，手动强制刷新
- **2026-03-10** — 引用去重：抓取时按 title（小写去空格）去重，避免 Scholar 跨年份/翻页返回重复结果；清理现有缓存中 142 条重复
- **2026-03-10** — 修复去重导致的无效重查：去重后缓存数 < Scholar 报告数（差值是重复），`_citation_status()` 改为比较 Scholar 当前引用数与上次完成时的 Scholar 引用数（`num_citations_on_scholar`），而非比较缓存条数；Scholar 没涨 → complete，Scholar 涨了 → partial；去掉 `_fetch_citations_with_progress` 中清除 `completed_years` 的逻辑
- **2026-03-11** — 修复 Scholar 引用增长时的更新逻辑：保留已有缓存但清空 `completed_years`，避免全量重下载或跳过所有年份；三种场景分别处理：中断续传（保留缓存+完成年份）、Scholar 增长（保留缓存，清年份）、首次抓取（全空）
- **2026-03-11** — 年份扫描 early stop 优化：传入 `prev_scholar_count`，当本篇论文新增引用数 >= Scholar 增量时提前停止扫描更早年份；使用 `paper_new_count` 局部计数而非全局 `_new_citations_count`
- **2026-03-11** — 添加 `--force-refresh-citations`：正常模式下 `_citation_status` 比较 Scholar 当前数 vs 上次完成时 Scholar 数（容忍去重差值）；force 模式直接比较缓存条数 vs Scholar 当前数，不匹配即重新抓取；两个 force 参数独立：`--force-refresh-pubs` 作用于 profile 阶段，`--force-refresh-citations` 作用于 citation 阶段
- **2026-03-12** — 修复 `--force-refresh-citations` 被提前 return 拦截：main() 中"无变化检测"分支加入 `not args.force_refresh_citations` 条件，force 模式下跳过该检查
- **2026-03-12** — 修复 `--force-refresh-citations` 中 `completed_years` 未清空：force 模式下主循环始终清空 `completed_years`，确保每个年份都重新检查；新增 `force re-check` action 标签与 `resume`/`update`/`first fetch` 区分
- **2026-03-12** — 修复漏抓引用：`start_index` 依赖 `year_counts` 不可靠（4% 引用的 year 字段为 N/A 导致计数偏少）；改为每个年份始终从 `start_index=0` 开始，完全依靠 `seen_titles` 去重跳过已有引用
- **2026-03-12** — 去重时输出重复条目：遇到重复引用时打印 `[dedup]` 日志，显示新条目和已有条目的标题+年份，方便人工核查
- **2026-03-12** — 修复 scholarly `search_citedby` 漏抓 bug：该方法始终在 URL 中加入 `&as_sdt=N,33`（地区过滤参数），导致 Scholar 过滤部分结果（实测 19→18）；改为直接构造不含 `as_sdt` 的 URL，使用 `_SearchScholarIterator` 迭代，与 `PublicationParser.citedby()` 内部做法一致
- **2026-03-13** — 延长重试等待时间：第一次失败 3h→6h，第二次失败 6h→12h
- **2026-03-14** — 修复 `_refresh_scholarly_session` 因 httpx 不兼容崩溃：捕获 `TypeError`，session 刷新失败时静默跳过
- **2026-03-14** — 添加浏览器特征参数 `as_sdt=0,5`：citation URL 改用全局地区码（5），避免 scholarly 默认的 `,33`（纽约地区）过滤结果；彻底研究确认 `as_sdt` 含义：第一段=搜索类型，第二段=地区码，`as_sdt=2005` 是 Scholar 内部 "Cited by" 专用复合标志
- **2026-03-14** — 随机化抓取顺序：无 `--skip`/`--limit` 时 shuffle `need_fetch`，避免每次从最高引用论文开始触发 ban；有 `--skip`/`--limit` 时保持原始顺序以保证定位准确
- **2026-03-14** — 优化运行摘要输出：显示 `X/231 papers, N fetched, M new`；`_papers_fetched_count` 记录本次实际抓取数；中断/limit 导致的未处理论文从缓存补数据，保证总引用数准确
- **2026-03-15** — 记录 `num_citations_seen`（缓存数+去重数）：新增 `_dedup_count` 只统计 Scholar 自身列表的重复（不含 cache hit），保存到缓存；`_citation_status` 优先用 `num_citations_seen >= current` 判断完整性，无此字段时回退旧逻辑
- **2026-03-16** — 修复 `search_author_id` 返回 `None` 导致崩溃：Scholar 限流时返回 None，加入显式检查并打印清晰错误信息
- **2026-03-17** — 修复 `--skip`/`--limit` 语义：改为基于所有论文列表的绝对位置；`--skip M` 跳过前 M 篇，`--limit N` 在 skip 之后处理 N 篇（M+1 到 M+N），skip 不计入 limit
- **2026-03-17** — 记录 citation URL：每次按年份请求时打印完整 URL 方便对比验证
- **2026-03-17** — 修复中断后年份丢失：`_fetch_by_year` 的 `except` 由仅捕获 `KeyboardInterrupt` 扩展为同时捕获 `Exception`，任何异常都调用 `save_progress(complete=False)` 保存已完成年份
- **2026-03-17** — 年份扫描方向自适应：普通更新模式（Scholar 引用增长）从新→老，早停更快；Force/首次抓取模式从老→新，老年份数据稳定，中断续传更高效
- **2026-03-18** — 改善 `fetch_basics` 异常提示：`AttributeError`/`TypeError`（Scholar 返回 None 导致）单独捕获，输出明确的网络问题提示，不打印堆栈；其他异常仍打印完整堆栈
- **2026-03-20** — 降级 httpx 至 0.27.2 修复 session 刷新：scholarly 1.7.11 使用已在 httpx 0.28 移除的 `proxies=` 参数，导致 `_new_session()` 始终抛 `TypeError` 被静默吞掉，session 从未真正刷新；降至 0.27.2 后 `_new_session()` 正常工作；requirements.txt 固定 `httpx==0.27.2`
- **2026-03-20** — 增加请求延迟：`DELAY_MIN 30→45`，`DELAY_MAX 60→90`，降低 Scholar IP 级速率限制触发概率
- **2026-03-20** — 新增强制长休息机制：每 8-12 页（随机）触发一次 3-6 分钟休息，让 Scholar 的滑动窗口速率限制有时间重置；休息后顺带刷新 session 并重置 `_next_refresh_at`；与常规 session 刷新（10-20 页一次）独立运行，长休息优先级更高；`_next_break_at` 和 `_next_refresh_at` 错开初始化，避免两者同时触发
- **2026-03-20** — 修复 httpx 0.27.2 下 proxy 失效问题：`scholarly.use_proxy(pg)` 在 0.28.1 时因 TypeError 被 catch 而不生效，proxy 由 httpx 自动读取环境变量；降至 0.27.2 后该调用开始生效，但 scholarly 的 `{'http': url}` 格式与 httpx 0.27.x 要求的 `{'http://': url}` 格式不符，导致 proxy 静默失效、直连 Scholar 被封；修复方案：`setup_proxy()` 不再调用 scholarly proxy API，完全依赖 httpx `trust_env=True` 自动读取 `HTTPS_PROXY` 环境变量
- **2026-03-20** — 浏览器请求还原（通过抓取真实 cURL 分析）：①年份查询 URL 从 `as_sdt=0,5` 改为 `as_sdt=2005`（Scholar citation-search 专用内部标志），并补充 `sciodt=0,5` 和 `scipsc=`，与浏览器点击年份过滤产生的 URL 完全一致；②在 scholarly 的 httpx session 上添加完整浏览器 headers：`sec-fetch-dest/mode/site/user`、`sec-ch-ua`、`upgrade-insecure-requests`、完整 `accept`；③动态 Referer：每次翻页前将 `_last_scholar_url`（上一页 URL）设为 Referer，初始值为 author profile URL（模拟用户从作者主页点击"Cited by"的导航链）；④patch `_new_session`：scholarly 在真正 403 后重建 httpx client 时自动重新应用 browser headers；⑤session 策略改变：`_refresh_scholarly_session` 改为 soft reset（只重置 `got_403`，不销毁 httpx client），保留 Scholar 在请求过程中积累的 cookies
- **2026-03-21** — 交互式 captcha 输入改为逐行读取（`input()` loop + 末行无 `\` 自动结束），替换原来的 `sys.stdin.read()`；`sys.stdin.read()` 在 SSH+tmux 下会卡死（Ctrl+D 被 tmux/SSH 拦截无法触发 EOF）；新方案基于 Chrome DevTools cURL 格式特征（最后一行无 `\`）自动检测粘贴完成，无需任何结束符
- **2026-03-21** — 程序结束时输出 Run summary：`elapsed X | N pages accessed | M new citations`，与运行中的 `_wait_status()` 格式保持一致；修复了同次提交中 `_save_output` 方法定义被意外删除的 bug（`def _save_output` 行在插入 `_wait_proxy_switch` 时被覆盖）
- **2026-03-21** — 更新 README：补充所有主要功能（year-based fetching、mandatory breaks、HTTP/2、interactive captcha bypass、proxy-switch wait、run summary、`--skip`/`--limit` 语义、`--force-refresh-citations` 说明）；添加 `user.md` 和 `WORK_NOTES.md` 说明（已提交到 git，无个人信息，记录 AI 辅助开发过程，用户零代码）；注明项目完全由 Claude Code CLI 开发
- **2026-03-21** — Interactive 模式跳过 session 重置：`--interactive-captcha` 模式下用户已注入真实 cookies，重置 session 会丢弃它们；在 4 处 `_refresh_scholarly_session()` 调用（mandatory break 后、常规 soft refresh、年份切换、retry 开头）均加 `not self.interactive_captcha` 守卫
- **2026-03-21** — 修复年份中断后重从第1页开始的问题：新增 `partial_year_start` 机制（仅内存，不写入缓存文件），`_fetch_by_year` 在每个 item 迭代时即更新 `self._partial_year_start[year] = start_index + year_items_seen`；同一次运行内 retry 从 `self._partial_year_start` 读取断点直接跳到对应页；程序重启时自然清零，从第 0 条重来（更安全）；年份完成后 pop 清除；新增 "resuming from position N" 日志
- **2026-03-23** — 新增 `approach.md`：记录每次用户提出意见后的标准工作流程（6步：分析→修改→确认→WORK_NOTES→user.md→提交）
- **2026-03-23** — 完善 `--help` 输出：各参数加详细 `help=` 说明，`--skip`/`--limit` 用 `metavar` 标注变量名，加 `formatter_class=RawDescriptionHelpFormatter` 和 `epilog` 示例区；argparse 原生支持 `--help`，无需额外代码
- **2026-03-23** — 新增 `--hard` 参数：force refresh 时默认在 `len(citations) >= num_citations` 或找到足够新引用时提前停止，避免不必要的请求；`--hard` 禁用两个 early-stop 条件，强制抓取所有年份；仅与 `--force-refresh-citations` 配合使用有意义
- **2026-03-23** — 持久化 `dedup_count`：Scholar 自身结果中的重复条目数量写入缓存 JSON（`dedup_count` 字段）；resume/force-refresh 时以保存值初始化 `_dedup_count`（而非重置为 0），防止 force refresh 时重复条目因命中 `cached_titles` 而不被重新计数，导致 `num_citations_seen` 偏低；仅在 `--force-refresh-citations --hard` 组合时清零（此时全部年份重新抓取，重复会被重新发现）
- **2026-03-24** — 修复 profile→citation 过渡触发验证码：`_patch_scholarly()` 创建新 HTTP/2 session 时会丢弃 profile 阶段 Scholar 设置的所有 cookies，Scholar 看到无 cookie 的全新连接直接触发验证码；修复：创建新 session 前先复制旧 session 的全部 cookies，过渡后 Scholar 仍能识别已有连接
- **2026-03-24** — 修复 SSH/tmux 粘贴 cURL 卡死（多次迭代）：root cause 是 pty canonical mode 的行缓冲导致 pty 输入队列填满，SSH 流控生效，终端冻结；最终方案：用 `termios` 只关掉 `ICANON`（保留 ECHO），用 `select + os.read()` 在非行缓冲模式下快速消费数据；phase 1 无限等待第一个字节，phase 2 每次读后等 3s 超时判定粘贴结束；finally 块确保终端设置总被还原；经过 readline/raw mode/file-based 等多种方案测试后，ICANON-only 方案在 SSH+tmux 下验证有效
- **2026-03-24** — 修复 probing year 三个问题：①probe 异常不再被 catch 吞掉，网络/访问类错误直接抛出传递到 `_run_main_loop` 统一触发 captcha/proxy-switch；②retry 时 start_year 不后退——probe 结果与 `min(year_counts.keys())` 取 min，防止 Scholar 档位变化导致跳过已缓存早期年份；③probe 解析多来源：除 `as_ylo=YYYY` 粗粒度侧边栏链接外，还解析单年柱状图链接（`as_ylo=X&as_yhi=X`）和引用片段年份文本（`gs_age`/`gs_gray`/`gs_a` 元素），取所有来源最小值，比仅依赖粗粒度档位更准确
- **2026-03-25** — 原地重试机制重构：① probe 遇到验证码/封锁时在方法内部处理（`MAX_PROBE_RETRIES=3`），interactive 模式调 `_try_interactive_captcha`，非 interactive 调 `_wait_proxy_switch`，失败则返回 `None` 降级，不向外抛出；② 年份迭代中途遇到验证码时也原地重试（per-year retry loop）：interactive 模式解完验证码直接 `continue` 从断点位置继续，非 interactive 则向外抛出走 outer retry loop；③ 有 `completed_years` 记录时跳过 probe——直接用 `min(completed_years)` 作为 start_year，retry 时不再重新 probe
- **2026-03-25** — 修复 citation start_year probe 的主数据源：用户提供了 Scholar 页面真实 DOM 片段，先误以为 sidebar 小图 `#gs_res_sb_hist_wrp .gs_hist_g_a[data-year][data-count]` 就是完整年份分布，后经用户验证发现它只覆盖最近几年；真正完整分布对应的是弹窗/完整图容器中的 `.gs_rs_hist_dialog-g_bar_wrapper .gs_hist_g_a[data-year][data-count]` / `#gs_md_hist .gs_hist_g_a[data-year][data-count]` 节点。当前 `_probe_citation_start_year` 优先解析完整图 DOM（仅取 `data-count > 0` 的年份），且一旦拿到完整图年份就直接返回，不再混入 `as_ylo=1996` 这类 fallback 链接导致错误回退；单年链接 `as_ylo=X&as_yhi=X`、粗粒度 `as_ylo=YYYY`、snippet 年份文本只在完整图 DOM 不可用时才作为 fallback
- **2026-03-26** — 年份确认逻辑改为“每次新抓取都 probe，一次运行内恢复不重复 probe”：用 TDD 添加 `test_year_probe_logic.py` 验证三种行为——① force refresh 且有缓存年份时仍会 probe；② 普通新一轮抓取即使已有缓存年份也会 probe；③ 同一次运行内如果 `completed_years` 非空，则跳过 probe。实现上删除了“有 `year_counts` 且非 force 就直接信缓存年份”的分支，仅保留“`completed_years` 非空时跳过 probe”的特例。验证命令：`python3 -m unittest test_year_probe_logic.py` 与 `python3 -c "import ast; ast.parse(open('scholar_citation.py').read())"` 均通过
- **2026-03-26** — CLI 语义清理：将 `--force-refresh-citations` 重命名为 `--recheck-citations`，更准确表达“在选中论文范围内重新检查 citation 完整性并仅重抓不完整者”的实际行为；`--skip` / `--limit` 语义不变。删除已过时的 `--hard` 参数（当前默认 early-stop 行为已经足够，`--hard` 不再提供独立价值）；保留 `--force-refresh-citations` 作为 deprecated alias 以兼容旧用法
- **2026-03-28** — Interactive 模式等待缩短实验：用户观察到 roughly 每 40 页左右就会再次触发验证码，长等待并未明显改善页面数/验证码比，因此将 interactive 模式下的所有抓取等待缩短为原来的 1/10（包括 profile 阶段等待、citation request wait、citation probe wait、paper-to-paper wait、mandatory break）；新增全局 `_captcha_solved_count`，每次成功导入 cookies 后加 1，并把 captcha solves 一并输出到 `_wait_status()` 中，便于对比“页面访问数 / 验证次数”的实验效果

---

## scholarly 内部实现笔记

### 源码位置
`<python-env>/lib/python3.11/site-packages/scholarly/`

### citedby() 的两条路径

| 条件 | 路径 | 实现 |
|------|------|------|
| `num_citations <= 1000` | 简单路径 | `PublicationParser.citedby()` → `_SearchScholarIterator` 直接翻页 |
| `num_citations > 1000` | 长路径 | `_citedby_long()` → 按年份分段，每段用 `search_citedby(id, year_low, year_high)` |

### _citedby_long 按年份分段策略
- 从 `citedby_url` 中用正则提取 publication ID（`cites=[\d+,]*`）
- `source == AUTHOR_PUBLICATION_ENTRY` 时：先 `fill()` 获取 `cites_per_year`，用 `_bin_citations_by_year()` 分组（每组 ≤1000）
- `source == PUBLICATION_SEARCH_SNIPPET` 时：逐年遍历（当前年 → pub_year），每年一段
- 单年仍超 1000 时只 log warning，实际只能拿到 1000 条（Scholar 硬限制）

### _SearchScholarIterator 翻页
- 每页 10 条结果
- `__next__()` 检查是否有 `gs_ico_nav_next` 按钮决定是否翻页
- `_load_url()` 调用 `_nav._get_soup(url)` → `_get_page()` 发起 HTTP 请求

### 403 / 限流处理（_navigator._get_page）
- **每次请求前**：`random.uniform(1, 2)` 等 1-2 秒（第 112 行）
- **首次 403**：立即重试，新 session
- **后续 403**：`random.uniform(60, 120)` 等 60-120 秒，再换 session 重试（第 142 行）
- **DOSException**：同样等 60-120 秒（第 161 行）
- **最多重试**：`_max_retries` 次（默认 5），超过抛 `MaxTriesExceededException`
- **异常传播**：NOT caught in iterator，直接传给调用者
- **⚠️ 冲突问题**：scholarly 的 60-120 秒等待会超过心跳 80 秒超时 → 已通过 monkey-patch `_get_page` 解决，将 >5s 的 sleep 替换为 30-60s 并标记主动等待

### `as_sdt` 参数说明

`as_sdt` 格式：`as_sdt=<类型>,<地区码>`
- 类型：`0`=排除专利（默认），`7`=包含专利，`4`=判例法搜索，`2005`=citation 搜索专用内部标志
- 地区码：`5`=全球默认，`33`=特定地区（会过滤部分结果）

scholarly `_construct_url()` 默认生成 `&as_sdt=0,33` 或 `&as_sdt=1,33`，地区码 `33` 会导致 Scholar 过滤部分结果（实测 19→18）。

浏览器点击 "Cited by" 链接时生成 `as_sdt=2005&sciodt=0,5`（`2005` 是 citation 搜索专用复合标志，`sciodt` 用途未完全确认暂不设置）。

**当前方案**：使用 `as_sdt=0,5`（排除专利 + 全球地区），避免地区限制导致漏抓。
**解决方案**：直接构造 URL 使用 `_SearchScholarIterator`，不通过 `search_citedby()`。

### 续传时 >1000 引用的问题
- `_citedby_long` 无法用 `&start=N` 跳页（它按年份重新查询）
- 续传时只能逐条 skip 已有的引用，需要重新翻过所有已缓存的页面
- 990 条缓存 = 重新翻 ~99 页 × 30-60s 延迟 ≈ 可能 50+ 分钟才到断点
- 大量重复请求极易触发 Scholar 限流 → 需要论文级重试机制
