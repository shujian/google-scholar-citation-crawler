### ✅ 已处理

> direct: diagnostics summary absent — will re-fetch
> 怎么还有这样的日志？这里说会re-fetch，但是实际也并没有发生re-fetch，这是为什么？

- `completeness_diag()` 的文案改成 `diagnostics summary absent`，不再说 "will re-fetch"
- 是否 re-fetch 由调用方（`_citation_status` → `need_fetch`）决定，不是 diagnostic 函数能判断的

### ✅ 已处理

> 我发现fetched at对应的时间并没有被记录在日志里。请检查一下。

- fetch 完成后现在同步 `_fetched_at` 到 `PaperFetchState`

----
