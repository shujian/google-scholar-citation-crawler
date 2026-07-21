# 论文选择参数统一：google-scholar-citation-crawler

## 结论：暂不强制统一 ✅

Crawler 场景与另外三个项目本质不同——它操作的是 Google Scholar 上的论文列表，没有本地 `papers/` 目录。当前 `--skip`/`--limit` 适用于位置切片场景，建议保留。

## 当前状态
- 使用 `--skip M --limit N` 按引用数降序排列后的位置切片
- 这在 "先试几篇" 和 "从中间继续" 场景下工作良好

## 可选增强（不紧急）

增加 `--papers` 参数，按 `pub.no`（论文编号）选择特定论文：

```bash
# 新用法（可选）
python scholar_citation.py --author ID --papers 1,3,5-8  # 只爬这几篇
python scholar_citation.py --author ID --papers 12         # 只爬 #12

# 与现有参数互斥
# --papers 不能与 --skip/--limit 同时使用
```

实现要点：
1. 在 Phase 1（AuthorProfileFetcher）获取 publication list 后，用 `pub.no` 筛选
2. 筛选后的论文列表再传给 Phase 2（PaperCitationFetcher）
3. `--papers` 和 `--skip/--limit` 互斥

## 参考

- 统一规范：`../../paper-select-unification.md`
