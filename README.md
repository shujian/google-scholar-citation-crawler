# Google Scholar Citation Crawler

A Python tool to crawl Google Scholar author profiles and per-paper citation lists. Supports incremental caching, resume after interruption, and outputs both JSON and Excel files.

> **Developed entirely with [Claude Code CLI](https://github.com/anthropics/claude-code)** — the user wrote zero lines of code. All implementation, debugging, and iteration were driven through natural-language conversation. `_user.zh.md` and `_work_notes.zh.md` in this repository document that process in full.

## Features

- **Unified Workflow**: Fetches author profile then crawls per-paper citations in one command
- **Smart Skip**: If total citations and publication count haven't changed since the last run, citation crawling is skipped entirely
- **Incremental Caching**: Re-fetches only papers whose cache is incomplete; year-based papers use a per-year histogram to decide which years need refreshing
- **Stall Detection**: A paper whose fetch keeps producing the same result (e.g. Scholar lists fewer citations than it reports) stops being retried after two identical runs, and resumes on its own when Scholar's counts change
- **Year-Based Fetching**: Papers with many citations (configurable threshold, default ≥ 50) are fetched year-by-year (oldest → newest), skipping years whose cached count already matches Scholar's histogram
- **Dedup Handling**: Deduplicates citations using Scholar-native `cites_id` when available, falling back to `title + venue/authors` metadata; tolerates Scholar self-duplicates
- **Resume Support**: Interrupted fetches resume from the last checkpoint; on exit the in-flight paper's state (citations fetched so far + the live year probe) is written to the output, so a year left short is re-fetched next run instead of being reported complete
- **Anti-Ban Strategies**: HTTP/2 via scholarly's native `httpx` client with full browser headers (fingerprint adopted from the injected browser cURL), randomized 45–90 s delays, mandatory 3–6 min breaks every 8–12 pages, browser header simulation
- **Interactive Captcha Bypass**: `--interactive-captcha` lets you paste a browser cURL to inject real cookies and resume without restarting
- **Proxy Switch Wait**: On non-interactive failure, prompts hourly to switch proxy/IP until you type `ok`
- **Change Tracking**: Records per-run citation-count history in the profile JSON
- **Dual Output**: JSON and formatted Excel for both author profile and citations
- **Run Summary**: Prints elapsed time, total pages accessed, and new citations at the end of each run

## Requirements

- Python 3.8+
- Dependencies: `scholarly>=1.7, openpyxl>=3.1, httpx==0.27.2`

```bash
pip install -r requirements.txt
```

> **Note**: `httpx` is pinned to `0.27.2` for compatibility with `scholarly 1.7.11`. Newer versions break internal session handling.
>
> **Note**: A `NO_PROXY` / `no_proxy` entry that wraps an IPv6 literal in brackets (e.g. `[::1]`, common on macOS when a local proxy is running) is normalised automatically before `scholarly` is imported. Without that, `httpx 0.27.2` raises `InvalidURL: Invalid port: ':1]'` while scholarly builds its client at import time, and the crawler cannot start at all.
>
> **Note**: The crawler uses scholarly's native `httpx` client (HTTP/2) rather than a TLS-impersonation library. `curl_cffi` was previously used but Scholar's citation-search endpoint (`/scholar?cites=`) returns 429 for every `curl_cffi` impersonation target, while `httpx` works.

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
                           [--fetch-mode {rough,normal,exact,force}]
                           [--interactive-captcha] [--accelerate SCALE] [-v]

required:
  --author AUTHOR               Google Scholar author ID or full profile URL

optional:
  --output-dir DIR              Output directory (default: ./output)
  --skip M                      Skip first M papers (sorted by citations desc); must be >= 0
  --limit N                     Process exactly N papers after --skip M; must be >= 1
  --fetch-mode {rough,normal,exact,force}
                                Controls re-fetch aggressiveness (default: normal)
  --interactive-captcha         Enable interactive captcha bypass (see below)
  --accelerate SCALE            Scale all deliberate waits (e.g. 0.1 = 10× faster);
                                must be > 0. Waits exist to avoid Scholar's rate
                                limits, so pair it with --interactive-captcha
  -v, --verbose                 Debug-level logging (also mirrored into the run log)
```

Out-of-range values are rejected up front (`--skip -1`, `--limit 0`,
`--accelerate 0`), so a typo cannot silently clear state or turn a "process
nothing" request into "process everything".

### `--skip` and `--limit`

Papers are sorted by citation count descending. `--skip M` skips the first M papers; `--limit N` then processes the next N papers (positions M+1 to M+N), regardless of whether each needs fetching. Useful for targeting a specific range for debugging or manual recovery.

### `--fetch-mode`

| Mode | Behavior |
|------|----------|
| `rough` | Skip papers whose Scholar count hasn't changed since the last fetch **and whose last fetch ran to completion**. Use when you only care about truly new citations and want to trust an incomplete cache. |
| `normal` *(default)* | Re-fetch any paper whose cache is missing or incomplete, using `seen >= probe` to decide year-level completeness (conservative). |
| `exact` | Like `normal`, but also re-fetch years where `seen > probe` (cached count exceeds histogram), rebuilding those year buckets from Scholar. Catches citation reclassification between years (and, being authoritative, may drop rows Scholar no longer lists under that year). |
| `force` | Clear the output state and re-fetch from scratch. Recommended with `--skip`/`--limit` to limit scope. |

```bash
# Only fetch papers where Scholar count actually changed
python scholar_citation.py --author YOUR_AUTHOR_ID --fetch-mode rough

# Catch year reclassification drift
python scholar_citation.py --author YOUR_AUTHOR_ID --fetch-mode exact --skip 0 --limit 5

# Re-fetch papers 1–5 from scratch
python scholar_citation.py --author YOUR_AUTHOR_ID --fetch-mode force --skip 0 --limit 5
```

## Output Files

Both JSON files are written atomically (temp file + rename), so an interrupted
write cannot leave a truncated state file behind; a file that is still
unreadable is backed up as `<name>.corrupt` and reported. The Excel workbooks
are convenience views — if one cannot be written the run keeps going and only
prints a warning, and the JSON is left untouched.

| File | Description |
|------|-------------|
| `author_<ID>_profile.json` | Author info, publication list, and change history |
| `author_<ID>_profile.xlsx` | Excel: Author Overview, Publications, Change History |
| `author_<ID>_paper_citations.json` | Per-paper citation lists |
| `author_<ID>_paper_citations.xlsx` | Excel: Summary, All Citations, Run Metadata |
| `output/logs/author_<ID>_run_<ts>.log` | Full log of each run (mirrors stdout) |

## Rate Limiting & Anti-Ban Strategies

Google Scholar aggressively rate-limits automated requests. This tool uses multiple mitigation layers:

- **Randomized delays**: 45–90 s between requests
- **Mandatory long breaks**: Every 8–12 pages, a 3–6 minute break resets Scholar's sliding-window rate limit
- **Browser header simulation**: Full `sec-fetch-*`, `sec-ch-ua-*`, `user-agent`, `accept`, `accept-language`, and dynamic `Referer` headers sent over HTTP/2 via scholarly's native `httpx` client. When a browser cURL is injected, the whole browser fingerprint (user-agent + `sec-ch-ua` + `sec-ch-ua-full-version-list`, enforced as one coherent unit) is adopted from that cURL so it always matches the browser that issued the cookies; a built-in Chrome 145 default is only the cold-start fallback
- **Session refresh**: Soft-resets the session every 10–20 pages (preserves cookies, clears `got_403` flag)
- **Fast failure**: `scholarly` retries limited to 1 per page so failures reach the paper-level retry quickly
- **Rate-limit cooldown**: 429 responses trigger a 5–10 min cooldown instead of a captcha prompt

**Proxy support**: Set `https_proxy` / `http_proxy` environment variables.

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

If the pasted request contains no cookies (a common mis-copy, e.g. the wrong
resource from DevTools), the program re-prompts you to paste again instead of
giving up. Press Enter to skip pasting and fall back to automatic waiting.

In interactive mode, the program **never exits on failure** — it keeps prompting until you solve the captcha or switch proxies. With no terminal to prompt on (cron, `nohup`, redirected stdin) the prompt is skipped and the program falls back to automatic waiting.

In non-interactive mode each page failure goes through two quick retries and then 5–10 minute rate-limit cooldowns. Once three cooldowns have not helped, the program waits up to 24 hours, prompting hourly to switch your proxy/IP; type `ok` and press Enter to retry immediately. If that wait times out (or stdin is not a terminal, so nobody could ever answer), the page error is returned to the paper-level retry budget instead of looping here forever — so an unattended run finishes with a clear failure rather than spinning indefinitely.

## Resume After Interruption

If the script is interrupted (Ctrl+C, timeout, or error):

1. Progress is saved automatically, and on exit the in-flight paper's partial
   state is written to the output file — including the citations fetched so far
   and the live year probe, so a year that was left short stays visible
2. Simply re-run the same command to resume
3. Completed papers and completed year segments within a paper are skipped

## Stalled Papers (Same Result Repeated)

Some papers can never be completed by retrying: Scholar's own citation list
returns fewer rows than its "Cited by" count, so every attempt ends with the
same numbers. The crawler now notices this:

- After each fetch the outcome is signed (e.g. `direct:0/49`, or
  `year:241/235=2[2026:64/66]` for year mode, which names the short years) and
  the signature is stored together with how many runs in a row produced it.
- Once **two consecutive runs produce the same signature**, the paper is marked
  `stalled` and skipped: it stays in the output with whatever was collected, the
  run summary prints `Stalled (skip): N`, and the plan lists
  `Skipping N stalled paper(s)`.
- The stall clears itself as soon as the outcome changes — a larger Scholar
  count changes the signature — so such papers are picked up again when
  Scholar's numbers move.
- An *interrupted* fetch (block, captcha, Ctrl+C) never reaches the recording
  step, so a transient failure cannot stall a paper by itself.

To force a retry for a specific paper, clear its state with
`--fetch-mode force --skip <N-1> --limit 1`.

## Diagnosing Empty Pages

A citation request can succeed (HTTP 200, no exception) yet come back with no
result rows — an empty result set, a consent page, or an unrecognised
interstitial. That used to be logged silently, so such a paper just looked like
it had no citations and was re-fetched forever with no explanation. It is now
recorded:

- `Page items: 0 (no citation rows) [<evidence>]` — printed for a citation page
  with no rows, with the evidence: whether the results container was present, the
  result-count banner text, and markers (`captcha`, `consent`, `unusual_traffic`,
  `no_results`). `scholarly` already rejects captcha pages itself, so a line
  without markers means the page was accepted but simply had no rows.
- `termination=empty_first_page` — a direct-mode list returned no rows while
  Scholar reports citations for the paper
- `termination=empty_page` — a probed year (histogram says N > 0) returned no rows

An empty page still records "no new citations" for this run, but it is no longer
allowed to destroy data: the year (or paper) it belongs to keeps the citations
already cached and stays incomplete, so the next run retries it.

## Year-Based Fetch Details

Papers with ≥ 50 **total** citations switch to year-by-year fetch mode:

- **Direction**: Always oldest → newest
- **Selective refresh**: Scholar's year histogram is probed once per paper; only years where the cached count differs from the histogram are re-fetched
- **A fetch never deletes citations it did not return**: a year whose page comes
  back empty (blocked/consent/interstitial) keeps its cached citations and stays
  incomplete, and a full re-scan that returns fewer rows than the cache is merged
  with the cached rows instead of replacing them. Only `--fetch-mode exact`
  (and a `force` re-fetch) rebuilds a year bucket from Scholar.
- **Skip logic**: 
  - **Per-year**: A year is skipped if `seen_total (cached + dedup) >= histogram_count` for that year
  - **Probe authority**: a cached year that is *absent* from the histogram is only skipped when the probe actually produced per-year counts. When the probe fails, an absent year is not evidence of completeness, so the year is fetched.
  - **Paper-level (year mode)**: complete only when **every** year covers its own `histogram_count`. The aggregate `histogram_total <= seen_total` alone is not enough — years whose bucket exceeds the probe inflate the total and can hide a different year that is still short
  - **Paper-level (direct mode)**: complete when `scholar_total <= seen_total`
- **Resume**: `partial_year_start` records the item offset within the in-progress year and is persisted with the fetch state, so an interrupted year resumes at that offset on the next run instead of re-walking from item 0 (its page is page-aligned and already-collected rows are skipped). Direct mode uses a page-aligned `direct_resume_state` while the paper is in flight; a fresh run still re-walks a direct list from the head (its length is small enough that this is cheaper than trusting a stale offset). A completed fetch never deletes rows it did not return: a smaller result is merged with the cached rows unless `--fetch-mode exact` asked for an authoritative rebuild.

## Development Notes

This repository includes three files that document the AI-assisted development process:

- **`_user.zh.md`**: A timestamped, sequentially numbered log of all user messages — shows how requirements evolved entirely through natural language, with the user writing zero code.
- **`_update_history.zh.md`**: Chronological record of every feature, fix, and refactor with clear date headings.
- **`_work_notes.zh.md`**: Technical reference — architecture decisions, caching design, scholarly internals, and key bug records.

Both files are committed to git and contain no personally identifiable information.

### Project Structure

```
scholar_citation.py          # CLI entry point + PaperCitationFetcher orchestrator
crawler/
  common.py                  # Constants and stateless utilities
  fetch_session.py           # BatchFetchSession, YearFetchSession
  page_visit.py              # PageVisit — per-page error recovery
  author_fetcher.py          # AuthorProfileFetcher
  profile_io.py              # Profile JSON / Excel output
  citation_cache.py          # Year-count and diagnostics pure functions
  citation_strategy.py       # Fetch policy, refresh strategy, reconciliation
  citation_identity.py       # Citation dedup key and info extraction
  citation_io.py             # Cache I/O, status derivation, citations Excel output
  citation_fetch.py          # fetch_citations_with_progress + fetch_by_year engine
  scholarly_session.py       # SessionContext + scholarly monkey-patch + year probe
  interactive.py             # cURL cookie injection, captcha prompt, proxy-switch wait
  citation_models.py         # YearRecord, ResumeState, FetchPolicy dataclasses
  output_state.py            # PaperFetchState / PaperState + output file _fetch_state read/write
  pub_info.py                # PubInfo dataclass for publication field normalization
  cli.py                     # parse_args() only — the entry point is scholar_citation.main()
tests/                       # 174 unit tests, no network required
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
