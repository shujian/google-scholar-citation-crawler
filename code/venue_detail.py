"""
code/venue_detail.py — Full venue strings from Scholar's publication detail page.

Why this module exists
----------------------
The citation-list pages (``/scholar?cites=…``) only carry a *truncated* venue
(``'Proceedings of the 2024 Conference of the North American Chapter of the …'``).
For a publication that is in **our own author profile**, Scholar has a second
page that spells the venue out in full::

    /citations?hl=en&view_op=view_citation&citation_for_view=<author_id>:<token>

That page is fetched once per *publication* (hundreds — one per source paper),
not once per citing entry (thousands), and the full venue is stored in a **new**
field ``pub['venue_full']``.

``pub['venue']`` is deliberately **never** rewritten
(``venue_full`` is strictly additive): ``venue`` takes part in the metadata
identity key ``meta\\t{title}\\t{venue}`` (``code/citation_identity.py``) and in
the downstream stages' identity checks (K123/K125), so rewriting it would drift
historical identity keys and re-key data that has already been analysed.
Downstream 3.a.1 may *prefer* ``venue_full`` when it repairs venues; that is a
later, separate decision.

Fetching uses the crawler's existing patched scholarly session
(``scholarly._Scholarly__nav._get_soup()`` — the same call the year probe uses),
so the 45–90 s waits, ``--accelerate`` scaling, captcha recovery and proxy
prompts all keep applying.  ``code/page_visit.py`` / ``code/scholarly_session.py``
are *not* used directly here: they only work inside the patched session.

Resume/caching: every parsed page is stored in ``output/venue_detail_cache.json``
keyed by the ``citation_for_view`` token, so an interrupted run continues where
it stopped and a later run does not re-request a page it already has.  A page
whose request failed is **not** cached, so it is retried next run.

Skipping: a publication whose ``venue`` is already a real, untruncated venue
(``code/venue_quality.is_complete_venue`` — preprints and placeholders count as
*incomplete*) is **not** fetched at all and writes **no** cache entry: the detail
page could only add volume/pages, and the caller accepted that trade-off rather
than one request per already-good publication.  See ``code/venue_quality.py``.
"""

import json
import os
import re
from datetime import datetime

from bs4 import BeautifulSoup

from code.log_utils import run_log


def _log_venue_line(message="", **_ignored):
    """Default ``print_fn``: per-publication detail goes through the run log."""
    for part in str(message).split("\n"):
        run_log.debug(part.lstrip(), module="venue", indent=1)
from code.common import write_json_atomic
from code.venue_quality import is_complete_venue


# scholarly's own detail-page URL template (publication_parser._CITATIONPUB);
# kept identical so the patched session is exercised exactly like scholarly does.
_DETAIL_PATH = '/citations?hl=en&view_op=view_citation&citation_for_view={0}'

# Same pattern scholarly uses to pull the token out of the profile title link.
_CITATION_FOR_VIEW_RE = re.compile(r'citation_for_view=([\w-]*:[\w-]*)')

# Labels Scholar uses for the venue-ish row.  It is a *set*, not a per-type
# mapping: live pages put conference papers under "Journal" too (verified on
# ICLR / ACM / Nature pages of the same profile).
_VENUE_LABELS = ('journal', 'conference', 'book', 'publication', 'periodical')

#: Value written to ``pub['venue_full_source']``.
VENUE_FULL_SOURCE = 'scholar_detail'

#: Cache file name, written under the crawler's ``output/`` directory.
CACHE_FILENAME = 'venue_detail_cache.json'


# ---------------------------------------------------------------------------
# Pure helpers (token + page parsing)
# ---------------------------------------------------------------------------

def normalize_whitespace(value):
    """Collapse runs of whitespace (including newlines) into single spaces."""
    if value is None:
        return ''
    return ' '.join(str(value).split())


def extract_citation_for_view(url):
    """Return '<author_id>:<token>' from a Scholar view_citation URL, or ''."""
    if not url:
        return ''
    match = _CITATION_FOR_VIEW_RE.search(str(url))
    return match.group(1) if match else ''


def citation_for_view_of(pub):
    """Return a publication's ``citation_for_view`` token.

    The token is the link to the publication's detail page.  It is read from
    the stored field when present, otherwise parsed out of ``pub['url']`` —
    which is where it lives in profiles written before this field existed
    (Scholar always links an author-profile title to the detail page).
    """
    if not isinstance(pub, dict):
        return ''
    token = normalize_whitespace(pub.get('citation_for_view'))
    if token:
        return token
    return extract_citation_for_view(pub.get('url'))


def detail_path(token):
    """Relative detail-page path for *token* (scholarly prepends the host)."""
    return _DETAIL_PATH.format(token)


def parse_detail_fields(source):
    """Return {'<lowercased label>': value} for one detail page.

    The page lists ``<div class="gsc_oci_field">label</div>`` and
    ``<div class="gsc_oci_value">value</div>`` in pairs inside
    ``<div class="gs_scl">`` rows.  Parsing is done by DOM pairing, **not** by a
    regex over ``gsc_oci_value``: the page opens with a ``<style>`` block that
    *mentions* ``.gsc_oci_value{margin-left:116px;…}`` and a naive regex reads
    that CSS as a field value.

    *source* is HTML text or an already-parsed soup.  Returns ``{}`` when the
    page carries no field rows at all (a captcha / consent / redirect page),
    which callers treat as a failed fetch rather than as "no venue".
    """
    soup = (source if hasattr(source, 'find_all')
            else BeautifulSoup(source or '', 'html.parser'))
    fields = {}
    for row in soup.find_all('div', class_='gs_scl'):
        label_el = row.find(class_='gsc_oci_field')
        value_el = row.find(class_='gsc_oci_value')
        if label_el is None or value_el is None:
            continue
        label = normalize_whitespace(label_el.get_text(' ')).lower()
        if not label or label in fields:
            continue
        fields[label] = normalize_whitespace(value_el.get_text(' '))
    return fields


def pick_venue_field(fields):
    """Return (label, value) of the first usable venue row, or ('', '').

    Rows are inspected in page order, so a reordered page still resolves; a
    page without any venue label (or with an empty venue value) yields
    ``('', '')``.
    """
    for label, value in (fields or {}).items():
        if label in _VENUE_LABELS and value:
            return label, value
    return '', ''


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def load_venue_cache(path):
    """Load the detail cache; a missing or corrupt file means "empty"."""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError, TypeError, ValueError) as e:
        run_log.warn(f"{path} is unreadable ({type(e).__name__}: {e}); "
                     f"starting an empty venue cache",
                     module="venue", indent=1)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): value for key, value in data.items()
            if isinstance(value, dict)}


def save_venue_cache(path, cache):
    """Write the detail cache atomically (a crash cannot truncate it)."""
    write_json_atomic(path, cache, ensure_ascii=False, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def enrich_publications_with_venue_full(publications, cache_path=None,
                                        get_soup=None,
                                        print_fn=_log_venue_line,
                                        fetched_at=None):
    """Fetch one detail page per publication and fill in ``venue_full``.

    *get_soup* is a callable that takes a relative Scholar path and returns the
    page (soup or HTML); in production it is
    ``scholarly._Scholarly__nav._get_soup``, i.e. the crawler's patched session
    with its waits/retry/captcha handling.

    Every per-publication failure is caught and counted: a publication that
    cannot be resolved keeps ``venue_full`` empty and the run continues.  Only
    successfully parsed pages are cached (failed requests are retried next run).

    Returns a stats dict: ``total``, ``with_token``, ``missing_token``,
    ``skipped_complete``, ``cache_hits``, ``fetched``, ``resolved``,
    ``no_venue_field``, ``failed``.
    """
    publications = list(publications or [])
    stats = {
        'total': len(publications),
        'with_token': 0,
        'missing_token': 0,
        'skipped_complete': 0,
        'cache_hits': 0,
        'fetched': 0,
        'resolved': 0,
        'no_venue_field': 0,
        'failed': 0,
    }
    cache = load_venue_cache(cache_path)
    stamp = fetched_at or datetime.now().isoformat()

    for index, pub in enumerate(publications, 1):
        if not isinstance(pub, dict):
            continue
        if is_complete_venue(pub.get('venue')):
            # Already a real venue: one more request could only add volume/pages,
            # and no cache entry is written so a later (cheaper) run does not see
            # a page it never needed.
            stats['skipped_complete'] += 1
            continue
        token = citation_for_view_of(pub)
        if not token:
            stats['missing_token'] += 1
            continue
        stats['with_token'] += 1

        entry = cache.get(token)
        if entry is None:
            if not callable(get_soup):
                stats['failed'] += 1
                continue
            try:
                page = get_soup(detail_path(token))
                fields = parse_detail_fields(page)
            except Exception as e:
                # KeyboardInterrupt is not an Exception, so Ctrl+C still
                # propagates and the cache keeps what was already fetched.
                stats['failed'] += 1
                print_fn(f"    [{index}/{len(publications)}] "
                         f"{str(pub.get('title', ''))[:50]}... -> detail page "
                         f"failed ({type(e).__name__}: {e})")
                continue
            if not fields:
                stats['failed'] += 1
                print_fn(f"    [{index}/{len(publications)}] "
                         f"{str(pub.get('title', ''))[:50]}... -> no field table "
                         f"(captcha/consent/redirect page); not cached")
                continue
            label, value = pick_venue_field(fields)
            entry = {
                'status': 'ok' if value else 'no_venue_field',
                'venue_full': value,
                'venue_label': label,
                'fetched_at': stamp,
            }
            cache[token] = entry
            stats['fetched'] += 1
            if not value:
                stats['no_venue_field'] += 1
                print_fn(f"    [{index}/{len(publications)}] "
                         f"{str(pub.get('title', ''))[:50]}... -> no venue field "
                         f"(labels: {', '.join(sorted(fields)) or '-'})")
            if cache_path:
                save_venue_cache(cache_path, cache)
        else:
            stats['cache_hits'] += 1

        if entry.get('status') == 'ok' and entry.get('venue_full'):
            pub['venue_full'] = entry['venue_full']
            pub['venue_full_source'] = VENUE_FULL_SOURCE
            stats['resolved'] += 1

    return stats
