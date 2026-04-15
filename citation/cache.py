"""
citation/cache.py — Pure year-count and fetch-diagnostics helpers.

All functions here are stateless (no class instance needed).  They are used
by both PaperCitationFetcher and citation/strategy.py, so they live at this
lower layer to avoid circular imports.
"""


# ---------------------------------------------------------------------------
# Year-count map helpers
# ---------------------------------------------------------------------------

def year_count_map(citations):
    """Return {year_int: count} for citations that carry a usable year."""
    counts = {}
    for c in citations:
        y = c.get('year', 'N/A')
        if y and y != 'N/A' and y != 'NA':
            try:
                year = int(y)
                counts[year] = counts.get(year, 0) + 1
            except ValueError:
                pass
    return counts


def normalize_year_count_map(year_counts):
    """Coerce keys and values to int, drop bad entries."""
    normalized = {}
    for year, count in (year_counts or {}).items():
        try:
            y = int(year)
            c = int(count)
        except (TypeError, ValueError):
            continue
        if c < 0:
            continue
        normalized[y] = c
    return normalized


def dump_year_count_map(year_counts):
    """Return a JSON-friendly {str_year: count} dict sorted by year."""
    return {str(year): count for year, count in sorted(year_counts.items())}


# ---------------------------------------------------------------------------
# Year fetch diagnostics helpers
# ---------------------------------------------------------------------------

def build_year_fetch_diagnostics(year, scholar_total, cached_total, dedup_count, termination_reason):
    """Build a single-year diagnostics dict from raw fetch results."""
    try:
        year = int(year)
        scholar_total = int(scholar_total)
    except (TypeError, ValueError):
        return None
    try:
        cached_total = int(cached_total or 0)
    except (TypeError, ValueError):
        cached_total = 0
    try:
        dedup_count = int(dedup_count or 0)
    except (TypeError, ValueError):
        dedup_count = 0
    cached_total = max(0, cached_total)
    dedup_count = max(0, dedup_count)
    seen_total = cached_total + dedup_count
    gap = max(0, scholar_total - seen_total)
    return {
        'mode': 'year',
        'year': year,
        'scholar_total': scholar_total,
        'cached_total': cached_total,
        'seen_total': seen_total,
        'dedup_count': dedup_count,
        'underfetched': seen_total < scholar_total,
        'underfetch_gap': gap,
        'termination_reason': termination_reason or 'iterator_exhausted',
    }


def normalize_year_fetch_diagnostics(year_fetch_diagnostics):
    """Normalize a raw year_fetch_diagnostics dict to {year_int: diag_dict}."""
    normalized = {}
    for raw_year, raw_diag in (year_fetch_diagnostics or {}).items():
        if not isinstance(raw_diag, dict):
            continue
        diagnostic = build_year_fetch_diagnostics(
            raw_diag.get('year', raw_year),
            raw_diag.get('scholar_total'),
            raw_diag.get('cached_total'),
            raw_diag.get('dedup_count', 0),
            raw_diag.get('termination_reason'),
        )
        if not diagnostic:
            continue
        raw_seen_total = raw_diag.get('seen_total')
        try:
            raw_seen_total = int(raw_seen_total)
        except (TypeError, ValueError):
            raw_seen_total = None
        if raw_seen_total is not None and raw_seen_total >= diagnostic['cached_total']:
            diagnostic['seen_total'] = raw_seen_total
            diagnostic['dedup_count'] = raw_seen_total - diagnostic['cached_total']
            diagnostic['underfetch_gap'] = max(0, diagnostic['scholar_total'] - raw_seen_total)
            diagnostic['underfetched'] = raw_seen_total < diagnostic['scholar_total']
        normalized[diagnostic['year']] = diagnostic
    return normalized


def dump_year_fetch_diagnostics(year_fetch_diagnostics):
    """Return a JSON-friendly {str_year: diag_dict} sorted by year."""
    normalized = normalize_year_fetch_diagnostics(year_fetch_diagnostics)
    return {str(year): diagnostic for year, diagnostic in sorted(normalized.items())}


def year_fetch_diagnostic_matches_total(diagnostic, scholar_total, cached_total=None):
    """Return True iff a single-year diagnostic proves the year is fully fetched."""
    if not isinstance(diagnostic, dict):
        return False
    try:
        scholar_total = int(scholar_total)
    except (TypeError, ValueError):
        return False
    try:
        diagnostic_total = int(diagnostic.get('scholar_total'))
        diagnostic_cached_total = int(diagnostic.get('cached_total', 0) or 0)
        seen_total = int(diagnostic.get('seen_total', 0) or 0)
    except (TypeError, ValueError):
        return False
    if cached_total is not None:
        try:
            cached_total = int(cached_total or 0)
        except (TypeError, ValueError):
            return False
        if diagnostic_cached_total != cached_total:
            return False
    return (
        diagnostic_total == scholar_total
        and diagnostic_cached_total <= scholar_total
        and seen_total >= scholar_total
    )


def probed_year_counts_satisfied(cached_year_counts, probed_year_counts, year_fetch_diagnostics=None):
    """Return True iff cached counts + diagnostics fully cover the probed histogram."""
    cached_year_counts = normalize_year_count_map(cached_year_counts)
    probed_year_counts = normalize_year_count_map(probed_year_counts)
    year_fetch_diagnostics = normalize_year_fetch_diagnostics(year_fetch_diagnostics)
    if not probed_year_counts:
        return False
    for year, live_total in probed_year_counts.items():
        if cached_year_counts.get(year, 0) == live_total:
            continue
        if year_fetch_diagnostic_matches_total(
            year_fetch_diagnostics.get(year),
            live_total,
            cached_year_counts.get(year, 0),
        ):
            continue
        return False
    return True


# ---------------------------------------------------------------------------
# Probe metadata rehydration
# ---------------------------------------------------------------------------

def rehydrate_probe_metadata(cached, current_scholar_total):
    """
    Load probed_year_counts and probe_complete from a cached dict.
    Returns (normalized_counts_or_None, probe_complete_bool).
    """
    normalized_counts = normalize_year_count_map(
        (cached or {}).get('probed_year_counts')
    )
    probe_complete = False
    if normalized_counts:
        cached_probe_complete = (cached or {}).get('probe_complete') is True
        histogram_total = (cached or {}).get('probed_year_total')
        try:
            histogram_total = int(histogram_total)
        except (TypeError, ValueError):
            histogram_total = sum(normalized_counts.values())
        if cached_probe_complete and current_scholar_total is not None and histogram_total == current_scholar_total:
            probe_complete = True
    return normalized_counts or None, probe_complete


def rehydrate_year_fetch_diagnostics(cached):
    """Load and normalize year_fetch_diagnostics from a cached dict."""
    return normalize_year_fetch_diagnostics(
        (cached or {}).get('year_fetch_diagnostics')
    ) or None
