"""
code/cite_format.py — All five citation formats (and BibTeX) for one citing entry.

Why this module exists
----------------------
The citation-list pages (``/scholar?cites=…``) give one short line per citing
entry: abbreviated authors (``H Wang, F Zhang``), a truncated venue, a year.  Every
Scholar result row also carries a ``data-cid`` cluster id, and that id addresses
the row's **Cite dialog**::

    /scholar?q=info:<cid>:scholar.google.com/&output=cite&scirp=0&hl=en

One request returns **all five** formatted citations — MLA, APA, Chicago, Harvard,
Vancouver — and, in the same HTML, a *signed* link to the BibTeX download.  Two of
those five carry what the list page cannot:

* **Chicago** spells every author out in full
  (``Zhang, Jie, Haoyu Bu, …, and Dan Meng.``);
* **Vancouver** carries the untruncated venue plus volume/issue/pages
  (``… Cybersecurity. 2025 Feb 5;8(1):55.``).

Storing them is therefore the source for the two things downstream stages keep
asking for (full author names, repaired venues).  Nothing here decides *how* they
may be used: this module only resolves and persists them.

What is written where
---------------------
* ``citation['cid']`` — persisted by
  ``code.citation_identity.extract_citation_info`` (pure, no request), so a row can
  be re-resolved later.  Distinct from ``cites_id``: that one identifies the
  *cited* work's cluster list and takes part in the identity keys, the cid
  identifies one *citing* row.
* ``citation['cite']`` — the formats themselves, **strictly additive**:
  ``{'mla': …, 'apa': …, 'chicago': …, 'harvard': …, 'vancouver': …,
  'bibtex': {…}}``.  ``title``/``authors``/``venue``/``year``/``url``/``cites_id``
  are never touched (``venue`` and ``cites_id`` take part in the identity keys —
  the same K123/K125 lesson as ``code/venue_detail.py``).

Cost and etiquette
------------------
This is **one request per citing entry** (thousands for a real profile, plus one
more per entry for BibTeX), so it is opt-in: ``--cite-formats``.  A normal run must
not silently start paying for it.

Requests go through the crawler's existing patched scholarly session
(``scholarly._Scholarly__nav._get_soup`` for the Cite dialog and
``_get_page`` for the signed BibTeX URL — the same entry points ``scholarly``
itself uses), so the 45–90 s waits, ``--accelerate`` scaling, captcha recovery and
proxy prompts all keep applying.  Note that this path takes the per-request delay
but **not** the mandatory 3–6 min break that
``scholarly_session.patched_load_url`` applies to citation-list pagination, which
is exactly what got the detail-page pass rate-limited on 2026-10-14 (see
``issues.md``); the caller is expected to watch for the first blocked page and
stop rather than push through it.

Resume/caching: every parsed Cite dialog is stored in
``output/cite_format_cache.json`` keyed by cid, so an interrupted run continues
where it stopped and a later run re-requests nothing it already has.  A page that
failed (request error, captcha/consent page, no format table) is **not** cached
and is retried next run.  A page that parsed but whose BibTeX download failed is
cached *with* its signed ``bib_url``, so the next run retries only that one
request instead of re-fetching the page.

Skipping: a citing entry whose venue is already complete **and** whose authors are
already spelled out in full (``code/venue_quality.citation_needs_cite_dialog``) is
not fetched and writes no cache entry — those two things are all this page
returns.  An entry that already has a cached dialog is still served from that
cache, so re-running with the skip rule cannot drop ``citation['cite']`` from the
output for rows an earlier run paid for.
"""

import html
import json
import os
import re
from datetime import datetime

from bs4 import BeautifulSoup

from code.common import write_json_atomic
from code.shared.venue_key import citation_venue_key
from code.venue_quality import citation_needs_cite_dialog


# scholarly's own Cite-dialog template (publication_parser._BIBCITE), minus the
# scirp row-position argument: pos 0 is accepted for every row we send.
_CITE_PATH = '/scholar?q=info:{0}:scholar.google.com/&output=cite&scirp=0&hl=en'

#: The five labels Scholar's Cite dialog uses, in page order.
FORMAT_LABELS = ('mla', 'apa', 'chicago', 'harvard', 'vancouver')

#: Key added to a citation dict; the formats live inside it.
CITE_FIELD = 'cite'

#: Cache file name, written under the crawler's ``output/`` directory.
CACHE_FILENAME = 'cite_format_cache.json'

_BIB_ENTRY_RE = re.compile(r'@([A-Za-z]+)\s*\{\s*([^,\s}]*)')
_BIB_FIELD_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*')


# ---------------------------------------------------------------------------
# Pure helpers (cite URL + page parsing)
# ---------------------------------------------------------------------------

def normalize_whitespace(value):
    """Collapse runs of whitespace (including newlines) into single spaces."""
    if value is None:
        return ''
    return ' '.join(str(value).split())


def normalize_cid(value):
    """Return a usable cluster id, or '' (the Cite dialog cannot be addressed)."""
    if not value:
        return ''
    text = normalize_whitespace(value)
    if not text or text.lower() in ('none', 'null', 'n/a', 'na'):
        return ''
    return text


def cite_path(cid):
    """Relative Cite-dialog path for *cid* (scholarly prepends the host)."""
    return _CITE_PATH.format(cid)


def parse_cite_page(source):
    """Parse one Cite dialog into ``{'formats': {...}, 'bib_url': '...'}``.

    The page is a plain ``<table>`` of ``<th class="gs_cith">MLA</th>`` +
    ``<td><div class="gs_citr">…</div></td>`` rows, followed by an
    ``<div id="gs_citi">`` of export links.  Parsing pairs each label with its own
    cell (like ``code/venue_detail.py`` does for the detail-page field rows)
    instead of regexing over the text: pages can open with a ``<style>`` block
    that *mentions* ``.gs_citr{…}``, and a naive regex reads that CSS as a
    citation.

    Only the five known labels are kept, and only when they carry text; a page
    without any of them (captcha / consent / redirect) yields empty ``formats``,
    which callers treat as a failed fetch rather than as "no citation".

    *source* is HTML text or an already-parsed soup.  Entities are decoded by the
    parser, so ``&quot;``/``&amp;`` arrive as ``"``/``&``.  Child elements are
    concatenated **without** a separator (``get_text()``): the page marks its own
    word boundaries, so ``<i>Cybersecurity</i>, <i>8</i>(1)`` becomes
    ``Cybersecurity, 8(1)`` instead of ``Cybersecurity , 8 (1)``.
    """
    soup = (source if hasattr(source, 'find_all')
            else BeautifulSoup(source or '', 'html.parser'))
    formats = {}
    for label_el in soup.find_all('th', class_='gs_cith'):
        label = normalize_whitespace(label_el.get_text(' ')).lower()
        if label not in FORMAT_LABELS or label in formats:
            continue
        cell = label_el.find_next_sibling('td')
        if cell is None:
            continue
        text_el = cell.find(class_='gs_citr') or cell
        value = normalize_whitespace(text_el.get_text())
        if value:
            formats[label] = value

    bib_url = ''
    for link in soup.find_all('a', class_='gs_citi'):
        if normalize_whitespace(link.get_text(' ')).lower() != 'bibtex':
            continue
        href = link.get('href')
        if href:
            bib_url = html.unescape(str(href).strip())
            break
    return {'formats': formats, 'bib_url': bib_url}


# ---------------------------------------------------------------------------
# 从格式化串里取 venue 与作者全名（用户 2026-10-15 定）
#
# 弃用 BibTeX 之后，未截断的 venue 与作者全名改从对话框的**带引号格式**里取：
#   `Yi, Sibo, …, and Qi Li. "Jailbreak …: A survey." arXiv preprint arXiv:2407.04295 (2024).`
#   ① 引号里 = 标题；② 引号之前（去掉尾部句点）= 作者；③ 收尾引号之后 = venue（剥掉尾部 `(2024).`）。
# 没有引号的格式（APA 用斜体、Vancouver 不用引号）**不猜**：返回空，调用方保持旧行为 ——
# 这正是当初"五种格式故意不解析"的理由（避免把标题当 venue），现在只在**能确定边界**时才解析。
# ---------------------------------------------------------------------------

#: 引号里的标题（兼容直引号与中文/弯引号）
_QUOTED_TITLE_RE = re.compile(r'[“"](?P<title>[^”"]+)[”"]')
#: 尾部的括号年份 `(2024)` / `[2024]`，允许尾随标点
_TRAILING_PAREN_YEAR_RE = re.compile(r'[\\(\[]\s*((?:19|20)\d{2})\s*[\\)\]]\s*[.,;]?\s*$')
#: 尾部的裸年份 `… 2024.`
_TRAILING_BARE_YEAR_RE = re.compile(r'(?<![\d])((?:19|20)\d{2})\s*[.,;]?\s*$')


def parse_format_parts(text):
    """拆一条带引号的格式化串 → `{'authors','title','venue','year'}`（拆不出返回 `{}`）。"""
    value = normalize_whitespace(text or '')
    match = _QUOTED_TITLE_RE.search(value)
    if not match:
        return {}
    title = match.group('title').strip().strip('.').strip()
    authors = value[:match.start()].strip().rstrip('.').strip()
    tail = value[match.end():].strip().lstrip('.').strip()
    year = ''
    year_match = _TRAILING_PAREN_YEAR_RE.search(tail)
    if year_match:
        year, tail = year_match.group(1), tail[:year_match.start()].strip()
    else:
        year_match = _TRAILING_BARE_YEAR_RE.search(tail)
        if year_match:
            year, tail = year_match.group(1), tail[:year_match.start()].strip()
    return {'authors': authors, 'title': title,
            'venue': tail.strip().rstrip('.').strip(), 'year': year}


def format_extras(formats):
    """从 Chicago（缺则 MLA）里取 `venue_text` / `authors_full`（+ `*_source`）。"""
    for key in ('chicago', 'mla'):
        parts = parse_format_parts((formats or {}).get(key))
        if not parts:
            continue
        extras = {}
        if parts.get('venue'):
            extras['venue_text'] = parts['venue']
            extras['venue_source'] = key
        if parts.get('authors'):
            extras['authors_full'] = parts['authors']
            extras['authors_source'] = key
        if extras:
            return extras
    return {}


def parse_bibtex(source):
    """Parse one BibTeX record into ``{'entry_type', 'key', <field>: …}``.

    Handles the shapes Scholar actually emits: a single ``@article{key,`` entry
    whose fields are brace-quoted (nested braces included), with values that may
    span lines.  Returns ``{}`` when no entry header is present.  Values keep
    their content — only the surrounding braces and the layout whitespace are
    removed, so ``author`` stays a ``and``-separated string.
    """
    text = html.unescape(source or '')
    match = _BIB_ENTRY_RE.search(text)
    if not match:
        return {}
    entry = {'entry_type': match.group(1).lower(), 'key': match.group(2)}
    for name, value in _iter_bib_fields(text, match.end()):
        if value:
            entry[name] = value
    return entry


def _iter_bib_fields(text, position):
    """Yield ``(lowercased field name, value)`` pairs after *position*."""
    while True:
        match = _BIB_FIELD_RE.search(text, position)
        if not match:
            return
        value, position = _read_bib_value(text, match.end())
        if value is None:
            return
        yield match.group(1).lower(), normalize_whitespace(value)


def _read_bib_value(text, position):
    """Read one BibTeX value at *position*; return ``(value, next position)``."""
    if position >= len(text):
        return None, position
    if text[position] == '{':
        depth = 0
        index = position
        while index < len(text):
            char = text[index]
            if char == '\\':
                index += 2
                continue
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    return text[position + 1:index], index + 1
            index += 1
        return None, position
    if text[position] == '"':
        index = position + 1
        while index < len(text):
            if text[index] == '\\':
                index += 2
                continue
            if text[index] == '"':
                return text[position + 1:index], index + 1
            index += 1
        return None, position
    end = position
    while end < len(text) and text[end] not in ',}\n':
        end += 1
    return text[position:end], end


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def load_cite_cache(path):
    """Load the Cite-dialog cache; a missing or corrupt file means "empty"."""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError, TypeError, ValueError) as e:
        print(f"Warning: {path} is unreadable ({type(e).__name__}: {e}); "
              f"starting an empty citation-format cache", flush=True)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): value for key, value in data.items()
            if isinstance(value, dict)}


def save_cite_cache(path, cache):
    """Write the Cite-dialog cache atomically (a crash cannot truncate it)."""
    write_json_atomic(path, cache, ensure_ascii=False, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def enrich_citations_with_cite_formats(citations, cache_path=None,
                                       get_soup=None, get_page=None,
                                       print_fn=print, fetched_at=None):
    """Fill ``citation['cite']`` for every citation that carries a ``cid``.

    *get_soup* takes the relative Cite-dialog path and returns the page (soup or
    HTML); *get_page* takes the absolute signed BibTeX URL and returns its text.
    In production both are the patched scholarly navigator's methods, i.e. the
    crawler's own waits/retry/captcha handling.  Without *get_page* the five
    formats are still collected and BibTeX is simply skipped.

    Every per-citation failure is caught and counted: a citation that cannot be
    resolved keeps whatever it had and the run continues.  Only successfully
    parsed pages are cached (failed requests are retried next run); a page whose
    BibTeX download failed keeps its signed ``bib_url`` in the cache so the retry
    costs one request instead of two.

    Writing ``citation['cite']`` also refreshes the derived ``venue_key`` /
    ``venue_key_source`` from the BibTeX venue (``journal`` / ``booktitle`` /
    ``series``): that string is untruncated, unlike the citation-list page's
    ``venue``.  A dialog without a venue field leaves the existing key alone.

    Returns a stats dict: ``total``, ``with_cid``, ``missing_cid``,
    ``skipped_complete``, ``cache_hits``, ``fetched``, ``failed``, ``resolved``,
    ``bib_fetched``, ``bib_from_cache``, ``bib_failed``, ``bib_missing``.
    """
    citations = list(citations or [])
    stats = {
        'total': len(citations),
        'with_cid': 0,
        'missing_cid': 0,
        'skipped_complete': 0,
        'cache_hits': 0,
        'fetched': 0,
        'failed': 0,
        'resolved': 0,
        'bib_fetched': 0,
        'bib_from_cache': 0,
        'bib_failed': 0,
        'bib_missing': 0,
    }
    cache = load_cite_cache(cache_path)
    stamp = fetched_at or datetime.now().isoformat()

    for index, citation in enumerate(citations, 1):
        if not isinstance(citation, dict):
            continue
        cid = normalize_cid(citation.get('cid'))
        if not cid:
            stats['missing_cid'] += 1
            continue
        stats['with_cid'] += 1

        entry = cache.get(cid)
        changed = False
        if entry is None:
            if not citation_needs_cite_dialog(citation):
                # Complete venue *and* full author names already in hand: this
                # page has nothing left to add, so no request and no cache entry.
                stats['skipped_complete'] += 1
                continue
            if not callable(get_soup):
                stats['failed'] += 1
                continue
            try:
                page = get_soup(cite_path(cid))
                parsed = parse_cite_page(page)
            except Exception as e:
                # KeyboardInterrupt is not an Exception, so Ctrl+C still
                # propagates and the cache keeps what was already fetched.
                stats['failed'] += 1
                print_fn(f"    [{index}/{len(citations)}] {cid} -> cite page "
                         f"failed ({type(e).__name__}: {e})")
                continue
            if not parsed['formats']:
                stats['failed'] += 1
                print_fn(f"    [{index}/{len(citations)}] {cid} -> no citation "
                         f"table (captcha/consent/redirect page); not cached")
                continue
            entry = {
                'formats': parsed['formats'],
                'bib_url': parsed['bib_url'],
                'fetched_at': stamp,
            }
            cache[cid] = entry
            changed = True
            stats['fetched'] += 1
        else:
            stats['cache_hits'] += 1

        if entry.get('bibtex'):
            if not changed:
                stats['bib_from_cache'] += 1
        elif not entry.get('bib_url'):
            stats['bib_missing'] += 1
        elif not callable(get_page):
            stats['bib_missing'] += 1
        else:
            try:
                bibtex = parse_bibtex(get_page(entry['bib_url']))
            except Exception as e:
                bibtex = {}
                print_fn(f"    [{index}/{len(citations)}] {cid} -> BibTeX "
                         f"download failed ({type(e).__name__}: {e}); "
                         f"retried next run")
            if bibtex:
                entry['bibtex'] = bibtex
                stats['bib_fetched'] += 1
                changed = True
            else:
                stats['bib_failed'] += 1

        # venue 与作者全名：优先用条目里已存的，没有就从格式化串现拆（拆出来就写进缓存，
        # 所以**已有的老缓存条目无需重新联网**即可补齐这两个字段）。
        extras = {k: entry[k] for k in ('venue_text', 'venue_source',
                                       'authors_full', 'authors_source') if entry.get(k)}
        if not extras:
            extras = format_extras(entry.get('formats'))
            if extras:
                entry.update(extras)
                changed = True

        if changed and cache_path:
            save_cite_cache(cache_path, cache)

        payload = dict(entry['formats'])
        payload.update(extras)
        if entry.get('bibtex'):
            payload['bibtex'] = entry['bibtex']
        citation[CITE_FIELD] = payload
        # The dialog's BibTeX venue is untruncated, so refresh the derived key now
        # that it is available; without a venue field in the dialog this falls
        # back to citation['venue'], i.e. exactly the previous behaviour.
        key, key_source = citation_venue_key(citation)
        citation['venue_key'] = key
        citation['venue_key_source'] = key_source
        stats['resolved'] += 1

    return stats
