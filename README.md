# Google Scholar Citation Crawler

A Python tool to crawl Google Scholar author profiles and per-paper citation lists. Supports incremental
caching, resume after interruption, and outputs both JSON and Excel files.

## Features

- **Unified Workflow**: Fetches author profile then crawls per-paper citations in one command
- **Smart Skip**: If total citations and publication count haven't changed, there is no pending
  citation work, and the Cite-dialog cache already covers the output, citation crawling is skipped
- **Incremental Caching**: Re-fetches only papers whose cache is incomplete; year-based papers use a
  per-year histogram to decide which years need refreshing
- **Stall Detection**: A paper whose fetch keeps producing the same result (e.g. Scholar lists fewer
  citations than it reports) stops being retried after one completed walk that lands short, and resumes
  on its own when Scholar's counts change
- **Year-Based Fetching**: Papers with many citations (configurable threshold, default ≥ 50) are fetched
  year-by-year (oldest → newest), skipping years whose cached count already matches Scholar's histogram
- **Dedup Handling**: Deduplicates citations using Scholar-native `cites_id` when available, falling
  back to `title + venue/authors` metadata. A row Scholar never clustered has no real `cites_id` and is
  keyed by the synthetic `no_id:<title>|<year>|<surname>` identity (never the literal `"None"`); the
  stage-1 writer records that key in `citations_meta.json`. The written output collapses rows that carry
  the **same real `cites_id`** (the same cluster re-listed across runs, keeping the row with the richest
  metadata) — and, for rows without one, only rows whose `title + venue` (or `title + authors`) are
  fully equal. A shared normalised title is deliberately **not** an identity: Scholar reporting one
  title under two clusters means two entries, and both are written (one index each downstream); the
  number collapsed is printed per paper
- **Resume Support**: Interrupted fetches resume from the last checkpoint; on exit the in-flight paper's
  state (citations fetched so far + the live year probe) is written to the output, so a year left short
  is re-fetched next run instead of being reported complete. A year that was interrupted mid-way keeps
  its item offset, so the next run continues inside that year rather than re-walking it
- **Consistent sentinels**: an unusable year is always written as `N/A` (older states may still contain
  `NA` or an empty string; every reader treats all of them as unknown)
- **Two different completeness signals**: `fetch_complete` in the output is the *data* verdict (does
  every probed year have its rows?), while `_fetch_state.complete_fetch_attempt` records whether the
  last attempt finished cleanly (a blocked/empty page makes it false). `--fetch-mode rough` reads the
  latter
- **Anti-Ban Strategies**: HTTP/2 via scholarly's native `httpx` client with full browser headers
  (fingerprint adopted from the injected browser cURL), randomized 45–90 s delays, mandatory 3–6 min
  breaks every 8–12 pages, browser header simulation
- **Full venue strings (`venue_full`)**: the citation-list pages truncate venues
  (`Proceedings of the 2024 Conference of the North American Chapter of the …`). Each publication's
  Scholar *detail* page (`citations?view_op=view_citation&citation_for_view=…`) is therefore fetched
  once per run to read the untruncated value — one request per publication (hundreds), not per citing
  entry (thousands). It is stored in the additive field `pub['venue_full']` (plus
  `pub['venue_full_source'] = "scholar_detail"` and the detail-page token `pub['citation_for_view']`);
  `venue` itself is never rewritten, because it takes part in identity keys downstream. Publications
  whose `venue` is **already complete** (non-empty, untruncated, not a preprint source) are skipped
  without a request and without a cache entry. The requests go through the same patched session (same
  waits, `--accelerate` scaling, captcha recovery, proxy prompt), and parsed pages are cached in
  `output/venue_detail_cache.json` — so an interrupted run resumes and a later run asks for nothing twice
- **Venue identity keys (`venue_key`)**: every publication and every citing entry carries a normalized
  venue identity — conference = acronym + edition year (`ACL2026`, `EMNLP2025`, `Findings of ACL2026`,
  `NeurIPS2024`), journal = short name without a year (`TACL`, `TASLP`), preprint = server name
  (`arXiv`). This lets downstream stages tell "same title, different venue" apart. Names come from a
  shared vocabulary (spelled-out proceedings titles resolve to their acronym, TACL stays the journal,
  and **Findings is a venue of its own**, not its host conference). Venues outside the table keep their
  own text as the key with `venue_key_source: "raw"`, which marks them as reviewable rather than
  normalized; a row without a venue gets no key at all
- **Citation formats (on by default; `--no-cite-formats` skips it)**: every citing entry keeps its
  Scholar cluster id as `cid`, and the entry's Cite dialog
  (`scholar?q=info:<cid>:scholar.google.com/&output=cite`) gives all five formats — MLA / APA /
  **Chicago** (the **full author list with given names**) / Harvard / **Vancouver** (the **untruncated
  venue** + volume/issue/pages) — stored in the additive `citation['cite']` field. It runs by default
  because for a *citing* paper this dialog is the only source of both: the citation-list page truncates
  the venue and abbreviates every author. It costs one request per citing entry, cached in
  `output/cite_format_cache.json`; an entry that already carries a parsed Chicago venue matching the
  venue seen online is skipped **without a request**, and an entry whose venue is already complete
  **and** whose authors are already spelled out in full is skipped too (those two things are all the
  dialog returns); entries without a `cid` (rows written before the field existed) cannot be addressed
  until the paper is re-fetched
- **Interactive Captcha Bypass**: `--interactive-captcha` lets you paste a browser cURL to inject real
  cookies and resume without restarting; if playwright is installed you can type `browser` at the prompt
  instead and let it harvest the cookies from a real browser window
- **Proxy Switch Wait**: On non-interactive failure, prompts hourly to switch proxy/IP until you type
  `ok` (the 24 h window scales with `--accelerate`)
- **Change Tracking**: Records per-run citation-count history in the profile JSON
- **Dual Output**: JSON and formatted Excel for both author profile and citations
- **Run Summary**: Prints elapsed time, total pages accessed, and new citations at the end of each run

## Requirements

- Python 3.8+
- Dependencies: `scholarly>=1.7, openpyxl>=3.1, httpx[http2]==0.27.2`

```bash
pip install -r requirements.txt
```

> **Note**: `httpx` is pinned to `0.27.2` for compatibility with `scholarly 1.7.11`. Newer versions
> break internal session handling. The `[http2]` extra is required: the crawler builds its sessions with
> `httpx.Client(http2=True)`, which raises `ImportError` when the `h2` package is missing.
>
> **Note**: A `NO_PROXY` / `no_proxy` entry that wraps an IPv6 literal in brackets (e.g. `[::1]`, common
> on macOS when a local proxy is running) is normalised automatically before `scholarly` is imported
> (`code/lib_env.py`). Without that, `httpx 0.27.2` raises `InvalidURL: Invalid port: ':1]'` while
> scholarly builds its client at import time, and the crawler cannot start at all.
>
> **Note**: The crawler uses scholarly's native `httpx` client (HTTP/2), not a TLS-impersonation
> library. Scholar's citation-search endpoint (`/scholar?cites=`) returns 429 for every `curl_cffi`
> impersonation target, while `httpx` works; `curl_cffi` is deliberately not a dependency.

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
                                       [--renumber-papers {off,dry-run,apply,force}]
                                       [--no-cite-formats] [--accelerate SCALE] [-v]

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
  --cite-formats                Resolve each citing entry's five citation formats
                                (MLA/APA/Chicago/Harvard/Vancouver) in citation["cite"];
                                ON by default; one request per entry, cached
  --no-cite-formats             Skip the Cite-dialog step (the list page's abbreviated
                                authors and truncated venue are kept)
  --renumber-papers {off,dry-run,apply,force}
                                Papers-directory maintenance: off, dry-run (report
                                only), apply (default: create missing directories with
                                their paper_meta.json, rename the ones whose index
                                moved) or force (apply even while stage 2 holds the
                                papers/ lock)
  --accelerate SCALE            Scale all deliberate waits (e.g. 0.1 = 10× faster);
                                must be > 0. Waits exist to avoid Scholar's rate
                                limits, so pair it with --interactive-captcha
  -v, --verbose                 Debug-level logging (also mirrored into the run log)
```

Out-of-range values are rejected up front (`--skip -1`, `--limit 0`, `--accelerate 0`), so a typo
cannot silently clear state or turn a "process nothing" request into "process everything".

### `--skip` and `--limit`

Papers are sorted by citation count descending. `--skip M` skips the first M papers; `--limit N` then
processes the next N papers (positions M+1 to M+N), regardless of whether each needs fetching. Useful
for targeting a specific range for debugging or manual recovery.

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

Both JSON files are written atomically (temp file + rename), so an interrupted write cannot leave a
truncated state file behind; a file that is still unreadable is backed up as `<name>.corrupt` and
reported. The Excel workbooks are convenience views — if one cannot be written the run keeps going and
only prints a warning, and the JSON is left untouched.

| File | Description |
|------|-------------|
| `author_<ID>_profile.json` | Author info, publication list, and change history |
| `author_<ID>_profile.xlsx` | Excel: Author Overview, Publications, Change History |
| `author_<ID>_paper_citations.json` | Per-paper citation lists; also the resume state |
| `author_<ID>_paper_citations.xlsx` | Excel: Summary, All Citations, Run Metadata |
| `venue_detail_cache.json` | Publication detail pages already parsed (`citation_for_view` token → full venue). Makes the `venue_full` step resumable; safe to delete to re-read every page |
| `cite_format_cache.json` | Cite dialogs already parsed (row `cid` → the five formats + `venue_text` / `authors_full`). Makes `--cite-formats` resumable; safe to delete to re-read every dialog |
| `author_<ID>_papers/` | **The pipeline's shared papers tree**: `<NNN>-<slug>/` per publication with `citations/` (PDFs + per-citation Cite JSON), `citations_meta.json`, `paper_meta.json`, `download_progress.json`, `enrich_info/`, `analysis_info/`, `analysis_content/`, `.shared/work/`, `old/`. Stage 1 creates the root, the directories, `paper_meta.json`, the `citations_meta.json` main body and the Cite JSON files; every later stage reaches the tree through its own `papers` symlink, never by this path |
| `output/logs/author_<ID>_run_<ts>.log` | **Full** log of each run — everything, including the per-page/per-request detail the screen hides (stdout is mirrored, filtered lines are written directly). The newest **30** are kept; older ones are **moved** (never deleted) to `output/logs/old/` |

## Run Logs

Every line of a run has the same shape, so the log can be read top-down and grepped like a table:

```
11:40:12 INFO  run       Run log: ./output/logs/author_…_run_….log
11:40:13 INFO  phase     ── Phase 1 · author profile · HF3-E9kAAAAJ ──────────────
11:40:13 INFO  profile     previous profile <timestamp> (incremental compare)
11:40:16 WARN  http        blocked (captcha) — phase-1 page 1
11:44:02 INFO  phase     ── Phase 2 · citations · HF3-E9kAAAAJ ──────────────────
11:44:02 INFO  paper     [3/241] Attention is all you need... -> cached (1364 citations)
11:44:02 INFO  year      2025 · page 3 · 10 items
11:44:02 INFO  year      Year 2017 status: year_total=120, year_new=0, pages=12, … | histogram=120, …
11:44:03 INFO  output    ── Saving Output Files ──────────────────────────────────
11:44:05 INFO  summary   ── Run summary ──────────────────────────────────────────
```

- `<time> <LEVEL> <module> <indent><message>`; the module tag (`run / phase / profile / venue / http /
  fetch / year / paper / plan / output / cite / summary`) is padded so messages line up, and the indent
  (2 spaces per level) mirrors the structure: run → phase → paper → year/page → detail.
- **Pacing**: a citation-list page waits 45–90 s; the per-entry Cite dialog and the publication
  detail page wait **30%** of that (13.5–27 s), because they are 60–83% of a run's requests. Every
  **100 requests** — dialogs included — the run rests for **2 hours**. Slower, but it is the
  cheapest thing we know that keeps a several-thousand-request crawl out of bot territory.
- **Interrupting a run**: `Ctrl+C` prints what is actually on disk (product and cite cache with their
  timestamps, and which save step was reached) and then exits; the next run resumes from
  `author_<ID>_paper_citations.json`, so the papers that were already fetched are not walked again.
  The product is written **before** the Cite-dialog step, so an interrupt there costs only the dialogs.
- **Screen vs log file**: the terminal shows the run's **progress** — the probe/cache summary, each
  year's `skip` / `fetching` / `status`, the page line, each request URL and wait, plus `WARN`+ — while
  `output/logs/<run>.log` additionally keeps the per-entry detail (each citing row, `Progress saved`,
  dedup skips, retry reasons). Both sinks write every line exactly once (screen `INFO`, file `DEBUG`).
- **Levels**: `INFO` = state changes and conclusions; `DEBUG` = the per-request / per-page / per-year
  detail (off the screen by default, always in the log file); `WARN` = something that needs a human
  look, e.g. a page that returned zero rows. `-v` / `--verbose` lowers the **screen** to `DEBUG` as
  well. On the screen, the run's pulse is one `Mandatory break after N pages …` line every 8–12 pages
  (~5 min) plus one line per paper. While a paper is being fetched, **every** Cite-dialog request names
  the **citing entry** once, at the end of its `Request URL:` line
  (`… &hl=en · "SurfDesign: Effective Protein Design …"`), because the URL itself only carries the cid.
  No name is repeated: the cited paper appears once in its `[N/241] <title>` line, the citing entry
  once per request, and the other lines (`waiting …`, page/year) carry no prefix.
- **One fact, one line**: a phase is a single ruled `── title ──` line instead of a 70-character
  banner, the year's counters and the diagnosis they came from are one line, and a request URL is
  printed once (by the session layer) rather than twice.
- **Page progress**: every citation-list page writes one line at `INFO`, e.g.
  `11:40:13 INFO  year      2025 · page 3 · 10 items` — so a run that is 40 minutes into a year can be
  traced page by page (`page 2 · 7 items` in direct mode, where there is no year filter).
- **Retention**: `output/logs/` keeps the newest 30 run logs; at the start of a run the older ones are
  moved to `output/logs/old/`. Nothing is ever deleted.

## Full Venue Strings (`venue_full`)

Scholar's citation-list pages truncate the venue (`Proceedings of the 2024 Conference of the North
American Chapter of the …`), so the profile step ends by reading each publication's **detail page**
once (`citations?hl=en&view_op=view_citation&citation_for_view=<author>:<token>`), where the field is
spelled out in full:

```json
{
  "venue": "Proceedings of the 2024 Conference of the North American Chapter of the …, 2024",
  "venue_full": "Proceedings of the 2024 Conference of the North American Chapter of the Association for Computational Linguistics: Human Language Technologies (Volume 1: Long Papers)",
  "venue_full_source": "scholar_detail",
  "citation_for_view": "HF3-E9kAAAAJ:xtoqd-5pKcoC"
}
```

Notes:

- One request per **publication** (hundreds), not per citing entry (thousands) — and **none** for a
  publication whose `venue` is already complete (present, untruncated, not a preprint source), so the
  pass only pays for the rows that can still improve. The trade-off is deliberate: the detail page could
  also supply volume/pages for an already-complete venue, and those rows no longer get it.
- Requests go through the same patched session as everything else, so the standard delays,
  `--accelerate` scaling, captcha recovery and proxy wait all apply. `output/venue_detail_cache.json`
  records every parsed page, so an interrupted run resumes and a later run issues no request for a page
  it already has. A page whose request failed is **not** cached and is retried next run; a publication
  that cannot be resolved simply keeps `venue_full: ""` and the rest of the run continues.
- **`venue` is never overwritten.** It takes part in metadata identity keys (`code/citation_identity.py`,
  and the downstream stages' identity checks), so rewriting it would drift keys that historical data
  was analysed under. `venue_full` is purely additive.

## Venue Identity Keys (`venue_key`)

Downstream dedup/uniqueness checks need to know *which venue* a record belongs to, and Scholar writes
the same venue in a dozen shapes (acronym, spelled-out proceedings title, journal form, truncated).
Both the profile's publications and every citing entry therefore carry a normalized key.

```json
{
  "venue": "Proceedings of the 2024 Conference of the North American Chapter of the …, 2024",
  "venue_full": "Proceedings of the 2024 Conference of the North American Chapter of the Association for Computational Linguistics: Human Language Technologies (Volume 1: Long Papers)",
  "venue_key": "NAACL2024",
  "venue_key_source": "normalized"
}
```

- **Preprints are keyed by their server**: every arXiv spelling — `arXiv preprint arXiv:2402.18567`, a
  truncated `arXiv preprint arXiv …`, `arxiv.org/abs/…` — becomes `arXiv`; the same holds for
  `bioRxiv`, `medRxiv`, `SSRN`, `TechRxiv`, `Research Square` (including the `researchsquare.com`
  domain form) and `preprints.org`. No year is added: the server *is* the venue. A row that only says
  "Preprint" names no server and keeps that text.
- **There is no bare `Findings` venue**: a Findings volume that names its host keeps it
  (`Findings of EMNLP2024`), and one that does not is treated as **`Findings of ACL`**.
- **Conferences carry the year, journals do not**: a conference key is the acronym plus the edition year
  (`ACL2026`, `ICASSP2025`, `EMNLP2024`) because the year identifies the edition; a journal is a
  continuous venue, so its key is the short name alone (`TACL`, `TASLP`, `Nature Machine Intelligence`,
  `Neurocomputing`).
- **Findings is its own venue**: `Findings of the Association for Computational Linguistics: ACL 2026`
  → `Findings of ACL2026`, never `ACL2026`.
- The shared vocabulary has **167 venues (83 conferences + 84 journals)** — speech / machine
  translation / NLP / ML conferences (`ICASSP`, `INTERSPEECH`, `IJCNN`, `PAKDD`, `LREC`, `CCMT`,
  `CWMT`, `WMT`, `NLPCC`, `PACLIC`, `IALP` …) and journals (`TASLP`, `TALLIP`, `TKDD`, `JAIR`,
  `IEEE Access`, `Neurocomputing`, `中文信息学报` …). Spelled-out proceedings resolve to their acronym,
  `TACL` (journal) is not swallowed by `ACL` (conference), `CCMT` / `CWMT` / `WMT` stay apart, and the
  two spellings `NIPS` / `ACMMM` collapse onto `NeurIPS` / `ACM MM`.
- The year is the first 4-digit year inside the venue string (the conference edition), falling back to
  the record's own `year`.
- **A citing entry prefers the Cite dialog's venue**: the parsed `citation['cite'].venue_text` (or a
  legacy `bibtex` field) carries `journal` / `booktitle` / `series` untruncated, so `venue_key` is
  derived from it whenever the dialog data is present, and only falls back to the citation-list `venue`
  (cut off at ~25 characters) otherwise. The five formatted strings are deliberately not parsed — the
  venue sits between the authors, the title and the volume there, and reading the title as the venue
  would mis-normalize the key.
- **Unknown venues keep their own text** (`venue_key_source: "raw"`) so the values are reviewable; a
  row with no venue at all (empty / `NA` / `N/A`) gets `venue_key: null`, because a key of `NA` would
  merge unrelated records. Citation list pages truncate venues aggressively, and a **truncated** venue
  gets **no key** unless it matches the explicit `TRUNCATED_VENUE_PREFIXES` table (`Advances in
  Neural …` → NeurIPS, `Proceedings of the AAAI …` → AAAI, `Expert Systems with …` → its journal), so
  many citing entries stay keyless until the table is extended — filter on
  `venue_key_source == "normalized"` when a key must be trustworthy.
- Publications derive the key from `venue_full or venue` (the detail page resolves what the list page
  cut off); citing entries prefer the Cite-dialog venue and fall back to their list-page venue.
- A host name is not a venue either: `kdd.org` / `iclr.cc` do not resolve to the KDD / ICLR
  conferences.
- Existing output files can be filled in (and re-derived after a table change) without re-crawling:

  ```bash
  python -m code.data_upgrade.normalize_venue_key                    # dry-run + summary
  python -m code.data_upgrade.normalize_venue_key --review-file output/venue_key_review.json
  python -m code.data_upgrade.normalize_venue_key --apply            # write + backup + manifest
  ```

  The tool only writes the two derived fields, backs the previous files up under
  `output/venue_key_backup_<stamp>/`, is idempotent, and its report lists every distinct
  *unrecognised* venue with its count so the table can be extended deliberately.

## Citation Formats (on by default; `--no-cite-formats` skips it)

Every citing entry keeps the Scholar cluster id of its result row as `citation['cid']` (the row's
`data-cid`; scholarly exposes it as `url_scholarbib`). It is **not** `cites_id`: that one identifies the
*cited* work's cluster and takes part in identity keys, while `cid` addresses that row's Cite dialog:

```
scholar?q=info:<cid>:scholar.google.com/&output=cite&scirp=0&hl=en
```

One request returns all five formats. (BibTeX is not fetched: its signed link is minted per dialog
render, so a cached one is always stale.) They are stored in the additive `citation['cite']` field
(this runs by default — pass `--no-cite-formats` to skip the requests, at the cost of keeping the list
page's abbreviated authors and truncated venue). Storing them also refreshes `venue_key` from the
dialog's Chicago venue, which is the untruncated one:

```json
{
  "title": "…", "authors": "H Wang, F Zhang", "venue": "Proceedings of the 2025 …",
  "cites_id": "3724674774839834281", "cid": "nYGTsUfPxnMJ",
  "cite": {
    "mla": "…", "apa": "…",
    "chicago": "Zhang, Jie, Haoyu Bu, …, and Dan Meng. \"…\" Cybersecurity 8, no. 1 (2025): 55.",
    "harvard": "…",
    "vancouver": "Zhang J, Bu H, …, Meng D. …. Cybersecurity. 2025 Feb 5;8(1):55.",
    "venue_text": "Cybersecurity",
    "authors_full": "Zhang, Jie, Haoyu Bu, …, and Dan Meng"
  }
}
```

Notes:

- **On by default.** It costs one request per citing entry — thousands for a real profile — so a run
  only pays it for entries that can still improve. `output/cite_format_cache.json` records every parsed
  dialog, so a repeat run issues no request for a page it already has.
- **Skipped when there is nothing to gain**: a row whose `venue` is already complete **and** whose
  `authors` are already spelled out in full is not requested at all (no cache entry either). Rows that
  already have a cached dialog are still served from the cache, so re-running cannot drop `cite`.
- **Additive only.** `title` / `authors` / `venue` / `year` / `url` / `cites_id` are never rewritten
  (`venue` and `cites_id` take part in downstream identity keys); whatever a dialog does not provide is
  simply absent from `cite`.
- Requests reuse the patched session (standard delays, captcha recovery, proxy wait), but this path
  takes the per-request delay **without** the mandatory 3–6 min break that citation-list pagination
  applies — the same property that makes the detail-dialog pass the first to hit the rate limit when
  run continuously (see `issues.md`).
- A page that fails (request error, captcha/consent page, no citation table) is **not** cached and is
  retried next run.

## Rate Limiting & Anti-Ban Strategies

Google Scholar aggressively rate-limits automated requests. This tool uses multiple mitigation layers:

- **Randomized delays**: 45–90 s between requests; the **detail-style paths** — publication detail
  pages and citing entries' Cite dialogs — run at **1/10 of that (~4.5–9 s)** via the single
  `code.config.DETAIL_PAGE_DELAY_FACTOR` setting. `--accelerate` still scales everything on top;
  citation-list pagination keeps the full delay and is the only path with the mandatory breaks. Honest
  cost: Scholar rate-limits by volume, so the shorter delay reaches the block sooner in wall-clock
  terms; the disk cache plus resume is what makes it worthwhile — stop at the first blocked page, come
  back later, and far less wall-clock goes into waiting
- **Mandatory long breaks**: Every 8–12 **citation-list pages** (Cite dialogs and detail pages do not
  count), a 3–6 minute break resets Scholar's sliding-window rate limit
- **Browser header simulation**: Full `sec-fetch-*`, `sec-ch-ua-*`, `user-agent`, `accept`,
  `accept-language`, and dynamic `Referer` headers sent over HTTP/2 via scholarly's native `httpx`
  client. When a browser cURL is injected, the whole browser fingerprint (user-agent + `sec-ch-ua` +
  `sec-ch-ua-full-version-list`, enforced as one coherent unit) is adopted from that cURL so it always
  matches the browser that issued the cookies; a built-in Chrome 145 default is only the cold-start
  fallback
- **Session refresh**: Soft-resets the session every 10–20 pages (preserves cookies, clears `got_403`
  flag)
- **Fast failure**: `scholarly` retries limited to 1 per page so failures reach the paper-level retry
  quickly
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

When Scholar shows a CAPTCHA, `--interactive-captcha` lets you inject real browser cookies without
restarting:

```bash
python -m code.scholar_citation --author YOUR_AUTHOR_ID --interactive-captcha
```

When a block is detected:

1. The program shows you the blocked URL
2. Open that URL in your browser (solve the CAPTCHA if needed)
3. In Chrome/Edge DevTools → Network tab, right-click the request → **Copy as cURL**
4. Paste the cURL into the terminal; the program detects the end automatically (last line has no `\`)
5. Cookies and selected headers are extracted and injected; the program retries immediately

If the pasted request contains no cookies (a common mis-copy, e.g. the wrong resource from DevTools),
the program re-prompts you to paste again instead of giving up. Press Enter to skip pasting and fall
back to automatic waiting.

In interactive mode, the program **never exits on failure** — it keeps prompting until you solve the
captcha or switch proxies. With no terminal to prompt on (cron, `nohup`, redirected stdin) the prompt is
skipped and the program falls back to automatic waiting.

In non-interactive mode each page failure goes through two quick retries and then 5–10 minute
rate-limit cooldowns. Once three cooldowns have not helped, the program waits up to 24 hours, prompting
hourly to switch your proxy/IP; type `ok` and press Enter to retry immediately. If that wait times out
(or stdin is not a terminal, so nobody could ever answer), the page error is returned to the paper-level
retry budget instead of looping here forever — so an unattended run finishes with a clear failure rather
than spinning indefinitely.

## Resume After Interruption

If the script is interrupted (Ctrl+C, timeout, or error):

1. Progress is saved automatically, and on exit the in-flight paper's partial state is written to the
   output file — including the citations fetched so far and the live year probe, so a year that was
   left short stays visible
2. Simply re-run the same command to resume
3. Completed papers and completed year segments within a paper are skipped

The aggregate JSON is the only resume ledger: there is no separate partial snapshot file.

## Stalled Papers (Same Result Repeated)

Some papers can never be completed by retrying: Scholar's own citation list returns fewer rows than its
"Cited by" count, so every attempt ends with the same numbers. The crawler notices this:

- After each **full walk** the result is signed by the number of rows it obtained (e.g. `direct:0`, or
  `year:241`) and stored together with how many full walks in a row produced it. Scholar's own counts
  are deliberately *not* part of the signature: they drift between runs even when the listing does not,
  which would make every run look like a new outcome and keep such a paper in the plan forever.
- With **`STALL_AFTER_ATTEMPTS = 1`**, a single completed walk that lands short is enough: the paper
  is marked `stalled` and skipped. It stays in the output with whatever was collected, the run summary
  prints `Stalled (skip): N`, and the plan lists `Skipping N stalled paper(s)`. It is parked even
  though it still looks incomplete — the same list returned the same rows, so "the paper that was
  just fetched" is not walked again from page 1. Raise the threshold only if you would rather give a
  transient short listing a second chance.
- The stall clears itself when the paper's Scholar count grows (the one event that can add rows), and
  whenever the number of rows obtained changes.
- Only a **full walk** counts: an attempt that was blocked, hit an empty page, was interrupted (Ctrl+C)
  or deliberately stopped early never reaches the recording step, so it neither counts as a repeat nor
  wipes one. A transient failure cannot stall a paper by itself, and cannot postpone a genuine repeat
  either. (The number compared is the paper's whole row set — rows fetched this run plus the cached
  rows of years that were already covered — not just this run's pages.)

To force a retry for a specific paper, clear its state with
`--fetch-mode force --skip <N-1> --limit 1`.

## Diagnosing Empty Pages

A citation request can succeed (HTTP 200, no exception) yet come back with no result rows — an empty
result set, a consent page, or an unrecognised interstitial. Such a response is recorded:

- `Page items: 0 (no citation rows) [<evidence>]` — printed for a citation page with no rows, with the
  evidence: whether the results container was present, the result-count banner text, and markers
  (`captcha`, `consent`, `unusual_traffic`, `no_results`). `scholarly` already rejects captcha pages
  itself, so a line without markers means the page was accepted but simply had no rows.
- `termination=empty_first_page` — a direct-mode list returned no rows while Scholar reports citations
  for the paper
- `termination=empty_page` — a probed year (histogram says N > 0) returned no rows
- `Blocked fetching ...: redirected to Google's block page (https://www.google.com/sorry/index)` —
  Scholar bounced the request to its `/sorry/` interstitial. The redirect itself is treated as the
  block, so an interstitial that serves its CAPTCHA page with **200** takes the captcha/retry path
  instead of being parsed as an empty citation list.

An empty page still records "no new citations" for this run, but it is not allowed to destroy data: the
year (or paper) it belongs to keeps the citations already cached and stays incomplete, so the next run
retries it.

## When Scholar Has No Cite Dialog for an Entry

A few citation rows have no Cite dialog at all: the list page serves the row, but
`/scholar?q=info:<cid>:scholar.google.com/&output=cite` answers **404** (Google's own "Error 404 (Not
Found)" page).

The HTTP status of every response is recorded, so a 404/410 on a one-shot detail page (Cite dialog,
publication detail page) means "this record does not exist" rather than "we are blocked":

- such an entry is remembered in `output/cite_format_cache.json` as
  `{"unavailable": true, "status": 404, "fetched_at": …}` and not asked again for
  `UNAVAILABLE_RETRY_DAYS` (7) — deliberately finite: the handle belongs to Scholar, and how it
  (re)builds clusters is not fully understood yet, so a 404 today may answer later;
- the citation-formats summary counts them separately: `no dialog N`;
- a failed entry is not retried with cooldowns and does not trigger a captcha prompt; if a fresh cookie
  injection also fails for a single record, the same `RecordUnavailable` path is used instead of
  asking for cookies again.

For such a row the Cite dialog's **full author list** and **untruncated venue** are out of reach; the
row itself (title, authors, venue, year, url) is unaffected. A host-wide TLS-level rejection
(`www.google.com` / `scholar.google.com` answering `sslv3 alert handshake failure`) is a different
failure and is still treated as a block.

## Year-Based Fetch Details

Papers with ≥ 50 **total** citations switch to year-by-year fetch mode:

- **Direction**: Always oldest → newest
- **Selective refresh**: Scholar's year histogram is probed once per paper; only years where the cached
  count differs from the histogram are re-fetched
- **A fetch never deletes citations it did not return**: a year whose page comes back empty
  (blocked/consent/interstitial) keeps its cached citations and stays incomplete, and a full re-scan
  that returns fewer rows than the cache is merged with the cached rows instead of replacing them. Only
  `--fetch-mode exact` (and a `force` re-fetch) rebuilds a year bucket from Scholar.
- **Skip logic**:
  - **Per-year**: A year is skipped if `seen_total (cached + dedup) >= histogram_count` for that year
  - **Probe authority**: a cached year that is *absent* from the histogram is only skipped when the
    probe actually produced per-year counts. When the probe fails, an absent year is not evidence of
    completeness, so the year is fetched.
  - **Paper-level (year mode)**: complete only when **every** year covers its own `histogram_count`.
    The aggregate `histogram_total <= seen_total` alone is not enough — years whose bucket exceeds the
    probe inflate the total and can hide a different year that is still short
  - **Paper-level (direct mode)**: complete when `scholar_total <= seen_total`
- **Plan start year**: the plan widens to cached years only inside the histogram's own range; cache
  years outside that range cannot add data and are reported as WARN instead of being requested
- **Resume**: `partial_year_start` records the item offset within the in-progress year and is persisted
  with the fetch state, so an interrupted year resumes at that offset on the next run instead of
  re-walking from item 0 (its page is page-aligned and already-collected rows are skipped). Direct mode
  uses a page-aligned `direct_resume_state` while the paper is in flight; a fresh run still re-walks a
  direct list from the head (its length is small enough that this is cheaper than trusting a stale
  offset). A completed fetch never deletes rows it did not return: a smaller result is merged with the
  cached rows unless `--fetch-mode exact` asked for an authoritative rebuild.

## Development Notes

This repository includes three files that document the AI-assisted development process:

- **`_user.zh.md`**: A timestamped, sequentially numbered log of all user messages — shows how
  requirements evolved entirely through natural language, with the user writing zero code.
- **`_update_history.zh.md`**: Chronological record of every feature, fix, and refactor with clear date
  headings.
- **`_work_notes.zh.md`**: Technical reference — architecture decisions, caching design, scholarly
  internals, and key bug records.

All three are committed to git and contain no personally identifiable information.

### Project Structure

All code lives in the `code/` package; the entry point is run as a module from the project root:

```
code/
  scholar_citation.py        # CLI entry point + PaperCitationFetcher orchestrator
  cli.py                     # parse_args() only — the entry point is code.scholar_citation.main()
  author_fetcher.py          # AuthorProfileFetcher
  profile_io.py              # Profile JSON / Excel output
  pub_info.py                # PubInfo dataclass for publication field normalization
  venue_detail.py            # venue_full from the publication detail page (+ its cache)
  venue_quality.py           # is this venue complete / is it only a preprint source / full names?
  cite_format.py             # five citation formats from the row's Cite dialog (+ its cache)
  citation_cache.py          # Year-count and diagnostics pure functions
  citation_strategy.py       # Fetch policy, refresh strategy, reconciliation
  citation_identity.py       # Citation dedup key, info extraction, derived venue keys
  citation_io.py             # Cache I/O, status derivation, citations Excel output
  citation_models.py         # YearRecord, ResumeState, FetchPolicy dataclasses
  citation_fetch.py          # fetch_citations_with_progress + fetch_by_year engine
  fetch_session.py           # BatchFetchSession, YearFetchSession
  output_state.py            # PaperFetchState / PaperState + output _fetch_state read/write
  citation_index.py          # Immutable citation indices; citations_meta / cite files
  papers_maintenance.py      # Papers-directory create / rename / merge / archive
  page_visit.py              # PageVisit — per-page error recovery
  scholarly_session.py       # SessionContext + scholarly monkey-patch + year probe
  interactive.py             # cURL cookie injection, captcha prompt, proxy-switch wait
  config.py                  # Centralised constants + crawler_config.json / proxy resolution
  common.py                  # Constants (re-exported from config) and stateless utilities
  lib_env.py                 # Third-party env staging + NO_PROXY normalise + proxy env bridge
  log_utils.py               # run_log / setup_logging() structured logging
  venue_key.py               # thin re-export of shared.venue_key
  shared/                    # cross-repo shared modules (PEP 420, no __init__.py)
    venue_key.py             # venue identity keys
    identity.py              # same_citation_identity()
    citation_key.py          # synthetic citation identity keys
    papers_layout.py         # papers-directory layout / numbering / naming
    stage_lock.py            # papers/ usage lock
    artifact_sync.py         # per-citation artifact naming (output_NNNN.json)
    lib_env.py               # NO_PROXY sanitising engine
  data_upgrade/              # one-off data repair/upgrade tools — kept apart from the main entry point
    align_citations_meta.py  # align citations_meta main body with the crawl product
    compare_citations_meta.py# read-only comparison of product vs citations_meta
    dedupe_meta_rows.py      # remove duplicate same-cites_id rows from citations_meta
    fix_diag_from_year_record.py
    fix_output_fetch_state.py
    normalize_venue_key.py   # fill / refresh venue_key + venue_key_source
    refresh_product_keys.py  # add derived keys to the crawl product
    rename_artifacts.py      # rename per-citation artifacts to output_NNNN.json
    repair_chicago_titles.py # fix titles truncated by the old Chicago parser
    seed_citation_index.py   # seed existing citation numbers into stage 1
  examples/run_example.sh    # minimal example invocation
output/                      # crawl products (author_*.json/.xlsx) + logs/ + caches
tests/                       # unit tests, no network required
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
