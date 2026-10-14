"""
code/venue_quality.py — Is a venue complete, and is it just a preprint's?

Two of the crawler's paths spend one request per item to read a *better* venue:
the publication detail page (``code/venue_detail.py``) and a citing entry's Cite
dialog (``code/cite_format.py``).  Both are pointless when the string we already
have is already the real, complete venue — and the second of them is pointless
when the author list is already spelled out in full, because those two things are
all it returns.

This module is the **single definition** of both questions:

* :func:`is_complete_venue` — the venue is present, not truncated by Scholar
  (``…`` / ``...``) and not merely a *temporary source* ("arXiv preprint
  arXiv:2401.00001", "bioRxiv", "SSRN", …).  A missing venue is **not** complete:
  "no value" is not the same as "good value".
* :func:`has_full_author_names` — at least one author carries a spelled-out given
  name.  Scholar's list pages write ``<initials> <surname>`` ("H Wang, F Zhang"),
  so the given-name part of an author is every token but the last one.

Both are pure and additionally exposed as the two skip predicates the callers use
(:func:`venue_needs_detail`, :func:`citation_needs_cite_dialog`), so the rule
lives in exactly one place per question.

Honest trade-off (recorded in ``issues.md``): the venue we derive from a Cite
dialog is **not** a superset of the list-page venue — skipping "already complete"
entries gives up the volume/issue/pages that the dialog could add.  That is the
accepted cost of not paying two requests per citing entry for rows that already
have what we came for.
"""

#: Venue strings that name a *temporary* source (a preprint server) rather than
#: the venue the work eventually appeared in.  Case-insensitive substring match on
#: the normalised string, so "arXiv preprint arXiv:2401.00001", "arXiv:2401.00001"
#: and "arxiv.org/abs/2401.00001" are all covered by ``arxiv``.
PREPRINT_VENUE_MARKERS = (
    'arxiv',
    'preprint',
    'biorxiv',
    'medrxiv',
    'ssrn',
    'techrxiv',
    'research square',
    'researchsquare',
    'preprints.org',
)

#: Placeholder venue values that mean "no venue at all".  Missing is **not**
#: complete: those rows are exactly the ones worth one more request.
MISSING_VENUE_VALUES = frozenset([
    '', 'na', 'n/a', 'none', 'null', 'nil', '-', '--', '?', 'unknown', 'n.a.',
])

#: Scholar marks a truncated string with one of these.
TRUNCATION_MARKERS = ('…', '...')

#: A given name of at least this many letters is spelled out; shorter means an
#: initial ("H Wang", "AH Kargaran").
FULL_GIVEN_NAME_MIN_LENGTH = 3

#: Name particles that carry no given name ("J van der Berg").  Without this,
#: ``van`` (3 letters) would be mistaken for a spelled-out given name and the row
#: would be skipped although its author list is still abbreviated.
_NAME_PARTICLES = frozenset([
    'van', 'von', 'der', 'den', 'de', 'del', 'della', 'di', 'da', 'dos', 'das',
    'la', 'le', 'les', 'ter', 'ten', 'bin', 'ibn', 'al', 'el', 'du', 'st',
])


def normalize_venue_text(value):
    """Lower-case and collapse whitespace; '' for None."""
    if value is None:
        return ''
    return ' '.join(str(value).split()).lower()


def is_missing_venue(venue):
    """True when *venue* carries no venue at all (empty or a placeholder)."""
    return normalize_venue_text(venue) in MISSING_VENUE_VALUES


def is_truncated_venue(venue):
    """True when Scholar cut the venue off with ``…`` or ``...``."""
    text = '' if venue is None else str(venue)
    return any(marker in text for marker in TRUNCATION_MARKERS)


def is_preprint_venue(venue):
    """True when *venue* names a preprint/temporary source (or nothing at all)."""
    text = normalize_venue_text(venue)
    if text in MISSING_VENUE_VALUES:
        return True
    return any(marker in text for marker in PREPRINT_VENUE_MARKERS)


def is_complete_venue(venue):
    """True when *venue* is a real, untruncated, non-preprint venue string."""
    if is_missing_venue(venue):
        return False
    if is_truncated_venue(venue):
        return False
    return not is_preprint_venue(venue)


def has_full_author_names(authors):
    """True when at least one author carries a spelled-out given name.

    The list-page shape is ``<initials> <surname>`` per comma-separated author
    ("H Wang, F Zhang"), so everything before the last token of an author is its
    given-name part; a token of ``FULL_GIVEN_NAME_MIN_LENGTH`` letters or more
    (ignoring particles) means it is spelled out.  An author given as a single
    token ("Agarwal") is *no* evidence of a full name — the caller keeps fetching,
    which is the conservative direction.
    """
    for author in str(authors or '').replace(';', ',').split(','):
        tokens = author.split()
        if len(tokens) < 2:
            continue
        for token in tokens[:-1]:
            cleaned = token.strip('.').lower()
            if cleaned in _NAME_PARTICLES:
                continue
            if len(cleaned) >= FULL_GIVEN_NAME_MIN_LENGTH:
                return True
    return False


def venue_needs_detail(venue):
    """True when a publication's *venue* is worth a detail-page request."""
    return not is_complete_venue(venue)


def citation_needs_cite_dialog(citation):
    """True when a citing entry is worth a Cite-dialog request.

    The Cite dialog returns the five formats (venue) *and* the full author names,
    so a row is only skipped when **both** are already in hand: a complete,
    non-preprint venue and an author list that is already spelled out.
    """
    if not isinstance(citation, dict):
        return False
    if not is_complete_venue(citation.get('venue')):
        return True
    return not has_full_author_names(citation.get('authors'))
