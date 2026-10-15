"""从对话框的**带引号格式**里取 venue 与作者全名（用户 2026-10-15 定）。

弃用 BibTeX 之后，这两样东西改从 Chicago（缺则 MLA）里解析：

    Yi, Sibo, … , and Qi Li. "Jailbreak …: A survey." arXiv preprint arXiv:2407.04295 (2024).
    └──── 作者（引号之前）────┘ └── 标题 ──┘ └──────── venue（收尾引号之后）───────┘

**没有引号的格式不猜**（APA 用斜体、Vancouver 不用引号）：返回空，调用方保持旧行为 ——
这条边界就是当初"五种格式故意不解析"的教训（避免把标题当 venue）。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from code.cite_format import format_extras, parse_format_parts  # noqa: E402
from code.shared.venue_key import citation_venue_key  # noqa: E402

EXAMPLE = ('Yi, Sibo, Yule Liu, Zhen Sun, Tianshuo Cong, Xinlei He, Jiaxing Song, '
           'Ke Xu, and Qi Li. "Jailbreak attacks and defenses against large language '
           'models: A survey." arXiv preprint arXiv:2407.04295 (2024).')


class ParseFormatPartsTests(unittest.TestCase):

    def test_the_users_example_splits_into_three_parts(self):
        parts = parse_format_parts(EXAMPLE)
        self.assertEqual(parts['authors'],
                         'Yi, Sibo, Yule Liu, Zhen Sun, Tianshuo Cong, Xinlei He, '
                         'Jiaxing Song, Ke Xu, and Qi Li')
        self.assertEqual(parts['title'],
                         'Jailbreak attacks and defenses against large language '
                         'models: A survey')
        self.assertEqual(parts['venue'], 'arXiv preprint arXiv:2407.04295')
        self.assertEqual(parts['year'], '2024')

    def test_conference_venue_keeps_volume_and_pages(self):
        parts = parse_format_parts(
            'Wang, Hao, and Feng Zhang. "A paper." '
            'Proceedings of ACL 2024, pp. 1–10 (2024).')
        self.assertEqual(parts['venue'], 'Proceedings of ACL 2024, pp. 1–10')
        self.assertEqual(parts['year'], '2024')

    def test_bare_trailing_year_is_recognised(self):
        parts = parse_format_parts('Wang, Hao. "A paper." Journal of Testing 2023.')
        self.assertEqual(parts['venue'], 'Journal of Testing')
        self.assertEqual(parts['year'], '2023')

    def test_formats_without_quotes_are_not_parsed(self):
        """APA / Vancouver 没有引号 → 宁可什么都不给，也不猜标题当 venue。"""
        self.assertEqual(parse_format_parts('Team, I. (2023). A title. arXiv.'), {})
        self.assertEqual(parse_format_parts('Team I. A title [Internet]. 2023 Sep 27'), {})
        self.assertEqual(parse_format_parts(''), {})

    def test_chicago_wins_and_mla_is_the_fallback(self):
        chicago = {'chicago': EXAMPLE, 'mla': 'Yi, Sibo. "Something else." Other 2020.'}
        extras = format_extras(chicago)
        self.assertEqual(extras['venue_source'], 'chicago')
        self.assertEqual(extras['authors_source'], 'chicago')
        self.assertIn('Yule Liu', extras['authors_full'])
        # Chicago 缺失/无引号时才用 MLA
        mla_only = format_extras({'mla': 'Yi, Sibo. "A survey." arXiv 2024.'})
        self.assertEqual(mla_only['venue_source'], 'mla')
        self.assertEqual(mla_only['venue_text'], 'arXiv')
        self.assertEqual(format_extras({}), {})
        self.assertEqual(format_extras({'chicago': 'no quotes here'}), {})


class VenueKeyPrecedenceTests(unittest.TestCase):
    """优先级：存量 BibTeX 字段 → `cite.venue_text` → 列表页（截断的）venue。"""

    TRUNCATED = 'Proceedings of the 2025 Conference on Empirical …'

    def test_parsed_venue_beats_the_truncated_list_page(self):
        key, source = citation_venue_key({
            'venue': self.TRUNCATED, 'year': '2025',
            'cite': {'venue_text': 'Proceedings of EMNLP 2025'}})
        self.assertEqual((key, source), ('EMNLP2025', 'normalized'))

    def test_legacy_bibtex_still_wins(self):
        key, source = citation_venue_key({
            'venue': self.TRUNCATED, 'year': '2024',
            'cite': {'venue_text': 'Proceedings of ACL 2024',
                     'bibtex': {'booktitle': 'Proceedings of EMNLP 2024'}}})
        self.assertEqual((key, source), ('EMNLP2024', 'normalized'))

    def test_without_cite_nothing_changes(self):
        key, _source = citation_venue_key({'venue': 'Proceedings of ACL 2024',
                                           'year': '2024'})
        self.assertEqual(key, 'ACL2024')


if __name__ == '__main__':
    unittest.main()
