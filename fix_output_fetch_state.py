#!/usr/bin/env python3
"""
Sync output JSON _fetch_state with per-paper cache files and rebuild
summary (formerly citation_count_summary) from per-year diagnostics,
nesting it inside year_fetch_diagnostics or direct_fetch_diagnostics.

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
    'fetch_strategy', 'num_citations_seen', 'dedup_count',
    'cached_year_counts', 'year_fetch_diagnostics',
    'probed_year_counts', 'complete', 'complete_fetch_attempt',
    'completed_years',
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

        # 2b. Rebuild year_fetch_diagnostics from probed/cached counts if missing
        yfd = state.get('year_fetch_diagnostics')
        probed = normalize_year_count_map(state.get("probed_year_counts"))
        cached_yc = state.get('cached_year_counts') or year_count_map(citations)
        if (not yfd) and probed and cached_yc:
            yfd = {}
            all_years = set(probed.keys()) | set(cached_yc.keys())
            for year in sorted(all_years):
                s = probed.get(year, cached_yc.get(year, 0))
                c = cached_yc.get(year, 0)
                yfd[str(year)] = {
                    'year': year,
                    'histogram_count': s,
                    'cached_total': c,
                    'seen_total': c,
                    'dedup_count': 0,
                    'termination_reason': 'short_page_stop',
                }
            state['year_fetch_diagnostics'] = yfd
            merged = True

        # 3. Rebuild summary (formerly citation_count_summary) and nest it
        # inside year_fetch_diagnostics (year mode) or direct_fetch_diagnostics
        # (direct mode) so callers always find it alongside the diagnostics it
        # belongs to.
        year_diags = state.get("year_fetch_diagnostics")
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
        # Infer fetch_strategy from available data
        if not state.get('fetch_strategy'):
            if state.get('year_fetch_diagnostics') or state.get('probed_year_counts'):
                state['fetch_strategy'] = 'year'
            else:
                state['fetch_strategy'] = 'direct'

        # Remove legacy fields
        state.pop('probed_year_total', None)
        state.pop('probe_complete', None)
        state.pop('cached_unyeared_count', None)
        state.pop('citation_count_summary', None)
        # Rename scholar_total → histogram_count in year_fetch_diagnostics entries
        yfd = state.get('year_fetch_diagnostics')
        if isinstance(yfd, dict):
            for key, diag in list(yfd.items()):
                if isinstance(diag, dict) and 'scholar_total' in diag and 'histogram_count' not in diag:
                    diag['histogram_count'] = diag.pop('scholar_total')
                    merged = True
                if isinstance(diag, dict):
                    diag.pop('mode', None)
                    diag.pop('underfetched', None)
                    diag.pop('underfetch_gap', None)
        # Restructure direct_fetch_diagnostics: move flat fields into summary,
        # renaming reported_total → scholar_total, yielded_total → cached_total
        dfd = state.get('direct_fetch_diagnostics')
        if isinstance(dfd, dict):
            flat_keys = ('reported_total', 'yielded_total', 'seen_total', 'dedup_count', 'termination_reason')
            if any(k in dfd for k in flat_keys):
                existing_summary = dfd.get('summary', {})
                if 'reported_total' in dfd:
                    existing_summary.setdefault('scholar_total', dfd.pop('reported_total'))
                if 'yielded_total' in dfd:
                    existing_summary.setdefault('cached_total', dfd.pop('yielded_total'))
                if 'seen_total' in dfd:
                    existing_summary.setdefault('seen_total', dfd.pop('seen_total'))
                if 'dedup_count' in dfd:
                    existing_summary.setdefault('dedup_count', dfd.pop('dedup_count'))
                if 'termination_reason' in dfd:
                    existing_summary.setdefault('termination_reason', dfd.pop('termination_reason'))
                dfd['summary'] = existing_summary
                merged = True
        if isinstance(dfd, dict):
            dfd.pop('mode', None)
            dfd.pop('underfetched', None)
            dfd.pop('underfetch_gap', None)
        # Remove direct_resume_state entirely (cross-run resume is not supported;
        # it belongs in per-paper cache files for within-run resume only)
        state.pop('direct_resume_state', None)
        state.pop('completed_years_in_current_run', None)

        # Nest summary in the appropriate diagnostics object.
        # For year mode: summary goes under year_fetch_diagnostics.
        # For direct mode: summary also goes under year_fetch_diagnostics
        # (generated from cached data), and direct_fetch_diagnostics carries
        # its own summary with direct-specific fields.
        strategy = state.get('fetch_strategy', 'direct')
        yfd = state.setdefault('year_fetch_diagnostics', {})
        if isinstance(yfd, dict) and yfd.get('summary') != new_summary:
            yfd['summary'] = new_summary
            merged = True
        if strategy == 'direct':
            dfd = state.get('direct_fetch_diagnostics')
            if isinstance(dfd, dict) and dfd.get('summary'):
                # Update the direct summary with latest scholar_total etc.
                dfd['summary'].update({
                    'scholar_total': new_summary.get('scholar_total'),
                    'cached_total': new_summary.get('cached_total'),
                    'seen_total': new_summary.get('seen_total'),
                    'dedup_count': new_summary.get('dedup_count'),
                })
                merged = True

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
