"""Chicago 标题截断的存量修复（`code/data_upgrade/repair_chicago_titles.py`）。

被测的三件事：
1. 只修"当前值恰好等于截断版本"的行 —— 别的值一律不碰；
2. 顺带刷新 `cite.venue_text` 与派生的 `venue_key`，但**不动** `url` / `cites_id` / `cid`；
3. 默认 dry-run 什么都不写；`--apply` 才写、写前备份 + manifest、复跑幂等。
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from code.data_upgrade import repair_chicago_titles as RT  # noqa: E402

CHICAGO = ('Poibeau, Thierry. "On “human parity” and “super human performance” in '
           'machine translation evaluation." In Proceedings of the Thirteenth '
           'Language Resources and Evaluation Conference, pp. 6018-6023. 2022.')
MANGLED_TITLE = 'On “human parity'
MANGLED_VENUE = ('and “super human performance” in machine translation evaluation." '
                 'In Proceedings of the Thirteenth Language Resources and '
                 'Evaluation Conference, pp. 6018-6023')
FULL_TITLE = ('On “human parity” and “super human performance” in machine '
              'translation evaluation')
FULL_VENUE = ('In Proceedings of the Thirteenth Language Resources and Evaluation '
              'Conference, pp. 6018-6023')


def _mangled(**over):
    row = {'title': MANGLED_TITLE, 'authors': 'Poibeau, Thierry', 'venue': MANGLED_VENUE,
           'year': '2022', 'url': 'https://aclanthology.org/2022.lrec-1.647/',
           'cites_id': '15207774320991617001', 'index': 17, 'cid': '6T8DsyveDNMJ',
           'cite': {'chicago': CHICAGO, 'venue_text': MANGLED_VENUE,
                    'venue_source': 'chicago'},
           'venue_key': 'LREC2022', 'venue_key_source': 'normalized'}
    row.update(over)
    return row


def _untouched():
    """标题/venue 不是旧规则写出来的 → 一个字都不该改。"""
    return {'title': 'A title that came from the list page',
            'authors': 'X Y', 'venue': 'Proceedings of ACL 2024', 'year': '2024',
            'url': 'https://example.org/x', 'cites_id': 'c9', 'index': 18,
            'cid': 'cOther', 'cite': {'chicago': CHICAGO,
                                      'venue_text': 'In Proceedings of ACL 2024',
                                      'venue_source': 'chicago'},
            'venue_key': 'ACL2024', 'venue_key_source': 'normalized'}


class RepairPlanTests(unittest.TestCase):

    def test_the_mangled_row_is_recognised(self):
        row = _mangled()
        changed = RT.repair_citation(row)
        self.assertEqual(row['title'], FULL_TITLE)
        self.assertEqual(row['venue'], FULL_VENUE)
        self.assertEqual(row['cite']['venue_text'], FULL_VENUE)
        self.assertEqual(changed['title']['from'], MANGLED_TITLE)
        self.assertEqual(row['url'], 'https://aclanthology.org/2022.lrec-1.647/')
        self.assertEqual(row['cites_id'], '15207774320991617001')
        self.assertEqual(row['cid'], '6T8DsyveDNMJ')

    def test_a_value_that_is_not_the_old_rule_output_is_left_alone(self):
        row = _untouched()
        before = json.loads(json.dumps(row))
        self.assertEqual(RT.repair_citation(row), {})
        self.assertEqual(row, before)

    def test_an_already_repaired_row_is_a_no_op(self):
        row = _mangled(title=FULL_TITLE, venue=FULL_VENUE)
        row['cite']['venue_text'] = FULL_VENUE
        self.assertEqual(RT.repair_citation(row), {})

    def test_venue_key_follows_the_repaired_venue(self):
        row = _mangled(venue_key='raw:stale', venue_key_source='raw')
        RT.repair_citation(row)
        self.assertEqual((row['venue_key'], row['venue_key_source']),
                         ('LREC2022', 'normalized'))

    def test_cache_entry_extras_are_refreshed(self):
        entry = {'formats': {'chicago': CHICAGO}, 'venue_text': MANGLED_VENUE,
                 'venue_source': 'chicago', 'authors_full': 'Poibeau, Thierry',
                 'authors_source': 'chicago'}
        changed = RT.repair_cache_entry(entry)
        self.assertEqual(entry['venue_text'], FULL_VENUE)
        self.assertEqual(list(changed), ['venue_text'])
        self.assertEqual(RT.repair_cache_entry(entry), {})


class RepairApplyTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='cite-title-repair-')
        self.out = os.path.join(self.tmp, 'output')
        os.makedirs(self.out)
        self.product = os.path.join(self.out, 'author_TEST_paper_citations.json')
        self.cache = os.path.join(self.out, 'cite_format_cache.json')
        self._write(self.product, {'author_id': 'TEST', 'papers': [
            {'pub': {'no': 2, 'title': 'Findings of the 2017 conference'},
             'citations': [_mangled(), _untouched()]}]})
        self._write(self.cache, {'6T8DsyveDNMJ': {
            'formats': {'chicago': CHICAGO}, 'venue_text': MANGLED_VENUE,
            'venue_source': 'chicago', 'authors_full': 'Poibeau, Thierry',
            'authors_source': 'chicago'}})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _write(path, payload):
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    @staticmethod
    def _read(path):
        with open(path, 'r', encoding='utf-8') as handle:
            return json.load(handle)

    def _sha(self, path):
        with open(path, 'rb') as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    def test_dry_run_writes_nothing(self):
        before = (self._sha(self.product), self._sha(self.cache))
        self.assertEqual(RT.main(['--output-dir', self.out]), 0)
        self.assertEqual((self._sha(self.product), self._sha(self.cache)), before)
        self.assertEqual(len([n for n in os.listdir(self.out)
                              if n.startswith('cite_title_repair_')]), 0)

    def test_apply_repairs_both_files_and_writes_a_manifest(self):
        backup = os.path.join(self.tmp, 'backup')
        self.assertEqual(RT.main(['--output-dir', self.out, '--apply',
                                  '--backup-dir', backup]), 0)
        rows = self._read(self.product)['papers'][0]['citations']
        self.assertEqual(rows[0]['title'], FULL_TITLE)
        self.assertEqual(rows[0]['venue'], FULL_VENUE)
        self.assertEqual(rows[1], _untouched())
        self.assertEqual(self._read(self.cache)['6T8DsyveDNMJ']['venue_text'],
                         FULL_VENUE)
        manifest = self._read(os.path.join(backup, 'manifest.json'))
        self.assertEqual(len(manifest['files']), 2)
        for entry in manifest['files']:
            self.assertTrue(os.path.isfile(entry['backup']))
            self.assertEqual(len(entry['sha256_before']), 64)
        self.assertEqual(len(manifest['rows']), 1)

    def test_second_run_is_idempotent(self):
        backup = os.path.join(self.tmp, 'backup')
        RT.main(['--output-dir', self.out, '--apply', '--backup-dir', backup])
        after_first = (self._sha(self.product), self._sha(self.cache))
        self.assertEqual(RT.main(['--output-dir', self.out, '--apply',
                                  '--backup-dir', backup]), 0)
        self.assertEqual((self._sha(self.product), self._sha(self.cache)),
                         after_first)

    def test_rerun_without_apply_reports_nothing_to_do(self):
        RT.main(['--output-dir', self.out, '--apply'])
        rows, cache_changes, payloads, _cache = RT.plan(self.out, self.cache)
        self.assertEqual((rows, cache_changes), ([], []))
        self.assertEqual(sum(entry['hits'] for entry in payloads), 0)

    def test_missing_cache_only_warns_and_still_repairs_the_rows(self):
        os.remove(self.cache)
        self.assertEqual(RT.main(['--output-dir', self.out, '--apply',
                                  '--backup-dir',
                                  os.path.join(self.tmp, 'backup')]), 0)
        rows = self._read(self.product)['papers'][0]['citations']
        self.assertEqual(rows[0]['title'], FULL_TITLE)


if __name__ == '__main__':
    unittest.main()
