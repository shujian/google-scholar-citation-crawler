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
3b. ~~**引用级别筛选（`-p 12(50,141,300)`）要不要在阶段 1 实现**~~ → **定案：不实现** ✅
   （原 `suggestion.md`，2026-10-15 按"一次性文档落地后并入台账并删除"的约定并入本条并删除该文件）。
   理由与当时的评估：阶段 1 的范围控制只有 `--skip`/`--limit`（按引用数降序的位置切片，见
   `AGENTS.md` 的「论文选择」节）；Scholar 的引用列表页**无法跳过中间的条目**，所以引用级筛选
   在这里只能是**输出裁剪**（在 `_finalize_paper()` / `_save_paper_citations()` 写盘前按位置过滤），
   省的是下游处理时间、不是抓取时间与请求数 —— 收益不足以再加一条 CLI 约定。
   若将来真的要缩小输出，按上面那个落点做"裁剪"而不是"抓取优化"。
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
`data-cid` 就是 scholarly 的 `url_scholarbib`），并**默认开启**抓取该条目的 Cite 对话框，
把五种格式 + BibTeX 存进 `citation['cite']`（只增不改）。

1. **默认开启（2026-10-15 用户定，crawler `6f83505`）**：引文列表页给不了**全作者全名**（实测仅
   6.2% 含任一全名）与**未截断的 venue**（61.8% 被 Scholar 截到约 25 字符），而 Cite 对话框一次请求
   就能给出这两样 —— 所以"新抓的数据必须带上它"优先于省请求。关掉用 `--no-cite-formats`，或在
   `crawler_config.json` 写 `"cite_formats": false`（解析链 `code.config.resolve_cite_formats()`：
   参数 > 配置文件 > 默认开）。成本仍是每个施引条目 1 次请求（BibTeX 再 1 次），但：**已有 `cid` 的
   条目才计费**（老的 8,989 条没有 `cid`，要重抓才有）、**venue 已完整且作者已是全名时零请求跳过**、
   缓存 `output/cite_format_cache.json` 让重跑几乎零成本（命中不发请求）、失败不写缓存且能续跑。
   另外 `citation['cite']` 存在时 `venue_key` 也改用 BibTeX 里未截断的 venue（`citation_venue_key()`）。
2. **下游可以怎么用**（本节写于 2026-10-14，**下游当时没改**；3.a.1 仍不读这两个字段 —— 要用需另开一轮）：
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

## 八、venue_key（2026-10-15）：跨仓共享与引文侧截断，需你决定

1. ~~**规范词表暂时有两份（跨仓共享待办）**~~ → **已落地共享（2026-10-15 当天）** ✅：
   唯一实体是**本仓库的 `code/shared/venue_key.py`**（宿主 = 阶段 1），`code/venue_key.py` 只是
   `from code.shared.venue_key import *` 的薄再导出 ✓；3.a.2 经 `code/core/classifier.py` 的
   `from shared.venue_key import venue_key_name` 消费同一份 ✓（各消费方有
   `tests/test_shared_venue_key.py` 守卫）。两处刻意的差异也已定案：`NIPS`→`NeurIPS`、
   `ACMMM`→`ACM MM` **各合并成一个 key** ✓（去重键不能有两个同义键）。
   **本条已无待办** ✓（原描述留档：本轮按你的选择先各持一份，改动面小、可后补）。

2. **引文侧 venue 被 Scholar 截到约 25 字符 → raw 键不可直接当去重键**
   （2026-10-15 二轮扩表后的现状：词表 81 → **168 条**，pub 侧 canonical 120 → **190**、
   引文文件整体 canonical 354 → **1255**、raw 7690 → **6789**；下面的 2,912 是扩表**前**的数字，
   扩表后引文侧仍有 2,756 个不同 raw 取值，成因不变。）
   且最高频的是**彼此不可区分的截断前缀** —— `Proceedings of the …` x556、`Findings of the …` x207、
   `IEEE Transactions on …` x138、`International Conference on …` x49 …
   按当前口径它们各自成为 `venue_key`，若下游直接拿 `venue_key` 判"同一篇"，
   这些条目会**误并**（例如两条 `Proceedings of the …` 其实来自不同会议）。
   → 三个候选，请挑一个（选定后我改 `code/venue_key.py` + 重跑存量脚本，幂等且带备份）：
   ① 截断（含 `…`）的 venue **不给 key**（`null`），下游只用 `normalized`；
   ② 保留 raw 但**另加标记**（如 `venue_key_source='raw_truncated'`），由消费方自行过滤；
   ③ 补「截断前缀 → 会议」的显式规则（只对**唯一**可判定的前缀生效，如 `Advances in Neural …` → NeurIPS），
      其余仍按 ①② 处理。**我倾向 ① + ③ 组合**：既不误并，又能救回可判定的那部分。
   注：pub 侧不受影响（`venue_full or venue` 已让 241 篇里 120 篇 canonical，raw 的 117 篇是中文期刊、
   小众会议、arXiv 预印本等**本身不在词表**的值，串本身带年份、可区分）。

3. **`venue_quality.py` 的取舍说明与实测方向相反（文档待改，非代码）**
   `code/venue_quality.py` 的 docstring 写"详情页/对话框**只能再加**卷期页码"，据此论证"跳过已完整
   的条目代价不大"。但逐条对比 92 篇"已 complete"里带详情页缓存的 **30** 篇：列表页 `venue`
   **全部**比详情页 `venue_full` 更长（`Ijcai 17 (2017), 3203-3209, 2017` → `Ijcai`；
   `Proceedings of the AAAI … 34 (05), 9298-9305, 2020` → `... artificial intelligence`），
   即详情页给的是**更少**的信息（只有 venue 名称）。所以"跳过已完整"这个决定**比文档说的更安全**
   （跳过 = 保住信息量更大的列表页串），但 docstring 的理由需要按实测改写；
   全 149 候选里 93 篇是"截断 → 补全"（功能本意）、50 篇是"列表页更全"。
   → 待办：更正 `venue_quality.py` docstring / `AGENTS.md` 相应段落（本轮只记在这里，未动代码与文档，
   因为这是你上一轮提问的调查结论，不属于本次 venue_key 需求）。
   **同时撤回**我在上一轮提的"先查缓存再跳过可把 resolved 提到 173"：那 30 篇的缓存值写回去是**降级**。

4. ~~**arXiv 预印本要不要给 key**~~ —— **已定并已实现（2026-10-15 用户：arxiv 的 venue 就写 `arXiv`）** ✓：
   所有 arXiv 写法（含被截断的 `arXiv preprint arXiv …`）→ `venue_key = 'arXiv'`（不带年份；
   与已知会议同时出现时会议优先）。存量：pub 190 → **229** canonical、raw 47 → **8**；
   引文侧 canonical 1,065 → **2,491**。
   **同类已全部落地（2026-10-15 用户："其他预印版服务器也按照一样逻辑命名"）** ✓：`bioRxiv`、
   `medRxiv`、`SSRN`、`TechRxiv`、`Research Square`、`preprints.org` 各按服务器名给 key
   （不带年份）；实测新增命中引文侧 59 条（bioRxiv 37 / SSRN 21 / medRxiv 1），
   只写 `Preprint` 而不点服务器名的 4 条仍是 raw。
   **顺带修掉一个潜伏 bug**：短全大写缩写的匹配原本只看词边界，于是宿主名里的 `www.` 会命中
   `WWW` 会议（`https://www.researchsquare.com/…` → `WWW2023`）、`kdd.org` → `KDD`、
   `iclr.cc` → `ICLR`。现在缩写两侧额外拒绝相邻的点。**当前真实数据零影响**（venue 里含 `www.`
   的条目 0 条、12 个 `WWW*` 全是真 Web Conference），属未被触发的错误。
   （下面是决定前的原文，留档。）
   原问题：arXiv 预印本要不要给 key（2026-10-15 二轮扩表后剩下的最大一类）
   扩表后 pub 侧仍未识别的 45 个不同值里 **40 个是 `arXiv preprint arXiv:<id>`**，引文侧
   `arXiv preprint arXiv …`（779 条，被截断、**没有 id**）+ `bioRxiv`（35 条）同理。
   现在它们都按"认不出 → key = 原串"处理。三个候选：
   ① 保持原样（每条一个 key，同 id 的多次引用能合并，但**截断那批**的全都是
      `arXiv preprint arXiv …`，会把 779 条无关论文并成一个 key ✗）；
   ② 有 id 的给 `arXiv:<id>`（真实身份，与下游已有的 arXiv 凭据口径一致），
      **没 id 的给 `null`**（不参与去重）—— **我倾向这个**；
   ③ 一律给 `arXiv`（等于放弃区分，只标"这是预印本"）。
   → 你挑一个我就改（`code/venue_key.py` 的 raw 分支，改完重跑脚本，幂等且带备份）。

5. **`Findings` 兜底键已删除（2026-10-15 用户：不存在这个会议）**
   未注明主会的 `Findings of the Association for Computational Linguistics` 现归 **`Findings of ACL`**；
   宿主明确的写法（`Findings of EMNLP` 等）不受影响。实测存量里没有任何一条曾落到裸 `Findings`，
   所以对已有 key 零改动。**注意跨仓影响**：3.a.2 的 `VENUE_PRESTIGE` 里**仍有**裸 `Findings` 键
   （作为未指明主会时的兜底，tier 2）—— 两边现在口径不同（阶段 1 认为不存在、3.a.2 仍会兜底）。
   若要让下游也按"归 ACL"处理，需要改 3.a.2（跨仓，记在这里）。

6. **两条有误并风险的别名（已实现，请复核）**
   - `软件学报` ← `Journal of Software`：真实数据里 7 条都正好是 `Journal of Software`，
     但 `Journal of Software: Evolution and Process` 之类会被并进来；
   - 我一度把 `Acta Electronica Sinica` 挂给了 `Chinese Journal of Electronics` —— **写错了**：
     它其实只是《电子学报》的英文名，`Chinese Journal of Electronics` 是另一本刊。**已在本轮当场改正**
     （别名归 `电子学报`，`Chinese Journal of Electronics` 不带别名）。

## 九、Cite 对话框 404 ＝"这个 cluster 句柄没有对话框"（2026-10-16 实测，已按当前理解处理）

**现象**：`https://scholar.google.com/scholar?q=info:44BcOmzPt1MJ:scholar.google.com/&output=cite&scirp=0&hl=en`
三次失败：19:52（crawler，`.com`）、05:49（crawler，`.com`）、10:5x（`output/probe_cite_rows.py`，裸 httpx，
`.hk` 镜像）。第三次拿到 **HTTP 404**，响应体是 Google 自己的
`<title>Error 404 (Not Found)!!1</title> … The requested URL … was not found on this server.`（1642 字节）。
**同一分钟、同一会话**下同页两条兄弟 `cid`（`4fZewaSVrhQJ`、`XNR1XcrxA1QJ`）都是 **200 + 5 种格式**
→ 排除"整站/整会话被封"。

**它是什么**：`cid` 是**记录（cluster）的句柄**（行上的 `data-cid`，`cite_format.cite_path()` 拿它寻址
`output=cite`），**不是文献身份**（`cites_id` 才是身份键）。所以 404 的语义是"这个句柄在 cite 端点上
没有答复"，**不是**"这篇论文没有引用格式"——列表页明明给出了它的题目/作者/venue/年份。

**顺带测到**（同一趟，论文 no=113）：Scholar 自称 9 条引用，列表页只回 **1 行**（且那 1 行正确）→ 这是
"列表比它自己宣称的少"那一类现象（`stalled` 的成因），**与 404 是两件事**。

**另一件必须分开的事**：同一天 `scholar.google.com` 与 `www.google.com` 对整条出口回
`sslv3 alert handshake failure`（TLS 级拒绝），而 `scholar.google.com.hk` 正常 200。**"通道坏了"与
"这个句柄没有对话框"在旧日志里长得一模一样**（都只有 `Cannot Fetch from Google Scholar.`）。

**当前处置（已实现）**：`SessionContext.last_response_status`（httpx `response` hook，每请求前清零）
保存状态码；`PageVisit.fetch(missing_record_on_404=is_detail_style_path(url))` 对 detail 类页面的
**404/410** 直接抛 `RecordUnavailable`（不重试、不冷却、不弹验证码）；`cite_format` 记**带时间戳的否定
条目**并在 `UNAVAILABLE_RETRY_DAYS = 7` 天后自动重问。**刻意不是永久判决**，理由见下。

**待理解（用户 2026-10-16 的判断："我们对 cluster 类论文在 Scholar 内部的组织还没有全部理解"）** ——
目前已掌握的碎片与未解问题：

- 同一篇作品可以有**多个 cluster**（实测 36 组"同标题、≥2 个不同 `cites_id`"：例如同一篇的 arXiv 版
  `6279971151076542878` 与会议版 `v7gvTAYdW8cJ`；`Pirates of the RAG` 的 2024 arXiv 行与 2025 ECAI 行）；
- `cites_id` 会**换代**（`KNOWN_ISSUES.md` T），`cid` 是同类句柄；
- 列表页给了行 `data-cid`，但 cite 端点可以 **404**；
- 列表页可以**只回一部分行**（no=113：9 → 1）；
- **未回答**：404 是节流伪装、cluster 重建、还是 Scholar 对"不同来源/版本"行的处理差异？是否需要
  "同一作品换一个 cluster/版本再试一次"的兜底？—— 需要更多实测，可复用
  `output/probe_cite_rows.py`（`--host=…` / 论文号 / 额外 cid）批量跑同类条目。

**补充实测（2026-10-16 11:2x，用户新解验证码后的全新 cookies，`.hk`）**：论文 no=89
（`The first impression problem…`）的 `cites=9818199414281846317,5272786230091896258` 列表页

```
status=200  .gs_r=3   .gs_ri=3   .gs_ab_mdw 第二条 = "3 results ( 0.03 sec)"   分页链接：NONE
```

而：profile 说 **Cited by 15**、我们库里存着 **16 行**（历次抓到并缓存）、只有 **3 行**带 `cid`
——**正好就是这一页现在返回的 3 行**（`X5ilAxew2yEJ` / `PNlJR1H2n0cJ` / `aMWB6YjHjhwJ`，三条 Cite
对话框都是 200 + 5 种格式）。所以：

- **同一个 cluster 的 `cites=` 结果集不是固定对象**：声称 15 / 缓存 16 / 现在 3，而且没有分页可翻；
  我们的"16 行"是历次会话看到的结果的**并集**，盘上那 13 行已不在当前列表里；"只有 3 行带 `cid`"
  也顺理成章 —— 带 `cid` 的记录期只见过这 3 行。
- **两种解释尚未分开**（需要下一步实测）：① 时间维度：该 cluster 的索引被重建/条目被折叠（这与
  `cites_id` 换代、`stalled`、以及"404 = 句柄退役"是同一族现象）；② 会话/地区维度：`.hk`＋这套
  cookies 看到的视图本来就比 `.com` 少。`.com` 当时 TLS 级拒绝，无法对照。
- **死句柄那次复现强化了"句柄级"结论**：**全新 cookies**（用户刚解过验证码）下 `44BcOmzPt1MJ`
  依然 **404**（第 4 次），而同分钟 `XNR1XcrxA1QJ` 与那 3 行都是 200 → 与 cookie/会话无关。
- **下一步（便宜）**：用**同一个 URL** 在浏览器里看一眼结果条数 —— 若浏览器显示 15，则解释②；
  若也显示 3，则解释①（cluster 变了），那"404 = 退役句柄"就更可信。
  页面留档：`output/probe89_list.html`。
