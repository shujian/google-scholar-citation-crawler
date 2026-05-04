#!/usr/bin/env python3
"""
Rebuild citation_count_summary in output JSON files to reflect current
code definitions (deriving totals from year_fetch_diagnostics).

Usage:
  python fix_output_fetch_state.py [output_dir]

If output_dir is omitted, defaults to ./output.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crawler.citation_strategy import build_citation_count_summary
from crawler.citation_cache import normalize_year_count_map


def migrate_one_file(json_path):
    """Read an output JSON, rebuild citation_count_summary per paper, save back."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    papers = data.get("papers", [])
    if not papers:
        return 0, 0

    updated = 0
    for paper in papers:
        state = paper.get("_fetch_state")
        if not state:
            continue

        year_diags = state.get("year_fetch_diagnostics")
        probed = normalize_year_count_map(state.get("probed_year_counts"))
        probe_complete = bool(state.get("probe_complete"))
        dedup = state.get("dedup_count", 0) or 0
        scholar = state.get("num_citations_on_scholar")
        citations = paper.get("citations", [])

        new_summary = build_citation_count_summary(
            citations,
            scholar_total=scholar,
            probed_year_counts=probed,
            probe_complete=probe_complete,
            dedup_count=dedup,
            year_fetch_diagnostics=year_diags,
        )
        new_summary.pop("cached_year_counts", None)
        new_summary.pop("probed_year_counts", None)

        old_summary = state.get("citation_count_summary", {})
        if old_summary != new_summary:
            state["citation_count_summary"] = new_summary
            # Remove stale top-level cached_unyeared_count (it lives in summary now)
            state.pop("cached_unyeared_count", None)
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

    for fname in sorted(os.listdir(output_dir)):
        if not fname.endswith("_paper_citations.json"):
            continue
        path = os.path.join(output_dir, fname)
        try:
            n, u = migrate_one_file(path)
            status = f"{u} updated" if u else "no changes"
            print(f"  {fname}: {n} papers, {status}")
        except Exception as e:
            print(f"  {fname}: ERROR - {e}")

    print("Done.")


if __name__ == "__main__":
    main()
