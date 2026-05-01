"""
crawler/citation_identity.py — Citation deduplication key and info extraction.

All functions are pure (no instance state).  They are used by
PaperCitationFetcher to normalise raw scholarly results into a stable
identity key and a clean dict suitable for caching.
"""

import re

_SCHOLARPUBRE = re.compile(r'cites=([\d,]*)')


def _extract_cites_id_from_url(url):
    """Extract cites_id from a Google Scholar citedby_url, or None."""
    if not url:
        return None
    match = _SCHOLARPUBRE.search(str(url))
    if not match:
        return None
    parts = [p.strip() for p in match.group(1).split(',') if p.strip()]
    return parts if parts else None


def normalize_cites_id(cites_id):
    """Return a normalised string cites_id, or None if absent/empty."""
    if cites_id in (None, '', [], ()):
        return None
    if isinstance(cites_id, (list, tuple, set)):
        parts = [str(part).strip() for part in cites_id if str(part).strip()]
        return ','.join(parts) if parts else None
    value = str(cites_id).strip()
    return value or None


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


def extract_citation_info(pub, fallback_year=None):
    """
    Convert a raw scholarly publication dict into a clean citation dict.

    fallback_year is used when the bib has no usable pub_year.
    """
    bib = pub.get('bib', {})
    authors = bib.get('author', [])
    year = str(bib.get('pub_year', 'N/A'))
    if str(year).strip().lower() in ('', 'n/a', 'na', '?') and fallback_year is not None:
        year = str(fallback_year)
    raw_cites_id = pub.get('cites_id')
    if raw_cites_id in (None, '', [], ()):
        raw_cites_id = _extract_cites_id_from_url(pub.get('citedby_url'))
    return {
        'title':    bib.get('title', 'N/A'),
        'authors':  ', '.join(authors) if isinstance(authors, list) else str(authors),
        'venue':    bib.get('venue', 'N/A'),
        'year':     year,
        'url':      pub.get('pub_url', pub.get('eprint_url', 'N/A')),
        'cites_id': normalize_cites_id(raw_cites_id),
    }
