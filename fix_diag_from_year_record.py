#!/usr/bin/env python3
"""
fix_diag_from_year_record.py — Rebuild diagnostics from source data.

Reads an author_<ID>_paper_citations.json output file and fixes
inconsistent diagnostics using PaperFetchState methods:

  Year mode:   restore_year_diag_from_year_records()
  Direct mode: restore_direct_diag_from_citations(citations)

Usage:
  python fix_diag_from_year_record.py <output_json_path>
"""

import json
import sys
import os

from crawler.output_state import PaperFetchState


def fix_output_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    papers = data.get('papers', [])
    changed = 0

    for paper in papers:
        state_dict = paper.get('_fetch_state')
        if not state_dict:
            continue

        fs = PaperFetchState.from_dict(state_dict)
        strategy = fs.fetch_strategy

        if strategy == 'year':
            before = fs.year_fetch_diagnostics
            fs.restore_year_diag_from_year_records()
            after = fs.year_fetch_diagnostics
            if before != after:
                changed += 1
                title = paper.get('pub', {}).get('title', '?')[:60]
                n = len(fs.year_records or [])
                print(f"  [year] {title}: rebuilt diag from {n} years")

        elif strategy == 'direct':
            citations = paper.get('citations', [])
            before = fs.direct_fetch_diagnostics
            fs.restore_direct_diag_from_citations(citations)
            after = fs.direct_fetch_diagnostics
            if before != after:
                changed += 1
                title = paper.get('pub', {}).get('title', '?')[:60]
                n = len(citations)
                print(f"  [direct] {title}: created diag from {n} citations")

        # Write back the fixed state
        paper['_fetch_state'] = fs.to_dict()

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
