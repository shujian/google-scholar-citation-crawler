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

from crawler.citation_io import (
    derive_citation_cache_state,
    resolve_citation_status_from_state,
)


# Fields that belong in _fetch_state (everything from the cache dict except
# the citations array, which already lives at the top level of the output entry).
_FETCH_STATE_KEYS = frozenset([
    'title',
    'pub_url',
    'citedby_url',
    'num_citations_on_scholar',
    'num_citations_cached',
    'num_citations_seen',
    'dedup_count',
    'complete',
    'complete_fetch_attempt',
    'completed_years',
    'completed_years_in_current_run',
    'probe_complete',
    'probed_year_counts',
    'probed_year_total',
    'cached_year_counts',
    'year_fetch_diagnostics',
    'cached_unyeared_count',
    'citation_count_summary',
    'direct_fetch_diagnostics',
    'direct_resume_state',
    'fetched_at',
])


def load_output_fetch_state(output_path):
    """
    Load the aggregate output JSON and return a mapping
    {paper_title: _fetch_state_dict}.

    Returns an empty dict when the file is missing or unreadable.
    """
    if not os.path.exists(output_path):
        return {}
    try:
        with open(output_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError, TypeError):
        return {}
    result = {}
    for paper in data.get('papers', []):
        state = paper.get('_fetch_state')
        if isinstance(state, dict) and state.get('title'):
            result[state['title']] = state
    return result


def resolve_citation_status_from_output(pub, state, year_based_threshold):
    """
    Derive 'complete' | 'partial' | 'skip_zero' | 'missing' from an output
    _fetch_state dict, reusing the same pure logic used for cache files.

    *pub* is the publication dict from the profile (must contain
    'num_citations' and optionally 'year').
    *state* is the _fetch_state dict extracted from the output file.
    """
    if pub.get('num_citations') == 0:
        return 'skip_zero'
    cache_state = derive_citation_cache_state(pub, state, year_based_threshold)
    return resolve_citation_status_from_state(cache_state)


def extract_fetch_state(cached):
    """
    Given a per-paper cache dict, return the subset of fields that should be
    persisted inside the aggregate output file as _fetch_state.

    The 'citations' array is intentionally excluded because it already lives at
    the top level of the output entry.
    """
    if not cached:
        return {}
    return {k: v for k, v in cached.items() if k in _FETCH_STATE_KEYS}
