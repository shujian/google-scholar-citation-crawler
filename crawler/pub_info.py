"""
crawler/pub_info.py — PubInfo dataclass for publication metadata.

PubInfo wraps the 8-field publication dict that appears as "pub" in
the output JSON papers array.  It enforces a fixed set of keys and
a stable field order on serialisation.
"""

from dataclasses import dataclass
from typing import Optional


_PUB_FIELDS = [
    'no', 'title', 'year', 'venue', 'authors',
    'num_citations', 'url', 'citedby_url',
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

    @classmethod
    def from_dict(cls, d):
        """Construct from a raw dict (profile JSON or scholarly output).

        Only the 8 known keys are extracted; everything else is ignored.
        """
        if not isinstance(d, dict):
            return cls()
        return cls(
            no=_coerce_int(d.get('no', 0)) or 0,
            title=d.get('title', ''),
            year=str(d.get('year', '')),
            venue=d.get('venue', ''),
            authors=d.get('authors', ''),
            num_citations=_coerce_int(d.get('num_citations', 0)) or 0,
            url=d.get('url', ''),
            citedby_url=d.get('citedby_url', ''),
        )

    @classmethod
    def from_scholarly(cls, pub, index):
        """Construct from a raw scholarly publication dict + 1-based index."""
        bib = pub.get('bib', {}) if isinstance(pub, dict) else {}
        author = bib.get('author', '')
        if isinstance(author, list):
            author = '; '.join(author)
        return cls(
            no=index,
            title=bib.get('title', ''),
            year=str(bib.get('pub_year', '')),
            venue=bib.get('citation', bib.get('venue', bib.get('journal', ''))),
            authors=author,
            num_citations=pub.get('num_citations', 0) if isinstance(pub, dict) else 0,
            url=pub.get('pub_url', pub.get('eprint_url', '')) if isinstance(pub, dict) else '',
            citedby_url=pub.get('citedby_url', '') if isinstance(pub, dict) else '',
        )

    def to_dict(self):
        """Serialize to the 8-key dict written as "pub" in the output JSON."""
        return {
            'no': self.no,
            'title': self.title,
            'year': self.year,
            'venue': self.venue,
            'authors': self.authors,
            'num_citations': self.num_citations,
            'url': self.url,
            'citedby_url': self.citedby_url,
        }


def _coerce_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
