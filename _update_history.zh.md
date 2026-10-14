## 2026-10-14: 一次性数据修复工具移到 `code/data_upgrade/`

### 问题

两个数据修复脚本 `fix_diag_from_year_record.py` / `fix_output_fetch_state.py` 与主线模块平铺在
`code/` 下，看不出它们**不是抓取流程的一部分** —— 容易被当成入口或支撑模块。

### 改动

- `git mv code/fix_diag_from_year_record.py code/data_upgrade/fix_diag_from_year_record.py`、
  `git mv code/fix_output_fetch_state.py code/data_upgrade/fix_output_fetch_state.py`（保留历史），
  并新增 `code/data_upgrade/__init__.py`。
- 调用改成 `python -m code.data_upgrade.fix_diag_from_year_record <output_json>` /
  `python -m code.data_upgrade.fix_output_fetch_state [output_dir]`。
- 两个模块的 docstring 与「plain script path」注释同步改路径；`sys.path` 引导从两层 `dirname`
  改为**三层**（脚本深了一层，否则直跑脚本路径时 `from code.output_state import ...` 解析不到包）。
- **搬前核实无人 import**：`grep -rn "fix_diag_from_year_record\|fix_output_fetch_state" code/ tests/ *.md`
  只命中文档清单与模块自身，`run.sh` / 容器根 `preflight.sh` / `run_all_tests.sh` 均无引用。
- 文档：`AGENTS.md`（Development commands 的 Repair tools、目录治理树）、`README.md`（Project Structure）、
  `_work_notes.zh.md`（项目结构树）改新路径，并写明「这些是数据修复/升级工具，与主线入口分开」。

### 验证

- 两种调用形式都通：`python -m code.data_upgrade.fix_*`（无参数打印 usage / 目录不存在报错）与直跑
  `python code/data_upgrade/fix_*.py`（`from code.output_state import ...` 正常解析）。
- 全套 **418 例 OK**（与搬前基线一致；`tests/test_no_env_config.py` 只遍历 `code/` 顶层 `.py`，
  不递归，故模块数变化不影响它）。

## 2026-10-13: 输出期去重不再按归一化标题合并（K123）

### 问题

`_dedup_citations_for_output()` 的身份键里带了**无条件归一化标题**（`title\t<归一化标题>`），
于是"同一标题、不同 `cites_id`"的两条（同一作品的 arXiv 预印本与正式版、或同一 thesis 的
两个落地页）会在写盘前被合并成一条 —— 与跨项目口径 K123「Google 认为不同就是不同、任何环节
都不按标题去重」直接冲突，也让下游少了本该独立的引用条目。

### 改动

- 新增 `code/citation_identity.py: citation_output_identity_keys()`：有真实 `cites_id` 的行
  **只**发 `cites_id` 键；无真实 id 的行沿用 `citation_identity_keys()` 的
  `title+venue` / `title+authors` 全等回退键。有 id 的行与无 id 的行**永不互相匹配**。
- `code/scholar_citation.py: PaperCitationFetcher._citation_output_identity_keys()` 改为委托该
  函数，并同步两处 docstring（`_dedup_citations_for_output` 也写明"共享标题不算身份"）。
- 新增 `tests/test_output_dedup.py`（6 例，含真实快照只读回归）；全套 257 → **263 例 OK**。

### 实测（`paper_downloader/main/paper_citations.json`，只读）

| 规则 | 输入 → 输出 | 折叠 |
|------|------------|------|
| 旧（+无条件标题键） | 9200 → 8971 | 229 |
| 新（仅真实 `cites_id` / 无 id 全等） | 9200 → **9172** | **28** |

折叠的 28 条**全部**是真实 `cites_id` 相同的两条（同一 cluster 在多次抓取间以不同元数据重复落盘），
无一条是因标题相同被折叠；无 id 的 2191 条一条未丢。（原预期"9200 → 9200"，实测 28 条同 id
重复，故为 9172；回归测试按语义断言"只有同 id 才允许折叠、无 id 一条不丢"，不写死数字。）

---

## 2026-10-11: cookie 获取加固 + 可选浏览器自动收割（借自阶段 2）

- **安全**：`output/curl.txt` 从 0644 改为 **0600**（内容是活的 Scholar 会话 cookie 与
  浏览器指纹头），并在重写已有文件时纠正宽松权限
- **健壮**：粘贴的 cookie 名/值先剥掉 CR/LF/TAB，避免污染已保存的会话与后续请求头
- **便利**：cookie 提示处新增 `browser` 关键字 —— 环境里有 playwright 时打开**可见**浏览器，
  人工过验证/登录后按回车即**自动收割**（合成一段 cURL 走原有解析/注入/落盘路径，
  指纹三件套一起带上）；没有 playwright、非 TTY、或收割失败都自动落回原来的粘贴流程
- 新增 `tests/test_cookie_acquisition.py`（10 例）；全套 220 → **230 例 OK**

---

# Update History

本文档按时间倒序记录每次功能更新、bug 修复和重构的详细内容。

---

## 2026-10-08: 不要把 `'None'` 当成 `cites_id`

### 问题

scholarly 用行的 `data-cid` 拼 `url_scholarbib`；当某行没有被 Scholar 建 cluster 时该属性缺失，`.get()` 返回 `None`，URL 就变成 `info:None:scholar.google.com`。我们的正则 `info:([^:]+):` 会把字面量 **`'None'` 当成有效 cluster id** 存下来：

- 这类行会共享同一个身份键 `cites_id\tNone`，被去重逻辑当成同一条引文（不同论文被折叠）；
- 下游 `paper_downloader` 以 `cites_id` 为主键，会拿到一个不存在的 id。

当前生产数据里没有这种值（9054 行中 0 行），属潜在缺陷。

### 实现

- `crawler/citation_identity.py`：新增 `_clean_cites_id()`（空串与 `none`/`null`/`n/a`/`na` 一律视为无效），`_extract_cid_from_scholarbib()`、`_extract_cites_id_from_url()`、`normalize_cites_id()` 全部经过它 —— 后两者是唯一入口，因此也覆盖 `citedby_url` 解析与列表形态的值。

### 不做的事

不对存量数据做回填或迁移（按用户决定）：历史缺 id 的行会在该论文下次被抓取时，随新行按身份键替换而自然更新；页面本身没有 cluster id 的行（当前约占 20%）则保持 `null`。

### 验证

- 测试 219 → 220：`test_scholarbib_none_cluster_is_not_a_cites_id` —— 断言 `_extract_cid_from_scholarbib('…info:None:…')` 为 None、`extract_citation_info()` 的 `cites_id` 为 None、身份键退化为 meta 键（不含 `cites_id`），以及 `normalize_cites_id` 对 7 种哨兵值返回 None、列表形态只保留有效项。先在回退到 HEAD 的副本上验证为红（`'None' is not None`）。

---

## 2026-10-08: 收尾三项（状态不压过证据、Excel 行号、过期注释）

### 问题

1. **诊断声明压过实际数据**：`resolve_citation_status_from_output()` 只看 `_fetch_state` 里的诊断（`seen_total >= target`），不看 citations 数组。若某篇的诊断说"完整"而数组为空，它会被判 `complete` 并永远跳过 —— 输出里 0 条引文却写着 Complete=Y。
2. **profile Excel 行号取自 `pub['no']`**：`no` 缺失（被 `PubInfo` 归一为 0）或重复时，多篇论文写到同一行，最后一篇覆盖表头 —— JSON 有 3 篇而 Excel 只剩表头，且完全静默。
3. `crawler/output_state.py` 的模块注释仍说"每篇 cache 文件写在 `scholar_cache/`"，而该目录早已不再写入。

### 实现

- `crawler/output_state.py: resolve_citation_status_from_output()`：诊断判定为完整后，若 `state.citations` 为空则返回 `partial`（信任行数据而不是声明）。
- `crawler/profile_io.py`：Publications 表行号改用 `enumerate(publications, 2)`，`pub['no']` 只作为展示列。
- `crawler/output_state.py`：更新模块注释（状态来自输出文件，运行中的断点只在内存里、随运行结束写入输出）。

### 验证

- 测试 217 → 219：`test_complete_diagnostics_with_no_stored_rows_is_partial`（旧实现返回 `complete`）、`test_author_profile_xlsx_rows_follow_position_not_no`（旧实现第 1 行被 'Paper C' 覆盖）。两者先在回退到 HEAD 的副本上验证为红。

---

## 2026-10-08: 补上两个测试盲区（真实 openpyxl、真实 scholarly 契约）

### 问题

`tests/conftest.py` 把 `scholarly` 与 `openpyxl` 整体替换成 stub，因此：

- Excel 写入路径从未被真实库执行过 —— 一个错误的 openpyxl API 调用或 openpyxl 拒绝的取值都不会让任何测试变红（审计里"Excel 失败中止整轮"就是靠真实 openpyxl 才复现的）；
- 补丁层从未对着真实 `scholarly` 验证过 —— 上游改名时测试仍然全绿，而爬虫会静默失去浏览器头/限速等待/分页解析。

### 实现

- `tests/test_output.py`：`test_citations_xlsx_round_trips_with_real_openpyxl` —— 临时从 `sys.modules` 移除 stub、导入真实 openpyxl，调用 `save_citations_xlsx()` 写盘再用 `load_workbook()` 读回，校验三个 sheet 名与关键单元格。
- `tests/test_scholar_patch.py`：`test_real_scholarly_still_exposes_every_patched_hook` —— 同样临时恢复真实库，断言 `Navigator`（单例 `scholarly.scholarly._Scholarly__nav`）上的 `_get_page`/`_new_session`/`_set_retries`/`_TIMEOUT`/`_max_retries`/`_session1`/`_session2`/`pm1`/`pm2._handle_captcha2`、`_SearchScholarIterator.__init__/__next__/_load_url`、`PublicationParser._citation_pub` 都存在。

### 验证

- 测试 215 → 217（两个新测试均在真实依赖下通过；第一个测试同时确认导入到的确实不是 stub）。

---

## 2026-10-08: 输出口径（行为完成 vs 数据完整）、年份哨兵统一、输出前强制去重

### 问题

1. **`complete_fetch_attempt` 名不副实**：它由 `_compute_data_complete()` 按"数据是否完整"计算，而消费方（`rough` 模式、year 全无年份跳过、direct 续抓状态）都把它当作"本次抓取是否正常跑完"。于是它与输出的 `fetch_complete`（同样是数据完整性判定，但用不同时点的数字算）在 68/247 篇上互相矛盾，审计时无法判断谁对。
2. **年份哨兵不统一**：`'N/A'`、`'NA'`、`''`、`'?'` 在不同读取处被区别对待（生产数据里 102 条 `year='NA'`、7 篇 `pub.year` 为空），`year_count_map` 只跳过 `'N/A'/'NA'` 而不跳过 `''`；`extract_citation_info` 的 fallback 判断里也不含 `'N/A'`。
3. **篇内重复行**：9 篇存在同 `cites_id` 两行（元数据互相矛盾），另有大量"同一论文的不同 cluster"条目（审计显示下游因此把同一 PDF 下载两遍），文档却只说"容忍自重复"。

### 实现

- `crawler/citation_fetch.py`：`_compute_data_complete()` → `_compute_fetch_attempt_complete()`，语义改为"本次尝试跑到结束且没有遇到空页/被拦截页"（`empty_page`/`empty_first_page` 记为 False），并支持 `year_records` 是 list 或 `{year: diag}` 两种形状（原实现只认 list，实际传的是 dict）。
- `scholar_citation.py`：数据完整性只由 `fetch_complete = PaperFetchState.is_complete()` 表达；`rough` 的注释与 README 对齐为"计数未变 **且** 上次抓取正常跑完"。
- `crawler/citation_cache.py`：新增 `UNKNOWN_YEAR_VALUES` / `UNKNOWN_YEAR_SENTINEL='N/A'`、`normalize_citation_year()`、`is_unknown_year()`；`year_count_map()` 用它们统一判定。
- `crawler/citation_identity.py`：年份走 `normalize_citation_year(bib.get('pub_year'), fallback_year)`。
- `crawler/pub_info.py`：`from_dict`/`from_scholarly` 的年份统一为 `N/A` 哨兵。
- `scholar_citation.py: _dedup_citations_for_output()`：写盘前按"`cites_id` 主键 + 归一化标题"折叠重复行，保留元数据更丰富的一条（venue/url/authors/年份有效各计 1 分），并列时保留先出现者；折叠数量按论文打印。

### 验证与实测影响

- 测试 212 → 215：年份哨兵统一、按 `cites_id` 去重（保留 richer）、无 `cites_id` 时按标题去重；另有两个测试的 `complete_fetch_attempt` 断言按新语义更正（并保留 `is_complete()` 为假的断言，避免丢掉"数据不完整"的覆盖）。3 个新测试先在回退到 HEAD 的副本上验证为红（旧实现输出 `3 collected citation records`）。
- **真实数据实测**（只读跑一遍去重）：9054 → 8833 行，折叠 221 行 / 46 篇（189 个标题）。抽样确认折叠对象主要是"同一论文的不同 Scholar cluster"（例如 `large language diffusion models` 有 2 个 `cites_id`、`backdoorllm` 有 3 个），符合用户选择的"按 cites_id/title 强制去重"。
- 注意：`complete_fetch_attempt` 的**含义**变了；旧输出里的值会一直保留到该论文下次被抓取。旧值为 False 只会让 `rough` 多抓一次（安全），旧值为 True 与新的"正常跑完"判定不冲突。

---

## 2026-10-08: 收敛入口与 CLI（删除死入口、参数校验、-v/--accelerate 真正生效）

### 问题

1. `crawler/cli.py` 里有一份 `_run_main()`/`main()` 副本，与 `scholar_citation.main()` 复制粘贴后分叉，且必然崩溃：它对 `load_prev_profile()` 的返回值调用 `.get()`，而该函数返回 `AuthorProfile` dataclass（无 `get`）→ 第二次运行就 `AttributeError`。它没有任何调用方，也没有测试。
2. `has_pending_work()` 在 `main()` 里于 `run()` **之前**被调用，而输出状态是在 `run()` 里加载的 → `_citation_status` 把每篇有引文的论文都判成 `missing` → "总数未变则跳过 phase 2" 的短路分支永远不成立。
3. CLI 无参数校验：`--limit 0` 因 falsy 被当作"全量"（配 `--fetch-mode force` 会清空**所有**状态）、`--skip -1` 静默变成"最后一篇"、`--accelerate` 为 0 或负数会直冲限流或在 `time.sleep` 抛错。
4. `-v/--verbose` 只是被接受：结构化日志的 `setup_logging()` 只在那份死掉的 `_run_main()` 里调用过（76 个生产日志里 `[INFO]` 命中数为 0）。
5. `--accelerate` 在不配 `--interactive-captcha` 时被静默忽略（README 却把它列为独立开关）。
6. 一处死代码：`random.shuffle(need_fetch)` 的结果从未被使用（主循环按 `publications` 顺序跑），`need_fetch_set` 赋值后无人读；另有 4 个无调用方的 `PaperCitationFetcher` 静态包装、`config.SCHOLAR_CACHE_DIR`、`output_state` 里 4 个未使用的 frozenset。

### 实现

- 删除 `crawler/cli.py` 的 `_run_main()`/`main()`，该模块只保留 `parse_args()`；`parse_args()` 增加范围校验（`--skip >= 0`、`--limit >= 1`、`--accelerate > 0`，非法直接 `parser.error`）。
- `scholar_citation.main()`：调用 `setup_logging(verbose=...)` 并补上 `logger.info/debug`（console handler 走 `sys.stdout`，也就是 TeeStream，因此日志仍进运行日志，且不产生第二个写同一文件的句柄）；`--accelerate` 无条件生效并在未配 `--interactive-captcha` 时打印警告。
- `has_pending_work()` 在未加载状态时先加载输出状态。
- 删除上述死代码；`README` 的 CLI 章节同步（补 `exact`、`-v`、参数范围、`rough` 的真实语义、`exact` 的权威重建副作用）。

### 验证

- 测试 208 → 212：`parse_args` 边界值（4 组非法值 + 1 组合法值）、`has_pending_work` 先加载状态（旧实现返回 True）、`--accelerate` 生效且 `setup_logging(verbose=True)` 被调用（旧实现 delay_scale=1.0 且从不调用）。4 个新测试先在回退到 HEAD 的副本上验证为红。

---

## 2026-10-08: 写盘安全（原子写、Excel 容错、单次保存、skip_zero 不再清空引文）

### 问题

1. **非原子写**：citations 输出与 profile 都是 `open(path,'w')` + `json.dump`。写到一半崩溃/Ctrl+C/磁盘满会留下半截文件；读回时 citations 输出被当成"没有任何状态"（静默 `{}` → 整库重抓），profile 则直接 `JSONDecodeError`（无捕获 → 工具此后不可用）。
2. **Excel 失败会中止整轮**：`_save_xlsx` 在 JSON 落盘**之后**、无 try 保护，且 `citation_io`/`profile_io` 用 `cite['authors']`、`pub['no']` 直接下标（缺键、控制字符、孤立代理项都会抛）。
3. **双重保存**：重试耗尽路径先 `_save_output(results)` 再 `sys.exit(1)`，`run()` 的 `finally` 又存一次；第一次保存会清空 `_mid_paper_state`，第二次于是写回"运行前旧状态"，把在飞论文本次抓到的引文清零。
4. **`skip_zero` 清空已抓引文**：`cache_status` 对 `num_citations == 0` 直接返回 `('skip_zero', None, None)`，主循环写 `citations: []`，而 `_fetch_state` 仍是旧状态 → JSON 自相矛盾（Collected=0 但 Complete=Y），且上一轮的引文列表从输出里消失。
5. **两份输出不对账**：profile 里没有的论文会被 `_save_output` 直接丢掉，无任何提示。

### 实现

- `crawler/common.py: write_json_atomic()`：临时文件 + `fsync` + `os.replace`，失败时清理临时文件。
- `scholar_citation.py: _save_output()` 用原子写；Excel 写入包 `try/except` 降级为警告；不再清空 `_mid_paper_state`（`run()` 开头已重置），因此重复保存内容一致；移除 `sys.exit(1)` 前那次冗余保存；新增"上一篇输出里有、当前 profile 里没有"的对账警告。
- `crawler/profile_io.py`：`save_json` 改原子写；`load` 遇到坏文件时备份为 `.corrupt`、打印警告并按"首次运行"继续，而不是让之后每次运行都以 parse error 终止。
- `crawler/author_fetcher.py`：profile Excel 写入失败降级为警告。
- `crawler/output_state.py`：抽出 `_read_output_json()`，解析失败时备份为 `.corrupt` 并**明确告知**"全部论文按缺失处理"，不再静默返回 `{}`。
- `scholar_citation.py: cache_status()` 对 `skip_zero` 仍返回已缓存状态，主循环据此保留引文并打印 `keeping N cached`。

### 验证

- 测试 203 → 208，5 个新测试全部先在回退到 HEAD 的副本上验证为红：原子写失败后旧文件完好且无残留 `.tmp`、Excel 失败仍写出 JSON、重复保存保留在飞引文（旧实现写出 `[]`）、坏输出备份 + 警告、`skip_zero` 保留 3 条缓存（旧实现输出 `0 collected citation records`）。

---

## 2026-10-08: 让续抓真正续得上（并对齐计数口径）

### 问题

文档承诺的续抓通道实际全部退化，且通道一旦启用就会静默丢引文：

1. **in-page skip 差一位**：`fetch_session._run_once` 先 `_start_index += 1` 再解析该行；解析抛异常时这一行"被消费但没保存"，而重试的页对齐 + in-page skip 依据 `_start_index`，于是那一行被静默跳过。
2. **year 逐年 `seen_total` 重复计数**：年扫描在续抓时用缓存播种 batch，于是返回行里"已缓存"的部分被计为 dedup，而 `seen_total = cached_total + dedup` 又把它们加了一遍 → 短缺年份可被判 complete。
3. **`partial_year_start` 从不落盘**：`_resolve_refresh_strategy` 把它硬编码为 `{}`；`PaperFetchState` 也没有该字段，即使快照里有也会在 `to_dict()` 时被丢掉 → 中断的年份下轮从第 0 条重走整年。
4. **direct 续抓偏移永不前进**：`direct_next_index` 只在入口读一次，`save_progress` 从不按 batch 前进 → 同一次运行的整篇重试也从页首重抓。
5. **同 run 已完成年份没传给内层上下文**：`_fetch_by_year` 用 `self._completed_year_segments` 播种内层 ctx，而 `_fetch_citations_with_progress` 只把它写进外层 ctx → 整篇重试会重走已完成的年份。

### 实现

- `crawler/fetch_session.py`：解析失败时回退 `_start_index`（该行必须被重读），append 之后的失败不回退（该行已保存，重试应跳过）。
- `crawler/citation_fetch.py`：
  - 作废的逐年 `seen_total = cached_total + dedup` 改为"该年桶里的去重行数"，dedup 仅作日志/诊断；
  - `save_progress` 在 direct 模式下按 `batch.start_index` 前进 `direct_next_index`，并把 materialize 结果写回 `fresh_citations`（返回给主循环的就是最终列表，而不是本次扫描的后半段）；
  - 快照新增 `partial_year_start`（取内层 ctx 的实时值）；
  - `direct_materialized_citations` 增加 `authoritative=is_year_mode`：year 模式已按年自行决定 replace/merge，其列表是终态；direct 模式在"续抓或结果比缓存小"时按 identity 合并，保住已缓存的头部。
- `crawler/output_state.py`：`PaperFetchState` 新增 `partial_year_start`（`{year: offset}`、只保留 offset>0、JSON 友好、向后兼容缺失）。
- `scholar_citation.py`：
  - `_resolve_refresh_strategy` 从 `PaperFetchState`（或缓存 dict）读回 `partial_year_start`；
  - `_fetch_citations_with_progress` 把 `completed_years_in_current_run` 同步到 `self._completed_year_segments`（内层 ctx 的播种来源）；
  - `_fetch_by_year` 的 save 包装器把内层 `partial_year_start` 暴露为 `_live_partial_year_start`；
  - `_materialize_citation_cache` 增加 `authoritative` 开关（year 模式豁免"变小即合并"）。

### 有意不做的事

不把 `completed_years` 跨运行读回：年份循环的"本 run 已完成"判断发生在 probe 比较**之前**，跨运行保留会让某个年份即使 probe 说它变多了也被跳过。逐年 offset 只在"该年本身不完整"时存在，因此安全；"已完成年份"始终由逐年 probe 判定。

### 验证

- 测试 197 → 203。新增/改写：`test_cursor_steps_back_when_an_item_cannot_be_extracted`（回退到旧实现验证为红：`3 != 2`）、`test_resolve_refresh_strategy_restores_partial_year_start`、`test_resolve_refresh_strategy_defaults_partial_year_start_to_empty`、`test_partial_year_start_roundtrips_and_normalizes`、`test_interrupted_year_persists_its_resume_offset`、`test_direct_resume_state_advances_after_a_full_page`、`test_direct_resume_merges_the_cached_prefix`、`test_completed_years_skip_that_year_in_the_same_run`；`test_save_progress_wrapper_syncs_inner_ctx_dedup_to_fetcher` 的 `seen_total` 断言按新口径更正（1，而不是 2）。
- 四个关键新测试先在回退到 HEAD 的副本上验证为红（`{} != {2025: 10}`、`0 != 10`、后缀丢前缀等），再转绿。
- 真实数据回归：247 篇状态分布不变（complete 193 / partial 11 / skip_zero 43），无一篇带 `partial_year_start` → 新字段对存量数据零影响。

---

## 2026-10-08: PageVisit 加上退出条件，并接上换代理提示

### 问题

`PageVisit.fetch()` 的 docstring 写着"选项用尽后重新抛出原异常"，实现里却只有 `return` 与 `continue`：冷却分支还把 `retries`/`captcha_attempted` 重置，于是页面级失败会以"2 次快速重试 + 每次 5-10 分钟冷却"永久空转。后果：

- 上层的 `MAX_RETRIES` 放弃分支（`scholar_citation.py`）永远不可达；
- `page_visit.py` 头部与 `__init__` docstring 声称处理"proxy-switch prompts (non-interactive mode)"，但 `wait_proxy_switch_fn` 全仓库只在探测失败路径被调用，引文页从不提示换代理；
- README 承诺的"非交互模式最多等 24 小时"对引文页不成立。

同一族的无人值守问题：

- `try_interactive_captcha()` 没有 `prompt_first_curl()` 那样的 `isatty()` 守卫，`--interactive-captcha` 在管道/重定向 stdin 下会走进 `_read_multiline_input()`；
- `_read_multiline_input()` 的 `sys.stdin.fileno()` 在 `try` 之外，非真实 fd（StringIO/已关闭）会直接抛异常，从 `page_visit` 的 `except` 分支里冒出，终止整轮；
- `wait_proxy_switch()` 在 stdin 已 EOF 时 `select()` 恒为可读、`readline()` 恒返回空串，会 100% CPU 空转满 24 小时。

### 实现

- `crawler/page_visit.py`：新增 `cooldowns`/`max_cooldowns=3`；超过后先调用 `ctx.wait_proxy_switch_fn()`（返回 True 则重置计数继续），否则重新抛出原异常，交给上层的重试预算。交互模式捕获到验证码仍优先走粘贴 cURL 路径。
- `crawler/interactive.py`：
  - `try_interactive_captcha()` 增加 `sys.stdin.isatty()` 守卫，非终端时打印说明并返回 False（退回自动等待）；
  - `_read_multiline_input()` 把 `fileno()` 纳入 `try`，拿不到真实 fd 时返回 `[]`；
  - `wait_proxy_switch()` 把 `readline()` 返回空串识别为 stdin 已关闭，立即返回 False 而不是空转到超时。

### 验证

- 测试 186 → 195，新增 `tests/test_page_visit.py`（此前 `PageVisit` 零测试覆盖）：瞬时错误重试后成功、无换代理回调时 3 次冷却后抛错、确认换代理后继续并在下一次成功、换代理超时抛出原异常、交互式解题后立即重试、KeyboardInterrupt 直接传播、非 TTY 跳过粘贴提示、无 fd 返回空、stdin 关闭时立即返回。
- 计数按真实节奏钉住（每轮冷却 = 2 次快速重试 + 触发冷却的那次，共 12 次调用后才提示换代理），避免测试把循环结构写错。

---

## 2026-10-08: 修复 stall 计数从未落盘（"重复结果就停止重试"实际不生效）

### 问题

`stalled` 状态在生产中从未出现：247 篇输出里 `stall_signature` 全为空、76 个 run 日志里 `stalled` 0 次，`Mirror-Generative`(0/49) 每轮仍被计划 `+49 needed`。原因：抓取成功后计数记在 `_paper_states` 上（内存正确），但收尾 `_save_output()` 的 `_freshest_fetch_state()` 优先采用 in-run 快照，而 `save_progress()` 写的快照里没有 stall 字段（写快照时计数还没产生），`PaperFetchState.to_dict()` 于是把刚记下的 `stall_signature/stall_count` 清零。

原有回归测试 `test_two_identical_outcomes_stall_the_paper_across_runs` 是绿的，因为它 patch 了 `_load_citation_cache`、从不填充 `_mid_paper_state`，于是走了"没有快照"的回退分支 —— 恰好绕开生产必走的分支。

### 实现

- `scholar_citation.py: _save_output()._freshest_fetch_state()`：当数据源是 in-run 快照时，从 `_paper_states[title]`（抓取后状态）搬运 `stall_signature`/`stall_count`；数据源本来就是 `_paper_states` 时行为不变。

### 验证

- 测试 185 → 186：`test_save_output_persists_stall_counters_alongside_in_run_snapshot` —— 显式提供生产路径的 in-run 快照（带 `fetch_strategy`、无 stall 字段），断言落盘后计数为 2 且 `is_stalled()` 为真，并顺带钉住签名跨写盘/读回不漂移。
- 该测试先在本机副本上回退 `scholar_citation.py` 验证为红（`None != 'year:11/11=1[2026:4/5]'`），再转绿。
- `_work_notes.zh.md` 的 2026-10-04 节已加更正标注，避免继续误以为 `inherit_stall_tracking()` 已经解决。

---

## 2026-10-08: 探测失败必须清空上一篇论文的直方图

### 问题

`probe_citation_start_year()` 的复位语句写在"页面取回成功之后"，异常路径（3 次尝试全部失败）直接 `return None` 而不复位。`scholar_citation.py` 又把 `session_ctx.probed_year_counts` **无条件**写进每篇论文的 fetch context —— 于是论文 A 探测成功后论文 B 探测全败，B 会拿 A 的直方图做 `prev_seen >= live_count` 判定：整年被误跳（永不抓）或被误判完整，并把错误的 `histogram_count` 写进持久化诊断。

### 实现

- `crawler/scholarly_session.py: probe_citation_start_year()`：把 `ctx.probed_year_counts = None` / `probed_year_count_complete = False` 移到 `while True` 之前，覆盖包括 `return None` 在内的所有出口。

### 验证

- 测试 184 → 185：`test_probe_failure_clears_previous_paper_histogram` —— 先用直方图 DOM 探测成功（`{2021: 7}`、complete=True），再让 3 次尝试全部抛错，断言上下文被清空。

---

## 2026-10-08: 抓取结果不得删除它没有返回的数据（空页/截断页安全）

### 问题

一次抓取的返回值被当作"权威重建"，但它可能只是被拦截或截断：

- year 模式某年页面返回 0 行（HTTP 200、无异常、被 consent/拦截）时，`fresh_year_buckets[year] = []` 会用空桶替换该年缓存，**删掉该年已抓到的引文**；并且该年被记入 `completed_year_segments`（本 run 不再重试）。叠加 stall 检测后损失会永久化。
- 同一年从第 0 页全量重扫时 batch 是空的（只有 `start_index > 0` 才用缓存播种），所以一次截断的响应返回的行数少于缓存时，替换同样会删数据。
- direct 模式同构：`_materialize_citation_cache(complete=True)` 直接返回 fresh，只要 fetch 正常结束就把缓存里没被再次返回的引文丢掉。
- `no_probe_data_skip` 分支不看 probe 是否有逐年计数：探测彻底失败（DOM 失效导致回退路径不写 `probed_year_counts`）时，所有"缓存里有引文"的年份都被当作"不在直方图里"跳过，新引文永远抓不到，而日志显示正常完成。该分支也不看 `--fetch-mode`。

### 实现

- `crawler/citation_fetch.py`（year 循环）：
  - `empty_page`：不再触碰该年桶（缓存引文原地保留），不写入 `completed_year_segments`/`partial_year_start`，`cached_total`/`seen_total` 按保留后的条数记录 → 该年保持不完整、下轮重试。
  - 返回条数 < 缓存条数且非 `force_year_rebuild`：按 identity 合并（缓存行 + 新行），并打印 `keeping the cached rows (N total)`。`--fetch-mode exact` 的"权威重建"语义保留。
- `scholar_citation.py: _materialize_citation_cache()`：`complete=True` 仅在 `len(fresh) >= len(old)` 时整表替换，否则退化为按 identity 合并 —— direct 模式的同构保护。
- `crawler/citation_fetch.py`：`no_probe_data_skip` 增加 `histogram_authoritative` 门（probe 确实给出了逐年计数才允许据此跳过）；否则打印 `no histogram probe — an absent year is not evidence of completeness` 并正常抓取该年。

### 验证

- 测试 180 → 184：新增 `test_empty_page_keeps_cached_year_citations`、`test_truncated_year_scan_keeps_cached_rows`、`test_materialize_citation_cache_merges_when_complete_fetch_is_smaller`、`test_materialize_citation_cache_replaces_when_complete_fetch_is_not_smaller`；两个锁定旧行为的测试改名并拆分（`test_years_absent_from_an_authoritative_histogram_are_skipped` + `test_years_without_any_histogram_are_fetched_when_cache_exists`）。
- 新测试先按当前实现复现"空页删引文"（`[A,B] → []`）与"截断即删行"，确认红后再改实现。

---

## 2026-10-08: 启动健壮性：NO_PROXY 规范化 + 补丁生效自检

### 问题

1. `NO_PROXY` 含带方括号的 IPv6 字面量（macOS / 代理工具常见，如 `[::1]`）时 `import scholarly` 直接崩：scholarly 在构造 Navigator 单例时创建 `httpx.Client`，httpx 0.27.2 把 NO_PROXY 每项转成 URLPattern，`[::1]` 生成 `all://*[::1]` → `httpx.InvalidURL: Invalid port: ':1]'`。程序在拿到控制权之前退出，报错完全不指向根因。
2. 12 个 scholarly 补丁点没有任何生效校验：上游改名/改签名时不报错，只会静默失去浏览器头、限速等待或分页解析。已有活样本：`scholarly._citedby_long` 补丁从未被执行。

### 实现

- `crawler/common.py: sanitize_no_proxy()`：把 `NO_PROXY`/`no_proxy` 中的 `[IPv6]` 规范化为裸 `::1`（端口后缀丢弃——httpx 无法表达 IPv6+port，"该主机全部端口绕过代理"是安全方向）。幂等。
- `crawler/__init__.py` 在任何 `crawler.*` 子模块导入时执行一次；`scholar_citation.py` 在 `from scholarly import ...` **之前**显式调用（主入口先导入 scholarly，包初始化来不及生效）。
- `setup_proxy()` 额外回显最终生效的 `no_proxy`，便于确认规范化结果。
- `crawler/scholarly_session.py: assert_scholarly_patches_active()`：`patch_scholarly()` 结束时校验 9 个 hook（`_max_retries=1`、`_TIMEOUT=30`、`_get_page`、`_new_session`、`pm1/pm2._handle_captcha2`、`_SearchScholarIterator.__next__`/`_load_url`、`PublicationParser._citation_pub`），任一不符立刻 `RuntimeError`，提示 scholarly 版本不兼容。
- 移除不可达的 `scholarly._citedby_long` 补丁与 SessionContext 的 `current_year_segment`/`completed_year_segments`：scholarly 仅在对象带 `bib` 键时才走 `_citedby_long`，而 direct 的 citedby stub 没有该键，year 模式直接构造 `_SearchScholarIterator`；年份分段状态实际由 `citation_fetch.YearFetchSession` 持有。

### 验证

- 测试 174 → 180。新增 6 个：规范化与幂等、`httpx.Client` 在 `[::1]` 下构造成功（回归）、自检报错信息、`patch_scholarly` 全 hook 校验、`_set_retries` 失效时启动即报错。
- 本机 `NO_PROXY=localhost,127.0.0.1,::1,[::1]` 下：改动前 `python scholar_citation.py` 启动即崩、2 个测试报错；改动后同环境 180 个测试全过，`import crawler / scholarly / scholar_citation` 正常。

---

## 2026-10-04: 连续两次结果相同即停止重试（stall 检测）

### 问题

有一类论文**重试永远不会改变结果**：Scholar 自己的引用列表返回的条数少于它自己标的 "Cited by N"。例如 `Mirror-Generative`（0/49，已在浏览器确认是 Google 侧空页）、`PRIMT`(31/32)、`Getting more from less`(16/17)。在本次改动前，它们每轮都会重跑一次（0 行的那种白跑一次请求，少 1 条的那种白走整张列表），并长期占在 Fetch plan 里。

### 规则（用户定）

**连续两次得到相同结果，就在当前状态下不再重试**；"当前状态"指抓取结果的签名未变——Scholar 计数一变签名就变，自动恢复重试。

### 实现

- **签名**（`crawler/citation_cache.py: fetch_outcome_signature()`）：direct 为 `direct:{seen}/{scholar_total}`；year 为 `year:{Σseen}/{Σhist}={缺口}[{短年:seen/hist}]`（必须含短年，否则"填平 A 年、B 年仍缺"会被当成没变化）。
- **状态**（`crawler/output_state.py`）：新增持久化字段 `stall_signature` / `stall_count`；`record_fetch_outcome()` 在抓取正常返回后更新（相同 → +1，不同 → 重置 1）；`is_stalled()` = 计数达阈值且签名仍一致且无 `scholar_changed`。
- **新状态 `stalled`**：`resolve_citation_status_from_output()` 返回它（不进 need_fetch → 不发请求）；主循环打印 `-> stalled (same result N runs in a row)` 并保留已抓到的引用；运行摘要 `Stalled (skip): N`；计划末尾 `Skipping N stalled paper(s)`。
- **阈值**：`crawler/config.py: STALL_AFTER_ATTEMPTS = 2`。
- **中断不计入**：`record_fetch_outcome()` 只在抓取正常返回后调用，抛异常/被中断的尝试不算，避免"两次偶发封锁"就误停机。
- **状态重建要继承计数**：抓取成功后 `_paper_states[title]` 会被 cache 快照整体替换，快照不含 stall 字段；`inherit_stall_tracking()` 在替换处搬运，否则计数每轮从 1 重来、规则永不生效。
- **手动解锁**：`--fetch-mode force --skip N-1 --limit 1` 清状态后立即重试。

### 验证

- 测试 158 → 174；新增 16 个：签名（direct/year/含短年/无诊断）、计数与重置、阈值与 `scholar_changed`/结果变化的解除、`stalled` 状态、字段持久化、主循环跳过且保留引用、以及"连跑两轮 → 写盘 → 读回仍为 stalled"。
- 关键 4 个测试先 `git stash` 回退实现验证为红再转绿。
- 真实数据（247 篇）回归：状态分布 `complete 154 / partial 52 / skip_zero 41`，**无 stall**（已有状态没有该字段）→ 对现有抓取决策零影响；引用 9054 → 9054。

---

## 2026-10-04: 完善抓取日志（0 行页面不再静默）+ 修复"probe 年份无缓存时被丢弃"导致的漏抓

### 背景

排查 `Mirror-Generative Neural Machine Translation.` 长期 0/49 时发现：一次抓取跑完却拿到 **0 行**，日志里**什么都没有**，状态只记了 `termination_reason=iterator_exhausted`——和"这篇论文确实没有引用"完全无法区分。

两个信息丢失点：

1. **页面层**：`crawler/scholarly_session.py` 的行数统计原本是 `if page_size: print("Page items: N")`，**0 行时不打印任何东西**。而 scholarly 自身只会拦带 captcha id 的页面（`gs_captcha_ccl`/`recaptcha`/`captcha-form`/`rc-doscaptcha-body`），所以能走到解析层的 0 行页**必然不是验证码页**，其真实原因（空结果集 / 同意页 / 未识别的中间页）无从判断。
2. **落盘层**：`build_year_records()` 判断"哪些年份记录要保留"时读的是**外层** `year_ctx.probed_year_counts`，而 probe 实际写在内层 ctx（`_fetch_by_year` 自建），外层永远是 `None` → 于是"有 probe、但本次没抓到任何引用"的年份记录被**整体丢弃**。后果不只是丢日志：该年的 probe 数也从聚合 `histogram_total` 里消失，论文因此可能被判成 **complete**（例：2024 有 2/2、2026 probe=3 但页面 0 行 → 旧代码聚合只剩 2/2 → "完整"，2026 整年再也不会被重抓）。

### 修复

- **`crawler/scholarly_session.py`**
  - 新增 `citation_page_evidence(soup)`：返回 `{rows, results_container, result_banner, markers}`（markers 覆盖 captcha / consent / unusual_traffic / no_results），**永不抛异常**；
  - 新增 `format_page_evidence()` 与 `record_zero_row_citation_page()`；
  - `patched_load_url`：引用列表页出现 0 行时调用 `record_zero_row_citation_page()` → 打印 `Page items: 0 (no citation rows) [证据]` 并存入 `ctx.last_page_evidence`（过去这一行是静默的）；
  - `SessionContext` 新增字段 `last_page_evidence`。
- **`crawler/citation_fetch.py`**
  - direct 模式：拿到 0 行但 `scholar_total > 0` 时，termination 记为 **`empty_first_page`** 并打印明确说明（含 URL）；
  - year 模式：某年 probe > 0 但该年页面 0 行时，该年 termination 记为 **`empty_page`** 并打印说明；
  - `build_year_records()` 改用 `fetcher._live_probed_year_counts`（内层同步的实测 probe），必要时才回退外层 ctx —— 修掉上面第 2 条的信息（与完整性）漏洞。

### 验证

- 测试 145 → 158；新增 13 个：证据提取（满页/空页/验证码/中间页/consent/无 soup 不抛异常/格式化）、`record_zero_row_citation_page` 打日志并存证据、direct 的 `empty_first_page`（并保持"期望为 0 时不误报"）、year 的 `empty_page`、以及"probe 年份 0 行必须让论文保持 incomplete"。
- 三个 caller 侧测试 + "probe 年份被丢弃"测试都**先在旧代码上验证为红**（`git stash` 单文件回退）再转绿。
- 真实数据（247 篇）回归：`need_fetch` 仍为 52，引用 9054 → 9054，无状态翻转。

### 说明（未改行为）

本次只加**可观测性**，不改抓取/重试行为：0 行页面仍按原逻辑记为"本次无新引用"，只是现在会显式说明原因并在状态里可区分（`empty_first_page` / `empty_page`）。若要据此触发重试或换代理，属于行为变更，需另行决定。

### 后续确认：0/49 是 Google 侧问题

本次改动后，用户在浏览器直接打开 `Mirror-Generative` 的 `oi=bibs&cites=...` 链接，确认 **Scholar 自己的页面就返回空结果**（而作者页写 "Cited by 49"）。因此该篇的 0/49 既不是爬虫解析问题，也不是封锁/中间页——是 Google 侧"作者页计数 > 该 cites 链接可列出的条目"的不一致。该论文目前仍每轮重试一次（策略未改），只是日志与状态现在会明确显示为空页。

---

## 2026-10-04: 修复"被中断的抓取下次不再续传"（year 模式按年判定完整性 + 中断状态落盘）

### 问题

一次运行里某篇论文的详细引用没有抓完，下次运行这些未下载的引用不会再被续抓，且这一点体现在 Fetch plan 上（这些论文从计划里消失）。

### 根因（两条独立缺陷）

**1. year 模式用跨年聚合判定完整性，掩盖单年缺口**

`is_data_complete('year', summary)` 只比较 `seen_total >= histogram_total`（各年求和）。当某些年的 cached 超过 probe（Scholar 直方图与缓存桶天然会不一致）时，多出来的量会把另一年（真正没抓全）的缺口抵平 → 判 complete。`_plan_fetch_gap` 用同一口径算出 `needed = 0` → 被 `st != 'missing' and needed <= 0` 过滤 → 计划里"看不到"。

真实实例 `Deep matrix factorization`：2019/2020/2022–2025 各年 cached 超过 probe（合计 +21），抵平了 2021（220/222）与 2026（64/66）的缺口。

**2. 被中断的论文落盘的是"运行开始时的旧状态"，等于抹掉了"这次没抓完"**

`save_progress` 的 in-run 快照（含本次 live probe 与已抓到的引用）只存在于内存 `_mid_paper_state`；`_save_output` 只写 `_paper_states`（被中断的论文不会更新），写完还把 `_mid_paper_state` 清空。于是中断时：本次探到的缺口丢失、已抓到的引用丢失，论文按旧状态（seen ≥ 旧 target）落盘为 complete → 下次不再抓。

真实实例：2026-09-15 那次运行，第 21 篇 `Rethinking document-level NMT` 正在抓 2026 年（probe=5 / cached=4）时被打断，之后一直显示完整——那次只是靠残留的 `scholar_changed` 才被后来的运行救回来。

### 修复

- **`crawler/citation_cache.py`**：新增纯函数 `iter_year_records` / `year_record_gap` / `year_records_gap` / `year_records_shortfall_years` / `year_records_data_complete`（按年判定：每年 `seen_total >= histogram_count`，且仍要求聚合口径成立）。
- **`crawler/output_state.py`**：`PaperFetchState.is_complete()` 的 year 分支改用 `year_records_data_complete()`；`completeness_diag()` 追加 `; short years: 2026(4/5)`；新增 `year_shortfall_summary()`。
- **`crawler/citation_fetch.py`**：`_compute_data_complete()` 的 year 分支使用同一按年规则，落盘的 `complete_fetch_attempt` 不再与 `is_complete()` 自相矛盾。
- **`scholar_citation.py`**：
  - `_plan_fetch_gap()` year 分支改为"各年缺口之和"，并返回 `short_years` 供展示；Fetch plan 增加 `[short years: ...]` 注记
  - 计划里被过滤（无缺口）但**仍会处理**的论文单独汇总一行 `Also rechecking N paper(s) ...`，不再无声消失
  - `_save_output()` 优先使用本次运行的 in-run 快照（含已抓到的引用）；但仅当快照自带 `fetch_strategy` 时才允许覆盖旧状态——direct 模式中途 save 的快照还没有 `fetch_strategy`/`direct_fetch_diagnostics`，覆盖会把论文错判进 year 规则并报 complete（已加回归测试）
  - `run()` 起始处重置 `_mid_paper_state`
- `_complete_fetch_attempt` 改为三态（`None` = 未知/遗留），`from_dict` 不再把"键缺失"压成 `False`。

### 验证

- 测试 119 → 145；新增 8 个回归测试，每个都先在旧代码上复现失败再修复：聚合掩盖单年缺口 → partial；plan 缺口按年求和；中断论文落盘保留 live probe 与部分引用；重载后 partial 且计划显示 `2026(4/5)`；write 侧 cfa 与 `is_complete()` 一致；direct 中途快照不得覆盖旧状态；纯函数边界（空/字典形式/缺失字段）。
- 真实数据（247 篇）零影响：新旧规则下 `need_fetch` 均为 52 篇（当前数据中不存在"已落盘的单年缺口"）；`_save_output` 往返稳定（need_fetch 52→52，引用 9054→9054，无状态翻转）。
- 顺带撤回一个误判：先前怀疑的"plan 与 status 口径不一致"是验证脚本未复现 `cache_status` 的原地修改所致；逐条比对 2026-09-22 那次运行，计划中 15 篇全部被真正抓取。

### 影响面（重要）

本修复使**今后**中断的抓取能被续抓；对**已经**卡住的历史论文（如 `Academic paper recommendation` 54/55；`complete_fetch_attempt=False` 的 65 篇）不会自动重抓——它们的已落盘状态里没有可观测的单年缺口（差额来自 `scholar_total − histogram_total`，即 Scholar 两套计数的不一致；见 `_work_notes.zh.md` 对该差额含义的更正），若强行按 cfa 重抓会白跑 64 篇。如需清理，宜另做一次针对性补抓。

---

## 2026-08-13: 每次粘贴 cURL 时联动更新整套浏览器指纹（修复 Edge/Chrome 版本矛盾）

### 问题

用户粘贴了另一台电脑（Edge 151）的 cookie，但请求里浏览器指纹仍是硬编码的 Chrome 145：

- `_CURL_HEADER_ALLOWLIST` 故意排除了 `user-agent` / `sec-ch-ua` / `sec-ch-ua-full-version-list`（原注释：三者"必须保持彼此同步"），导致这三项永远不会从 curl 里更新
- 结果：Edge 151 签发的 cookie + Chrome 145 的 UA/sec-ch-ua → 指纹自相矛盾，属于风控可识别异常
- 日志证据：最新运行 `16:19` 注入新 cookie 后下一条请求仍立即 "Cannot Fetch" → **当前代理 IP 已被 Scholar 按 IP 级累计封锁**，cookie 换多少次都救不了当前 IP

### 修复

`crawler/scholarly_session.py`：
- 新增 `_BROWSER_IDENTITY_HEADERS`（user-agent / sec-ch-ua / sec-ch-ua-full-version-list），三者加入 `_CURL_HEADER_ALLOWLIST`
- 新增 `complete_browser_fingerprint()`：三者**全有或全无**整体采用 + UA↔sec-ch-ua 主版本一致性校验；不完整或版本矛盾时整组丢弃，回退到硬编码默认指纹，绝不产生部分覆盖的矛盾指纹

`crawler/interactive.py`：`inject_cookies_from_curl()` 在应用 header 前调用 `complete_browser_fingerprint()`。

启动自动加载（`curl.txt`）、交互式粘贴、403 重建 session（`_full_session_setup`）三条路径均采用最新指纹。

### 验证

- 新增 3 个测试（完整采用 / 部分缺失回退 / 版本矛盾回退），全量 122 个测试通过
- 用真实 `output/curl.txt` 模拟注入：user-agent → Edge 151、sec-ch-ua → Microsoft Edge v=151、full-version-list → 151.0.4129.72、sec-ch-ua-arch → x86、platform-version → 15.7.9，与 cookie 的浏览器完全一致

### 附注

当前 `output/curl.txt` 仍是完整登录态（SID / __Secure-*PSID / HSID / SSID / APISID / SAPISID / SIDCC 等 15 个账号认证 cookie），用户想用的"未登录" cookie 未生效。

---

## 2026-08-12: scholarly 超时从 5s 提升到 30s（修复 "Cannot Fetch"）

### 根因

用户报告"又不行了"。排查发现 httpx + cookies + headers 全部正常（三个端点 200），但爬虫实际运行时 profile 页也有 "Cannot Fetch from Google Scholar" 错误。

根因：**scholarly 的 `_TIMEOUT` 默认值 5 秒**，通过代理访问 Scholar 时偶发超过 5 秒即触发 TimeoutException → scholarly 内部 timeout 递增重试（5→10→15）→ 最终 `MaxTriesExceededException`。之前的 curl_cffi 429 问题叠加这个 5s timeout，导致一直以为是 TLS 指纹的锅——实际 curl_cffi 换了 httpx 后，429 没了但 5s timeout 还在。

### 修复

`crawler/scholarly_session.py` `patch_scholarly()` 中 `nav._TIMEOUT = 30`（直接设置属性，不用 `set_timeout` 方法以兼容测试 mock）。

### 验证（真实运行）

新运行（`_TIMEOUT=30`）vs 旧运行（`_TIMEOUT=5`）：

| | 旧（5s） | 新（30s） |
|---|---|---|
| Profile 页 1 | ✅ 200 | ✅ 200 |
| Profile 页 2 | ❌ "Cannot Fetch" | ✅ 200 |
| Profile 页 3 | ❌ "Cannot Fetch" | ✅ 200 |
| 引用页 probe | 未到达 | ✅ histogram 完整 |
| 年份抓取 | 未到达 | ✅ 采集到实际引用 |

完整 profile 3 页翻页 + 引用页 probe + 年份抓取全部成功，真正采集到了引用数据。

### 为什么之前没发现

之前 curl_cffi 429 时日志也报 "Cannot Fetch"，但那个是 curl_cffi 的 429 被 scholarly 的 403 处理路径吞没 + PageVisit retry 后最终 MaxTries。两个问题（curl_cffi 429 + scholarly 5s timeout）叠加，换了 httpx 后还剩 timeout 问题。这次对照实验（直接 httpx 200 vs 爬虫失败）精准定位了残余的 timeout。

---

## 2026-08-11: 交互式 cookie 粘贴失败重试

### 问题

用户在粘贴 cURL 时可能误操作（复制了错误的请求，如非 Scholar 资源或缺少 cookie 头）。此前 `prompt_first_curl` / `try_interactive_captcha` 在检测不到 cookies 时直接跳过，导致 cookies 注入失败、后续请求被 Scholar 拦截，用户只能重启程序。

### 修复（`crawler/interactive.py`）

- 新增共享辅助函数 `_inject_with_cookie_retry(inject_fn, skip_message, retry_message)`：循环读取粘贴内容，直到检测到 cookies（`inject_fn` 返回 > 0）或用户按 Enter 跳过
- `prompt_first_curl` / `try_interactive_captcha` 重构为调用该辅助函数，不再单次读取即放弃
- 重试提示语明确说明可能复制了错误的请求，引导用户从 DevTools 重新复制

### 验证

- 场景 1（误操作→重试成功）：第一次粘贴无 cookies → 提示重试 → 第二次粘贴有 cookies → 注入成功，读取 2 次
- 场景 2（用户跳过）：按 Enter → 立即返回 False，读取 1 次
- 119 个测试全部通过

---

## 2026-08-11: HTTP 客户端从 curl_cffi 改为 httpx（解决引用页 429）

### 根因：curl_cffi 被 Scholar 的 `/scholar?cites=` 端点识别

用户澄清浏览器和命令行走同一个 7890 代理后，之前的"代理 IP 限流"结论被推翻。通过对照实验彻底定位：

| 客户端 | profile 页 `/citations` | 引用页 `/scholar?cites=` |
|--------|------------------------|------------------------|
| 标准 curl (libcurl) | ✅ 200 | ✅ 200 |
| **httpx 0.27.2** (scholarly 原生) | ✅ 200 | ✅ **200** |
| **curl_cffi** (所有指纹: chrome110/124/131/145/edge101/safari) | ✅ 200 | ❌ **429/超时** |
| 浏览器 Edge 150 | ✅ | ✅ |

**同一代理（185.94.29.86）、同一 header、同一 cookies**，唯一差异是 HTTP 客户端库。Scholar 的引用搜索端点有更严格的反爬检测，能识别出 curl_cffi 的 TLS/HTTP2 指纹与真实浏览器/标准库的差异——而 profile 端点不查。

**关键发现**：之前投入的 chrome145/conda-forge/sec-fetch-site 修复都是对的（profile 页 200 证明链路正确），但 curl_cffi 本身是瓶颈——任何 impersonation 目标都无法通过引用端点检测。

### 修复：保留 scholarly 原生 httpx，移除 curl_cffi

`crawler/scholarly_session.py`：
- `_make_http2_session()` 从 `curl_cffi.Session(impersonate=chrome145)` 改为 `httpx.Client(http2=True, follow_redirects=True)`
- 移除 `_IMPERSONATE_TARGET`，`_BROWSER_HEADERS` 保留（注入到 httpx session）
- 移除 curl_cffi 依赖（requirements.txt 删除 `curl_cffi==0.15.0`）
- `_CURL_HEADER_ALLOWLIST` 注释更新

### 验证（真实运行）

`./run.sh --author HF3-E9kAAAAJ --limit 1 --fetch-mode force`：
- Profile 页 200，cookies 注入 2 个
- **引用页探测成功**：`histogram incomplete (hist_total=1340, scholar_total=1347)`
- **年份直方图完整读取**：`2018:37, 2019:107, ..., 2026:55`
- **2017 年抓取成功**：`Year 2017 done: 3 new in year`（采集到 3 条真实引用）
- 逐年份继续抓取正常

**对比之前 curl_cffi**：同样 URL 直接 429，引用页探测、年份抓取全部失败。

### 环境说明

curl_cffi 0.14.0（pip）/ 0.15.0（conda-forge）均已验证在引用端点上失败。httpx 0.27.2（scholarly 原生，conda-forge）成功。scholarly 依赖链中 httpx 本来就存在，无需新增依赖。

---

## 2026-08-11: Fetch plan 过滤无缺口论文 + 下载问题根因定位（代理 IP 限流）

### Fetch plan 不再输出 cached==target 的无更新论文

**问题**：Fetch plan 列出了大量 `cached=N, target=N`（无缺口）的论文，看起来"没有更新需求"却出现在列表中。

**根因**：部分论文 `cached == scholar_total` 且 `is_complete() == True`，但 `scholar_changed` 标志残留（上次运行引用数变化时标记，未随 fetch 完成清除），导致 `need_fetch()` 返回 True → 进入抓取计划。这些论文数据实际完整，无真实缺口。

**修复**（`scholar_citation.py`）：
- 新增 `_plan_fetch_gap(strategy, cached, scholar_total, cached_count)`：基于 diagnostics 计算真实缺口 `needed = max(0, target - seen)`（year: histogram_total - seen_total；direct: scholar_total - seen_total），无 diagnostics 时回退到 `scholar_total - len(citations)`
- Fetch plan 循环跳过 `needed <= 0` 的论文（非 missing），只显示真正需要获取新数据的论文
- 显示格式增强：`cached=X, seen=Y, target=Z, +N needed`，`missing` 显示 `target=N (first fetch)`

**效果**：75 篇需抓取论文中，52 篇有真实缺口（显示），23 篇无缺口（过滤）。此前 5 篇 cached==target 的论文（Adaptive NNMT 139/139、Question translation 63/63 等）全部正确过滤。

### 下载问题根因定位：代理 IP 被 Scholar 限流（429）

**问题**：换 curl_cffi 0.15.0（conda-forge，chrome145）后仍无法下载 citations。

**排查过程**（运行真实爬虫测试）：
1. Profile 页面正常（HTTP 200，含 "Shujian Huang (黄书剑)"）→ **TLS/headers/cookies 全部正确**
2. Citations 页面 HTTP 429（`unusual traffic`）→ **IP 级限流**
3. 出口 IP 对比：代理 185.94.29.86（海外）vs 直连 202.119.41.110（南京）
4. 直连 Google 超时（被墙），必须走代理

**结论**：下载失败的根因**不是** TLS/cookies/headers（都已对齐 Chrome 145），而是**代理出口 IP 185.94.29.86 被 Scholar 限流**。用户浏览器能访问是因为浏览器走了不同的代理节点（不同出口 IP）。**解决方案：在代理工具（Clash 等）中切换节点，更换出口 IP**。

---

## 2026-08-11: TLS 指纹对齐 chrome145（conda-forge 0.15.0）+ sec-fetch-site 修复 + Fetch plan + 环境 conda 化

### 根因排查：curl_cffi 版本与 TLS 指纹不匹配

用户报告「浏览器可以访问，但 crawler 仍被 block」。深入排查发现的问题：

1. **pip 版 curl_cffi 0.15.0/0.16.0 在 macOS arm64 上损坏**：`import curl_cffi` 即崩溃（0.15.0 报 `_CFRelease` 符号缺失，0.16.0 报 `_CFArrayAppendValue` 符号缺失）。这是 PyPI wheel 编译时未链接 CoreFoundation 框架。
2. **pip 0.14.0 不支持 `chrome145`**：降级到 0.14.0 后实测 `chrome145` 抛 `ImpersonateError`，TLS 指纹回退到无伪装状态 → Google 在 TLS 层识别出爬虫。pip 0.14.0 支持的最高 Chrome 目标为 `chrome131`。
3. **Cookie 注入后 `sec-fetch-site` 不一致**：从浏览器粘贴 cURL 注入 cookie 后，`sec-fetch-site` 仍为 `'none'`（跨站导航），但 cookie 来自已在 Scholar 页面上的浏览器（应发 `same-origin`）。"有 session cookie" + "声称跨站访问" = 自相矛盾，触发反爬检测。

### 关键发现：conda-forge 版 curl-cffi 0.15.0 完全可用

**初版方案误以为 conda-forge 的 0.15.0 与 pip 版同样损坏，将 TLS 目标降为 chrome131。实测发现 conda-forge 用自己的编译链正确链接了 CoreFoundation，`import` 正常且支持 `chrome145`**（0.15.0 最高目标）。因此最终方案：

- 用 `conda install -n scholar curl-cffi=0.15.0 -c conda-forge` 安装（弃用 pip 0.14.0）
- `_IMPERSONATE_TARGET` 保持 `chrome145`，`_BROWSER_HEADERS` 对齐 Chrome 145

### 修改

- **`crawler/scholarly_session.py`**：
  - `_IMPERSONATE_TARGET` 最终为 `chrome145`（与 conda-forge 0.15.0 匹配）
  - `_BROWSER_HEADERS` 对齐 Chrome 145（user-agent、sec-ch-ua、sec-ch-ua-full-version-list、sec-ch-ua-platform-version）
  - `_full_session_setup()` 增加 `if ctx.injected_cookies:` 时覆盖 `sec-fetch-site: same-origin`
  - 代码注释记录 pip/conda-forge 版本差异
- **`crawler/interactive.py`**：`inject_cookies_from_curl()` cookie 注入成功后设置 `sec-fetch-site: same-origin`
- **`requirements.txt`**：`curl_cffi==0.15.0`（附注释：macOS arm64 必须用 conda-forge 版）

### 新增：Fetch plan 预检清单

- **`scholar_citation.py`** `run()` 汇总统计后新增 Fetch plan 段落：逐篇列出论文、策略（YEAR/DIRECT）、已缓存数、目标引用数、缺口（+N needed）、Scholar 计数变化（old→new）。仅在有需要抓取的论文时显示。

### 环境 conda 化

按用户要求，scholar 环境中 requirements.txt 依赖从 pip 版转为 conda-forge 版：

| 包 | 之前 | 现在 |
|----|------|------|
| scholarly | pip 1.7.11 | conda-forge 1.7.11 |
| openpyxl | pip 3.1.5 | conda-forge 3.1.5 |
| httpx | conda 0.28.1 | **conda-forge 0.27.2**（对齐 requirements.txt）|
| requests | pip 2.32.5 | conda-forge 2.34.2 |
| selenium | pip 4.41.0 | conda-forge 4.46.0 |
| free-proxy | pip 1.1.3 | conda-forge 1.2.2 |
| curl-cffi | pip 0.14.0 | **conda-forge 0.15.0**（chrome145）|

**踩坑**：pip 卸载后直接 `conda install` 会报 "already installed" 但实际文件缺失（pip 删除了 site-packages 文件，conda 只更新了 metadata）。修复方法：`conda remove --force` 清理不一致的 metadata 后再 `conda install`。涉及的三个包（requests、selenium、free-proxy）均按此流程处理。

### 其他清理

- 删除根目录残留的过期 `curl.txt`（74 字节，内含假 cookie `SID=abc`）。代码只读 `output/curl.txt`，不会误读，但该文件易造成混淆。

---

## 2026-07-05: 结构化日志 + 配置集中化 + 项目规范化

- **`crawler/log_utils.py` 新增**：`setup_logging(verbose, log_file)` 配置 console + file handler，支持日志级别过滤。`--verbose` / `-v` 参数开启 DEBUG 级别输出（走 stderr，不干扰 stdout 通过 TeeStream 写入日志文件）。新增 `get_logger()` 工具函数。现有 `print()` 调用全部保留，通过 TeeStream 继续写入日志文件；新增代码使用 `logger.info()` / `logger.debug()` / `logger.warning()` / `logger.error()`。
- **`crawler/config.py` 新增**：集中管理所有可调优参数（延迟、阈值、路径），提供单一配置入口。`crawler/common.py` 中的常量已迁移至此并保持向后兼容 re-export，现有代码无需修改。
- **`run.sh` 新增**：便捷入口，自动检测 conda 环境。
- **`.gitignore` 修正**：移除 `CLAUDE.md`（纳入版本控制），`.claude/` 目录已足够保护敏感配置。
- **CLAUDE.md 更新**：新增模块文档、`-v` 示例、`run.sh` 用法；依赖图更新。

## 2026-06-16: captcha 计数修正 + year_records 保存

- **captcha 计数修正**：`inject_cookies_from_curl` 在每次注入 cookie 时都会递增 `captcha_solved_count`，但该函数不仅在 captcha 恢复时被调用，也在程序启动加载 `curl.txt` 和首次 curl 提示时被调用。修复：将计数器递增从 `inject_cookies_from_curl`（`crawler/interactive.py`）移至 `_try_interactive_captcha`（`scholar_citation.py`），只在 captcha 成功解决后才递增。同时移除 `inject_cookies_from_curl` 的 `captcha_solved_count_ref` 参数和日志中的 `Captcha solves: N` 输出。
- **exact 模式 `force_year_rebuild`**：`force_year_rebuild = (self.fetch_mode == 'exact')`。在 exact 模式下，重新抓取的年份完全替换旧缓存数据，而非与旧数据合并。防止旧引用超过 probe 数量后持续存在——例如上次多抓了 3 条引用（seen > probe），normal mode 会保留它们，但 exact mode 会丢弃，以 probe 为准。
- **`save_progress` 补充 `year_records` 字段**：进度保存时写入 `year_records`（`year_records_to_save`），确保断点续传时 year_records 数据不丢失。

## 2026-06-05: CSS 选择器 `class_` 精确匹配修正

- **`class_='gs_r gs_or'` → `soup.select('div.gs_r.gs_or')`**：BeautifulSoup 的 `class_='gs_r gs_or'` 做的是 class 属性的**精确字符串匹配**，只匹配 `class="gs_r gs_or"` 的元素。但很多结果行还包含 `gs_scl`，实际 class 为 `"gs_r gs_or gs_scl"`，被精确匹配误判为不匹配。修改为 CSS 选择器 `div.gs_r.gs_or`，浏览器语义：同时拥有 `gs_r` 和 `gs_or` 两个 class 即匹配（真正的 AND 语义）。

## 2026-06-05: curl_cffi TLS 指纹 + 选择器修正 + 429 处理 + header 同步

- **`curl_cffi` 替换 `httpx`**：`_make_http2_session()` 改用 `curl_cffi.requests.Session(impersonate='chrome124')`，模拟 Chrome 124 的 TLS 握手。Google 从 TLS 层面无法区分 crawler 和真实浏览器，不再需要 cookie/header 的各种 workaround。
- **CSS 选择器修正**：`patched_load_url` 中用 `gs_r gs_or`（至少两个 class）替代 scholarly 原生的 `gs_r gs_or gs_scl`（要求三个 class）。比原始宽（捕获末尾元素），比 `div.gs_r` 窄（不匹配错误页面元素）。
- **429 频率限制处理**：所有重试耗尽后进入 5-10 分钟静默冷却，而非 proxy switch 或 captcha 提示。
- **`sec-fetch-*` header 同步**：加入 curl allowlist，消除 `sec-fetch-site: same-origin` vs `none` 的不一致。
- **cookie 注入前清理旧 cookie**。
- **no-probe 年份 skip**。
- **`--fetch-mode exact`**。

- **429 频率限制特殊处理**：Google 返回 "unusual traffic" (429) 而非 captcha 时，不再弹出验证码提示。所有重试耗尽后进入 5-10 分钟静默冷却等待，而非 24 小时 proxy switch 阻塞。直接测试验证 cookies + URL 均正确，问题仅在请求频率。
- **`sec-fetch-*` header 同步**：将 `sec-fetch-site` 等 5 个 header 加入 curl allowlist。浏览器 curl 的 `sec-fetch-site: none` 与 crawler 硬编码的 `same-origin` 不一致，Google 据此判断非浏览器访问。
- **cookie 注入前清理旧 cookie**：`inject_cookies_from_curl` 注入前先 `session.cookies.clear()`，防止失败请求残留的追踪 cookie 污染新会话。
- **no-probe 年份 skip**：probe 没有数据的年份不再尝试抓取，直接跳过。
- **`--fetch-mode exact`**：年份级别精确匹配，`seen == probe` 才跳过。
- **CSS 选择器**：`div.gs_r` 替代 `gs_r gs_or gs_scl`，确保所有结果行都被捕获。
- **跨年份新引用去重**：`counted_new_keys` 集合防止重复计数。

## 2026-06-05: `--fetch-mode exact` + CSS 选择器修正 + no-probe 年份 skip (initial)

- **新增 `--fetch-mode exact`**：年份级别精确匹配，`seen == probe` 才跳过（而非 `>=`）。`seen > probe` 时也会重新抓取，捕获 Scholar 年份归类漂移。
- **CSS 选择器修正**：`patched_load_url` 中用 `div.gs_r` 替代 scholarly 原生的 `gs_r gs_or gs_scl`，不再按 CSS class 白名单过滤，确保所有结果行都被捕获。
- **无 probe 数据的年份 skip**：当 histogram 完整但不包含某年份时，若缓存已有数据则跳过，避免无效访问。
- **跨年份新引用去重**：`counted_new_keys` 集合防止同一引用跨多个年份桶重复计数。

## 2026-06-02: 页面等待与重试机制修复 + _max_retries=0 回退

### 问题链路
用户报告爬虫在 profile 阶段长时间卡住（从 "Waiting 54s" 到 "Blocked fetching" 间隔 1 小时），以及无法访问 Scholar 页面。

### 根因分析（按发现顺序）
1. **PageVisit 丢失 unified_sleep**：`384e240` 引入 PageVisit 时删除了 `unified_sleep` 机制——该机制拦截 scholarly `_get_page` 内部的 60-120s 等待，替换为 45-90s `rand_delay`，并限制每页最多 1 次 sleep。丢失后 scholarly 和 PageVisit 的等待叠加。
2. **`_max_retries=0` 的致命错误**：处理问题 1 时，误以为 `_max_retries` 是重试次数，设成 0 想禁止 scholarly 内部重试。但 scholarly `_get_page` 的 while 条件是 `while tries < self._max_retries`，0 导致 `0 < 0` 永远为 False，HTTP 请求从未发出。
3. **curl cookie 解析只支持 `-b` 格式**：现代浏览器（Chrome/Edge）输出的 cURL 使用 `-H 'cookie: ...'` 格式，解析器不认识，直接返回 0。
4. **user-agent 指纹不一致**：curl 中的 `user-agent` 不在 allowlist，cookies 来自 Edge/146 但 user-agent 仍是硬编码的 Edge/145，Google 检测到浏览器版本冲突。

### 修复列表
- 恢复 `unified_sleep` 为 `_fetch_with_sleep_limit()` 闭包，拦截 scholarly 内部 sleep，替换为 `rand_delay`，限制每页 2 次 sleep
- **回退 `_max_retries=0`** 为 `_max_retries=1`（即 `nav._set_retries(1)`），确保 while 循环至少执行一次
- 第一个页面跳过等待（`total_page_count == 1` 时 `_limited_sleep` 的第一个 sleep 不做 rand_delay）
- captcha 提示前移除无效的 45-90s 等待
- curl cookie 解析增加 `-H 'cookie:'` 格式支持
- `user-agent` 加入 curl header allowlist，保持浏览器指纹一致

## 2026-05-19: 修复 scholar_changed 同次运行失效 + Year done 日志歧义

- **`cache_status` 中 `scholar_changed` 同次运行不生效**：`_citation_status()` 在 `mark_scholar_changed()` 之前被调用，导致本次检测到引用数变化的论文仍需等到下一次运行才会被重新抓取。修复：重构 `cache_status`，先更新 `PaperFetchState`（`num_citations_on_scholar` + `scholar_changed`）再调用 `_citation_status()`。
- **Year done 日志只显示 year_new_count**：`Year XX done: N new citations` 中的 N 是年份桶新增（`year_new_count`），而非真正的新引用（`_new_citations_count`），与 Paper Done 和 wait status 中显示的计数不一致。修复：改为同时显示两个值：`N new in year, M truly new this paper`。

## 2026-05-17: 文档修正 — 过期引用和错误示例

- `_work_notes.zh.md`：修正 7 处过期/错误内容
  - `_output_fetch_state` / `_output_citations` → `_paper_states`
  - "三层内存结构" → "两层内存结构"
  - `direct_fetch_diagnostics` 示例从旧嵌套格式改为当前扁平格式
  - `direct_summary` → `direct_fetch_diagnostics`
- `README.md`：`output_state.py` 描述加入 `PaperState`

## 2026-05-16: PaperState 封装 — 合并 fetch state 与 citations

- 新增 `PaperState` dataclass（`crawler/output_state.py`）：包含 `fetch: PaperFetchState` + `citations: list`
- `_output_fetch_state` + `_output_citations` 两个独立 dict → 单个 `_paper_states: {title: PaperState}`
- `cache_status` 简化为直接读取 `PaperState.fetch` 和 `PaperState.citations`
- 修复 `restore_direct_diag_from_citations` 中对只读 property 的错误赋值

## 2026-05-15: 删除 `restore_from_cache_snapshot`，`cache_status` 直接更新 PaperFetchState

- `restore_from_cache_snapshot` 被删除——其功能完全等同于 `PaperFetchState.from_dict(cache_snapshot)`，存在多余
- `cache_status` 现在直接在 `PaperFetchState` 对象上更新 `num_citations_on_scholar`、`complete_fetch_attempt` 和 diagnostics，不再只修补临时的 synthetic dict
- 设计原则：`_output_fetch_state` 中的 `PaperFetchState` 是唯一权威对象，dict 仅用于接口兼容

## 2026-05-15: 修复 `restore_from_cache_snapshot` 遗漏字段（已被后续重构替换）

- `restore_from_cache_snapshot()` 漏掉了 `complete_fetch_attempt`、`fetch_strategy`、`num_citations_on_scholar` 三个字段
- 导致 fetch 成功后输出 JSON 中 `complete_fetch_attempt` 始终为 false，`fetch_strategy` 不随阈值变化更新
- 修复：从 `cache_snapshot` 中同步这三个字段

---

## 2026-05-14: 代码规范清理 — 删除死代码、消除冗余、修复 Bug、更新文档

- **死代码删除**：`DirectFetchSession`、`Citation`、`YearDiagnostics`、`DirectDiagnostics`、`extract_fetch_state`、`build_profile_payload`/`save_profile_json`、`citation_status`、`refresh_reconciliation_status`、`_resort_publications`、`_effective_scholar_total` 的未使用参数
- **冗余消除**：合并 `_direct_fetch_diagnostics` 包装器、`_direct_fetch_*_message` 函数；`year_records` 解析逻辑提取为 `index_year_records()`
- **Bug 修复**：`year_fetched_citations` → `year_batch.citations`（未定义变量 NameError）
- **封装性改进**：`restore_from_cache_snapshot()` 封装私有字段写入；`to_paper_fetch_state()` 统一类型分派
- **参数精简**：删除 `rehydrated_probed_year_counts`/`rehydrated_probe_complete`/`rehydrated_year_fetch_diagnostics`（始终为 None/False/None）
- **文档修正**：测试数 127→119、删除 test_citation_page_stop.py 引用、PaperFetchState 字段数 10→11、依赖映射补全、版本约束修正
- 测试从 126 精简到 119（删除冗余/死代码相关测试）

---

## 2026-05-12: 代码规范 — 消除 4 处冗余代码 + 1 个 Bug 修复

### Issue 1: 删除 `_direct_fetch_diagnostics` 冗余包装器
- 删除 `citation_fetch.py` 中的 `_direct_fetch_diagnostics()`（只是 `_build_direct_fetch_diagnostics()` 的平凡包装器）
- 删除 `scholar_citation.py` 中对应包装器方法
- `test_citation_status.py` 中相关测试改为直接调用 `_build_direct_fetch_diagnostics`

### Issue 2: 合并 `_direct_fetch_summary_message` 和 `_direct_fetch_log_message`
- 两个函数体完全相同，仅前缀字符串不同
- 合并为 `_direct_fetch_diagnostics_message(diagnostics, prefix=...)`
- `citation_fetch.py` 和 `scholar_citation.py` 中两处调用点改为使用新函数并传入不同 prefix

### Issue 3: 提取 year_records 解析逻辑为共享函数
- `scholar_citation.py` 中相同的 `for rec in yr` 解析模式出现了 3 次
- 提取为 `index_year_records()` 放入 `crawler/output_state.py`
- `scholar_citation.py` 导入为 `_os_index_year_records`，3 处调用点均已替换

### Issue 4: 修复 `year_fetched_citations` 未定义变量 Bug
- `crawler/citation_fetch.py:875,879` 中使用 `year_fetched_citations`，但该变量从未在 `fetch_by_year` 中赋值
- 当 `stop_partial_resume_once_satisfied` 和 `resuming_partial_year` 都为 True 时会引发 NameError
- 替换为 `year_batch.citations`（同一函数体内已在使用的正确变量）

---

## 2026-05-12: 封装性修复 — PaperFetchState 私有字段不再被外部直接赋值

### 问题
`scholar_citation.py` 在 fetch 完成后有 4 处直接赋值 `PaperFetchState` 的私有字段：
- `pst._year_records = yr`
- `pst._direct_fetch_diagnostics = dfd`
- `pst._year_fetch_diagnostics = yfd`
- `pst._fetched_at = cache_snapshot.get('fetched_at') or datetime.now().isoformat()`

这违反了封装原则。

### 修复
- 在 `crawler/output_state.py` 的 `PaperFetchState` 类中添加 `restore_from_cache_snapshot(cache_snapshot)` 方法，将所有私有字段更新集中在一个方法内
- 修改 `scholar_citation.py` 中对应的代码，使用 `pst.restore_from_cache_snapshot(cache_snapshot)` 替换所有 4 处直接赋值
- 新方法遵循 chainable 惯例（返回 self）

### 影响文件
- `crawler/output_state.py`: 添加 `restore_from_cache_snapshot()` 方法
- `scholar_citation.py`: 替换直接私有字段赋值为方法调用

---
## 2026-05-13: 大规模重构 — 删除 cache 文件、rehydrate 函数，强化 PaperFetchState 封装

### 删除 cache 文件体系

- `save_progress` 不再写磁盘，改为存入 `fetcher._mid_paper_state[title]`（内存 dict）
- `_load_citation_cache` 改为从 `_mid_paper_state` 读取，不再访问文件系统
- 删除：`citation_cache_path()`、`cache_dir`、cache 目录创建和清理、`json.dump` 写入
- 所有中间状态通过 `_mid_paper_state` + `_output_fetch_state` + `_output_citations` 管理

### 删除 rehydrate 函数

- 删除 `rehydrate_year_fetch_diagnostics`：`_resolve_refresh_strategy` 直接从 `year_records` 读取 per-year 列表
- 删除 `rehydrate_probe_metadata`：probe 永远在 `fetch_by_year` 中新鲜执行，不再读缓存
- `_resolve_refresh_strategy` 优先从 `PaperFetchState` 属性读取（`year_records`、`direct_fetch_diagnostics`、`num_citations_on_scholar` 等），dict 仅作为 fallback

### Paper Done 日志增强

- 显示 fetch target 比较：`year: seen_total=N ≥ histogram_total=M` 或 `direct: seen_total=N ≥ scholar_total=M`
- 加入 per-paper new 计数

### 其他修复

- `year_new_count` 改为年份桶增量（比较 `old_year_identity_keys` 而非全局 `old_cache_identity_keys`）
- Year fetch 日志显示 `seen`（判断依据）而非 `cached`（不含 dedup）
- `_new_citations_count` 每篇论文重置，避免跨论文累加
- Fetch 完成后将 diagnostics 从 `_mid_paper_state` 同步到 `_output_fetch_state`

---

## 2026-05-12: 日志一致性修正 — 判断依据与打印值统一

- Year `fetching` / `resuming` 日志之前显示 `cached=320`（不含 dedup），但跳过判断使用的是 `seen_total=321`（含 dedup）→ 改为显示 `seen`，与判断逻辑一致
- Paper `Done` 日志加入 `N new this paper`，与 progress 行和 year-done 行形成完整的 per-year → per-paper → per-run 三级新引用计数
- `_format_completeness_diag` 优先使用 PaperFetchState，避免重新从 dict 构建

---

## 2026-05-12: 修复 year 跳过判断失效 — 从 year_records 直接读取 per-year 数据

- 输出文件中 `year_fetch_diagnostics` 是 summary（8 个汇总字段），`year_records` 才是 per-year 条目
- `_resolve_refresh_strategy` 原本调用 `rehydrate_year_fetch_diagnostics(cached)` 从 dict 中提取 per-year 数据，但函数读取了 summary 字段，格式错配返回 None
- skip 逻辑回退到 `cached_year_counts`（丢失 dedup_count），导致 `seen_total >= histogram` 为 True 的年份无法跳过
- 重构：`_resolve_refresh_strategy` 直接从 `cached.get('year_records')`（由 `PaperFetchState.to_dict()` 提供）读取 per-year 列表，转换为 `{year: diag}`，不再绕道 `rehydrate_year_fetch_diagnostics`
- `rehydrate_year_fetch_diagnostics` 保留为 fallback（优先读 `year_records`），供旧格式数据和 retry 路径使用

---

## 2026-05-12: 修复 year_new_count 统计为年份桶增量（而非跨年去重）

- `year_new_count` 之前用 `old_cache_identity_keys`（全局旧缓存）做 `is_new` 判断
- 当某引用从旧缓存的 2024 年"移动"到 2026 年时，`is_new=False`，被错误地排除
- 用户看到 `cached=20, probe=23` 期望 3 个 new，但实际只计入 1 个（跨年去重过滤了 2 个）
- 修复：新增 `old_year_identity_keys`（仅包含该年份旧桶中引用的 identity key），`year_new_count` 计数时使用该年份范围的 key 集合 — `is_new=True`（全局新）或 citation 不在 `old_year_identity_keys` 中（年份桶新）均计入
- `fetcher._new_citations_count` 保留跨年去重语义（真正的新引用），进度行同时显示两种计数
- 同步修复：`_new_citations_count` 在每个 paper 前重置，避免跨 paper 累加

---

## 2026-05-12: 修复 new citations 跨 paper 累加导致进度日志计数偏差

此问题仅为症状。真正的根因是 `year_new_count` 使用了错误的比较范围（全局旧缓存 vs 年份桶），详见上方条目。

旧记录保留如下：

- `_new_citations_count` 之前在整个 run 中只重置一次，导致第 N 篇 paper 的进度日志 `N new across run` 包含了前面 papers 的 new 总数
- 而 `year_new_count` 是每个 paper 每个 year 独立计数 → 两者数值不一致
- 修复：每个 paper 处理前重置 `_new_citations_count`，新增 `_run_new_citations_total` 记录 run 级别总计
- 进度日志文本 `new across run` → `new this paper` 以消除歧义

---

## 2026-05-12: CLAUDE.md 去重 — 精简项目结构为引用

- CLAUDE.md 中冗长的树形项目结构（55 行）替换为引用 `_work_notes.zh.md` 和 `README.md`
- 保留 Architecture overview、dependency map、testing notes 等 CLAUDE.md 独有内容
- 修正 Architecture overview 中过时引用：`FetchContext` → `PaperFetchState`/`YearFetchSession`、`--recheck-citations` → `--fetch-mode`、`publications.json` 缓存 → profile JSON
- 关联：修正前次遗漏的 stale 模块引用和测试计数（fetch_context.py、121/107 → 127）

---

## 2026-05-12: 文档修正 — 过期模块引用和测试计数

- CLAUDE.md、`_work_notes.zh.md` 中 `fetch_context.py` → `fetch_session.py`（FetchContext 已删除）
- 各文档项目结构中补充缺失模块：`fetch_session.py`、`page_visit.py`、`citation_models.py`、`output_state.py`、`pub_info.py`
- 测试计数统一为 127（之前 CLAUDE.md/README 为 107，`_work_notes.zh.md` 为 121）
- CLAUDE.md 移除过时的 `refactor/modularize` 分支引用，更新依赖映射

---

## 2026-05-09: 状态封装 + cache 清除 + scholar_changed 标志

### 字段私有化

- `PaperFetchState` 10 个字段 → `_field` 前缀，只读 property，通过 `from_dict()`/`restore_*()` 修改
- `BatchFetchSession` 8 个字段 → `_field` 前缀，只读 property，内部 `_run_once()` 修改

### 彻底移除 cache 依赖

- `_build_entry` 不再从 cache 合并 diagnostics
- 每次运行结束时 `shutil.rmtree(cache_dir)` 删除所有缓存
- 同次运行 retry 仍读取 cache（`save_progress` 写入的进度）

### scholar_changed 标志

- `PaperFetchState._scholar_changed`：引用总数变化时在 `cache_status` 中设置
- `need_fetch()`：`scholar_changed` 为 True 或 `is_complete()` 为 False 时返回 True
- 抓取成功后 `clear_scholar_changed()`，中断则保留到下次运行

### fix_diag_from_year_record.py

- 使用 `PaperFetchState.from_dict()` → `restore_*()` → `to_dict()` round-trip 修复输出文件
- Year 模式从 `year_records` 累加重建 `year_fetch_diagnostics`
- Direct 模式从 citations 计数重建 `direct_fetch_diagnostics`

### 诊断格式统一

- `year_fetch_diagnostics` 和 `direct_fetch_diagnostics` 本身就是 summary，不再有 `summary` 子键
- `_normalize_year_summary_dict` 兼容三种格式（新扁平格式 + 旧嵌套 summary + records 推导）

---

## 2026-05-09: 彻底移除 cache 文件回退读取

### _citation_status 不再读取 cache 文件

`_citation_status` 和 `cache_status` 在输出文件状态缺失时直接返回 `'missing'`，不再回退读取 per-paper cache 文件。cache 文件仅用于同次运行内的中断恢复。

### force mode 简化

只需清除输出文件的 `_fetch_state` 即可使论文被重新抓取，无需删除 cache 文件。

### 兼容性修复

- `_normalize_direct_diagnostics` 处理旧格式（无 `summary` 子键的扁平 diagnostics），同时识别旧字段名 `reported_total` / `yielded_total`
- `PaperFetchState` 新增 `num_citations_seen` 字段用于旧 cache 的向后兼容
- `is_complete` 推断策略时优先检查已有的 diagnostics，而非仅依赖引用数

---

## 2026-05-08: YearFetchSession 替换 FetchContext + force mode 修复

### YearFetchSession 替换 FetchContext

`crawler/fetch_session.py` 中的 `YearFetchSession` 合并了 `FetchContext` 的全部字段：
- `completed_year_segments`、`partial_year_start`、`dedup_count`
- `probed_year_counts`、`probed_year_count_complete`、`cached_year_counts`、`year_fetch_diagnostics`
- 新增 `baseline: PaperFetchState`（跨运行状态引用）
- `FetchContext` 文件已删除

### 恢复页面访问前随机等待

`patched_get_page` 中 `PageVisit.fetch()` 调用前增加 `rand_delay()`，恢复 45-90s 随机等待。

### force mode 语义变更

`--fetch-mode force` 不再删除 per-paper cache 文件，改为从 output file 的 `_fetch_state` 中清除对应论文的状态。

---

## 2026-05-08: PageVisit + BatchFetchSession 页面访问层重构

### PageVisit — 统一页面访问错误恢复

新增 `crawler/page_visit.py`：

- **`PageVisit(ctx)`**：封装单次 Scholar 页面访问
  - `fetch(fn, url, label)` — 调用 `fn()` 获取页面，自动处理所有错误
  - 三层恢复：captcha 解决 → 自动重试 → proxy 切换
  - KeyboardInterrupt 始终立即传播

- 集成到 `patched_get_page`：所有 scholarly HTTP 请求均通过 `PageVisit`，替换了之前的 `unified_sleep` hack
- 移除 `scholarly_session.py` 中不再需要的 `MaxTriesExceededException` import

### 错误处理层级

```
_run_main_loop        ← 论文级：MAX_RETRIES + 最终 proxy switch 兜底
  └─ YearFetchSession  ← 年份级：年份顺序 + 续传
       └─ BatchFetchSession ← 翻页级：分页迭代 + max_retries
            └─ PageVisit     ← 页面级：captcha / proxy switch / 重试
```

---

## 2026-05-08: BatchFetchSession 引入 + Direct fetch 迁移

### 新增 `crawler/fetch_session.py`

三个 dataclass：

- **`BatchFetchSession`**：封装单次分页抓取。给定 URL，`run(fetcher, ...)` 自动翻页、提取 citation、dedup、track 翻页位置。支持 `start_index` 续传和 `iterator` 注入（供测试 mock）
- **`DirectFetchSession`**：包装一个 `BatchFetchSession` + `PaperFetchState` baseline
- **`YearFetchSession`**：管理年份列表 + per-year `BatchFetchSession`，只需维护 `pending_years` + `current_batch`

### Direct fetch 迁移

`fetch_citations_with_progress` 中 direct 路径已改用 `BatchFetchSession`：
- 创建 `BatchFetchSession(url=...)`
- 通过 `fetcher._iter_direct_citedby()` 获取 iterator（保留测试 mock 路径）
- `batch.run()` 处理翻页 / dedup / 进度回调
- `save_progress` 接受 `batch` 参数，从 batch 同步 citations 和 dedup

---

## 2026-05-08: save_progress 参数清理 + 统一 is_data_complete

### save_progress 变量整理

- 参数 `complete` → `fetch_finished`（抓取循环是否结束，不等同于数据完整）
- 移除 `effective_complete` / `materialize_replace` 中间变量：`materialized_citations(fetch_finished)` 直接决策新旧引用替换/合并
- 移除 `data_complete` 中间变量：改为调用 `_compute_data_complete()` 函数
- 移除 `cache_data['complete']` key（与 `complete_fetch_attempt` 冗余）

### 统一 is_data_complete

`crawler/citation_cache.py` 新增 `is_data_complete(strategy, summary)`：
- Year 模式: `seen_total >= histogram_total`
- Direct 模式: `seen_total >= scholar_total`
- `PaperFetchState.is_complete()` 和 `_compute_data_complete` 均委托此函数

### 命名修正

- `build_materialized_year_fetch_diagnostics` → `build_year_records`（函数实际构建的是 per-year 记录）
- `year_fetch_diagnostics_to_save` → `year_records_to_save`
- `_synced_save_progress(complete)` → `_synced_save_progress(fetch_finished)`

### _build_entry 修复

`fetch_complete` 改为使用 `PaperFetchState.is_complete()`（数据完整性），而非 `complete_fetch_attempt`（过程是否跑完）。

---

## 2026-05-08: 修复 profile 中 author/url 为 N/A

### 根因

Scholarly 的 `_citation_pub()`（用于解析作者页论文列表）不提取 `bib['author']` 和 `pub_url`。它只读取 `gs_gray[1]`（venue），丢弃了 `gs_gray[0]`（authors）；也从标题链接提取 `author_pub_id` 但不保存 href 为 `pub_url`。这些字段只在后续对每篇论文单独调用 `fill()` 时才会被设置，但 `scholarly.fill(author, sections=['publications'])` 并不会对每篇论文调用 `fill()`。

### 修复：monkey-patch `_citation_pub`

在 `crawler/scholarly_session.py` 的 `patch_scholarly()` 中新增 monkey-patch：

- 从 `gs_gray[0]` 提取作者名字符串，写入 `bib['author']`
- 从标题链接 `gsc_a_at` 的 `href` 提取 `pub_url`，补全为绝对 URL（`https://scholar.google.com/...`）

此 patch 不需要额外 HTTP 请求——数据在原有 HTML 解析中已存在，只是被 scholarly 丢弃了。

---

## 2026-05-08: AuthorProfile dataclass + 移除只写缓存

### AuthorProfile dataclass 封装

`crawler/profile_io.py` 新增 `AuthorProfile` dataclass，封装 profile 阶段的所有数据：

- **字段**：`author_info`、`publications`、`fetch_time`、`change_history`
- **计算属性**：`total_publications`、`total_citations`、`citation_count_summary`
- **方法**：`from_dict()` / `to_dict()`（序列化）、`load(path)` / `save_json(path)`（文件 I/O）、`append_history(prev)`（变更追踪）
- Profile JSON 格式不变，`from_dict` 通过 `PubInfo.from_dict().to_dict()` 规范化旧数据

### 移除只写缓存文件

- `basics_cache`（`scholar_cache/.../basics.json`）：只写不读（basics 每次从网络拉取），移除 `save_basics_cache()` / `load_basics_cache()` 方法及文件路径
- `pubs_cache`（`scholar_cache/.../publications.json`）：前次提交已移除读取，本次移除 `save_pubs_cache()` / `load_pubs_cache()` 方法及文件路径
- `AuthorProfileFetcher` 不再创建 `cache_dir`

### profile_io 接口简化

- `save_profile_xlsx()` 接受 `AuthorProfile` 实例替代 5 个独立参数
- `build_profile_payload()`、`save_profile_json()` 逻辑移入 `AuthorProfile`；旧函数保留以兼容 `scholar_citation.py` 的 import
- `scholar_citation.py` 中移除未使用的 profile_io import

---

## 2026-05-08: 移除 --force-refresh-pubs + pubs_cache 依赖清理 + profile Excel 增强

### 移除 --force-refresh-pubs 参数

与 `--fetch-mode force` 功能重叠。`author_fetcher.py` 中已有自动检测引用总数变化并强制刷新 publications 的逻辑，不需要单独的手动参数。涉及 5 个文件。

### 输出文件作为唯一跨运行状态源

Profile 和 citation 阶段统一：跨运行状态只从输出文件（profile JSON / citations JSON）读取，不再依赖中间 cache 文件。

- **`fetch_publications()`**：不再读取 `pubs_cache`，改为接受 `prev_publications` 参数，从 `prev_profile`（来自 profile JSON）获取上一轮的 publications 列表
- **Citation 阶段 `url_map`**：从 profile JSON 的 `publications` 构建，不再读取 `pubs_cache`
- **移除死代码**：`scholar_citation.py` 中 `self.pubs_cache` 属性、`self._pubs_data` 属性、`_save_output` 中的 `pubs_cache` 回退读取

### PubInfo 健壮性增强

- `from_scholarly()` 处理 `bib['author']` 为 list 的情况（scholarly 的 `_citation_pub()` 不设置 author，`_scholar_pub()` 中 `_get_authorlist` 返回 list；只有后续 `fill()` 或 bibtex update 才转为 string）。list 以 `'; '.join()` 转为 string
- venue 增加 `bib.get('journal')` fallback

### Profile Excel 增强

- Publications 表新增 **Authors** 列（E 列），Link 移至 G 列
- 空值显示修复：`pub.get('url') or 'N/A'` 替代 `pub.get('url', 'N/A')`，对 `PubInfo` 产生的空字符串正确显示为 'N/A'。Year、Venue、Authors 同理

---

## 2026-05-07: 命名统一 + 运行时状态封装

### 命名标准化

- `strategy` = `year` / `direct`（`fetch_policy['strategy']`、`fetch_strategy`）
- `mode` = `rough` / `normal` / `force`（`self.fetch_mode`、`--fetch-mode`）
- `fetch_policy['mode']` → `fetch_policy['strategy']`（9 个文件，44 处修改）

### ResumeState 运行时 dataclass

`crawler/citation_models.py` 新增：
- **`ResumeState`**：统一 direct 和 year 模式的断点续传位置（`next_index`, `source_scholar_total`, `citedby_url`），提供 `page_start()` / `in_page_skip()` / `request_url()` / `is_valid()` + `from_dict`/`to_dict`
- **`FetchPolicy`**：替代 `resolve_citation_fetch_policy` 返回的 dict，`strategy` / `pub_year` / `reason` + dict-compat

`citation_fetch.py` 中 5 个 resume 函数改为委托 `ResumeState`（消除重复 dict 构造/校验逻辑，22 行新增 65 行删除）；`citation_strategy.py` 返回 `FetchPolicy` 对象。

### 控制流改为 mode 驱动

`fetch_by_year` 中的 `ctx.probed_year_count_complete`、`bool(probed_year_counts)` 等值存在性检查改为 `is_year` mode 标志驱动。

### Direct mode histogram=0

Direct mode 无 probe，year_records 中 `histogram_count=0`，summary 中 `histogram_total=0`。

---

## 2026-05-07: year_records 独立 + citation_models + PubInfo

### year_records 从 year_fetch_diagnostics 分离

`year_fetch_diagnostics` 现在只包含 8-field summary，per-year 条目移至新顶层字段 `year_records`（按年份排序的列表）。`_FETCH_STATE_KEYS` 新增 `year_records`，共 10 个字段。

### PubInfo dataclass

`crawler/pub_info.py`：封装 `pub` 的 8 个字段（`no`, `title`, `year`, `venue`, `authors`, `num_citations`, `url`, `citedby_url`）。`from_scholarly()` 替代 `bib.get('xxx', 'N/A')` 默认值。

### citation_models.py

`crawler/citation_models.py`：`Citation`、`YearRecord`、`YearDiagnostics`、`DirectDiagnostics` 四个 dataclass，各带 `from_dict`/`to_dict` 和 dict-compat 方法。当前在 I/O 边界使用，内部逐步迁移。

### 移除 profile 阶段间延时 + 论文间延时

Profile Phase 1→2 延时和论文间延时已移除（每页访问前已有 45-90s 等待）。

---

## 2026-05-07: PaperFetchState dataclass 重构

引入 `PaperFetchState` dataclass（`crawler/output_state.py`），封装 `_fetch_state` 的持久化字段，替代裸 dict。

**新增方法**：
- `from_dict()` / `to_dict()` — 序列化，入出两端均规范化 diagnostics 字段
- `is_complete()` — year/direct 统一 completeness 判断
- `completeness_diag()` — 替代 `_format_completeness_diag` 逻辑

**规范化**：
- `direct_fetch_diagnostics.summary` 严格限制为 5 字段
- `year_fetch_diagnostics` 按年份排序，per-year 条目限制为规范 keys，剔除 `underfetched`/`mode`/`underfetch_gap`

**多态兼容**：`_citation_status()`、`cache_status()`、retry logic 同时接受 PaperFetchState 和 dict。

**修复**：`_build_entry` 合并 cache 时补充 `fetched_at`、`complete_fetch_attempt` 字段（之前漏合并导致输出文件保留旧值）。

---

## 2026-05-07: 修复 year 模式 seen_total 错误包含 unyeared

**Bug**：`build_citation_count_summary` 中 year 模式的 `seen_total = diag_seen + cached_unyeared_count`，导致 year summary 的 `seen_total` 多计了 unyeared 引用。

**根因**：Year fetch 中 unyeared 引用在 `_resolve_refresh_strategy` 阶段被故意丢弃（`drop_cached_unyeared`），因为它们无法归入年份桶参与 histogram 对比。因此 `seen_total` 应该只反映有年份的引用（`= diag_seen`，各年份 seen 的累加值），不应该加回 unyeared。

**修复**：year 模式 `seen_total = diag_seen`；direct 模式 `seen_total = cached_total + dedup_count`（不丢弃 unyeared）。

---

## 2026-05-07: 修复 direct_fetch_diagnostics.summary 被 year 字段污染

`fix_output_fetch_state.py` 的 direct summary 同步只更新个别字段，不清理旧 buggy 运行残留的 year 模式字段（`histogram_total`、`cached_year_total`、`cached_unyeared_count`、`scholar_unyeared_count`），且 `seen_total` 只在 `None` 时才修正。

修复：direct summary 完全重建为 5 字段（`scholar_total`、`cached_total`、`seen_total`、`dedup_count`、`termination_reason`），`seen_total` 强制重算为 `cached_total + dedup_count`。

### 中文文档重命名

`update_history.md` → `_update_history.zh.md`、`WORK_NOTES.md` → `_work_notes.zh.md`、`user.md` → `_user.zh.md`。更新了 CLAUDE.md、README.md、approach.md 和 memory 中所有引用。

---

## 2026-05-06: 重试状态修复、冗余字段清理、诊断日志完善

### 重试时缓存状态丢失

重试时 `retry_strategy_cached = latest_output_state if latest_output_state else latest_cache`，当 paper 在 output state 中时直接使用 output state（不含 `citations` 数组），忽略了缓存文件中 `save_progress` 保存的最新引用。

修复：`retry_strategy_cached = latest_cache if latest_cache else latest_output_state`，缓存文件优先。

### direct_resume_state 页对齐

`direct_resume_state.next_index` 保存精确位置（如 7），重试时跳过前 7 个 item，但跳过的 item 可能未在 `old_citations` 中，导致丢失。

修复：`_build_direct_resume_state` 将 `next_index` 对齐到页边界（`_page_aligned_start`），重试从页开头重新抓取，已保存的引用通过 `old_citations` 去重。

### 论文间延时移除

每次页面访问前已有 45-90s 延时，论文之间的额外延时是冗余的。移除 `time.sleep(d)`，只保留状态日志。

### resolve_citation_status_from_state 无 diagnostics 时的 fallback

当 `direct_fetch_diagnostics.summary` 或 `year_fetch_diagnostics.summary` 缺失时，原逻辑直接返回 `partial`，未使用已有的 `current` / `num_seen` 进行判断。

修复：当 diagnostics summary 不可用时，fallback 到 `num_seen >= current`（当前 scholar total）判断 complete/partial。

### fix_output_fetch_state.py 多项修复

- `fetch_strategy` 强制按阈值重新评估（已有 `year` 但引用数 < 50 的论文纠正为 `direct`）
- `direct_fetch_diagnostics.summary` 为 `None` 时触发 repair
- `num_citations_seen` 不再从 `new_summary`（per-year 派生）设置，直接模式从 `direct_fetch_diagnostics.summary.seen_total` 取实际记录值

### Direct fetch 日志缩进统一

Direct fetch 的 item 行从 8 空格改为 10 空格，与 year fetch 一致。

### `num_citations_seen` 和 `cached_year_counts` 从 output 中移除

两个字段均可从 diagnostics summaries 推导，不需要作为顶层字段持久化：

- `num_citations_seen`：直接模式从 `direct_fetch_diagnostics.summary.seen_total`，年份模式从 `year_fetch_diagnostics.summary.seen_total`
- `cached_year_counts`：从 `year_fetch_diagnostics` 每个年份条目的 `cached_total` 累加

修改：`_FETCH_STATE_KEYS` 移除这两个字段；`derive_citation_cache_state` / `_resolve_refresh_strategy` / `_format_completeness_diag` 改为从 diagnostics summary 读取。

### `underfetched`/`underfetch_gap` 清理

这些字段已在日志中临时计算（不持久化），`fix_output_fetch_state.py` 清理了旧数据中残留的字段。

---

## 2026-05-06: 每篇论文抓取判定诊断日志

新增 `_format_completeness_diag(st, cached)`，在每篇论文标题下打印诊断信息：

```
  direct: seen_total=49 ≥ scholar_total=49 → complete
  year: seen_total=1343 ≥ histogram_total=1340 → complete
  direct: seen_total=45 < scholar_total=46 → partial
```

---

## 2026-05-05: Direct/Year summary 分离 + seen_total 使用记录值

### Direct/Year 两种 summary 分离

`save_progress` 中将 `count_summary`（per-year 派生值）直接覆盖到 `direct_fetch_diagnostics.summary`，导致 direct 模式的 summary 包含了 per-year 派生字段。

修复：
- `save_progress`：direct summary 只同步 `scholar_total`、`cached_total`、`seen_total`、`dedup_count` 五个顶层计数器
- `_build_direct_fetch_diagnostics`：新增 `seen_total` 参数，不再内部计算 `cached_total + dedup_count`
- `build_citation_count_summary`：year summary 的 `seen_total` 加上 `cached_unyeared_count`
- `_build_entry`：不再覆盖 summary 中的 `seen_total` 和 `dedup_count`

### seen_total 使用实际记录值

`seen_total` 应该是每次获取过程中记录的值，不应该通过 `cached + dedup` 进行计算。`_build_direct_fetch_diagnostics` 改为接受外部传入的 `seen_total` 参数。

### fix_output_fetch_state.py 多轮增强

- 合成条件从 `not yfd` 改为 `not has_year_entries`，处理只有 `summary` 键的 yfd
- 合成不再要求 `probed_year_counts` 存在
- `fetch_strategy` 基于引用数阈值（50）推断，不因合成 per-year 条目而误判
- 空 `_fetch_state` / `None` 正确初始化
- direct summary 同步时使用顶层计数器

---

## 2026-05-05: 日志优化 + 迁移脚本增强

### 日志去重

删除了三处重复/冗余的日志输出：`Year histogram summary`、`Prior run diagnostics`、`Direction: oldest→newest`。

### `num_citations_seen` 缺失时的 fallback

旧版缓存可能没有 `num_citations_seen` 字段，导致完整性检查失效。修复：从 `year_fetch_diagnostics` 中各年份 `seen_total` 求和来推导。

### captcha URL 显示错误

自动翻页时被阻止的页面 URL 显示为迭代器初始 URL 而非实际被阻止的页面。修复：根据实际位置重新构造 URL。

### `save_output` 中 `_fetch_state` 不更新

`_build_entry` 对已有 output state 的论文直接复制旧的 `year_fetch_diagnostics`，从不更新。修复：从当前运行的 cache 文件合并最新值。

### `citation_count_summary` → `summary` 嵌套

`citation_count_summary` 重命名为 `summary`，嵌套在 `year_fetch_diagnostics` (year mode) 或 `direct_fetch_diagnostics` (direct mode) 下。

### 字段重命名与清理

- `scholar_total` → `histogram_count`（year_fetch_diagnostics 逐年条目）
- `reported_total` → `scholar_total`、`yielded_total` → `cached_total`（direct_fetch_diagnostics）
- `unyeared_count` → `scholar_unyeared_count`
- 移除 `underfetched`/`underfetch_gap`/`completed_years_in_current_run`
- 移除 `probed_year_total`、`probe_complete`
- 新增 `fetch_strategy: "year"` / `"direct"` 顶层标记

---

## 2026-05-05: summary 嵌套 & cites_id fallback 修复

- `citation_count_summary` 重命名为 `summary`，作为 `year_fetch_diagnostics` 或 `direct_fetch_diagnostics` 的嵌套子字段
- **cites_id fallback**: scholarly 的 `_scholar_pub()` 不设置 `cites_id`，导致 73.7% 引用缺 `cites_id`。新增三级回退：`cites_id` → `citedby_url` 解析 → `url_scholarbib` 解析（提取 `cid`）

---

## 2026-05-05: complete/partial 判断简化 & 字段清理

- **complete/partial**: year 模式 `histogram_total <= seen_total` → complete；direct 模式 `scholar_total <= seen_total` → complete
- **年度重新获取**: 仅当本 run 已完成 或 `seen >= histogram_count` 时跳过
- **fetch policy 简化**: 仅用引用数阈值（<50 → direct，≥50 → year）
- **`_fetch_state` 字段清理**: 移除 `num_citations_cached`、`dedup_count`、`complete`、`completed_years`、`probed_year_counts` 等
- **direct→year 过渡**: 从缓存引用合成 year_fetch_diagnostics
- **direct_resume_state**: 仅 cache 文件保留（within-run resume），输出文件不保存

---

## 2026-05-03: 输出状态边界情况修复 + skip/limit 时状态更新

### 彻底修复输出状态读取的边界情况

1. `extract_fetch_state` 不补充缺失字段 → 导致 `actual_cached=0`、`num_seen=None`
2. `promoted_scholar_total` 缺失时回退到 0 → 兜底逻辑永远不触发
3. `load_output_fetch_state` 缺少 `AttributeError` 捕获

### skip/limit 时 _fetch_state 更新

profile 重新获取后，被 skip/limit 排除的论文 `num_citations_on_scholar` 保持旧值。修复：`cache_status` 强制用当前 profile 计数覆盖。

---

## 2026-05-01: 输出文件成为跨运行状态来源

per-paper 缓存文件同时承担"单次运行中断恢复"和"跨运行策略决策"两个职责。解耦：
- 缓存只做 within-run resume
- 输出文件 (`_fetch_state` 字段) 做跨运行决策

新增模块：`crawler/output_state.py`；新增测试：`tests/test_output_state.py`。

---

## 2026-05-01: 修复 cites_id 全为 null

scholarly 的 `_scholar_pub()` 不设置 `cites_id`。修复：fallback 到 `citedby_url` 中 `cites=` 参数提取。

---

## 2026-04-30: Year-based fetch 每页丢失 1 条

两种分页机制冲突：scholarly 的 `_SearchScholarIterator` 自动分页与我们的 `while True` 手动分页。修复：满页时也 break，分页控制权完全交还给 while True 循环。

---

## 2026-04-29: Cookie 跨次持久化 + profile 请求减少

- `output/curl.txt` 自动保存/加载 Scholar session cookie
- `fetch_publications` 复用 `fetch_basics` 的 author stub，每次 profile 抓取从 3 次 HTTP 请求减为 2 次
- Selective refresh 原因日志

---

## 2026-04-28: Direct fetch Progress saved 去重 + Cookie 持久化

- `_WrappedDirectIterator` 新增 `_items_in_current_page`，只在完整页打印 "Progress saved"
- 日志措辞改为 `Progress saved: N fetched this paper, M new across run`
- `author_fetcher.py` citation changes 从最多 5 条改为全量展示

---

## 2026-04-18: 分层缩进 year-based fetch 日志

三层体系：6 格（年份边界）、8 格（页面级）、10 格（页面内引用）。

---

## 2026-04-17: 修复 year_fetch_diagnostics dedup_count 跨次运行丢失

内外两个 FetchContext 不同步：`_fetch_by_year` 创建独立内部 ctx，`save_progress` 闭包读外部 ctx。修复：wrapper 同步内部 ctx → fetcher 属性 → save_progress。

---

## 2026-04-16: Histogram 权威化 + 策略简化

- `probe_complete` 降级、selective_refresh 大幅简化
- resume/update 合并为统一 fetch 策略
- dedup 不再跨 run 累计
- 移除 live citation count promotion 机制
- 移除 Refresh check 和 escalation 逻辑
- `drop_cached_unyeared` 恢复为所有 year-based fetch 都 drop

---

## 2026-04-15: 模块化重构完成 + fetch-mode

- 五轮模块化重构：拆出 12 个 crawler/ 模块 + 9 个测试文件
- `--fetch-mode {rough,normal,force}` 替换 `--recheck-citations`
- 共享 HTTP/2 session（profile + citation 阶段）
- 多项日志修复

---

## 2026-04-14: 模块化重构（第一至五轮）

- 第一轮：拆出 `scholar_common.py`、`scholar_profile_io.py`
- 第二轮：新建 `citation/cache.py`、`citation/strategy.py`
- 第三轮：统一到 `crawler/` 包 + 测试目录拆分（105 tests）
- 第四轮：拆出 citation_identity、citation_io、author_fetcher、cli
- 第五轮：拆出 interactive、scholarly_session、fetch_context、citation_fetch

---

## 2026-04-13: Citation 状态判定改为计数与 diagnostics 优先

`complete` 不再主导运行时流程。direct `seen >= scholar_total` 与 year histogram/diagnostics 满足关系决定 complete。

---

## 2026-04-11: post-fetch retry 限制 + direct early-stop 修复

- post-fetch 异常重试只允许一次
- direct fetch early-stop 计数改用 `fresh_citations` 而非 materialized cache

---

## 2026-04-10: 修复 year-based retry 死循环 + citation completeness 收口

- `_fetch_by_year` 外层的 reconciliation retry 死循环修复
- `_citation_status` 对 promoted totals 的误判修复

---

## 2026-04-08: 输出一致性修复 + cites_id 升级

- JSON / Excel 导出不一致修复
- cites_id 三级 fallback（title+venue/authors）
- 新增运行日志镜像

---

## 2026-04-04: citation accounting 语义切换为 histogram-authoritative

year-based 路径不再以 `cached_total == scholar_total` 作为完成条件，而是以 `cached_year_counts == probed_year_counts` 为准。

---

## 2026-04-01: year probe histogram 完整性语义 + 保守回退

probe 校验 full histogram DOM 的年度计数总和是否等于 Scholar 总数；不一致时回退为保守年份范围。

---

## 2026-03-31: 引用去重键升级 + 持久化年份分布

- 去重键从 title-only → `title + venue` / `title + authors` / `title`
- per-paper cache 新增 `probed_year_counts`、`cached_year_counts`

---

## 2026-03-28: Interactive 模式等待缩短实验参数化

`--accelerate SCALE` 参数，统一作用于所有 deliberate waits。

---

## 2026-03-26: CLI 语义清理

`--force-refresh-citations` → `--recheck-citations`；删除 `--hard`。

---

## 2026-03-25: 原地重试机制重构 + citation year probe 主数据源修正

probe 完整图 DOM 数据源修正：`#gs_md_hist .gs_hist_g_a` 节点。

---

## 2026-03-24: SSH/tmux 粘贴 cURL 卡死修复 + probing year 修复

ICANON-only 方案解决 SSH/tmux 下粘贴 cURL 卡死问题。

---

## 2026-03-23: `--hard` 参数 + `--help` 完善

---

## 2026-03-21: 交互式 captcha + cookie 跨阶段传递

- `--interactive-captcha` 模式
- profile → citation 过渡时复制 cookies

---

## 2026-03-20: httpx 降级至 0.27.2 + 浏览器请求还原 + 强制长休息

- httpx 0.27.2 修复 session 刷新
- 浏览器 headers 完整模拟
- 每 8-12 页强制 3-6 分钟长休息

---

## 2026-03-14: `as_sdt` 参数修正 + 随机化抓取顺序

`as_sdt=0,5` 替代 scholarly 默认的 `,33` 地区过滤。

---

## 2026-03-12: 修复漏抓引用 + captcha 交互改进

---

## 2026-03-10: 引用去重 + early stop 优化 + force refresh 参数

---

## 2026-03-09: 年份级查询 + session 刷新 + 日志增强

---

## 2026-03-08: 心跳超时 + 统一等待 + 项目重组

- 心跳 10s 检测，80s 超时 → `os._exit(1)`
- 所有主动等待 30-60s
- 两个脚本合并为 `scholar_citation.py`

---

## 2026-03-07: 项目初始化

- 第一步：作者主页采集脚本
- 第二步：论文引用采集 + 限流对策

## 2026-10-14 — NO_PROXY 消毒改为共享引擎（跨仓库共享代码）

- `code/lib_env.py::sanitize_no_proxy()` 改为**策略包装**：引擎抽到 `code/shared/lib_env.py`
  的 `sanitize_entries()`，由本仓库（阶段 1）作为宿主，3.a.1 / 3.a.2 / 3.b 经各自的
  `code/_shared.py` 消费同一个引擎。
- 本仓库的行为**一字未改**：解括号 `[::1]`→`::1`（带端口的丢端口）、返回被改写的条目数、幂等。
- 为什么共享引擎而不是统一语义：3.x 那三份是「丢弃 + bool」，两套语义都被各自测试钉死，
  按「整体搬迁不改语义」只能共享引擎、把策略留给调用方。
- 新增 `tests/test_lib_env_shared.py`（3 例）：引擎同源、包装确实委托、行为回归。全套 266 例通过。

---

## 2026-10-14（第二轮）: 阶段 1 抓取器审计修复（5 项）

另一轮审计逐行核实出的 5 个问题，一次修完；全套测试 **266 → 273 例 OK**。

### ① 探针页数重复计数

- 根因：`probe_citation_start_year()` 在调 `nav._get_soup()` 前手动 `ctx.total_page_count += 1`，
  而 `nav._get_soup()` 内部走 `nav._get_page()` —— 已被 patch 成 `patched_get_page()`，
  那里**也**计数一次。于是每次探针算两页，8–12 页长休息与 10–20 页刷新大约提前一半触发。
- 改动：删掉探针里那一次手动计数，只保留 `patched_get_page()` 里的唯一一处。
- 测试：`tests/test_scholar_patch.py::test_probe_counts_one_page_through_real_get_soup`
  —— 用**真实** `scholarly.Navigator._get_soup` + 打桩底层 `_get_page`（不打桩 `_get_soup`），
  断言 `total_page_count`/`curl_page_count` 各只 +1。

### ② 抓取失败不再把 profile 覆盖成 0 篇

- 根因：`fetch_publications()` 失败时 `return []`，与"该作者确实没有论文"无法区分；`run()`
  随后无条件 `save_profile()`，于是引用总数变化（`force_refresh=True`）时的抓取失败会把
  已有 profile 写成 0 篇，且看不出是失败。
- 改动：失败返回 `None`；`run()` 里"失败 **且** 旧 profile 有 publications"时**拒绝覆盖**——
  不写 JSON/Excel，内存里保留旧列表，打印
  `Failed to fetch publications; keeping the previous N publication(s) from <path>` 后继续
  （Phase 2 用盘上的旧 profile）；旧 profile 本来就没有 publications 时行为不变（照旧保存）。
- 测试：`tests/test_profile.py::AuthorProfileFetchFailureTests`（2 例）：旧 profile 逐字节未改写 +
  告警；旧 profile 无 publications 时仍照常写入新 basics。

### ③ 换代理等待 24h 也受 `--accelerate` 缩放

- 根因：`interactive.wait_proxy_switch(max_hours=24)` 不受加速系数影响，`--accelerate` 只缩放了
  `rand_delay`/cooldown/长休息；非交互模式下一旦被拦，单篇论文最多阻塞 24h（多篇即数天）。
- 改动：`wait_proxy_switch(max_hours=24, scale=1.0)` 内部 `max_hours *= scale`（默认行为不变）；
  加速系数在**唯一一处**接线：`PaperCitationFetcher._wait_proxy_switch()` 传 `scale=self._delay_scale`。
  `page_visit.py` 与探针都通过 `ctx.wait_proxy_switch_fn()` 这个**无参**回调走到该包装方法，
  因此不需要（也不应该）改这两个调用点的签名。
- 测试：`tests/test_page_visit.py::test_wait_proxy_switch_shortens_with_accelerate_scale`
  （假时钟 + 打桩 `select.select`：24h×1.0 与 24h×0.1）；
  `tests/test_scholar_patch.py::test_wait_proxy_switch_scales_with_accelerate`（断言包装方法把
  `_delay_scale` 传下去）。

### ④ 被替换掉的 httpx client 现在会 close()

- 根因：`code/scholarly_session.py` 全文没有 `.close()`：初始替换 scholarly 原生 session、软刷新
  `patched_new_session()` 里"建一个立刻丢掉"、captcha `patched_handle_captcha2()` 重建
  都把旧 client 的连接池丢给 GC。
- 改动：新增局部 `_close_quietly()`（无 `close()`（测试替身）/重复 close 都不致命），在三处
  替换/丢弃前调用。**有意不关**的一个：scholarly `_get_page()` 的 403 重试分支把旧 session 存在
  局部变量里，继续用它发下一次请求——关掉它会变成"用已关闭的 client"（该 client 其实已被
  scholarly 自己的 `pm._new_session()` 关掉，这是 scholarly 的既有行为，不在本仓库修）。
  captcha 重建时同一个新 client 会同时占据 `_session1`/`_session2`，关闭旧的两个前后不会关到新的。
- 测试：`tests/test_scholar_patch.py::test_replaced_httpx_clients_are_closed`（初始替换 / 软刷新 /
  captcha 三条路径的旧 client 都收到 `close()`，新 client 未被关闭）。
- 已知偏差：`pm._session` 会因初始替换而被提前关闭；`scholarly` 的请求路径只读
  `nav._session1/_session2`（`pm._session` 仅被我们已替换掉的 webdriver 版 `_handle_captcha2`
  使用），且 httpx 重复 `close()` 幂等，所以无功能影响。

### ⑦ 干净安装 ImportError：requirements 声明 http2 extra

- 根因：`requirements.txt` 只写 `httpx==0.27.2`，而 `_make_http2_session()` 用
  `httpx.Client(http2=True)`；httpx 0.27.2 在缺 `h2` 时构造即抛 `ImportError`。本机 conda
  环境恰好装了 `h2`，所以一直没暴露。
- 改动：`httpx[http2]==0.27.2`（对外行为不变）。README 依赖行与说明、AGENTS.md 依赖节同步。
- 测试：`tests/test_scholar_patch.py::test_requirements_declare_the_httpx_http2_extra`（源码级守卫）。

### 文档漂移

- `AGENTS.md` 目录树里删掉不存在的 `environment.yml`；测试例数 266 → 273；
  「Interactive recovery」补一句"24h 窗口随 `--accelerate` 缩放"；依赖节改为 `httpx[http2]==0.27.2`。
- `README.md`：依赖行、http2 extra 说明、"Proxy Switch Wait" 条目同步。

## 2026-10-14: venue_full —— 从出版详情页取完整 venue（只加不覆盖）

### 问题

Google Scholar 的**引用列表页**（`scholar?cites=…`）给的 venue 是**截断**的（`Proceedings of the …`）：
本 profile 实测 **106/241** 条被截断。而对**我们自己 profile 里的论文**，Scholar 还有一张**详情页**
（`citations?view_op=view_citation&citation_for_view=<author>:<token>`），字段里给的是完整全称
（实测 `user=HF3-E9kAAAAJ`、`citation_for_view=HF3-E9kAAAAJ:xtoqd-5pKcoC`：HTTP 200、89 KB，
`Conference = Proceedings of the 2024 Conference of the North American Chapter of the Association for
Computational Linguistics: Human Language Technologies (Volume 1: Long Papers)`）。
在此之前 `code/` 里 `view_citation` / `citation_for_view` **0 命中** —— 这个页面从未抓过。

### 改动

- 新增 `code/venue_detail.py`：`citation_for_view` token 解析、详情页「标签-值配对」解析
  （DOM 配对，避开页面开头 `<style>` 里的 `.gsc_oci_value{…}` 误判）、按 token 的 JSON 缓存、
  以及逐条降级的编排函数。
- `code/pub_info.py`：`pub` 新增 3 个字段 `citation_for_view` / `venue_full` / `venue_full_source`
  （`to_dict()` 固定 schema；`from_dict()` 从存储字段或 `url` 恢复 token，旧 profile 无需重抓；
  `from_scholarly()` 优先采用 scholarly 的 `author_pub_id`）。**`venue` 一个字节都不改。**
- `code/author_fetcher.py`：新增 `fetch_venue_details()`，由 `run()` 在 publications 定稿后、
  写 profile 之前调用；抓取走既有 patched session（`nav._get_soup`），单条失败/无 venue 字段只计数、
  不写 `venue_full`、不中断抓取。
- 测试：新增 `tests/test_venue_detail.py`（37 例，hermetic、不联网）。全套 **273 → 310 例**，全绿。
- 文档：`AGENTS.md`（新增模块说明 / Phase 1 说明 / 依赖图 / 测试表 / 例数 20 模块 16 文件 310 例）、
  `README.md`（Features 条目、Output Files 表加缓存文件、新增「Full Venue Strings (`venue_full`)」节、
  项目结构、例数 256 → 310）。

### 为什么绝不覆盖 `venue`

`venue` 参与元数据身份键（`code/citation_identity.py` 的 `meta\t{title}\t{venue}`，下游 3.a 各阶段
同口径，K123/K125 同类教训）。改写它会让历史身份键漂移、把已经分析过的数据重新判成"不符"。
所以 `venue_full` 是**纯增量**字段，下游 3.a.1 以后可以优先采用它（本轮不改下游，已记入
`issues.md`）。

### 真实覆盖率实测（2026-10-14，`--author HF3-E9kAAAAJ`，`--accelerate 0.3`）

跑法：`python -m code.scholar_citation --author HF3-E9kAAAAJ --accelerate 0.3`（Phase 1 的
详情页抓取约 20 s/请求），Phase 1 未跑完即被 Scholar 限流，按用户要求停下（见下）。

| 口径 | 总数 | 拿到 `venue_full` | 抓取失败/未抓到 | 无 venue 标签 | 无 token |
|------|------|------------------|------------------|---------------|----------|
| 全部 publication | 241 | **81**（cache `status=ok`） | 160（未跑到，被限流中断） | 0 | 0 |
| 原 `venue` 被截断（含 `…`） | 106 | **43** | 63（未跑到） | 0 | 0 |

- 缓存 `output/venue_detail_cache.json`：81 条全部 `status=ok`；标签分布 `conference` 44 /
  `journal` 29 / `book` 8。**没有一个 `venue_full` 里还带截断号 `…`**。
- 限流经过：约 30 分钟、约 **75 个详情页**成功后（页计数 87）Scholar 开始返回
  `Cannot Fetch from Google Scholar.`（scholarly 耗尽重试）；`PageVisit` 的 3 分钟冷却
  （`--accelerate 0.3` 缩放后）也未能恢复。**没有出现 captcha**（`0 captcha solves`）。
  按用户「遇限流就停、不要硬闯」的要求停进程，未再重试。
- 因 Phase 1 未跑完，本轮**没有重写** `author_<ID>_profile.json`（仍是 2026-10-13 的版本）：
  结果只落在缓存里。缓存命中不发请求，所以下一次（限流解除后、用默认延迟）跑到 Phase 1 末尾时
  会直接复用这 81 条并继续抓剩下的，最后一次性写进 profile。
- 5 条前后对照样例（`venue` → `venue_full`）：

| no | venue（截断） | venue_full | 标签 |
|----|---------------|------------|------|
| 4 | Proceedings of the 2024 Conference of the North American Chapter of the …, 2024 | Proceedings of the 2024 Conference of the North American Chapter of the Association for Computational Linguistics: Human Language Technologies (Volume 1: Long Papers) | conference |
| 5 | Proceedings of the 2019 Conference of the North American Chapter of the …, 2019 | Proceedings of the 2019 Conference of the North American Chapter of the Association for Computational Linguistics: Human Language Technologies, Volume 1 (Long and Short Papers) | conference |
| 9 | Proceedings of the AAAI conference on artificial intelligence 35 (15), 13745 …, 2021 | Proceedings of the AAAI conference on artificial intelligence | journal |
| 13 | Proceedings of the 53rd Annual Meeting of the Association for Computational …, 2015 | Proceedings of the 53rd Annual Meeting of the Association for Computational Linguistics | journal |
| 23 | Proceedings of the 29th ACM international conference on information …, 2020 | Proceedings of the 29th ACM international conference on information & knowledge management | book |

  另注：`book` 标签下的两条（no.23 / no.27）实际都是会议论文集，说明"标签集合"这一判定是必要的
  —— Scholar 不保证按论文类型给标签。

## 2026-10-14: `cid` + `cite` —— 施引条目的五种引用格式与 BibTeX（`--cite-formats`）

### 问题

引文列表页对每条施引条目只给一行缩写：作者是 `H Wang, F Zhang` 这样的首字母串，venue 被截断成
`Proceedings of the 2025 …`。上一轮已经从**出版详情页**解决了我们**自己论文**的 venue 截断
（`venue_full`），但**施引条目**（全库数千条）的缩写作者与截断 venue 一直没有来源。

### 改动

1. **`cid`（纯解析，无条件落盘）**：每条结果行都有 Scholar 的 `data-cid`，scholarly 把它放在
   `url_scholarbib` 里。`code/citation_identity.py` 新增公开函数 `extract_cid_from_scholarbib()`，
   并让 `extract_citation_info()` 多输出一个 `'cid'` 字段。**不发任何请求**，也不改动
   `title`/`authors`/`venue`/`year`/`url`/`cites_id`。
2. **`code/cite_format.py`（新模块）**：按 `cid` 请求 Cite 对话框
   （`/scholar?q=info:<cid>:scholar.google.com/&output=cite&scirp=0&hl=en`，一次拿全部五种格式），
   DOM 配对解析 MLA/APA/Chicago/Harvard/Vancouver，并从同一份响应里取**带签名**的 `scholar.bib`
   链接再取一次，解析成结构化 BibTeX（`author` 是 ` and ` 分隔的全名列表）。
   结果写进**新增**字段 `citation['cite']`；`citation['cid']` 之外一个旧字段都不动。
3. **`--cite-formats`（默认关闭）**：`cli.py` 新开关 → `PaperCitationFetcher(cite_formats=…)` →
   `_save_output()` 在写 JSON 前对最终 citations 跑一次富化。默认关闭是因为成本：**每个施引条目
   1 次请求**（BibTeX 再 1 次），全库数千次，与 venue 详情页的 241 量级不是一个成本级。
4. **缓存/续跑**：`output/cite_format_cache.json`（key = `cid`），原子写、命中不发请求、
   **失败不写缓存**；Cite 页成功而 BibTeX 失败时保留签名 URL，下轮只重试那一次请求。
5. **抓取礼仪**：沿用既有 patched session（`nav._get_soup()` / `nav._get_page()`），
   延迟 / `--accelerate` / captcha / 代理等待照旧；**没有**新增强制长休息（与 venue 详情页同一性质，
   产品决策记在 `issues.md` 第六节）。

### 为什么不覆盖 `venue` / 不用 `cid` 参与身份

`venue` 参与元数据身份键，`cites_id` 参与引用身份键（`citation_identity_keys()` /
`citation_output_identity_keys()`），改写或新增参与键都会让历史身份漂移（K123/K125 的同一条教训）。
所以 `cid` 与 `cite` 都是**纯增量**，去重语义与下游身份校验完全不变。

### 测试与文档

- 新增 `tests/test_cite_format.py`（71 例）：`cid` 持久化与旧数据兼容、五格式 DOM 配对
  （`<style>` 干扰 / 缺字段 / `&quot;`、`&amp;` 实体 / `<i>` 拼接间距）、BibTeX 结构化解析、
  缓存命中不重复请求、失败不写缓存、`venue`/`cites_id`/`authors` 逐字节不变、`--cite-formats` 接线。
  全套 310 → **383** 例，全绿。
- `AGENTS.md`（模块说明 / 依赖图 / Phase 2 / 测试表 / 例数）、`README.md`（Features、CLI、
  Output Files、新章节 Citation Formats & BibTeX、Project Structure）、`issues.md` 第六节同步。
- 小样本真实实测另行记录在本文件与 `_work_notes.zh.md`（2026-10-14 第二次追加）。

## 2026-10-14: 新增抓取路径的成本控制 —— 1/10 延迟 + 「已经完整就不抓」

### 问题

`venue_full`（出版详情页）与 `cite`（Cite 对话框）两条新路径都按「每请求 45–90 s」的通用延迟跑：
241 篇 publication 光等待就是数小时；而其中相当一部分记录的 venue 本来就已经完整，抓来的详情页
只能补卷期/页码。两条路径都是**一次性、非分页**的页面，与引用列表页的节奏需求并不相同。

### 改动

1. **1/10 延迟，只有一处定义**：`code/config.py` 的 `DETAIL_PAGE_DELAY_FACTOR = 0.1`；
   `code/scholarly_session.py` 的 `detail_path_delay_factor(url)` 是唯一判据（详情页 / Cite 对话框 /
   签名 BibTeX 下载 → 0.1，其余 → 1.0）；`patched_get_page()` 里
   `rand_delay(ctx.delay_scale * detail_path_delay_factor(request_url))` 是唯一计算点 ——
   **有效延迟 4.5–9 s/请求**，`--accelerate` 仍在其上相乘。引用列表页分页与 profile 页保持 45–90 s
   与强制长休息；两条新路径不得自带延迟（源码级守卫）。
2. **`code/venue_quality.py`（新模块）**：「这条记录还值得再抓吗」的唯一判据 ——
   `is_complete_venue()`（非空 + 无 `…`/`...` + 非预印本源）、`is_preprint_venue()`
   （arXiv/Preprint/bioRxiv/medRxiv/SSRN/TechRxiv/Research Square/preprints.org + 缺失值）、
   `has_full_author_names()`（given name ≥ 3 字母，排除 `van`/`von`/`de` 这类介词）。
3. **pub 级跳过**：`venue` 已完整 → 不请求、不写缓存、`venue` 逐字节不变。
4. **条目级跳过**：venue 已完整**并且** authors 已是全名 → 不请求、不写缓存；**已缓存的对话框仍复用**
   （避免重跑把上一轮付过钱的 `cite` 抹掉）。两个 stats 都新增 `skipped_complete` 并打进汇总行。
5. **风险与限流实测**：这套 1/10 延迟按量限流下会**更早**被拦（实测 3 分钟冷却 ✗、约 15 分钟 ✗），
   可用性依赖缓存 + 续跑 + 被拦即停。诚实代价已写进 `README.md` / `AGENTS.md` / `issues.md`
   （第七节，含冷却实测时间线）。

### 测试与文档

- `tests/test_scholar_patch.py` +8 例（打桩 `rand_delay` 与 `sleep` 断言实际缩放值，含 `--accelerate`
  叠加与纯判据边界），新增 `tests/test_venue_quality.py` 27 例（判据矩阵、四种组合、跳过零请求、
  跳过不写缓存、混合列表只请求需要的、缓存复用、字段逐字节不变）。全套 383 → **418** 例，全绿。
- `AGENTS.md`（patch 层延迟说明 / 新模块 / 依赖图 / 测试表 / 例数）、`README.md`（Features、限流一节、
  venue_full 与 Citation Formats 两节、Project Structure）、`issues.md`（第六节取舍 + 第七节冷却实测）
  同步。

## 2026-10-14: 修 `--cite-formats` 被 `has_pending_work()` 短路吞掉

`main()` 在「引用总数/论文数未变且没有待抓引文」时会整段跳过 Phase 2，而格式富化在 Phase 2 的
`_save_output()` 里 —— 于是开了 `--cite-formats` 也可能再也不执行。现在短路会先问新增的
`has_cite_format_work()`（`cid` 存在 + 不在 `cite_format_cache.json` + 仍需要对话框 → 有待办），
有待办就继续跑 Phase 2（引文照旧走缓存，只做格式那一步）。

## 2026-10-15: venue_key（规范发表地名 + 年份）与存量归一化脚本

新增 `code/venue_key.py`：`venue_key` = 规范发表地名 + 年份（`ACL2026`、`Findings of ACL2026`），
供下游做论文去重/唯一性判断；规范名与匹配算法沿用 3.a.2 已有的词表（51 会议 + 30 期刊，
`VENUE_PRESTIGE` + `VENUE_ALIASES` + `_venue_match` 的语义），**Findings 按主会分条且独立于主会**，
`TACL` 期刊不被 `ACL` 会议吞掉，两处拼写变体合并（`NIPS` → `NeurIPS`、`ACMMM` → `ACM MM`）。
认不出的 venue 先用自身串当 key（`venue_key_source='raw'`）等待人工确认，占位值（空/`NA`/`N/A`）
**不给 key**。年份取 venue 串里第一个 4 位年份，缺失才退回记录自身 `year`。

- `code/pub_info.py`：`venue_key` / `venue_key_source` 为**派生字段**（`venue_full or venue` + year），
  在 `to_dict()` 时重算 —— 所以详情页晚到、或历史 profile 里有旧值，都不会写出陈旧 key。
- `code/citation_identity.extract_citation_info()`：每条施引条目同样写这两个字段（只增不改，
  `title`/`authors`/`venue`/`year`/`url`/`cites_id` 一字不动）。
- 新增 `code/data_upgrade/normalize_venue_key.py`：把规则应用到**存量**输出（默认 dry-run，
  `--apply` 才写，只改这两个派生字段，备份 + `manifest.json`，幂等），并导出每个"认不出的 venue"
  连计数（`--review-file`）供人工审阅后扩表。
- 测试：`tests/test_venue_key.py` 18 例、`tests/test_normalize_venue_key.py` 8 例；全套 **449 例**全绿。
- 存量应用实测（作者 `HF3-E9kAAAAJ`）：profile 241 篇 → canonical 120 / raw 117 / 无 venue 4；
  引文 9,269 条 → canonical 354 / raw 7,690 / 无 venue 1,225；备份
  `output/venue_key_backup_20261014_194422/`，审阅清单 `output/venue_key_review.json`。
  引文侧 raw 居多是因为 Scholar 的引用列表页把 venue 截到约 25 字符（`Proceedings of the …` x556），
  这些串彼此不可区分，**不宜直接当去重键** —— 已在 `issues.md` 记下待决。
- `AGENTS.md` / `README.md` 同步（新模块、两个字段、CLI、测试表、例数 418 → 449）。

## 2026-10-15（续）: venue 词表扩到 168 条；期刊 key 不带年份

用户口径："其他会议包括 EACL、ICASSP 等，同理类推；期刊的 venue_key 为期刊的缩写名称
（TACL、TASLP、Nature Machine Intelligence 等）"。

- **规则细分**：会议 key = 规范名 + 年份（`ACL2026`/`ICASSP2025`）；**期刊 key = 缩写名、无年份**
  （`TACL`/`TASLP`/`Nature Machine Intelligence`）。实现是 `resolve_venue_key()` 里按
  `VENUE_CANONICAL[...][1]` 的类型分支（一行）。
- **扩表 81 → 168 条（84 会议 + 84 期刊）**，新增的正是真实数据里未识别的那批：
  语音/机器翻译/NLP/ML/视觉会议（ICASSP、INTERSPEECH、SLT、ASRU、Odyssey、IJCNN、ICANN、PAKDD、
  ACML、LREC、CCMT、CWMT、WMT、AMTA、EAMT、IWSLT、NLPCC、CCL、IJCNLP、PACLIC、IALP、CIPS-SIGHAN、
  NLP-NABD、LT4HALA、ALP、ALT、PMLR、RANLP、SIGDIAL、INLG、ICMI、ICME、ICPR、BMVC、WACV）
  与期刊（TASLP、TALLIP、TKDD、TNNLS、TCYB、TIST、TMM、CSUR、JAIR、JCST、FCS、SCIS、LRE、
  Neurocomputing、Neural Networks、Machine Learning、Pattern Recognition、KBS、KAIS、ESWA、
  Applied Intelligence/Soft Computing、Computer Speech & Language、Information Sciences/Fusion/
  Processing & Management、IEEE Access、Applied Sciences、Electronics、Mathematics、Symmetry、
  Sensors、Information、Scientific Reports、PeerJ CS、Procedia CS、The Journal of Supercomputing、
  Cognitive Computation、Complex & Intelligent Systems、Bioinformatics、AI Open、
  Nature Computational Science、eLife，以及中文期刊：中文信息学报、小型微型计算机系统、软件学报、
  电子学报、计算机研究与发展、计算机科学、模式识别与人工智能、自然言語処理）。
- 顺带修好的口径问题：`CCMT`/`CWMT`/`WMT` 共用词组、`TACL` 与 `ACL`、`Science` 与 SCIENCE CHINA、
  `Nature` 与 Nature 子刊 —— 全靠"按文本长度降序 + 匹配语义"分开，并补了 KDD/CIKM 的
  `… international conference on …` 写法别名与 `中文信息学报` ←→ `Journal of Chinese Information
  Processing` 这类同刊两名的别名。
- **存量重跑**（`normalize_venue_key --apply`，备份 `output/venue_key_backup_20261014_194909/`）：
  pub 120 → **190** canonical（raw 117 → 47，无 venue 4 不变）；
  引文文件整体 354 → **1255** canonical（其中纯引文 234 → 1065，raw 7690 → 6789）。
  幂等复跑 0 改动。剩余 raw 主要是 Scholar 在引文列表页截断的串（`Proceedings of the …` 556 条等）
  与 arXiv 预印本（pub 侧 40/45 个不同值都是 arXiv id）。
- 测试 449 → **453 例**全绿（新增期刊无年份、扩表会议、CCMT/CWMT/WMT 分离、中文期刊同刊两名合并）。
