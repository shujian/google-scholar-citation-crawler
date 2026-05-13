"""
crawler/output_state.py — Read fetch state from the aggregate output JSON.

The output file (author_{id}_paper_citations.json) becomes the authoritative
cross-run state source.  Per-paper cache files in scholar_cache/ are still
written for within-run resume, but strategy decisions on the next run read
from the output file first and fall back to cache only when output state is
absent.
"""

import json
import os
from dataclasses import dataclass
from typing import Optional

from crawler.citation_io import (
    derive_citation_cache_state,
    resolve_citation_status_from_state,
)


# ---------------------------------------------------------------------------
# PaperFetchState dataclass
# ---------------------------------------------------------------------------

_FETCH_STATE_KEYS = frozenset([
    'title',
    'pub_url',
    'citedby_url',
    'fetch_strategy',
    'num_citations_on_scholar',
    'complete_fetch_attempt',
    'year_fetch_diagnostics',
    'direct_fetch_diagnostics',
    'year_records',
    'fetched_at',
])

_DIRECT_SUMMARY_KEYS = frozenset([
    'scholar_total', 'cached_total', 'seen_total', 'dedup_count', 'termination_reason',
])

_YEAR_SUMMARY_KEYS = frozenset([
    'scholar_total', 'histogram_total',
    'cached_total', 'cached_year_total',
    'seen_total', 'cached_unyeared_count',
    'dedup_count', 'scholar_unyeared_count',
])

_PER_YEAR_KEYS = frozenset([
    'year', 'histogram_count', 'cached_total', 'seen_total', 'dedup_count',
    'termination_reason',
])


@dataclass
class PaperFetchState:
    """Per-paper fetch progress snapshot persisted in the output JSON.

    All fields are private; access via properties or from_dict/to_dict.
    Mutations go through restore_*() or from_dict().
    """

    _title: str = ""
    _pub_url: str = ""
    _citedby_url: str = ""
    _fetch_strategy: Optional[str] = None
    _num_citations_on_scholar: Optional[int] = None
    _complete_fetch_attempt: bool = False
    _year_fetch_diagnostics: Optional[dict] = None
    _direct_fetch_diagnostics: Optional[dict] = None
    _year_records: Optional[list] = None
    _scholar_changed: bool = False
    _fetched_at: Optional[str] = None

    # -- read-only properties -----------------------------------------------

    @property
    def title(self): return self._title
    @property
    def pub_url(self): return self._pub_url
    @property
    def citedby_url(self): return self._citedby_url
    @property
    def fetch_strategy(self): return self._fetch_strategy
    @property
    def num_citations_on_scholar(self): return self._num_citations_on_scholar
    @property
    def complete_fetch_attempt(self): return self._complete_fetch_attempt
    @property
    def scholar_changed(self): return self._scholar_changed
    @property
    def year_fetch_diagnostics(self): return self._year_fetch_diagnostics
    @property
    def direct_fetch_diagnostics(self): return self._direct_fetch_diagnostics
    @property
    def year_records(self): return self._year_records
    @property
    def fetched_at(self): return self._fetched_at

    @classmethod
    def from_dict(cls, d):
        if not isinstance(d, dict):
            return cls()
        year_records = _normalize_year_records(
            d.get('year_records') or d.get('year_fetch_diagnostics')
        )
        yfd = _normalize_year_summary_dict(d.get('year_fetch_diagnostics'))
        # Fallback: derive summary from year_records when diagnostics is absent
        if yfd is None and year_records:
            yfd = _normalize_year_summary_from_records(year_records)

        return cls(
            _title=d.get('title', ''),
            _pub_url=d.get('pub_url', ''),
            _citedby_url=d.get('citedby_url', ''),
            _fetch_strategy=d.get('fetch_strategy'),
            _num_citations_on_scholar=_coerce_int(d.get('num_citations_on_scholar')),
            _complete_fetch_attempt=bool(
                d.get('complete_fetch_attempt', d.get('complete', False))
            ),
            _year_fetch_diagnostics=yfd,
            _direct_fetch_diagnostics=_normalize_direct_diagnostics(
                d.get('direct_fetch_diagnostics')
            ),
            _year_records=year_records,
            _scholar_changed=bool(d.get('scholar_changed', False)),
            _fetched_at=d.get('fetched_at'),
        )

    def to_dict(self):
        return {
            'title': self._title,
            'pub_url': self._pub_url,
            'citedby_url': self.citedby_url,
            'fetch_strategy': self.fetch_strategy,
            'num_citations_on_scholar': self.num_citations_on_scholar,
            'complete_fetch_attempt': self.complete_fetch_attempt,
            'scholar_changed': self.scholar_changed,
            'year_fetch_diagnostics': _normalize_year_summary_dict(
                self.year_fetch_diagnostics
            ),
            'direct_fetch_diagnostics': _normalize_direct_diagnostics(
                self.direct_fetch_diagnostics
            ),
            'year_records': _normalize_year_records(
                self.year_records or self.year_fetch_diagnostics
            ),
            'fetched_at': self.fetched_at,
        }

    def restore_year_diag_from_year_records(self):
        """Rebuild year_fetch_diagnostics by summing year_records.

        Returns self for chaining.
        """
        if not self.year_records:
            return self
        derived = _normalize_year_summary_from_records(self.year_records)
        # All year-mode fields come from records.  Only scholar_total
        # (the Scholar page total) cannot be derived — keep the existing
        # value or fall back to num_citations_on_scholar.
        existing = self._year_fetch_diagnostics or {}
        scholar_total = (existing.get('scholar_total')
                         or self._num_citations_on_scholar)
        self._year_fetch_diagnostics = {
            'scholar_total': scholar_total,
            'histogram_total': derived['histogram_total'],
            'cached_total': derived['cached_total'],
            'cached_year_total': derived['cached_year_total'],
            'seen_total': derived['seen_total'],
            'cached_unyeared_count': 0,   # year mode drops unyeared
            'dedup_count': derived['dedup_count'],
            'scholar_unyeared_count': (
                max(0, (scholar_total or 0) - derived['histogram_total'])
                if scholar_total is not None else None
            ),
        }
        return self

    def restore_direct_diag_from_citations(self, citations):
        """Rebuild direct_fetch_diagnostics from a citations list.

        Only acts when direct_fetch_diagnostics is absent.
        Returns self for chaining.
        """
        if isinstance(self.direct_fetch_diagnostics, dict):
            return self
        n = len(citations)
        self.direct_fetch_diagnostics = {
            'scholar_total': self.num_citations_on_scholar,
            'cached_total': n,
            'seen_total': n,
            'dedup_count': 0,
            'termination_reason': 'derived_from_citations',
        }
        return self

    def restore_from_cache_snapshot(self, cache_snapshot):
        """Restore all private fetch-state fields from a cache snapshot dict.

        Intended for updating an in-memory PaperFetchState after a fetch
        completes.  Encapsulates every private-field write so callers never
        reach into the leading-underscore fields directly.

        Returns self for chaining.
        """
        from datetime import datetime

        # year_records + derived year_fetch_diagnostics
        yr = cache_snapshot.get('year_records') or []
        if yr:
            self._year_records = yr
            self.restore_year_diag_from_year_records()

        # direct_fetch_diagnostics
        dfd = cache_snapshot.get('direct_fetch_diagnostics') or {}
        if isinstance(dfd, dict) and dfd.get('scholar_total') is not None:
            self._direct_fetch_diagnostics = dfd

        # year_fetch_diagnostics (standalone summary)
        yfd = cache_snapshot.get('year_fetch_diagnostics') or {}
        if isinstance(yfd, dict) and yfd.get('scholar_total') is not None:
            self._year_fetch_diagnostics = yfd

        # fetched_at timestamp
        self._fetched_at = cache_snapshot.get('fetched_at') or datetime.now().isoformat()

        return self

    def mark_scholar_changed(self):
        """Mark that the Scholar total has changed since the last run."""
        self._scholar_changed = True
        return self

    def clear_scholar_changed(self):
        """Clear the scholar-changed flag after a successful fetch."""
        self._scholar_changed = False
        return self

    def need_fetch(self, current_scholar_total=None, pub_year='N/A',
                   year_based_threshold=50):
        """Return True if the paper needs to be (re-)fetched.

        True when: scholar_changed is set, or the data is incomplete.
        """
        if self._scholar_changed:
            return True
        return not self.is_complete(current_scholar_total, pub_year,
                                    year_based_threshold)

    def is_complete(self, current_scholar_total=None, pub_year='N/A',
                    year_based_threshold=50):
        from crawler.citation_cache import is_data_complete
        strategy, summary = self._infer_strategy_and_summary()
        if strategy is None:
            return False
        return is_data_complete(strategy, summary)

    def _infer_strategy_and_summary(self):
        """Return (strategy, summary) using the same inference rules as is_complete."""
        strategy = self.fetch_strategy
        if not strategy:
            if self.year_fetch_diagnostics or self.year_records:
                strategy = 'year'
            elif self.direct_fetch_diagnostics:
                strategy = 'direct'
            else:
                return None, None
        if strategy == 'year':
            summary = self.year_fetch_diagnostics
            if summary is None and self.year_records:
                summary = _normalize_year_summary_from_records(self.year_records)
            summary = summary or {}
        else:
            summary = self.direct_fetch_diagnostics or {}
        return strategy, summary

    def completeness_diag(self, citations_len=None):
        strategy, summary = self._infer_strategy_and_summary()
        if strategy is None:
            return '  diagnostics summary absent'
        if strategy == 'year':
            target = summary.get('histogram_total')
            seen = summary.get('seen_total')
            label = 'histogram_total'
        else:
            target = summary.get('scholar_total')
            seen = summary.get('seen_total')
            label = 'scholar_total'
        if target is not None and seen is not None:
            cmp_sym = '≥' if (seen or 0) >= (target or 0) else '<'
            return f'  {strategy}: seen_total={seen} {cmp_sym} {label}={target}'
        return f'  {strategy}: diagnostics summary absent'


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _coerce_int(value):
    try: return int(value)
    except (TypeError, ValueError): return None


def _normalize_direct_diagnostics(dfd):
    """Return a normalized direct-fetch diagnostics dict.

    Accepts both old format (with 'summary' sub-key) and current format
    (diagnostics IS the summary).  Always returns the current format.
    """
    if not isinstance(dfd, dict): return None
    raw = dfd.get('summary', dfd)  # accept both formats
    if not isinstance(raw, dict): return None
    dd = raw.get('dedup_count', 0) or 0
    ct = raw.get('cached_total', 0) or 0
    return {
        'scholar_total': _coerce_int(raw.get('scholar_total')),
        'cached_total': ct,
        'seen_total': _coerce_int(raw.get('seen_total', ct + dd)) or (ct + dd),
        'dedup_count': dd,
        'termination_reason': raw.get('termination_reason', 'iterator_exhausted'),
    }


def _normalize_year_records(data):
    """Return a sorted list of per-year record dicts (oldest first).

    Accepts a year_records list or a year_fetch_diagnostics dict (old format).
    """
    if isinstance(data, list):
        records = []
        for item in data:
            if isinstance(item, dict) and 'year' in item:
                try: year = int(item['year'])
                except (TypeError, ValueError): continue
                hc = item.get('histogram_count', item.get('scholar_total', 0)) or 0
                ct = item.get('cached_total', 0) or 0
                dd = item.get('dedup_count', 0) or 0
                records.append({
                    'year': year,
                    'histogram_count': hc,
                    'cached_total': ct,
                    'seen_total': item.get('seen_total', ct + dd),
                    'dedup_count': dd,
                    'termination_reason': item.get('termination_reason', 'iterator_exhausted'),
                })
        records.sort(key=lambda r: r['year'])
        return records if records else None
    if isinstance(data, dict):
        records = []
        for key, diag in data.items():
            if not isinstance(diag, dict) or 'year' not in diag: continue
            try: year = int(diag['year'])
            except (TypeError, ValueError): continue
            hc = diag.get('histogram_count', diag.get('scholar_total', 0)) or 0
            ct = diag.get('cached_total', 0) or 0
            dd = diag.get('dedup_count', 0) or 0
            records.append({
                'year': year,
                'histogram_count': hc,
                'cached_total': ct,
                'seen_total': diag.get('seen_total', ct + dd),
                'dedup_count': dd,
                'termination_reason': diag.get('termination_reason', 'iterator_exhausted'),
            })
        records.sort(key=lambda r: r['year'])
        return records if records else None
    return None


def _normalize_year_summary_from_records(records):
    """Derive the year-fetch summary from per-year records."""
    if not records:
        return None
    return {
        'scholar_total': None,
        'histogram_total': sum(r.get('histogram_count', r.get('scholar_total', 0)) or 0 for r in records),
        'cached_total': sum(r.get('cached_total', 0) or 0 for r in records),
        'cached_year_total': sum(r.get('cached_total', 0) or 0 for r in records),
        'seen_total': sum(r.get('seen_total', 0) or 0 for r in records),
        'cached_unyeared_count': 0,
        'dedup_count': sum(r.get('dedup_count', 0) or 0 for r in records),
        'scholar_unyeared_count': None,
    }


def _normalize_year_summary_dict(yfd):
    """Return a normalized year-fetch diagnostics dict from *yfd*."""
    if not isinstance(yfd, dict):
        return None
    # Current format: yfd IS the summary
    if 'histogram_total' in yfd or 'scholar_total' in yfd:
        return _normalize_year_summary(yfd)
    # Old format: summary nested under 'summary' sub-key
    raw = yfd.get('summary')
    if isinstance(raw, dict):
        return _normalize_year_summary(raw)
    return None


def _normalize_year_summary(raw):
    return {
        'scholar_total': _coerce_int(raw.get('scholar_total')),
        'histogram_total': _coerce_int(raw.get('histogram_total', 0)) or 0,
        'cached_total': _coerce_int(raw.get('cached_total', 0)) or 0,
        'cached_year_total': _coerce_int(raw.get('cached_year_total', 0)) or 0,
        'seen_total': _coerce_int(raw.get('seen_total', 0)) or 0,
        'cached_unyeared_count': _coerce_int(raw.get('cached_unyeared_count', 0)) or 0,
        'dedup_count': _coerce_int(raw.get('dedup_count', 0)) or 0,
        'scholar_unyeared_count': _coerce_int(raw.get('scholar_unyeared_count')),
    }


# ---------------------------------------------------------------------------
# Output file I/O
# ---------------------------------------------------------------------------

def load_output_fetch_state(output_path):
    if not os.path.exists(output_path):
        return {}
    try:
        with open(output_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError, TypeError, AttributeError):
        return {}
    result = {}
    for paper in data.get('papers', []):
        raw = paper.get('_fetch_state')
        if isinstance(raw, dict) and raw.get('title'):
            result[raw['title']] = PaperFetchState.from_dict(raw)
    return result


def resolve_citation_status_from_output(pub, state, year_based_threshold):
    if pub.get('num_citations') == 0:
        return 'skip_zero'
    if isinstance(state, PaperFetchState):
        if state.need_fetch(pub.get('num_citations'), pub.get('year', 'N/A'),
                            year_based_threshold):
            return 'partial'
        return 'complete'
    cache_state = derive_citation_cache_state(pub, state, year_based_threshold)
    return resolve_citation_status_from_state(cache_state)


def extract_fetch_state(cached):
    if not cached:
        return PaperFetchState()
    state = PaperFetchState.from_dict(cached)
    citations = cached.get('citations', []) or []
    if state.num_citations_on_scholar is None:
        yfd = (cached.get('year_fetch_diagnostics') or {})
        year_summary = yfd.get('summary', yfd) if isinstance(yfd, dict) else {}
        dfd = (cached.get('direct_fetch_diagnostics') or {})
        direct_summary = dfd.get('summary') or {}
        seen = year_summary.get('seen_total') or direct_summary.get('seen_total')
        if seen is not None:
            state.num_citations_on_scholar = seen
        elif state.complete_fetch_attempt:
            state.num_citations_on_scholar = len(citations)
        else:
            state.num_citations_on_scholar = len(citations)
    return state
