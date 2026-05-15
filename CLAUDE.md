# CLAUDE.md

请使用中文作为主要工作语言。但是在代码和提交中使用英文。

## 工作管理
- `_work_notes.zh.md`：技术细节、架构决策、踩坑记录，供自己和贡献者参考
- `_update_history.zh.md`：按时间顺序的更新历史
- `_user.zh.md`：用户原始输入历史，展示 AI 辅助开发的完整对话轨迹
- `README.md`：面向外部用户的功能说明，有重大功能更新时同步更新

每次完成功能更新或者bug修复等任务之后，请将用户完整输入记录在 _user.zh.md，将更新历史记录在 _update_history.zh.md，将技术细节记录在 _work_notes.zh.md，并考虑 README.md 是否需要更新。上述事宜都处理完毕之后，提交git更新。

如果用户对某项功能的实现有异议，或者发现了问题需要进一步修复，请同样在完成后以上述流程进行处理。

如果对某些用户要求有异议，比如觉得要求不合理，或者实现有问题，请先跟用户确认。

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development commands

- Install deps: `pip install -r requirements.txt`
- Run the CLI: `python scholar_citation.py --author YOUR_AUTHOR_ID`
- Run a small targeted crawl: `python scholar_citation.py --author YOUR_AUTHOR_ID --skip 2 --limit 3`
- Force re-fetch specific papers: `python scholar_citation.py --author YOUR_AUTHOR_ID --fetch-mode force --skip 2 --limit 3`
- Use interactive captcha recovery: `python scholar_citation.py --author YOUR_AUTHOR_ID --interactive-captcha`
- Speed up waits for debugging: `python scholar_citation.py --author YOUR_AUTHOR_ID --interactive-captcha --accelerate 0.1`
- Run all tests: `python -m unittest discover -s tests -p "test_*.py"`
- Run a single test file: `python -m unittest tests.test_citation_status`
- Run a single test: `python -m unittest tests.test_citation_status.CitationStatusTests.test_citation_status_stays_complete_when_seen_matches_current_total`

## Project structure

详细项目结构见 `_work_notes.zh.md` 的「项目结构」节（含各模块中文说明），对外简介见 `README.md` 的「Development Notes → Project Structure」节。

简况：`crawler/` 有 16 个模块（author profile 层、citation data 层、fetch engine 层、CLI）；`tests/` 有 119 个测试，10 个文件。

## Architecture overview

The program always runs in two sequential phases from `main()`:

**Phase 1 — Author profile** (`AuthorProfileFetcher` in `crawler/author_fetcher.py`):
- Fetches author basic info and publication list from Google Scholar every run
- Detects citation-count changes by comparing with the previous profile
- Sorts publications by citation count (descending) before downstream processing
- Maintains `change_history` inside `author_<ID>_profile.json`
- Writes both JSON and formatted Excel outputs

**Phase 2 — Paper citations** (`PaperCitationFetcher` in `scholar_citation.py`):
- Reads the saved profile JSON output (cross-run state) and in-memory mid-paper state (same-run resume)
- Decides per paper whether citation data is `missing`, `partial`, `complete`, or `skip_zero`
- Resumes from in-memory state (`_mid_paper_state`, `_paper_states`)
- Writes consolidated citation outputs to `author_<ID>_paper_citations.json` and `.xlsx`

### Citation fetch strategy

Status is derived from **current counts and diagnostics**, not from persisted `complete` flags:
- Normal runs skip a paper when `num_citations_seen >= scholar_total`
- `--fetch-mode {rough,normal,force}` controls re-fetch aggressiveness
- `scholar_changed` flag triggers re-fetch when Scholar count changes even for complete papers

Papers with `>= YEAR_BASED_THRESHOLD (50)` citations switch to year-by-year fetch mode:
- Supports resume via `completed_year_segments` and `partial_year_start` offsets
- Oldest→newest year order
- Year range is determined by `probe_citation_start_year()` via the Scholar histogram DOM

### scholarly patch layer (`crawler/scholarly_session.py`)

Patches `scholarly` internals rather than treating it as a black box:
- Replaces HTTP/1.1 sessions with `httpx` HTTP/2 clients
- Injects browser-like headers (Edge/145, sec-ch-ua-*, Referer chain)
- Enforces randomised 45–90s waits before every request
- Takes mandatory long breaks every 8–12 pages to reset Scholar's rate-limit window
- Performs soft session refresh every 10–20 pages
- Tracks year-segment switches in `_citedby_long` for resume support

All patch state is held in `SessionContext`; per-paper cross-run state is in `PaperFetchState` (`crawler/output_state.py`), and runtime fetch state is in `YearFetchSession` (`crawler/fetch_session.py`).

### Interactive recovery (`crawler/interactive.py`)

- `--interactive-captcha`: user pastes a browser "Copy as cURL"; cookies and
  allowlisted headers are injected into the patched scholarly sessions
- In interactive mode: retries indefinitely
- In non-interactive mode: waits up to 24h prompting the user to switch proxy and type `ok`

## Module dependency map

```
scholar_citation.py  (orchestrator + PaperCitationFetcher)
  ├── crawler.common              (constants, utilities)
  ├── crawler.author_fetcher      ← crawler.profile_io
  ├── crawler.profile_io          ← (no crawler deps)
  ├── crawler.citation_cache      (pure year/diagnostics functions)
  ├── crawler.citation_strategy   ← crawler.citation_cache
  ├── crawler.citation_identity   (pure dedup / info extract)
  ├── crawler.citation_io         ← crawler.citation_cache, crawler.citation_strategy
  ├── crawler.citation_models     (dataclasses for citations, diagnostics, resume state)
  ├── crawler.output_state        ← crawler.citation_io, crawler.citation_cache
  ├── crawler.pub_info            (PubInfo dataclass)
  ├── crawler.citation_fetch      ← crawler.common, crawler.fetch_session, crawler.citation_cache, crawler.citation_models
  ├── crawler.fetch_session       ← crawler.citation_models, crawler.output_state, crawler.common
  ├── crawler.page_visit          (per-page captcha/proxy/retry recovery)
  ├── crawler.scholarly_session   ← crawler.common, crawler.page_visit
  ├── crawler.interactive         (no crawler deps)
  └── crawler.cli  [lazy import]  ← crawler.common, crawler.author_fetcher
                                     + scholar_citation [lazy, inside _run_main]
```

No circular imports. `crawler.cli` imports `PaperCitationFetcher` lazily inside
`_run_main()` to break the `scholar_citation ↔ crawler.cli` cycle.

## Testing notes

Tests are in `tests/` and require no network or real Excel library (all external
dependencies are stubbed in `tests/conftest.py`).

- `FetcherTestCase` base class provides a fully-initialised `self.fetcher` with all
  runtime attributes zeroed out and common method stubs in place.
- Each test file covers one functional area; see `_work_notes.zh.md` 项目结构 for the mapping.
- When changing year-based fetching, resume behavior, or CLI flags, update the
  corresponding test file first rather than adding integration coverage.

## Dependencies and runtime assumptions

- `httpx==0.27.2` is pinned for compatibility with the current scholarly integration.
- Proxy handling relies on `https_proxy` / `http_proxy` environment variables;
  `scholarly`'s proxy API is intentionally not used (incompatible with httpx 0.27.x).
- The default output directory is `./output`; state is maintained in memory during runs.
- All development happens on `main` branch.
