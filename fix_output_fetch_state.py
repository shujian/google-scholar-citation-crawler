#!/usr/bin/env python3
"""
Sync output JSON _fetch_state with per-paper cache files and rebuild
citation_count_summary from per-year diagnostics.

Usage:
  python fix_output_fetch_state.py [output_dir]

If output_dir is omitted, defaults to ./output.
"""

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crawler.citation_strategy import build_citation_count_summary
from crawler.citation_cache import normalize_year_count_map, year_count_map


CACHE_KEYS = [
    'num_citations_seen', 'dedup_count', 'cached_year_counts',
    'year_fetch_diagnostics', 'probed_year_counts', 'probed_year_total',
    'probe_complete', 'complete', 'complete_fetch_attempt',
    'completed_years', 'completed_years_in_current_run',
    'num_citations_on_scholar', 'num_citations_cached',
]


def _cache_path(cache_dir, title):
    key = hashlib.md5(title.encode('utf-8')).hexdigest()[:16]
    return os.path.join(cache_dir, f"{key}.json")


def migrate_one_file(json_path, cache_dir):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    papers = data.get("papers", [])
    if not papers:
        return 0, 0

    updated = 0
    for paper in papers:
        state = paper.get("_fetch_state")
        title = (paper.get('pub') or {}).get('title', '') or state.get('title', '')
        if not state or not title:
            continue

        # 1. Merge fresh data from per-paper cache file
        cache_path = _cache_path(cache_dir, title)
        merged = False
        if os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            for key in CACHE_KEYS:
                if key in cached:
                    if key not in state or state[key] != cached[key]:
                        state[key] = cached[key]
                        merged = True

        # 2. Update num_citations_* from actual citations array
        citations = paper.get("citations", [])
        if citations:
            yfd = state.get('year_fetch_diagnostics') or {}
            dedup = sum(d.get('dedup_count', 0) for d in yfd.values()) or state.get('dedup_count', 0)
            state['num_citations_cached'] = len(citations)
            state['num_citations_seen'] = len(citations) + (dedup or 0)
            state['cached_year_counts'] = year_count_map(citations)

        # 3. Rebuild citation_count_summary from current state
        year_diags = state.get("year_fetch_diagnostics")
        probed = normalize_year_count_map(state.get("probed_year_counts"))
        dedup = state.get("dedup_count", 0) or 0
        scholar = state.get("num_citations_on_scholar")

        new_summary = build_citation_count_summary(
            citations,
            scholar_total=scholar,
            probed_year_counts=probed,
            probe_complete=False,
            dedup_count=dedup,
            year_fetch_diagnostics=year_diags,
        )
        new_summary.pop("cached_year_counts", None)
        new_summary.pop("probed_year_counts", None)

        # Prefer per-year sums for dedup_count / num_citations_seen
        if new_summary.get('dedup_count'):
            state['dedup_count'] = new_summary['dedup_count']
        if new_summary.get('seen_total'):
            state['num_citations_seen'] = new_summary['seen_total']

        old_summary = state.get("citation_count_summary", {})
        if old_summary != new_summary:
            state["citation_count_summary"] = new_summary
            merged = True

        state.pop("cached_unyeared_count", None)
        state.pop("probe_complete", None)

        # 4. Set fetch_complete at paper level from _fetch_state
        if state.get('complete_fetch_attempt') or state.get('complete'):
            paper['fetch_complete'] = True

        if merged:
            updated += 1

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

    # Find author ID from output file names
    author_id = None
    for fname in os.listdir(output_dir):
        if fname.endswith("_paper_citations.json") and not fname.endswith(".bak"):
            author_id = fname.replace("_paper_citations.json", "").replace("author_", "")
            break

    if not author_id:
        print("No paper_citations.json found")
        sys.exit(1)

    cache_dir = os.path.join(output_dir, "scholar_cache", f"author_{author_id}", "citations")

    for fname in sorted(os.listdir(output_dir)):
        if not fname.endswith("_paper_citations.json") or fname.endswith(".bak"):
            continue
        path = os.path.join(output_dir, fname)
        try:
            n, u = migrate_one_file(path, cache_dir)
            status = f"{u} updated" if u else "no changes"
            print(f"  {fname}: {n} papers, {status}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  {fname}: ERROR - {e}")

    print("Done.")


if __name__ == "__main__":
    main()
