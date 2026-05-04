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

    A year is a candidate when:
    - it has an in-progress partial resume (partial_year_start), OR
    - the histogram count for that year differs from the cached count.

    Years not present in the histogram are skipped (treated as complete).
    probe_complete is no longer used as a gate — any histogram data is treated
    as authoritative.

    Returns a list of candidate years (possibly empty), or None if there is
    no histogram data at all (caller should fetch all years).
    """
    cached_year_counts = normalize_year_count_map(cached_year_counts)
    probed_year_counts = normalize_year_count_map(probed_year_counts)
    if not probed_year_counts:
        return None
    partial_years = {int(year) for year in (partial_year_start or {}).keys()}
    candidate_years = set(partial_years)
    candidate_years.update(
        year for year in probed_year_counts
        if cached_year_counts.get(year, 0) != probed_year_counts[year]
    )
    return [year for year in year_range if year in candidate_years]


# ---------------------------------------------------------------------------
# Citation count summary
# ---------------------------------------------------------------------------

def build_citation_count_summary(citations, scholar_total=None, probed_year_counts=None,
                                 probe_complete=False, dedup_count=0,
                                 year_fetch_diagnostics=None):
    """Build a summary dict comparing cached citations against Scholar totals.

    When year_fetch_diagnostics is present all per-year totals are derived
    from it so that the summary is always consistent with the per-year data.
    """
    cached_total = len(citations or [])
    cached_year_counts = year_count_map(citations or [])
    normalized_probed_year_counts = normalize_year_count_map(probed_year_counts)
    histogram_total = sum(normalized_probed_year_counts.values())
    unyeared_count = None
    if scholar_total is not None:
        unyeared_count = max(0, scholar_total - histogram_total)

    year_diags = normalize_year_fetch_diagnostics(year_fetch_diagnostics)
    if year_diags:
        # Derive all totals from per-year diagnostics so they are always
        # consistent with the per-year data shown in Year fetch comparisons.
        diag_scholar = sum(d.get('scholar_total', 0) for d in year_diags.values())
        diag_cached = sum(d.get('cached_total', 0) for d in year_diags.values())
        diag_seen = sum(d.get('seen_total', 0) for d in year_diags.values())
        diag_dedup = sum(d.get('dedup_count', 0) for d in year_diags.values())
        histogram_total = diag_scholar
        if scholar_total is not None:
            unyeared_count = max(0, scholar_total - histogram_total)
        cached_year_total = diag_cached
        cached_unyeared_count = max(0, cached_total - diag_cached)
        seen_total = diag_seen
        cached_dedup = diag_dedup
    else:
        cached_year_total = sum(cached_year_counts.values())
        cached_unyeared_count = max(0, cached_total - cached_year_total)
        seen_total = cached_total + int(dedup_count or 0)
        cached_dedup = int(dedup_count or 0)

    return {
        'scholar_total': scholar_total,
        'histogram_total': histogram_total,
        'cached_total': cached_total,
        'cached_year_total': cached_year_total,
        'seen_total': seen_total,
        'cached_unyeared_count': cached_unyeared_count,
        'dedup_count': cached_dedup,
        'scholar_unyeared_count': unyeared_count,
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
        year_fetch_diagnostics=year_fetch_diagnostics,
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

def format_year_fetch_diagnostics_summary(year_fetch_diagnostics):
    """Return a human-readable summary of year_fetch_diagnostics, one year per line."""
    diagnostics = normalize_year_fetch_diagnostics(year_fetch_diagnostics)
    if not diagnostics:
        return 'none'
    items = sorted(diagnostics.items())
    lines = []
    for year, diagnostic in items:
        lines.append(
            f"  {year}: scholar={diagnostic.get('scholar_total')},"
            f"seen={diagnostic.get('seen_total')},"
            f"cached={diagnostic.get('cached_total')},"
            f"dedup={diagnostic.get('dedup_count')},"
            f"term={diagnostic.get('termination_reason')}"
        )
    return f"{len(items)} years\n" + "\n".join(lines)
