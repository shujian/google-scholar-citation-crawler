"""
tests/test_venue_quality.py — "already complete, do not fetch it again".

Hermetic: every page is an inline fixture, ``get_soup`` / ``get_page`` are fake
callables that fail loudly if they are reached, and cache paths live in a
temporary directory.  No network is touched.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import patch, StringIO  # noqa: E402  (installs the stubs)

from code import cite_format as cf  # noqa: E402
from code import venue_detail as vd  # noqa: E402
from code import venue_quality as vq  # noqa: E402


FULL_VENUE = 'Cybersecurity'
FULL_CONFERENCE = ('Proceedings of the 2024 Conference of the North American Chapter '
                   'of the Association for Computational Linguistics')
TRUNCATED = 'Proceedings of the 2024 Conference of the North American Chapter of the …, 2024'
TOKEN = 'HF3-E9kAAAAJ:xtoqd-5pKcoC'
CID = 'nYGTsUfPxnMJ'
BIB_URL = ('https://scholar.googleusercontent.com/scholar.bib?q=info:' + CID +
           ':scholar.google.com/&output=citation&scisig=abc&scisf=4')


def detail_page():
    return ('<html><body><div class="gsc_oci_table">'
            '<div class="gs_scl"><div class="gsc_oci_field">Conference</div>'
            f'<div class="gsc_oci_value">{FULL_CONFERENCE}</div></div>'
            '</div></body></html>')


def cite_page():
    return ('<html><body><div id="gs_citt"><table>'
            '<tr><th scope="row" class="gs_cith">Chicago</th><td>'
            '<div class="gs_citr">Zhang, Jie, and Dan Meng. "A paper." '
            'Cybersecurity (2025): 55.</div></td></tr></table></div>'
            f'<div id="gs_citi"><a class="gs_citi" href="{BIB_URL}">BibTeX</a></div>'
            '</body></html>')


def pub(venue=TRUNCATED, title='A paper'):
    return {
        'no': 1, 'title': title, 'year': '2024', 'venue': venue,
        'authors': 'P Ding, J Kuang', 'num_citations': 4,
        'url': ('https://scholar.google.com/citations?view_op=view_citation&hl=en&'
                f'citation_for_view={TOKEN}'),
        'citedby_url': '/scholar?cites=1',
        'citation_for_view': TOKEN, 'venue_full': '', 'venue_full_source': '',
    }


def citation(venue=TRUNCATED, authors='H Wang, F Zhang', cid=CID):
    return {
        'title': 'A paper', 'authors': authors, 'venue': venue, 'year': '2025',
        'url': 'https://example.org/paper', 'cites_id': '1234', 'cid': cid,
    }


def never_called(kind):
    def caller(*args, **kwargs):
        raise AssertionError(f'{kind} should not have been requested')
    return caller


def quiet(*args, **kwargs):
    return None


# ---------------------------------------------------------------------------
# 1. The predicates
# ---------------------------------------------------------------------------

class PreprintVenueTests(unittest.TestCase):

    def test_the_listed_temporary_sources_are_recognised(self):
        for venue in (
            'arXiv preprint arXiv:2401.00001',
            'arXiv:2401.00001',
            'arxiv.org/abs/2401.00001',
            'arXiv',
            'Preprint',
            'preprint',
            'bioRxiv',
            'medRxiv',
            'SSRN Electronic Journal',
            'TechRxiv',
            'Research Square',
            'researchsquare.com',
            '  ARXIV   PREPRINT  ',
        ):
            with self.subTest(venue=venue):
                self.assertTrue(vq.is_preprint_venue(venue))

    def test_missing_values_are_treated_as_temporary(self):
        for venue in ('', '   ', None, 'NA', 'n/a', 'N/A', 'None', '-', 'unknown'):
            with self.subTest(venue=venue):
                self.assertTrue(vq.is_preprint_venue(venue))

    def test_real_venues_are_not_preprints(self):
        for venue in (FULL_VENUE, FULL_CONFERENCE, 'Nature Computational Science',
                      'Findings of the Association for Computational Linguistics: EMNLP 2025',
                      'IEEE Transactions on Pattern Analysis and Machine Intelligence'):
            with self.subTest(venue=venue):
                self.assertFalse(vq.is_preprint_venue(venue))


class CompleteVenueTests(unittest.TestCase):

    def test_complete_venues(self):
        for venue in (FULL_VENUE, FULL_CONFERENCE, 'Proceedings of the AAAI conference'):
            with self.subTest(venue=venue):
                self.assertTrue(vq.is_complete_venue(venue))

    def test_truncated_venues_are_not_complete(self):
        for venue in (TRUNCATED, 'Proceedings of the 2025 ...', 'Findings of the …'):
            with self.subTest(venue=venue):
                self.assertFalse(vq.is_complete_venue(venue))

    def test_missing_venues_are_not_complete(self):
        for venue in ('', '   ', None, 'NA', 'N/A', 'None'):
            with self.subTest(venue=venue):
                self.assertFalse(vq.is_complete_venue(venue))

    def test_preprint_venues_are_not_complete(self):
        for venue in ('arXiv preprint arXiv:2401.00001', 'bioRxiv', 'SSRN'):
            with self.subTest(venue=venue):
                self.assertFalse(vq.is_complete_venue(venue))

    def test_truncation_markers_cover_the_unicode_ellipsis(self):
        self.assertTrue(vq.is_truncated_venue('Proceedings of the 2025 …'))
        self.assertTrue(vq.is_truncated_venue('Proceedings of the 2025 ...'))
        self.assertFalse(vq.is_truncated_venue(FULL_CONFERENCE))


class FullAuthorNameTests(unittest.TestCase):

    def test_spelled_out_given_names_are_detected(self):
        for authors in ('Jie Zhang', 'Jie Zhang, H Bu', 'M Bu, Haoyu Wen',
                        'J. Robert Oppenheimer'):
            with self.subTest(authors=authors):
                self.assertTrue(vq.has_full_author_names(authors))

    def test_initial_only_author_lists_are_not_full_names(self):
        for authors in ('H Wang, F Zhang', 'AH Kargaran, A Modarressi',
                        'M Bu', 'S Hartwig, D Engel, L Sick, H Kniesel',
                        'J. Wang'):
            with self.subTest(authors=authors):
                self.assertFalse(vq.has_full_author_names(authors))

    def test_missing_or_single_token_authors_are_no_evidence(self):
        for authors in ('', None, 'N/A', 'Agarwal', 'Aristotle', '   '):
            with self.subTest(authors=authors):
                self.assertFalse(vq.has_full_author_names(authors))

    def test_name_particles_are_not_mistaken_for_given_names(self):
        for authors in ('J van der Berg', 'H von Neumann', 'A de la Cruz'):
            with self.subTest(authors=authors):
                self.assertFalse(vq.has_full_author_names(authors))


class SkipPredicateTests(unittest.TestCase):

    def test_venue_detail_is_skipped_only_when_the_venue_is_complete(self):
        self.assertFalse(vq.venue_needs_detail(FULL_VENUE))
        self.assertTrue(vq.venue_needs_detail(TRUNCATED))
        self.assertTrue(vq.venue_needs_detail(''))
        self.assertTrue(vq.venue_needs_detail('arXiv preprint arXiv:2401.00001'))

    def test_the_four_citations_combinations(self):
        cases = [
            (FULL_VENUE, 'Jie Zhang', False),          # both in hand -> skip
            (FULL_VENUE, 'H Wang, F Zhang', True),     # authors still initials
            (TRUNCATED, 'Jie Zhang', True),            # venue still truncated
            ('arXiv preprint arXiv:1', 'Jie Zhang', True),  # preprint source
            ('', '', True),                            # nothing in hand
        ]
        for venue, authors, expected in cases:
            with self.subTest(venue=venue, authors=authors):
                self.assertEqual(
                    vq.citation_needs_cite_dialog(citation(venue, authors)),
                    expected)

    def test_non_dict_citations_are_not_requested(self):
        self.assertFalse(vq.citation_needs_cite_dialog('not a dict'))
        self.assertFalse(vq.citation_needs_cite_dialog(None))


# ---------------------------------------------------------------------------
# 2. The publication detail path
# ---------------------------------------------------------------------------

class VenueDetailSkipTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_path = os.path.join(self._tmp.name, vd.CACHE_FILENAME)
        self.pages = {vd.detail_path(TOKEN): detail_page()}

    def test_a_complete_venue_is_not_fetched(self):
        publications = [pub(venue=FULL_VENUE)]
        before = json.dumps(publications[0], sort_keys=True)
        stats = vd.enrich_publications_with_venue_full(
            publications, cache_path=self.cache_path,
            get_soup=never_called('detail page'), print_fn=quiet)

        self.assertEqual(stats['skipped_complete'], 1)
        self.assertEqual(stats['fetched'], 0)
        self.assertEqual(stats['with_token'], 0)
        self.assertEqual(publications[0]['venue_full'], '')
        self.assertEqual(json.dumps(publications[0], sort_keys=True), before)

    def test_skipping_writes_no_cache_entry(self):
        vd.enrich_publications_with_venue_full(
            [pub(venue=FULL_VENUE)], cache_path=self.cache_path,
            get_soup=never_called('detail page'), print_fn=quiet)
        self.assertFalse(os.path.exists(self.cache_path))

    def test_a_truncated_venue_is_still_fetched(self):
        publications = [pub(venue=TRUNCATED)]
        stats = vd.enrich_publications_with_venue_full(
            publications, cache_path=self.cache_path,
            get_soup=lambda path: self.pages.get(path), print_fn=quiet)
        self.assertEqual(stats['skipped_complete'], 0)
        self.assertEqual(stats['resolved'], 1)
        self.assertEqual(publications[0]['venue_full'], FULL_CONFERENCE)
        self.assertEqual(publications[0]['venue'], TRUNCATED)
        self.assertTrue(os.path.exists(self.cache_path))

    def test_preprint_and_missing_venues_are_still_fetched(self):
        for venue in ('arXiv preprint arXiv:2401.00001', '', 'N/A'):
            with self.subTest(venue=venue):
                publications = [pub(venue=venue, title=f'paper-{venue}')]
                stats = vd.enrich_publications_with_venue_full(
                    publications, cache_path=None,
                    get_soup=lambda path: detail_page(), print_fn=quiet)
                self.assertEqual(stats['fetched'], 1)
                self.assertEqual(publications[0]['venue_full'], FULL_CONFERENCE)

    def test_a_mixed_list_only_requests_the_incomplete_one(self):
        requests = []

        def get_soup(path):
            requests.append(path)
            return detail_page()

        complete = pub(venue=FULL_VENUE, title='complete')
        complete['citation_for_view'] = 'HF3-E9kAAAAJ:complete'
        truncated = pub(venue=TRUNCATED, title='truncated')
        stats = vd.enrich_publications_with_venue_full(
            [complete, truncated], cache_path=self.cache_path,
            get_soup=get_soup, print_fn=quiet)

        self.assertEqual(stats['skipped_complete'], 1)
        self.assertEqual(stats['fetched'], 1)
        self.assertEqual(requests, [vd.detail_path(TOKEN)])


# ---------------------------------------------------------------------------
# 3. The Cite-dialog path
# ---------------------------------------------------------------------------

class CiteFormatSkipTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_path = os.path.join(self._tmp.name, cf.CACHE_FILENAME)

    def _enrich(self, citations, **kwargs):
        kwargs.setdefault('cache_path', self.cache_path)
        kwargs.setdefault('get_soup', never_called('cite dialog'))
        kwargs.setdefault('get_page', never_called('bibtex'))
        kwargs.setdefault('print_fn', quiet)
        return cf.enrich_citations_with_cite_formats(citations, **kwargs)

    def test_a_complete_venue_with_full_names_is_not_fetched(self):
        rows = [citation(venue=FULL_VENUE, authors='Jie Zhang, Haoyu Bu')]
        before = json.dumps(rows[0], sort_keys=True)
        stats = self._enrich(rows)

        self.assertEqual(stats['skipped_complete'], 1)
        self.assertEqual(stats['fetched'], 0)
        self.assertEqual(stats['resolved'], 0)
        self.assertNotIn('cite', rows[0])
        self.assertEqual(json.dumps(rows[0], sort_keys=True), before)

    def test_skipping_writes_no_cache_entry(self):
        self._enrich([citation(venue=FULL_VENUE, authors='Jie Zhang')])
        self.assertFalse(os.path.exists(self.cache_path))

    def test_an_abbreviated_author_list_is_still_fetched(self):
        rows = [citation(venue=FULL_VENUE, authors='H Wang, F Zhang')]
        stats = self._enrich(rows, get_soup=lambda path: cite_page())
        self.assertEqual(stats['skipped_complete'], 0)
        self.assertEqual(stats['fetched'], 1)
        self.assertIn('chicago', rows[0]['cite'])

    def test_a_truncated_or_preprint_venue_is_still_fetched(self):
        for venue in (TRUNCATED, 'arXiv preprint arXiv:2401.00001', ''):
            with self.subTest(venue=venue):
                rows = [citation(venue=venue, authors='Jie Zhang')]
                stats = self._enrich(rows, cache_path=None,
                                     get_soup=lambda path: cite_page())
                self.assertEqual(stats['fetched'], 1)
                self.assertIn('cite', rows[0])

    def test_a_cached_dialog_is_reused_even_when_the_row_now_qualifies_to_skip(self):
        """Re-running with the rule must not drop data an earlier run paid for."""
        self._enrich([citation(venue=TRUNCATED, authors='H Wang, F Zhang')],
                     get_soup=lambda path: cite_page())
        rows = [citation(venue=FULL_VENUE, authors='Jie Zhang')]
        stats = self._enrich(rows)

        self.assertEqual(stats['cache_hits'], 1)
        self.assertEqual(stats['skipped_complete'], 0)
        self.assertIn('chicago', rows[0]['cite'])

    def test_a_mixed_list_only_requests_the_one_that_needs_it(self):
        requested = []

        def get_soup(path):
            requested.append(path)
            return cite_page()

        rows = [
            citation(venue=FULL_VENUE, authors='Jie Zhang', cid='already-good'),
            citation(venue=TRUNCATED, authors='H Wang', cid='needs-work'),
        ]
        stats = self._enrich(rows, get_soup=get_soup)

        self.assertEqual(stats['skipped_complete'], 1)
        self.assertEqual(stats['fetched'], 1)
        self.assertEqual(requested, [cf.cite_path('needs-work')])
        self.assertNotIn('cite', rows[0])
        self.assertIn('cite', rows[1])

    def test_only_the_row_that_needs_it_is_cached(self):
        rows = [
            citation(venue=FULL_VENUE, authors='Jie Zhang', cid='already-good'),
            citation(venue=TRUNCATED, authors='H Wang', cid='needs-work'),
        ]
        self._enrich(rows, get_soup=lambda path: cite_page())
        self.assertEqual(list(cf.load_cite_cache(self.cache_path)), ['needs-work'])


if __name__ == '__main__':
    unittest.main()


class SavedBibSkipsTheDialogTests(unittest.TestCase):
    """已保存的 bib 与本次线上 venue 一致时，**cite 与 bib 两个请求都省掉**（用户 2026-10-15）。

    crawler 下载 cite/bib 的访问量很大：只要这条引文已经下载过 bib，就先拿线上 venue 与
    已保存 bib 里的 venue 比一比 —— 一致 → 不访问；**不一致才更新 bib**。
    """

    FULL = ('Proceedings of the 2025 Conference on Empirical Methods in Natural '
            'Language Processing')

    def _citation(self, venue, bibtex=None):
        citation = {'venue': venue, 'authors': 'H Wang, F Zhang'}
        if bibtex is not None:
            citation['cite'] = {'bibtex': bibtex}
        return citation

    def test_truncated_online_venue_matching_the_saved_bib_skips(self):
        from code.venue_quality import citation_needs_cite_dialog, venue_consistent_with_saved_bib
        citation = self._citation('Proceedings of the 2025 Conference on Empirical …',
                                  {'booktitle': self.FULL})
        self.assertTrue(venue_consistent_with_saved_bib(citation))
        self.assertFalse(citation_needs_cite_dialog(citation))

    def test_identical_venue_skips(self):
        from code.venue_quality import citation_needs_cite_dialog
        self.assertFalse(citation_needs_cite_dialog(self._citation(self.FULL, {'booktitle': self.FULL})))

    def test_a_changed_venue_updates_the_bib(self):
        from code.venue_quality import citation_needs_cite_dialog, venue_consistent_with_saved_bib
        citation = self._citation('Proceedings of the 2024 Conference on Empirical …',
                                  {'booktitle': self.FULL})
        self.assertFalse(venue_consistent_with_saved_bib(citation))
        self.assertTrue(citation_needs_cite_dialog(citation))

    def test_journal_field_is_used_too(self):
        from code.venue_quality import citation_needs_cite_dialog
        citation = self._citation('Cybersecurity …', {'journal': 'Cybersecurity'})
        self.assertFalse(citation_needs_cite_dialog(citation))

    def test_without_a_saved_bib_nothing_changes(self):
        from code.venue_quality import citation_needs_cite_dialog
        self.assertTrue(citation_needs_cite_dialog(self._citation('Proceedings of the 2025 Conference on Empirical …')))

    def test_a_missing_online_venue_still_fetches(self):
        from code.venue_quality import citation_needs_cite_dialog
        self.assertTrue(citation_needs_cite_dialog(self._citation('', {'booktitle': self.FULL})))
        self.assertTrue(citation_needs_cite_dialog(self._citation('NA', {'booktitle': self.FULL})))

    def test_a_too_short_shared_prefix_is_not_treated_as_the_same_venue(self):
        """`IEEE` 之类短串跟谁都是前缀 —— 宁可多抓一次，也不误跳过。"""
        from code.venue_quality import citation_needs_cite_dialog, venue_consistent_with_saved_bib
        citation = self._citation('IEEE', {'booktitle': 'IEEE Transactions on Pattern Analysis'})
        self.assertFalse(venue_consistent_with_saved_bib(citation))
        self.assertTrue(citation_needs_cite_dialog(citation))

    def test_case_and_whitespace_do_not_matter(self):
        from code.venue_quality import citation_needs_cite_dialog
        citation = self._citation('proceedings of the 2025 conference on empirical   methods …',
                                  {'booktitle': self.FULL})
        self.assertFalse(citation_needs_cite_dialog(citation))
