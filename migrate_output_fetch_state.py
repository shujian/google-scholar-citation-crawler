#!/usr/bin/env python3
"""
One-off migration: populate _fetch_state in an existing output JSON
from the corresponding per-paper cache files.

Usage:
    python migrate_output_fetch_state.py output/author_<id>_paper_citations.json

The script reads the output file, finds each paper's cache file under
output/scholar_cache/author_<id>/citations/, extracts the control fields
(excluding citations array), and writes them back as _fetch_state.
A backup of the original output file is created with .backup suffix.
"""

import hashlib
import json
import os
import sys

from crawler.output_state import extract_fetch_state


def citation_cache_path(cache_dir, title):
    key = hashlib.md5(title.encode('utf-8')).hexdigest()[:16]
    return os.path.join(cache_dir, f"{key}.json")


def migrate(output_path):
    if not os.path.exists(output_path):
        print(f"Error: {output_path} not found.")
        return 1

    with open(output_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Derive cache dir from output path: output/author_<id>_paper_citations.json
    # -> output/scholar_cache/author_<id>/citations/
    output_dir = os.path.dirname(os.path.abspath(output_path))
    author_id = data.get('author_id', '')
    cache_dir = os.path.join(output_dir, "scholar_cache", f"author_{author_id}", "citations")

    papers = data.get('papers', [])
    migrated = 0
    skipped = 0

    for paper in papers:
        pub = paper.get('pub', {})
        title = pub.get('title', '')
        if not title:
            skipped += 1
            continue

        cache_path = citation_cache_path(cache_dir, title)
        if not os.path.exists(cache_path):
            skipped += 1
            continue

        with open(cache_path, 'r', encoding='utf-8') as f:
            cached = json.load(f)

        fetch_state = extract_fetch_state(cached)
        if fetch_state:
            paper['_fetch_state'] = fetch_state
            migrated += 1
        else:
            skipped += 1

    # Backup original
    backup_path = output_path + '.backup'
    with open(backup_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Backup written to: {backup_path}")

    # Write migrated output
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Migrated output written to: {output_path}")
    print(f"  Papers migrated: {migrated}")
    print(f"  Papers skipped (no cache): {skipped}")
    return 0


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} <path_to_author_paper_citations.json>")
        sys.exit(1)
    sys.exit(migrate(sys.argv[1]))
