# Work Notes: Google Scholar Citation Crawler

本文档记录开发中的关键技术细节、架构决策和踩坑记录。按时间顺序的更新历史见 [_update_history.zh.md](_update_history.zh.md)。

## 2026-10-14: 数据修复工具为什么单独放 `code/data_upgrade/`

**问题**：`fix_diag_from_year_record.py` / `fix_output_fetch_state.py` 平铺在 `code/` 下，与
`output_state.py` / `citation_*.py` 这些主线模块同级，看不出它们**不在抓取流程里**。

**做法**：`code/data_upgrade/`（带 `__init__.py`）承载一次性数据修复/升级工具；主线入口
`python -m code.scholar_citation` 一字未动。**这些是数据修复/升级工具，与主线入口分开。**

**坑（移动会真实踩到）**：两个脚本为了支持「直跑脚本路径」都有一段 `sys.path` 引导：
`sys.path.insert(0, dirname(dirname(abspath(__file__))))`。原先脚本在 `code/` 下，两层 `dirname`
正好是项目根；移进 `code/data_upgrade/` 后必须改成**三层**，否则直跑时 sys.path 里进的是 `code/`
自身、`from code.output_state import PaperFetchState` 解析不到（`code` 会落到标准库）。
模块形式 `python -m code.data_upgrade.fix_*` 本身不受影响（cwd 已在 sys.path），但两种形式都要保住。

**「无引用」的核实口径**（先验证再搬）：`grep -rn "<模块名>" code/ tests/ *.md`，两个候选的命中只有
文档清单 + 模块自身；`run.sh`、容器根 `preflight.sh`、`run_all_tests.sh` 均无引用 → 可搬。
**被 import 的就必须留原地**：反例是 3.b 的 `json_to_md.py` —— 它被
`tests/test_build_md_status.py`（`from code.json_to_md import build_md`）与
`tests/test_atomic_save.py`（`import code.json_to_md as jm`）import，且是正式报告生成路径，
所以判定**不搬**（本次跨仓库任务里 3.b 因此无改动）。

**副作用检查**：`tests/test_no_env_config.py::_iter_code_modules()` 只 `os.listdir(CODE_DIR)`
顶层 `.py`、**不递归** —— 搬进子目录的模块不再被该守卫扫描。这两个脚本本来就不读环境变量，
边界没有因此松动；但**今后往 `code/` 的任一子目录加「读环境变量」的模块，该守卫不会报错**
（遗留弱点，本轮未扩大守卫范围）。

## 2026-10-13: 「输出期去重」的身份键：标题不是身份

**为什么输出期还要去重**：抓取期 `fetch_session` 用 `citation_identity_keys()`（含 `cites_id` 键）
去重，但跨运行合并是"新结果与缓存取并集、绝不删行"（保权威性），所以同一 cluster 会在两次抓取
之间以"arXiv 版 + 正式版"两套元数据各留一行 —— 输出期去重就是为了收掉这种**同 cluster** 的重复。

**坑**：输出期那条身份键里额外塞了一个 `title\t<归一化标题>`，本意是"同一个 cluster 换过元数据
或没 id 时也能收掉"，实际效果是**把不同 cluster 的同名条目也收掉了**：实测旧快照因此从 9200 掉到
8971（229 条），其中 201 条是同标题、**不同** `cites_id` 的两条独立条目（K123 要求各留一条）。

**现在的口径**（`citation_output_identity_keys()`）：

| 行的情况 | 身份键 | 结果 |
|---------|--------|------|
| 有真实 `cites_id` | `cites_id\t<id>`（**只发这一条**） | 同 id 才合并 |
| 无真实 `cites_id` | `meta\t<title>\t<venue>` 或 `…\t<authors>`（全等） | 全等才合并 |

有 id 的行与无 id 的行永不互相匹配（一侧换过 id 的"同一条"会各留一行 —— 保守，符合
"宁可两条、不可误合"）。真实快照：9200 → 9172，折叠的 28 条**全部**同 `cites_id`；无 id 的
2191 条一条未丢。

**回归测试**：`tests/test_output_dedup.py` 的 `OutputDedupSnapshotRegressionTest` 直接读
`paper_downloader/main/paper_citations.json`（只读、文件不存在即 skip，保持 hermetic），断言的是
**语义**（"只有同 id 才允许折叠、无 id 一条不丢"）而不是 9172 这个数字 —— 该快照每天重抓都会变。

## 2026-10-08: "看起来像 id 的哨兵值"比空值更危险

scholarly 用行的 `data-cid` 拼 `url_scholarbib`；行没有被建 cluster 时属性缺失，`.get()` 返回 `None`，于是 URL 里出现 `info:None:scholar.google.com`。原正则 `info:([^:]+):` 会**成功匹配**并把 `'None'` 当 cluster id 存下来。

为什么比"取不到"更糟：`cites_id` 同时是**身份键**和下游的**主键**。空值只是退化为 `title+venue` 兜底键；而 `'None'` 会让所有这类行共享同一个键 `cites_id\tNone` —— 不同论文被当成同一条被去重，下游还会拿一个不存在的 id 去查 progress。

判据：**解析外部 id 时，正则匹配成功 ≠ 取到了合法值**。凡是把字符串当标识符的地方，都要显式排除"空、None、null、n/a"这类占位串，并且最好收敛到一个 `_clean_*` 函数（本次是 `_clean_cites_id`），让所有入口共用。

---

## 2026-10-08: 一个布尔字段不能同时回答两个问题

`complete_fetch_attempt` 被三个地方读作"本次抓取是否正常跑完"，却被 `_compute_data_complete()` 按"数据是否完整"写。两个问题在不同时点取数（写快照时 vs 读状态时），于是同一个文件里 `fetch_complete` 与它互相矛盾 68/247 篇——**不是有人写错，而是一个名字承担了两个语义**。

落地约定：
- `fetch_complete`（输出条目）= **数据完整性**，唯一权威是 `PaperFetchState.is_complete()`；
- `_fetch_state.complete_fetch_attempt` = **本次尝试是否干净跑完**（`empty_page`/`empty_first_page` 记为 False）；
- `rough` 模式要的是后者，`need_fetch()`/状态判定要的是前者。

同类教训：`seen_total` 也曾同时被当作"去重行数"和"缓存+重复数"。**给字段定语义时先问：谁读它、要回答什么问题**；一个字段被两类读者用时，拆开而不是折中。

### 派生结论：状态不能压过证据

"诊断说完整"是**声明**，"citations 数组里有行"是**证据**。只信声明时，一篇 0 行的论文会被判 complete 并永远跳过。规则：**声明与证据冲突时，以证据为准，并且冲突本身要能触发重抓**（`resolve_citation_status_from_output()` 在诊断为完整但数组为空时返回 partial）。
同理，Excel 的行号不能用数据里的编号（`pub['no']`）而要用位置——编号缺失或重复时位置仍然唯一。

---

## 2026-10-08: "两份入口"与"先问后加载"——两个反复出现的结构性坑

### 一份入口只能有一个

`code/cli.py` 曾复制了 `scholar_citation.main()` 的实现并分叉出必崩代码（对 `AuthorProfile` 调 `.get()`），而且**死代码不会自己暴露**：它没有调用方、没有测试，只在阅读时才发现。判据：**同一职责若有两份实现，删掉非活路的那份**，而不是让它们"保持一致"。

### 决策函数的输入必须已经就绪

`has_pending_work()` 在 `main()` 里先于 `run()` 被调用，而它依赖的输出状态是在 `run()` 里加载的。结果：判断恒为"有活"，短路分支成为死代码，且没有任何报错。
→ 判据：**任何"根据状态做决定"的方法，要么自己保证输入就绪（本项目选择在方法内按需加载），要么在签名上强制传入**。不要依赖"调用顺序恰好正确"。

### 开关的三种坏状态

`-v` 被接受但从不生效（接线在死代码里）；`--accelerate` 在缺少另一个开关时被静默忽略（README 又把它列为独立开关）；`--limit 0` 因 falsy 从"0 篇"变成"全量"。
→ 判据：**显式传入的参数要么生效、要么报错，不能静默改变含义**。范围校验放在 `parse_args()`：非法值直接 `parser.error`，比在深层的 `if self.limit and ...` 里靠真值判断安全得多。

---

## 2026-10-08: 写盘路径的三条硬规则

1. **状态文件必须原子写**：本项目把"跨轮状态"放在输出 JSON 里，半截文件读回就是"没有任何状态"（citations 输出）或直接崩（profile）。`write_json_atomic()`（tmp + fsync + `os.replace`）是唯一写入方式；读取侧遇到坏文件要**备份 + 明确告警**，不能静默 `{}` —— 静默会让"整库重抓"看起来像正常运行。
2. **附属产物不能阻断主产物**：Excel 是给人看的视图，它的失败只应降级为警告。原实现的失败点在 JSON 之后且无保护，于是"数据已存但进程报错退出"，还会盖掉末尾的 Done 汇总。
3. **`_save_output` 必须幂等**：退出路径会保存两次（显式 + `finally`）。幂等的前提是"不销毁下次保存还需要的内存态"——原实现在保存里清空 `_mid_paper_state`，第二次保存就退化成运行前旧状态。规则：**清理放在运行开始，不放在写出里**。

另外：profile 报告某篇 `num_citations == 0` 时，`skip_zero` 分支不得覆盖 `citations: []` —— "Scholar 说 0"和"我们没抓到"是两回事，前者不应删除已抓到的数据。

---

## 2026-10-08: 续抓的三条状态通道，哪些能跨运行、哪些不能

### `_start_index` 的语义（最容易写错的一处）

它同时被当作两样东西用：**页对齐重取的依据**（`page_start` + `in_page_skip`）和**已保存条数**（重试跳过）。这两者在正常情况下相等，只在"某行被消费后失败"时分离。此时必须把游标退回——否则重试会跳过那一行（静默丢一条引文）。
→ 规则：**append 之前的失败要回退，append 之后的失败不回退**。这是"游标语义"与"保存语义"同源导致的必然歧义，改代码时先想清楚这一点。

### `seen_total` 必须按"去重后的行数"记

年扫描续抓时会用该年缓存**播种** batch，因此"返回行里已缓存的部分"会被登记为 dedup；而 `seen_total = cached_total + dedup` 会把它们算第二遍。缓存桶已是 `cached ∪ new`，所以 `seen_total` 只能等于桶的行数，dedup 只能是诊断信息。
→ 判据：**任何"缓存数 + 重复数"的口径，都要先确认重复项是否已在缓存数里**。本项目在 direct（batch 不从缓存播种，dedup 是批内重复）与 year（batch 从缓存播种）两条路径上的答案不同，helper 不能共用同一个公式。

### 哪些续抓状态可以跨运行持久化

| 状态 | 能否跨运行 | 原因 |
|---|---|---|
| `partial_year_start`（年内偏移） | **能** | 它只描述"这一年在哪个位置断了"，而该年按定义仍不完整；probe 变大时正好从断点继续 |
| `direct_resume_state`（direct 偏移） | 只同 run | 跨运行信任旧偏移，等于假设列表头部没变；direct 列表短，重走代价可接受 |
| `completed_years`（本 run 已完成年份） | **不能** | 年份循环的"已完成"判断在 probe 比较**之前**，跨运行保留会让 probe 说"这年变多了"的年份也被跳过 |

### year 模式的 materialize 不能被"变小即合并"覆盖

`_materialize_citation_cache` 的"结果比缓存小就按 identity 合并"是为 **direct**（fresh 就是整份列表）设计的。year 模式已经在逐桶做 replace/merge 决策，再套一层合并会把 `--fetch-mode exact` 有意缩小的桶又填回去。故加 `authoritative` 参数由调用方声明语义。

---

## 2026-10-08: 页面级重试必须有退出条件（PageVisit 的无限循环）

### 为什么"无限重试"看起来安全，实际最危险

`PageVisit.fetch()` 只 `continue`、从不 `raise`，于是**所有上层放弃逻辑都成了死代码**：`scholar_citation` 的 `MAX_RETRIES` 分支不可达、`wait_proxy_switch_fn` 无人调用、日志里"Rate-limit cooldown"每 5-10 分钟一行却永远没有结论。生产日志里 182h/59h 的单轮运行、6 页请求就是这种空转的直接证据。

判据：**"重试"必须有一层负责终止**。页面级只做"短重试 + 冷却"，冷却若干次后把控制权交回上层（上层的重试预算、退出码、存档逻辑都在那里）。

### 三个 stdin 陷阱（无人值守时同时出现）

1. 非 TTY 下不要走交互提示：`prompt_first_curl()` 有 `isatty()` 守卫，`try_interactive_captcha()` 漏了 → 在 `except` 分支里再抛异常，直接终止整轮。
2. `sys.stdin.fileno()` 必须在 `try` 内：真实管道/重定向下它会抛 `io.UnsupportedOperation`。
3. `select([stdin])` 在 stdin 已 EOF 时**恒为可读**，`readline()` 恒返回空串 → 24 小时 100% CPU 空转。空串必须当作"stdin 已关闭"处理，而不是"用户没输入"。

---

## 2026-10-08: stall 计数为什么一直没落盘（"内存正确、写出被覆盖"）

### 症状与定位

`stalled` 状态在代码、文档、测试里都存在，生产上却从未出现：247 篇输出里 `stall_signature` 全为空、76 个 run 日志里 `stalled` 0 次，`Mirror-Generative`(0/49) 从 8 月到 10 月每轮仍被计划 `+49 needed`。

链路是"写两次、后一次覆盖前一次"：

1. 抓取成功后主循环把 `_paper_states[title]` 换成 `PaperState(fetch=from_dict(快照).inherit_stall_tracking(旧))`，然后 `record_fetch_outcome()` 更新计数 —— 内存里正确。
2. 收尾 `_save_output()` 的 `_freshest_fetch_state()` **优先取 in-run 快照**（快照带 `fetch_strategy`，因为 year 模式总要带 live probe），再用 `PaperFetchState.to_dict()` 序列化 —— 而 `save_progress()` 构建的快照里没有 stall 字段（那时也还没有：快照在抓取过程中写，`record_fetch_outcome()` 在抓取返回后才调用）。于是计数被静默清零。

### 修法与验证方法论

- 修法：`_freshest_fetch_state()` 在用快照作为数据源时，从 `paper_states[title]`（抓取后状态）**额外搬运** `stall_signature`/`stall_count`；数据源本来就是 `paper_states` 时不动。
- 关键点：签名必须取自**快照派生的状态**，因为下一次运行对比的是落盘后的状态。测试里显式用 `PaperFetchState.from_dict(mid_snapshot).current_outcome_signature()` 生成签名，这样测试同时钉住"签名跨写盘/读回不漂移"。
- 方法论：**回归测试必须走生产路径的那条分支**。原测试 patch 了 `_load_citation_cache` 却从不设置 `_mid_paper_state`，于是 `using_snapshot` 恒为假——测试覆盖的是"没有快照"的分支，而生产永远有快照。凡是"同一份状态有两个来源、按条件二选一"的代码，测试必须显式覆盖**两个**来源，而不是让 mock 顺带决定走哪条。

---

## 2026-10-08: 「抓取返回值」与「权威性」的判定原则

### 一条通用规则

**一次抓取的返回值只能覆盖它确实覆盖到的数据；返回条数少于已缓存时必须合并，不能替换。**

原因：HTTP 200 + 无异常 ≠ 拿到了完整列表。空页（consent/拦截/未识别中间页）与截断页都会"静默成功"，而 year 模式的年份桶、direct 模式的整体列表都以"fetch 正常结束"为权威信号。旧实现在 year 模式下用空桶替换该年缓存（删数据），在 direct 模式下用 fresh 整表替换（同样删数据），两者只有在"整份列表为空"时才有兜底。

落地判据（按数据形状，而不是按"是否抛异常"）：
- 明确信号（`empty_page`）：该年 probe>0 却 0 行 → 完全不碰该年桶，且该年不计入 `completed_year_segments`（本 run 不重试意味着下轮才重试，这符合"空页是临时状态"的判断）。
- 弱信号（返回条数 < 缓存条数）：按 identity 合并（缓存行 + 新行），保留 `--fetch-mode exact` 的权威重建语义。
- 权威信号（返回条数 ≥ 缓存条数）：整表替换，允许丢掉 Scholar 不再返回的陈旧行。

### probe 权威性与"不在直方图里"

`no_probe_data_skip` 的语义是"该年不在 Scholar 直方图里 → 没有新内容"。这只有在**直方图对本篇论文权威**时成立。探测失败时 `scholarly_session.probe_citation_start_year()` 的回退路径（DOM 选择器失配走 Fallback 1/2/3）只收集年份集合、**不写 `probed_year_counts`**，于是下游拿到空字典——此时"不在直方图里"退化为"我们什么都不知道"，必须抓取而不是跳过。
→ 判据：跳过需要 `histogram_authoritative = Σprobe > 0`；否则打印原因并抓取。该分支原先也不看 `--fetch-mode`，所以 `force` 都救不回来，这是"静默缺口"的根源。

**同一份状态在两处被解读时的配套要求**：既然"探测失败"要退化为"什么都不知道"，那么探测函数必须在**所有出口**（含失败 return）清空 `probed_year_counts`。原实现的复位写在"页面取回成功之后"，失败路径直接 return，于是上一篇论文的直方图被当作这一篇的期望值使用——这是"同名不同物"在 probe 上的第三次复发（前两次是 `partial_year_start`、`dedup_count`）。

---

## 2026-10-08: NO_PROXY 与 scholarly 补丁的启动期契约

### 为什么必须在 import scholarly 之前处理 NO_PROXY

httpx 0.27.2 在 `Client.__init__` 里把 `NO_PROXY` 的每一项解析成 URLPattern：非 IPv6 形态才走 `all://*{host}`。`is_ipv6_hostname('[::1]')` 为假（带方括号），于是生成 `all://*[::1]`，`URLPattern` 构造时抛 `InvalidURL: Invalid port: ':1]'`。scholarly 的 Navigator 是 import 期单例，其 ProxyGenerator 立刻建 `httpx.Client`，所以失败点在 `import scholarly`，不在我们的代码里。

→ 任何依赖 httpx 的进程都不能假设环境干净。规范化必须放在"包被导入时"和"主入口导入 scholarly 之前"两处，且必须幂等（两处都会执行）。

### 补丁点自检：为什么值得在启动时直接 raise

12 个补丁点里多数属于"赋值成功但语义可能变"的类型。属性被改名 → `AttributeError`（响亮）；符号还在但语义变了 → 静默失效。`_citedby_long` 就是活样本：文档里写了半年"按年份分段加延迟/续跑"，实测一次都没执行（scholarly 只在对象带 `bib` 键时才走到它，而 direct 的 citedby stub 没有该键）。

→ 判断一个补丁点该不该保留的标准：先用 grep 证明补丁体在真实调用路径上可达，再写进文档。自检只能覆盖"改名"这一类，但成本极低（9 次身份比较），能把"升级 scholarly 后悄悄失去限速/浏览器头"变成启动错误。

---

## 2026-10-04: "重复结果就停止重试"（stall）的设计与边界

### 规则与实现

需求（用户定）：**连续两次得到相同结果，在"当前状态下"不再重试**。"当前状态"用**结果签名**表达——签名只包含会随抓取变化的量，因此 Scholar 计数一变，签名就变，自动恢复重试。

- 签名（`code/citation_cache.py: fetch_outcome_signature`）
  - direct：`direct:{seen}/{scholar_total}`（例 `direct:0/49`）
  - year：`year:{Σseen}/{Σhist}={缺口数}[{短年:seen/hist},...]`（例 `year:241/235=2[2026:64/66]`）——**必须带上短年**，否则"填平 A 年、B 年仍缺"会被误判为"没变化"。无 diagnostics → 返回 `''`（不参与判定）。
- 状态（`code/output_state.py`）：`_stall_signature` + `_stall_count`，随 `_fetch_state` 持久化；`record_fetch_outcome()` 在每次抓取返回后更新（相同 → count+1，不同 → 重置为 1）；`is_stalled()` = `count >= STALL_AFTER_ATTEMPTS(2)` 且当前签名与记录一致且无 `scholar_changed`。
- 状态值：`resolve_citation_status_from_output()` 新增 **`stalled`**（仍在 need_fetch 之外 → 不发请求）。主循环打印 `-> stalled (same result N runs in a row)` 并保留已抓到的 citations；运行摘要打印 `Stalled (skip): N`；计划末尾打印 `Skipping N stalled paper(s)`。
- 阈值：`code/config.py: STALL_AFTER_ATTEMPTS = 2`（经 common 再导出）。

### 三个必须注意的边界

1. **中断不计入**：`record_fetch_outcome()` 只在抓取**正常返回**后调用；抛异常/被 Ctrl+C 的尝试到不了那里。所以封锁、验证码这类"报错型失败"不会把论文判成 stall（否则两次偶发失败就会误停机）。
   但**"0 行/短页型"失败是正常返回的**——如果连续两次都是"页面被静默截断"，会被判 stall。这是该规则的固有权衡（用户明确接受 2 次采样）。
2. **状态重建必须继承计数**：每次抓取成功后 `_paper_states[title]` 会被 `save_progress` 写的 cache 快照**整体替换**，而快照不知道 stall 字段——不处理的话计数每轮都从 1 重新开始，规则永远不生效。`inherit_stall_tracking()` 在替换处搬运；相关回归测试 `test_two_identical_outcomes_stall_the_paper_across_runs` 覆盖"连跑两轮后写盘再读回仍 stalled"（同时验证签名跨持久化不漂移）。
   → **2026-10-08 更正：这一条只做对了一半。** `inherit_stall_tracking()` 让内存里的计数正确，但**落盘**时 `_save_output._freshest_fetch_state()` 又会用 in-run 快照覆盖（快照带 `fetch_strategy` 却没有 stall 字段），刚记下的计数被丢掉 —— 生产数据里 247 篇没有一条带 `stall_signature`，`stalled` 一次都没出现。那个回归测试之所以是绿的：它 patch 了 `_load_citation_cache`，从不填充 `_mid_paper_state`，于是走了 `paper_states` 回退分支，恰好绕开了出问题的分支。详见下方 2026-10-08 节。
3. **手动解锁口**：`--fetch-mode force --skip N-1 --limit 1` 会清掉该论文状态（stall 字段随状态一起消失）→ 立即重试。若某次 stall 是误判（例如让代理换了 IP、Scholar 页面变正常），用这个恢复。

### 与"作者页计数 > 实际可列出条目"的关系

`Mirror-Generative`（0/49）经用户在浏览器确认是 Google 侧空页；同族的 `PRIMT`(31/32)、`Getting more from less`(16/17)、`Learning to generate personalized…`(16/17)、`LLMs are limited in out-of-context…`(8/9) 是"少 1 条"。这些论文在**连续两轮**产出同一签名后会自动停抓（此前每轮白跑一次请求/整张列表）。当前已有数据里没有任何 stall 字段 → 首次生效前它们各需再抓 2 轮。

---

## 2026-10-04: "0 行页面"的诊断设计，以及 probe 年份记录为什么不能丢

### 0 行页面为什么必须显式记录

一次请求完全成功（HTTP 200、无异常）却解析出 **0 行**，与"这篇论文确实没有引用"在抓取路径上**无法区分**：迭代器直接 StopIteration，批量层只看到"列表耗尽"。而 scholarly 自身只拦带 captcha id 的页（`gs_captcha_ccl` / `recaptcha` / `captcha-form`，以及会抛 `DOSException` 的 `rc-doscaptcha-body`），所以能走到解析层的 0 行页**必然不是验证码页**——真实原因只剩：空结果集 / 同意页 / 未识别的中间页（重定向由 scholarly 的 302 分支在会话内跟随）。

因此诊断点必须放在**唯一能看到原始页面的地方**：`patched_load_url`。原来的写法

```python
if page_size:                       # 0 行 → 什么都不打印
    print(f"        Page items: {page_size}")
```

把已经算出来的关键事实丢掉了。现在改为：引用列表页 0 行时调用 `record_zero_row_citation_page()`，打印
`Page items: 0 (no citation rows) [no results container; banner='...'; markers=...]`
并把证据存进 `ctx.last_page_evidence`。证据字段：`rows` / `results_container`（是否有 `#gs_res_ccl_mid`）/ `result_banner`（`gs_ab_mdw` 里的结果计数或提示文案）/ `markers`（captcha、consent、unusual_traffic、no_results）。

随后由**知道期望值的调用方**下判断，把术语写进状态，便于事后区分：

| 场景 | termination_reason |
|---|---|
| direct：0 行但 `scholar_total > 0` | `empty_first_page` |
| year：某年 probe > 0 但该年 0 行 | `empty_page` |
| 期望本来就是 0（空年份 / 首次就无引用） | 保持 `iterator_exhausted`，不误报 |

注意：`termination_reason` 只被存储/展示，**任何代码都不据此分支**，所以新增取值是安全的（各 normalizer 都以 `iterator_exhausted` 为默认值原样透传）。

### probe 年份记录被丢弃 → 不只丢日志，还会误判完整

`build_year_records()` 里"哪些年份记录保留"的过滤条件原本读**外层** `year_ctx.probed_year_counts`：

```python
for year, diagnostic in list(diagnostics.items()):
    if year not in year_counts and year not in (year_ctx.probed_year_counts or {}):
        diagnostics.pop(year, None)          # ← 外层永远是 None
```

而 probe 只写在内层 ctx（`_fetch_by_year` 自建并同步到 `fetcher._live_probed_year_counts`），外层 ctx 的 `probed_year_counts` 恒为 `None`（`fetch_citations_with_progress` 里创建时显式写死）。于是当一次抓取**完全没抓到引用**时，所有年份记录会被清空——正好是最需要证据的情形（`empty_page` 被抹掉）。更严重的是第二个后果：

`build_citation_count_summary()` 在存在逐年记录时，`histogram_total = Σ 各年 histogram_count`。丢掉"有 probe、无缓存"的年份记录，也就把**那几年的 probe 数从聚合里删掉**，聚合因此可能满足 `seen ≥ hist` → 论文被判 complete → 该年永远不再重抓。可复现的例子：2024 抓到 2/2、2026 probe=3 但页面 0 行 —— 旧代码聚合只剩 2/2，判"完整"；修复后保留 2026(0/3)，判"未完成"并重抓。这条路径**要求"某年被 probe 说有引用、而该年一次抓取后仍为 0"**，与"详细引用下载没有完成"的现象一致，是此前"永远 complete"类问题的可达成因之一（`_update_history.zh.md` 与本文档都已更正先前过强的归因）。

### 排查方法论补充

- **"静默成功"比报错更危险**：凡是"请求成功但结果为空/异常小"的地方，都要在**知道期望值**的那一层留下可区分的记录（本项目的期望值来源：direct 的 `scholar_total`、year 的该年 probe 数）。
- **同一份状态在两处被解读时，先对齐口径来源**：内层/外层 ctx、live 与持久化 probe 这类"同名不同物"的字段，是本项目反复出问题的形式（此前 `partial_year_start`、`direct_resume_state`、`dedup_count` 都有同类记录）。
- 审查日志时注意：**没打印不代表没发生**。本次两个信息丢失点都是"算出来了但不打印/不保留"。

---

## 2026-10-04: 完整性判定为什么必须"按年"，以及中断状态为什么会丢

### year 模式：聚合 `<` 单年

`histogram_total`/`seen_total` 都是各年求和，因此 `Σseen ≥ Σhist` **不蕴含** 每年 `seen ≥ hist`。缓存桶与 Scholar 直方图本来就不一致（缓存里可能有直方图不列的年份、或某年多于 probe），于是"某些年多出来的量"会抵平"另一年真正没抓全的缺口"。

判定口径必须与抓取时的跳过口径一致：`fetch_by_year` 决定跳过某年用的就是 `prev_seen >= live_count`（该年自己的数），所以完整性也必须逐年比较，否则会出现"抓取逻辑认为该年该抓、完整性逻辑认为整篇已完成"的自相矛盾，而赢的总是后者（状态被写成 complete → 不再进 need_fetch）。

落地：
- `year_records_data_complete(records, summary)` = 聚合成立 **且** 每年 `seen_total >= histogram_count`（无 records 时退化为原聚合规则，保持旧状态兼容）。
- `year_records_gap(records)` 返回 `(各年缺口之和, Σseen, Σhist)`；Fetch plan 的 `needed` 用它，展示时附 `[short years: 2026(4/5)]`。
- 逐年的 `seen_total` 在 `_normalize_year_fetch_diagnostics` 里取 `max(记录值, cached+dedup)`，因此只要该年确实抓够过就不会误报缺口——不会引入"永久 incomplete"式的反复重抓。

### 中断状态：`_mid_paper_state` 是内存态，落盘的是运行前旧状态

- `save_progress()` 把每篇论文的完整快照（含本次 live probe 的 `year_records`、已抓到的 `citations`、`direct_resume_state`）写进 `_mid_paper_state`，**只在内存**。
- 正常收尾时 `_save_output()` 从前一处读到的快照刷新 `_paper_states[title]`，所以"成功的抓取"没问题；但被中断的论文不会走到那一步，`_save_output` 只能写运行开始时的旧状态，并且随后把 `_mid_paper_state` 清空 → 本次的 probe/引用/断点全部蒸发。
- 后果：不仅断点丢失（`partial_year_start`、`direct_resume_state` 本来就不跨运行持久化），更严重的是**论文被判为完整**，于是下次连"要不要重抓"都不会问。

落地：`_save_output()` 优先取 in-run 快照（状态与 citations 都取），但**只允许自带 `fetch_strategy` 的快照覆盖旧状态**。原因（踩过的坑）：direct 模式中途 save 的快照里 `fetch_strategy`/`direct_fetch_diagnostics` 都还是 `None`（它们要等 batch 跑完才建），而 `_infer_strategy_and_summary()` 会因为存在 `year_records` 把它推断成 year 模式，再按 year 的按年规则判成 complete —— 一篇只抓到 30/45 的 direct 论文就会被写成完整。回归测试 `test_save_output_ignores_direct_snapshot_that_lacks_diagnostics` 固定这个行为。

### `complete_fetch_attempt` 改为三态（None=未知）

历史遗留：该字段 2026-04-17 引入，5 月才补齐"从快照恢复"，期间落下的 `False` 既可能表示"真没跑完"，也可能是"当时没写对"。文件里 `false` 与"键缺失"（旧状态）被 `bool(...)` 压成同一个值，无法区分。现在 `from_dict` 把缺失/`null` 映射为 `None`（未知），显式布尔才是确定值；写入侧用与 `is_complete()` 相同的按年规则计算，保证文件不自相矛盾。**注意：`complete_fetch_attempt == False` 目前不参与任何"要不要抓"的决策**——若要据此重抓，会波及 65 篇陈旧状态（其中只有 1 篇真有可观测缺口）。

### 排查方法论（本次踩的坑）

1. **不要用 `len(citations) vs scholar_total` 判断"抓全没有"**：year 模式不覆盖 unyeared 引用（`histogram_total` 天然小于 `scholar_total`），direct 模式的 dedup 会让 `seen > cached`。权威口径是 diagnostics 的 `seen_total`。
2. **复现 `cache_status` 的原地修改**再做离线验证：它会把 `num_citations_on_scholar` 同步成档案页计数、必要时置 `scholar_changed`。少了这一步，status 会算成 complete，从而得出"plan 与 status 不一致"的假结论（本次已撤回）。
3. **日志解析要锚定行首**：`^\[(DIRECT|YEAR)\]` / `^\[N/247\]`，否则会把日志里其他位置的同名文本当成计划项，得出错误的"计划里 X 却没抓"。
4. 验证"新规则会不会引发大规模重抓"：用真实输出跑一遍 `run()`（`_run_main_loop` 打桩），对比新旧规则的 `need_fetch` 集合。本次为 52 → 52（零新增）。**打桩时务必照抄 `results[:] = [None] * len(publications)`**，否则 `_save_output` 会把空结果写出去（本次在临时副本上踩到过，真实输出未被波及）。

### 已知未修（需产品决策）

- **`scholar_unyeared_count` 的真实含义**：它是 `scholar_total − histogram_total`，即 Scholar 自己的"Cited by N"与它自己直方图各年之和的差，**不是**"我们抓不到的无年份引用"。命名有误导性：year 模式在年份批次里会做 year 回填（`code/citation_fetch.py:843` 传 `fallback_year=year`，`citation_identity.py:101` 在 bib 无 `pub_year` 时用它），因此"在某年标签下抓到的引用会被填上该年"并正常计入该年 `seen`；对差额代码也不是放弃，而是放宽遍历起点（conservative start_year）。
  直方图本身并不可靠，双向都会错：真实数据里存在 `cached_total > histogram_count` 的年份（如 `Deep matrix factorization` 2024 cached 177 / probe 169，`Adaptive nearest neighbor` 存 143 > "Cited by" 141），说明年份查询能返回比直方图更多的条目。
  **未验证**：那些差额引用是否在任何年份标签下都不会被返回（若确实不会，year 模式就系统性覆盖不到它们，需要一条全量/无过滤通道）。验证方法：挑一篇这样的论文做一次无过滤（direct）全量抓取，与 year 模式的结果比对。
- **direct 模式中断**：快照里没有 dfd，因此只能保住"已抓到的引用"，缺口仍靠旧状态判断（若旧状态完整、只因 `scholar_changed` 才重抓，中断后可能丢掉这次重抓）。彻底解法是让 `save_progress` 在 direct 中途也生成一份 `direct_fetch_diagnostics`。
- **`Mirror-Generative` 类（0/N）**：**已确认是 Google 侧问题**——用户在浏览器直接打开该 `oi=bibs&cites=...` 链接，Scholar 自己的页面就返回空结果（作者页却写 "Cited by 49"），所以既不是爬虫解析、也不是封锁/中间页。诊断已就位（日志会打印 `Page items: 0 (no citation rows) [证据]`，状态记为 `empty_first_page`），但**重试策略未改**：该论文每轮仍会发一次请求并支付延迟（约 1 页），只是现在日志和状态都明说了是空页。若要收敛（例如连续 N 轮空页后标记为"Scholar 无此列表"并暂停重抓、待引用数变化时再试），属行为变更，需另行决定。
  同族的另外 4 篇（`PRIMT` 31/32、`Getting more from less` 16/17、`Learning to generate personalized…` 16/17、`LLMs are limited in out-of-context…` 8/9）是"页面能返回但比作者页计数少 1"的情形，未逐篇在浏览器确认，但很可能是同一个"作者页计数 > 实际可列出条目"的成因。

---

## 2026-08-13: 浏览器指纹（UA/sec-ch-ua）从 cURL 联动采用的设计

### 为什么之前写死 Chrome 145

`_CURL_HEADER_ALLOWLIST` 的原设计注释：user-agent / sec-ch-ua / sec-ch-ua-full-version-list "必须保持彼此同步"，所以禁止从 curl 覆盖。这个顾虑本身成立，但结论错误——真正该做的是把它们**作为一组整体**从 curl 采用，而不是永远写死默认值。

### 新设计：全有或全无 + 版本一致性

`complete_browser_fingerprint(header_overrides)`（在 `code/scholarly_session.py`）：

1. **完整性**：三者任一缺失 → 整组丢弃（保留硬编码默认指纹），避免部分覆盖产生新的矛盾
2. **版本一致性**：正则 `\b(?:Edg|Edge|Firefox|OPR|Chrome)/(\d+)` 提取 UA 主版本，与 `sec-ch-ua` 中所有 `v="(\d+)"` 比对；UA 版本不在集合中 → 整组丢弃
3. **通过**：打印 `Adopted browser fingerprint from cURL`，三者在 session headers 中生效

### 覆盖的三条路径

- 启动 `_patch_scholarly` → `_inject_cookies_from_curl(saved_curl)`（从 curl.txt 自动加载）
- 交互式粘贴（`prompt_first_curl` / `try_interactive_captcha`）
- 403 重建 session：`_full_session_setup` → `_apply_browser_headers` 先应用硬编码默认、再 `update(ctx.injected_header_overrides)`，注入值覆盖默认值

### 测试与实现不一致的修复

`tests/conftest.py` 手工维护的 allowlist 已包含 `sec-ch-ua` / `sec-ch-ua-full-version-list` 但不含 `user-agent`，与生产 `_CURL_HEADER_ALLOWLIST` 漂移。改为 setUp 时直接引用 `_CURL_HEADER_ALLOWLIST` 常量，避免再次漂移。

### 排查方法论沉淀：cookie vs IP 级封锁的判别

当"刚注入另一台电脑的新鲜 cookie，下一条请求立即仍被 block"时，说明封锁对象是 **IP**（Scholar 的 sorry/429 是 IP 级累计计数），cookie 无法解除。判别依据：注入 cookie 后 `curl_page_count` 从 1 开始，第一个请求无延迟立即发出仍被拒。换 IP 才能解除。

### 操作注意（非代码）

- 当前 `output/curl.txt` 是登录账号 cookie（SID 全家桶 15 个），抓引用不需要登录，建议改用**未登录浏览器**的匿名 cookie（NID/AEC 即可），并注意勿把真实凭据留在仓库。
- 同一 IP 换 cookie 无法解除封锁，必须换代理 IP。

---

## 2026-08-11: 交互式 cookie 粘贴的误操作防护

### 设计

`_inject_with_cookie_retry()` 是共享的"粘贴直到成功或跳过"循环：

- **成功判定**：`inject_fn` 返回注入的 cookies 数 > 0
- **跳过**：按 Enter（空输入）→ 返回 False
- **重试**：`inject_fn` 返回 0（无 cookies）→ 打印重试提示，重新进入读取循环

### 为什么用无限重试而不是次数上限

交互式场景下用户随时可 Ctrl+C 退出或按 Enter 跳过，无限循环不会卡死。次数上限反而可能让用户在关键 captcha 恢复时被迫放弃。每次重试提示都说明"可能复制了错误的请求"，引导用户从 DevTools 重新复制。

### 与 scholar_citation.py 的接线

`prompt_first_curl` / `try_interactive_captcha` 接收的 `inject_fn` 是 `PaperCitationFetcher._inject_cookies_from_curl`（返回 cookies 数），辅助函数用它判定成功/重试，无需改动调用方签名。

---

## 2026-08-11: curl_cffi 全面失败 → 改用 scholarly 原生 httpx（最终结论）

### 为什么 curl_cffi 无论如何都被 429

**现象**：crawler 引用页（`/scholar?cites=`）429，但 profile 页（`/citations`）正常；浏览器和命令行走同一代理。

**对照实验**（同一代理 185.94.29.86、同一 header、同一 cookies）：
- 标准 curl (libcurl) → 引用页 200 ✅
- httpx 0.27.2 → 引用页 200 ✅
- **curl_cffi（所有 impersonation 目标）→ 引用页 429/超时** ❌

**结论**：curl_cffi 的 TLS/HTTP2 指纹在 Scholar 的引用搜索端点（`/scholar?cites=`）被识别。这个端点反爬检测比 profile 页严格得多。TLS 指纹模拟（chrome145 等）无法通过——**问题在库本身，不在指纹目标选择**。

**教训**：TLS 指纹模拟不是万能的。Google 的反爬除了看 TLS 指纹，还会综合 HTTP/2 帧细节、请求时序等。curl_cffi 模拟 Chrome 145 指纹与真实 Chrome 存在微小差异（如 ECH 扩展配置不完整），在宽松端点不触发、在严格端点（引用搜索）触发。

### httpx 替代方案

scholarly 原生就用 httpx.Client（`_session1`/`_session2`）。改造极简：

```python
def _make_http2_session():
    return httpx.Client(http2=True, follow_redirects=True)
```

- httpx 的 `session.headers.update()` / `session.cookies.set()` / `cookies.clear()` 与 curl_cffi 完全兼容（interactive.py 的 cookie 注入无需改动）
- `_BROWSER_HEADERS`（Chrome 145 headers）照常注入
- httpx 已在 scholarly 依赖链中，无需新增

### 经验总结

1. **排查顺序**：先排除 TLS/header/cookie（profile 页 200 证明链路对），再怀疑 HTTP 客户端库。对照实验（不同库同配置）是决定性手段。
2. **不要迷信 TLS 指纹模拟**：curl_cffi 声称"浏览器不可区分"，实际在 Scholar 引用端点上失败。scholarly 原生的 httpx 反而成功。
3. **proxy 出口 IP 的验证方法**：`httpbin.org/ip` 看出口 IP，`--noproxy '*'` 绕代理看真实 IP，`scutil --proxy` 看系统代理，FlClash 控制 API（`127.0.0.1:9090/configs`）看 TUN 状态。

---

## 2026-08-11: 下载失败定位 = 代理 IP 限流（非 TLS/cookie）+ Fetch plan 缺口语义

### 429 限流的排查方法论

**现象**：浏览器能访问 Scholar，但爬虫 citations 页面报 `Cannot Fetch from Google Scholar`。

**排查层次**（从协议栈自顶向下）：
1. **TLS/headers/cookies** → 用爬虫的 patched session（curl_cffi chrome145 + 注入 cookies）直接请求：
   - Profile 页面 HTTP 200，含作者名 → TLS/headers/cookies **正确**
   - Citations 页面 HTTP 429 → **IP 级限流**
2. **网络路径** → 对比出口 IP：
   - 代理出口：185.94.29.86（海外，被 Scholar 限流）
   - 直连：202.119.41.110（南京，Google 被墙，连接超时）

**结论**：爬虫必须走代理（直连被墙），但当前代理节点 IP 已被 Scholar 标记（429）。换代理节点即可。**这不是代码问题**——profile 200 已证明整条 TLS/header/cookie 链路正确。

**区分手段**：profile 页面 vs citations 页面。Scholar 对普通页面和 citations 搜索结果页的限流策略不同——citations 页（`/scholar?cites=`）触发频率限制更敏感。测试时务必分别请求两类 URL，不要只测首页。

### scholar_changed 残留导致完整论文被重复抓取

`cached == scholar_total` 且 `is_complete() == True` 的论文，因 `scholar_changed` 标志残留（上次引用数变化时 `mark_scholar_changed`，fetch 中断未 `clear`），`need_fetch()` 仍返回 True。这些论文数据完整、无真实缺口，重抓只是浪费请求（且加重限流）。

Fetch plan 修复仅过滤了**显示**。深层问题（`scholar_changed` 残留导致重复抓取）仍在——完整论文仍会被程序重抓。若后续需要，可在 `cache_status` 中对 `is_complete()` 且 `seen >= target` 的论文清除残留的 `scholar_changed`。

### Fetch plan 缺口语义

真实缺口 `needed = max(0, target - seen)`，其中：
- year 模式：`target = histogram_total`，`seen = seen_total`
- direct 模式：`target = scholar_total`，`seen = seen_total`

**不要用 `len(citations) vs scholar_total` 算缺口**——year 模式下 cached 可能含 unyeared 条目（不算 seen），direct 模式下 dedup 使 `seen > cached`。基于 diagnostics 的 seen/target 才是权威。

---

## 2026-08-11: curl_cffi TLS 指纹与 conda 环境踩坑

### curl_cffi 版本与 TLS 目标的关系

| curl_cffi | 支持的最高 Chrome | 备注 |
|-----------|------------------|------|
| 0.13.0 | chrome131 | 仅 Python 3.9（base env 实测）|
| 0.14.0 | chrome131 | **scholar 环境当前版本**，macOS arm64 可正常导入 |
| 0.15.0 | chrome150+ | **在 macOS arm64 上损坏**：`_CFRelease` 符号缺失，`import curl_cffi` 即崩溃 |

关键教训：
- `Session(impersonate='chrome145')` **创建 session 不报错**，但发起实际请求时才抛 `ImpersonateError`。因此**必须实测请求**（`s.get('https://httpbin.org/ip')`）验证目标可用，仅创建 session 不够。
- 不支持的目标会静默回退到无 TLS 伪装 → Google 在 TLS 层直接识别爬虫。这与 headers/cookies 是否正确无关。
- `_BROWSER_HEADERS` 的 user-agent / sec-ch-ua / sec-ch-ua-full-version-list 必须与 `_IMPERSONATE_TARGET` 版本严格一致（如 chrome131 → Chrome/131.0.6778.86）。

### sec-fetch-site 一致性规则

`sec-fetch-site` 取值与是否已建立会话强相关：
- **冷启动**（无 cookie）：`'none'`（首次导航从外部进入）
- **已注入浏览器 cookie 或已访问过 Scholar 页面**：`'same-origin'`（后续都是同源导航）

Cookie 注入和 session 重建（`_full_session_setup`）时必须显式覆盖为 `same-origin`，否则"有 session cookie" + "声称跨站"的自相矛盾会成为反爬信号。

### conda 环境中 pip→conda 迁移的坑

**场景**：`pip install` 的包卸载后，`conda install` 报 "All requested packages already installed"，但实际 `site-packages` 文件缺失。

**根因**：pip 卸载删除了 site-packages 中的实际文件，但 conda 的 `conda-meta/*.json` 记录仍存在（或 conda 求解器把已卸载的包视为已满足版本约束）。conda 不会主动替换/补全被 pip 删掉的文件。

**修复**：`conda remove --force <pkg>` 清理不一致的 metadata → `conda install <pkg>` 重新完整安装。涉及包：requests、selenium、free-proxy。

**验证技巧**：`conda list` 显示 conda-forge 来源 ≠ 文件完整。用 `importlib.util.find_spec` 或直接 `import` 验证；检查 `site-packages/<pkg>/` 是否存在实际 `.py` 实现（曾出现 selenium 只剩 `.pyi` 存根、requests 只剩 `_types.py` 的情况）。

### curl-cffi 必须用 conda-forge 版本（关键发现）

**pip 版 0.15.0/0.16.0 在 macOS arm64 上全部损坏**：`import curl_cffi` 崩溃，0.15.0 报 `_CFRelease` 符号缺失、0.16.0 报 `_CFArrayAppendValue` 符号缺失。这是 PyPI wheel 编译时未正确链接 CoreFoundation 框架。

**但 conda-forge 编译的 0.15.0 完全可用**！conda-forge 用自己的编译链（正确链接了 CoreFoundation），`import` 正常，且支持 `chrome145`（0.15.0 最高目标）。已通过临时 conda 环境实测验证。

**结论**：curl-cffi 必须用 `conda install -n scholar curl-cffi=0.15.0 -c conda-forge` 安装，不能用 pip。代码 `_IMPERSONATE_TARGET` 用 `chrome145`（最高支持目标）。若用 pip 0.14.0（可用但最高仅 chrome131），需把 TLS 目标和 headers 一起降回 131。

---

## 2026-07-05: 结构化日志系统 + 配置集中化

### `code/log_utils.py` — 日志架构

**背景**：项目原先使用 `TeeStream` 类 + `sys.stdout` 替换来同时输出到控制台和日志文件。这导致所有 `print()` 输出混在一起，无法区分日志级别，没有 `--verbose` 控制。跨项目评审（与 citation_analysis_content 对比）建议迁移到 Python `logging` 模块。

**设计决策**：
1. **双路径并存，逐步迁移**。TeeStream 继续捕获所有 `print()` → stdout + log file。Logging `FileHandler` 写入同一 log 文件，但仅处理 logger 输出。两者不重复（logger → FileHandler，print → TeeStream，独立路径）。全部迁移完成后再移除 TeeStream。
2. **Console 使用 stderr**。`StreamHandler(sys.stderr)` 输出结构化日志到 stderr，避免污染 stdout（stdout 仍由 TeeStream 管理，用于 `print()` 输出）。
3. **文件 handler 始终写 DEBUG 级别**。即使 console 为 INFO，文件始终记录完整日志，便于后期排查。
4. **日期格式区分**。Console 用 `%H:%M:%S`（简洁），文件用 `%Y-%m-%d %H:%M:%S`（多天运行可辨识）。

### `code/config.py` — 配置集中化

**背景**：原先所有调优常量散落在 `code/common.py` 顶部，没有单一配置入口。

**实现方式**：
- `code/config.py` 为配置的 canonical 来源
- `code/common.py` 通过 `from code.config import ...` 重新导出所有常量
- 所有现有 `from code.common import DELAY_MIN, ...` 无需修改
- 新增代码建议直接 `from code.config import ...`

### `run.sh` — 便捷入口

自动检测 conda 环境，若无 conda 则使用系统 python。

### 项目结构更新

```
code/
  ├── config.py          # 新增：集中配置（canonical 来源）
  ├── log_utils.py       # 新增：结构化日志
  ├── common.py          # 修改：常量迁移至 config.py，保持 re-export
  └── cli.py             # 修改：新增 --verbose / -v 参数，setup_logging() 集成
```

## 2026-06-16: exact 模式 `force_year_rebuild` + `year_records` 修复 + captcha 计数修正

- **captcha 计数修正**：`inject_cookies_from_curl` 是 cookie 注入的通用入口，在三种场景下被调用：程序启动加载 `curl.txt`、首次 curl 提示、captcha 恢复。之前该函数每次调用都递增 `captcha_solved_count`，导致启动加载 cookie 被误计为 1 次 captcha solve。修复：将计数器递增从通用注入函数移至 `_try_interactive_captcha`（仅在实际 captcha 恢复时调用），同时移除 `inject_cookies_from_curl` 的 `captcha_solved_count_ref` 参数。
- **exact 模式年份数据替换而非合并**：normal 模式下，年份重新抓取后新旧数据合并（`materialized_citations(fetch_finished=False)` 合并 new + old）。但 exact 模式要求 `seen == probe` 才跳过，如果 `seen > probe` 就重抓，重抓后应该以新数据为准，丢弃旧数据中超出 probe 的部分。修复：`force_year_rebuild = (self.fetch_mode == 'exact')`，materialize 阶段用 `force_year_rebuild=True` 触发完全替换。
- **`save_progress` 中的 `year_records` 补充**：`year_records_to_save` 在 progress save 时缺失，导致断点续传时 year_records 数据无法从 `_mid_paper_state` 恢复。已补充到 `save_data` dict 中。

## 2026-06-05: BeautifulSoup `class_` 精确匹配陷阱

### 问题

`cea24c7` 中将 CSS 选择器从 `div.gs_r`（过宽，匹配错误页面元素）改为 `class_='gs_r gs_or'`。但 BeautifulSoup 的 `class_` 参数做的是**精确字符串匹配**：

- `class="gs_r gs_or"` → 匹配
- `class="gs_r gs_or gs_scl"` → **不匹配**（多了 `gs_scl`）

Google Scholar 的结果行 class 经常包含第三个 class `gs_scl`，导致部分结果行被漏掉（每页最后一条）。

### 修复

`2fa9c58`：`soup.find_all('div', class_='gs_r gs_or')` → `soup.select('div.gs_r.gs_or')`。CSS 选择器 `div.gs_r.gs_or` 匹配同时拥有 `gs_r` 和 `gs_or` 两个 class 的元素，不管还有没有其他 class（真正的 AND 语义）。

### curl_cffi TLS 指纹

`04d5797`：`_make_http2_session()` 从 `httpx` 切换到 `curl_cffi.requests.Session(impersonate='chrome124')`。`curl_cffi` 模拟 Chrome 124 的 TLS 握手签名（包括 cipher suites、extensions 顺序等），Google 无法从传输层区分 crawler 和浏览器。这解决了即使 cookies/headers 完全正确，crawler 仍被阻止的问题——根因在 TLS 指纹不一致。

## 2026-05-12: 代码规范修复

- **`index_year_records()`**: 从 `scholar_citation.py` 中重复 3 次的 year_records 解析逻辑提取的共享函数，位于 `code/output_state.py`。接受一个 year records 列表，返回 `{year: record}` 字典。
- **`_direct_fetch_diagnostics_message()`**: 合并了原 `_direct_fetch_summary_message` 和 `_direct_fetch_log_message`，通过 `prefix` 参数区分不同场景。
- **Bug**: `fetch_by_year` 中的 `year_fetched_citations` 变量从未赋值，已替换为 `year_batch.citations`。

## 开发环境

- **Conda 环境**: `scholar` (`/Users/huangshujian/miniforge3/envs/scholar`)
- **Python**: 3.11
- **scholarly package**: `/Users/huangshujian/miniforge3/envs/scholar/lib/python3.11/site-packages/scholarly`
  - 关键文件: `publication_parser.py` (PublicationParser, _SearchScholarIterator)
  - 关键文件: `_scholarly.py` (scholarly core)

## 项目结构

```
google-scholar-citation-crawler/
├── run.sh / my_citation.sh      # 薄启动脚本（python -m code.scholar_citation）
├── output/                      # 运行期产物：抓取产物 + logs/
├── code/                        # 唯一代码目录（包名 code）
│   ├── scholar_citation.py      # CLI 入口 + PaperCitationFetcher 编排器
│   ├── __init__.py
│   ├── common.py                # 常量 + 无状态工具函数
│   ├── author_fetcher.py        # AuthorProfileFetcher
│   ├── profile_io.py            # profile JSON / Excel 输出
│   ├── config.py                # 集中配置常量 + crawler_config.json 读取/解析
│   ├── log_utils.py             # 结构化日志（setup_logging / get_logger）
│   ├── lib_env.py               # 第三方库环境摆设 + NO_PROXY 规范化（唯一 os.environ 豁免）
│   ├── citation_cache.py        # year-count / diagnostics 纯函数
│   ├── citation_strategy.py     # fetch policy、refresh 策略、reconciliation
│   ├── citation_identity.py     # citation 去重键与信息提取
│   ├── citation_io.py           # cache I/O、status 推导、citations Excel 输出
│   ├── citation_fetch.py        # fetch_citations_with_progress + fetch_by_year
│   ├── fetch_session.py         # BatchFetchSession, YearFetchSession
│   ├── page_visit.py            # PageVisit — 每页 captcha/proxy/retry 恢复
│   ├── scholarly_session.py     # SessionContext + scholarly monkey-patch + year probe
│   ├── interactive.py           # cURL cookie 注入、captcha 提示、proxy-switch 等待
│   ├── citation_models.py       # YearRecord, ResumeState, FetchPolicy
│   ├── output_state.py          # PaperFetchState / PaperState dataclass + 输出文件 _fetch_state 读写
│   ├── venue_detail.py          # 出版详情页 → pub['venue_full']（按 token 缓存）
│   ├── venue_quality.py         # 「venue 是否已完整 / 是否预印本源 / 作者是否全名」纯判据
│   ├── venue_key.py             # venue → 身份键 venue_key（会议=名+年、期刊=缩写、预印本=服务器）
│   ├── cite_format.py           # Cite 对话框五格式 + BibTeX → citation['cite']（默认开启）
│   ├── pub_info.py              # PubInfo dataclass（pub 字段规范化）
│   ├── cli.py                   # parse_args() + _run_main(args)
│   └── data_upgrade/            # 一次性数据修复/升级工具（**与主线入口分开**，不算主线模块）
│       ├── __init__.py
│       ├── fix_diag_from_year_record.py  # python -m code.data_upgrade.fix_diag_from_year_record <output_json>
│       ├── fix_output_fetch_state.py     # python -m code.data_upgrade.fix_output_fetch_state [output_dir]
│       └── normalize_venue_key.py        # python -m code.data_upgrade.normalize_venue_key [--apply]
├── tests/                       # 单元测试（当前 475 个，不需要网络）
│   ├── conftest.py              # 共享 stubs + FetcherTestCase 基类
│   └── test_*.py                # 20 个文件，每个覆盖一个功能域
│                                #   （逐文件清单见 AGENTS.md 的「Testing notes」—— 那里是唯一权威，
│                                #    本节不再复制一份，免得两处各自过期，K80）
├── _update_history.zh.md        # 按时间顺序的更新历史
├── _user.zh.md                  # 用户输入记录
├── requirements.txt             # scholarly>=1.7, openpyxl>=3.1, httpx[http2]==0.27.2
│                                #   （curl_cffi 已于 2026-08-11 弃用并从依赖中移除，见下文「curl_cffi 全面失败」记录）
└── README.md                    # 对外功能说明
```

### 输出文件

- `author_{id}_profile.json` / `.xlsx` — 作者信息 + 论文列表
- `author_{id}_paper_citations.json` / `.xlsx` — 逐篇引用列表
- `output/curl.txt` — Cookie 持久化

---

## 快速使用

```bash
# 安装依赖
pip install -r requirements.txt

# 日常使用
python -m code.scholar_citation --author YOUR_AUTHOR_ID

# 测试少量
python -m code.scholar_citation --author YOUR_AUTHOR_ID --limit 2
python -m code.scholar_citation --author YOUR_AUTHOR_ID --limit 1 --skip 1
```

### CLI 参数

| 参数 | 说明 |
|------|------|
| `--author` (必填) | Scholar author ID 或完整 URL |
| `--output-dir` | 输出目录，默认 `./output` |
| `--skip M` | 跳过前 M 篇论文（按引用数降序） |
| `--limit N` | 在 skip 之后处理 N 篇 |
| `--fetch-mode` | `rough` / `normal` / `exact` / `force`（默认 normal） |
| `--interactive-captcha` | 交互式验证码恢复 |
| `--cite-formats` / `--no-cite-formats` | 施引条目的 Cite 对话框五格式 + BibTeX（**默认开启**，见 `code/cite_format.py`） |
| `--proxy-url` / `--no-proxy` / `--config PATH` | 代理与配置文件（优先级：参数 > `crawler_config.json` > 默认） |
| `--accelerate SCALE` | 等待倍率（默认 1.0，0.1 = 10× 快） |
| `-v` / `--verbose` | 打开 debug 日志 |

---

## 运行流程

**跨运行状态原则**：每次运行只从**输出文件**（profile JSON、citations JSON）读取上一轮状态。同次运行内的中断恢复通过内存 (`_mid_paper_state`、`_paper_states`) 实现，不再使用磁盘缓存文件。

1. **Profile 阶段**（每次都运行）
   - 阶段1：获取作者基本信息（name, affiliation, citation stats）
   - 阶段2：获取所有论文列表（含 citedby_url）。引用数未变时复用 `prev_profile['publications']`（来自输出文件，不读 `pubs_cache`）
   - 增量比较：与上次 profile 对比，发现新增论文、引用数变化
   - 保存 JSON + Excel + 追加 history
   - 保存 JSON + Excel + 追加 history

2. **智能跳过判断**
   - 比较本次与上次的 `total_citations` 和 `total_publications`
   - 都没变 → 跳过 citation 抓取

3. **Citation 阶段**（有变化时才运行）
   - 逐篇获取引用来源列表
   - 增量缓存 + 中断续传
   - 保存 JSON + Excel

---

## 等待与超时机制

### 统一等待策略

所有主动等待统一使用 45-90 秒随机值（`rand_delay()`）：
- Profile 阶段：搜索作者后、两阶段之间
- Citation 阶段：翻页时、年份段切换时

每次主动等待都会输出等待信息。论文间没有额外延时（每页请求前已有足够延时）。

### 心跳超时机制

- **心跳间隔**：10 秒检查一次（`HEARTBEAT_INTERVAL = 10`）
- **超时阈值**：80 秒无响应（`HEARTBEAT_TIMEOUT = 80`）
- **超时处理**：保存进度后 `os._exit(1)` 强制终止

### 主动等待与超时计时的关系

关键设计：主动等待不计入超时计时。
- 进入主动等待前设 `_deliberately_waiting = True`，心跳线程跳过检查
- 主动等待结束后设 `_deliberately_waiting = False`，**同时重置 `_last_activity = time.time()`**

---

## scholarly 限流对策

### Monkey-patch（运行时替换，不修改 scholarly 源码）

1. **翻页随机延迟**：替换 `_SearchScholarIterator._load_url`，每次翻页前等待 45-90 秒
2. **年份切换随机延迟**：替换 `scholarly._citedby_long`，按年份分段时加延迟
3. **Session 刷新**：随机 10-20 页刷新 scholarly 内部 httpx session

### citedby_url 前缀修正

scholarly 的 `_get_soup` 会在 URL 前拼接 `https://scholar.google.com`，但缓存中的 `citedby_url` 已是完整 URL，需要先剥掉前缀。

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
- `num_citations > 1000`：走 `_citedby_long()` → 按年份分批抓取

---

## 运行状态管理

### 两层内存结构

所有中间状态通过两个内存结构管理，不使用磁盘缓存文件：

| 结构 | 类型 | 内容 | 生命周期 |
|------|------|------|---------|
| `_paper_states` | `{title: PaperState}` | `PaperState(fetch=PaperFetchState, citations=list)` — 每篇论文的 diagnostics + 引用列表 | 从输出 JSON 加载，运行中逐篇更新，结束时写回 JSON |
| `_mid_paper_state` | `{title: cache_dict}` | 当前论文的中间进度（`save_progress` 写入） | 每页更新，retry 时读取，运行结束时清空 |

`PaperState` 是一次查询即可获取全部论文数据的封装类，取代了之前 `_output_fetch_state` + `_output_citations` 两个需要分别按相同 key 查询的 dict。

### 命名约定

- **`strategy`** = `year` / `direct`（`fetch_policy['strategy']`、`fetch_strategy`、`PaperFetchState.fetch_strategy`）
- **`mode`** = `rough` / `normal` / `force`（`--fetch-mode` CLI 参数、`self.fetch_mode`）
- `direct_resume_state` 中的 `'mode': 'direct'` 已移除，改用 `ResumeState` 对象

### 核心 dataclass 一览

| 类 | 位置 | 用途 | 字段数 |
|-----|------|------|--------|
| `AuthorProfile` | `profile_io.py` | 输出文件 author profile | 4 + 3 计算属性 |
| `PaperState` | `output_state.py` | 运行时论文状态容器（fetch + citations） | 2 |
| `PaperFetchState` | `output_state.py` | 输出文件 `_fetch_state` | 11 |
| `PubInfo` | `pub_info.py` | 输出文件 `pub` | 8 |
| `YearRecord` | `citation_models.py` | 单年抓取记录（I/O） | 6 |
| `ResumeState` | `citation_models.py` | 断点续传位置（运行时） | 3 |
| `FetchPolicy` | `citation_models.py` | 抓取策略决策（运行时） | 3 |

### 输出文件 `_fetch_state` 字段（11 个）

```json
{
  "title": "...",
  "pub_url": "...",
  "citedby_url": "...",
  "fetch_strategy": "year" | "direct",
  "num_citations_on_scholar": 1200,
  "scholar_changed": false,
  "complete_fetch_attempt": true,
  "year_fetch_diagnostics": {
    "histogram_total": 1200, "scholar_total": 1210,
    "cached_total": 1195, "cached_year_total": 1195,
    "seen_total": 1205, "cached_unyeared_count": 0,
    "dedup_count": 1, "scholar_unyeared_count": 10
  },
  "direct_fetch_diagnostics": {
    "scholar_total": 46, "cached_total": 46,
    "seen_total": 46, "dedup_count": 0,
    "termination_reason": "iterator_exhausted"
  },
  "year_records": [
    {"year": 2024, "histogram_count": 50, "cached_total": 49,
     "seen_total": 50, "dedup_count": 0, "termination_reason": "short_page_stop"},
    {"year": 2025, ...}
  ],
  "fetched_at": "2026-05-07T12:00:00"
}
```

**关键设计**：`year_fetch_diagnostics` 是纯 summary（8 字段），per-year 条目在 `year_records`（独立顶层列表）。`direct_fetch_diagnostics` 是扁平 5 字段结构（不含 `histogram_total` 等 year 字段）。

### 不在 output 中的字段

`num_citations_seen`、`cached_year_counts`、`dedup_count`、`complete` — 均可从 diagnostics 推导。

### 数据流：year_records → diagnostics

```
fetch 过程:
  year mode: per-year fetch → YearRecord (histogram_count 来自 probe)
  direct mode: 引用抓取 → YearRecord (histogram_count=0, 无 probe)

save_progress:
  YearRecord 列表 → build_citation_count_summary → year_fetch_diagnostics (summary)
  direct fetch → _build_direct_fetch_diagnostics → direct_fetch_diagnostics

输出:
  PaperFetchState.to_dict() → 规范化所有字段 → 写入 output JSON
```

### normalize 调用链

`from_dict` 入口 → `_normalize_year_summary_dict`（year summary）→ `_normalize_direct_diagnostics`（direct summary）→ `_normalize_year_records`（per-year 列表）。`to_dict` 出口同样调用这些函数，入出两端规范化。

### partial_year_start 的语义

- **只在同一次运行内有效**，不写入缓存文件
- 当某一年处理到一半被中断，记录已处理位置
- 必须是**页面对齐的**（10 的倍数）
- 跨运行时永远为空 `{}`

### PaperFetchState 封装原则

`PaperFetchState` 所有字段以 `_` 前缀实现为私有字段，外部代码**禁止**直接赋值。访问和修改只能通过以下途径：

- **初始化**: `from_dict()` 类方法
- **更新**: `from_dict()` 整体重建 / `restore_year_diag_from_year_records()` / `restore_direct_diag_from_citations()`
- **标志位**: `mark_scholar_changed()` / `clear_scholar_changed()`
- **查询**: `need_fetch()` / `is_complete()` / 只读 property

Fetch 完成后用 `PaperFetchState.from_dict(cache_snapshot)` 整体替换实例；字段级恢复方法返回 `self` 以支持链式调用。

### cache_status 与 PaperFetchState 的关系

`cache_status(pub)` 闭包是获取 paper 状态的统一入口，返回 `(st, cached_dict, paper_state)` 三元组：

- **`paper_state`**（`PaperFetchState`）：权威对象。所有字段更新（`num_citations_on_scholar`、`mark_scholar_changed()`、diagnostics 修正）都直接作用于它
- **`cached_dict`**（`dict`）：从 `paper_state.to_dict()` 派生，额外包含 `citations` 列表（来自 `_paper_states[title].citations`），仅用于传递给 `_resolve_refresh_strategy` 的接口兼容
- **`st`**（`str`）：`missing` | `complete` | `partial` | `skip_zero`，由 `_citation_status()` → `need_fetch()` → `is_complete()` 决定

关键设计：`paper_state` 是 `_paper_states[title].fetch` 中的同一个对象引用，所以 `cache_status` 对它的修改会直接影响内存状态，即使该 paper 被跳过（`complete`）也会保持数据一致。

---

## Citation 状态判定

### complete/partial 规则

- **Year mode**: `year_fetch_diagnostics.histogram_total <= seen_total` → complete（`year_fetch_diagnostics` 自身就是 summary）
- **Direct mode**: `direct_fetch_diagnostics.scholar_total <= seen_total` → complete
- 无 diagnostics → `PaperFetchState.is_complete()` 已封装，内部 fallback

### Fetch policy 选择

- 引用数 < 50 → `FetchPolicy(strategy='direct')`
- 引用数 >= 50 → `FetchPolicy(strategy='year')`
- `FetchPolicy.is_year()` / `is_direct()` 辅助方法；`__getitem__`/`get()` 兼容 dict 访问

### 断点续传

- 统一用 `ResumeState`（`next_index`, `source_scholar_total`, `citedby_url`）
- `page_start()` 返回页对齐位置，`in_page_skip()` 返回页内偏移
- Direct 模式：单个 `ResumeState` → 内存 `_mid_paper_state`
- Year 模式：`completed_year_segments`（已完成年份集）+ `partial_year_start`（`{year: int}`，年份内断点位置）

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
| Run Metadata | 运行级元数据（author_id, fetch_time, totals） |

---

## scholarly 内部机制

### 源码位置
`<python-env>/lib/python3.11/site-packages/scholarly/`

### citedby() 的两条路径

| 条件 | 路径 | 实现 |
|------|------|------|
| `num_citations <= 1000` | 简单路径 | `PublicationParser.citedby()` → `_SearchScholarIterator` 直接翻页 |
| `num_citations > 1000` | 长路径 | `_citedby_long()` → 按年份分段，每段用 `search_citedby(id, year_low, year_high)` |

### _SearchScholarIterator 翻页

- 每页 10 条结果
- `__next__()` 检查是否有 `gs_ico_nav_next` 按钮决定是否翻页
- `_load_url()` 调用 `_nav._get_soup(url)` → `_get_page()` 发起 HTTP 请求

### _citedby_long 按年份分段策略

- 从 `citedby_url` 中提取 publication ID（`cites=[\d+,]*`）
- `source == AUTHOR_PUBLICATION_ENTRY` 时：先 `fill()` 获取 `cites_per_year`，用 `_bin_citations_by_year()` 分组
- `source == PUBLICATION_SEARCH_SNIPPET` 时：逐年遍历（当前年 → 最早年）
- 单年仍超 1000 时只能拿到 1000 条（Scholar 硬限制）

### 403 / 限流处理（_navigator._get_page）

- **每次请求前**：`random.uniform(1, 2)` 等 1-2 秒
- **首次 403**：立即重试，新 session
- **后续 403**：`random.uniform(60, 120)` 等 60-120 秒
- **最多重试**：`_max_retries` 次（默认 5）
- scholarly 的 60-120 秒等待会超过心跳 80 秒超时 → 已通过 monkey-patch 解决

### `as_sdt` 参数说明

`as_sdt` 格式：`as_sdt=<类型>,<地区码>`
- 类型：`0`=排除专利，`7`=包含专利，`2005`=citation 搜索专用内部标志
- 地区码：`5`=全球默认，`33`=特定地区（会过滤部分结果）

当前方案：使用 `as_sdt=0,5`（排除专利 + 全球地区），配合 `sciodt=0,5`。

### 续传时 >1000 引用的问题

- `_citedby_long` 无法用 `&start=N` 跳页（按年份重新查询）
- 续传时只能逐条 skip 已有的引用，需要重新翻过所有已缓存的页面
- 大量重复请求极易触发 Scholar 限流

---

## `_scholar_pub` 不设置 `cites_id`

scholarly 的 `_scholar_pub()` 方法（用于 _SearchScholarIterator 解析 citedby 结果）从不设置 `cites_id` 字段，只设置 `citedby_url`（仅在引用有 "Cited by N" 链接时）。解决方案：三级 fallback — `pub['cites_id']` → `pub['citedby_url']` 解析 `cites=` → `pub['url_scholarbib']` 解析 `cid`。

---

## 关键 Bug 记录

### year_fetch_diagnostics dedup_count 跨次运行丢失（2026-04-17）

内外两个 FetchContext 不同步：`_fetch_by_year` 创建独立内部 ctx，`save_progress` 闭包读外部 ctx。修复：wrapper 同步内部 ctx → fetcher 属性 → save_progress。

### 分页机制冲突导致每页丢失 1 条（2026-04-30）

scholarly 的自动分页与手动分页冲突，`original_next` 自动加载下一页时丢弃当前页第 1 条。修复：满页时也 break，分页控制权完全交还给 while True 循环。

### cites_id 全为 null（2026-05-01）

`_scholar_pub()` 不设置 `cites_id`。修复：从 `citedby_url` 提取。

### 重试时 output state 覆盖 cache 导致丢失引用（2026-05-06）

`retry_strategy_cached` 优先使用 output state（不含 citations）。修复：缓存文件优先。

### _max_retries=0 导致 scholarly 循环不执行（2026-06-02）

**错误理解**：以为 `nav._max_retries` 是“重试次数”，设成 0 让 scholarly 不重试。

**实际情况**：scholarly `_get_page`（`_navigator.py:110`）的 while 条件是 `while tries < self._max_retries`。`_max_retries=0` 导致 `0 < 0` → False，循环体完全不执行，HTTP 请求从未发出，直接抛 `MaxTriesExceededException("Cannot Fetch from Google Scholar.")`。

**正确做法**：`_max_retries=1`（`nav._set_retries(1)`）确保循环至少执行一次。403/captcha 重试试用 `continue`（不增加 `tries`），不消耗重试预算。睡眠限制由 `_limited_sleep` 通过 `MAX_SLEEPS_PER_PAGE` 实现。

### curl 浏览器指纹一致性问题（2026-06-02）

**问题**：cookies 从浏览器 cURL 加载时，`sec-ch-ua` 被 allowlist 覆盖为正确版本（如 Edge/146），但 `user-agent` 不在 allowlist 中，保持硬编码的旧版本（Edge/145）。Google 检测到两个 header 声明的浏览器版本不一致，判定为机器人。

**修复**：将 `user-agent` 加入 `_CURL_HEADER_ALLOWLIST`。

**cookie 解析**：原始代码只识别 `-b '...'` / `--cookie '...'` 格式，现代浏览器输出 `-H 'cookie: ...'` 格式被忽略。增加 `-H 'cookie:'` 回退解析。

---

## 环境说明

- **Conda 环境**：`scholar`
- **关键包**：`scholarly==1.7.11`、`openpyxl==3.1.5`、`curl_cffi>=0.7`、`httpx==0.27.2`
- **TLS 指纹**：`curl_cffi.requests.Session(impersonate='chrome124')` 模拟 Chrome 124 的 TLS 握手签名，Google 无法从传输层区分 crawler 和浏览器
- **代理**：通过系统 `https_proxy` 环境变量生效，`curl_cffi` 自动读取

---

## 2026-10-11：cookie 获取机制的两处加固 + 可选自动收割

背景：用户要求对比阶段 2（paper_downloader）与阶段 1 的 cookie 获取机制、并在确认有实际便利后
互相借鉴。结论是"cookie 本身不通用（域名不重叠：scholar.google.com vs 出版商/机构），但机制互补"。

### 1. `curl.txt` 权限（借自阶段 2 的 cookies.txt 规则）

`save_curl_to_file()` 原名 `open(path, 'w')`，按 umask 落成 0644 —— 整段 cURL 里含
`-H 'cookie: ...'`（活的 `GSP`/`NID` 会话）与 UA 三件套，同机其它用户可读。
现改为 `os.open(..., 0o600)` + `os.chmod` 纠正历史宽松权限（对应 `output/curl.txt` 已存在
3,665 字节的旧文件）。`output/` 本来就在 `.gitignore` 里，所以这不是入库泄露，而是本机最小权限问题。

### 2. 粘贴值清洗

`inject_cookies_from_curl._parse_cookies()` 原样接受 `k=v`。若粘贴内容里混入换行
（DevTools 复制到多行、终端粘贴断行），值里会带 `\n` 并进入 session header 与 curl.txt。
新增模块级 `_strip_control()` 去掉 `\r\n\t`；空名直接丢弃。

### 3. 可选：从真实浏览器自动收割（借自阶段 2 的 `--attended`）

- 入口：cookie 提示处输入 `browser` + 回车（提示文案只在 `_playwright_available()` 为真时多打一行，
  没有 playwright 的用户看到的界面与行为**完全不变**）
- 实现：`try_browser_harvest(url, inject_fn=...)` 用 `sync_playwright()` 开**可见**浏览器
  （macOS 优先 Edge 可执行文件，root 时补 `--no-sandbox`）→ goto → 等人按回车 →
  `ctx.cookies(url)` 取 cookie + `navigator.userAgentData.getHighEntropyValues([...])` 取
  品牌/完整版本/平台 → `_build_curl_from_page()` **合成一段 cURL** →
  交给既有的 `inject_cookies_from_curl` 完成解析、白名单头、指纹一致性校验、会话注入与落盘
  （即：复用全部既有路径，存储格式不变）
- 失败面全部落回手工粘贴：没装 playwright / stdin 非 TTY / 浏览器起不来 / 收获 0 条 cookie /
  用户在重问循环里改贴 cURL
- 关键设计：**不引入硬依赖** —— playwright 只在 `try_browser_harvest` 内部 import，且先探测可用性；
  `_inject_with_cookie_retry()` 新增可注入的 `reader` 形参，让这套交互可以 hermetic 测试

### 不采纳的建议（附理由）

| 建议 | 为什么不采纳 |
|------|--------------|
| 改存 Netscape、丢弃原始 cURL | 原始 cURL 正是 UA/sec-ch-ua 三件套的载体（`complete_browser_fingerprint` 依赖它保持指纹一致），只存 cookie 反而更差 |
| 给 `session.cookies.set` 加域名限制 | 安全性有提升，但 GS 会跳 `googleusercontent` 等域，域写错会静默少发 cookie；风险高于收益，仅记录 |
| 阶段 2 侧也存 sec-ch-ua 三件套 | 出版商不校验该头，收益低于复杂度 |

### 测试

`tests/test_cookie_acquisition.py`：0600（含纠正旧权限）、往返、CR/LF 清洗、
`browser` 关键字路由、收割失败回落粘贴、空输入跳过、无 playwright/无 TTY 时不动作、
合成 cURL 含 cookie 与身份三件套且三件套在 `_CURL_HEADER_ALLOWLIST` 内。


## NO_PROXY 消毒：引擎与策略分离（2026-10-14）

四个仓库过去各有一份 `sanitize_no_proxy`，且**行为已分叉**（本仓库解括号 + 计数；3.a.1/3.a.2/3.b
丢弃 + bool），两边都有测试钉死，所以共享的是**引擎而不是策略**：

- 引擎（`code/shared/lib_env.py::sanitize_entries`，宿主＝阶段 1）：`env` 默认 `os.environ`、
  遍历两种大小写、`raw` 没有 `[` 就跳过、按 `,` 拆分并 trim、命中计数、`join` 后回写、幂等。
- 策略参数：`keys`（哪些变量）、`pattern`（编译好的正则，可带捕获组）、`unwrap`（命中后保留捕获组
  还是整条丢弃）、`count`（返回计数还是 bool）、`always_write`（含 `[` 但没命中时是否照样回写 ——
  本仓库历史行为是回写）。
- 跨仓库消费路径：3.x 各自的 `code/_shared.py` 把阶段 1 的 `code/` 追加进 `sys.path`（机制 A，
  `shared` 是 PEP 420 命名空间包），然后 `from shared.lib_env import sanitize_entries`。
- 解码细节：`ast.get_source_segment()` 会去掉首行缩进，改文件时要补回 4 空格，否则语法错误。


## 阶段 1 抓取器审计修复的技术细节（2026-10-14，第二轮）

### 探针为什么会被计两页（①）

`probe_citation_start_year()` 直接调 `nav._get_soup(citedby_url)`。scholarly 1.7.11 的
`Navigator._get_soup()` 实现是 `self._get_page('https://scholar.google.com' + url)` + BeautifulSoup，
而 `nav._get_page` 已被本仓库替换为 `patched_get_page()`，其中 `ctx.total_page_count += 1`。
所以"探针自己 +1"就是同一个 HTTP 请求被计两次。**判据**：只要请求最终经过 `nav._get_page`，
页数就已在那一处统计；任何在调用前手动加页数的代码都是重复计数。
探针测试若整体打桩 `nav._get_soup`（原有 `test_probe_failure_clears_previous_paper_histogram`
就是这样），就绕过了 `patched_get_page`，所以覆盖不到这条路径。

新用例的写法：保留 stub `nav`，但把**真实** `scholarly.Navigator._get_soup` 绑定到它
（`types.MethodType`），底层 `_get_page` 由 lambda 返回固定 histogram HTML。取真实函数需要在
`sys.modules` 里临时摘掉 scholarly stub、`importlib.import_module('scholarly')`，取完再把
stub 放回去（与既有 `test_real_scholarly_still_exposes_every_patched_hook` 同一套路）。

### 抓取失败的哨兵语义（②）

`fetch_publications()` 的返回值现在有三种含义：`[非空列表]` 抓到了、`[]` 抓到了但确实没有论文、
`None` 抓取失败。区分 `[]` 与 `None` 是必要的，因为 `run()` 的写盘决策依赖它。
`run()` 的规则：

- `None` + 旧 profile 有 publications → **完全不写**（JSON 逐字节不变），保留旧列表，打印告警，返回 True。
  选"完全不写"而不是"用旧 publications 写一份新 profile"，是因为后者会混搭"新 basics + 旧列表"，
  而且 `fetch_time`/`change_history` 都会动，旧文件就不再是可回退的权威快照。
- `None` + 旧 profile 没有 publications（或根本没有旧 profile）→ 与改动前一致，照旧保存空列表。
- 非 `None` → 原逻辑。

`main()` 在 `run()` 之后重新 `load_prev_profile()`，所以"没写盘"就意味着 Phase 2 拿到的是盘上那份
旧 profile（含旧 publications），不会出现"Phase 1 内存里有 0 篇 → Phase 2 按 0 篇写输出"的二次损坏。

### 加速系数的接线点（③）

`ctx.wait_proxy_switch_fn` 是**零参**回调：`page_visit.py` 与探针都写 `ctx.wait_proxy_switch_fn()`，
多个测试也用零参 lambda。改调用点签名会连带改这套约定，所以 scale 只在一个地方接线 ——
`PaperCitationFetcher._wait_proxy_switch()`（`ctx.wait_proxy_switch_fn` 的实际绑定对象）把
`self._delay_scale` 传给 `interactive.wait_proxy_switch(max_hours, scale)`。这样两个调用点、
以及未来任何经 ctx 的调用，都自动缩放。

测试用"假时钟"替代 `code.interactive.time`，并把 `select.select` 换成"把 timeout 加进假时钟、
返回空就绪列表"的桩，于是循环按虚拟时间走完：24h×1.0 → 86400s，24h×0.1 → 8640s（不真等）。

### close() 的边界：哪些旧 client 不能关（④）

三处替换都补了 `_close_quietly()`，但有一个**故意不关**的：

- scholarly `Navigator._get_page()` 的 403 分支是 `self._new_session(premium=...)` 然后 `continue`，
  而循环里发请求用的是**循环外读到的局部 `session` 变量**（指向替换前的那个 client）。
  这个 client 我们无法安全关闭，否则该次重试会变成"用已关闭的 client"
  （httpx 会抛 `RuntimeError: Cannot send a request, as the client has been closed.`）。
- 事实上它在生产里**已经**被 scholarly 自己关掉：`pm._new_session()` 会 `_close_session()` 掉
  `pm._session`，而 `pm._session` 恰好就是替换前的 `nav._session1`。这是 scholarly 的既有行为，
  本仓库不修；`PageVisit` 会把随之而来的异常当作一次失败尝试，下一次 `patched_get_page()` 读到的
  已是新 client，所以自愈。
- `patched_handle_captcha2()` 里新 client 同时占 `_session1`/`_session2`，关闭旧的两个时用
  `is not new_session` 排除新 client；旧的两个可能是同一个对象（上一次 captcha 重建造成的），
  httpx `Client.close()` 幂等（本机验证：连关两次 OK），所以重复关闭无害。
- `patched_new_session()` 关的是 `original_new_session()` **刚建出来又被我们立刻丢掉**的那个
  （生产里就是 `pm._session`）；scholarly 的请求路径只读 `nav._session1/_session2`，
  `pm._session` 只被我们已经替换掉的 webdriver 版 `_handle_captcha2` 使用，所以安全。

### requirements 的 http2 extra（⑦）

`httpx.Client(http2=True)` 在 0.27.2 上构造时就 `import h2`，缺包抛 `ImportError`（不是发请求时才抛）。
`httpx[http2]` 只是 extras 语法，装的东西与 `httpx` 相同外加 `h2`，所以对外行为不变。
守卫测试读 `requirements.txt` 断言 `httpx[http2]==`，把"干净安装能跑起来"钉在源码层。

## 2026-10-14: 出版详情页 → venue_full（抓取、解析、缓存）

### 页面与 token

- 详情页 URL 模板用 **scholarly 自己的** `publication_parser._CITATIONPUB`
  （`/citations?hl=en&view_op=view_citation&citation_for_view={0}`）。实测带不带 `&user=` 都返回同一页
  （都 200 / ~89 KB），所以不自己发明形状；`nav._get_soup()` 会**前置域名**
  （`'https://scholar.google.com' + url`），因此必须传**相对路径**（传绝对 URL 会拼成坏 URL）。
- token（`<author_id>:<token>`）**不必**从详情页找：作者 profile 页的标题链接
  （`a.gsc_a_at`，scholarly 的 `_citation_pub` 用正则 `citation_for_view=([\w-]*:[\w-]*)` 抽出）
  就是详情页链接。scholarly 存成 `publication['author_pub_id']`，我们的 patched
  `_citation_pub` 又把同一个 href 存成 `pub_url`。于是：
  - 新抓的 profile：`PubInfo.from_scholarly()` 用 `author_pub_id`，URL 兜底；
  - **旧 profile（没有该字段）也能用**：`PubInfo.from_dict()` 从 `url` 里解析。
  现存 `output/author_HF3-E9kAAAAJ_profile.json` 的 241 条里 241 条都能解析出 token（0 缺失）。
- 详情页是「自己 profile 的论文」，所以量级是**每篇源论文一次**（241），而不是每个施引条目一次（8,998）。

### 抓取机制（复用，不新起一套）

- 用 `scholarly._Scholarly__nav._get_soup(rel_path)` —— 与 **year probe** 完全同一入口，
  于是自动经过 `patched_get_page()`：请求前 `rand_delay(delay_scale)`（45–90 s × `--accelerate`）、
  `PageVisit` 的 captcha/重试/5–10 min cooldown/代理切换提示、页数计数与 session 刷新判定。
  本模块**不**直接碰 `page_visit.py` / `scholarly_session.py`（它们只在 patched session 里成立）。
- 调用位置在 **Phase 1**（`AuthorProfileFetcher.run()` 里、写 profile 之前）。`main()` 在 Phase 1
  之前就已 `_patch_scholarly()`，所以延迟等逻辑天然生效；放 Phase 2 的问题是
  `has_pending_work()` 为假时整段被跳过。
- `nav._get_soup` 不存在时（例如被单测替身/未 patch 的会话）只打印一行提示并返回 None，
  profile 照旧保存，`venue_full` 留空。

### 解析：为什么必须按「标签-值配对」

真实页面结构（实测抓下来的 89 KB 页面）：

```html
<div id="gsc_oci_table">
  <div class="gs_scl"><div class="gsc_oci_field">Authors</div><div class="gsc_oci_value">…</div></div>
  <div class="gs_scl"><div class="gsc_oci_field">Publication date</div><div class="gsc_oci_value">2024/6</div></div>
  <div class="gs_scl"><div class="gsc_oci_field">Conference</div><div class="gsc_oci_value">Proceedings of …</div></div>
  …
</div>
```

**坑（用户也踩过）**：页面 `<head>` 里有一大段 `<style>`，其中就有
`.gsc_oci_field{float:left;…}` 与 `.gsc_oci_value{margin-left:116px;…}`。任何
"`gsc_oci_value[^>]*>(.*?)<`" 式的朴素正则会把 CSS 文本当成字段值（还会把字段错位）。
因此解析用 BeautifulSoup DOM 配对：`div.gs_scl` → `gsc_oci_field`（标签）+ `gsc_oci_value`（值），
标签小写化，值 `get_text(' ')` 后再 `' '.join(split())` 规范化空白（换行/多空格/`\xa0` 都压成单空格）。
测试里专门放了一段写着 `.gsc_oci_value{…}` 的 `<style>` 来钉这个行为。

**标签集合**：实测同一作者多篇论文（TACL / ICLR / ACM TKDD / Nature Comput. Sci. / 中文信息学报 /
arXiv）**全部**把 venue 放在 `Journal` 行（连 ICLR 这种会议也是 `Journal => International Conference on
Learning Representations`），只有部分会议论文用 `Conference`。所以判定是**标签集合**而不是按论文类型映射：
`journal / conference / book / publication / periodical`，按页面顺序取第一个有非空值的。
本 profile 首轮 11 条命中里 `journal` 与 `conference` 都出现过。

**没有字段表 = 抓取失败，不是"没有 venue"**：captcha / consent / 跳转页没有任何 `gs_scl` 行，
`parse_detail_fields()` 返回 `{}`，调用方记为 `failed` 且**不写缓存**（下次重试）。
真正的"有表格但缺 venue 标签"才记 `no_venue_field` 并写缓存（避免每轮重复请求同一页）。

### 缓存与续跑

- `output/venue_detail_cache.json`：`{token: {status, venue_full, venue_label, fetched_at}}`，
  `status ∈ {ok, no_venue_field}`，用 `write_json_atomic()` **每抓一页写一次**（文件才几十 KB），
  所以 Ctrl+C/崩溃都不丢已抓到的进度。
- 命中缓存的 token **不发请求**；`failed` 的 token 不写缓存 → 下轮自动重试；
  缓存文件损坏只告警并按空缓存继续（不会让整轮抓取失败）。
- `KeyboardInterrupt` 不是 `Exception`，会继续向上抛（编排只 catch `Exception`），所以 Ctrl+C 立刻生效
  且已抓进度在缓存里。

### 边界与已知取舍

- **`venue` 绝不覆盖**：见 `_update_history.zh.md` 同日条目（身份键漂移）。
- 抓不到时写**空字符串**（不是 null、也不是删 key）：`PubInfo.to_dict()` 是固定 schema，
  下游 `.get('venue_full')` 语义稳定。
- **`--skip/--limit` 不限制详情页**：详情页按"每篇 publication"抓，属于 Phase 1；曾实现过窗口后主动
  回退（要改构造签名 + 既有测试替身，是用户未要求的额外行为）。缓存让"先试几篇再续跑"仍然可行
  （中途停掉即可）。
- **输出文件何时带上 `venue_full`**：`_save_output()` 用 `PubInfo.from_dict(pub).to_dict()` 重建
  `pub`，所以只要 Phase 2 跑过一次，`author_<ID>_paper_citations.json` 就会带上它；若某轮
  "无变化 + 无待抓" 直接跳过 Phase 2，输出文件保持旧内容（下次真跑才更新）。这一条已记入 `issues.md`。

### 详情页抓取的限流实测（2026-10-14）

- 实测：`--accelerate 0.3`（约 20 s/请求）连续抓 **75 个左右详情页**（约 30 分钟）后开始被限流
  （scholarly 抛 `Cannot Fetch from Google Scholar.`，无 captcha）；`PageVisit` 的 2 次快重试 +
  3 分钟冷却（缩放后）都没能恢复，冷却结束后同一篇仍失败。
- **为什么比引文列表页更容易被限流**：引文列表页走 `_SearchScholarIterator` → `patched_load_url()`，
  那里有「每 8–12 页强制长休息 3–6 分钟」；详情页走 `nav._get_soup()` → `patched_get_page()`，
  **只有 45–90 s 的请求前延迟，没有强制长休息**。year probe 也是同一个入口，所以这是该路径的既有性质，
  不是本次新引入的（本次只是把它的调用量从"每篇论文一次"放大到"每篇 publication 一次"）。
- 结论（给后续决策）：要跑完整 241 篇，用**默认延迟**（不加 `--accelerate`）并分成几轮跑；
  更稳的做法是给直接 `_get_soup` 路径也接上强制长休息（本轮**没做**，属于新行为，需要用户拍板，
  已记入 `issues.md` 第二节）。
- 续跑很便宜：`output/venue_detail_cache.json` 命中不发请求，重跑时已抓的 81 条直接复用。

## 2026-10-14: 施引条目的五种引用格式（`cid` / `cite` / `--cite-formats`）

**为什么**：引文列表页给每条施引条目一行缩写（`H Wang, F Zhang` + 截断 venue），而每条结果行的
**Cite 对话框**一次给全部五种格式：`/scholar?q=info:<cid>:scholar.google.com/&output=cite&scirp=0&hl=en`
（实测 `scholar.google.com` 与 `scholar.google.com.hk` 返回同一份约 2.7 KB 的 HTML）。其中
**Chicago 给全作者全名**、**Vancouver 给 venue 全称 + 卷期页码**、同一份响应里还有**带签名的 BibTeX 链接**。

**`cid` 从哪来（不需要额外请求）**：scholarly 解析结果行时把 `data-cid` 写进
`pub['url_scholarbib']`（`/scholar?hl=en&q=info:<cid>:scholar.google.com/&output=cite&scirp=<pos>&hl=en`）。
仓库原本只用它做 `cites_id` 的兜底（`_extract_cid_from_scholarbib`）；现在 `extract_citation_info()`
额外输出 `'cid'`（并暴露公开函数 `extract_cid_from_scholarbib`），**纯解析、零请求**，所以旧数据
「没有 cid」也能被识别（字段缺失 → `None`，与 `cites_id` 的缺省值同款）。

**页面结构（实测）**：

```html
<div id="gs_citt"><table>
  <tr><th scope="row" class="gs_cith">MLA</th><td><div tabindex="0" class="gs_citr">…</div></td></tr>
  <!-- APA / Chicago / Harvard / Vancouver 同款 -->
</table></div>
<div id="gs_citi">
  <a class="gs_citi" href="https://scholar.googleusercontent.com/scholar.bib?q=info:<cid>:…&scisdr=…&scisig=…">BibTeX</a>
  <a class="gs_citi" href="…scholar.enw…">EndNote</a> …  <!-- 还有 RefMan / RefWorks -->
</div>
```

- 解析按 DOM 配对（`th.gs_cith` → 它的兄弟 `td` → `div.gs_citr`），**不用正则**：页面可以带
  `<style>` 段提到 `.gs_citr{…}`，朴素正则会把它读成引用文本（与 `venue_detail.py` 的 `<style>` 坑同类）。
- 值用 `get_text()`（**无分隔符**）再规范化空白。**这个选择是实测出来的**：`get_text(' ')` 会把
  `<i>Cybersecurity</i>, <i>8</i>(1)` 拼成 `Cybersecurity , 8 (1)`（标点前多出空格），
  无分隔符拼接才等于页面上的 `Cybersecurity, 8(1)`。
- 实体交给 bs4 解码（`&quot;` → `"`、`&amp;` → `&`）；**只保留五个已知标签**，缺哪个就没有哪个 key
  （页面没有 `gs_cith` 行 = captcha/consent/跳转页 = 抓取失败，不写缓存）。

**BibTeX**：从同一份 Cite 响应里取带签名的 `scholar.bib` 链接，用 `nav._get_page(url)`（绝对 URL；
scholarly 自己的 `_fill_publication` 也是 `_get_bibtex()` + `_get_page()` 这么走的）拿纯文本，解析成
`{entry_type, key, <field>: value}`（支持嵌套花括号、`\{`、双引号值、裸值、跨行值）。
实测 `@article{zhang2025llms, …}` →
`{'entry_type': 'article', 'key': 'zhang2025llms', 'author': 'Zhang, Jie and … and Meng, Dan',
'journal': 'Cybersecurity', 'volume': '8', 'number': '1', 'pages': '55', 'year': '2025', 'publisher': 'Springer'}`。
**为什么值一次请求**：`bibtex.author` 是 ` and ` 分隔的全名列表，机器可切分，比从 Chicago 文本里
抽作者可靠；`journal`/`booktitle`/`volume`/`pages` 是结构化的 venue 证据。

**只增不改**：新增 `citation['cite'] = {mla, apa, chicago, harvard, vancouver, bibtex}`（缺的 key 不写），
`title`/`authors`/`venue`/`year`/`url`/`cites_id` 一字不动 —— `venue` 与 `cites_id` 参与身份键
（同一课在 `venue_detail.py` 与 K123/K125 已记过）。`cid` 同样**不进** `citation_identity_keys()` /
`citation_output_identity_keys()`，所以去重语义、论文内编号与下游身份校验都不受影响。

**接线与开关**：`--cite-formats`（默认**关**）→ `PaperCitationFetcher(cite_formats=…)` →
`_save_output()` 在写 JSON **之前**对全部 `final_results` 的 citations 跑一次
`cite_format.enrich_citations_with_cite_formats()`。放在这里而不是抓取循环里的三个原因：
①去重已完成，抓的就是最终要写出的那批；②中断（Ctrl+C）也会走到 `_save_output`，已抓到的格式照样落盘；
③缓存只 load/save 一轮（若放进 `_build_entry`，241 篇论文会反复读写同一个文件）。
**默认关闭**是因为成本：每个施引条目 1 次请求（BibTeX 再 1 次），全库是数千次量级，
与「每篇源论文一次」的 venue 详情页（241 量级）不是一个成本级 —— 见 `issues.md` 第六节。

**缓存与续跑**：`output/cite_format_cache.json`，key = `cid`：

```json
{"nYGTsUfPxnMJ": {"formats": {"mla": "…", "chicago": "…", "vancouver": "…"},
                  "bib_url": "https://scholar.googleusercontent.com/scholar.bib?…&scisig=…",
                  "bibtex": {"author": "…", "journal": "Cybersecurity", "volume": "8"},
                  "fetched_at": "2026-10-14T…"}}
```

- 每抓到一条就 `write_json_atomic()` 写一次，Ctrl+C / 崩溃都不丢已抓进度；命中缓存的 cid **不发请求**。
- **失败不写缓存**：请求异常、captcha/consent 页（没有五个标签）都记 `failed` 且**不缓存** → 下轮重试。
- 一个刻意保留的例外：**Cite 页成功但 BibTeX 下载失败**时，缓存保留该页与它的签名 URL（无 `bibtex`），
  下轮只用那一个请求重试 BibTeX，而不是重抓整页 —— 页本身成功了，不该丢；签名若过期只退化成
  「该条没有 bibtex」，不影响五种格式。
- 缓存损坏只告警并按空缓存继续（`venue_detail.py` 同款）。

**限流性质（与 venue 详情页同源）**：Cite 对话框走 `nav._get_soup()` → `patched_get_page()`，
只有 45–90 s 的请求前延迟，**没有**引用列表页那种每 8–12 页的 3–6 分钟强制长休息
（`patched_load_url()`）。`issues.md` 第二节第 5 条已就这条路径提过产品问题，本次沿用既有行为、
不新增机制。

**测试**：`tests/test_cite_format.py`（71 例）钉住 `cid` 持久化（含 `info:None:` 占位、无
`url_scholarbib` 的旧行、`cid` 不泄漏进 `cites_id`）、五个标签的 DOM 配对（`<style>` 干扰、顺序、
重复标签、未知标签、缺 `<td>`、空值）、实体解码与 `<i>` 拼接间距、BibTeX 解析（嵌套/转义花括号、
引号值、裸值、多行、截断、非 bibtex 文本）、优雅降级（请求异常、captcha 页、BibTeX 失败后只重试 bib、
KeyboardInterrupt 保留已抓内容）、缓存/续跑/原子写，以及 `--cite-formats` 接线
（关闭时零请求、开启时 `_save_output` 写出 `cite`、没有 navigator 也能写出输出）。
全套 310 → **383** 例。

## 2026-10-14: 新抓取路径的 1/10 延迟 + 「已经完整就不抓」（成本控制第二轮）

### A. detail 路径的请求前延迟缩到 1/10

- **唯一常量**：`code/config.py` 的 `DETAIL_PAGE_DELAY_FACTOR = 0.1`（连同"这是唯一一处定义"的
  说明与诚实代价一起写在常量注释里）。
- **唯一判据**：`code/scholarly_session.py` 的 `detail_path_delay_factor(url)` ——
  `view_op=view_citation`（出版详情页）/ `output=cite`（Cite 对话框）/ `scholar.bib` 或
  `output=citation`（带签名 BibTeX 下载）→ `0.1`，其余（引用列表页、年过滤页、profile 页）→ `1.0`。
- **唯一计算点**：`patched_get_page()` 里 `rand_delay(ctx.delay_scale * detail_path_delay_factor(request_url))`。
  **有效延迟 45–90 s → 4.5–9 s**；`--accelerate` 仍是**乘积**（`0.5 × 0.1 = 0.05`），语义没混。
- **为什么按 URL 分类而不是让调用方传参**：venue 详情页的调用方是 `AuthorProfileFetcher`（它**没有**
  SessionContext），cite 页在 `PaperCitationFetcher._save_output()` 里 —— 把"属于哪条路径"绑在
  「即将请求的那个 URL」上，就不必把 ctx / getter 一路往上传，而且天然覆盖了 BibTeX 这第二次请求。
  两条新路径因此**共用同一处定义**；`tests/test_scholar_patch.py` 有源码级守卫断言
  `venue_detail.py` / `cite_format.py` 里不存在 `time.sleep` / `rand_delay` / `DELAY_MIN`。
- **既有行为没动**：引用列表页分页与 profile 页仍是 45–90 s；`patched_load_url()` 的
  「每 8–12 页强制长休息 3–6 分钟」一字未改；首请求/注入 cookies 后首个请求仍不等待。
- **测试**（`tests/test_scholar_patch.py` 新增 8 例）：用**打桩的** `rand_delay`（记录 scale、返回定值
  7.0）+ 打桩的 `time.sleep`（记录"实际睡了多少"）断言四类 URL 的 scale 与 sleep 值，不真的睡；
  `--accelerate` 叠加（0.5 × 0.1）；纯判据边界（`''`/`None`/列表页/年过滤页/profile → 1.0）；
  两个新模块不得自带延迟。

### B. 「已经完整就不抓」

- **唯一判据**：`code/venue_quality.py`（纯函数、无环境变量、可单测）：
  `is_missing_venue()`（空/`NA`/`n/a`/`None`/`-`/`unknown`…）、`is_truncated_venue()`（`…` **和**
  `...`）、`is_preprint_venue()`（`arxiv`、`preprint`、`biorxiv`、`medrxiv`、`ssrn`、`techrxiv`、
  `research square`、`researchsquare`、`preprints.org`，大小写不敏感子串；空值也算"不是真 venue"）、
  `is_complete_venue()` = 非缺失 **且** 未截断 **且** 非预印本源。
  `has_full_author_names()`：列表页形态是 `<首字母> <姓>`，所以每位作者"最后一个 token 之前"都是
  given name，**任一位**出现长度 ≥ 3 的 token 即视为全名；`van/von/de/der/den/del/di/da/dos/la/le/
  ter/ten/bin/ibn/al/el/du/st` 这些介词要排除（否则 `J van der Berg` 会因 `van` 三字母被误判成全名）。
  两个调用点的薄包装：`venue_needs_detail(venue)` / `citation_needs_cite_dialog(citation)`。
- **pub 级（`venue_detail.py`）**：`is_complete_venue(pub['venue'])` → `stats['skipped_complete'] += 1`
  并 `continue`：**不发请求、不写缓存条目**；`venue` 逐字节不变，`venue_full` 保持空。
- **条目级（`cite_format.py`）**：**先查缓存，再判跳过** —— 缓存命中照样写进 `citation['cite']`
  （否则重跑会把上一轮付过钱的 `cite` 从输出里抹掉，等于数据倒退）；没有缓存且
  `citation_needs_cite_dialog()` 为假（venue 已完整**并且** authors 已是全名）→ 跳过，零请求、
  不写缓存。
- **两个 stats 都加 `skipped_complete`**，并打进各自的汇总行：
  `venue_full resolved: … already complete N` / `citation formats resolved: … already complete N`。
- **取舍（诚实写进 README/AGENTS/issues）**：跳过的条目**放弃** Cite 对话框本可补上的卷期/页码/年份；
  `venue_full` 实测**不是** `venue` 的超集（上一轮已证），所以"用详情页给完整 venue 补卷期"这条路
  本来也不成立。用户已知悉。
- **测试**（新增 `tests/test_venue_quality.py` 27 例）：判据矩阵（arXiv 三种写法 / preprint / bioRxiv /
  medRxiv / SSRN / TechRxiv / Research Square / 空值与 `N/A` / 含 `…` 与 `...` / 真 venue）；
  全名判据（首字母串、姓名介词、单 token 作者、空值）；四种组合；**跳过时零请求**（打桩的
  `get_soup`/`get_page` 一旦被调用就抛 `AssertionError`）；**跳过时不生成缓存文件**；混合列表只请求
  需要的那条、只给那条写缓存；缓存复用的例外；既有字段逐字节不变。

### 本轮限流实测与冷却结论（写进 issues.md 第七节）

venue 跑批在 15:05（第 88 页）触发整站限流；crawler 自带的 **3 分钟冷却 ×2 ✗**；本实验 15:20 第一条
请求即失败；15:22 search 端点返回 **captcha 表单**；15:35 profile **429** / cite 对话框 **403**。
**3 分钟 ✗、约 15 分钟 ✗**，更长的冷却（30/45/75 分钟）由后台 watcher 在测。所以 1/10 延迟的可用性
完全依赖「缓存 + 续跑 + 被拦即停」；若将来证明拦截是**按时间窗**而非按量，这个结论要重估。

全套测试 383 → **418** 例（`test_scholar_patch.py` +8、新增 `test_venue_quality.py` 27 例）。

## 2026-10-14: `--cite-formats` 的 `has_pending_work()` 短路坑

**坑**：`main()` 在「引用总数 + 论文数都没变」时若 `has_pending_work()` 为假就**整段跳过 Phase 2**，
而 `--cite-formats` 的富化跑在 Phase 2 的 `_save_output()` 里 —— 于是计数一旦稳定，开了这个开关也
**永远不会再执行**（缓存没覆盖的那些条目永远拿不到 `cite`）。

**修复**：新增 `PaperCitationFetcher.has_cite_format_work()`（只在 `cite_formats` 打开时有效）：
输出 JSON 缺失/不可读 → 视为有待办；否则逐条看 `cid` 存在、不在
`output/cite_format_cache.json` 里、且 `code.venue_quality.citation_needs_cite_dialog()` 为真
（venue 不完整或作者未全名）→ 有待办。`main()` 的短路改成两级：`has_pending_work()` 为假时
再看 `has_cite_format_work()`，仍有待办就打印
`No changes in totals, but --cite-formats still has citations outside its cache. Continuing...`
并照常进 Phase 2（各篇按 `complete` 走缓存、不重抓引文，只有格式那一步干活）。
`tests/test_year_fetch_main.py` 的 `FakePaperFetcher` 补了同名 stub（沿用 `has_pending_work` 的既有约定）。

## venue_key：规范发表地名 + 年份（2026-10-15）

**为什么**：下游判断"同一篇作品"时，标题/年份/作者/URL 之外还差一个维度 —— 发表地。
而 Scholar 把同一个发表地写成十几种形态（缩写 `ACL 2024`、全称 `Proceedings of the 62nd Annual
Meeting of the Association for Computational Linguistics`、期刊形态、以及被截断的 `…`）。
`venue_key` 就是把这些折叠成 **规范名 + 年份** 的唯一键。

**词表从哪来**：不新造标准 —— 3.a.2 已有 `VENUE_PRESTIGE`（51 会议 + 30 期刊）+ `VENUE_ALIASES`
+ `_venue_match()` 的匹配语义（多词子串；≤5 字符的全大写缩写要词边界，所以 `CL` 不会命中 `ACL`；
其它单名要整名，所以 `Science` 不命中 `Science China`），按文本长度降序尝试，所以
`Transactions of the Association for Computational Linguistics` 先命中期刊 `TACL` 而不会被会议
`ACL` 吞掉，`Findings of the Association for Computational Linguistics: ACL` 先命中
`Findings of ACL`（**独立于**主会 `ACL`）。两处**刻意合并**：`NIPS` → `NeurIPS`、`ACMMM` → `ACM MM`
（3.a.2 里它们是两个键、两个 tier 相同的会议；做去重键时同一会议不能有两个 key）。

**输入选哪个 venue 串**：pub 用 `venue_full or venue`（详情页的未截断名能救回列表页被截断的那批：
只用 `venue` 时 241 篇里仅 51 篇可识别，用 `venue_full` 后 120 篇）；施引条目没有详情页，
只能用列表页的 `venue`（因此 raw 居多）。

**年份**：取 venue 串里第一个 1990–2035 的 4 位年份（会议届次，如
`Findings of the …: ACL 2024, 8411-8423, 2024` → 2024；`Ijcai 17 (2017), 3203-3209, 2017` → 2017），
串里没有才退回记录自身的 `year`；两者都没有 → 只有规范名、没有年份。

**认不出的怎么办**：`venue_key` = venue 自身串 + `venue_key_source='raw'`。这是**可审阅的临时状态**：
`code/data_upgrade/normalize_venue_key.py --review-file …` 会导出所有不同取值连计数。占位值
（空/`NA`/`N/A`/`None`）**不给 key**（`NA` 当 key 会把上千条无关记录并成一条）。审阅后往
`VENUE_CANONICAL` 里补一行即可，重跑脚本覆盖。

**派生字段，不落状态**：`PubInfo.venue_key/venue_key_source` 是 property，`to_dict()` 时按
`venue_full or venue` 重算；`from_dict()` 不读回存过的值。好处是**不可能写出陈旧 key**（详情页
晚到、手工改过 venue 都会自动跟上），代价是每次序列化多一次匹配（纯字符串，可忽略）。

**存量修复工具** `code/data_upgrade/normalize_venue_key.py`：默认 dry-run（连备份目录都不建），
`--apply` 才写；只用 `write_json_atomic` 改这两个字段，其余键逐字节不变；备份目录
`output/venue_key_backup_<stamp>/` + `manifest.json`（原路径 + 改动前 sha256 + 计数）；
幂等（第二次跑 0 改动）。**踩坑**：备份目录的默认值最初写成相对 cwd 的 `output/…`，测试里
（`--output-dir` 指向临时目录）于是把备份写进了**本仓库真实的 `output/`** —— 已改为
`os.path.join(output_dir, …)`；另有两处小坑：未识别的计数器用了普通 dict 直接 `+= 1`（KeyError），
以及"无 venue 记录"因为 `.get()` 默认值相等而从未被写入显式 `null`（改成先判 `'venue_key' in record`）。

**引文侧的现实**：实测 9,269 条施引条目里 7,690 条是 raw —— Scholar 在引用列表页把 venue 截到约
25 字符（`Proceedings of the …` x556、`Findings of the …` x207、`IEEE Transactions on …` x138 …），
其中 2,912 个不同取值。**这些串彼此不可区分**（`Proceedings of the …` 可能来自任何会议），
所以直接把 raw 当去重键会误并 —— 已记进 `issues.md` 待你决定（截断的给 null / 补前缀规则 /
只信 canonical）。可辨识的那 354 条 canonical（如 `Neurocomputing`、`ACL 2024` 这类短名）不受影响。

## venue_key 二轮：会议"同理类推"，期刊 key 不带年份（2026-10-15）

**用户口径**：会议继续"名 + 年份"并"同理类推"（EACL、ICASSP 等）；**期刊 = 期刊缩写名**
（TACL、TASLP、Nature Machine Intelligence）。据此把 `resolve_venue_key()` 分成两支：
会议走 `f"{name}{year}"`，期刊直接返回 `name`（`VENUE_CANONICAL[name][1] == 'journal'`）。
理由：期刊是**连续出版物**，没有"2024 届"这种届次语义；年份信息仍在记录自身的 `year` 字段里，
不会丢。**若你其实想要 `TASLP2020` 形态，改这一支即可**（一行）。

**词表从 81 → 168 条（84 会议 + 84 期刊）**，全部来自"真实数据里仍未识别 + 同领域直接相邻"的取舍：
- 会议：ICASSP、INTERSPEECH、SLT、ASRU、Odyssey、IJCNN、ICANN、PAKDD、ACML、LREC、CCMT、CWMT、
  WMT、AMTA、EAMT、IWSLT、NLPCC、CCL、IJCNLP、PACLIC、IALP、CIPS-SIGHAN、NLP-NABD、LT4HALA、ALP、
  ALT、PMLR、RANLP、SIGDIAL、INLG、ICMI、ICME、ICPR、BMVC、WACV。
- 期刊：TASLP、TALLIP、TKDD、TNNLS、TCYB、TIST、TMM、CSUR、JAIR、JCST、FCS、SCIS、LRE +
  一批"没有通用缩写、直接用刊名"的（Neurocomputing、IEEE Access、KBS、ESWA、Information Sciences…）
  + 中文期刊（中文信息学报、小型微型计算机系统、软件学报、电子学报、计算机研究与发展、
  计算机科学、模式识别与人工智能）+ 日文期刊（自然言語処理）。

**踩坑与取舍**（都在测试里钉住）：
1. `CCMT`/`CWMT`/`WMT` 共用 "Machine Translation" 词组 —— Springer 的会议卷写法是
   `Machine Translation: 19th China Conference, CCMT 2023, …`，光靠 "China Conference on Machine
   Translation" 匹配不到（卷名里没有 "on Machine Translation"），所以补了别名
   `China Conference, CCMT` / `China Workshop, CWMT`（比另一个候选别名 `Machine Translation`
   更长 → 先命中），否则会被误判成《Machine Translation》期刊。
2. 与之相反，**没有**给 'Machine Translation' 建期刊 key：引文侧那 8 条 `Machine Translation`
   无法区分是期刊还是被截断的会议卷名 → 留 raw 让人看。
3. `Science China Information Sciences` 以前判不出（`Science` 的"整名"规则不命中），现在归到 `SCIS`
   （`SCIENTIA SINICA Informationis` 作别名 —— 同一刊中英两版）。
4. 单名期刊的锚定规则（`^name` + 结束/逗号）自动挡住了误配：`Information` 不会命中
   `Information Sciences`、`Patterns` 不会命中 `Pattern Recognition`。
5. `软件学报` ← `Journal of Software` 这条别名有**误并风险**（"Journal of Software: Evolution and
   Process" 之类会命中），但真实数据里就是 7 条 `Journal of Software`；已在 `issues.md` 记明，
   要收紧就删这一条别名。
6. 中文刊名之间的前缀关系没问题（`计算机科学` 用锚定规则，不会命中 `计算机研究与发展`）。

**存量结果**（`--apply`，备份 `output/venue_key_backup_20261014_194909/`，幂等复跑 0 改动）：
pub canonical 120 → **190**、raw 117 → **47**、无 venue 4；引文文件整体 canonical 354 → **1255**
（纯引文 234 → 1065）；引文侧 raw 仍以**截断串**为主（`Proceedings of the …` 556、`Findings of the …`
207、`IEEE Transactions on …` 138 …）—— 这些串里已经不含会议名，词表再扩也救不回来。

## venue_key 三轮：arXiv 与 Findings（2026-10-15）

**arXiv**：用户明确"arxiv 的 venue 就写做 arXiv"。实现是 `PREPRINT_SERVER_KEYS = (('arxiv','arXiv'),)`
+ `preprint_venue_key()`（大小写不敏感子串），`resolve_venue_key()` 里的顺序是
**规范词表 → 预印本服务器 → raw**。放在词表之后是刻意的：`ACL 2024 (arXiv:2402.18567)` 这类串
仍应判会议。好处是引文侧那 779 条被截断的 `arXiv preprint arXiv …`（id 已丢失、彼此不可区分）
终于有了一个真实语义的 key —— 预印本的"发表地"本来就是服务器，而不是某个年度届次，所以不带年份。
**只做了 arXiv**：bioRxiv / medRxiv / SSRN / TechRxiv / Research Square / preprints.org 同为预印本
服务器，但用户只点名了 arXiv，按"只做明确要求的事"留给 issues.md 待确认（实测引文侧还有
~1,488 条带这些标记，其中与被 arXiv 命中的 1,426 条有重叠）。

**Findings**：用户指出"不存在这个会议"，`Findings of the Association for Computational Linguistics`
这一条（3.a.2 的兜底键，原本映射到裸词组）在阶段 1 是**多余的甚至有害的** —— 它会把"没注明主会"
的卷按一个不存在的 venue 记账。删掉该键，把裸词组降级为 `Findings of ACL` 的**别名**：
宿主明确的写法（`… Linguistics: EMNLP`）文本更长 → 最长优先仍命中自己的主会；
裸写法（无冒号 + 主会）→ `Findings of ACL<年份>`。实测当前数据里没有任何一条曾落到裸 `Findings`
（`Findings2*` 命中 0），所以这次删除对存量 key 零改动，只是纠正未来行为。

## venue_key 四轮：其余预印本服务器 + 宿主名误命中（2026-10-15）

**服务器名单**：`PREPRINT_SERVER_KEYS` 从 1 条扩到 8 条（arXiv / bioRxiv / medRxiv / SSRN /
TechRxiv / Research Square（含 `researchsquare.com`）/ preprints.org）。语义与 arXiv 一致：预印本的
"发表地"就是服务器，所以 key = 服务器名、不带年份；`resolve_venue_key()` 里仍在**规范词表之后**判断，
所以"已知会议 + 预印本链接"这类串依旧判会议。**故意不为裸 `Preprint` 建 key** —— 它没点出任何服务器，
给了 key 反而会把一堆无关预印本并成一条。

**宿主名误命中（我自己测试时撞出来的）**：`venue_match()` 的短全大写缩写分支是
`(?:^|\b)KW(?:\b|$)`，而 `.` 是词边界 —— 于是
`https://www.researchsquare.com/article/rs-123/v1` 里的 `www` 命中了 `WWW` 会议（得到 `WWW2023`），
`kdd.org` → `KDD`、`iclr.cc` → `ICLR` 同理。修法是把两侧的边界换成
`(?<![\w.])KW(?![\w.])`：**域名标签不是 venue**。回归确认 `WWW 2024`、`KDD 2020`、`ACL 2024`、
`Ijcai 17 (2017)`、`NIPS 2019` 都照旧命中，456 例旧测试全绿。真实数据零影响（含 `www.` 的 venue 0 条，
12 个 `WWW*` 键都对应真的 Web Conference 串），属**未被触发的错误** —— 值得记一笔：把它放到
`researchsquare.com` 那种 URL 上就立刻错。

**存量**：引文侧 canonical 2,491 → 2,550（新增 59：bioRxiv 37 / SSRN 21 / medRxiv 1），raw 5,316 → 5,257；
pub 侧 229 / 8 / 4 不变。

## venue_key 五轮：取值改名 canonical → normalized（2026-10-15）

**动机**：用户看字段值时问"canonical 是什么意思"，随后要求改叫 `normalized`。取值属于**数据契约**，
所以三件事一起做：① 常量与取值改名（`SOURCE_CANONICAL` → `SOURCE_NORMALIZED`，值 `'canonical'` →
`'normalized'`）；② 工具与测试、文档全部同步；③ **存量数据用同一个工具迁移**（这是本工具第二次
证明"派生字段"的价值 —— 改规则或改口径只要重跑一次，不用重新抓取）。

**没跟着改的名字**：`VENUE_CANONICAL`（词表）与 `canonical_venue()`（函数）。理由：它们说的是
"规范**名**"这件事，而 `canonical` 在算法/CS 里的正典含义就是"等价类的唯一代表"；同时 3.a.2 里
有同名的 `canonical_venue()` / `VENUE_ALIASES`，两处术语对齐比字面统一更重要。取值则用
`normalized`（"已归一化"），因为它是给下游看的**结果标记**，语义是"这个 key 是归一化产物、
不是原串"。已在 `SOURCE_NORMALIZED` 上方写清这条区分。

**迁移实测**：229 条 pub + 2,779 条引文文件记录被改写（= 229 pub + 2,550 引文），备份
`output/venue_key_backup_20261014_201404/`，复跑 0 改动；字段分布
`normalized 229 / raw 8 / null 4`（pub）与 `normalized 2,550 / raw 5,257 / null 1,221`（引文）。
**踩坑**：改 `stats['canonical']` 时用了 `replace(..., 1)`，而工具里该模式出现两次 → 断言失败、
脚本中断（好在断言在写盘前，没有留下半成品）。这类"同名多处"要么不带 count、要么先 grep 计数。

## venue/citation 六轮：Cite 对话框步骤默认开启（2026-10-15）

**为什么改默认**：用户要"保证 scholar 新抓下来的数据中获取了该信息"。事实是：引文列表页给的信息**先天不全**
——venue 被截到中位 25 字符（全库 61.8% 带 `…`）、authors 是缩写（只有 6.2% 含任一全名）；
而 Cite 对话框一次请求就能给 **Chicago 全作者全名**、**Vancouver venue 全称 + 卷期页**，
BibTeX 再给一份全作者全名 + venue + volume/number/pages。**只增不改**、有缓存、可续跑、
失败不写缓存、条目"venue 已完整且作者已全名"时零请求跳过 —— 代价与风险都可控，所以把它从
可选改成默认，是让"新数据自带完整信息"的唯一直接办法。

**设计要点**：
1. **三态解析**：CLI 的 `--cite-formats` / `--no-cite-formats` 都 `default=None`（"未指定"），
   由 `resolve_cite_formats(cli, config_path)` 按 **CLI > 配置文件 `cite_formats` > `CITE_FORMATS=True`**
   解析并返回来源（`cli` / `config` / `default`），`main()` 记 debug 日志。这样"默认开"不是硬编码在
   调用点，而是可配置、可审计的一条链路 —— 与本仓库"零环境变量、参数 > 文件 > 默认"的既定约定一致。
2. **fetcher 默认也改成 True**，避免"main 说开、直接构造 fetcher 却是关"的分叉；要关就显式传
   `cite_formats=False`（测试即这么用）。
3. **短路不再吞掉这一步**：`has_cite_format_work()`（2026-10-14 加的）判定"有 `cid` 且不在缓存且仍需对话框"
   → 即使引文总数没变也继续跑 Phase 2；默认开启后它同时成了**存量回填**的入口。
4. **`cid` 是硬门槛**：对话框按 `cid` 寻址（`info:<cid>:scholar.google.com`），而 `cid` 只在
   2026-10-14 之后写入的条目上（现 39/9,028）。所以"保证"对**新抓的数据**成立；存量要么重抓
   （`--fetch-mode force`）、要么等它自然重抓。这一点已在 AGENTS/README 写明，避免误解成"全库立刻都有"。
5. **成本**：每条约 1 次（+ BibTeX 1 次）请求，详情类路径按 1/10 延迟（`DETAIL_PAGE_DELAY_FACTOR`），
   被拦即停/失败跳过，靠 `output/cite_format_cache.json` 续跑。要省请求：`--no-cite-formats`。

## venue_key 七轮：引文侧改用 Cite 对话框的 venue（2026-10-15）

**动机**：用户要"新抓的数据 venue 也是完整的"。引文列表页的 `venue` 被 Scholar 截到中位 25 字符
（全库 61.8% 带 `…`），而 Cite 对话框的 **BibTeX** 里有未截断的 `journal`（article）/
`booktitle`（inproceedings）/ `series` —— 这才是算 `venue_key` 该用的串。

**为什么只认 BibTeX、不解析那五种格式化串**：`chicago`/`vancouver`/`mla`/`apa`/`harvard` 里 venue 夹在
作者、**标题**、卷期/日期之间（如 `… Meng D. Cybersecurity. 2025 Feb 5;8(1):55.`），没有可靠分隔符；
一旦把标题当 venue，就会算出一个**错误的** `venue_key`，而 key 是要参与去重的 —— 错比 raw 更糟。
BibTeX 是结构化字段，零歧义，所以只用它；字段优先级 `journal` → `booktitle` → `series`
（article 用 journal、inproceedings 用 booktitle、`series` 只作兜底，如 LNCS）。

**实现与一致性**：`citation_venue_key()` 是**唯一判据**，被抓取侧（`cite_format` 写完 `cite` 后刷新）
与数据修复工具（`normalize_venue_key` 的引文分支）共用 → "先抓的行、后补的对话框"在下次跑脚本时自动升级，
不需要两条实现。`resolve_venue_key()` 保持原样（pub 侧与"没有 cite 的引文"行为一字未变）。

**边界**：① 对话框里没有 venue 字段（BibTeX 缺失或字段为空）→ 保持原来的 key（列表页那个），不猜；
② 年份仍按"venue 串里的第一个 4 位年份优先" —— 所以 `Proceedings of the 2024 Conference …` +
记录 year=2025 会得到 `EMNLP2024`（会议届次），这是既定口径，测试里已钉住（我第一版测试写错成 2025）。

## K78 复现与修复细节（2026-10-15）

**为什么"少个字段"会变成"少抓数据"**：`PaperFetchState._infer_strategy_and_summary()`
（`code/output_state.py:245`）的推断顺序是 `fetch_strategy` → 有 `year_fetch_diagnostics`/`year_records`
就当年模式 → 否则看 direct 诊断。而**每篇论文**（含 direct 模式）都会写一份 `year_fetch_diagnostics`
（`build_year_records()` 从缓存引用反推，direct 模式下每个年份的 `histogram_count` 都是 0，
`code/citation_fetch.py:316`）。所以：**没有 `fetch_strategy` 的快照一律被读成年模式**，
再配 `histogram_total = 0` → `is_data_complete('year')` 恒真 → `need_fetch()` False。

因此 K78 的修法必须同时解决两件事，缺一不可：
1. direct 模式下**每次落盘都带 `direct_fetch_diagnostics`**（本次：在 `save_progress()` 里就地构建，
   `termination_reason='interrupted'`；抓完后 post-loop 构建覆盖成真实 reason）；
2. `fetch_strategy` 那一行（`:438-442`）**依赖诊断是否存在**才会写 `'direct'` —— 诊断补齐后它才成立。
   （只补 1 不补 2 也够，因为二者同源；只补 2 不补 1 则 `is_complete` 会走 direct 分支，
   但快照仍缺诊断证据，报告/`stall` 签名会少信息。）

**验证方式**：用现成的 `tests/test_direct_fetch.py` harness 复现（中断迭代器 → 查 `_mid_paper_state`
→ `PaperFetchState.from_dict` 再问 `is_complete`/`need_fetch`）。这条"装载后行为"才是有意义的断言 ——
只断言快照里有某个字段是抓不住这类"写入端正常、读取端误判"的 bug 的。**真实数据未被咬到**：
169 篇 direct 论文全部带诊断，坏形状 0 例（若曾有，症状是"某篇引用数远小于 Cited by 却再不被重抓"）。

**成本**：本次改动只在 direct 模式多构建一个 5 字段小字典，无额外请求、无新配置键。

## papers 物理位置前移到阶段 1（2026-10-15，用户搬迁）

**为什么是"创建"这一步先落**：物理位置在本仓库的 output 下，按"谁拥有谁负责建"，
`main()` 每次幂等创建即可；**论文子目录与编号仍由阶段 2 的 `preflight_repairs` 做** ——
那部分（含 `renumber_papers` 的搬迁）按 `KNOWN_ISSUES.md` Y-6 的分步计划走，不混在这次改动里。

**踩坑（值得记）**：`find "$DIR"` 当 `$DIR` 是**符号链接**时**不跟随起始链接** → 计数恒为 0。
`preflight.sh` 的"数据目录存在：0 个文件，0 篇论文"就是这么来的（改成 `find -L` 后恢复 241 篇）。
同理记住：`os.listdir` / Python 的 `glob` **会**跟随；只有 `find`/`ls -d` 这类要显式 `-L`。

**给后续步骤的约束**：消费方一律经各自的 `papers` 符号链接，**绝不要在四个仓库里拼
`author_<ID>_papers` 这个物理路径**（那会把作者写死进代码）；换作者只改
`paper_downloader/papers` 一条链接。共享件搬迁时（`renumber_papers` 等）同理：路径走
调用时解析，不写死。
