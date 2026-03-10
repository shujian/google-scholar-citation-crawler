# Google Scholar Citation Crawler

A Python tool to crawl Google Scholar author profiles and per-paper citation lists. Supports incremental caching, resume after interruption, and outputs both JSON and Excel files.

## Features

- **Unified Workflow**: Automatically fetches author profile, then crawls per-paper citations in one command
- **Smart Skip**: If total citations and publication count haven't changed since the last run, citation crawling is skipped entirely
- **Incremental Caching**: Only re-fetches citation lists when citation counts change
- **Resume Support**: Interrupted fetches resume from the last checkpoint
- **Rate Limiting**: Randomized 30-60s delays between all requests to avoid Scholar blocks
- **Heartbeat Monitoring**: Detects stalled requests (80s timeout), saves progress and terminates
- **Change Tracking**: Records history of citation changes across runs
- **Dual Output**: Generates both JSON and formatted Excel files

## Requirements

- Python 3.8+
- Dependencies: `scholarly`, `openpyxl`

```bash
pip install -r requirements.txt
```

## Quick Start

```bash
# Fetch profile + citations for an author
python scholar_citation.py --author YOUR_AUTHOR_ID

# You can also use a full Google Scholar URL
python scholar_citation.py --author "https://scholar.google.com/citations?user=YOUR_AUTHOR_ID&hl=en"

# Test with a small batch (only fetch citations for 2 papers)
python scholar_citation.py --author YOUR_AUTHOR_ID --limit 2
```

## CLI Reference

```
usage: scholar_citation.py [-h] --author AUTHOR [--output-dir DIR]
                           [--limit N] [--skip N]

required:
  --author AUTHOR         Google Scholar author ID or full profile URL

optional:
  --output-dir DIR        Output directory (default: ./output)
  --limit N               Only process first N papers needing fetch
  --skip N                Skip first N papers in fetch list
```

## Output Files

After running, you'll find these files in the output directory:

| File | Description |
|------|-------------|
| `author_<ID>_profile.json` | Author info, publication list, and change history |
| `author_<ID>_profile.xlsx` | Excel with Author Overview, Publications, and Change History sheets |
| `author_<ID>_paper_citations.json` | Per-paper citation lists |
| `author_<ID>_paper_citations.xlsx` | Excel with Summary and All Citations sheets |
| `scholar_cache/` | Incremental cache (auto-managed) |

## Rate Limiting & Proxy

Google Scholar aggressively rate-limits automated requests. This tool uses several strategies:

- **Randomized delays**: All waits between requests are randomized (30-60s) to appear human-like
- **Session refresh**: Proactively refreshes session every 5 pages to avoid session-based bot detection
- **Reduced internal retries**: Scholar library retries limited to 2 per page to fail fast
- **Graduated retry**: On failure, waits 3 hours then retries; if still failing, saves progress, waits 6 hours, retries once more; terminates if all attempts fail
- **Page counter**: Tracks total pages fetched for diagnostics

**Proxy support**: Set `http_proxy` / `https_proxy` environment variables. The tool will auto-detect and configure them.

```bash
export https_proxy=http://127.0.0.1:7890
python scholar_citation.py --author YOUR_AUTHOR_ID
```

## Resume After Interruption

If the script is interrupted (Ctrl+C, timeout, or error):

1. Progress is saved to cache automatically
2. Simply re-run the same command to resume
3. Completed papers are skipped, partial papers resume from checkpoint

## License

MIT
