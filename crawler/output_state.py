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
    """Per-paper fetch progress snapshot persisted in the output JSON."""

    title: str = ""
    pub_url: str = ""
    citedby_url: str = ""
    fetch_strategy: Optional[str] = None
    num_citations_on_scholar: Optional[int] = None
    complete_fetch_attempt: bool = False
    year_fetch_diagnostics: Optional[dict] = None
    direct_fetch_diagnostics: Optional[dict] = None
    year_records: Optional[list] = None
    fetched_at: Optional[str] = None

    @classmethod
    def from_dict(cls, d):
        if not isinstance(d, dict):
            return cls()
        return cls(
            title=d.get('title', ''),
            pub_url=d.get('pub_url', ''),
            citedby_url=d.get('citedby_url', ''),
            fetch_strategy=d.get('fetch_strategy'),
            num_citations_on_scholar=_coerce_int(d.get('num_citations_on_scholar')),
            complete_fetch_attempt=bool(
                d.get('complete_fetch_attempt', d.get('complete', False))
            ),
            year_fetch_diagnostics=_normalize_year_summary_dict(
                d.get('year_fetch_diagnostics')
            ),
            direct_fetch_diagnostics=_normalize_direct_diagnostics(
                d.get('direct_fetch_diagnostics')
            ),
            year_records=_normalize_year_records(
                d.get('year_records') or d.get('year_fetch_diagnostics')
            ),
            fetched_at=d.get('fetched_at'),
        )

    def to_dict(self):
        return {
            'title': self.title,
            'pub_url': self.pub_url,
            'citedby_url': self.citedby_url,
            'fetch_strategy': self.fetch_strategy,
            'num_citations_on_scholar': self.num_citations_on_scholar,
            'complete_fetch_attempt': self.complete_fetch_attempt,
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

    def is_complete(self, current_scholar_total=None, pub_year='N/A',
                    year_based_threshold=50):
        from crawler.citation_cache import is_data_complete
        strategy = self.fetch_strategy
        if not strategy:
            # Infer from which diagnostics are populated
            if self.year_fetch_diagnostics:
                strategy = 'year'
            elif self.direct_fetch_diagnostics:
                strategy = 'direct'
            else:
                return False
        if strategy == 'year':
            summary = self.year_fetch_diagnostics or {}
        else:
            summary = self.direct_fetch_diagnostics or {}
        return is_data_complete(strategy, summary)

    def completeness_diag(self, citations_len=None):
        strategy = self.fetch_strategy or 'direct'
        if strategy == 'year':
            summary = self.year_fetch_diagnostics or {}
            target = summary.get('histogram_total')
            seen = summary.get('seen_total')
            label = 'histogram_total'
        else:
            summary = self.direct_fetch_diagnostics or {}
            target = summary.get('scholar_total')
            seen = summary.get('seen_total')
            label = 'scholar_total'
        if target is not None and seen is not None:
            cmp_sym = '≥' if (seen or 0) >= (target or 0) else '<'
            return f'  {strategy}: seen_total={seen} {cmp_sym} {label}={target}'
        return f'  {strategy}: diagnostics summary absent — will re-fetch'


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


def _normalize_year_summary_dict(yfd):
    """Return a normalized year-fetch diagnostics dict from *yfd*.

    *yfd* IS the summary — no 'summary' sub-key, no per-year entries.
    """
    if not isinstance(yfd, dict):
        return None
    if 'histogram_total' in yfd or 'scholar_total' in yfd:
        return _normalize_year_summary(yfd)
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
        if state.is_complete(pub.get('num_citations'), pub.get('year', 'N/A'),
                             year_based_threshold):
            return 'complete'
        return 'partial'
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
