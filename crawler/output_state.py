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

        Unknown keys are silently ignored.  The legacy 'complete' key is
        accepted as a fallback for 'complete_fetch_attempt'.
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
            year_fetch_diagnostics=d.get('year_fetch_diagnostics'),
            direct_fetch_diagnostics=d.get('direct_fetch_diagnostics'),
            fetched_at=d.get('fetched_at'),
        )

    def to_dict(self):
        """Serialize to the 9-key dict stored as _fetch_state in the output JSON."""
        return {
            'title': self.title,
            'pub_url': self.pub_url,
            'citedby_url': self.citedby_url,
            'fetch_strategy': self.fetch_strategy,
            'num_citations_on_scholar': self.num_citations_on_scholar,
            'complete_fetch_attempt': self.complete_fetch_attempt,
            'year_fetch_diagnostics': self.year_fetch_diagnostics,
            'direct_fetch_diagnostics': self.direct_fetch_diagnostics,
            'fetched_at': self.fetched_at,
        }

    def is_complete(self, current_scholar_total=None, pub_year='N/A',
                    year_based_threshold=50):
        """Return True when diagnostics prove the paper was fully fetched.

        Re-evaluates fetch_policy from *current_scholar_total* (which may
        have grown since the last run), then compares seen_total from the
        appropriate diagnostics summary against the target.

        When no diagnostics summary is available, falls back to comparing
        seen_total against the current Scholar total.
        """
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
        # No diagnostics summary — fall back to the last known seen count.
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

        # Legacy fallback: no diagnostics summary
        scholar = self.num_citations_on_scholar
        seen_val = citations_len or 0
        cmp_sym = '≥' if seen_val >= (scholar or 0) else '<'
        return (f'  {strategy}: seen={seen_val} {cmp_sym} '
                f'scholar_total={scholar} (no diagnostics)')


def _coerce_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Output file I/O
# ---------------------------------------------------------------------------

def load_output_fetch_state(output_path):
    """Return {paper_title: PaperFetchState} from the aggregate output JSON.

    Returns an empty dict when the file is missing or unreadable.
    """
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
    """Derive 'complete' | 'partial' | 'skip_zero' | 'missing' from an output
    _fetch_state dict, reusing the same pure logic used for cache files.

    *state* may be a PaperFetchState (new path) or a raw dict (legacy path).
    """
    if pub.get('num_citations') == 0:
        return 'skip_zero'
    if isinstance(state, PaperFetchState):
        if state.is_complete(pub.get('num_citations'), pub.get('year', 'N/A'),
                             year_based_threshold):
            return 'complete'
        return 'partial'
    # Legacy dict path
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
