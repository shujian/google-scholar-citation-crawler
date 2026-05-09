#!/usr/bin/env python3
"""
fix_diag_from_year_record.py — Rebuild diagnostics from source data.

Reads an author_<ID>_paper_citations.json output file and fixes
inconsistent diagnostics:

  Year mode:   rebuild year_fetch_diagnostics by summing year_records.
  Direct mode: if direct_fetch_diagnostics is absent, compute cached_total
               and seen_total from the citations array.

Usage:
  python fix_diag_from_year_record.py <output_json_path>
"""

import json
import sys
import os


def fix_year_diagnostics(state):
    """Rebuild year_fetch_diagnostics from year_records."""
    records = state.get('year_records') or []
    if not records:
        return state

    histogram_total = sum(
        r.get('histogram_count', r.get('scholar_total', 0)) or 0
        for r in records
    )
    cached_total = sum(r.get('cached_total', 0) or 0 for r in records)
    seen_total = sum(r.get('seen_total', 0) or 0 for r in records)
    dedup_count = sum(r.get('dedup_count', 0) or 0 for r in records)

    state['year_fetch_diagnostics'] = {
        'scholar_total': state.get('num_citations_on_scholar'),
        'histogram_total': histogram_total,
        'cached_total': cached_total,
        'cached_year_total': cached_total,
        'seen_total': seen_total,
        'cached_unyeared_count': 0,
        'dedup_count': dedup_count,
        'scholar_unyeared_count': max(
            0,
            (state.get('num_citations_on_scholar') or 0) - histogram_total,
        ) if state.get('num_citations_on_scholar') is not None else None,
    }
    return state


def fix_direct_diagnostics(state, citations):
    """Compute direct_fetch_diagnostics from citations when absent."""
    dfd = state.get('direct_fetch_diagnostics')
    if isinstance(dfd, dict):
        return state
    n = len(citations)
    state['direct_fetch_diagnostics'] = {
        'scholar_total': state.get('num_citations_on_scholar'),
        'cached_total': n,
        'seen_total': n,
        'dedup_count': 0,
        'termination_reason': 'derived_from_citations',
    }
    return state


def fix_output_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    papers = data.get('papers', [])
    changed = 0

    for paper in papers:
        state = paper.get('_fetch_state')
        if not state:
            continue

        strategy = state.get('fetch_strategy')

        if strategy == 'year':
            before = state.get('year_fetch_diagnostics')
            fix_year_diagnostics(state)
            after = state.get('year_fetch_diagnostics')
            if before != after:
                changed += 1
                title = paper.get('pub', {}).get('title', '?')[:60]
                print(f"  [year] {title}: rebuilt diag from {len(state.get('year_records') or [])} years")

        elif strategy == 'direct':
            citations = paper.get('citations', [])
            before = state.get('direct_fetch_diagnostics')
            fix_direct_diagnostics(state, citations)
            after = state.get('direct_fetch_diagnostics')
            if before != after:
                changed += 1
                title = paper.get('pub', {}).get('title', '?')[:60]
                n = len(citations)
                print(f"  [direct] {title}: created diag from {n} citations")

    if changed == 0:
        print("  No changes needed.")
        return

    # Write back
    bak = path + '.bak'
    if not os.path.exists(bak):
        os.rename(path, bak)
    else:
        os.remove(path)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nFixed {changed} papers. Backup: {bak}")


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <output_json_path>")
        sys.exit(1)

    path = sys.argv[1]
    if not os.path.exists(path):
        print(f"Error: {path} not found")
        sys.exit(1)

    print(f"Processing: {path}")
    fix_output_file(path)


if __name__ == '__main__':
    main()
