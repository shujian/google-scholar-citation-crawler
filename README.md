# Google Scholar Citation Crawler

A Python tool to crawl Google Scholar author profiles and per-paper citation lists. Supports incremental caching, resume after interruption, and outputs both JSON and Excel files.

> **Developed entirely with [Claude Code CLI](https://github.com/anthropics/claude-code)** — the user wrote zero lines of code. All implementation, debugging, and iteration were driven through natural-language conversation. `_user.zh.md` and `_work_notes.zh.md` in this repository document that process in full.

## Features

- **Unified Workflow**: Fetches author profile then crawls per-paper citations in one command
- **Smart Skip**: If total citations and publication count haven't changed since the last run, citation crawling is skipped entirely
- **Incremental Caching**: Re-fetches only papers whose cache is incomplete; year-based papers use a per-year histogram to decide which years need refreshing
- **Stall Detection**: A paper whose fetch keeps producing the same result (e.g. Scholar lists fewer citations than it reports) stops being retried after two identical runs, and resumes on its own when Scholar's counts change
- **Year-Based Fetching**: Papers with many citations (configurable threshold, default ≥ 50) are fetched year-by-year (oldest → newest), skipping years whose cached count already matches Scholar's histogram
- **Dedup Handling**: Deduplicates citations using Scholar-native `cites_id` when available, falling back to `title + venue/authors` metadata. A row Scholar never clustered stores a null `cites_id` (never the literal `"None"`). The written output additionally collapses rows that carry the **same real `cites_id`** (the same cluster re-listed across runs, keeping the row with the richest metadata) — and, for rows without one, only rows whose `title + venue` (or `title + authors`) are fully equal. A shared normalised title is deliberately **not** an identity: Scholar reporting one title under two clusters means two entries, and both are written (one index each downstream); the number collapsed is printed per paper
- **Resume Support**: Interrupted fetches resume from the last checkpoint; on exit the in-flight paper's state (citations fetched so far + the live year probe) is written to the output, so a year left short is re-fetched next run instead of being reported complete. A year that was interrupted mid-way keeps its item offset, so the next run continues inside that year rather than re-walking it
- **Consistent sentinels**: an unusable year is always written as `N/A` (older states may still contain `NA` or an empty string; every reader treats all of them as unknown)
- **Two different completeness signals**: `fetch_complete` in the output is the *data* verdict (does every probed year have its rows?), while `_fetch_state.complete_fetch_attempt` records whether the last attempt finished cleanly (a blocked/empty page makes it false). `--fetch-mode rough` reads the latter
- **Resume Support**: Interrupted fetches resume from the last checkpoint; on exit the in-flight paper's state (citations fetched so far + the live year probe) is written to the output, so a year left short is re-fetched next run instead of being reported complete
- **Anti-Ban Strategies**: HTTP/2 via scholarly's native `httpx` client with full browser headers (fingerprint adopted from the injected browser cURL), randomized 45–90 s delays, mandatory 3–6 min breaks every 8–12 pages, browser header simulation
- **Full venue strings (`venue_full`)**: the citation-list pages truncate venues (`Proceedings of the 2024 Conference of the North American Chapter of the …`). Each publication's Scholar *detail* page (`citations?view_op=view_citation&citation_for_view=…`) is therefore fetched once per run to read the untruncated value — one request per publication (hundreds), not per citing entry (thousands). It is stored in a **new** field `pub['venue_full']` (plus `pub['venue_full_source'] = "scholar_detail"` and the detail-page token `pub['citation_for_view']`); `venue` itself is never rewritten, because it takes part in identity keys downstream. Publications whose `venue` is **already complete** (non-empty, untruncated, not a preprint source) are skipped without a request and without a cache entry The requests go through the same patched session (same waits, `--accelerate` scaling, captcha recovery, proxy prompt), and parsed pages are cached in `output/venue_detail_cache.json` — so an interrupted run resumes and a later run asks for nothing twice
- **Venue identity keys (`venue_key`)**: every publication and every citing entry carries a normalized `venue_key` — the venue name plus the year (`ACL2026`, `EMNLP2025`, `Findings of ACL2026`, `NeurIPS2024`) — so downstream stages can tell "same title, different venue" apart. Names come from the same vocabulary stage 3.a.2 uses (spelled-out proceedings titles resolve to their acronym, TACL stays the journal, and **Findings is a venue of its own**, not its host conference). Venues outside the table keep their own text as the key with `venue_key_source: "raw"`, which marks them as reviewable rather than normalized; a row without a venue gets no key at all
- **Citation formats  (on by default; `--no-cite-formats` skips it)**: every citing entry keeps its Scholar cluster id as `cid`, and the entry's Cite dialog (`scholar?q=info:<cid>:scholar.google.com/&output=cite`) gives all five formats — MLA / APA / **Chicago** (the **full author list with given names**) / Harvard / **Vancouver** (the **untruncated venue** + volume/issue/pages) — stored in the additive `citation['cite']` field. It runs by default because for a *citing* paper this dialog is the only source of both: the citation-list page truncates the venue and abbreviates every author. It costs one request per citing entry, cached in `output/cite_format_cache.json`; an entry that already carries a parsed Chicago venue matching the venue seen online is skipped **without a request** (that is the common case on re-runs: the record already holds the full author list and the untruncated venue, so a mismatch -- a changed venue, say -- is the only reason to fetch again), and an entry whose venue is already complete **and** whose authors are already spelled out in full is skipped too (those two things are all the dialog returns), and entries without a `cid` (rows written before the field existed) cannot be addressed until the paper is re-fetched
- **Interactive Captcha Bypass**: `--interactive-captcha` lets you paste a browser cURL to inject real cookies and resume without restarting; if playwright is installed you can type `browser` at the prompt instead and let it harvest the cookies from a real browser window
- **Proxy Switch Wait**: On non-interactive failure, prompts hourly to switch proxy/IP until you type `ok` (the 24 h window scales with `--accelerate`)
- **Change Tracking**: Records per-run citation-count history in the profile JSON
- **Dual Output**: JSON and formatted Excel for both author profile and citations
- **Run Summary**: Prints elapsed time, total pages accessed, and new citations at the end of each run

## Requirements

- Python 3.8+
- Dependencies: `scholarly>=1.7, openpyxl>=3.1, httpx[http2]==0.27.2`

```bash
pip install -r requirements.txt
```

> **Note**: `httpx` is pinned to `0.27.2` for compatibility with `scholarly 1.7.11`. Newer versions break internal session handling. The `[http2]` extra is required: the crawler builds its sessions with `httpx.Client(http2=True)`, which raises `ImportError` when the `h2` package is missing.
>
> **Note**: A `NO_PROXY` / `no_proxy` entry that wraps an IPv6 literal in brackets (e.g. `[::1]`, common on macOS when a local proxy is running) is normalised automatically before `scholarly` is imported (`code/lib_env.py`). Without that, `httpx 0.27.2` raises `InvalidURL: Invalid port: ':1]'` while scholarly builds its client at import time, and the crawler cannot start at all.
>
> **Note**: The crawler uses scholarly's native `httpx` client (HTTP/2) rather than a TLS-impersonation library. `curl_cffi` was previously used but Scholar's citation-search endpoint (`/scholar?cites=`) returns 429 for every `curl_cffi` impersonation target, while `httpx` works.

## Quick Start

Run from the repository root. The entry point is a module (`code/scholar_citation.py`), so use
`python -m code.scholar_citation` — running the file path directly would shadow the standard-library
`code` module.

```bash
# Fetch profile + citations for an author
python -m code.scholar_citation --author YOUR_AUTHOR_ID

# Also accepts a full Google Scholar profile URL
python -m code.scholar_citation --author "https://scholar.google.com/citations?user=YOUR_AUTHOR_ID&hl=en"

# Process only papers 3–5 (skip 2, limit 3) — useful for testing
python -m code.scholar_citation --author YOUR_AUTHOR_ID --skip 2 --limit 3
```

## CLI Reference

```
usage: python -m code.scholar_citation [-h] --author AUTHOR [--output-dir DIR]
                                       [--config PATH] [--proxy-url URL]
                                       [--no-proxy LIST] [--skip M] [--limit N]
                                       [--fetch-mode {rough,normal,exact,force}]
                                       [--interactive-captcha] [--cite-formats]
                                       [--accelerate SCALE] [-v]

required:
  --author AUTHOR               Google Scholar author ID or full profile URL

optional:
  --output-dir DIR              Output directory (default: ./output)
  --config PATH                 JSON config file (default: crawler_config.json in the
                                project root; a missing default file is ignored)
  --proxy-url URL               Proxy URL, e.g. http://127.0.0.1:7890; overrides the config
                                file. Pass an empty string to connect directly
  --no-proxy LIST               Comma-separated proxy bypass list, e.g. localhost,127.0.0.1,::1
  --skip M                      Skip first M papers (sorted by citations desc); must be >= 0
  --limit N                     Process exactly N papers after --skip M; must be >= 1
  --fetch-mode {rough,normal,exact,force}
                                Controls re-fetch aggressiveness (default: normal)
  --interactive-captcha         Enable interactive captcha bypass (see below)
  --cite-formats | --no-cite-formats
                                Resolve each citing entry's five citation formats
                                (MLA/APA/Chicago/Harvard/Vancouver) in
                                citation["cite"]; one request per entry, cached.
                                ON by default (the dialog is the only source of a
                                citing paper's full authors + untruncated venue)
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
python -m code.scholar_citation --author YOUR_AUTHOR_ID --fetch-mode rough

# Catch year reclassification drift
python -m code.scholar_citation --author YOUR_AUTHOR_ID --fetch-mode exact --skip 0 --limit 5

# Re-fetch papers 1–5 from scratch
python -m code.scholar_citation --author YOUR_AUTHOR_ID --fetch-mode force --skip 0 --limit 5
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
| `venue_detail_cache.json` | Publication detail pages already parsed (`citation_for_view` token → full venue). Makes the `venue_full` step resumable; safe to delete to re-read every page |
| `cite_format_cache.json` | Cite dialogs already parsed (row `cid` → the five formats + `venue_text` / `authors_full`). Makes `--cite-formats` resumable; safe to delete to re-read every dialog |
| `author_<ID>_papers/` | **The pipeline's shared papers tree** (physical home since 2026-10-15): `<NNN>-<slug>/` per publication with the PDFs, `citations_meta.json`, `enrich_info/`, `analysis_info/`, `analysis_content/`, `.shared/work/`, `progress.json`, `old/`. Created here (idempotently); every later stage reaches it through its own `papers` symlink, never by this path |
| `output/logs/author_<ID>_run_<ts>.log` | Full log of each run (mirrors stdout) |

## Full Venue Strings (`venue_full`)

Scholar's citation-list pages truncate the venue (`Proceedings of the 2024
Conference of the North American Chapter of the …`), so the profile step ends by
reading each publication's **detail page** once
(`citations?hl=en&view_op=view_citation&citation_for_view=<author>:<token>`),
where the field is spelled out in full:

```json
{
  "venue": "Proceedings of the 2024 Conference of the North American Chapter of the …, 2024",
  "venue_full": "Proceedings of the 2024 Conference of the North American Chapter of the Association for Computational Linguistics: Human Language Technologies (Volume 1: Long Papers)",
  "venue_full_source": "scholar_detail",
  "citation_for_view": "HF3-E9kAAAAJ:xtoqd-5pKcoC"
}
```

Notes:

- One request per **publication** (hundreds), not per citing entry (thousands) — and **none** for a publication whose `venue` is already complete (present, untruncated, not a preprint source), so the pass only pays for the rows that can still improve. The trade-off is deliberate: the detail page could also supply volume/pages for an already-complete venue, and those rows no longer get it.
- Requests go through the same patched session as everything else, so the
  standard delays, `--accelerate` scaling, captcha recovery and proxy wait all
  apply. `output/venue_detail_cache.json` records every parsed page, so an
  interrupted run resumes and a later run issues no request for a page it
  already has. A page whose request failed is **not** cached and is retried next
  run; a publication that cannot be resolved simply keeps `venue_full: ""` and
  the rest of the run continues.
- **`venue` is never overwritten.** It takes part in metadata identity keys
  (`code/citation_identity.py`, and the downstream stages' identity checks), so
  rewriting it would drift keys that historical data was analysed under.
  `venue_full` is purely additive.

## Venue Identity Keys (`venue_key`)

Downstream dedup/uniqueness checks need to know *which venue* a record belongs
to, and Scholar writes the same venue in a dozen shapes (acronym, spelled-out
proceedings title, journal form, truncated). Both the profile's publications and
every citing entry therefore carry a normalized key: **venue name + year**.

```json
{
  "venue": "Proceedings of the 2024 Conference of the North American Chapter of the …, 2024",
  "venue_full": "Proceedings of the 2024 Conference of the North American Chapter of the Association for Computational Linguistics: Human Language Technologies (Volume 1: Long Papers)",
  "venue_key": "NAACL2024",
  "venue_key_source": "normalized"
}
```

- **Preprints are keyed by their server**: every arXiv spelling — `arXiv preprint
  arXiv:2402.18567`, a truncated `arXiv preprint arXiv …`, `arxiv.org/abs/…` —
  becomes `arXiv`; the same holds for `bioRxiv`, `medRxiv`, `SSRN`, `TechRxiv`,
  `Research Square` (including the `researchsquare.com` domain form) and
  `preprints.org`.  No year is added: the server *is* the venue.  A row that only
  says "Preprint" names no server and keeps that text.
- **There is no bare `Findings` venue**: a Findings volume that names its host
  keeps it (`Findings of EMNLP2024`), and one that does not is treated as
  **`Findings of ACL`**.
- **Conferences carry the year, journals do not**: a conference key is the
  acronym plus the edition year (`ACL2026`, `ICASSP2025`, `EMNLP2024`) because the
  year identifies the edition; a journal is a continuous venue, so its key is the
  short name alone (`TACL`, `TASLP`, `Nature Machine Intelligence`,
  `Neurocomputing`).
- **Findings is its own venue**: `Findings of the Association for Computational
  Linguistics: ACL 2026` → `Findings of ACL2026`, never `ACL2026`.
- The vocabulary started from stage 3.a.2's table (`VENUE_PRESTIGE` +
  `VENUE_ALIASES`: 51 conferences + 30 journals) and was extended to **167 venues
  (84 conferences + 83 journals)** with the names the real data left unrecognised —
  speech / machine translation / NLP / ML conferences (`ICASSP`, `INTERSPEECH`,
  `IJCNN`, `PAKDD`, `LREC`, `CCMT`, `CWMT`, `WMT`, `NLPCC`, `PACLIC`, `IALP` …) and
  journals (`TASLP`, `TALLIP`, `TKDD`, `JAIR`, `IEEE Access`, `Neurocomputing`,
  `中文信息学报` …). Spelled-out proceedings resolve to their acronym, `TACL`
  (journal) is not swallowed by `ACL` (conference), `CCMT` / `CWMT` / `WMT` stay
  apart, and the two spellings `NIPS` / `ACMMM` collapse onto `NeurIPS` / `ACM MM`.
- The year is the first 4-digit year inside the venue string (the conference
  edition), falling back to the record's own `year`.
- **A citing entry prefers the Cite dialog's venue**: the parsed `citation['cite'].venue_text` (or a legacy `bibtex` field)
  carries `journal` / `booktitle` / `series` untruncated, so `venue_key` is derived
  from it whenever the dialog data is present, and only falls back to the
  citation-list `venue` (cut off at ~25 characters) otherwise.  The five formatted
  strings are deliberately not parsed — the venue sits between the authors, the
  title and the volume there, and reading the title as the venue would
  mis-normalize the key.
- **Unknown venues keep their own text** (`venue_key_source: "raw"`) so the values
  are reviewable; a row with no venue at all (empty / `NA` / `N/A`) gets
  `venue_key: null`, because a key of `NA` would merge unrelated records. Citation
  list pages truncate venues aggressively, so many citing entries stay `raw` until
  the table is extended — filter on `venue_key_source == "normalized"` when a key
  must be trustworthy.
- Publications derive the key from `venue_full or venue` (the detail page resolves
  what the list page cut off); citing entries use their own `venue`.
- Real data (author `HF3-E9kAAAAJ`): publications **229 normalized / 8 raw** / 4 without
  a venue; citing entries **2,550 normalized / 5,257 raw** out of 9,028 (1,221 have no
  venue at all).  The raw remainder is dominated by venues Scholar truncated to ~25
  characters (`Proceedings of the …` 556, `Findings of the …` 207,
  `IEEE Transactions on …` 138), which no table can restore.  A host name is not a
  venue either: `kdd.org` / `iclr.cc` no longer resolve to the KDD / ICLR
  conferences.
- Existing output files can be filled in (and re-derived after a table change)
  without re-crawling:

  ```bash
  python -m code.data_upgrade.normalize_venue_key                    # dry-run + summary
  python -m code.data_upgrade.normalize_venue_key --review-file output/venue_key_review.json
  python -m code.data_upgrade.normalize_venue_key --apply            # write + backup + manifest
  ```

  The tool only writes the two derived fields, backs the previous files up under
  `output/venue_key_backup_<stamp>/`, is idempotent, and its report lists every
  distinct *unrecognised* venue with its count so the table can be extended
  deliberately.

## Citation Formats (on by default; `--no-cite-formats` skips it) — BibTeX abandoned 2026-10-15

Every citing entry keeps the Scholar cluster id of its result row as
`citation['cid']` (the row's `data-cid`; scholarly exposes it as
`url_scholarbib`). It is **not** `cites_id`: that one identifies the *cited*
work's cluster and takes part in identity keys, while `cid` addresses that row's
Cite dialog:

```
scholar?q=info:<cid>:scholar.google.com/&output=cite&scirp=0&hl=en
```

One request returns all five formats. (BibTeX was abandoned on 2026-10-15: its signed link is minted per dialog render, so a cached one is always stale.)
They are stored in a new, purely additive field (this runs by default — pass
`--no-cite-formats` to skip the requests, at the cost of keeping the list page's
abbreviated authors and truncated venue).  Storing them also refreshes
`venue_key` from the BibTeX venue, which is the untruncated one:

```json
{
  "title": "…", "authors": "H Wang, F Zhang", "venue": "Proceedings of the 2025 …",
  "cites_id": "3724674774839834281", "cid": "nYGTsUfPxnMJ",
  "cite": {
    "mla": "…", "apa": "…",
    "chicago": "Zhang, Jie, Haoyu Bu, …, and Dan Meng. \"…\" Cybersecurity 8, no. 1 (2025): 55.",
    "harvard": "…",
    "vancouver": "Zhang J, Bu H, …, Meng D. …. Cybersecurity. 2025 Feb 5;8(1):55.",
    "bibtex": {
      "entry_type": "article", "key": "zhang2025llms",
      "author": "Zhang, Jie and Bu, Haoyu and … and Meng, Dan",
      "journal": "Cybersecurity", "volume": "8", "number": "1", "pages": "55", "year": "2025"
    }
  }
}
```

Notes:

- **Off by default.** It costs one request per citing entry (plus one for
  BibTeX) — thousands for a real profile — so a normal run never pays for it.
  `output/cite_format_cache.json` records every parsed dialog, so a repeat run
  issues no request for a page it already has.
- **Skipped when there is nothing to gain**: a row whose `venue` is already
  complete **and** whose `authors` are already spelled out in full is not
  requested at all (no cache entry either). Rows that already have a cached
  dialog are still served from the cache, so re-running cannot drop `cite`.
- **Additive only.** `title` / `authors` / `venue` / `year` / `url` / `cites_id`
  are never rewritten (`venue` and `cites_id` take part in downstream identity
  keys); whatever a dialog does not provide is simply absent from `cite`.
- Requests reuse the patched session (standard delays, captcha recovery, proxy
  wait), but this path takes the per-request delay **without** the mandatory
  3–6 min break that citation-list pagination applies — the same property that
  got the detail-page pass rate-limited in October 2026 (see `issues.md`).
- A page that fails (request error, captcha/consent page, no citation table) is
  **not** cached and is retried next run. When only the BibTeX download fails,
  the dialog stays cached together with its signed URL, so the retry costs one
  request instead of two.

## Rate Limiting & Anti-Ban Strategies

Google Scholar aggressively rate-limits automated requests. This tool uses multiple mitigation layers:

- **Randomized delays**: 45–90 s between requests; the **detail-style paths** — publication detail pages, citing entries' Cite dialogs and their signed BibTeX download — run at **1/10 of that (~4.5–9 s)** via the single `code.config.DETAIL_PAGE_DELAY_FACTOR` setting. `--accelerate` still scales everything on top; citation-list pagination keeps the full delay and is the only path with the mandatory breaks. Honest cost: Scholar rate-limits by volume, so the shorter delay reaches the block sooner in wall-clock terms (the first venue pass was blocked after ~75–88 pages); the disk cache plus resume is what makes it worthwhile — stop at the first blocked page, come back later, and far less wall-clock goes into waiting
- **Mandatory long breaks**: Every 8–12 pages, a 3–6 minute break resets Scholar's sliding-window rate limit
- **Browser header simulation**: Full `sec-fetch-*`, `sec-ch-ua-*`, `user-agent`, `accept`, `accept-language`, and dynamic `Referer` headers sent over HTTP/2 via scholarly's native `httpx` client. When a browser cURL is injected, the whole browser fingerprint (user-agent + `sec-ch-ua` + `sec-ch-ua-full-version-list`, enforced as one coherent unit) is adopted from that cURL so it always matches the browser that issued the cookies; a built-in Chrome 145 default is only the cold-start fallback
- **Session refresh**: Soft-resets the session every 10–20 pages (preserves cookies, clears `got_403` flag)
- **Fast failure**: `scholarly` retries limited to 1 per page so failures reach the paper-level retry quickly
- **Rate-limit cooldown**: 429 responses trigger a 5–10 min cooldown instead of a captcha prompt

**Proxy support**: the program itself requires **no environment variables**. Proxy settings resolve with
the priority **command-line argument > `crawler_config.json` > system default**.

```bash
# 1. Command line (highest priority)
python -m code.scholar_citation --author YOUR_AUTHOR_ID --proxy-url http://your-proxy-host:port \
    --no-proxy localhost,127.0.0.1,::1

# 2. config file: crawler_config.json in the project root (gitignored, machine-local).
#    Pass --config PATH to point somewhere else.
cat crawler_config.json
{
  "proxy_url": "http://127.0.0.1:7890",
  "no_proxy": "localhost,127.0.0.1,::1"
}

# 3. system default (conventional HTTP behaviour, also read by httpx/requests)
export https_proxy=http://your-proxy-host:port
python -m code.scholar_citation --author YOUR_AUTHOR_ID
```

Remove a key (or set it to `null`) to fall back to the next level; pass an empty string to force
"no proxy". The resolved values are exported into the process environment before crawling, because
`httpx` (and therefore `scholarly`) only understands proxies through the environment.

## Interactive Captcha Bypass

When Scholar shows a CAPTCHA, `--interactive-captcha` lets you inject real browser cookies without restarting:

```bash
python -m code.scholar_citation --author YOUR_AUTHOR_ID --interactive-captcha
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

- After each **completed** fetch the result is signed by the number of rows it
  obtained (e.g. `direct:0`, or `year:241`) and stored together with how many
  completed traversals in a row produced it. Scholar's own counts are
  deliberately *not* part of the signature: they drift between runs even when
  the listing does not (one paper's 2026 bucket alternated 190/200 ↔ 194/201
  within a day), which used to make every run look like a new outcome and kept
  such a paper in the plan forever.
- Once **two completed traversals in a row obtain the same number of rows**, the
  paper is marked `stalled` and skipped: it stays in the output with whatever was
  collected, the run summary prints `Stalled (skip): N`, and the plan lists
  `Skipping N stalled paper(s)`. It is parked even though it still looks
  incomplete — two full walks of the same list returned the same rows.
- The stall clears itself when the paper's Scholar count grows (the one event
  that can add rows), and whenever the number of rows obtained changes.
- A blocked or truncated attempt (and Ctrl+C) is never folded into the counter,
  so it neither counts as a repeat nor wipes one: a transient failure cannot
  stall a paper by itself, and cannot postpone a genuine repeat either.

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

All code lives in the `code/` package; the entry point is run as a module from the project root:

```
code/
  scholar_citation.py        # CLI entry point + PaperCitationFetcher orchestrator
  common.py                  # Constants and stateless utilities
  lib_env.py                 # Third-party env staging + NO_PROXY normalise + proxy env bridge
  config.py                  # Centralised constants + crawler_config.json / proxy resolution
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
  venue_detail.py            # venue_full from the publication detail page (+ its cache)
  venue_quality.py           # is this venue complete / is it only a preprint source / full names?
  cite_format.py             # five citation formats + BibTeX from the row's Cite dialog (+ its cache)
  log_utils.py               # setup_logging() / get_logger()
  cli.py                     # parse_args() only — the entry point is code.scholar_citation.main()
  data_upgrade/              # one-off data repair/upgrade tools — kept apart from the main entry point
    __init__.py
    fix_diag_from_year_record.py  # python -m code.data_upgrade.fix_diag_from_year_record <output_json>
    fix_output_fetch_state.py     # python -m code.data_upgrade.fix_output_fetch_state [output_dir]
  examples/run_example.sh    # minimal example invocation
output/                      # crawl products (author_*.json/.xlsx) + logs/
tests/                       # 418 unit tests, no network required
run.sh                       # thin launcher: conda-aware python -m code.scholar_citation
crawler_config.json          # machine-local runtime config (gitignored)
```

> Run the entry point as a module from the project root (`python -m code.scholar_citation ...`).
> Do **not** run `python code/scholar_citation.py`: the `code/` package directory would shadow
> the standard-library `code` module.

### Running Tests

```bash
# Run all tests
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests

# Run a single test file
PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_year_fetch_early
```

## License

MIT
