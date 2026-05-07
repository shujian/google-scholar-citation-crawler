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

# Fields that belong in _fetch_state (everything from the cache dict except
# the citations array, which already lives at the top level of the output entry).
_FETCH_STATE_KEYS = frozenset([
    'title',
    'pub_url',
    'citedby_url',
    'fetch_strategy',
    'num_citations_on_scholar',
    'complete_fetch_attempt',
    'year_fetch_diagnostics',
    'direct_fetch_diagnostics',
    'fetched_at',
])

# Allowed keys in direct_fetch_diagnostics.summary.
_DIRECT_SUMMARY_KEYS = frozenset([
    'scholar_total', 'cached_total', 'seen_total', 'dedup_count', 'termination_reason',
])

# Allowed keys in year_fetch_diagnostics.summary.
_YEAR_SUMMARY_KEYS = frozenset([
    'scholar_total', 'histogram_total',
    'cached_total', 'cached_year_total',
    'seen_total', 'cached_unyeared_count',
    'dedup_count', 'scholar_unyeared_count',
])

# Allowed keys in each per-year entry of year_fetch_diagnostics.
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
    fetched_at: Optional[str] = None

    @classmethod
    def from_dict(cls, d):
        """Construct from a raw dict (output JSON _fetch_state or cache file).

        Only known fields are extracted — unknown keys are discarded.
        Diagnostics objects are normalised on entry so that even legacy
        or cache-file data with extra keys is cleaned up.
        """
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
            year_fetch_diagnostics=_normalize_year_diagnostics(
                d.get('year_fetch_diagnostics')
            ),
            direct_fetch_diagnostics=_normalize_direct_diagnostics(
                d.get('direct_fetch_diagnostics')
            ),
            fetched_at=d.get('fetched_at'),
        )

    def to_dict(self):
        """Serialize to the 9-key dict stored as _fetch_state in the output JSON.

        Every field is explicitly constructed — no pass-through of raw dicts.
        This guarantees the output schema even when internal state was modified
        by cache-file merges or other runtime operations.
        """
        return {
            'title': self.title,
            'pub_url': self.pub_url,
            'citedby_url': self.citedby_url,
            'fetch_strategy': self.fetch_strategy,
            'num_citations_on_scholar': self.num_citations_on_scholar,
            'complete_fetch_attempt': self.complete_fetch_attempt,
            'year_fetch_diagnostics': _normalize_year_diagnostics(
                self.year_fetch_diagnostics
            ),
            'direct_fetch_diagnostics': _normalize_direct_diagnostics(
                self.direct_fetch_diagnostics
            ),
            'fetched_at': self.fetched_at,
        }

    def is_complete(self, current_scholar_total=None, pub_year='N/A',
                    year_based_threshold=50):
        """Return True when diagnostics prove the paper was fully fetched."""
        from crawler.citation_strategy import resolve_citation_fetch_policy

        scholar = int(current_scholar_total or 0)
        fetch_policy = resolve_citation_fetch_policy(scholar, pub_year, year_based_threshold)

        if fetch_policy['mode'] == 'year':
            summary = (self.year_fetch_diagnostics or {}).get('summary') or {}
            target = summary.get('histogram_total')
            seen = summary.get('seen_total')
        else:
            summary = (self.direct_fetch_diagnostics or {}).get('summary') or {}
            target = summary.get('scholar_total')
            seen = summary.get('seen_total')

        if target is not None and seen is not None:
            return (seen or 0) >= target
        seen = seen or self.num_citations_on_scholar or 0
        return seen >= scholar

    def completeness_diag(self, citations_len=None):
        """Return a one-line completeness diagnostic string."""
        strategy = self.fetch_strategy or 'direct'

        if strategy == 'year':
            summary = (self.year_fetch_diagnostics or {}).get('summary') or {}
            target = summary.get('histogram_total')
            seen = summary.get('seen_total')
            label = 'histogram_total'
        else:
            summary = (self.direct_fetch_diagnostics or {}).get('summary') or {}
            target = summary.get('scholar_total')
            seen = summary.get('seen_total')
            label = 'scholar_total'

        if target is not None and seen is not None:
            cmp_sym = '≥' if (seen or 0) >= (target or 0) else '<'
            return f'  {strategy}: seen_total={seen} {cmp_sym} {label}={target}'

        scholar = self.num_citations_on_scholar
        seen_val = citations_len or 0
        cmp_sym = '≥' if seen_val >= (scholar or 0) else '<'
        return (f'  {strategy}: seen={seen_val} {cmp_sym} '
                f'scholar_total={scholar} (no diagnostics)')


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _coerce_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_direct_diagnostics(dfd):
    """Return direct_fetch_diagnostics with exactly the 5 allowed summary fields.

    Discards any year-mode fields that leaked in from buggy merges.
    Returns None when the input is missing or invalid.
    """
    if not isinstance(dfd, dict):
        return None
    raw = dfd.get('summary')
    if not isinstance(raw, dict):
        return None
    dd = raw.get('dedup_count', 0) or 0
    ct = raw.get('cached_total', 0) or 0
    return {
        'summary': {
            'scholar_total': _coerce_int(raw.get('scholar_total')),
            'cached_total': ct,
            'seen_total': _coerce_int(raw.get('seen_total', ct + dd)) or (ct + dd),
            'dedup_count': dd,
            'termination_reason': raw.get('termination_reason', 'iterator_exhausted'),
        },
    }


def _normalize_year_diagnostics(yfd):
    """Return year_fetch_diagnostics with per-year entries sorted by year
    and restricted to the canonical per-year keys.  The summary key is also
    filtered to allowed fields.

    Returns None when the input is missing or invalid.
    """
    if not isinstance(yfd, dict):
        return None
    # Normalize per-year entries (keep only _PER_YEAR_KEYS), sorted by year.
    per_year = {}
    for key, diag in yfd.items():
        if not isinstance(diag, dict) or 'year' not in diag:
            continue
        try:
            year = int(diag['year'])
        except (TypeError, ValueError):
            continue
        cleaned = {
            'year': year,
            'histogram_count': diag.get('histogram_count', diag.get('scholar_total', 0)) or 0,
            'cached_total': diag.get('cached_total', 0) or 0,
            'seen_total': diag.get('seen_total', (diag.get('cached_total', 0) or 0) + (diag.get('dedup_count', 0) or 0)),
            'dedup_count': diag.get('dedup_count', 0) or 0,
            'termination_reason': diag.get('termination_reason', 'iterator_exhausted'),
        }
        per_year[str(year)] = cleaned

    # Sort by year ascending
    sorted_entries = {str(y): per_year[str(y)] for y in sorted(per_year.keys())}

    # Normalize summary key with fixed field order.
    raw_summary = yfd.get('summary')
    if isinstance(raw_summary, dict):
        summary = _normalize_year_summary(raw_summary)
    else:
        summary = _build_year_summary_from_entries(sorted_entries)

    result = dict(sorted_entries)
    result['summary'] = summary
    return result


def _normalize_year_summary(raw):
    """Return a year_fetch_diagnostics.summary with fixed field order."""
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


def _build_year_summary_from_entries(entries):
    """Derive a year_fetch_diagnostics.summary from per-year entries."""
    hist_total = sum(d.get('histogram_count', 0) for d in entries.values())
    cached_total = sum(d.get('cached_total', 0) for d in entries.values())
    seen_total = sum(d.get('seen_total', 0) for d in entries.values())
    dedup_total = sum(d.get('dedup_count', 0) for d in entries.values())
    return {
        'scholar_total': None,
        'histogram_total': hist_total,
        'cached_total': cached_total,
        'cached_year_total': cached_total,
        'seen_total': seen_total,
        'cached_unyeared_count': 0,
        'dedup_count': dedup_total,
        'scholar_unyeared_count': None,
    }


# ---------------------------------------------------------------------------
# Output file I/O
# ---------------------------------------------------------------------------

def load_output_fetch_state(output_path):
    """Return {paper_title: PaperFetchState} from the aggregate output JSON."""
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
    """Derive 'complete' | 'partial' | 'skip_zero' | 'missing' from output state.

    *state* may be a PaperFetchState (new path) or a raw dict (legacy path).
    """
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
    """Given a per-paper cache dict, return a PaperFetchState.

    Missing numeric fields are derived from the citations array when possible.
    """
    if not cached:
        return PaperFetchState()
    state = PaperFetchState.from_dict(cached)
    citations = cached.get('citations', []) or []
    if state.num_citations_on_scholar is None:
        yfd = (cached.get('year_fetch_diagnostics') or {})
        year_summary = yfd.get('summary') or {}
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
