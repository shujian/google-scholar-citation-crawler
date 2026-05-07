"""
crawler/citation_models.py — Typed dataclasses for citations and diagnostics.

Replaces bare dicts for per-year entries, year-mode diagnostics,
direct-mode diagnostics, and individual citation entries.  Each class
handles its own to_dict() / from_dict() serialization, producing
output identical to the current JSON format for backward compatibility.
"""

from dataclasses import dataclass, field
from typing import Optional, List


# ---------------------------------------------------------------------------
# Citation
# ---------------------------------------------------------------------------

@dataclass
class Citation:
    """A single citing paper entry with 6 fields."""

    title: str = ""
    authors: str = ""
    venue: str = ""
    year: str = ""
    url: str = ""
    cites_id: Optional[str] = None

    @classmethod
    def from_dict(cls, d):
        if not isinstance(d, dict):
            return cls()
        return cls(
            title=d.get('title', ''),
            authors=d.get('authors', ''),
            venue=d.get('venue', ''),
            year=str(d.get('year', '')),
            url=d.get('url', ''),
            cites_id=d.get('cites_id'),
        )

    def to_dict(self):
        return {
            'title': self.title,
            'authors': self.authors,
            'venue': self.venue,
            'year': self.year,
            'url': self.url,
            'cites_id': self.cites_id,
        }

    def get(self, key, default=None):
        """dict-compat accessor for transitional compatibility."""
        return getattr(self, key, default)

    def __getitem__(self, key):
        """dict-compat subscript access."""
        return getattr(self, key)

    def __contains__(self, key):
        return hasattr(self, key)


# ---------------------------------------------------------------------------
# YearRecord — single-year fetch progress
# ---------------------------------------------------------------------------

@dataclass
class YearRecord:
    """Per-year fetch diagnostics for one calendar year."""

    year: int = 0
    histogram_count: int = 0
    cached_total: int = 0
    seen_total: int = 0
    dedup_count: int = 0
    termination_reason: str = ""

    @classmethod
    def from_dict(cls, d):
        if not isinstance(d, dict):
            return None
        try:
            year = int(d.get('year', 0))
        except (TypeError, ValueError):
            return None
        hc = _coerce_int(d.get('histogram_count', d.get('scholar_total', 0))) or 0
        ct = _coerce_int(d.get('cached_total', 0)) or 0
        dd = _coerce_int(d.get('dedup_count', 0)) or 0
        st = _coerce_int(d.get('seen_total', ct + dd)) or (ct + dd)
        return cls(
            year=year,
            histogram_count=hc,
            cached_total=ct,
            seen_total=st,
            dedup_count=dd,
            termination_reason=d.get('termination_reason', 'iterator_exhausted'),
        )

    def to_dict(self):
        return {
            'year': self.year,
            'histogram_count': self.histogram_count,
            'cached_total': self.cached_total,
            'seen_total': self.seen_total,
            'dedup_count': self.dedup_count,
            'termination_reason': self.termination_reason,
        }

    def get(self, key, default=None):
        """dict-compat accessor."""
        return getattr(self, key, default)

    def __getitem__(self, key):
        return getattr(self, key)

    def __contains__(self, key):
        return hasattr(self, key)


# ---------------------------------------------------------------------------
# YearDiagnostics — year-mode diagnostics collection
# ---------------------------------------------------------------------------

@dataclass
class YearDiagnostics:
    """Year-mode fetch diagnostics: per-year records + summary.

    to_dict() produces the same {str(year): record_dict, ..., 'summary': summary_dict}
    structure as the current JSON output.
    """

    records: List[YearRecord] = field(default_factory=list)
    scholar_total: Optional[int] = None
    histogram_total: int = 0
    cached_total: int = 0
    cached_year_total: int = 0
    seen_total: int = 0
    cached_unyeared_count: int = 0
    dedup_count: int = 0
    scholar_unyeared_count: Optional[int] = None

    @classmethod
    def from_dict(cls, d):
        if not isinstance(d, dict):
            return None
        records = []
        raw_summary = None
        for key, val in d.items():
            if key == 'summary' and isinstance(val, dict):
                raw_summary = val
            elif isinstance(val, dict) and 'year' in val:
                rec = YearRecord.from_dict(val)
                if rec is not None:
                    records.append(rec)
        records.sort(key=lambda r: r.year)
        if raw_summary:
            return cls(
                records=records,
                scholar_total=_coerce_int(raw_summary.get('scholar_total')),
                histogram_total=_coerce_int(raw_summary.get('histogram_total', 0)) or 0,
                cached_total=_coerce_int(raw_summary.get('cached_total', 0)) or 0,
                cached_year_total=_coerce_int(raw_summary.get('cached_year_total', 0)) or 0,
                seen_total=_coerce_int(raw_summary.get('seen_total', 0)) or 0,
                cached_unyeared_count=_coerce_int(raw_summary.get('cached_unyeared_count', 0)) or 0,
                dedup_count=_coerce_int(raw_summary.get('dedup_count', 0)) or 0,
                scholar_unyeared_count=_coerce_int(raw_summary.get('scholar_unyeared_count')),
            )
        # No summary — derive from records
        return cls(
            records=records,
            scholar_total=None,
            histogram_total=sum(r.histogram_count for r in records),
            cached_total=sum(r.cached_total for r in records),
            cached_year_total=sum(r.cached_total for r in records),
            seen_total=sum(r.seen_total for r in records),
            cached_unyeared_count=0,
            dedup_count=sum(r.dedup_count for r in records),
            scholar_unyeared_count=None,
        )

    def to_dict(self):
        result = {str(r.year): r.to_dict() for r in self.records}
        result['summary'] = {
            'scholar_total': self.scholar_total,
            'histogram_total': self.histogram_total,
            'cached_total': self.cached_total,
            'cached_year_total': self.cached_year_total,
            'seen_total': self.seen_total,
            'cached_unyeared_count': self.cached_unyeared_count,
            'dedup_count': self.dedup_count,
            'scholar_unyeared_count': self.scholar_unyeared_count,
        }
        return result

    def values(self):
        """Return records list (compat with dict.values() iteration)."""
        return self.records

    def get(self, key, default=None):
        """dict-compat: lookup by int/str year, or 'summary'."""
        if key == 'summary':
            return self.to_dict().get('summary', default)
        try:
            y = int(key)
        except (TypeError, ValueError):
            return default
        for r in self.records:
            if r.year == y:
                return r
        return default

    def items(self):
        """Return [(str(year), YearRecord), ...] sorted."""
        return [(str(r.year), r) for r in self.records]

    def __bool__(self):
        return len(self.records) > 0 or self.scholar_total is not None

    def __getitem__(self, key):
        """dict-compat: lookup by int/str year or 'summary'."""
        if key == 'summary':
            return self.to_dict()['summary']
        try:
            y = int(key)
        except (TypeError, ValueError):
            raise KeyError(key)
        for r in self.records:
            if r.year == y:
                return r
        raise KeyError(key)


# ---------------------------------------------------------------------------
# DirectDiagnostics — direct-mode diagnostics
# ---------------------------------------------------------------------------

@dataclass
class DirectDiagnostics:
    """Direct-mode fetch diagnostics: summary only (5 fields)."""

    scholar_total: Optional[int] = None
    cached_total: int = 0
    seen_total: int = 0
    dedup_count: int = 0
    termination_reason: str = ""

    @classmethod
    def from_dict(cls, d):
        if not isinstance(d, dict):
            return None
        raw = d.get('summary', d)
        if not isinstance(raw, dict):
            return None
        ct = _coerce_int(raw.get('cached_total', 0)) or 0
        dd = _coerce_int(raw.get('dedup_count', 0)) or 0
        return cls(
            scholar_total=_coerce_int(raw.get('scholar_total')),
            cached_total=ct,
            seen_total=_coerce_int(raw.get('seen_total', ct + dd)) or (ct + dd),
            dedup_count=dd,
            termination_reason=raw.get('termination_reason', 'iterator_exhausted'),
        )

    def to_dict(self):
        return {
            'summary': {
                'scholar_total': self.scholar_total,
                'cached_total': self.cached_total,
                'seen_total': self.seen_total,
                'dedup_count': self.dedup_count,
                'termination_reason': self.termination_reason,
            },
        }

    def __bool__(self):
        return self.scholar_total is not None or self.cached_total > 0

    def get(self, key, default=None):
        """dict-compat: get 'summary' returns to_dict()['summary'], else attribute."""
        if key == 'summary':
            return self.to_dict().get('summary', default)
        return getattr(self, key, default)

    def __getitem__(self, key):
        if key == 'summary':
            return self.to_dict()['summary']
        return getattr(self, key)


# ---------------------------------------------------------------------------
# ResumeState — unified resume position for both direct and year-based fetch
# ---------------------------------------------------------------------------

@dataclass
class ResumeState:
    """Resume position within a fetch segment (direct or per-year).

    next_index is page-aligned so the retry re-fetches the whole page;
    already-saved items serve as old_citations for dedup.
    """

    next_index: int = 0
    source_scholar_total: int = 0
    citedby_url: str = ""

    @classmethod
    def from_dict(cls, d):
        if not isinstance(d, dict):
            return None
        try:
            ni = int(d.get('next_index', 0))
            st = int(d.get('source_scholar_total', 0))
        except (TypeError, ValueError):
            return None
        url = d.get('citedby_url', '')
        if ni < 0 or st < 0 or ni > st or not url:
            return None
        return cls(next_index=ni, source_scholar_total=st, citedby_url=url)

    def to_dict(self):
        return {
            'next_index': self.next_index,
            'source_scholar_total': self.source_scholar_total,
            'citedby_url': self.citedby_url,
        }

    def is_valid(self):
        return self.next_index >= 0 and self.source_scholar_total > 0 and bool(self.citedby_url)

    def page_start(self):
        return _page_align(self.next_index)

    def in_page_skip(self):
        return self.next_index - self.page_start()

    def request_url(self, base_url=None):
        """Return the URL with &start= appended for the resume page."""
        url = base_url or self.citedby_url
        ps = self.page_start()
        if ps <= 0:
            return url
        import re
        sep = '&' if '?' in url else '?'
        if re.search(r'([?&])start=\d+', url):
            return re.sub(r'([?&])start=\d+', lambda m: f'{m.group(1)}start={ps}', url)
        return f'{url}{sep}start={ps}'


def _page_align(index):
    try: index = int(index or 0)
    except (TypeError, ValueError): return 0
    if index <= 0: return 0
    return (index // 10) * 10


# ---------------------------------------------------------------------------
# FetchPolicy — fetch strategy decision
# ---------------------------------------------------------------------------

@dataclass
class FetchPolicy:
    """Result of resolve_citation_fetch_policy."""

    strategy: str = 'direct'  # 'year' | 'direct'
    pub_year: Optional[int] = None
    reason: str = ""

    def is_year(self): return self.strategy == 'year'
    def is_direct(self): return self.strategy == 'direct'

    def get(self, key, default=None):
        """dict-compat accessor."""
        return getattr(self, key, default)

    def __getitem__(self, key):
        return getattr(self, key)

    def __contains__(self, key):
        return hasattr(self, key)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _coerce_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
