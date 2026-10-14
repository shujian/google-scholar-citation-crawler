"""
code/citation_identity.py — Citation deduplication key and info extraction.

All functions are pure (no instance state).  They are used by
PaperCitationFetcher to normalise raw scholarly results into a stable
identity key and a clean dict suitable for caching.
"""

import re

from code.citation_cache import normalize_citation_year
from code.venue_key import resolve_venue_key

_SCHOLARPUBRE = re.compile(r'cites=([\d,]*)')


_SCHOLARBIB_CID_RE = re.compile(r'info:([^:]+):scholar\.google\.com')

# scholarly builds url_scholarbib from the row's `data-cid`; when a row has none,
# the attribute is absent and `.get()` yields None, so the URL becomes
# 'info:None:scholar.google.com'.  Taken literally that string looks like a real
# cluster id, and every such row would then share the identity key
# `cites_id\tNone` (collapsing unrelated citations into one, and handing the
# downloader a bogus primary key).  The other spellings are the same class of
# trap from hand-edited or legacy state.
_INVALID_CITES_IDS = frozenset(['none', 'null', 'n/a', 'na'])


def _clean_cites_id(value):
    """Return a usable single cluster id, or None for empty/invalid values."""
    text = '' if value is None else str(value).strip()
    if not text or text.lower() in _INVALID_CITES_IDS:
        return None
    return text


def _extract_cites_id_from_url(url):
    """Extract cites_id from a Google Scholar citedby_url, or None."""
    if not url:
        return None
    match = _SCHOLARPUBRE.search(str(url))
    if not match:
        return None
    parts = [_clean_cites_id(p) for p in match.group(1).split(',')]
    parts = [p for p in parts if p]
    return parts if parts else None


def _extract_cid_from_scholarbib(url):
    """Extract Google Scholar cluster ID (cid) from url_scholarbib, or None."""
    if not url:
        return None
    match = _SCHOLARBIB_CID_RE.search(str(url))
    if not match:
        return None
    return _clean_cites_id(match.group(1))


def extract_cid_from_scholarbib(url_scholarbib):
    """Public accessor for the row's ``data-cid`` (scholarly: url_scholarbib).

    The cid addresses Scholar's **Cite dialog** for that result row — one page
    carrying all five citation formats plus the signed BibTeX link
    (``code/cite_format.py``).  It is deliberately a *separate* value from
    ``cites_id``: ``cites_id`` identifies the cited work's cluster list and takes
    part in the identity keys, the cid identifies one citing row.
    """
    return _extract_cid_from_scholarbib(url_scholarbib)


def normalize_cites_id(cites_id):
    """Return a normalised string cites_id, or None if absent/empty/invalid."""
    if cites_id in (None, '', [], ()):
        return None
    if isinstance(cites_id, (list, tuple, set)):
        parts = [_clean_cites_id(part) for part in cites_id]
        parts = [part for part in parts if part]
        return ','.join(parts) if parts else None
    return _clean_cites_id(cites_id)


def normalize_identity_part(value):
    """Lower-case, collapse whitespace, return empty string for None."""
    if value is None:
        return ''
    return ' '.join(str(value).strip().lower().split())


def citation_identity_keys(info):
    """
    Return a list of identity keys for a citation info dict.

    The first key is always cites_id-based (most specific); the second is a
    title+venue or title+authors fallback.  Callers may use any key for
    deduplication.
    """
    keys = []
    cites_id = normalize_cites_id(info.get('cites_id'))
    if cites_id:
        keys.append(f"cites_id\t{cites_id}")
    title = normalize_identity_part(info.get('title'))
    venue = normalize_identity_part(info.get('venue'))
    authors = normalize_identity_part(info.get('authors'))
    if venue:
        keys.append(f"meta\t{title}\t{venue}")
    elif authors:
        keys.append(f"meta\t{title}\t{authors}")
    else:
        keys.append(f"meta\t{title}")
    # deduplicate while preserving order
    seen = set()
    deduped = []
    for key in keys:
        if key not in seen:
            seen.add(key)
            deduped.append(key)
    return deduped


def citation_identity_key(info):
    """Return the primary identity key for a citation info dict."""
    return citation_identity_keys(info)[0]


def citation_output_identity_keys(info):
    """
    Return the identity keys used to collapse duplicate rows **in the output**.

    Stricter than :func:`citation_identity_keys`, which always appends a
    title-based fallback key: that extra key merged rows Scholar itself reports
    as different clusters (same title, different ``cites_id``) and silently
    dropped entries the downstream stages must keep apart (K123: "Google says
    they differ, so they differ").

    A row therefore only ever matches on Scholar's own identity:

    * a row **with** a real ``cites_id`` yields that key only, so it still
      collapses with rows carrying the same cluster id (the same work re-listed
      by a later run with richer metadata);
    * a row **without** one yields the ``title + venue`` (or ``title +
      authors``) fallback key from :func:`citation_identity_keys`, so two rows
      that are fully equal on those fields still collapse.

    A row with a real ``cites_id`` is never matched against a row without one,
    and two rows sharing only a normalised title are never merged.
    """
    if normalize_cites_id(info.get('cites_id')):
        return citation_identity_keys(info)[:1]
    return citation_identity_keys(info)


def extract_citation_info(pub, fallback_year=None):
    """
    Convert a raw scholarly publication dict into a clean citation dict.

    fallback_year is used when the bib has no usable pub_year.

    ``cid`` is the row's Scholar cluster id (scholarly keeps it in
    ``url_scholarbib``).  It is **purely additive** — it is not part of any
    identity key, and ``cites_id``/``authors``/``venue``/``title``/``year``/
    ``url`` are unchanged byte for byte — but it is what makes Scholar's Cite
    dialog addressable later (see ``code/cite_format.py``).

    ``venue_key`` / ``venue_key_source`` are derived from the row's venue + year
    by ``code/venue_key.py`` (``ACL2026``; the raw venue text when the venue is
    not in the table; ``None`` when the row carries no venue).  Citation-list
    venues are frequently truncated by Scholar, so many rows keep their raw text
    until the table is extended — the data-upgrade tool lists them for review.
    """
    bib = pub.get('bib', {})
    authors = bib.get('author', [])
    year = normalize_citation_year(bib.get('pub_year'), fallback_year)
    venue = bib.get('venue', 'N/A')
    raw_cites_id = pub.get('cites_id')
    if raw_cites_id in (None, '', [], ()):
        raw_cites_id = _extract_cites_id_from_url(pub.get('citedby_url'))
    if raw_cites_id in (None, '', [], ()):
        raw_cites_id = _extract_cid_from_scholarbib(pub.get('url_scholarbib'))
    key, key_source = resolve_venue_key(venue, year)
    return {
        'title':    bib.get('title', 'N/A'),
        'authors':  ', '.join(authors) if isinstance(authors, list) else str(authors),
        'venue':    venue,
        'year':     year,
        'url':      pub.get('pub_url', pub.get('eprint_url', 'N/A')),
        'cites_id': normalize_cites_id(raw_cites_id),
        'cid':      _extract_cid_from_scholarbib(pub.get('url_scholarbib')),
        'venue_key': key,
        'venue_key_source': key_source,
    }
