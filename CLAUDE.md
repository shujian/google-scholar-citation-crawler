# CLAUDE.md

请使用中文作为主要工作语言。但是在代码和提交中使用英文。

## 工作管理
- `WORK_NOTES.md`：技术细节、架构决策、踩坑记录，供自己和贡献者参考
- `user.md`：用户原始输入历史，展示 AI 辅助开发的完整对话轨迹
- `README.md`：面向外部用户的功能说明，有重大功能更新时同步更新

每次完成功能更新或者bug修复等任务之后，请将用户完整输入记录在user.md，讲技术细节记录在WORK_NOTES.md，并考虑README.md是否需要更新。上述事宜都处理完毕之后，提交git更新。

如果用户对某项功能的实现有异议，或者发现了问题需要进一步修复，请同样在完成后以上述流程进行处理。

如果对某些用户要求有异议，比如觉得要求不合理，或者实现有问题，请先跟用户确认。

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development commands

- Install deps: `pip install -r requirements.txt`
- Run the CLI: `python scholar_citation.py --author YOUR_AUTHOR_ID`
- Run a small targeted crawl: `python scholar_citation.py --author YOUR_AUTHOR_ID --skip 2 --limit 3`
- Recheck incomplete citation caches: `python scholar_citation.py --author YOUR_AUTHOR_ID --recheck-citations --skip 2 --limit 3`
- Use interactive captcha recovery: `python scholar_citation.py --author YOUR_AUTHOR_ID --interactive-captcha`
- Speed up waits for debugging: `python scholar_citation.py --author YOUR_AUTHOR_ID --interactive-captcha --accelerate 0.1`
- Run all tests: `python -m unittest discover -s tests -p "test_*.py"`
- Run a single test file: `python -m unittest tests.test_citation_status`
- Run a single test: `python -m unittest tests.test_citation_status.CitationStatusTests.test_citation_status_stays_complete_when_seen_matches_current_total`
- Legacy monolithic test file (still valid): `python -m unittest test_citation_page_stop.py`

## Project structure

```
google-scholar-citation-crawler/
│
├── scholar_citation.py          # CLI entry point + PaperCitationFetcher orchestrator
│
├── crawler/                     # All supporting modules
│   ├── __init__.py
│   ├── common.py                # Constants (delays, thresholds) + stateless utilities
│   ├── fetch_context.py         # FetchContext dataclass: per-paper mutable fetch state
│   │
│   │   ── Author profile layer ──
│   ├── author_fetcher.py        # AuthorProfileFetcher: fetch + cache author profile
│   ├── profile_io.py            # Build and write profile JSON / Excel outputs
│   │
│   │   ── Citation data layer ──
│   ├── citation_cache.py        # Year-count maps and fetch-diagnostics pure functions
│   ├── citation_strategy.py     # Fetch policy, refresh strategy, reconciliation
│   ├── citation_identity.py     # Citation dedup key and info extraction
│   ├── citation_io.py           # Cache I/O, status derivation, citations Excel output
│   │
│   │   ── Fetch engine ──
│   ├── citation_fetch.py        # fetch_citations_with_progress + fetch_by_year engine
│   ├── scholarly_session.py     # SessionContext + scholarly monkey-patch + year probe
│   ├── interactive.py           # cURL cookie injection, captcha prompt, proxy-switch wait
│   │
│   │   ── CLI ──
│   └── cli.py                   # parse_args() + _run_main(args)
│
├── tests/                       # Unit tests (105 tests, no network required)
│   ├── conftest.py              # Shared stubs (scholarly/openpyxl mocks) + FetcherTestCase
│   ├── test_scholar_patch.py    # scholarly patch URL logging, inject_curl, parse_args
│   ├── test_year_fetch_early.py # fetch_by_year early-stop and histogram-authoritative mode
│   ├── test_fetch_policy.py     # Fetch policy selection, refresh strategy, effective totals
│   ├── test_direct_fetch.py     # Direct fetch: progress save, early-stop, resume, dedup
│   ├── test_year_fetch_main.py  # Year fetch: materialize, selective refresh, force rebuild
│   ├── test_output.py           # save_output, flush promotion, reconciliation
│   ├── test_citation_status.py  # _citation_status, rehydrate, diagnostics boundary tests
│   ├── test_main_loop.py        # _run_main_loop retry, main() CLI integration
│   └── test_profile.py          # AuthorProfileFetcher count summary and JSON/Excel output
│
├── test_citation_page_stop.py   # Legacy monolithic test file (kept for transition)
├── requirements.txt             # scholarly>=1.7, openpyxl>=3.1, httpx==0.27.2
├── README.md                    # Public-facing documentation
├── WORK_NOTES.md                # Technical change log (for contributors)
├── approach.md                  # Development workflow description
└── user.md                      # Raw user message history (AI-assisted dev record)
```

## Architecture overview

The program always runs in two sequential phases from `main()`:

**Phase 1 — Author profile** (`AuthorProfileFetcher` in `crawler/author_fetcher.py`):
- Fetches author basic info and publication list from Google Scholar every run
- Detects citation-count changes by comparing with the previous profile
- Sorts publications by citation count (descending) before downstream processing
- Maintains `change_history` inside `author_<ID>_profile.json`
- Writes both JSON and formatted Excel outputs

**Phase 2 — Paper citations** (`PaperCitationFetcher` in `scholar_citation.py`):
- Reads the saved profile and `scholar_cache/.../publications.json`
- Decides per paper whether citation data is `missing`, `partial`, `complete`, or `skip_zero`
- Resumes from partial per-paper cache files under `scholar_cache/author_<ID>/citations/`
- Writes consolidated citation outputs to `author_<ID>_paper_citations.json` and `.xlsx`

### Citation fetch strategy

Status is derived from **current counts and diagnostics**, not from persisted `complete` flags:
- Normal runs skip a paper when `num_citations_seen >= scholar_total`
- `--recheck-citations` re-evaluates papers using cached-vs-current completeness logic
- When totals are unchanged, the script still checks whether any paper cache is incomplete

Papers with `>= YEAR_BASED_THRESHOLD (50)` citations switch to year-by-year fetch mode:
- Supports resume via `completed_years` and partial year offsets in `FetchContext`
- Newest→oldest in update mode (early stop once increase is recovered)
- Oldest→newest in full/recheck mode
- Year range is determined by `probe_citation_start_year()` via the Scholar histogram DOM

### scholarly patch layer (`crawler/scholarly_session.py`)

Patches `scholarly` internals rather than treating it as a black box:
- Replaces HTTP/1.1 sessions with `httpx` HTTP/2 clients
- Injects browser-like headers (Edge/145, sec-ch-ua-*, Referer chain)
- Enforces randomised 45–90s waits before every request
- Takes mandatory long breaks every 8–12 pages to reset Scholar's rate-limit window
- Performs soft session refresh every 10–20 pages
- Tracks year-segment switches in `_citedby_long` for resume support

All patch state is held in `SessionContext`; per-paper fetch state is in `FetchContext`.

### Interactive recovery (`crawler/interactive.py`)

- `--interactive-captcha`: user pastes a browser "Copy as cURL"; cookies and
  allowlisted headers are injected into the patched scholarly sessions
- In interactive mode: retries indefinitely
- In non-interactive mode: waits up to 24h prompting the user to switch proxy and type `ok`

## Module dependency map

```
scholar_citation.py  (orchestrator + PaperCitationFetcher)
  ├── crawler.common              (constants, utilities)
  ├── crawler.fetch_context       (FetchContext dataclass)
  ├── crawler.author_fetcher      ← crawler.profile_io
  ├── crawler.profile_io          ← (no crawler deps)
  ├── crawler.citation_cache      (pure year/diagnostics functions)
  ├── crawler.citation_strategy   ← crawler.citation_cache
  ├── crawler.citation_identity   (pure dedup / info extract)
  ├── crawler.citation_io         ← crawler.citation_cache, crawler.citation_strategy
  ├── crawler.citation_fetch      ← crawler.common, crawler.fetch_context
  ├── crawler.scholarly_session   ← crawler.common
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
- Each test file covers one functional area; see the project structure above.
- When changing year-based fetching, resume behavior, or CLI flags, update the
  corresponding test file first rather than adding integration coverage.
- The legacy `test_citation_page_stop.py` is kept as a compatibility fallback during
  the transition period; both it and `tests/` run the same 105 tests.

## Dependencies and runtime assumptions

- `httpx==0.27.2` is pinned for compatibility with the current scholarly integration.
- Proxy handling relies on `https_proxy` / `http_proxy` environment variables;
  `scholarly`'s proxy API is intentionally not used (incompatible with httpx 0.27.x).
- The default output directory is `./output`; cache correctness depends on files
  inside `output/scholar_cache/`.
- Current active development branch: `refactor/modularize`
  (main branch is one refactor commit behind).
