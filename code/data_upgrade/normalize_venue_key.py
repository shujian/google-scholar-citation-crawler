#!/usr/bin/env python3
"""
Add / refresh the derived ``venue_key`` field in the crawler's output files.

``venue_key`` is the normalized ``<venue name><year>`` identity (``ACL2026``,
``Findings of ACL2026``) produced by ``code/venue_key.py`` for the *future*
dedup / uniqueness checks.  New data gets it automatically (``PubInfo`` and
``extract_citation_info`` write it), but rows that were already on disk never
had the field, so this tool applies the rule to the existing files.

Usage (from the project root):

  python -m code.data_upgrade.normalize_venue_key                 # dry-run, prints a summary
  python -m code.data_upgrade.normalize_venue_key --json          # the same plan, as JSON
  python -m code.data_upgrade.normalize_venue_key --review-file output/venue_key_review.json
  python -m code.data_upgrade.normalize_venue_key --apply --backup-dir output/venue_key_backup

Behaviour:

* targets ``output/author_*_profile.json`` (publications) and
  ``output/author_*_paper_citations.json`` (each paper's ``pub`` **and** every
  ``citations[*]`` entry); ``--author ID`` narrows it to one author,
  ``--output-dir DIR`` changes the directory.
* **only** the two derived fields are touched — ``venue`` / ``venue_full`` /
  ``year`` / ``cites_id`` and every other key stay byte-identical.
* publications use ``venue_full or venue`` (the detail page's untruncated name
  resolves venues the list page cut off), citations use their own ``venue`` —
  that is the best text each record has.
* **default is dry-run** and creates nothing; ``--apply`` writes the files
  atomically and copies each one it changes into the backup directory together
  with a ``manifest.json`` (path + sha256 before + change counts).
* idempotent: a second run reports 0 changes.
* unknown venues keep the venue text as their key (``venue_key_source='raw'``);
  recognised venues get ``venue_key_source='normalized'``;
  the report lists every distinct one with its count so the table in
  ``code/venue_key.py`` can be extended deliberately.
"""

import argparse
import glob
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime

# Keep the project root importable when invoked as a script path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from code.common import write_json_atomic  # noqa: E402
from code.venue_key import resolve_venue_key  # noqa: E402

PROFILE_GLOB = 'author_*_profile.json'
CITATIONS_GLOB = 'author_*_paper_citations.json'
REPORT_LIMIT = 40


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _apply_fields(record, key, source):
    """Set the two derived fields; return True when something changed.

    Both fields are written explicitly (``None`` included) so every record has
    the same shape; the presence check keeps a record that already carries the
    empty pair from looking like a change.
    """
    if ('venue_key' in record and record.get('venue_key') == key
            and record.get('venue_key_source') == source):
        return False
    record['venue_key'] = key
    record['venue_key_source'] = source
    return True


def _scan_pub(pub, stats, unknown):
    key, source = resolve_venue_key(pub.get('venue_full') or pub.get('venue'), pub.get('year'))
    stats['total'] += 1
    if key is None:
        stats['no_venue'] += 1
    elif source == 'normalized':
        stats['normalized'] += 1
    else:
        stats['raw'] += 1
        unknown[key] = unknown.get(key, 0) + 1
    return _apply_fields(pub, key, source)


def _scan_citation(citation, stats, unknown):
    key, source = resolve_venue_key(citation.get('venue'), citation.get('year'))
    stats['total'] += 1
    if key is None:
        stats['no_venue'] += 1
    elif source == 'normalized':
        stats['normalized'] += 1
    else:
        stats['raw'] += 1
        unknown[key] = unknown.get(key, 0) + 1
    return _apply_fields(citation, key, source)


def _empty_stats():
    return {'total': 0, 'normalized': 0, 'raw': 0, 'no_venue': 0, 'updated': 0}


def plan_profile(path):
    """Return (payload, stats, unknown) for an author profile file."""
    with open(path, 'r', encoding='utf-8') as handle:
        payload = json.load(handle)
    stats, unknown = _empty_stats(), {}
    for pub in payload.get('publications', []) or []:
        if isinstance(pub, dict) and _scan_pub(pub, stats, unknown):
            stats['updated'] += 1
    return payload, stats, unknown


def plan_citations(path):
    """Return (payload, stats, unknown) for a paper-citations file."""
    with open(path, 'r', encoding='utf-8') as handle:
        payload = json.load(handle)
    stats, unknown = _empty_stats(), {}
    for paper in payload.get('papers', []) or []:
        pub = paper.get('pub')
        if isinstance(pub, dict) and _scan_pub(pub, stats, unknown):
            stats['updated'] += 1
        for citation in paper.get('citations', []) or []:
            if isinstance(citation, dict) and _scan_citation(citation, stats, unknown):
                stats['updated'] += 1
    return payload, stats, unknown


def discover_files(output_dir, author=None):
    """Return [(path, kind)] for the profile/citations files to scan.

    With ``author`` the two exact file names are used (``author_<id>_profile.json``
    and ``author_<id>_paper_citations.json``); without it every author in the
    directory is scanned.  Backup/corrupt copies do not match either pattern.
    """
    if author:
        names = [(f'author_{author}_profile.json', 'profile'),
                 (f'author_{author}_paper_citations.json', 'citations')]
        return [(os.path.join(output_dir, name), kind) for name, kind in names
                if os.path.exists(os.path.join(output_dir, name))]
    found = []
    for path in sorted(glob.glob(os.path.join(output_dir, 'author_*_profile.json'))):
        found.append((path, 'profile'))
    for path in sorted(glob.glob(os.path.join(output_dir, 'author_*_paper_citations.json'))):
        found.append((path, 'citations'))
    return found


def _merge_unknown(target, source):
    for key, count in source.items():
        target[key] = target.get(key, 0) + count


def build_plan(output_dir, author=None):
    """Read every target file and return the full plan (nothing is written)."""
    plan = {'files': [], 'pubs': _empty_stats(), 'citations': _empty_stats(),
            'unknown_pubs': {}, 'unknown_citations': {}}
    for path, kind in discover_files(output_dir, author):
        try:
            if kind == 'profile':
                payload, stats, unknown = plan_profile(path)
                plan['pubs']['total'] += stats['total']
                for field in ('normalized', 'raw', 'no_venue', 'updated'):
                    plan['pubs'][field] += stats[field]
                _merge_unknown(plan['unknown_pubs'], unknown)
            else:
                payload, stats, unknown = plan_citations(path)
                _merge_unknown(plan['unknown_citations'], unknown)
                for key, value in stats.items():
                    plan['citations'][key] = plan['citations'].get(key, 0) + value
        except (OSError, ValueError) as exc:
            plan['files'].append({'path': path, 'kind': kind, 'error': str(exc)})
            continue
        plan['files'].append({
            'path': path,
            'kind': kind,
            'sha256': _sha256(path),
            'updated': stats['updated'],
            'total': stats['total'],
            'payload': payload,
        })
    return plan


def apply_plan(plan, backup_dir, output_dir):
    """Write the changed files and back the previous versions up.

    The default backup directory lives under *output_dir* (not the current
    working directory), so a tool run against another data directory never
    drops backups into this repository's ``output/``.
    """
    if not any(entry.get('updated') for entry in plan['files']):
        # Nothing to do: do not leave an empty backup directory behind.
        return None, 0
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_dir = backup_dir or os.path.join(output_dir, f'venue_key_backup_{stamp}')
    os.makedirs(backup_dir, exist_ok=True)
    manifest = {'created_at': datetime.now().isoformat(), 'backup_dir': backup_dir, 'files': []}
    written = 0
    for entry in plan['files']:
        if 'error' in entry:
            continue
        if entry['updated']:
            backup_path = os.path.join(backup_dir, os.path.basename(entry['path']))
            shutil.copy2(entry['path'], backup_path)
            write_json_atomic(entry['path'], entry.pop('payload'),
                              ensure_ascii=False, indent=2)
            written += 1
            manifest['files'].append({
                'path': entry['path'],
                'backup': backup_path,
                'sha256_before': entry['sha256'],
                'updated': entry['updated'],
                'total': entry['total'],
            })
        else:
            entry.pop('payload', None)
    write_json_atomic(os.path.join(backup_dir, 'manifest.json'), manifest,
                      ensure_ascii=False, indent=2)
    return backup_dir, written


def _unknown_rows(unknown):
    return [{'venue': key, 'count': count}
            for key, count in sorted(unknown.items(), key=lambda kv: (-kv[1], kv[0]))]


def summarize(plan):
    """Return the JSON-serialisable report (without the file payloads)."""
    return {
        'generated_at': datetime.now().isoformat(),
        'files': [{k: v for k, v in entry.items() if k != 'payload'}
                  for entry in plan['files']],
        'pubs': plan['pubs'],
        'citations': plan['citations'],
        'unknown_pubs': _unknown_rows(plan['unknown_pubs']),
        'unknown_citations': _unknown_rows(plan['unknown_citations']),
    }


def print_summary(report, dry_run=True):
    mode = 'DRY-RUN (nothing written)' if dry_run else 'APPLY'
    print(f'venue_key normalization — {mode}')
    for entry in report['files']:
        if 'error' in entry:
            print(f"  !! {entry['path']}: {entry['error']}")
            continue
        print(f"  {entry['path']}: {entry['updated']}/{entry['total']} records updated")
    pubs, citations = report['pubs'], report['citations']
    print(f"  publications: {pubs['total']} scanned, {pubs['updated']} updated "
          f"(normalized {pubs['normalized']}, raw {pubs['raw']}, "
          f"no venue {pubs['no_venue']})")
    print(f"  citations   : {citations['total']} scanned, {citations['updated']} updated "
          f"(normalized {citations['normalized']}, raw {citations['raw']}, "
          f"no venue {citations['no_venue']})")
    for label, rows in (('publications', report['unknown_pubs']),
                        ('citations', report['unknown_citations'])):
        print(f"  unrecognised {label}: {len(rows)} distinct venue values")
        for row in rows[:REPORT_LIMIT]:
            print(f"      x{row['count']:<5} {row['venue'][:96]}")
        if len(rows) > REPORT_LIMIT:
            print(f"      … {len(rows) - REPORT_LIMIT} more (use --review-file to dump all)")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='Add/refresh the derived venue_key field in the crawler output files')
    parser.add_argument('--output-dir', default='output', metavar='DIR',
                        help='directory holding author_*_profile.json / '
                             'author_*_paper_citations.json (default: output)')
    parser.add_argument('--author', default=None, metavar='ID',
                        help='only that author id (default: every author found)')
    parser.add_argument('--apply', action='store_true',
                        help='write the files (default: dry-run, nothing is written)')
    parser.add_argument('--backup-dir', default=None, metavar='DIR',
                        help='where to copy the previous versions (default: '
                             'output/venue_key_backup_<stamp>)')
    parser.add_argument('--review-file', default=None, metavar='PATH',
                        help='write the full report (incl. every unrecognised venue value) '
                             'to PATH')
    parser.add_argument('--json', action='store_true',
                        help='print the report as JSON instead of the summary')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if not os.path.isdir(args.output_dir):
        print(f'Error: {args.output_dir} is not a directory')
        return 1
    plan = build_plan(args.output_dir, args.author)
    if not plan['files']:
        print(f'Error: no author_*_profile.json / author_*_paper_citations.json '
              f'found in {args.output_dir}')
        return 1
    report = summarize(plan)
    if args.review_file:
        write_json_atomic(args.review_file, report, ensure_ascii=False, indent=2)
        report['review_file'] = args.review_file
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_summary(report, dry_run=not args.apply)
    if args.apply:
        backup_dir, written = apply_plan(plan, args.backup_dir, args.output_dir)
        if backup_dir is None:
            print('  nothing to write (already up to date)')
        else:
            print(f'  wrote {written} file(s); previous versions in {backup_dir}')
    elif not args.json:
        print('  (dry-run: re-run with --apply to write)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
