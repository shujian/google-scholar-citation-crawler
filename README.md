# Google Scholar Citation Crawler

A Python tool to crawl Google Scholar author profiles and per-paper citation lists. Supports incremental caching, resume after interruption, and outputs both JSON and Excel files.

> **Developed entirely with [Claude Code CLI](https://github.com/anthropics/claude-code)** — the user wrote zero lines of code. All implementation, debugging, and iteration were driven through natural-language conversation. `user.md` and `WORK_NOTES.md` in this repository document that process in full.

## Features

- **Unified Workflow**: Automatically fetches author profile, then crawls per-paper citations in one command
- **Smart Skip**: If total citations and publication count haven't changed since the last run, citation crawling is skipped entirely
- **Incremental Caching**: Only re-fetches citation lists when Scholar reports more citations; tracks seen/dedup counts so already-complete papers are not re-fetched unnecessarily
- **Dedup Handling**: Automatically deduplicates citations; tolerates count differences caused by Scholar duplicates
- **Resume Support**: Interrupted fetches resume from the last checkpoint; per-year progress is saved so even mid-paper interruptions recover gracefully
- **Year-Based Fetching**: Papers with many citations are fetched year-by-year (newest→oldest for updates, oldest→newest for first fetch/force), with early-stop when enough citations are collected
- **Anti-Ban Strategies**: Randomized delays, mandatory long breaks, browser header simulation, and HTTP/2 to reduce Scholar IP-level rate limiting
- **Interactive Captcha Bypass**: `--interactive-captcha` mode lets you paste a browser cURL to inject real cookies and bypass captchas without restarting the program
- **Proxy Switch Wait**: On failure, prompts you to switch proxy/IP and checks every hour for an "ok" signal to continue
- **Change Tracking**: Records history of citation changes across runs
- **Dual Output**: Generates both JSON and formatted Excel files
- **Run Summary**: Prints total elapsed time, pages accessed, and new citations at the end of each run

## Requirements

- Python 3.8+
- Dependencies: `scholarly`, `openpyxl`, `httpx==0.27.2`

```bash
pip install -r requirements.txt
```

> **Note**: `httpx` is pinned to `0.27.2` for compatibility with `scholarly 1.7.11`. Newer versions break the internal session handling.

## Quick Start

```bash
# Fetch profile + citations for an author
python scholar_citation.py --author YOUR_AUTHOR_ID

# You can also use a full Google Scholar URL
python scholar_citation.py --author "https://scholar.google.com/citations?user=YOUR_AUTHOR_ID&hl=en"

# Test with a small batch (only process papers 3-5, i.e. skip 2, limit 3)
python scholar_citation.py --author YOUR_AUTHOR_ID --skip 2 --limit 3
```

## CLI Reference

```
usage: scholar_citation.py [-h] --author AUTHOR [--output-dir DIR]
                           [--limit N] [--skip N]
                           [--force-refresh-pubs] [--force-refresh-citations]
                           [--interactive-captcha]

required:
  --author AUTHOR               Google Scholar author ID or full profile URL

optional:
  --output-dir DIR              Output directory (default: ./output)
  --skip M                      Skip first M papers in the full list (sorted by citations desc)
  --limit N                     Process exactly N papers starting after --skip M (papers M+1 to M+N),
                                regardless of whether each paper needs fetching
  --force-refresh-pubs          Force re-fetch publications list from Scholar
  --force-refresh-citations     Re-check all papers: re-fetch any where cached count < Scholar count
  --interactive-captcha         Enable interactive captcha bypass (see below)
```

### `--skip` and `--limit`

Papers are always sorted by citation count descending. `--skip M` skips the first M papers in that list. `--limit N` then processes exactly the next N papers (positions M+1 to M+N), whether or not they need fetching. This allows targeting a specific range for debugging or manual recovery.

### `--force-refresh-citations`

In normal mode, a paper is skipped if its Scholar citation count hasn't increased since the last complete fetch. With `--force-refresh-citations`, any paper where `cached count < Scholar count` is re-fetched. Useful when previous runs may have missed citations.

## Output Files

After running, you'll find these files in the output directory:

| File | Description |
|------|-------------|
| `author_<ID>_profile.json` | Author info, publication list, and change history |
| `author_<ID>_profile.xlsx` | Excel with Author Overview, Publications, and Change History sheets |
| `author_<ID>_paper_citations.json` | Per-paper citation lists |
| `author_<ID>_paper_citations.xlsx` | Excel with Summary and All Citations sheets |
| `scholar_cache/` | Incremental cache (auto-managed, not committed to git) |

## Rate Limiting & Anti-Ban Strategies

Google Scholar aggressively rate-limits automated requests, typically banning IPs after 30-40 page accesses. This tool uses several layers of mitigation:

- **Slower randomized delays**: All waits between requests use randomized 45-90s delays
- **Mandatory long breaks**: Every 8-12 pages, a 3-6 minute break is taken to let Scholar's sliding-window rate limit reset
- **Browser header simulation**: Full `sec-fetch-*`, `sec-ch-ua-*`, `user-agent`, `accept`, `accept-language`, and `Referer` headers matching a real Edge 145 browser
- **HTTP/2**: Requests use HTTP/2 (`httpx` with `h2`) for a more authentic browser-like TLS profile
- **Session refresh**: Proactively soft-resets the session every 10-20 pages (clears `got_403` flag, preserves cookies)
- **Fast failure**: Scholar library retries limited to 1 per page to fail fast and reach paper-level retry
- **Dynamic Referer**: Each page request sets the previous page's URL as Referer

**Proxy support**: Set `http_proxy` / `https_proxy` environment variables. The tool automatically uses them via `httpx`'s `trust_env=True`.

```bash
export https_proxy=http://your-proxy-host:port
python scholar_citation.py --author YOUR_AUTHOR_ID
```

## Interactive Captcha Bypass

When Scholar shows a CAPTCHA, the `--interactive-captcha` flag lets you inject real browser cookies without restarting:

```bash
python scholar_citation.py --author YOUR_AUTHOR_ID --interactive-captcha
```

When a block is detected:

1. The program shows you the blocked URL
2. Open that URL in your browser (solve the CAPTCHA if needed)
3. In Chrome/Edge DevTools → Network tab, right-click the request → **Copy as cURL**
4. Paste the cURL into the terminal; the program detects the end automatically (last line has no `\`)
5. Cookies are extracted and injected; the program retries

In interactive mode, the program **never exits automatically** — it will keep asking you to solve captchas or switch proxies until it succeeds.

In non-interactive mode, on failure the program waits up to 24 hours while prompting you hourly to switch your proxy/IP. Type `ok` and press Enter when you've switched, and it will immediately retry.

## Resume After Interruption

If the script is interrupted (Ctrl+C, timeout, or error):

1. Progress is saved to cache automatically (including which years have been fully fetched)
2. Simply re-run the same command to resume
3. Completed papers and completed years within a paper are skipped

## Development Notes

This repository includes two files that document the AI-assisted development process:

- **`user.md`**: A timestamped log of all user messages from development conversations. Shows exactly how requirements evolved — from initial design to bug fixes to feature requests — entirely through natural language, without the user writing any code.
- **`WORK_NOTES.md`**: Detailed technical notes, architecture decisions, bug fixes, and implementation details accumulated during development. Explains *why* the code is structured as it is, useful for contributors and as a record of the AI reasoning process.

Both files are committed to git and contain no personally identifiable information.

## License

MIT
