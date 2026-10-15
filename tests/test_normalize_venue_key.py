"""
tests/test_normalize_venue_key.py — the data-upgrade tool for ``venue_key``.

Hermetic: synthetic profile / citations files in a temporary directory, no
network.  What matters here is the tool's *contract*: dry-run writes nothing,
``--apply`` touches only the two derived fields, the previous version is backed
up with a manifest, and a second run is a no-op.
"""

import copy
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import patch, StringIO  # noqa: E402,F401  (installs the stubs)

from code.data_upgrade import normalize_venue_key as tool  # noqa: E402


def _profile_pub(title, venue, year, venue_full=None):
    return {
        'no': 1, 'title': title, 'year': year, 'venue': venue, 'authors': 'A',
        'num_citations': 3, 'url': 'https://example.com', 'citedby_url': '/scholar?cites=1',
        'citation_for_view': 'AUTHOR:tok', 'venue_full': venue_full or '',
        'venue_full_source': 'scholar_detail' if venue_full else '',
    }


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(__import__('shutil').rmtree, self.tmpdir, ignore_errors=True)
        self.output_dir = os.path.join(self.tmpdir, 'output')
        os.makedirs(self.output_dir)
        self.profile_path = os.path.join(self.output_dir, 'author_test_profile.json')
        self.citations_path = os.path.join(
            self.output_dir, 'author_test_paper_citations.json')
        with open(self.profile_path, 'w', encoding='utf-8') as handle:
            json.dump({'author_info': {'name': 'A'}, 'publications': [
                _profile_pub('Canonical', 'ACL 2024', '2024'),
                _profile_pub('Truncated', 'Proceedings of the 2024 Conference on '
                                          'Empirical Methods in Natural Language …, 2024',
                             '2024',
                             venue_full='Proceedings of the 2024 Conference on Empirical '
                                        'Methods in Natural Language Processing'),
                _profile_pub('Unknown', 'Journal of Obscure Studies 1 (2), 3-4, 2019', '2019'),
                _profile_pub('NoVenue', 'NA', '2018'),
            ]}, handle, ensure_ascii=False, indent=2)
        with open(self.citations_path, 'w', encoding='utf-8') as handle:
            json.dump({'papers': [{
                'pub': _profile_pub('Source', 'ACL 2024', '2024'),
                'citations': [
                    {'title': 'C1', 'authors': 'B', 'venue': 'Findings of the Association '
                                                            'for Computational Linguistics: '
                                                            'EMNLP 2024',
                     'year': '2024', 'url': 'u1', 'cites_id': 'cid1'},
                    {'title': 'C2', 'authors': 'C', 'venue': 'Proceedings of the …',
                     'year': '2023', 'url': 'u2', 'cites_id': 'cid2'},
                ],
                'fetch_complete': True,
            }]}, handle, ensure_ascii=False, indent=2)

    def _read(self, path):
        with open(path, 'r', encoding='utf-8') as handle:
            return json.load(handle)

    def _run(self, *argv):
        with patch('sys.stdout', new_callable=StringIO) as stdout:
            code = tool.main(['--output-dir', self.output_dir] + list(argv))
        return code, stdout.getvalue()

    def test_dry_run_changes_nothing(self):
        before_profile = open(self.profile_path, 'rb').read()
        before_citations = open(self.citations_path, 'rb').read()
        code, output = self._run()
        self.assertEqual(code, 0)
        self.assertIn('DRY-RUN', output)
        self.assertIn('publications: 4 scanned', output)
        self.assertEqual(open(self.profile_path, 'rb').read(), before_profile)
        self.assertEqual(open(self.citations_path, 'rb').read(), before_citations)
        self.assertEqual([name for name in os.listdir(self.output_dir)
                          if name.startswith('venue_key_backup')], [])
        self.assertNotIn('venue_key', self._read(self.profile_path)['publications'][0])

    def test_apply_writes_keys_and_touches_nothing_else(self):
        with open(self.profile_path, 'r', encoding='utf-8') as handle:
            before = json.load(handle)
        code, output = self._run('--apply')
        self.assertEqual(code, 0)
        payload = self._read(self.profile_path)
        pubs = payload['publications']
        self.assertEqual(pubs[0]['venue_key'], 'ACL2024')
        self.assertEqual(pubs[0]['venue_key_source'], 'normalized')
        self.assertEqual(pubs[1]['venue_key'], 'EMNLP2024')  # venue_full wins
        self.assertEqual(pubs[2]['venue_key'], 'Journal of Obscure Studies 1 (2), 3-4, 2019')
        self.assertEqual(pubs[2]['venue_key_source'], 'raw')
        self.assertIsNone(pubs[3]['venue_key'])
        self.assertIsNone(pubs[3]['venue_key_source'])
        # everything else is byte-identical
        for original, updated in zip(before['publications'], pubs):
            for key, value in original.items():
                self.assertEqual(updated[key], value, key)

        citations = self._read(self.citations_path)['papers'][0]['citations']
        self.assertEqual(citations[0]['venue_key'], 'Findings of EMNLP2024')
        # 截断的列表页串**不给 key** ✓（用户 2026-10-15：截断串不能当去重键；见 test_venue_key）
        self.assertIsNone(citations[1]['venue_key'])
        self.assertIsNone(citations[1]['venue_key_source'])

        source_pub = self._read(self.citations_path)['papers'][0]['pub']
        self.assertEqual(source_pub['venue_key'], 'ACL2024')

        backups = [name for name in os.listdir(self.output_dir)
                   if name.startswith('venue_key_backup')]
        self.assertEqual(len(backups), 1)
        backup_dir = os.path.join(self.output_dir, backups[0])
        manifest = self._read(os.path.join(backup_dir, 'manifest.json'))
        self.assertEqual(len(manifest['files']), 2)
        for entry in manifest['files']:
            self.assertTrue(os.path.exists(entry['backup']))
            self.assertTrue(entry['sha256_before'])
        # the backup is the *previous* content
        restored = self._read(os.path.join(backup_dir, 'author_test_profile.json'))
        self.assertNotIn('venue_key', restored['publications'][0])
        self.assertIn('wrote 2 file(s)', output)

    def test_second_apply_is_a_no_op(self):
        self._run('--apply')
        profile_after = open(self.profile_path, 'rb').read()
        code, output = self._run('--apply')
        self.assertEqual(code, 0)
        self.assertIn('0/4 records updated', output)
        self.assertEqual(open(self.profile_path, 'rb').read(), profile_after)

    def test_reapply_leaves_no_backup_directory_when_nothing_changes(self):
        self._run('--apply')
        before = sorted(os.listdir(self.output_dir))
        code, output = self._run('--apply')
        self.assertEqual(code, 0)
        self.assertIn('nothing to write', output)
        self.assertEqual(sorted(os.listdir(self.output_dir)), before)

    def test_review_file_lists_every_unrecognised_venue(self):
        review = os.path.join(self.tmpdir, 'review.json')
        code, _ = self._run('--review-file', review)
        self.assertEqual(code, 0)
        report = self._read(review)
        pubs = {row['venue']: row['count'] for row in report['unknown_pubs']}
        self.assertIn('Journal of Obscure Studies 1 (2), 3-4, 2019', pubs)
        self.assertNotIn('ACL 2024', pubs)
        citations = {row['venue']: row['count'] for row in report['unknown_citations']}
        # 截断的 venue 既不是 normalized 也不是 raw（它**不给 key**），所以不进"待审阅"清单 ✓
        self.assertNotIn('Proceedings of the …', citations)
        self.assertEqual(citations, {})
        self.assertEqual(report['citations']['total'], 3)  # pub + 2 citations
        self.assertEqual(report['citations']['normalized'], 2)  # pub + C1

    def test_missing_output_dir_is_an_error(self):
        code, output = self._run_for('/nonexistent/dir')
        self.assertEqual(code, 1)
        self.assertIn('not a directory', output)

    def test_empty_output_dir_is_an_error(self):
        empty = os.path.join(self.tmpdir, 'empty')
        os.makedirs(empty)
        code, output = self._run_for(empty)
        self.assertEqual(code, 1)
        self.assertIn('no author_', output)

    def _run_for(self, output_dir, *argv):
        with patch('sys.stdout', new_callable=StringIO) as stdout:
            code = tool.main(['--output-dir', output_dir] + list(argv))
        return code, stdout.getvalue()

    def test_author_filter_only_scans_that_author(self):
        with open(os.path.join(self.output_dir, 'author_other_profile.json'), 'w',
                  encoding='utf-8') as handle:
            json.dump({'publications': [_profile_pub('Other', 'ACL 2024', '2024')]}, handle)
        report = tool.summarize(tool.build_plan(self.output_dir, author='test'))
        self.assertEqual(len(report['files']), 2)
        report_all = tool.summarize(tool.build_plan(self.output_dir))
        self.assertEqual(len(report_all['files']), 3)

    def test_a_citation_with_a_cite_dialog_uses_the_dialog_venue(self):
        """The tool and the crawler share one resolver, so a re-run upgrades it."""
        with open(self.citations_path, 'r', encoding='utf-8') as handle:
            payload = json.load(handle)
        citations = payload['papers'][0]['citations']
        citations[0]['cite'] = {'bibtex': {
            'booktitle': 'Proceedings of the 62nd Annual Meeting of the '
                         'Association for Computational Linguistics'}}
        citations[0]['venue'] = 'Proceedings of the 2025 …'
        citations[0].pop('venue_key', None)
        citations[0].pop('venue_key_source', None)
        with open(self.citations_path, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        self._run('--apply')
        updated = self._read(self.citations_path)['papers'][0]['citations'][0]
        self.assertEqual(updated['venue_key'], 'ACL2024')  # venue year, not record year
        self.assertEqual(updated['venue_key_source'], 'normalized')
        self.assertEqual(updated['venue'], 'Proceedings of the 2025 …')  # never rewritten

    def test_other_fields_of_a_citation_are_untouched(self):
        with open(self.citations_path, 'r', encoding='utf-8') as handle:
            before = json.load(handle)['papers'][0]['citations']
        self._run('--apply')
        after = self._read(self.citations_path)['papers'][0]['citations']
        for original, updated in zip(copy.deepcopy(before), after):
            for key, value in original.items():
                self.assertEqual(updated[key], value, key)


if __name__ == '__main__':
    unittest.main()
