"""
tests/test_venue_detail.py — full venue strings from Scholar's detail page.

Hermetic: HTML fixtures are inline, ``get_soup`` is a fake callable and every
cache/profile path lives in a temporary directory.  No network is touched.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import patch, StringIO  # noqa: E402  (installs the stubs)

from code import author_fetcher as af  # noqa: E402
from code.pub_info import PubInfo  # noqa: E402
from code.profile_io import AuthorProfile  # noqa: E402
from code import venue_detail as vd  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# The real page opens with a <style> block that mentions .gsc_oci_field and
# .gsc_oci_value; a regex over gsc_oci_value reads that CSS as a field value.
STYLE_BLOCK = (
    "<style>"
    ".gsc_oci_field{float:left;width:100px;text-align:right;color:#777;}"
    ".gsc_oci_value{margin-left:116px;max-width:512px;margin-bottom:16px;}"
    "</style>"
)

TRUNCATED_VENUE = (
    "Proceedings of the 2024 Conference of the North American Chapter of the …, 2024"
)
FULL_VENUE = (
    "Proceedings of the 2024 Conference of the North American Chapter of the "
    "Association for Computational Linguistics: Human Language Technologies "
    "(Volume 1: Long Papers)"
)

TOKEN = "HF3-E9kAAAAJ:xtoqd-5pKcoC"
DETAIL_URL = (
    "https://scholar.google.com/citations?view_op=view_citation&hl=en&"
    f"user=HF3-E9kAAAAJ&pagesize=100&citation_for_view={TOKEN}"
)


def read_json(path):
    with open(path, 'r', encoding='utf-8') as handle:
        return json.load(handle)


def detail_page(rows, style=True):
    """Build a detail page from (label, value_html) pairs."""
    body = "".join(
        '<div class="gs_scl"><div class="gsc_oci_field">%s</div>'
        '<div class="gsc_oci_value">%s</div></div>' % (label, value)
        for label, value in rows
    )
    return ("<!doctype html><html><head>%s</head><body>"
            '<div id="gsc_oci_title_wrapper"><div id="gsc_oci_title">A paper</div></div>'
            '<div id="gsc_oci_table">%s</div></body></html>'
            % (STYLE_BLOCK if style else "", body))


def default_page():
    return detail_page([
        ("Authors", "P Ding, J Kuang, D Ma, X Cao, Y Xian, J Chen, S Huang"),
        ("Publication date", "2024/6"),
        ("Conference", FULL_VENUE),
        ("Pages", "2136-2153"),
        ("Total citations", "Cited by 404"),
    ])


def pub(title="A paper", token=TOKEN, url=None, venue=TRUNCATED_VENUE):
    return {
        'no': 1, 'title': title, 'year': '2024', 'venue': venue,
        'authors': 'P Ding, J Kuang', 'num_citations': 404,
        'url': DETAIL_URL if url is None else url,
        'citedby_url': '/scholar?cites=1',
        'citation_for_view': token, 'venue_full': '', 'venue_full_source': '',
    }


class _FakeNav:
    """Stand-in for the patched scholarly navigator."""

    def __init__(self, pages=None, error_for=None, error=None):
        self.pages = pages or {}
        self.error_for = error_for
        self.error = error or RuntimeError("blocked")
        self.calls = []

    def _get_soup(self, path):
        self.calls.append(path)
        if self.error_for is not None and self.error_for in path:
            raise self.error
        return self.pages.get(path)


# ---------------------------------------------------------------------------
# 1. citation_for_view token
# ---------------------------------------------------------------------------

class CitationForViewTokenTests(unittest.TestCase):

    def test_token_is_read_from_the_stored_field(self):
        self.assertEqual(vd.citation_for_view_of(pub()), TOKEN)

    def test_token_is_recovered_from_a_legacy_url(self):
        """Profiles written before the field existed only carry the URL."""
        legacy = {'title': 'A paper', 'url': DETAIL_URL}
        self.assertEqual(vd.citation_for_view_of(legacy), TOKEN)

    def test_token_is_missing_for_a_publication_without_the_link(self):
        for url in ('', 'N/A', 'https://example.org/paper.pdf', None):
            with self.subTest(url=url):
                self.assertEqual(vd.citation_for_view_of({'url': url}), '')

    def test_detail_path_matches_the_scholarly_template(self):
        self.assertEqual(
            vd.detail_path(TOKEN),
            f'/citations?hl=en&view_op=view_citation&citation_for_view={TOKEN}',
        )

    def test_pub_info_takes_the_token_from_scholarly_author_pub_id(self):
        info = PubInfo.from_scholarly(
            {'author_pub_id': TOKEN, 'bib': {'title': 'A paper'}, 'pub_url': ''}, 1)
        self.assertEqual(info.citation_for_view, TOKEN)

    def test_pub_info_recovers_the_token_from_the_pub_url(self):
        info = PubInfo.from_scholarly(
            {'bib': {'title': 'A paper'}, 'pub_url': DETAIL_URL}, 1)
        self.assertEqual(info.citation_for_view, TOKEN)

    def test_pub_info_from_dict_round_trips_the_new_fields(self):
        raw = pub()
        raw['venue_full'] = FULL_VENUE
        raw['venue_full_source'] = vd.VENUE_FULL_SOURCE
        out = PubInfo.from_dict(raw).to_dict()
        self.assertEqual(out['citation_for_view'], TOKEN)
        self.assertEqual(out['venue_full'], FULL_VENUE)
        self.assertEqual(out['venue_full_source'], 'scholar_detail')
        self.assertEqual(out['venue'], TRUNCATED_VENUE)

    def test_pub_info_from_dict_leaves_unknown_venue_full_empty(self):
        out = PubInfo.from_dict({'title': 'x', 'url': DETAIL_URL}).to_dict()
        self.assertEqual(out['venue_full'], '')
        self.assertEqual(out['venue_full_source'], '')


# ---------------------------------------------------------------------------
# 2. Detail-page field pairing
# ---------------------------------------------------------------------------

class DetailFieldParsingTests(unittest.TestCase):

    def test_labels_are_paired_with_their_own_values(self):
        fields = vd.parse_detail_fields(default_page())
        self.assertEqual(fields['conference'], FULL_VENUE)
        self.assertEqual(fields['pages'], '2136-2153')
        self.assertEqual(fields['publication date'], '2024/6')

    def test_style_block_is_not_parsed_as_a_field(self):
        """A regex over gsc_oci_value would read the CSS as a value."""
        fields = vd.parse_detail_fields(
            detail_page([("Journal", "Nature computational science")]))
        self.assertEqual(set(fields), {'journal'})
        self.assertNotIn('#777', fields['journal'])
        self.assertNotIn('margin-left', ' '.join(fields.values()))

    def test_field_order_does_not_matter(self):
        ordered = detail_page([
            ("Authors", "A B"),
            ("Journal", "Journal of Examples"),
            ("Pages", "1-2"),
        ])
        reordered = detail_page([
            ("Pages", "1-2"),
            ("Journal", "Journal of Examples"),
            ("Authors", "A B"),
        ])
        self.assertEqual(vd.pick_venue_field(vd.parse_detail_fields(ordered)),
                         vd.pick_venue_field(vd.parse_detail_fields(reordered)))
        self.assertEqual(vd.pick_venue_field(vd.parse_detail_fields(reordered)),
                         ('journal', 'Journal of Examples'))

    def test_venue_label_variants(self):
        for label in ('Journal', 'Conference', 'BOOK', 'Publication', 'Periodical'):
            with self.subTest(label=label):
                fields = vd.parse_detail_fields(
                    detail_page([("Authors", "A B"), (label, "The Venue")]))
                self.assertEqual(vd.pick_venue_field(fields),
                                 (label.lower(), 'The Venue'))

    def test_missing_venue_label_yields_nothing(self):
        fields = vd.parse_detail_fields(detail_page([
            ("Authors", "A B"), ("Publication date", "2024"), ("Pages", "1-2")]))
        self.assertEqual(vd.pick_venue_field(fields), ('', ''))

    def test_empty_venue_value_is_not_used(self):
        fields = vd.parse_detail_fields(
            detail_page([("Journal", "   "), ("Conference", "Real Conference")]))
        self.assertEqual(vd.pick_venue_field(fields), ('conference', 'Real Conference'))

    def test_whitespace_and_newlines_are_collapsed(self):
        fields = vd.parse_detail_fields(detail_page([
            ("Conference", "Proceedings of the 2024\n        Conference of the\n"
                           "North American Chapter\t(Volume 1)")]))
        self.assertEqual(fields['conference'],
                         'Proceedings of the 2024 Conference of the North '
                         'American Chapter (Volume 1)')

    def test_page_without_a_field_table_is_empty(self):
        captcha = ('<html><head>' + STYLE_BLOCK + '</head><body>'
                   '<div id="gs_captcha_ccl">Please show you are not a robot</div>'
                   '</body></html>')
        self.assertEqual(vd.parse_detail_fields(captcha), {})

    def test_accepts_an_already_parsed_soup(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(default_page(), 'html.parser')
        self.assertEqual(vd.parse_detail_fields(soup)['conference'], FULL_VENUE)

    def test_row_without_a_value_is_ignored(self):
        html = ('<html><body><div class="gs_scl">'
                '<div class="gsc_oci_field">Journal</div></div>'
                '<div class="gs_scl"><div class="gsc_oci_field">Pages</div>'
                '<div class="gsc_oci_value">1-2</div></div></body></html>')
        self.assertEqual(vd.parse_detail_fields(html), {'pages': '1-2'})


# ---------------------------------------------------------------------------
# 3. Enrichment: new fields, venue untouched
# ---------------------------------------------------------------------------

class EnrichmentTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_path = os.path.join(self._tmp.name, vd.CACHE_FILENAME)

    def _get_soup(self, pages):
        nav = _FakeNav(pages=pages)
        return nav, nav._get_soup

    def test_venue_full_is_added_with_its_source(self):
        publications = [pub()]
        nav, get_soup = self._get_soup({vd.detail_path(TOKEN): default_page()})

        stats = vd.enrich_publications_with_venue_full(
            publications, cache_path=self.cache_path, get_soup=get_soup,
            print_fn=lambda *a, **k: None, fetched_at='2026-10-14T00:00:00')

        self.assertEqual(publications[0]['venue_full'], FULL_VENUE)
        self.assertEqual(publications[0]['venue_full_source'], 'scholar_detail')
        self.assertEqual(publications[0]['venue'], TRUNCATED_VENUE)
        self.assertEqual(nav.calls, [vd.detail_path(TOKEN)])
        self.assertEqual(stats['fetched'], 1)
        self.assertEqual(stats['resolved'], 1)

    def test_venue_is_byte_identical_after_a_profile_round_trip(self):
        publications = [pub()]
        profile_path = os.path.join(self._tmp.name, 'author_x_profile.json')
        AuthorProfile(author_info={'citedby': 1}, publications=publications,
                      fetch_time='2026-10-13T00:00:00').save_json(profile_path)

        before = read_json(profile_path)
        before_venue = before['publications'][0]['venue']

        loaded = AuthorProfile.load(profile_path)
        vd.enrich_publications_with_venue_full(
            loaded.publications, cache_path=self.cache_path,
            get_soup=self._get_soup({vd.detail_path(TOKEN): default_page()})[1],
            print_fn=lambda *a, **k: None)
        loaded.save_json(profile_path)

        after = read_json(profile_path)
        self.assertEqual(after['publications'][0]['venue'], before_venue)
        self.assertEqual(after['publications'][0]['venue'], TRUNCATED_VENUE)
        self.assertEqual(after['publications'][0]['venue_full'], FULL_VENUE)
        self.assertEqual(after['publications'][0]['venue_full_source'], 'scholar_detail')

    def test_publication_without_a_token_is_skipped_without_a_request(self):
        publications = [pub(url='N/A', token='')]
        nav, get_soup = self._get_soup({})

        stats = vd.enrich_publications_with_venue_full(
            publications, cache_path=self.cache_path, get_soup=get_soup,
            print_fn=lambda *a, **k: None)

        self.assertEqual(nav.calls, [])
        self.assertEqual(stats['missing_token'], 1)
        self.assertEqual(stats['fetched'], 0)
        self.assertEqual(publications[0]['venue_full'], '')

    def test_unresolved_publication_keeps_the_new_fields_empty(self):
        publications = [pub()]
        stats = vd.enrich_publications_with_venue_full(
            publications, cache_path=self.cache_path,
            get_soup=self._get_soup({
                vd.detail_path(TOKEN): detail_page([("Authors", "A B")])})[1],
            print_fn=lambda *a, **k: None)
        self.assertEqual(stats['no_venue_field'], 1)
        self.assertEqual(stats['resolved'], 0)
        self.assertEqual(publications[0]['venue_full'], '')
        self.assertEqual(publications[0]['venue_full_source'], '')

    def test_output_pub_dict_keeps_the_new_fields(self):
        """Phase 2 rebuilds pub through PubInfo; the fields must survive."""
        publications = [pub()]
        vd.enrich_publications_with_venue_full(
            publications, cache_path=self.cache_path,
            get_soup=self._get_soup({vd.detail_path(TOKEN): default_page()})[1],
            print_fn=lambda *a, **k: None)
        out = PubInfo.from_dict(publications[0]).to_dict()
        self.assertEqual(out['venue_full'], FULL_VENUE)
        self.assertEqual(out['venue'], TRUNCATED_VENUE)


# ---------------------------------------------------------------------------
# 4. Graceful degradation
# ---------------------------------------------------------------------------

class GracefulDegradationTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_path = os.path.join(self._tmp.name, vd.CACHE_FILENAME)

    def test_request_failure_is_counted_and_the_next_publication_continues(self):
        pages = {vd.detail_path('HF3-E9kAAAAJ:ok'): default_page()}
        publications = [pub(title='broken', token='HF3-E9kAAAAJ:broken'),
                        pub(title='fine', token='HF3-E9kAAAAJ:ok')]
        nav = _FakeNav(pages=pages, error_for='broken')

        stats = vd.enrich_publications_with_venue_full(
            publications, cache_path=self.cache_path, get_soup=nav._get_soup,
            print_fn=lambda *a, **k: None)

        self.assertEqual(stats['failed'], 1)
        self.assertEqual(stats['resolved'], 1)
        self.assertEqual(publications[0]['venue_full'], '')
        self.assertEqual(publications[1]['venue_full'], FULL_VENUE)

    def test_failed_fetch_is_not_cached_so_it_is_retried(self):
        publications = [pub(token='HF3-E9kAAAAJ:broken')]
        nav = _FakeNav(error_for='broken')
        vd.enrich_publications_with_venue_full(
            publications, cache_path=self.cache_path, get_soup=nav._get_soup,
            print_fn=lambda *a, **k: None)
        self.assertNotIn('HF3-E9kAAAAJ:broken', vd.load_venue_cache(self.cache_path))

        retry_nav = _FakeNav(pages={vd.detail_path('HF3-E9kAAAAJ:broken'): default_page()})
        stats = vd.enrich_publications_with_venue_full(
            [pub(token='HF3-E9kAAAAJ:broken')], cache_path=self.cache_path,
            get_soup=retry_nav._get_soup, print_fn=lambda *a, **k: None)
        self.assertEqual(stats['fetched'], 1)
        self.assertEqual(stats['resolved'], 1)

    def test_page_without_a_field_table_is_a_failure_not_a_venue(self):
        empty_page = '<html><head>' + STYLE_BLOCK + '</head><body>no table</body></html>'
        publications = [pub()]
        stats = vd.enrich_publications_with_venue_full(
            publications, cache_path=self.cache_path,
            get_soup=lambda path: empty_page, print_fn=lambda *a, **k: None)
        self.assertEqual(stats['failed'], 1)
        self.assertEqual(stats['no_venue_field'], 0)
        self.assertNotIn(TOKEN, vd.load_venue_cache(self.cache_path))

    def test_interrupt_propagates_and_keeps_what_was_already_fetched(self):
        calls = []

        def get_soup(path):
            calls.append(path)
            if len(calls) > 1:
                raise KeyboardInterrupt()
            return default_page()

        publications = [pub(token='HF3-E9kAAAAJ:first'),
                        pub(token='HF3-E9kAAAAJ:second')]
        with self.assertRaises(KeyboardInterrupt):
            vd.enrich_publications_with_venue_full(
                publications, cache_path=self.cache_path, get_soup=get_soup,
                print_fn=lambda *a, **k: None)
        self.assertIn('HF3-E9kAAAAJ:first', vd.load_venue_cache(self.cache_path))

    def test_missing_navigator_leaves_the_profile_untouched(self):
        with patch('sys.stdout', new_callable=StringIO):
            fetcher = af.AuthorProfileFetcher('test-author', output_dir=self._tmp.name)
            result = fetcher.fetch_venue_details([pub()])
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# 5. Cache / resume
# ---------------------------------------------------------------------------

class VenueDetailCacheTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_path = os.path.join(self._tmp.name, vd.CACHE_FILENAME)

    def test_cache_file_is_written_per_fetched_page(self):
        vd.enrich_publications_with_venue_full(
            [pub()], cache_path=self.cache_path,
            get_soup=lambda path: default_page(),
            print_fn=lambda *a, **k: None, fetched_at='2026-10-14T00:00:00')
        cache = read_json(self.cache_path)
        self.assertEqual(cache[TOKEN]['venue_full'], FULL_VENUE)
        self.assertEqual(cache[TOKEN]['venue_label'], 'conference')
        self.assertEqual(cache[TOKEN]['status'], 'ok')
        self.assertEqual(cache[TOKEN]['fetched_at'], '2026-10-14T00:00:00')

    def test_second_run_is_served_from_the_cache_without_a_request(self):
        nav = _FakeNav(pages={vd.detail_path(TOKEN): default_page()})
        vd.enrich_publications_with_venue_full(
            [pub()], cache_path=self.cache_path, get_soup=nav._get_soup,
            print_fn=lambda *a, **k: None)
        self.assertEqual(len(nav.calls), 1)

        publications = [pub()]
        stats = vd.enrich_publications_with_venue_full(
            publications, cache_path=self.cache_path,
            get_soup=lambda path: (_ for _ in ()).throw(AssertionError("refetched")),
            print_fn=lambda *a, **k: None)

        self.assertEqual(stats['cache_hits'], 1)
        self.assertEqual(stats['fetched'], 0)
        self.assertEqual(stats['resolved'], 1)
        self.assertEqual(publications[0]['venue_full'], FULL_VENUE)

    def test_no_venue_field_result_is_cached_and_not_refetched(self):
        pages = {vd.detail_path(TOKEN): detail_page([("Authors", "A B")])}
        nav = _FakeNav(pages=pages)
        vd.enrich_publications_with_venue_full(
            [pub()], cache_path=self.cache_path, get_soup=nav._get_soup,
            print_fn=lambda *a, **k: None)
        nav2 = _FakeNav(pages=pages)
        stats = vd.enrich_publications_with_venue_full(
            [pub()], cache_path=self.cache_path, get_soup=nav2._get_soup,
            print_fn=lambda *a, **k: None)
        self.assertEqual(nav2.calls, [])
        self.assertEqual(stats['cache_hits'], 1)
        self.assertEqual(vd.load_venue_cache(self.cache_path)[TOKEN]['status'],
                         'no_venue_field')

    def test_corrupt_cache_file_starts_empty(self):
        with open(self.cache_path, 'w', encoding='utf-8') as handle:
            handle.write('{not json')
        with patch('builtins.print'):
            self.assertEqual(vd.load_venue_cache(self.cache_path), {})

    def test_missing_cache_file_is_empty(self):
        self.assertEqual(vd.load_venue_cache(self.cache_path), {})

    def test_cache_entries_that_are_not_dicts_are_dropped(self):
        vd.save_venue_cache(self.cache_path, {TOKEN: 'plain string'})
        self.assertEqual(vd.load_venue_cache(self.cache_path), {})


# ---------------------------------------------------------------------------
# 6. AuthorProfileFetcher wiring
# ---------------------------------------------------------------------------

class AuthorProfileFetcherVenueTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        with patch('sys.stdout', new_callable=StringIO):
            self.fetcher = af.AuthorProfileFetcher('test-author',
                                                   output_dir=self._tmp.name)

    def _run(self, publications, nav):
        basics = {'name': 'Test Author', 'citedby': 10, 'cites_per_year': {}}
        with patch.object(self.fetcher, 'fetch_basics', return_value=(basics, True)), \
             patch.object(self.fetcher, 'fetch_publications', return_value=publications), \
             patch.object(af.scholarly, '_Scholarly__nav', nav), \
             patch('sys.stdout', new_callable=StringIO) as stdout:
            success = self.fetcher.run()
        return success, stdout.getvalue()

    def test_run_fills_venue_full_from_the_detail_pages(self):
        publications = [pub()]
        success, output = self._run(
            publications, _FakeNav(pages={vd.detail_path(TOKEN): default_page()}))

        self.assertTrue(success)
        saved = read_json(self.fetcher.profile_json)
        saved_pub = saved['publications'][0]
        self.assertEqual(saved_pub['venue_full'], FULL_VENUE)
        self.assertEqual(saved_pub['venue_full_source'], 'scholar_detail')
        self.assertEqual(saved_pub['venue'], TRUNCATED_VENUE)
        self.assertEqual(saved_pub['citation_for_view'], TOKEN)
        self.assertIn('venue_full resolved: 1/1', output)
        self.assertTrue(os.path.exists(self.fetcher.venue_detail_cache))

    def test_run_survives_a_failing_detail_fetch(self):
        publications = [pub()]
        success, output = self._run(
            publications, _FakeNav(error_for='citation_for_view'))

        self.assertTrue(success)
        saved = read_json(self.fetcher.profile_json)
        self.assertEqual(saved['publications'][0]['venue_full'], '')
        self.assertEqual(saved['publications'][0]['venue'], TRUNCATED_VENUE)
        self.assertIn('failed 1', output)

    def test_run_without_a_navigator_still_saves_the_profile(self):
        success, output = self._run([pub()], None)

        self.assertTrue(success)
        saved = read_json(self.fetcher.profile_json)
        self.assertEqual(saved['publications'][0]['venue_full'], '')
        self.assertIn('detail pages unavailable', output)


if __name__ == '__main__':
    unittest.main()
