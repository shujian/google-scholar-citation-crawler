"""逐页取 cite/BibTeX（用户 2026-10-15 定，逐页粒度）。

背景：Cite 对话框（`output=cite`）原先在整篇抓完后**集中**发一次；一次几十上百条连着的
detail 请求容易被 Scholar 看成异常流量。现在挂到"每页抓完"的回调上（direct 与 year 两种
模式都有 `on_page_complete`），请求序列变成「列表页 → 少数几条对话框 → 列表页 …」。

本文件钉住三件事：① 开关关掉时**不挂钩子**（行为与从前一致）；② 钩子只把**本页里还没取过、
且判据说值得取**的行送出去（不会重复发请求）；③ `citation_fetch` 的两个回调确实接了线。
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import FetcherTestCase, patch  # noqa: E402
from code import citation_fetch, scholar_citation  # noqa: E402

FULL_VENUE = 'Proceedings of the 2025 Conference on Empirical Methods in NLP'


class PageCiteHookTests(FetcherTestCase):

    def _fetcher(self, cite_formats=True):
        self.fetcher.cite_formats = cite_formats
        self.fetcher.output_dir = tempfile.mkdtemp()
        self.fetcher._install_page_cite_hook()
        return self.fetcher

    def test_no_hook_when_the_step_is_off(self):
        fetcher = self._fetcher(cite_formats=False)
        self.assertIsNone(getattr(fetcher, '_on_citations_page', None),
                          "关掉对话框时抓取路径必须一字不变")

    def test_only_rows_that_still_need_the_dialog_are_sent(self):
        fetcher = self._fetcher()
        self.assertTrue(callable(fetcher._on_citations_page))

        already = {'title': 'done', 'venue': FULL_VENUE, 'authors': 'H Wang', 'cid': 'A1',
                   'cite': {'bibtex': {'booktitle': FULL_VENUE}}}
        complete = {'title': 'complete', 'venue': FULL_VENUE,
                    'authors': 'Haoyang Wang, Feng Zhang', 'cid': 'A2'}
        truncated = {'title': 'needs it',
                     'venue': 'Proceedings of the 2025 Conference on Empirical …',
                     'authors': 'H Wang', 'cid': 'A3'}
        sent = []

        def _record(rows, **kwargs):
            sent.append(list(rows))
            for row in rows:                     # 模拟真实调用：取回后写进 citation['cite']
                row['cite'] = {'bibtex': {'booktitle': FULL_VENUE}}
            return {'resolved': len(rows), 'fetched': len(rows), 'cache_hits': 0,
                    'failed': 0, 'with_cid': len(rows), 'missing_cid': 0}

        with patch.object(scholar_citation._cite_format,
                          'enrich_citations_with_cite_formats', _record):
            fetcher._on_citations_page([already, complete, truncated])

        self.assertEqual(len(sent), 1, "只有一批要发")
        self.assertEqual([r['cid'] for r in sent[0]], ['A3'], "只发确实需要的那一行")

    def test_second_page_does_not_reask_the_same_row(self):
        fetcher = self._fetcher()
        calls = []
        with patch.object(scholar_citation._cite_format,
                          'enrich_citations_with_cite_formats',
                          lambda rows, **kw: calls.append(list(rows)) or
                          {'resolved': 0, 'fetched': 0, 'cache_hits': 0,
                           'failed': 0, 'with_cid': 0, 'missing_cid': 0}):
            row = {'title': 'x', 'venue': 'Proceedings of the 2025 Conference on Empirical …',
                   'authors': 'H Wang', 'cid': 'A3'}
            fetcher._on_citations_page([row])
            row['cite'] = {'bibtex': {'booktitle': FULL_VENUE}}   # 已取回
            fetcher._on_citations_page([row])
        self.assertEqual(len(calls), 1, "同一行第二页不该再发请求")

    def test_both_page_callbacks_are_wired(self):
        """源码级守卫：两种抓取模式的"本页抓完"都必须经过这个钩子。"""
        source = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), 'code', 'citation_fetch.py'),
            encoding='utf-8').read()
        calls = (source.count('_run_page_cite_hook(fetcher, batch)')
                 - source.count('def _run_page_cite_hook(fetcher, batch)'))
        self.assertEqual(calls, 2, "direct 与 year 两个 on_page_complete 都要接线")
        self.assertIn('def _run_page_cite_hook(fetcher, batch):', source)
