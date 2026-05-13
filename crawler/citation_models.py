"""
crawler/citation_models.py — Typed dataclasses for citation fetch state and policy.

Replaces bare dicts for per-year entries, resume state, fetch policy,
and page-alignment utilities.  Each class handles its own to_dict() /
from_dict() serialization, producing output identical to the current
JSON format for backward compatibility.
"""

from dataclasses import dataclass, field
from typing import Optional, List


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
