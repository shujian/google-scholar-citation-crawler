"""
code/pub_info.py — PubInfo dataclass for publication metadata.

PubInfo wraps the publication dict that appears as "pub" in the output JSON
papers array.  It enforces a fixed set of keys and a stable field order on
serialisation.

The three fields after ``citedby_url`` are additive (2026-10-14):
``citation_for_view`` is the token of the publication's Scholar detail page and
``venue_full`` is the untruncated venue read from that page (with
``venue_full_source`` recording where it came from).  ``venue`` itself is never
rewritten — see ``code/venue_detail.py`` for why.
"""

from dataclasses import dataclass
from typing import Optional

from code.citation_cache import normalize_citation_year
from code.venue_detail import extract_citation_for_view


_PUB_FIELDS = [
    'no', 'title', 'year', 'venue', 'authors',
    'num_citations', 'url', 'citedby_url',
    'citation_for_view', 'venue_full', 'venue_full_source',
]


@dataclass
class PubInfo:
    """Publication metadata from the author profile."""

    no: int = 0
    title: str = ""
    year: str = ""
    venue: str = ""
    authors: str = ""
    num_citations: int = 0
    url: str = ""
    citedby_url: str = ""
    citation_for_view: str = ""
    venue_full: str = ""
    venue_full_source: str = ""

    @classmethod
    def from_dict(cls, d):
        """Construct from a raw dict (profile JSON or scholarly output).

        Only the known keys are extracted; everything else is ignored.  The
        detail-page token is taken from the stored field when present and
        otherwise recovered from ``url``, so profiles written before that field
        existed still yield it without a re-crawl.
        """
        if not isinstance(d, dict):
            return cls()
        url = d.get('url', '')
        token = d.get('citation_for_view', '') or extract_citation_for_view(url)
        return cls(
            no=_coerce_int(d.get('no', 0)) or 0,
            title=d.get('title', ''),
            year=normalize_citation_year(d.get('year')),
            venue=d.get('venue', ''),
            authors=d.get('authors', ''),
            num_citations=_coerce_int(d.get('num_citations', 0)) or 0,
            url=url,
            citedby_url=d.get('citedby_url', ''),
            citation_for_view=token,
            venue_full=d.get('venue_full') or '',
            venue_full_source=d.get('venue_full_source') or '',
        )

    @classmethod
    def from_scholarly(cls, pub, index):
        """Construct from a raw scholarly publication dict + 1-based index."""
        bib = pub.get('bib', {}) if isinstance(pub, dict) else {}
        author = bib.get('author', '')
        if isinstance(author, list):
            author = '; '.join(author)
        url = pub.get('pub_url', pub.get('eprint_url', '')) if isinstance(pub, dict) else ''
        # scholarly extracts the detail-page token itself (author_pub_id); the
        # URL fallback covers stub/legacy dicts that do not carry it.
        token = pub.get('author_pub_id', '') if isinstance(pub, dict) else ''
        if not isinstance(token, str):
            token = ''
        if not token:
            token = extract_citation_for_view(url)
        return cls(
            no=index,
            title=bib.get('title', ''),
            year=normalize_citation_year(bib.get('pub_year')),
            venue=bib.get('citation', bib.get('venue', bib.get('journal', ''))),
            authors=author,
            num_citations=pub.get('num_citations', 0) if isinstance(pub, dict) else 0,
            url=url,
            citedby_url=pub.get('citedby_url', '') if isinstance(pub, dict) else '',
            citation_for_view=token,
        )

    def to_dict(self):
        """Serialize to the dict written as "pub" in the output JSON."""
        return {
            'no': self.no,
            'title': self.title,
            'year': self.year,
            'venue': self.venue,
            'authors': self.authors,
            'num_citations': self.num_citations,
            'url': self.url,
            'citedby_url': self.citedby_url,
            'citation_for_view': self.citation_for_view,
            'venue_full': self.venue_full,
            'venue_full_source': self.venue_full_source,
        }


def _coerce_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
