#!/usr/bin/env python3
"""
Sync output JSON _fetch_state with per-paper cache files and normalise
all diagnostics via PaperFetchState.from_dict/to_dict round-trip.

Usage:
  python fix_output_fetch_state.py [output_dir]

If output_dir is omitted, defaults to ./output.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crawler.output_state import PaperFetchState
from crawler.pub_info import PubInfo


def migrate_one_file(json_path):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    papers = data.get("papers", [])
    if not papers:
        return 0, 0

    updated = 0
    for paper in papers:
        state = paper.get("_fetch_state")
        pub = paper.get('pub') or {}
        title = pub.get('title', '') or (state or {}).get('title', '')
        if not title:
            continue
        if not state:
            paper['_fetch_state'] = state = {}

        state_before = dict(state)

        # 1. Sync num_citations_on_scholar from profile pub.
        scholar = pub.get('num_citations')
        if scholar is not None and state.get('num_citations_on_scholar') != scholar:
            state['num_citations_on_scholar'] = scholar

        # 2. If year_fetch_diagnostics is corrupted (all zeros) but
        # year_records has data, rebuild the summary from records.
        yfd = state.get('year_fetch_diagnostics') or {}
        yr = state.get('year_records')
        if isinstance(yfd, dict) and isinstance(yr, list) and yr:
            if not any(yfd.get(k) for k in ('histogram_total', 'cached_total', 'seen_total')):
                state['year_fetch_diagnostics'] = {
                    'scholar_total': state.get('num_citations_on_scholar'),
                    'histogram_total': sum(r.get('histogram_count', 0) for r in yr),
                    'cached_total': sum(r.get('cached_total', 0) for r in yr),
                    'cached_year_total': sum(r.get('cached_total', 0) for r in yr),
                    'seen_total': sum(r.get('seen_total', 0) for r in yr),
                    'cached_unyeared_count': 0,
                    'dedup_count': sum(r.get('dedup_count', 0) for r in yr),
                    'scholar_unyeared_count': None,
                }

        # 3. Round-trip through PaperFetchState to normalise everything.
        fs = PaperFetchState.from_dict(state)
        normalised = fs.to_dict()
        if normalised != state_before:
            paper['_fetch_state'] = normalised
            updated += 1

        # 4. Normalise pub field via PubInfo.
        pub_normalised = PubInfo.from_dict(pub).to_dict()
        if pub_normalised != pub:
            paper['pub'] = pub_normalised
            updated += 1

        # 5. Set fetch_complete at paper level.
        paper['fetch_complete'] = fs.complete_fetch_attempt

    if updated:
        bak = json_path + ".bak"
        os.rename(json_path, bak)
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.remove(bak)
        except Exception:
            os.rename(bak, json_path)
            raise

    return len(papers), updated


def main():
    output_dir = sys.argv[1] if len(sys.argv) > 1 else "./output"
    if not os.path.isdir(output_dir):
        print(f"Error: {output_dir} is not a directory")
        sys.exit(1)

    for fname in sorted(os.listdir(output_dir)):
        if not fname.endswith("_paper_citations.json") or fname.endswith(".bak"):
            continue
        path = os.path.join(output_dir, fname)
        try:
            n, u = migrate_one_file(path)
            status = f"{u} updated" if u else "no changes"
            print(f"  {fname}: {n} papers, {status}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  {fname}: ERROR - {e}")

    print("Done.")


if __name__ == "__main__":
    main()
