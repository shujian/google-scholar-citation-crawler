"""
tests/test_venue_key.py — canonical ``<venue name><year>`` identity.

Hermetic and network-free.  It pins the canonical names so a drift away from
stage 3.a.2's vocabulary (`VENUE_PRESTIGE` + `VENUE_ALIASES`) becomes visible,
and it covers the two call sites that must write the field: ``PubInfo`` (the
author's own publications) and ``extract_citation_info`` (citing entries).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import patch  # noqa: E402,F401  (installs the stubs)

from code.venue_key import (  # noqa: E402
    SOURCE_NORMALIZED,
    SOURCE_RAW,
    VENUE_CANONICAL,
    canonical_venue,
    describe_venue_key,
    pick_venue_year,
    resolve_venue_key,
    venue_key,
)


class CanonicalNameTests(unittest.TestCase):
    def test_acronym_with_year(self):
        self.assertEqual(venue_key('ACL 2026'), 'ACL2026')
        self.assertEqual(venue_key('EMNLP 2025'), 'EMNLP2025')
        self.assertEqual(venue_key('NeurIPS', '2021'), 'NeurIPS2021')
        self.assertEqual(venue_key('ACL', '2024'), 'ACL2024')

    def test_spelled_out_forms_resolve(self):
        cases = {
            'Proceedings of the 62nd Annual Meeting of the Association for '
            'Computational Linguistics': 'ACL',
            'Proceedings of the 2024 Conference of the North American Chapter of the '
            'Association for Computational Linguistics': 'NAACL',
            'Proceedings of the 2025 Conference on Empirical Methods in Natural '
            'Language Processing': 'EMNLP',
            'Advances in Neural Information Processing Systems': 'NeurIPS',
            'Proceedings of the 30th ACM SIGKDD Conference on Knowledge Discovery '
            'and Data Mining': 'KDD',
        }
        for text, expected in cases.items():
            with self.subTest(text=text[:40]):
                self.assertEqual(canonical_venue(text), expected)

    def test_findings_is_its_own_venue(self):
        self.assertEqual(
            venue_key('Findings of the Association for Computational Linguistics: ACL 2026'),
            'Findings of ACL2026')
        self.assertNotEqual(
            venue_key('Findings of the Association for Computational Linguistics: ACL 2026'),
            venue_key('ACL 2026'))
        self.assertEqual(
            venue_key('Findings of the Association for Computational Linguistics: EMNLP 2024'),
            'Findings of EMNLP2024')

    def test_a_findings_volume_without_a_host_is_findings_of_acl(self):
        """There is no bare "Findings" venue (2026-10-15 user)."""
        self.assertNotIn('Findings', VENUE_CANONICAL)
        self.assertEqual(
            venue_key('Findings of the Association for Computational Linguistics 2023'),
            'Findings of ACL2023')
        # ... while an explicit host still wins over the bare phrase
        self.assertEqual(
            venue_key('Findings of the Association for Computational Linguistics: NAACL 2024'),
            'Findings of NAACL2024')

    def test_arxiv_is_a_venue_of_its_own(self):
        """A preprint's venue is the server, so the key is arXiv with no year."""
        for text in ('arXiv preprint arXiv:2402.18567, 2024',
                     'arXiv preprint arXiv:2402.18567',
                     'arXiv preprint arXiv …',
                     'arxiv.org/abs/2402.18567',
                     'URL https://arxiv. org/abs/2403.14541'):
            with self.subTest(text=text):
                self.assertEqual(resolve_venue_key(text, '2024'), ('arXiv', SOURCE_NORMALIZED))
        # a known venue mentioned alongside an arXiv id still wins
        self.assertEqual(venue_key('ACL 2024 (arXiv:2402.18567)'), 'ACL2024')

    def test_every_preprint_server_keys_on_its_own_name(self):
        cases = {
            'arXiv preprint arXiv:2402.18567, 2024': 'arXiv',
            'bioRxiv': 'bioRxiv',
            'bioRxiv preprint, 2023': 'bioRxiv',
            'medRxiv, 2020': 'medRxiv',
            'SSRN Electronic Journal, 2019': 'SSRN',
            'TechRxiv, 2022': 'TechRxiv',
            'Research Square, 2024': 'Research Square',
            'https://www.researchsquare.com/article/rs-123/v1': 'Research Square',
            'preprints.org, 2021': 'preprints.org',
        }
        for text, expected in cases.items():
            with self.subTest(text=text[:40]):
                self.assertEqual(resolve_venue_key(text, '2024'), (expected, SOURCE_NORMALIZED))

    def test_a_bare_preprint_names_no_server(self):
        self.assertEqual(resolve_venue_key('Preprint', '2022'), ('Preprint', SOURCE_RAW))

    def test_a_domain_label_is_not_a_venue_acronym(self):
        """A host is not a venue: www./kdd.org/iclr.cc must not resolve."""
        for text in ('https://kdd.org/kdd2020/', 'https://iclr.cc/virtual/2024',
                     'http://www.aclweb.org/anthology/'):
            with self.subTest(text=text):
                self.assertEqual(resolve_venue_key(text, '2020'), (text, SOURCE_RAW))
        # the real acronyms still work
        self.assertEqual(venue_key('WWW 2024'), 'WWW2024')
        self.assertEqual(venue_key('KDD 2020'), 'KDD2020')
        self.assertEqual(venue_key('ICLR 2024'), 'ICLR2024')

    def test_journal_is_not_swallowed_by_the_conference(self):
        self.assertEqual(
            venue_key('Transactions of the Association for Computational Linguistics 12, '
                      '576-592, 2024'),
            'TACL')

    def test_journals_have_no_year_in_their_key(self):
        """A journal is a continuous venue, not a yearly edition (2026-10-15)."""
        self.assertEqual(
            venue_key('IEEE/ACM Transactions on Audio, Speech, and Language Processing 28, '
                      '1707-1716, 2020'),
            'TASLP')
        self.assertEqual(
            venue_key('IEEE/ACM Transactions on Audio, Speech, and Language Processing 31, '
                      '1-10, 2023'),
            'TASLP')
        self.assertEqual(venue_key('Nature Machine Intelligence 5, 1-10, 2023'),
                         'Nature Machine Intelligence')
        self.assertEqual(venue_key('Neurocomputing 500, 1-10, 2022'), 'Neurocomputing')

    def test_extended_conference_table(self):
        self.assertEqual(
            venue_key('ICASSP 2025-2025 IEEE International Conference on Acoustics, '
                      'Speech and Signal Processing (ICASSP)'),
            'ICASSP2025')
        self.assertEqual(canonical_venue('Proceedings of the 2016 International Joint '
                                        'Conference on Neural Networks (IJCNN)'),
                         'IJCNN')
        self.assertEqual(canonical_venue('Pacific-Asia Conference on Knowledge Discovery '
                                         'and Data Mining, 245-255, 2013'),
                         'PAKDD')
        self.assertEqual(canonical_venue('Proceedings of the Eleventh International '
                                         'Conference on Language Resources and Evaluation '
                                         '(LREC 2018)'),
                         'LREC')
        self.assertEqual(canonical_venue('CCF International Conference on Natural Language '
                                         'Processing and Chinese Computing'),
                         'NLPCC')

    def test_machine_translation_venues_stay_apart(self):
        """CCMT / CWMT / WMT share words; each string must land on its own key."""
        self.assertEqual(venue_key('Machine Translation: 19th China Conference, CCMT 2023, '
                                   'Jinan, China, Proceedings', '2023'), 'CCMT2023')
        self.assertEqual(venue_key('Machine Translation: 14th China Workshop, CWMT 2018, '
                                   'Wuyishan, China, Proceedings', '2018'), 'CWMT2018')
        self.assertEqual(venue_key('Proceedings of the Seventh Conference on Machine '
                                   'Translation (WMT), 615-620, 2022'), 'WMT2022')

    def test_two_spellings_of_the_same_chinese_journal_share_a_key(self):
        self.assertEqual(venue_key('中文信息学报 31 (2), 179-186, 2017'), '中文信息学报')
        self.assertEqual(venue_key('Journal of Chinese Information Processing 26 (3), '
                                   '54-58, 2012'), '中文信息学报')
        self.assertEqual(venue_key('Journal of Chinese Computer Systems 36 (6), 1-5, 2015'),
                         '小型微型计算机系统')

    def test_longest_match_wins(self):
        # "Nature Machine Intelligence" must not be reported as the journal Nature,
        # and "Computational Linguistics" must not match the CL acronym inside it.
        self.assertEqual(canonical_venue('Nature Machine Intelligence 5, 1-10, 2023'),
                         'Nature Machine Intelligence')
        self.assertEqual(canonical_venue('Computational Linguistics 44 (1), 1-10, 2018'),
                         'CL')
        self.assertEqual(canonical_venue('SCIENCE CHINA Information Sciences, 2025'),
                         'SCIS')

    def test_spelling_variants_share_one_key(self):
        self.assertEqual(canonical_venue('NIPS 2019'), 'NeurIPS')
        self.assertEqual(canonical_venue('ACMMM 2023'), 'ACM MM')
        self.assertEqual(venue_key('NIPS 2019'), venue_key('NeurIPS 2019'))

    def test_case_insensitive_acronym_and_edition_number(self):
        self.assertEqual(venue_key('Ijcai 17 (2017), 3203-3209, 2017'), 'IJCAI2017')
        self.assertEqual(
            venue_key('Proceedings of the AAAI conference on artificial intelligence '
                      '34 (05), 9298-9305, 2020'),
            'AAAI2020')


class KeyShapeTests(unittest.TestCase):
    def test_unknown_venue_keeps_its_text(self):
        key, source = resolve_venue_key('Journal of Obscure Studies 1 (2), 3-4, 2019')
        self.assertEqual(key, 'Journal of Obscure Studies 1 (2), 3-4, 2019')
        self.assertEqual(source, SOURCE_RAW)
        self.assertEqual(resolve_venue_key('ACL 2024'), ('ACL2024', SOURCE_NORMALIZED))

    def test_missing_venue_has_no_key(self):
        for value in (None, '', 'NA', 'N/A', 'None', '  '):
            with self.subTest(value=value):
                self.assertEqual(resolve_venue_key(value, '2020'), (None, None))
                self.assertEqual(describe_venue_key(value), {})

    def test_whitespace_is_collapsed(self):
        self.assertEqual(venue_key('ACL   2024\n'), 'ACL2024')
        self.assertEqual(venue_key('Journal of Obscure\nStudies 1 (2)'),
                         'Journal of Obscure Studies 1 (2)')

    def test_year_prefers_the_venue_string_then_the_record(self):
        self.assertEqual(pick_venue_year('ACL 2026', '2025'), '2026')
        self.assertEqual(pick_venue_year('Some Place', '2025'), '2025')
        self.assertIsNone(pick_venue_year('Some Place', 'N/A'))
        self.assertIsNone(pick_venue_year('Some Place', '999'))

    def test_canonical_without_a_year_still_yields_a_key(self):
        self.assertEqual(venue_key('ACL', None), 'ACL')

    def test_every_canonical_name_is_unique_and_typed(self):
        for name, (full_name, kind, aliases) in VENUE_CANONICAL.items():
            self.assertIn(kind, ('conference', 'journal'), name)
            self.assertTrue(full_name, name)
            for alias in aliases:
                self.assertNotEqual(alias, '', name)


class WiringTests(unittest.TestCase):
    """The field must be written by both producers of the output JSON."""

    def test_pub_info_writes_venue_key(self):
        from code.pub_info import PubInfo
        pub = PubInfo.from_dict({'title': 'T', 'year': '2024', 'venue': 'ACL 2024'})
        out = pub.to_dict()
        self.assertEqual(out['venue_key'], 'ACL2024')
        self.assertEqual(out['venue_key_source'], SOURCE_NORMALIZED)

    def test_pub_info_prefers_the_untruncated_detail_page_venue(self):
        from code.pub_info import PubInfo
        pub = PubInfo.from_dict({
            'title': 'T', 'year': '2024',
            'venue': 'Proceedings of the 2024 Conference on Empirical Methods in '
                     'Natural Language …, 2024',
            'venue_full': 'Proceedings of the 2024 Conference on Empirical Methods in '
                          'Natural Language Processing',
        })
        self.assertEqual(pub.to_dict()['venue_key'], 'EMNLP2024')

    def test_pub_info_key_is_recomputed_not_read_back(self):
        """A stale stored key must not survive serialisation."""
        from code.pub_info import PubInfo
        pub = PubInfo.from_dict({'title': 'T', 'year': '2024', 'venue': 'ACL 2024',
                                 'venue_key': 'STALE', 'venue_key_source': 'normalized'})
        self.assertEqual(pub.to_dict()['venue_key'], 'ACL2024')

    def test_extract_citation_info_writes_venue_key(self):
        from code.citation_identity import extract_citation_info
        info = extract_citation_info({
            'bib': {'title': 'T', 'author': ['A'], 'venue': 'Findings of the '
                                                       'Association for Computational '
                                                       'Linguistics: ACL 2025',
                    'pub_year': '2025'},
            'pub_url': 'https://example.com',
        })
        self.assertEqual(info['venue_key'], 'Findings of ACL2025')
        self.assertEqual(info['venue_key_source'], SOURCE_NORMALIZED)
        # additive only: the identity fields are untouched
        self.assertEqual(info['venue'],
                         'Findings of the Association for Computational Linguistics: ACL 2025')

    def test_extract_citation_info_without_a_venue(self):
        from code.citation_identity import extract_citation_info
        info = extract_citation_info({'bib': {'title': 'T', 'author': ['A']}})
        self.assertIsNone(info['venue_key'])
        self.assertIsNone(info['venue_key_source'])


if __name__ == '__main__':
    unittest.main()
