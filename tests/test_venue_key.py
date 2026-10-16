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

from code.shared.venue_key import (  # noqa: E402
    SOURCE_NORMALIZED,
    SOURCE_RAW,
    citation_venue_key,
    cite_dialog_venue,
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


class CiteDialogVenueTests(unittest.TestCase):
    """A citing entry prefers the Cite dialog's BibTeX venue over the list page."""

    def test_bibtex_journal_wins_over_the_list_page_venue(self):
        citation = {'venue': 'Proceedings of the 2025 …', 'year': '2025',
                    'cite': {'bibtex': {'journal': 'Cybersecurity', 'volume': '8'}}}
        self.assertEqual(citation_venue_key(citation), ('Cybersecurity', SOURCE_RAW))

    def test_a_known_conference_in_the_bibtex_booktitle_is_normalized(self):
        citation = {
            'venue': 'Proceedings of the 2025 …', 'year': '2025',
            'cite': {'bibtex': {
                'booktitle': 'Proceedings of the 62nd Annual Meeting of the '
                             'Association for Computational Linguistics'}},
        }
        self.assertEqual(citation_venue_key(citation), ('ACL2025', SOURCE_NORMALIZED))

    def test_field_precedence_and_the_field_name(self):
        citation = {'year': '2024', 'cite': {'bibtex': {
            'journal': '', 'booktitle': 'Proceedings of the 2024 Conference on '
                                        'Empirical Methods in Natural Language Processing',
            'series': 'Lecture Notes in Computer Science'}}}
        self.assertEqual(cite_dialog_venue(citation),
                         ('Proceedings of the 2024 Conference on Empirical Methods '
                          'in Natural Language Processing', 'booktitle'))
        self.assertEqual(citation_venue_key(citation), ('EMNLP2024', SOURCE_NORMALIZED))

    def test_placeholders_and_unrelated_fields_are_ignored(self):
        for bibtex in ({'journal': 'N/A', 'booktitle': '  ', 'publisher': 'Springer'},
                       {'title': 'Some paper', 'publisher': 'Springer'},
                       {}):
            with self.subTest(bibtex=bibtex):
                citation = {'venue': 'ACL 2024', 'year': '2024',
                            'cite': {'bibtex': bibtex}}
                # no usable venue field -> the list-page venue is used as before
                self.assertEqual(citation_venue_key(citation), ('ACL2024', SOURCE_NORMALIZED))

    def test_the_formatted_strings_are_deliberately_not_parsed(self):
        """Only BibTeX names the venue unambiguously; the rest embed the title."""
        citation = {'venue': 'Proceedings of the 2025 …', 'year': '2025',
                    'cite': {'mla': 'Zhang, Jie, et al. "T." Cybersecurity 8.1 (2025): 55.',
                             'apa': 'Zhang, J. (2025). T. Cybersecurity, 8(1), 55.',
                             'chicago': 'Zhang, Jie. "T." Cybersecurity 8 (2025): 55.',
                             'vancouver': 'Zhang J. T. Cybersecurity. 2025;8(1):55.'}}
        self.assertEqual(cite_dialog_venue(citation), (None, None))
        # 列表页那个串被 Scholar 截断 → **不给 key** ✓（用户 2026-10-15：截断串不能当去重键）
        self.assertEqual(citation_venue_key(citation), (None, None))

    def test_a_truncated_venue_only_resolves_through_the_explicit_table(self):
        """① 截断一律不给 key，③ 只有显式登记的"唯一可判定前缀"才还原（用户 2026-10-15）。"""
        # 同一个截断前缀在现实里指向多个发表地 → 没有 key（这是 564 条 `Proceedings of the …` 的处境）
        for ambiguous in ('Proceedings of the …', 'Findings of the …',
                          'IEEE Transactions on …', 'International Conference on …'):
            with self.subTest(venue=ambiguous):
                self.assertEqual(resolve_venue_key(ambiguous, '2024'), (None, None))
                self.assertEqual(citation_venue_key({'venue': ambiguous, 'year': '2024'}),
                                 (None, None))
        # 显式登记的前缀 → 规范化 key（会议带年份、期刊不带）
        self.assertEqual(resolve_venue_key('Advances in Neural …', '2024'),
                         ('NeurIPS2024', SOURCE_NORMALIZED))
        self.assertEqual(resolve_venue_key('Proceedings of the AAAI …', '2023'),
                         ('AAAI2023', SOURCE_NORMALIZED))
        self.assertEqual(resolve_venue_key('Expert Systems with …', '2024'),
                         ('Expert Systems with Applications', SOURCE_NORMALIZED))
        # 截断的**预印本**仍按服务器名给 key（`arXiv preprint arXiv …` 787 条，规则未变 ✓）
        self.assertEqual(resolve_venue_key('arXiv preprint arXiv …', '2024'),
                         ('arXiv', SOURCE_NORMALIZED))
        # 未截断的未知串仍按原串给 raw ✓（行为一字未变）
        self.assertEqual(resolve_venue_key('Journal of Obscure Studies 1 (2), 2019', '2019'),
                         ('Journal of Obscure Studies 1 (2), 2019', SOURCE_RAW))

    def test_refresh_venue_keys_recomputes_cached_rows(self):
        """序列化前统一重算派生键（`refresh_venue_keys`，2026-10-15）。

        缓存恢复的行不带这两个字段，照抄会让上一轮从 Cite 对话框拿到的键在一次重抓后
        静默消失（实测 meta 侧被抹掉 7,901 条）。
        """
        from code.citation_identity import refresh_venue_keys
        rows = [
            {'venue': 'Proceedings of the 2024 Conference on Empirical Methods in '
                      'Natural Language Processing', 'year': '2024'},     # 缓存行：没有 key
            {'venue': 'Journal of Obscure Studies 1, 2019', 'year': '2019'},  # 未知 → raw
            {'venue': 'Proceedings of the …', 'year': '2024',                 # 截断 → 清掉旧键
             'venue_key': 'Proceedings of the …', 'venue_key_source': 'raw'},
            {'venue': 'Advances in Neural …', 'year': '2024'},                # ③ 显式前缀
            {'venue': 'NA', 'year': '2024', 'venue_key': 'STALE',             # 占位 → 清掉
             'venue_key_source': 'normalized'},
        ]
        refresh_venue_keys(rows)
        self.assertEqual(rows[0]['venue_key'], 'EMNLP2024')
        self.assertEqual(rows[0]['venue_key_source'], 'normalized')
        self.assertEqual(rows[1]['venue_key'], 'Journal of Obscure Studies 1, 2019')
        self.assertEqual(rows[1]['venue_key_source'], 'raw')
        self.assertNotIn('venue_key', rows[2])
        self.assertNotIn('venue_key_source', rows[2])
        self.assertEqual(rows[3]['venue_key'], 'NeurIPS2024')
        self.assertNotIn('venue_key', rows[4])
        # 幂等：再跑一遍结果不变
        snapshot = [dict(row) for row in rows]
        refresh_venue_keys(rows)
        self.assertEqual(rows, snapshot)
        # `None` / 非 dict 的输入不炸
        self.assertEqual(refresh_venue_keys(None), None)
        refresh_venue_keys(['not a dict'])

    def test_the_explicit_truncation_table_only_names_real_venues(self):
        from code.shared.venue_key import (TRUNCATED_VENUE_PREFIXES, VENUE_CANONICAL,
                                           is_truncated_venue)
        self.assertTrue(TRUNCATED_VENUE_PREFIXES, '③ 的显式表不能是空的')
        for prefix, key in TRUNCATED_VENUE_PREFIXES:
            with self.subTest(prefix=prefix):
                self.assertIn(key, VENUE_CANONICAL, '登记的键必须真实存在')
                # 前缀本身**不含**省略号（它是要去匹配截断串的开头的）
                self.assertFalse(is_truncated_venue(prefix))
                # 且必须真的能落在某条表项的开头（否则它永远匹配不到）
                self.assertTrue(any(text.lower().startswith(prefix.lower())
                                    for text in [key] + list(VENUE_CANONICAL[key][2])
                                    + [VENUE_CANONICAL[key][0]]))

    def test_without_a_cite_field_nothing_changes(self):
        citation = {'venue': 'ACL 2024', 'year': '2024'}
        self.assertEqual(citation_venue_key(citation), ('ACL2024', SOURCE_NORMALIZED))
        self.assertEqual(citation_venue_key({'venue': 'NA', 'year': '2024'}), (None, None))
        self.assertEqual(citation_venue_key(None), (None, None))


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



class AmpersandFoldingTest(unittest.TestCase):
    """`&` 与 `and` 同义（2026-10-16）。

    真库里 `IEEE Transactions on Knowledge & Data Engineering 35, no. 10 (2023): 1`
    本该命中 `TKDE`，却因为 `&` 落到 `raw` ✗ —— 只在**匹配**时折叠，原串一字不改 ✓。
    """

    def test_the_ampersand_form_resolves_to_the_same_key(self):
        from code.shared.venue_key import citation_venue_key
        self.assertEqual(
            citation_venue_key({"venue": "IEEE Transactions on Knowledge & Data "
                                         "Engineering 35, no. 10 (2023): 1"}),
            ("TKDE", "normalized"))
        self.assertEqual(citation_venue_key({"venue": "IEEE Transactions on Knowledge "
                                                       "and Data Engineering"}),
                         ("TKDE", "normalized"))

    def test_matching_is_symmetric_for_the_two_spellings(self):
        from code.shared.venue_key import venue_match
        self.assertTrue(venue_match("A and B Conference", "A & B Conference 2024"))
        self.assertTrue(venue_match("A & B Conference", "A and B Conference 2024"))

    def test_the_stored_text_is_never_rewritten(self):
        from code.shared.venue_key import venue_key
        # 认不出的含 `&` 串仍是它自己的原样（raw）—— 折叠只影响匹配 ✓
        self.assertEqual(venue_key("Foo & Bar Workshop 2024"), "Foo & Bar Workshop 2024")
        # 认得出的也不改变键的形状（只有词表里的规范名）✓
        self.assertEqual(venue_key("IEEE Transactions on Knowledge & Data Engineering"),
                         "TKDE")

if __name__ == '__main__':
    unittest.main()
