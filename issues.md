# 待办：跨仓库与需产品决策的事项

> 来源：2026-10-08 对 `google-scholar-citation-crawler` 的只读代码审查 + 生产数据审计。
> 本仓库内已完成的部分见 `_update_history.zh.md` 的 2026-10-08 各节；**下面这些不属于本仓库**（或需要你拍板），已按你的要求集中记录，便于其他仓库处理。

## 一、paper_downloader（另一个独立 git 仓库）

1. **输入文件靠人工放置，没有自动同步**
   阶段 2 的输入是**当前工作目录**下的 `paper_citations.json`：`--input-file` > cwd > 本 worktree
   根目录（并告警），见 `paper_downloader/AGENTS.md` 的「配置约定」。`{local,main}/code/update_run.sh`
   只检查该文件是否存在，**不再 `cp` 同步**；旧布局的 `<容器根>/paper_citations.json` 程序也不再读。
   因此 crawler 的新输出不会自动流到阶段 2 —— 2026-10-11 实测三份输入是三个世代：
   `main` = 2026-10-08 抓取（241 篇 / 9,200 条）、`local` = 2026-07-05（245 篇 / 8,476 条）、
   容器根 = 2026-07-01（243 篇 / 8,546 条，遗留副本，程序不读）。
   → 建议：每轮抓取后把 `google-scholar-citation-crawler/output/author_<ID>_paper_citations.json`
   放到实际运行的那个 worktree 根目录（或用 `--input-file` 直接指过去），并核对双方的 `fetch_time`。

2. **兜底键 `no_id:{title}` 没有论文作用域**
   crawler 侧 24.25%（2196/9054）的引文没有 `cites_id`（Scholar 不提供，crawler 无法凭空生成）；下游 `progress` 用全局主键，实测 131 个兜底键跨论文冲突（多出 139 行）。
   → 建议：下游把键改为 `{paper_no}:{title}`。

3. **`url == "N/A"` 的条目无法下载**
   crawler 侧 159 条；downloader 显式判为非法 URL 并 `mark_failed`，于是每轮都可能重试。
   → 建议：这类条目在下游标为"已知不可下载"（而不是 failed），或由 crawler 侧尝试从 Scholar 详情页补 URL。

4. **重复 cluster 造成的重复下载**
   审计发现同一论文的不同 cluster 被当作两条引文，两条都 downloaded（同一 PDF 存两份）。crawler 现在会在输出层按 `cites_id`/归一化标题折叠重复行（见 `_update_history.zh.md` 2026-10-08 节）。
   → 建议：清理一次历史 `citations_meta.json` 里的重复条目，或让 downloader 也按标题去重。

## 二、本仓库内仍需产品决策的遗留项（本次刻意未改）

1. **`cites_id` 缺失的 24.25%**：可选方案 (a) 下游用带论文作用域的 `title` 键（成本最低）；(b) 对这类条目额外抓一次 Scholar 详情页（成本高、易触发限流）。
2. **`Mirror-Generative` 类 0/N 论文**：已确认是 Google 侧空页。现在会记 `termination=empty_page`，并有 stall 抑制（连续两轮同结果后跳过），但仍会每轮发一次请求（约 1 页）。是否要进一步"暂停 N 天"需要你决定。
3. **`scholar_unyeared_count` 的真实含义**（详见 `_work_notes.zh.md` 2026-10-04 节）：它是 `scholar_total − histogram_total`，**不是**"我们抓不到的无年份引用"。是否要开一条无过滤（direct）通道来验证这些差额条目能否被抓到，需要你决定。
4. **`--accelerate` 的生效范围**：现在无条件生效（未配 `--interactive-captcha` 时打印警告）。若你更希望它只在交互模式下生效，说一声即可回退。
5. **详情页抓取没有强制长休息（2026-10-14 实测）**：引文列表页每 8–12 页有 3–6 分钟强制休息
   （`patched_load_url()`），而直接 `nav._get_soup()`（year probe 与新的 venue 详情页抓取都走这条路）
   只有 45–90 s 请求前延迟。实测 `--accelerate 0.3`（约 20 s/请求）连续 75 个详情页后就被限流，
   冷却也救不回来。当前需要人工控制节奏（用默认延迟、分轮跑，缓存可续跑）。
   → 是否需要把强制长休息也接到直接 `_get_soup` 路径上？属于新行为，等你拍板。

## 三、本次已在本仓库修掉、但值得后续回归验证的

- 连续两次真实运行观察 `_fetch_state.stall_signature` / `stall_count` 是否落盘、`stalled` 是否出现（生产数据里目前尚无该字段，需要各篇再抓两轮）。
- 观察一次被中断的 year 抓取是否在下次运行从 `partial_year_start` 续抓（日志 `Partial resume points`）。
- 观察 `--fetch-mode rough` 在新语义（计数未变 + 上次干净跑完）下跳过哪些论文，是否符合预期。

## 四、本仓库内的长期重构项（低优先级，不需要产品决策）

1. **`print()` → 结构化日志的逐步迁移**：`code/` 下仍有约 270 处 `print()`，目前靠 `TeeStream`
   写入日志文件；只有 `code/log_utils.py` 与 `code/scholar_citation.py` 使用 `get_logger()`。
   约定是**新代码一律用 logger**，存量在后续迭代里逐步迁移；全部迁完才可能移除 `TeeStream`
   （2026-07-05 的跨项目代码评审留下的「后续建议」，至今未做）。

## 五、下游 3.a.1（另一个独立 git 仓库）：venue 修复可优先用 `venue_full`

2026-10-14 起，阶段 1 会在 `pub` 上多写三个**只增不改**的字段：`citation_for_view`（出版详情页的
token）、`venue_full`（详情页给的**未截断** venue）、`venue_full_source`（目前恒为 `"scholar_detail"`）。
本 profile 实测 241 篇里有 **106 篇**的 `venue` 是被 Scholar 列表页截断的。

→ 建议：3.a.1 的 venue 修复**优先采用 `venue_full`**（为空时回退 `venue`）。**阶段 1 本轮不改下游**；
`venue` 自身仍旧绝不覆盖（它参与元数据身份键，见本仓库 `AGENTS.md`）。

两个给下游的提醒：

1. `venue_full` 只在阶段 1 的 **Phase 2 跑过一次**之后才会出现在
   `output/author_<ID>_paper_citations.json` 里（`pub` 由 `_save_output()` 重建）。若某轮
   「总数无变化 + 无待抓引文」直接跳过 Phase 2，输出文件保持旧内容（profile JSON 仍然是新的）。
2. 详情页抓取是**每篇源论文一次**网络请求（241 量级），受 `--accelerate` 缩放、约 20–65 s/篇；
   缓存 `output/venue_detail_cache.json` 可续跑，删掉它才会重新请求。

## 六、施引条目的引用格式（`cid` / `cite` / `--cite-formats`，2026-10-14）

阶段 1 现在**无条件**把每个施引条目的 Scholar cluster id 存成 `citation['cid']`（纯解析、不发请求，
`data-cid` 就是 scholarly 的 `url_scholarbib`），并在**显式开启** `--cite-formats` 时抓该条目的
Cite 对话框，把五种格式 + BibTeX 存进 `citation['cite']`（只增不改）。需要你决策的点：

1. **默认关闭是刻意的**：每个施引条目 1 次请求（BibTeX 再 1 次），全库是数千次量级 ——
   与「每篇源论文一次」的 venue 详情页（241 量级）不是一个成本级。是否默认开启、或只对
   「作者是缩写串 / venue 被截断」的条目开启，需要你拍板。缓存 `output/cite_format_cache.json`
   让重跑几乎零成本（命中不发请求），所以"先小样本试、再决定铺不铺"是可行路径。
2. **下游可以怎么用**（本轮**没改**下游，3.a.1 现在读不到这两个新字段）：
   - 全名：`cite.chicago` 与 `cite.bibtex.author` 都给全作者全名（`M Bu` → `Bu, Haoyu`）；
   - venue：`cite.bibtex.journal` / `booktitle` 给未截断的 venue，`cite.vancouver` 还给卷期页码；
   - 仍然**绝不覆盖 `venue`**（身份键），下游要用请写新字段或只在 enrich 阶段作为证据。
3. **`cid` 与 `cites_id` 是两个东西**：`cid` 只定位「这一条施引结果行」的 Cite 对话框，
   **绝不参与任何身份键**，别拿它当 `cites_id` 用（`cites_id` 是被引作品的 cluster 列表）。
4. **BibTeX 链接是带签名的**（`scisdr` / `scisig`）：签名只在本次响应里有效，所以缓存把 URL 一起存，
   只用它重试失败的那一次请求；签名若过期只会退化成「该条没有 bibtex」，不影响五种格式。
5. **这条路径没有强制长休息**：Cite 对话框走 `nav._get_soup()` → `patched_get_page()`，与 venue 详情页
   同一个入口（只有 45–90 s 请求前延迟，没有引用列表页那种每 8–12 页的 3–6 分钟长休息）。
   与上面第二节第 5 条是同一个产品问题：**是否给这条路径也接上强制长休息？**

## 七、新抓取路径的等待时间缩到 1/10（2026-10-14 用户要求）与限流实测代价

**已实现**：`code/config.py` 的 `DETAIL_PAGE_DELAY_FACTOR = 0.1` 是**唯一**定义；
`code/scholarly_session.py` 的 `detail_path_delay_factor(url)` 是**唯一**判据，决定哪些 URL 算
「detail 类」—— 出版详情页（`view_op=view_citation`）、施引条目的 Cite 对话框（`output=cite`）、
它的带签名 BibTeX 下载（`scholar.bib` / `output=citation`）。`patched_get_page()` 里
`rand_delay(ctx.delay_scale * detail_path_delay_factor(url))` 是唯一计算点：
**有效延迟 45–90 s → 4.5–9 s**；`--accelerate` 仍在其上相乘（两者是乘积关系，语义不变）。
引用列表页分页与 profile 页保持 45–90 s 与强制长休息。两条新路径**不允许**自己定义延迟
（`tests/test_scholar_patch.py` 有源码级守卫）。缓存/续跑/原子写/「遇限流即停」一律不变。

**诚实的代价（已写进 README/AGENTS）**：Scholar 是按**量**限流的，缩短延迟不会提高"每窗口能拿多少页"，
只会让拦截更早到来。实测（2026-10-14）：

| 时间 | 现象 |
|------|------|
| 15:05 | venue 详情页跑批（`--accelerate 0.3`，约 20 s/请求）在第 88 页开始 `Cannot Fetch from Google Scholar` |
| 15:07–15:11 | crawler 自带的 3 分钟限流冷却 ×2 —— **救不回来** ✗（同一页仍失败） |
| 15:20 | 本实验第一条请求（引文列表页）仍旧失败 |
| 15:22 | 复测：search 端点返回 **captcha 表单**（`id="gs_captcha_f"`） |
| 15:35 | 复测：profile 页 **429**、cite 对话框 **403** —— 整站限流 |
| 15:52–17:52 | 后台 watcher 按 30/45/75 分钟递增冷却各探一次（每次 2 个请求） |

结论：**3 分钟 ✗、约 15 分钟 ✗**；更长的冷却在测。所以这套 1/10 延迟的可用性**完全依赖**
「缓存 + 续跑 + 被拦即停」：单轮能更快地拿满一个窗口的页数，被拦后停下、过一段时间续跑，
总墙钟时间比"每页等 45–90 s 再被同样拦下"更短。若将来发现拦截是**按时间窗**而非按量，
这个结论要重新评估（待办）。

### 跳过「已经完整」的记录：取舍（2026-10-14 用户认可）

规则：`venue` 详情页路径 —— `venue` 非空 + 不含 `…`/`...` + 不是预印本源 → **不抓、不写缓存**；
cite 页路径 —— 上述 venue 判据成立**并且**该条目 `authors` 已是全名形态 → 不抓（两者都齐才跳过）。

**代价（诚实记录）**：实测 `venue_full` **不是** `venue` 的超集 —— 详情页有时比列表页更短、
会丢卷期页码；同理，跳过"已经完整"的施引条目，等于放弃 Cite 对话框本可补上的
卷期/页码/年份（`cite.bibtex.volume|number|pages`、`cite.vancouver`）。用户已知悉并接受。
反过来，**已有的缓存条目仍然复用**：重跑不会把上一轮付过钱的 `cite` 从输出里抹掉。
