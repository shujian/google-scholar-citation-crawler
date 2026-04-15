"""
crawler/citation_io.py — Citation cache I/O, status derivation, and XLSX output.

Functions accept all dependencies as explicit parameters so they remain
testable without a PaperCitationFetcher instance.  The fetcher methods
become thin wrappers that pass self.* attributes through.
"""

import hashlib
import json
import os

from crawler.citation_cache import (
    normalize_year_count_map,
    normalize_year_fetch_diagnostics,
    rehydrate_probe_metadata,
    year_count_map,
    probed_year_counts_satisfied,
)
from crawler.citation_strategy import resolve_citation_fetch_policy


# ---------------------------------------------------------------------------
# Cache path + load
# ---------------------------------------------------------------------------

def citation_cache_path(cache_dir, title):
    """Return the path to the per-paper citation cache JSON file."""
    key = hashlib.md5(title.encode('utf-8')).hexdigest()[:16]
    return os.path.join(cache_dir, f"{key}.json")


def load_citation_cache(cache_dir, title):
    """Load and return the cached citation dict for *title*, or None."""
    path = citation_cache_path(cache_dir, title)
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


# ---------------------------------------------------------------------------
# Cache state derivation
# ---------------------------------------------------------------------------

def derive_citation_cache_state(pub, cached, year_based_threshold):
    """
    Extract and normalise all completeness-relevant fields from a cached dict.

    Returns a flat dict with keys:
        current, fetch_policy, actual_cached, promoted_scholar_total,
        num_seen, probed_year_counts, probe_complete, probed_hist_total,
        cached_year_counts, year_fetch_diagnostics, direct_fetch_diagnostics.
    """
    citations = cached.get('citations', []) or []
    current = int(pub.get('num_citations', 0) or 0)
    fetch_policy = resolve_citation_fetch_policy(current, pub.get('year', 'N/A'), year_based_threshold)

    actual_cached = cached.get('num_citations_cached', len(citations))
    try:
        actual_cached = int(actual_cached)
    except (TypeError, ValueError):
        actual_cached = len(citations)
    actual_cached = max(actual_cached, len(citations))

    try:
        promoted_scholar_total = int(cached.get('num_citations_on_scholar', 0) or 0)
    except (TypeError, ValueError):
        promoted_scholar_total = 0

    num_seen = cached.get('num_citations_seen')
    try:
        num_seen = int(num_seen) if num_seen is not None else None
    except (TypeError, ValueError):
        num_seen = None

    direct_fetch_diagnostics = cached.get('direct_fetch_diagnostics') or {}
    if direct_fetch_diagnostics.get('mode') != 'direct':
        direct_fetch_diagnostics = {}
    direct_seen_total = direct_fetch_diagnostics.get('seen_total')
    try:
        direct_seen_total = int(direct_seen_total) if direct_seen_total is not None else None
    except (TypeError, ValueError):
        direct_seen_total = None
    if num_seen is None and direct_seen_total is not None:
        num_seen = direct_seen_total
    if num_seen is not None:
        num_seen = max(num_seen, actual_cached)

    probed_year_counts, probe_complete = rehydrate_probe_metadata(cached, current)
    probed_hist_total = cached.get('probed_year_total')
    try:
        probed_hist_total = int(probed_hist_total)
    except (TypeError, ValueError):
        probed_hist_total = sum((probed_year_counts or {}).values())

    cached_year_counts = normalize_year_count_map(cached.get('cached_year_counts'))
    if not cached_year_counts:
        cached_year_counts = year_count_map(citations)

    year_fetch_diag = normalize_year_fetch_diagnostics(cached.get('year_fetch_diagnostics'))

    return {
        'current': current,
        'fetch_policy': fetch_policy,
        'actual_cached': actual_cached,
        'promoted_scholar_total': promoted_scholar_total,
        'num_seen': num_seen,
        'probed_year_counts': probed_year_counts,
        'probe_complete': probe_complete,
        'probed_hist_total': probed_hist_total,
        'cached_year_counts': cached_year_counts,
        'year_fetch_diagnostics': year_fetch_diag,
        'direct_fetch_diagnostics': direct_fetch_diagnostics,
    }


# ---------------------------------------------------------------------------
# Citation status
# ---------------------------------------------------------------------------

def resolve_citation_status_from_state(state):
    """
    Determine completeness from a pre-derived cache state dict.

    *state* must be the dict returned by derive_citation_cache_state().
    Returns 'complete' | 'partial'.
    """
    current = state['current']
    fetch_policy = state['fetch_policy']
    actual_cached = state['actual_cached']
    promoted_scholar_total = state['promoted_scholar_total']
    num_seen = state['num_seen']
    probed_year_counts = state['probed_year_counts']
    probe_complete = state['probe_complete']
    probed_hist_total = state['probed_hist_total']
    cached_year_counts = state['cached_year_counts']
    year_fetch_diagnostics = state['year_fetch_diagnostics']

    year_histogram_satisfied = bool(probed_year_counts) and probed_year_counts_satisfied(
        cached_year_counts, probed_year_counts, year_fetch_diagnostics,
    )
    histogram_match_complete = (
        bool(probed_year_counts)
        and year_histogram_satisfied
        and current >= probed_hist_total
    )
    probe_histogram_complete = probe_complete and histogram_match_complete

    if fetch_policy['mode'] == 'year' and probe_complete:
        if probe_histogram_complete:
            return 'complete'
        return 'partial'

    if num_seen is not None:
        if fetch_policy['mode'] == 'direct':
            if num_seen >= current:
                return 'complete'
        elif probed_year_counts:
            if num_seen >= probed_hist_total:
                return 'complete'
            if histogram_match_complete:
                return 'complete'
        elif num_seen >= current:
            return 'complete'

    if histogram_match_complete:
        return 'complete'

    if (
        current <= promoted_scholar_total
        and actual_cached >= current
        and (fetch_policy['mode'] == 'direct' or not probed_year_counts)
    ):
        return 'complete'
    return 'partial'


def citation_status(pub, cache_dir, year_based_threshold):
    """
    Return the completeness status for *pub*: 'skip_zero' | 'missing' |
    'complete' | 'partial'.

    Reads the cache from *cache_dir* via load_citation_cache.
    """
    if pub['num_citations'] == 0:
        return 'skip_zero'
    cached = load_citation_cache(cache_dir, pub['title'])
    if not cached:
        return 'missing'
    state = derive_citation_cache_state(pub, cached, year_based_threshold)
    return resolve_citation_status_from_state(state)


# ---------------------------------------------------------------------------
# XLSX output
# ---------------------------------------------------------------------------

def save_citations_xlsx(
    out_xlsx_path,
    results,
    author_id,
    metadata=None,
    *,
    openpyxl_module,
    font_cls,
    pattern_fill_cls,
    alignment_cls,
):
    """Write a 3-sheet citation Excel file to *out_xlsx_path*."""
    wb = openpyxl_module.Workbook()
    metadata = metadata or {}

    hdr_fill = pattern_fill_cls(start_color="4472C4", end_color="4472C4", fill_type="solid")
    hdr_font = font_cls(bold=True, color="FFFFFF", size=11)
    center = alignment_cls(horizontal="center", vertical="center")
    wrap = alignment_cls(vertical="center", wrap_text=True)

    # Sheet1: Summary
    ws1 = wb.active
    ws1.title = "Summary"
    for col, (w, h) in enumerate(zip(
        [6, 55, 12, 25, 16, 16],
        ["No.", "Title", "Year", "Venue", "Citations (Scholar)", "Citations Collected"]
    ), 1):
        ws1.column_dimensions[chr(64 + col)].width = w
        c = ws1.cell(row=1, column=col, value=h)
        c.fill, c.font, c.alignment = hdr_fill, hdr_font, center
    ws1.row_dimensions[1].height = 28

    for i, item in enumerate(results, 2):
        pub = item['pub']
        ws1.cell(row=i, column=1, value=pub['no']).alignment = center
        ws1.cell(row=i, column=2, value=pub['title']).alignment = wrap
        ws1.cell(row=i, column=3, value=pub.get('year', 'N/A')).alignment = center
        ws1.cell(row=i, column=4, value=pub.get('venue', 'N/A')).alignment = wrap
        ws1.cell(row=i, column=5, value=pub['num_citations']).alignment = center
        ws1.cell(row=i, column=6, value=len(item['citations'])).alignment = center
        ws1.row_dimensions[i].height = 36

    # Sheet2: All Citations
    ws2 = wb.create_sheet("All Citations")
    for col, (w, h) in enumerate(zip(
        [45, 50, 35, 25, 10, 18, 55],
        ["Cited Paper", "Citing Paper Title", "Authors", "Venue", "Year", "Cites ID", "Link"]
    ), 1):
        ws2.column_dimensions[chr(64 + col)].width = w
        c = ws2.cell(row=1, column=col, value=h)
        c.fill, c.font, c.alignment = hdr_fill, hdr_font, center
    ws2.row_dimensions[1].height = 28

    row = 2
    for item in results:
        for cite in item['citations']:
            ws2.cell(row=row, column=1, value=item['pub']['title']).alignment = wrap
            ws2.cell(row=row, column=2, value=cite['title']).alignment = wrap
            ws2.cell(row=row, column=3, value=cite['authors']).alignment = wrap
            ws2.cell(row=row, column=4, value=cite['venue']).alignment = wrap
            ws2.cell(row=row, column=5, value=cite['year']).alignment = center
            ws2.cell(row=row, column=6, value=cite.get('cites_id', 'N/A') or 'N/A').alignment = wrap
            url = cite['url']
            lc = ws2.cell(row=row, column=7, value=url)
            if url and url != 'N/A':
                try:
                    lc.hyperlink = url
                    lc.font = font_cls(color="0563C1", underline="single")
                except Exception:
                    pass
            lc.alignment = wrap
            ws2.row_dimensions[row].height = 32
            row += 1

    # Sheet3: Run Metadata
    ws3 = wb.create_sheet("Run Metadata")
    ws3.column_dimensions['A'].width = 26
    ws3.column_dimensions['B'].width = 50
    for row_idx, (key, value) in enumerate([
        ("Author ID", metadata.get('author_id', author_id)),
        ("Fetch Time", metadata.get('fetch_time', 'N/A')),
        ("Total Papers", metadata.get('total_papers', len(results))),
        ("Total Citations Collected", metadata.get(
            'total_citations_collected',
            sum(len(item['citations']) for item in results),
        )),
    ], 1):
        label = ws3.cell(row=row_idx, column=1, value=key)
        label.fill, label.font, label.alignment = hdr_fill, hdr_font, center
        ws3.cell(row=row_idx, column=2, value=value).alignment = wrap

    wb.save(out_xlsx_path)
