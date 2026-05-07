#!/usr/bin/env python3
"""
Sync output JSON _fetch_state with per-paper cache files and normalise
all diagnostics via PaperFetchState.from_dict/to_dict round-trip.

Usage:
  python fix_output_fetch_state.py [output_dir]

If output_dir is omitted, defaults to ./output.
"""

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crawler.output_state import PaperFetchState
from crawler.pub_info import PubInfo


# Fields to merge from per-paper cache files into the output state before
# normalisation.  These supplement what is already in the output file.
CACHE_KEYS = [
    'fetch_strategy',
    'year_fetch_diagnostics',
    'direct_fetch_diagnostics',
    'complete_fetch_attempt',
    'num_citations_on_scholar',
    'fetched_at',
]

# Legacy fields that may still exist in cache files; merged for completeness
# but will be stripped by the PaperFetchState round-trip.
LEGACY_CACHE_KEYS = [
    'dedup_count', 'probed_year_counts', 'complete', 'completed_years',
    'num_citations_cached',
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
        title = (paper.get('pub') or {}).get('title', '') or (state or {}).get('title', '')
        if not title:
            continue
        if not state:
            paper['_fetch_state'] = state = {}

        # 1. Merge fresh data from per-paper cache file.
        cache_path = _cache_path(cache_dir, title)
        if os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            for key in CACHE_KEYS + LEGACY_CACHE_KEYS:
                if key in cached and state.get(key) != cached[key]:
                    state[key] = cached[key]

        # 2. Sync num_citations_on_scholar from profile pub.
        pub = paper.get('pub') or {}
        scholar = pub.get('num_citations')
        if scholar is not None and state.get('num_citations_on_scholar') != scholar:
            state['num_citations_on_scholar'] = scholar

        # 3. Round-trip through PaperFetchState to normalise everything.
        fs = PaperFetchState.from_dict(state)
        normalised = fs.to_dict()
        if normalised != state:
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
