# 待办：跨仓库与需产品决策的事项

> 来源：2026-10-08 对 `google-scholar-citation-crawler` 的只读代码审查 + 生产数据审计。
> 本仓库内已完成的部分见 `_update_history.zh.md` 的 2026-10-08 各节；**下面这些不属于本仓库**（或需要你拍板），已按你的要求集中记录，便于其他仓库处理。

## 一、paper_downloader（另一个独立 git 仓库）

1. **输入文件不是 crawler 的输出**
   `paper_downloader/{local,main}/code/config.py` 中 `INPUT_FILE = <paper_downloader>/paper_citations.json`，靠 `update_run.sh` 手工 `cp` 同步。
   实测该副本停在 **2026-07-01（243 篇 / 8546 条）**，而 crawler 最新输出是 **2026-10-04（247 篇 / 9054 条）** —— 旧约 3 个月、少 508 条引用。
   → 建议：`INPUT_FILE` 直接指向 `../google-scholar-citation-crawler/output/author_<ID>_paper_citations.json`，或在同步脚本里校验双方 `fetch_time`。

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
5. **根目录 `AGENTS.md` / `CLAUDE.md` 的测试数量已过期**（分别写 119 / 174，实际 215）。它们在另一个 git 仓库（workspace 根），本次未改动。

## 三、本次已在本仓库修掉、但值得后续回归验证的

- 连续两次真实运行观察 `_fetch_state.stall_signature` / `stall_count` 是否落盘、`stalled` 是否出现（生产数据里目前尚无该字段，需要各篇再抓两轮）。
- 观察一次被中断的 year 抓取是否在下次运行从 `partial_year_start` 续抓（日志 `Partial resume points`）。
- 观察 `--fetch-mode rough` 在新语义（计数未变 + 上次干净跑完）下跳过哪些论文，是否符合预期。
