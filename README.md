# Google Scholar Citation Crawler

A Python tool to crawl Google Scholar author profiles and per-paper citation lists. Supports incremental caching, resume after interruption, and outputs both JSON and Excel files.

> **Developed entirely with [Claude Code CLI](https://github.com/anthropics/claude-code)** — the user wrote zero lines of code. All implementation, debugging, and iteration were driven through natural-language conversation. `user.md` and `WORK_NOTES.md` in this repository document that process in full.

## Features

- **Unified Workflow**: Fetches author profile then crawls per-paper citations in one command
- **Smart Skip**: If total citations and publication count haven't changed since the last run, citation crawling is skipped entirely
- **Incremental Caching**: Re-fetches only papers whose cache is incomplete; year-based papers use a per-year histogram to decide which years need refreshing
- **Year-Based Fetching**: Papers with many citations (configurable threshold, default ≥ 50) are fetched year-by-year (oldest → newest), skipping years whose cached count already matches Scholar's histogram
- **Dedup Handling**: Deduplicates citations using Scholar-native `cites_id` when available, falling back to `title + venue/authors` metadata; tolerates Scholar self-duplicates
- **Resume Support**: Interrupted fetches resume from the last checkpoint; per-year progress is saved so mid-paper interruptions recover gracefully
- **Anti-Ban Strategies**: Randomized 45–90 s delays, mandatory 3–6 min breaks every 8–12 pages, browser header simulation (Edge 145, sec-ch-ua-*, dynamic Referer), and HTTP/2
- **Interactive Captcha Bypass**: `--interactive-captcha` lets you paste a browser cURL to inject real cookies and resume without restarting
- **Proxy Switch Wait**: On non-interactive failure, prompts hourly to switch proxy/IP until you type `ok`
- **Change Tracking**: Records per-run citation-count history in the profile JSON
- **Dual Output**: JSON and formatted Excel for both author profile and citations
- **Run Summary**: Prints elapsed time, total pages accessed, and new citations at the end of each run

## Requirements

- Python 3.8+
- Dependencies: `scholarly`, `openpyxl`, `httpx==0.27.2`

```bash
pip install -r requirements.txt
```

> **Note**: `httpx` is pinned to `0.27.2` for compatibility with `scholarly 1.7.11`. Newer versions break internal session handling.

## Quick Start

```bash
# Fetch profile + citations for an author
python scholar_citation.py --author YOUR_AUTHOR_ID

# Also accepts a full Google Scholar profile URL
python scholar_citation.py --author "https://scholar.google.com/citations?user=YOUR_AUTHOR_ID&hl=en"

# Process only papers 3–5 (skip 2, limit 3) — useful for testing
python scholar_citation.py --author YOUR_AUTHOR_ID --skip 2 --limit 3
```

## CLI Reference

```
usage: scholar_citation.py [-h] --author AUTHOR [--output-dir DIR]
                           [--skip M] [--limit N]
                           [--force-refresh-pubs]
                           [--fetch-mode {rough,normal,force}]
                           [--interactive-captcha] [--accelerate SCALE]

required:
  --author AUTHOR               Google Scholar author ID or full profile URL

optional:
  --output-dir DIR              Output directory (default: ./output)
  --skip M                      Skip first M papers (sorted by citations desc)
  --limit N                     Process exactly N papers after --skip M
  --force-refresh-pubs          Force re-fetch of the publications list
  --fetch-mode {rough,normal,force}
                                Controls re-fetch aggressiveness (default: normal)
  --interactive-captcha         Enable interactive captcha bypass (see below)
  --accelerate SCALE            Scale all deliberate waits (e.g. 0.1 = 10× faster)
```

### `--skip` and `--limit`

Papers are sorted by citation count descending. `--skip M` skips the first M papers; `--limit N` then processes the next N papers (positions M+1 to M+N), regardless of whether each needs fetching. Useful for targeting a specific range for debugging or manual recovery.

### `--fetch-mode`

| Mode | Behavior |
|------|----------|
| `rough` | Skip papers whose Scholar count hasn't changed since the last fetch, even if the cache is incomplete. Use when you only care about truly new citations. |
| `normal` *(default)* | Re-fetch any paper whose cache is missing or incomplete, using a seen-count comparison to decide completeness. |
| `force` | Delete the cache and re-fetch from scratch. Recommended with `--skip`/`--limit` to limit scope. |

```bash
# Only fetch papers where Scholar count actually changed
python scholar_citation.py --author YOUR_AUTHOR_ID --fetch-mode rough

# Re-fetch papers 1–5 from scratch
python scholar_citation.py --author YOUR_AUTHOR_ID --fetch-mode force --skip 0 --limit 5
```

## Output Files

| File | Description |
|------|-------------|
| `author_<ID>_profile.json` | Author info, publication list, and change history |
| `author_<ID>_profile.xlsx` | Excel: Author Overview, Publications, Change History |
| `author_<ID>_paper_citations.json` | Per-paper citation lists |
| `author_<ID>_paper_citations.xlsx` | Excel: Summary, All Citations, Run Metadata |
| `output/logs/author_<ID>_run_<ts>.log` | Full log of each run (mirrors stdout) |
| `scholar_cache/` | Incremental cache (auto-managed) |

## Rate Limiting & Anti-Ban Strategies

Google Scholar aggressively rate-limits automated requests. This tool uses multiple mitigation layers:

- **Randomized delays**: 45–90 s between requests
- **Mandatory long breaks**: Every 8–12 pages, a 3–6 minute break resets Scholar's sliding-window rate limit
- **Browser header simulation**: Full `sec-fetch-*`, `sec-ch-ua-*`, `user-agent`, `accept`, `accept-language`, and dynamic `Referer` headers matching a real Edge 145 session
- **HTTP/2**: Requests use HTTP/2 (`httpx` with `h2`) for an authentic browser TLS profile
- **Session refresh**: Soft-resets the session every 10–20 pages (preserves cookies, clears `got_403` flag)
- **Shared session**: Profile and citation fetches share the same HTTP/2 session, so citation requests start with a warm session history
- **Fast failure**: `scholarly` retries limited to 1 per page so failures reach the paper-level retry quickly

**Proxy support**: Set `https_proxy` / `http_proxy` environment variables; `httpx`'s `trust_env=True` picks them up automatically.

```bash
export https_proxy=http://your-proxy-host:port
python scholar_citation.py --author YOUR_AUTHOR_ID
```

## Interactive Captcha Bypass

When Scholar shows a CAPTCHA, `--interactive-captcha` lets you inject real browser cookies without restarting:

```bash
python scholar_citation.py --author YOUR_AUTHOR_ID --interactive-captcha
```

When a block is detected:

1. The program shows you the blocked URL
2. Open that URL in your browser (solve the CAPTCHA if needed)
3. In Chrome/Edge DevTools → Network tab, right-click the request → **Copy as cURL**
4. Paste the cURL into the terminal; the program detects the end automatically (last line has no `\`)
5. Cookies and selected headers are extracted and injected; the program retries immediately

In interactive mode, the program **never exits on failure** — it keeps prompting until you solve the captcha or switch proxies.

In non-interactive mode, the program waits up to 24 hours, prompting hourly to switch your proxy/IP. Type `ok` and press Enter to retry immediately.

## Resume After Interruption

If the script is interrupted (Ctrl+C, timeout, or error):

1. Progress is saved automatically to the per-paper cache (including which years have been fully fetched)
2. Simply re-run the same command to resume
3. Completed papers and completed year segments within a paper are skipped

## Year-Based Fetch Details

Papers with ≥ 50 average citations per year switch to year-by-year fetch mode:

- **Direction**: Always oldest → newest
- **Selective refresh**: Scholar's year histogram is probed once per paper; only years where the cached count differs from the histogram are re-fetched
- **Skip logic**: A year is skipped if `seen_total (cached + dedup) >= probe_count` for that year; a whole paper is skipped if every year satisfies this condition (equivalent to `seen >= scholar_total − unyeared`)
- **Resume**: `partial_year_start` records the exact item offset within the in-progress year, so a retry resumes from the correct page rather than replaying from the beginning

## Development Notes

This repository includes two files that document the AI-assisted development process:

- **`user.md`**: A timestamped log of all user messages from development conversations — shows how requirements evolved entirely through natural language, with the user writing zero code.
- **`WORK_NOTES.md`**: Detailed technical notes, architecture decisions, and bug-fix records accumulated during development.

Both files are committed to git and contain no personally identifiable information.

### Project Structure

```
scholar_citation.py          # CLI entry point + PaperCitationFetcher orchestrator
crawler/
  common.py                  # Constants and stateless utilities
  fetch_context.py           # FetchContext dataclass (per-paper mutable state)
  author_fetcher.py          # AuthorProfileFetcher
  profile_io.py              # Profile JSON / Excel output
  citation_cache.py          # Year-count and diagnostics pure functions
  citation_strategy.py       # Fetch policy, refresh strategy, reconciliation
  citation_identity.py       # Citation dedup key and info extraction
  citation_io.py             # Cache I/O, status derivation, citations Excel output
  citation_fetch.py          # fetch_citations_with_progress + fetch_by_year engine
  scholarly_session.py       # SessionContext + scholarly monkey-patch + year probe
  interactive.py             # cURL cookie injection, captcha prompt, proxy-switch wait
  cli.py                     # parse_args() + _run_main()
tests/                       # 96 unit tests, no network required
```

### Running Tests

```bash
# Run all tests
python -m unittest discover -s tests -p "test_*.py"

# Run a single test file
python -m unittest tests.test_year_fetch_early
```

## License

MIT
