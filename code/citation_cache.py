"""
citation/cache.py — Pure year-count and fetch-diagnostics helpers.

All functions here are stateless (no class instance needed).  They are used
by both PaperCitationFetcher and citation/strategy.py, so they live at this
lower layer to avoid circular imports.
"""


# ---------------------------------------------------------------------------
# Year-count map helpers
# ---------------------------------------------------------------------------

# Every spelling of "no usable year" seen from Scholar / old caches.  Kept in
# one place so records, pub metadata and the Excel writers agree.
UNKNOWN_YEAR_VALUES = ('', 'n/a', 'na', '?', 'none')
UNKNOWN_YEAR_SENTINEL = 'N/A'


def normalize_citation_year(value, fallback_year=None):
    """Return a usable year string, or the 'N/A' sentinel.

    *fallback_year* is used when the citation carries no year of its own (the
    year-mode fetch knows which year bucket it is filling).
    """
    text = '' if value is None else str(value).strip()
    if text.lower() in UNKNOWN_YEAR_VALUES:
        if fallback_year is not None:
            return str(fallback_year).strip()
        return UNKNOWN_YEAR_SENTINEL
    return text


def is_unknown_year(value):
    """True when *value* does not carry a usable year."""
    text = '' if value is None else str(value).strip()
    return text.lower() in UNKNOWN_YEAR_VALUES or text == UNKNOWN_YEAR_SENTINEL


def year_count_map(citations):
    """Return {year_int: count} for citations that carry a usable year."""
    counts = {}
    for c in citations:
        y = c.get('year', UNKNOWN_YEAR_SENTINEL)
        if is_unknown_year(y):
            continue
        try:
            year = int(y)
        except (TypeError, ValueError):
            continue
        counts[year] = counts.get(year, 0) + 1
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

def build_year_fetch_diagnostics(year, histogram_count, cached_total, dedup_count, termination_reason,
                                short_attempts=0):
    """Build a single-year diagnostics dict from raw fetch results."""
    try:
        year = int(year)
        histogram_count = int(histogram_count)
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
    try:
        short_attempts = int(short_attempts or 0)
    except (TypeError, ValueError):
        short_attempts = 0
    return {
        'year': year,
        'histogram_count': histogram_count,
        'cached_total': cached_total,
        'seen_total': seen_total,
        'dedup_count': dedup_count,
        'termination_reason': termination_reason or 'iterator_exhausted',
        # 连续多少次"见到的比 Scholar 声称的少"（2026-10-19）：连续 N 次 → 下一次从第 1 页重扫。
        'short_attempts': max(0, short_attempts),
    }


def normalize_year_fetch_diagnostics(year_fetch_diagnostics):
    """Normalize a raw year_fetch_diagnostics dict to {year_int: diag_dict}."""
    normalized = {}
    for raw_year, raw_diag in (year_fetch_diagnostics or {}).items():
        if not isinstance(raw_diag, dict):
            continue
        diagnostic = build_year_fetch_diagnostics(
            raw_diag.get('year', raw_year),
            raw_diag.get('histogram_count', raw_diag.get('scholar_total')),
            raw_diag.get('cached_total'),
            raw_diag.get('dedup_count', 0),
            raw_diag.get('termination_reason'),
            raw_diag.get('short_attempts', 0),
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
        normalized[diagnostic['year']] = diagnostic
    return normalized


def dump_year_fetch_diagnostics(year_fetch_diagnostics):
    """Return a JSON-friendly {str_year: diag_dict} sorted by year."""
    normalized = normalize_year_fetch_diagnostics(year_fetch_diagnostics)
    return {str(year): diagnostic for year, diagnostic in sorted(normalized.items())}


def year_fetch_diagnostic_matches_total(diagnostic, histogram_count, cached_total=None):
    """Return True iff a single-year diagnostic proves the year is fully fetched."""
    if not isinstance(diagnostic, dict):
        return False
    try:
        histogram_count = int(histogram_count)
    except (TypeError, ValueError):
        return False
    try:
        diagnostic_total = int(diagnostic.get('histogram_count', diagnostic.get('scholar_total')))
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
        diagnostic_total == histogram_count
        and diagnostic_cached_total <= histogram_count
        and seen_total >= histogram_count
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
            year_fetch_diagnostics.get(year) if year_fetch_diagnostics else None,
            live_total,
            cached_year_counts.get(year, 0),
        ):
            continue
        return False
    return True


# ---------------------------------------------------------------------------
# Probe metadata rehydration
def is_data_complete(strategy, summary):
    """Return whether *summary* indicates complete citation data.

    For year mode, compares seen_total against histogram_total.
    For direct mode, compares seen_total against scholar_total.
    Returns False when the summary is absent or key fields are missing.
    """
    if not summary:
        return False
    target = summary.get('histogram_total' if strategy == 'year' else 'scholar_total')
    seen = summary.get('seen_total')
    if target is not None and seen is not None:
        return (seen or 0) >= target
    return False


# ---------------------------------------------------------------------------
# Per-year completeness (year mode)
#
# Year mode must be judged per year.  The aggregate comparison in
# is_data_complete() only looks at sum(seen) vs sum(histogram): years whose
# cached bucket exceeds the probe (which happens whenever Scholar's per-year
# histogram and the cached buckets disagree) inflate the aggregate and hide a
# different year whose own bucket is short.  A paper judged complete that way
# is never re-probed, so the short year's citations are never fetched.
# ---------------------------------------------------------------------------

def iter_year_records(year_records):
    """Yield the per-year record dicts from a list or {year: record} mapping."""
    if isinstance(year_records, dict):
        values = list(year_records.values())
    elif isinstance(year_records, (list, tuple)):
        values = list(year_records)
    else:
        return
    for record in values:
        if isinstance(record, dict):
            yield record


def year_record_gap(record):
    """Return max(0, histogram_count - seen_total) for one per-year record."""
    try:
        histogram_count = int(record.get('histogram_count', record.get('scholar_total', 0)) or 0)
        seen_total = int(record.get('seen_total', 0) or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, histogram_count - seen_total)


def year_records_gap(year_records):
    """Return (needed, seen_total, histogram_total) summed over per-year records.

    `needed` is the per-year shortfall sum, which the aggregate totals can hide.
    Returns None when no usable per-year records are present.
    """
    needed = seen_sum = histogram_sum = 0
    count = 0
    for record in iter_year_records(year_records):
        try:
            histogram_count = int(record.get('histogram_count', record.get('scholar_total', 0)) or 0)
            seen_total = int(record.get('seen_total', 0) or 0)
        except (TypeError, ValueError):
            continue
        count += 1
        seen_sum += seen_total
        histogram_sum += histogram_count
        needed += max(0, histogram_count - seen_total)
    if not count:
        return None
    return needed, seen_sum, histogram_sum


def year_records_shortfall_years(year_records):
    """Return a list of (year, seen_total, histogram_count) for short years."""
    short = []
    for record in iter_year_records(year_records):
        if year_record_gap(record) > 0:
            short.append((
                record.get('year'),
                int(record.get('seen_total', 0) or 0),
                int(record.get('histogram_count', record.get('scholar_total', 0)) or 0),
            ))
    return short


def year_records_data_complete(year_records, summary=None):
    """Return True iff every per-year record covers its probed histogram count.

    Falls back to the aggregate comparison when no per-year records exist, and
    requires the aggregate to hold as well so a summary listing years that have
    no record still counts as incomplete.
    """
    if not is_data_complete('year', summary):
        return False
    gap = year_records_gap(year_records)
    if gap is None:
        return True
    return gap[0] == 0


# ---------------------------------------------------------------------------
# Fetch-outcome signature (stall detection)
#
# A paper can end every run with the same short result — Scholar's own page
# lists fewer citations than its "Cited by" count claims, so re-fetching
# cannot help.  The signature makes such outcomes comparable across runs: two
# consecutive **completed** traversals that produce the same string obtained
# the same rows.
# ---------------------------------------------------------------------------

def fetch_outcome_signature(strategy, summary, year_records=None):
    """Return a short signature of *what a traversal obtained*, or ''.

    The signature counts the rows the fetch actually saw, and deliberately
    leaves out Scholar's own expectation (``histogram_total`` /
    ``scholar_total``) as well as the short-year list.  Those move between runs
    even when the listing does not (2026-10-15: one paper's 2026 bucket
    alternated 190/200, 193/200, 194/201, 192/201), so every run looked like a
    new outcome and a paper that cannot converge never accumulated a stall
    count — it was re-walked in full on every run.  Completeness is still
    judged against those targets (`year_records_data_complete` /
    `is_data_complete`); only the stall comparison ignores them.
    """
    if strategy == 'year':
        gap = year_records_gap(year_records)
        if gap is not None:
            return f'year:{gap[1]}'
        summary = summary or {}
        seen = summary.get('seen_total')
    else:
        summary = summary or {}
        seen = summary.get('seen_total')
    if seen is None:
        return ''
    return f'{strategy}:{seen}'
