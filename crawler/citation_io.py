"""
crawler/citation_io.py — Citation cache I/O, status derivation, and XLSX output.

Functions accept all dependencies as explicit parameters so they remain
testable without a PaperCitationFetcher instance.  The fetcher methods
become thin wrappers that pass self.* attributes through.
"""

import hashlib
import json
import os

from crawler.citation_cache import normalize_year_fetch_diagnostics
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
    Extract completeness-relevant fields from a cached dict.

    Returns a flat dict with the summary-level targets and seen totals
    needed by resolve_citation_status_from_state.
    """
    citations = cached.get('citations', []) or []
    current = int(pub.get('num_citations', 0) or 0)
    fetch_policy = resolve_citation_fetch_policy(current, pub.get('year', 'N/A'), year_based_threshold)

    # seen_total is always read from diagnostics summaries
    # (direct_fetch_diagnostics for direct mode,
    # year_fetch_diagnostics for year mode).
    raw_year_diag = cached.get('year_fetch_diagnostics') or {}
    year_summary = raw_year_diag.get('summary') or {}
    year_diag = normalize_year_fetch_diagnostics(raw_year_diag)
    year_histogram_total = year_summary.get('histogram_total')
    year_seen_total = year_summary.get('seen_total')

    direct_fetch_diagnostics = cached.get('direct_fetch_diagnostics') or {}
    direct_summary = direct_fetch_diagnostics.get('summary') or {}
    direct_scholar_total = direct_summary.get('scholar_total')
    direct_seen_total = direct_summary.get('seen_total')

    # Fallback: if summary is missing but per-year diagnostics exist, derive
    # the totals from the per-year entries (legacy cache compatibility).
    if year_histogram_total is None and year_diag:
        year_histogram_total = sum(
            d.get('histogram_count', d.get('scholar_total', 0))
            for d in year_diag.values()
        )
    if year_seen_total is None and year_diag:
        year_seen_total = sum(d.get('seen_total', 0) for d in year_diag.values())

    # Derive num_seen from the appropriate diagnostics summary.
    if direct_seen_total is not None:
        num_seen = direct_seen_total
    elif year_seen_total is not None:
        num_seen = year_seen_total
    else:
        actual_cached = max(
            int(cached.get('num_citations_cached', len(citations)) or 0),
            len(citations),
        )
        num_seen = actual_cached

    return {
        'current': current,
        'fetch_policy': fetch_policy,
        'num_seen': num_seen,
        'year_histogram_total': year_histogram_total,
        'year_seen_total': year_seen_total,
        'direct_scholar_total': direct_scholar_total,
        'direct_seen_total': direct_seen_total,
        'complete_fetch_attempt': bool(cached.get('complete_fetch_attempt')),
    }


# ---------------------------------------------------------------------------
# Citation status
# ---------------------------------------------------------------------------

def resolve_citation_status_from_state(state):
    """
    Determine completeness from a pre-derived cache state dict.

    *state* must be the dict returned by derive_citation_cache_state().
    Returns 'complete' | 'partial'.

    Rules:
      - Year mode:  complete when histogram_total <= seen_total
      - Direct mode: complete when scholar_total <= seen_total
      - If the diagnostics summary is absent, fall back to
        num_seen >= current (the scholar page total).
    """
    fetch_policy = state['fetch_policy']
    num_seen = state['num_seen']

    if fetch_policy['strategy'] == 'year':
        target = state.get('year_histogram_total')
        if target is not None and num_seen is not None:
            return 'complete' if num_seen >= target else 'partial'

    elif fetch_policy['strategy'] == 'direct':
        target = state.get('direct_scholar_total')
        if target is not None and num_seen is not None:
            return 'complete' if num_seen >= target else 'partial'

    # No diagnostics summary available — fall back to top-level counters.
    current = state.get('current')
    if current is not None and num_seen is not None and num_seen >= current:
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

    # Collect all years across all papers (for per-year columns), newest first
    all_years = set()
    for item in results:
        for cite in item['citations']:
            y = cite.get('year')
            if y and y not in ('N/A', 'NA', '', None):
                try:
                    all_years.add(int(y))
                except (ValueError, TypeError):
                    pass
    year_cols = sorted(all_years, reverse=True)  # newest → oldest

    # Sheet1: Summary with per-year citation counts
    ws1 = wb.active
    ws1.title = "Summary"
    base_headers = ["No.", "Title", "Year", "Venue", "Citations (Scholar)", "Citations Collected", "Fetch Complete", "Unyeared"]
    base_widths  = [6,     55,      12,     25,      16,                    16,                    14,               10]
    all_headers = base_headers + [str(y) for y in year_cols]
    all_widths  = base_widths  + [8] * len(year_cols)

    for col, (w, h) in enumerate(zip(all_widths, all_headers), 1):
        # Convert column number to letter(s): A-Z, AA-AZ, etc.
        col_letter = ''
        n = col
        while n:
            n, r = divmod(n - 1, 26)
            col_letter = chr(65 + r) + col_letter
        ws1.column_dimensions[col_letter].width = w
        c = ws1.cell(row=1, column=col, value=h)
        c.fill, c.font, c.alignment = hdr_fill, hdr_font, center
    ws1.row_dimensions[1].height = 28

    for i, item in enumerate(results, 2):
        pub = item['pub']
        citations = item['citations']
        # Build per-year counts
        year_counts = {}
        unyeared = 0
        for cite in citations:
            y = cite.get('year')
            if y and y not in ('N/A', 'NA', '', None):
                try:
                    year_counts[int(y)] = year_counts.get(int(y), 0) + 1
                except (ValueError, TypeError):
                    unyeared += 1
            else:
                unyeared += 1

        ws1.cell(row=i, column=1, value=pub['no']).alignment = center
        ws1.cell(row=i, column=2, value=pub['title']).alignment = wrap
        ws1.cell(row=i, column=3, value=pub.get('year', 'N/A')).alignment = center
        ws1.cell(row=i, column=4, value=pub.get('venue', 'N/A')).alignment = wrap
        ws1.cell(row=i, column=5, value=pub['num_citations']).alignment = center
        ws1.cell(row=i, column=6, value=len(citations)).alignment = center
        ws1.cell(row=i, column=7, value='Y' if item.get('fetch_complete') else 'N').alignment = center
        ws1.cell(row=i, column=8, value=unyeared if unyeared else None).alignment = center
        for j, year in enumerate(year_cols, 9):
            count = year_counts.get(year)
            ws1.cell(row=i, column=j, value=count if count else None).alignment = center
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
