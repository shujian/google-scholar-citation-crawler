# CLAUDE.md

请使用中文作为主要工作语言。但是在代码和提交中使用英文。

工作流程在approach.md中进行了描述。

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development commands

- Install deps: `pip install -r requirements.txt`
- Run the CLI: `python scholar_citation.py --author YOUR_AUTHOR_ID`
- Run a small targeted crawl: `python scholar_citation.py --author YOUR_AUTHOR_ID --skip 2 --limit 3`
- Recheck incomplete citation caches in a selected range: `python scholar_citation.py --author YOUR_AUTHOR_ID --recheck-citations --skip 2 --limit 3`
- Use interactive captcha recovery: `python scholar_citation.py --author YOUR_AUTHOR_ID --interactive-captcha`
- Speed up deliberate waits for debugging interactive mode: `python scholar_citation.py --author YOUR_AUTHOR_ID --interactive-captcha --accelerate 0.1`
- Run all tests: `python -m unittest discover -s tests -p "test_*.py"`
- Run a single test file: `python -m unittest tests.test_citation_status`
- Run a single test: `python -m unittest tests.test_citation_status.CitationStatusTests.test_citation_status_stays_complete_when_seen_matches_current_total`
- Legacy monolithic test file (still valid): `python -m unittest test_citation_page_stop.py`

## Project structure

Entry point is `scholar_citation.py`. Supporting modules live in `crawler/` (shared utilities, profile I/O, citation cache helpers, citation strategy). Tests are in `tests/` (9 focused files + shared stubs in `tests/conftest.py`).

## Architecture overview

The script always runs in two phases from `main()`:
1. `AuthorProfileFetcher` fetches the author profile and publication list, writes profile outputs, and records change history.
2. `PaperCitationFetcher` loads the saved profile/publications cache and fetches per-paper citation lists incrementally.

`AuthorProfileFetcher` is responsible for:
- resolving cache/output paths under `output/` and `output/scholar_cache/`
- fetching author basics every run to detect citation-count changes
- fetching or reusing the publication list
- sorting publications by citation count descending before downstream processing
- maintaining `change_history` inside `author_<ID>_profile.json`
- writing both JSON and formatted Excel outputs for the author/profile phase

`PaperCitationFetcher` is responsible for:
- reading the saved profile plus `scholar_cache/.../publications.json`
- deciding per paper whether citation data is `missing`, `partial`, `complete`, or `skip_zero`
- resuming from partial per-paper cache files under `scholar_cache/author_<ID>/citations/`
- writing consolidated citation outputs to `author_<ID>_paper_citations.json` and `.xlsx`

The key behavior in citation fetching is incremental cache validation rather than naive refetching:
- normal runs skip a paper when cached citation coverage is already complete relative to Scholar
- `--recheck-citations` re-evaluates selected papers using cached-vs-current completeness logic
- when author total citations and publication count are unchanged, the script still checks whether any paper cache is incomplete before deciding to skip citation crawling entirely

For highly cited papers (`YEAR_BASED_THRESHOLD = 50`), citation fetching switches to year-by-year mode. That logic is important because it supports:
- resume after interruption via `completed_years` and partial year offsets
- different fetch directions for update mode vs full/recheck mode
- early stop once enough citations have been recovered
- reprobe of the citation year range on fresh runs, but not same-run retries

The crawler heavily patches `scholarly` internals inside `PaperCitationFetcher._patch_scholarly()` instead of treating `scholarly` as a black box. That patch layer handles:
- browser-like headers
- HTTP/2 sessions via `httpx`
- dynamic referer updates
- randomized waits and mandatory long breaks
- session refreshes
- captcha/session recovery hooks

Interactive recovery is a first-class workflow, not an afterthought:
- `--interactive-captcha` lets the user paste a browser “Copy as cURL” request
- cookies are extracted from the cURL and injected into patched `scholarly` sessions
- in interactive mode the program retries indefinitely instead of stopping after `MAX_RETRIES`
- in non-interactive mode it falls back to a long wait loop prompting the user to switch proxy/IP and type `ok`

## Testing notes

The existing tests are narrow unit tests around CLI parsing and year-probe/resume behavior. `test_year_probe_logic.py` stubs `scholarly` and `openpyxl` modules before importing `scholar_citation`, so tests run without real network or Excel dependencies.

When changing year-based fetching, resume behavior, or CLI flags, update `test_year_probe_logic.py` first instead of trying to add full integration coverage.

## Dependencies and runtime assumptions

- `httpx` is pinned to `0.27.2` for compatibility with the current `scholarly` integration.
- Proxy handling relies on `https_proxy` / `http_proxy` environment variables; the code intentionally does not use `scholarly`’s proxy API.
- The default output directory is `./output`, and cache correctness depends on files inside `output/scholar_cache/`.
