#!/usr/bin/env python3
"""修「标题自带引号」被 Chicago 解析截断的存量行（2026-10-20）。

**背景**：`cite_format.parse_format_parts()` 的标题边界原先是
`[“"]([^”"]+)[”"]`（**非贪婪**）。标题里本来就带引号时，它会在第一个内嵌引号处停下：
`title` 只剩前半截、剩下半截被当成 `venue`，再由 `apply_chicago_correction()`
当"权威修正"写回条目（`cite.corrected` 留审计）。实测 4 条：

* `On “human parity” and “super human performance” in …`（`On “human parity`）
* `“How to get consensus with neighbors?”: …`（`“How to get consensus with neighbors?`）
* `AI translation of literary texts is" fine", …`（`AI translation of literary texts is`）
* `Minimal-Action Discrete Schr\\" odinger …`（`Minimal-Action Discrete Schr\\`）

**为什么它会让阶段 2 报"缺引用"**：`citations_meta.json` 的主体按这批条目写，
而阶段 2 判"缺条目"用的是**归一化标题**（键会换代，标题被认为更稳）。标题被截断后，
输入里那条（完整标题）在 meta 里找不到同标题的行，于是被报成"缺 1 条、请重抓本篇"
—— 行其实在，只是标题不对。

**它做什么**（全程用 `output/cite_format_cache.json` 里的 **Chicago 原文**，不联网）：

* 条目里 `title` / `venue` **恰好等于旧规则算出的那个（错）值** 时，换成新规则的值 ——
  其它值一律不碰（可能来自后续世代的列表页/对话框，或者人工修正）；
* 同步刷新 `cite.venue_text` / `venue_source` 与派生的 `venue_key` / `venue_key_source`；
* 缓存条目里过期的 `venue_text` / `authors_full` 一并刷新（运行时**优先用缓存里那份**，
  不刷新的话下次跑又会把旧值写回去）。

**它不做什么**：不动 `url` / `cites_id` / `cid` / `authors` / `year` / 任何下载字段；
**不写 `citations_meta.json`** —— 那是 `citation_index.write_citations_meta()` 的唯一职责，
修完这一步再跑：

    python -m code.data_upgrade.align_citations_meta --apply

**默认 dry-run**；`--apply` 才写，写前把将被改动的文件复制进备份目录 + `manifest.json`，
原子写入、幂等（复跑 0 改动）。

用法（项目根）：

    python -m code.data_upgrade.repair_chicago_titles                  # 预览
    python -m code.data_upgrade.repair_chicago_titles --apply          # 写 + 备份 + manifest
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from code.cite_format import (  # noqa: E402
    format_extras, normalize_whitespace, parse_format_parts)
from code.common import write_json_atomic  # noqa: E402
from code.shared.venue_key import citation_venue_key  # noqa: E402

PRODUCT_GLOB = 'author_*_paper_citations.json'
CACHE_NAME = 'cite_format_cache.json'

#: **旧规则**（有 bug 的那一版）。只用来认出"这个值正是它写坏的"，不要拿它做新判断；
#: 两个年份正则也在这里逐字复刻，故意不 import 活解析器的那份（历史值不该跟着新代码漂移）。
_LEGACY_TITLE_RE = re.compile(r'[“"](?P<title>[^”"]+)[”"]')
_LEGACY_PAREN_YEAR_RE = re.compile(r'[\(\[]\s*((?:19|20)\d{2})\s*[\)\]]\s*[.,;]?\s*$')
_LEGACY_BARE_YEAR_RE = re.compile(r'(?<![\d])((?:19|20)\d{2})\s*[.,;]?\s*$')


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def legacy_parts(text):
    """旧规则算出的 `{authors,title,venue,year}`（`parse_format_parts()` 的逐字复刻，仅用于比对）。"""
    value = normalize_whitespace(text or '')
    match = _LEGACY_TITLE_RE.search(value)
    if not match:
        return {}
    title = match.group('title').strip().strip('.').strip()
    authors = value[:match.start()].strip().rstrip('.').strip()
    tail = value[match.end():].strip().lstrip('.').strip()
    year = ''
    year_match = _LEGACY_PAREN_YEAR_RE.search(tail)
    if year_match:
        year, tail = year_match.group(1), tail[:year_match.start()].strip()
    else:
        year_match = _LEGACY_BARE_YEAR_RE.search(tail)
        if year_match:
            year, tail = year_match.group(1), tail[:year_match.start()].strip()
    return {'authors': authors, 'title': title,
            'venue': tail.strip().rstrip('.').strip(), 'year': year}


def repair_citation(citation):
    """修一条引用（就地改）；返回改动字典，空 = 没动。"""
    cite = citation.get('cite')
    if not isinstance(cite, dict):
        return {}
    chicago = cite.get('chicago') or ''
    parts = parse_format_parts(chicago)
    if not parts:
        return {}
    old = legacy_parts(chicago)
    changed = {}
    for field in ('title', 'venue'):
        new = (parts.get(field) or '').strip()
        current = (citation.get(field) or '').strip()
        if not new or new == current:
            continue
        if field not in old or current != (old.get(field) or '') or old.get(field) == new:
            continue          # 不是旧规则写出来的值 → 不碰
        citation[field] = new
        changed[field] = {'from': current, 'to': new}
    if not changed:
        return {}
    venue_text = (parts.get('venue') or '').strip()
    if venue_text and cite.get('venue_text') != venue_text:
        changed['venue_text'] = {'from': cite.get('venue_text'), 'to': venue_text}
        cite['venue_text'] = venue_text
        cite['venue_source'] = 'chicago'
    key, source = citation_venue_key(citation)
    if citation.get('venue_key') != key or citation.get('venue_key_source') != source:
        changed['venue_key'] = {'from': citation.get('venue_key'), 'to': key}
        citation['venue_key'] = key
        citation['venue_key_source'] = source
    return changed


def repair_cache_entry(entry):
    """刷一条缓存条目里过期的派生字段（就地改）；返回改动字典。"""
    formats = entry.get('formats') if isinstance(entry, dict) else None
    if not isinstance(formats, dict):
        return {}
    extras = format_extras(formats)
    changed = {}
    for field in ('venue_text', 'venue_source', 'authors_full', 'authors_source'):
        new = extras.get(field)
        if new and entry.get(field) != new:
            changed[field] = {'from': entry.get(field), 'to': new}
            entry[field] = new
    return changed


def plan(output_dir, cache_path):
    """只读地算出全部改动（返回 `(rows, cache_changes, payloads, cache_payload)`）。"""
    rows, cache_changes, payloads = [], [], []
    for path in sorted(glob.glob(os.path.join(output_dir, PRODUCT_GLOB))):
        with open(path, 'r', encoding='utf-8') as handle:
            payload = json.load(handle)
        hits = 0
        for paper in payload.get('papers', []) or []:
            for citation in paper.get('citations', []) or []:
                if not isinstance(citation, dict):
                    continue
                changed = repair_citation(citation)
                if not changed:
                    continue
                hits += 1
                rows.append({'file': os.path.basename(path),
                             'paper_no': (paper.get('pub') or {}).get('no'),
                             'index': citation.get('index'),
                             'cites_id': citation.get('cites_id'),
                             'changed': changed})
        payloads.append({'path': path, 'payload': payload, 'hits': hits,
                         'sha256': _sha256(path)})
    cache_payload = None
    if cache_path and os.path.exists(cache_path):
        with open(cache_path, 'r', encoding='utf-8') as handle:
            cache_payload = json.load(handle)
        for cid, entry in (cache_payload or {}).items():
            changed = repair_cache_entry(entry)
            if changed:
                cache_changes.append({'cid': cid, 'changed': changed})
    return rows, cache_changes, payloads, cache_payload


def apply_changes(rows, cache_changes, payloads, cache_payload, cache_path,
                  backup_dir):
    """备份 + 写入（只写真正有改动的文件）；返回 manifest。"""
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_dir = backup_dir or os.path.join(
        os.path.dirname(payloads[0]['path']) if payloads else 'output',
        f'cite_title_repair_{stamp}')
    targets = [entry for entry in payloads if entry['hits']]
    if cache_changes and cache_payload is not None:
        targets.append({'path': cache_path, 'payload': cache_payload,
                        'hits': len(cache_changes), 'sha256': _sha256(cache_path),
                        'sort_keys': True})
    if not targets:
        return None
    os.makedirs(backup_dir, exist_ok=True)
    manifest = {'created_at': datetime.now().isoformat(), 'backup_dir': backup_dir,
                'rows': rows, 'cache': cache_changes, 'files': []}
    for entry in targets:
        backup_path = os.path.join(backup_dir, os.path.basename(entry['path']))
        shutil.copy2(entry['path'], backup_path)
        kwargs = {'ensure_ascii': False, 'indent': 2}
        if entry.get('sort_keys'):
            kwargs['sort_keys'] = True
        write_json_atomic(entry['path'], entry['payload'], **kwargs)
        manifest['files'].append({'path': entry['path'], 'backup': backup_path,
                                  'sha256_before': entry['sha256'],
                                  'entries': entry['hits']})
    write_json_atomic(os.path.join(backup_dir, 'manifest.json'), manifest,
                      ensure_ascii=False, indent=2)
    return manifest


def print_report(rows, cache_changes, payloads, dry_run=True):
    mode = 'DRY-RUN (nothing written)' if dry_run else 'APPLY'
    print(f'chicago title repair — {mode}')
    for entry in payloads:
        print(f"  {os.path.basename(entry['path'])}: {entry['hits']} row(s) to repair")
    for row in rows:
        print(f"    paper {row['paper_no']} · index {row['index']} · "
              f"cites_id {row['cites_id']}")
        for field, change in row['changed'].items():
            print(f"      {field}: {str(change.get('from'))[:70]!r}"
                  f" -> {str(change.get('to'))[:70]!r}")
    print(f"  cite cache: {len(cache_changes)} entry(ies) to refresh")
    for row in cache_changes[:10]:
        print(f"    {row['cid']}: {', '.join(row['changed'])}")
    if len(cache_changes) > 10:
        print(f"    … {len(cache_changes) - 10} more")
    total = len(rows)
    print(f'  total: {total} citation row(s), {len(cache_changes)} cache entr(ies)')
    if dry_run and (total or cache_changes):
        print('  (dry-run: re-run with --apply to write; then run '
              '`python -m code.data_upgrade.align_citations_meta --apply` '
              'so citations_meta.json follows)')


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='Repair rows whose title/venue were truncated by the old '
                    'Chicago quote-boundary rule')
    parser.add_argument('--output-dir', default='output', metavar='DIR',
                        help='directory holding author_*_paper_citations.json '
                             '(default: output)')
    parser.add_argument('--cache', default=None, metavar='PATH',
                        help='Cite-dialog cache (default: <output-dir>/'
                             'cite_format_cache.json)')
    parser.add_argument('--apply', action='store_true',
                        help='write the files (default: dry-run, nothing is written)')
    parser.add_argument('--backup-dir', default=None, metavar='DIR',
                        help='where to copy the previous versions (default: '
                             'output/cite_title_repair_<stamp>)')
    parser.add_argument('--json', action='store_true',
                        help='print the plan as JSON instead of the summary')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if not os.path.isdir(args.output_dir):
        print(f'Error: {args.output_dir} is not a directory')
        return 1
    cache_path = args.cache or os.path.join(args.output_dir, CACHE_NAME)
    rows, cache_changes, payloads, cache_payload = plan(args.output_dir, cache_path)
    if not payloads:
        print(f'Error: no {PRODUCT_GLOB} found in {args.output_dir}')
        return 1
    if not os.path.exists(cache_path):
        print(f'Warning: no Cache file at {cache_path} — cache extras are left '
              f'untouched (the citation rows are still repaired)')
    if args.json:
        print(json.dumps({'dry_run': not args.apply, 'rows': rows,
                          'cache': cache_changes,
                          'files': [{k: v for k, v in entry.items()
                                     if k != 'payload'} for entry in payloads]},
                         ensure_ascii=False, indent=2))
    else:
        print_report(rows, cache_changes, payloads, dry_run=not args.apply)
    if args.apply:
        manifest = apply_changes(rows, cache_changes, payloads, cache_payload,
                                 cache_path, args.backup_dir)
        if manifest is None:
            print('  nothing to write (already up to date)')
        else:
            print(f"  wrote {len(manifest['files'])} file(s); previous versions "
                  f"in {manifest['backup_dir']}")
            print('  next: python -m code.data_upgrade.align_citations_meta --apply')
    return 0


if __name__ == '__main__':
    sys.exit(main())
