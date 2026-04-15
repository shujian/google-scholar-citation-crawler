"""
citation/strategy.py — Fetch-policy, refresh strategy, and diagnostics formatting.

Functions here are pure or near-pure: they accept explicit parameters and
return data structures without side effects.  The only external dependency is
citation.cache for the lower-level year-count / diagnostics helpers.
"""

from datetime import datetime

from crawler.citation_cache import (
    normalize_year_count_map,
    normalize_year_fetch_diagnostics,
    year_count_map,
    year_fetch_diagnostic_matches_total,
    probed_year_counts_satisfied,
)


# Threshold imported by callers from scholar_common; passed in explicitly here
# to keep this module free of global state.

# ---------------------------------------------------------------------------
# Publication-year normalisation
# ---------------------------------------------------------------------------

def normalize_pub_year(pub_year, current_year):
    """Return int year if valid and not in the future, else None."""
    if pub_year in (None, '', 'N/A', 'NA', '?'):
        return None
    try:
        year = int(str(pub_year).strip())
    except (TypeError, ValueError):
        return None
    if year > int(current_year):
        return None
    return year


# ---------------------------------------------------------------------------
# Fetch policy
# ---------------------------------------------------------------------------

def resolve_citation_fetch_policy(num_citations, pub_year, year_based_threshold, current_year=None):
    """
    Decide whether to use 'direct' or 'year' fetch mode for a paper.

    Returns a dict with keys: mode, covered_years, avg_citations_per_year,
    pub_year, reason.
    """
    current_year = current_year or datetime.now().year
    total = int(num_citations or 0)
    if total < year_based_threshold:
        return {
            'mode': 'direct',
            'covered_years': None,
            'avg_citations_per_year': None,
            'pub_year': normalize_pub_year(pub_year, current_year),
            'reason': 'below_year_threshold',
        }

    normalized_pub_year = normalize_pub_year(pub_year, current_year)
    if normalized_pub_year is None:
        return {
            'mode': 'year',
            'covered_years': None,
            'avg_citations_per_year': None,
            'pub_year': None,
            'reason': 'invalid_pub_year',
        }

    covered_years = max(1, int(current_year) - normalized_pub_year + 1)
    avg_citations_per_year = total / covered_years
    return {
        'mode': 'direct' if avg_citations_per_year <= 20 else 'year',
        'covered_years': covered_years,
        'avg_citations_per_year': avg_citations_per_year,
        'pub_year': normalized_pub_year,
        'reason': 'low_average_per_year' if avg_citations_per_year <= 20 else 'high_average_per_year',
    }


# ---------------------------------------------------------------------------
# Selective refresh
# ---------------------------------------------------------------------------

def selective_refresh_candidate_years(
    cached_year_counts,
    probed_year_counts,
    year_range,
    partial_year_start=None,
    probe_complete=False,
    year_fetch_diagnostics=None,
):
    """
    Return the subset of year_range that needs to be re-fetched.

    Returns None if no refresh is needed (empty candidate set and not
    probe_complete), or a list of years (possibly empty) if probe_complete.
    """
    cached_year_counts = normalize_year_count_map(cached_year_counts)
    probed_year_counts = normalize_year_count_map(probed_year_counts)
    year_fetch_diagnostics = normalize_year_fetch_diagnostics(year_fetch_diagnostics)
    partial_years = {int(year) for year in (partial_year_start or {}).keys()}
    candidate_years = set(partial_years)

    def should_refresh_year(year, live_total):
        existing_diag = year_fetch_diagnostics.get(year)
        if year in partial_years:
            return True
        if existing_diag and existing_diag.get('underfetched'):
            return True
        try:
            historical_scholar_total = int(existing_diag.get('scholar_total')) if existing_diag else None
        except (TypeError, ValueError):
            historical_scholar_total = None
        if historical_scholar_total is not None and historical_scholar_total != live_total:
            return True
        try:
            seen_total = int(existing_diag.get('seen_total', 0) or 0) if existing_diag else None
        except (TypeError, ValueError):
            seen_total = None
        if seen_total is not None and seen_total < live_total:
            return True
        if cached_year_counts.get(year, 0) != live_total and not year_fetch_diagnostic_matches_total(
            existing_diag,
            live_total,
            cached_year_counts.get(year, 0),
        ):
            return True
        return False

    if probe_complete:
        candidate_years.update(
            year for year in year_range
            if should_refresh_year(year, probed_year_counts.get(year, 0))
        )
        return [year for year in year_range if year in candidate_years]

    candidate_years.update(
        year for year in year_range
        if year in probed_year_counts and should_refresh_year(year, probed_year_counts[year])
    )
    if not candidate_years:
        return None
    return [year for year in year_range if year in candidate_years]


# ---------------------------------------------------------------------------
# Citation count summary
# ---------------------------------------------------------------------------

def build_citation_count_summary(citations, scholar_total=None, probed_year_counts=None,
                                 probe_complete=False, dedup_count=0):
    """Build a summary dict comparing cached citations against Scholar totals."""
    cached_total = len(citations or [])
    cached_year_counts = year_count_map(citations or [])
    cached_year_total = sum(cached_year_counts.values())
    cached_unyeared_count = max(0, cached_total - cached_year_total)
    normalized_probed_year_counts = normalize_year_count_map(probed_year_counts)
    histogram_total = sum(normalized_probed_year_counts.values())
    unyeared_count = None
    if scholar_total is not None:
        unyeared_count = max(0, scholar_total - histogram_total)
    return {
        'scholar_total': scholar_total,
        'histogram_total': histogram_total,
        'cached_total': cached_total,
        'cached_year_total': cached_year_total,
        'cached_unyeared_count': cached_unyeared_count,
        'dedup_count': int(dedup_count or 0),
        'probe_complete': bool(probe_complete),
        'unyeared_count': unyeared_count,
        'cached_year_counts': cached_year_counts,
        'probed_year_counts': normalized_probed_year_counts,
    }


# ---------------------------------------------------------------------------
# Reconciliation status
# ---------------------------------------------------------------------------

def refresh_reconciliation_status(
    citations,
    num_citations,
    dedup_count=0,
    probed_year_counts=None,
    probe_complete=False,
    year_fetch_diagnostics=None,
):
    """
    Return a status dict indicating whether the current cached citations are
    consistent with the Scholar total and probed histogram.
    """
    count_summary = build_citation_count_summary(
        citations,
        scholar_total=num_citations,
        probed_year_counts=probed_year_counts,
        probe_complete=probe_complete,
        dedup_count=dedup_count,
    )
    normalized_year_fetch_diagnostics = normalize_year_fetch_diagnostics(year_fetch_diagnostics)
    status = {
        'ok': False,
        'reason': 'histogram_incomplete',
        **count_summary,
        'year_fetch_diagnostics': normalized_year_fetch_diagnostics,
    }

    if probe_complete:
        if probed_year_counts_satisfied(
            count_summary['cached_year_counts'],
            count_summary['probed_year_counts'],
            normalized_year_fetch_diagnostics,
        ):
            status.update({'ok': True, 'reason': 'matched_complete_histogram'})
        else:
            status.update({'reason': 'year_count_mismatch'})
        return status

    if count_summary['probed_year_counts']:
        if probed_year_counts_satisfied(
            count_summary['cached_year_counts'],
            count_summary['probed_year_counts'],
            normalized_year_fetch_diagnostics,
        ):
            status.update({'ok': True, 'reason': 'matched_incomplete_histogram'})
            return status
        return status

    if count_summary['cached_total'] == count_summary['scholar_total']:
        status.update({'ok': True, 'reason': 'count_matched_without_histogram'})
        return status

    return status


# ---------------------------------------------------------------------------
# Diagnostics formatting
# ---------------------------------------------------------------------------

def format_year_fetch_diagnostics_summary(year_fetch_diagnostics, limit=8):
    """Return a compact human-readable summary of year_fetch_diagnostics."""
    diagnostics = normalize_year_fetch_diagnostics(year_fetch_diagnostics)
    if not diagnostics:
        return 'none'
    items = sorted(diagnostics.items())
    display_items = items
    if len(items) > limit:
        head = items[: max(1, limit // 2)]
        tail = items[-max(1, limit - len(head)):]
        display_items = head + [('...', None)] + tail
    parts = []
    for year, diagnostic in display_items:
        if year == '...':
            parts.append('...')
            continue
        parts.append(
            f"{year}:scholar={diagnostic.get('scholar_total')},"
            f"seen={diagnostic.get('seen_total')},"
            f"cached={diagnostic.get('cached_total')},"
            f"dedup={diagnostic.get('dedup_count')},"
            f"term={diagnostic.get('termination_reason')}"
        )
    return f"{len(items)} years [{'; '.join(parts)}]"
