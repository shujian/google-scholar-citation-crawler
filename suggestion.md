# 功能建议：引用级别筛选（Citation-Level Filtering）

## 进度：暂不实现

本项目的论文选择已通过 `--skip`/`--limit` 满足需求，引用数据须从 Google Scholar 批量抓取（不支持按索引选择性抓取），因此暂不引入引用级别的 CLI 筛选语法。

## 背景

其余 4 个子项目（paper_downloader、enrich_info、citation_analysis_info、citation_analysis_content）将统一支持以下语法：

```
-p 12(50,300,141),27(16)
```

表示「论文 #12 的引用 50、300、141，以及论文 #27 的引用 16」。括号内支持逗号和范围（`2,3,5-8,12`）。

## 若未来需要

如果未来需要缩小输出范围（例如只想输出特定引用的抓取结果），建议在 `_finalize_paper()` 或 `_save_paper_citations()` 中增加输出过滤步骤——即在去重后、写入 `author_<ID>_paper_citations.json` 前，根据引用在数组中的位置索引做过滤。

但此功能是 **输出裁剪** 而非 **抓取优化**，因为 Scholar 的引用页面无法跳过中间的引用条目。节省的是下游处理时间，不是抓取时间。
