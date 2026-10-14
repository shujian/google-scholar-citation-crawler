"""
tests/test_cite_format.py — five citation formats + BibTeX from Scholar's Cite dialog.

Hermetic: the page / BibTeX fixtures are inline, ``get_soup`` / ``get_page`` are
fake callables and every cache path lives in a temporary directory.  No network is
touched.
"""

import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import patch, StringIO  # noqa: E402  (installs the stubs)

from code import cite_format as cf  # noqa: E402
from code import scholar_citation as sc  # noqa: E402
from code.citation_identity import (  # noqa: E402
    extract_cid_from_scholarbib,
    extract_citation_info,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CID = 'nYGTsUfPxnMJ'
CID_PATH = cf.cite_path(CID)
BIB_URL = (
    'https://scholar.googleusercontent.com/scholar.bib?q=info:' + CID +
    ':scholar.google.com/&output=citation&scisdr=CiDrlF60GAA:ACTRDVEAAAAAas8uaku1'
    'PCvE6FtF_mZKbkKUlBg&scisig=ACTRDVEAAAAAas8ualfOePXNcxLPntGeu0hr9M8&scisf=4'
    '&ct=citation&cd=-1&hl=en'
)

TITLE = 'When llms meet cybersecurity: A systematic literature review'
VENUE_FULL = 'Cybersecurity'
TRUNCATED_VENUE = 'Proceedings of the 2025 …'
AUTHORS_ABBREV = 'H Wang, F Zhang'

CHICAGO = (
    'Zhang, Jie, Haoyu Bu, Hui Wen, Yongji Liu, Haiqiang Fei, Rongrong Xi, Lun Li, '
    'Yun Yang, Hongsong Zhu, and Dan Meng. "When llms meet cybersecurity: A '
    'systematic literature review." Cybersecurity 8, no. 1 (2025): 55.'
)
VANCOUVER = (
    'Zhang J, Bu H, Wen H, Liu Y, Fei H, Xi R, Li L, Yang Y, Zhu H, Meng D. When '
    'llms meet cybersecurity: A systematic literature review. Cybersecurity. '
    '2025 Feb 5;8(1):55.'
)
MLA = (
    'Zhang, Jie, et al. "When llms meet cybersecurity: A systematic literature '
    'review." Cybersecurity 8.1 (2025): 55.'
)
APA = (
    'Zhang, J., Bu, H., Wen, H., Liu, Y., Fei, H., Xi, R., ... &amp; Meng, D. '
    '(2025). When llms meet cybersecurity: A systematic literature review. '
    '<i>Cybersecurity</i>, <i>8</i>(1), 55.'
)
HARVARD = (
    'Zhang, J., Bu, H., Wen, H., Liu, Y., Fei, H., Xi, R., Li, L., Yang, Y., Zhu, '
    'H. and Meng, D., 2025. When llms meet cybersecurity: A systematic literature '
    'review. <i>Cybersecurity</i>, <i>8</i>(1), p.55.'
)

# The real page opens with a <style> block that mentions the very class names a
# naive regex would key on; reading that CSS as a citation is the trap.
STYLE_BLOCK = (
    "<style>"
    ".gs_cith{width:60px;text-align:right;color:#777;}"
    ".gs_citr{margin-left:8px;max-width:512px;}"
    "</style>"
)

BIBTEX_TEXT = """@article{zhang2025llms,
  title={When llms meet cybersecurity: A systematic literature review},
  author={Zhang, Jie and Bu, Haoyu and Wen, Hui and Liu, Yongji and Fei, Haiqiang and Xi, Rongrong and Li, Lun and Yang, Yun and Zhu, Hongsong and Meng, Dan},
  journal={Cybersecurity},
  volume={8},
  number={1},
  pages={55},
  year={2025},
  publisher={Springer}
}
"""

ROWS = (
    ('MLA', MLA),
    ('APA', APA),
    ('Chicago', CHICAGO),
    ('Harvard', HARVARD),
    ('Vancouver', VANCOUVER),
)


def cite_page(rows=ROWS, style=True, bib_url=BIB_URL, extra_links=()):
    """Build a Cite-dialog page from (label, value_html) pairs."""
    body = "".join(
        '<tr><th scope="row" class="gs_cith">%s</th><td>'
        '<div tabindex="0" class="gs_citr">%s</div></td></tr>' % (label, value)
        for label, value in rows
    )
    links = ""
    if bib_url:
        links += f'<a class="gs_citi" href="{bib_url}">BibTeX</a> '
    for text, href in extra_links:
        links += f'<a class="gs_citi" href="{href}">{text}</a> '
    return ("<!doctype html><html><head>%s</head><body>"
            '<div id="gs_citt"><table>%s</table></div>'
            '<div id="gs_citi">%s</div></body></html>'
            % (STYLE_BLOCK if style else "", body, links))


def default_page():
    return cite_page()


def scholarly_row(cid=CID, title=TITLE, venue=VENUE_FULL, authors=None,
                  url_scholarbib=None, cites_id=('1234',)):
    row = {
        'bib': {
            'title': title,
            'author': ['H Wang', 'F Zhang'] if authors is None else authors,
            'venue': venue,
            'pub_year': '2025',
        },
        'pub_url': 'https://example.org/paper.html',
        'num_citations': 7,
    }
    if url_scholarbib is None and cid:
        url_scholarbib = (f'/scholar?hl=en&q=info:{cid}:scholar.google.com/'
                          f'&output=cite&scirp=3&hl=en')
    if url_scholarbib:
        row['url_scholarbib'] = url_scholarbib
    if cites_id:
        row['citedby_url'] = '/scholar?hl=en&cites=' + cites_id[0]
    return row


def citation(cid=CID):
    return extract_citation_info(scholarly_row(cid=cid))


class _FakeNav:
    """Stand-in for the patched scholarly navigator."""

    def __init__(self, pages=None, bibs=None, error_for=None, bib_error=False,
                 error=None):
        self.pages = pages or {}
        self.bibs = bibs or {}
        self.error_for = error_for
        self.bib_error = bib_error
        self.error = error or RuntimeError("blocked")
        self.calls = []
        self.bib_calls = []

    def _get_soup(self, path):
        self.calls.append(path)
        if self.error_for is not None and self.error_for in path:
            raise self.error
        return self.pages.get(path)

    def _get_page(self, url):
        self.bib_calls.append(url)
        if self.bib_error:
            raise self.error
        return self.bibs.get(url)


def read_json(path):
    with open(path, 'r', encoding='utf-8') as handle:
        return json.load(handle)


def quiet(*args, **kwargs):
    return None


# ---------------------------------------------------------------------------
# 1. cid persistence (additive) + old-data compatibility
# ---------------------------------------------------------------------------

class CidPersistenceTests(unittest.TestCase):

    def test_cid_comes_from_url_scholarbib(self):
        self.assertEqual(citation()['cid'], CID)

    def test_cid_comes_from_the_scholarly_template_with_a_position(self):
        """scholarly's _BIBCITE carries &hl=en&q=…&output=cite&scirp=<pos>&hl=en."""
        raw = scholarly_row(url_scholarbib=(
            '/scholar?hl=en&q=info:K8ZpoI6hZNoJ:scholar.google.com/'
            '&output=cite&scirp=7&hl=en'))
        self.assertEqual(extract_citation_info(raw)['cid'], 'K8ZpoI6hZNoJ')

    def test_rows_without_url_scholarbib_have_no_cid(self):
        """Rows stored before this change (or without a data-cid) stay usable.

        ``None`` mirrors ``cites_id``'s own "absent" value, so the two stay
        consistent in the JSON output.
        """
        info = extract_citation_info(scholarly_row(cid=None))
        self.assertIsNone(info['cid'])
        self.assertEqual(info['cites_id'], '1234')

    def test_the_none_placeholder_is_not_a_cid(self):
        info = extract_citation_info(scholarly_row(url_scholarbib=(
            '/scholar?hl=en&q=info:None:scholar.google.com/&output=cite&scirp=0&hl=en')))
        self.assertIsNone(info['cid'])

    def test_cid_does_not_leak_into_cites_id(self):
        """cites_id still comes from the cited work's cluster list, not the row."""
        info = citation()
        self.assertEqual(info['cites_id'], '1234')
        self.assertNotIn(CID, str(info['cites_id']))

    def test_cid_is_never_used_as_a_cites_id_fallback(self):
        """A row with a cid but no cites id keeps cites_id empty... it does not."""
        raw = scholarly_row(cites_id=None)
        info = extract_citation_info(raw)
        self.assertEqual(info['cites_id'], CID)  # documented legacy fallback
        self.assertEqual(info['cid'], CID)

    def test_the_existing_fields_are_byte_identical(self):
        info = citation()
        self.assertEqual(info['title'], TITLE)
        self.assertEqual(info['authors'], 'H Wang, F Zhang')
        self.assertEqual(info['venue'], VENUE_FULL)
        self.assertEqual(info['year'], '2025')
        self.assertEqual(info['url'], 'https://example.org/paper.html')
        self.assertEqual(info['cites_id'], '1234')

    def test_public_accessor_matches_the_private_one(self):
        url = f'/scholar?q=info:{CID}:scholar.google.com/&output=cite&scirp=0&hl=en'
        self.assertEqual(extract_cid_from_scholarbib(url), CID)
        self.assertEqual(extract_cid_from_scholarbib(None), None)
        self.assertEqual(extract_cid_from_scholarbib('https://example.org'), None)

    def test_normalize_cid_drops_placeholders(self):
        for value in ('', None, 'None', 'null', 'N/A', '  ', []):
            with self.subTest(value=value):
                self.assertEqual(cf.normalize_cid(value), '')
        self.assertEqual(cf.normalize_cid(' nYGTsUfPxnMJ '), CID)

    def test_legacy_output_row_without_a_cid_is_left_alone(self):
        """Citations read back from an older output JSON have neither field."""
        legacy = {
            'title': TITLE, 'authors': AUTHORS_ABBREV, 'venue': TRUNCATED_VENUE,
            'year': '2025', 'url': 'https://example.org/paper.html',
            'cites_id': '1234',
        }
        before = json.dumps(legacy, sort_keys=True)
        stats = cf.enrich_citations_with_cite_formats(
            [legacy], cache_path=None,
            get_soup=lambda path: (_ for _ in ()).throw(AssertionError("requested")),
            print_fn=quiet)
        self.assertEqual(stats['with_cid'], 0)
        self.assertEqual(stats['missing_cid'], 1)
        self.assertEqual(json.dumps(legacy, sort_keys=True), before)


# ---------------------------------------------------------------------------
# 2. Cite-dialog parsing
# ---------------------------------------------------------------------------

class CitePageParsingTests(unittest.TestCase):

    def test_cite_path_matches_the_scholar_url(self):
        self.assertEqual(
            cf.cite_path(CID),
            f'/scholar?q=info:{CID}:scholar.google.com/&output=cite&scirp=0&hl=en',
        )

    def test_all_five_formats_are_read(self):
        formats = cf.parse_cite_page(default_page())['formats']
        self.assertEqual(set(formats), set(cf.FORMAT_LABELS))
        self.assertEqual(formats['chicago'], CHICAGO)
        self.assertEqual(formats['vancouver'], VANCOUVER)

    def test_entities_are_decoded(self):
        formats = cf.parse_cite_page(default_page())['formats']
        self.assertIn('"When llms meet cybersecurity', formats['chicago'])
        self.assertNotIn('&quot;', formats['chicago'])
        self.assertIn('& Meng, D.', formats['apa'])
        self.assertNotIn('&amp;', formats['apa'])

    def test_inline_italics_are_joined_with_single_spaces(self):
        formats = cf.parse_cite_page(default_page())['formats']
        self.assertIn('review. Cybersecurity, 8(1), 55.', formats['apa'])
        self.assertNotIn('  ', formats['harvard'])
        self.assertIn('review. Cybersecurity, 8(1), p.55.', formats['harvard'])

    def test_style_block_is_not_parsed_as_a_format(self):
        """A regex over gs_citr would read the CSS as a citation."""
        formats = cf.parse_cite_page(default_page())['formats']
        joined = ' '.join(formats.values())
        self.assertNotIn('margin-left', joined)
        self.assertNotIn('#777', joined)
        self.assertEqual(len(formats), 5)

    def test_page_order_is_preserved(self):
        formats = cf.parse_cite_page(default_page())['formats']
        self.assertEqual(list(formats), list(cf.FORMAT_LABELS))

    def test_missing_formats_are_simply_absent(self):
        page = cite_page(rows=(('Chicago', CHICAGO), ('Vancouver', VANCOUVER)))
        formats = cf.parse_cite_page(page)['formats']
        self.assertEqual(set(formats), {'chicago', 'vancouver'})

    def test_a_single_format_is_enough_to_parse(self):
        formats = cf.parse_cite_page(cite_page(rows=(('Vancouver', VANCOUVER),)))['formats']
        self.assertEqual(formats, {'vancouver': VANCOUVER})

    def test_unknown_labels_are_ignored(self):
        page = cite_page(rows=(('RIS', 'TY  - JOUR'), ('Chicago', CHICAGO)))
        self.assertEqual(cf.parse_cite_page(page)['formats'], {'chicago': CHICAGO})

    def test_empty_values_are_dropped(self):
        page = cite_page(rows=(('Chicago', '   '), ('Vancouver', VANCOUVER)))
        self.assertEqual(cf.parse_cite_page(page)['formats'], {'vancouver': VANCOUVER})

    def test_a_row_without_a_value_cell_is_skipped(self):
        page = ('<table><tr><th class="gs_cith">Chicago</th></tr>'
                '<tr><th class="gs_cith">Vancouver</th><td>'
                f'<div class="gs_citr">{VANCOUVER}</div></td></tr></table>')
        self.assertEqual(cf.parse_cite_page(page)['formats'], {'vancouver': VANCOUVER})

    def test_duplicate_labels_keep_the_first(self):
        page = cite_page(rows=(('Chicago', 'first'), ('Chicago', 'second')))
        self.assertEqual(cf.parse_cite_page(page)['formats'], {'chicago': 'first'})

    def test_a_page_without_a_citation_table_reports_no_formats(self):
        page = ('<html><head>' + STYLE_BLOCK + '</head><body>'
                '<p>Our systems have detected unusual traffic.</p></body></html>')
        parsed = cf.parse_cite_page(page)
        self.assertEqual(parsed['formats'], {})
        self.assertEqual(parsed['bib_url'], '')

    def test_a_soup_input_is_accepted(self):
        from bs4 import BeautifulSoup
        parsed = cf.parse_cite_page(BeautifulSoup(default_page(), 'html.parser'))
        self.assertEqual(parsed['formats']['chicago'], CHICAGO)

    def test_bibtex_link_is_read_and_unescaped(self):
        page = cite_page(bib_url=BIB_URL.replace('&', '&amp;'))
        self.assertEqual(cf.parse_cite_page(page)['bib_url'], BIB_URL)

    def test_other_export_links_do_not_masquerade_as_bibtex(self):
        page = cite_page(bib_url='', extra_links=(
            ('EndNote', 'https://scholar.googleusercontent.com/scholar.enw?x=1'),
            ('RefMan', 'https://scholar.googleusercontent.com/scholar.ris?x=1'),
            ('RefWorks', 'https://scholar.googleusercontent.com/scholar.rfw?x=1'),
        ))
        self.assertEqual(cf.parse_cite_page(page)['bib_url'], '')

    def test_bibtex_link_after_the_other_links_is_still_found(self):
        page = cite_page(bib_url='', extra_links=(
            ('EndNote', 'https://scholar.googleusercontent.com/scholar.enw?x=1'),
        )) .replace('</div></body>',
                    f'<a class="gs_citi" href="{BIB_URL}">BibTeX</a></div></body>')
        self.assertEqual(cf.parse_cite_page(page)['bib_url'], BIB_URL)


# ---------------------------------------------------------------------------
# 3. BibTeX parsing
# ---------------------------------------------------------------------------

class BibtexParsingTests(unittest.TestCase):

    def setUp(self):
        self.bib = cf.parse_bibtex(BIBTEX_TEXT)

    def test_entry_type_and_key(self):
        self.assertEqual(self.bib['entry_type'], 'article')
        self.assertEqual(self.bib['key'], 'zhang2025llms')

    def test_structured_fields(self):
        self.assertEqual(self.bib['title'], TITLE)
        self.assertEqual(self.bib['journal'], VENUE_FULL)
        self.assertEqual(self.bib['volume'], '8')
        self.assertEqual(self.bib['number'], '1')
        self.assertEqual(self.bib['pages'], '55')
        self.assertEqual(self.bib['year'], '2025')
        self.assertEqual(self.bib['publisher'], 'Springer')

    def test_author_keeps_the_full_names_and_the_and_separator(self):
        self.assertTrue(self.bib['author'].startswith('Zhang, Jie and Bu, Haoyu'))
        self.assertTrue(self.bib['author'].endswith('and Meng, Dan'))
        self.assertEqual(self.bib['author'].count(' and '), 9)

    def test_multiline_values_are_collapsed(self):
        self.assertNotIn('\n', self.bib['author'])
        self.assertNotIn('  ', self.bib['author'])

    def test_nested_braces_are_kept(self):
        bib = cf.parse_bibtex('@article{k,\n title={The {DNA} of {RNA}},\n year={2024}\n}')
        self.assertEqual(bib['title'], 'The {DNA} of {RNA}')
        self.assertEqual(bib['year'], '2024')

    def test_quoted_values_are_supported(self):
        bib = cf.parse_bibtex('@inproceedings{k, title="Quoted title", year="1999"}')
        self.assertEqual(bib['title'], 'Quoted title')
        self.assertEqual(bib['year'], '1999')

    def test_bare_values_are_supported(self):
        bib = cf.parse_bibtex('@article{k, year=2024, volume=8}')
        self.assertEqual(bib['year'], '2024')
        self.assertEqual(bib['volume'], '8')

    def test_escaped_braces_inside_a_value_do_not_end_it(self):
        bib = cf.parse_bibtex('@article{k, title={A \\{brace\\} story}, year={2024}}')
        self.assertEqual(bib['title'], 'A \\{brace\\} story')
        self.assertEqual(bib['year'], '2024')

    def test_html_entities_are_unescaped(self):
        bib = cf.parse_bibtex('@article{k, title={Foo &amp; Bar}, year={2024}}')
        self.assertEqual(bib['title'], 'Foo & Bar')

    def test_empty_fields_are_dropped(self):
        bib = cf.parse_bibtex('@article{k, title={}, year={2024}}')
        self.assertNotIn('title', bib)
        self.assertEqual(bib['year'], '2024')

    def test_non_bibtex_text_yields_nothing(self):
        for text in ('', None, '<html><body>unusual traffic</body></html>',
                     'TY  - JOUR\nTI  - A title\nER  - '):
            with self.subTest(text=text):
                self.assertEqual(cf.parse_bibtex(text), {})

    def test_a_truncated_record_stops_instead_of_raising(self):
        bib = cf.parse_bibtex('@article{k, title={Never closed')
        self.assertEqual(bib['entry_type'], 'article')
        self.assertNotIn('title', bib)


# ---------------------------------------------------------------------------
# 4. Orchestration
# ---------------------------------------------------------------------------

class EnrichmentTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_path = os.path.join(self._tmp.name, cf.CACHE_FILENAME)

    def _run(self, citations, nav, **kwargs):
        kwargs.setdefault('cache_path', self.cache_path)
        kwargs.setdefault('get_soup', nav._get_soup)
        kwargs.setdefault('get_page', nav._get_page)
        kwargs.setdefault('print_fn', quiet)
        return cf.enrich_citations_with_cite_formats(citations, **kwargs)

    def test_each_citation_gets_its_own_formats(self):
        rows = [citation('cid-one'), citation('cid-two')]
        nav = _FakeNav(pages={
            cf.cite_path('cid-one'): cite_page(rows=(('Chicago', 'one'),)),
            cf.cite_path('cid-two'): cite_page(rows=(('Chicago', 'two'),)),
        })
        stats = self._run(rows, nav)
        self.assertEqual(stats['resolved'], 2)
        self.assertEqual(rows[0]['cite']['chicago'], 'one')
        self.assertEqual(rows[1]['cite']['chicago'], 'two')

    def test_the_five_formats_and_bibtex_land_in_the_cite_field(self):
        row = citation()
        nav = _FakeNav(pages={CID_PATH: default_page()}, bibs={BIB_URL: BIBTEX_TEXT})
        stats = self._run([row], nav)
        self.assertEqual(stats['bib_fetched'], 1)
        self.assertEqual(row['cite']['chicago'], CHICAGO)
        self.assertEqual(row['cite']['vancouver'], VANCOUVER)
        self.assertEqual(row['cite']['bibtex']['journal'], VENUE_FULL)
        self.assertEqual(row['cite']['bibtex']['volume'], '8')

    def test_citations_without_a_cid_issue_no_request(self):
        legacy = {'title': 'x', 'authors': 'a', 'venue': 'v', 'year': '2024',
                  'url': 'u', 'cites_id': '5'}
        nav = _FakeNav(pages={CID_PATH: default_page()})
        stats = self._run([legacy], nav)
        self.assertEqual(nav.calls, [])
        self.assertEqual(stats['missing_cid'], 1)
        self.assertNotIn('cite', legacy)

    def test_the_other_citation_fields_stay_byte_identical(self):
        row = citation()
        before = {key: row[key] for key in
                  ('title', 'authors', 'venue', 'year', 'url', 'cites_id', 'cid')}
        snapshot = json.dumps(before, sort_keys=True)
        nav = _FakeNav(pages={CID_PATH: default_page()}, bibs={BIB_URL: BIBTEX_TEXT})
        self._run([row], nav)
        after = {key: row[key] for key in before}
        self.assertEqual(json.dumps(after, sort_keys=True), snapshot)
        self.assertEqual(row['venue'], VENUE_FULL)

    def test_bibtex_is_skipped_without_a_get_page(self):
        row = citation()
        nav = _FakeNav(pages={CID_PATH: default_page()}, bibs={BIB_URL: BIBTEX_TEXT})
        stats = self._run([row], nav, get_page=None)
        self.assertEqual(nav.bib_calls, [])
        self.assertNotIn('bibtex', row['cite'])
        self.assertEqual(row['cite']['chicago'], CHICAGO)
        self.assertEqual(stats['bib_missing'], 1)

    def test_a_page_without_a_bibtex_link_is_not_a_failure(self):
        row = citation()
        nav = _FakeNav(pages={CID_PATH: cite_page(bib_url='')})
        stats = self._run([row], nav)
        self.assertEqual(stats['failed'], 0)
        self.assertEqual(stats['bib_missing'], 1)
        self.assertEqual(stats['resolved'], 1)

    def test_stats_describe_the_run(self):
        rows = [citation('cid-one'), citation('cid-two'), {'title': 'old'}]
        nav = _FakeNav(pages={cf.cite_path('cid-one'): default_page(),
                              cf.cite_path('cid-two'): default_page()},
                       bibs={BIB_URL: BIBTEX_TEXT})
        stats = self._run(rows, nav)
        self.assertEqual(stats['total'], 3)
        self.assertEqual(stats['with_cid'], 2)
        self.assertEqual(stats['missing_cid'], 1)
        self.assertEqual(stats['fetched'], 2)
        self.assertEqual(stats['resolved'], 2)
        self.assertEqual(stats['bib_fetched'], 2)
        self.assertEqual(stats['failed'], 0)

    def test_no_navigator_counts_the_citations_as_failed(self):
        stats = self._run([citation()], _FakeNav(), get_soup=None)
        self.assertEqual(stats['failed'], 1)
        self.assertEqual(stats['resolved'], 0)

    def test_non_dict_rows_are_ignored(self):
        stats = self._run(['not a dict', None], _FakeNav())
        self.assertEqual(stats['with_cid'], 0)
        self.assertEqual(stats['failed'], 0)


# ---------------------------------------------------------------------------
# 5. Graceful degradation
# ---------------------------------------------------------------------------

class GracefulDegradationTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_path = os.path.join(self._tmp.name, cf.CACHE_FILENAME)

    def _run(self, citations, nav, **kwargs):
        kwargs.setdefault('cache_path', self.cache_path)
        kwargs.setdefault('get_soup', nav._get_soup)
        kwargs.setdefault('get_page', nav._get_page)
        kwargs.setdefault('print_fn', quiet)
        return cf.enrich_citations_with_cite_formats(citations, **kwargs)

    def test_request_failure_is_counted_and_the_next_citation_continues(self):
        pages = {cf.cite_path('cid-ok'): default_page()}
        rows = [citation('cid-broken'), citation('cid-ok')]
        nav = _FakeNav(pages=pages, error_for='cid-broken')
        stats = self._run(rows, nav)
        self.assertEqual(stats['failed'], 1)
        self.assertEqual(stats['resolved'], 1)
        self.assertNotIn('cite', rows[0])
        self.assertEqual(rows[1]['cite']['chicago'], CHICAGO)

    def test_failed_page_is_not_cached_so_it_is_retried(self):
        rows = [citation('cid-broken')]
        self._run(rows, _FakeNav(error_for='cid-broken'))
        self.assertNotIn('cid-broken', cf.load_cite_cache(self.cache_path))

        retry_nav = _FakeNav(pages={cf.cite_path('cid-broken'): default_page()},
                             bibs={BIB_URL: BIBTEX_TEXT})
        stats = self._run([citation('cid-broken')], retry_nav)
        self.assertEqual(stats['fetched'], 1)
        self.assertEqual(stats['resolved'], 1)

    def test_blocked_page_is_a_failure_not_a_citation(self):
        blocked = ('<html><body><form id="gs_captcha_f">'
                   'Our systems have detected unusual traffic</form></body></html>')
        rows = [citation()]
        stats = self._run(rows, _FakeNav(pages={CID_PATH: blocked}))
        self.assertEqual(stats['failed'], 1)
        self.assertNotIn(CID, cf.load_cite_cache(self.cache_path))
        self.assertNotIn('cite', rows[0])

    def test_bibtex_failure_keeps_the_formats_and_retries_only_bibtex(self):
        rows = [citation()]
        nav = _FakeNav(pages={CID_PATH: default_page()}, bib_error=True)
        stats = self._run(rows, nav)
        self.assertEqual(stats['bib_failed'], 1)
        self.assertEqual(rows[0]['cite']['chicago'], CHICAGO)
        self.assertNotIn('bibtex', rows[0]['cite'])

        # The cached page is reused (no Cite-dialog request) and only the
        # signed BibTeX URL is tried again.
        retry_nav = _FakeNav(pages={CID_PATH: default_page()},
                             bibs={BIB_URL: BIBTEX_TEXT})
        rows2 = [citation()]
        stats2 = self._run(rows2, retry_nav)
        self.assertEqual(retry_nav.calls, [])
        self.assertEqual(retry_nav.bib_calls, [BIB_URL])
        self.assertEqual(stats2['bib_fetched'], 1)
        self.assertEqual(rows2[0]['cite']['bibtex']['volume'], '8')

    def test_unparseable_bibtex_is_counted_as_failed(self):
        rows = [citation()]
        nav = _FakeNav(pages={CID_PATH: default_page()},
                       bibs={BIB_URL: '<html>unusual traffic</html>'})
        stats = self._run(rows, nav)
        self.assertEqual(stats['bib_failed'], 1)
        self.assertNotIn('bibtex', rows[0]['cite'])

    def test_interrupt_propagates_and_keeps_what_was_fetched(self):
        calls = []

        def get_soup(path):
            calls.append(path)
            if len(calls) > 1:
                raise KeyboardInterrupt()
            return default_page()

        rows = [citation('cid-one'), citation('cid-two')]
        with self.assertRaises(KeyboardInterrupt):
            self._run(rows, _FakeNav(), get_soup=get_soup)
        self.assertIn('cid-one', cf.load_cite_cache(self.cache_path))
        self.assertEqual(rows[0]['cite']['chicago'], CHICAGO)


# ---------------------------------------------------------------------------
# 6. Cache / resume
# ---------------------------------------------------------------------------

class CiteCacheTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_path = os.path.join(self._tmp.name, cf.CACHE_FILENAME)

    def _run(self, citations, nav, **kwargs):
        kwargs.setdefault('cache_path', self.cache_path)
        kwargs.setdefault('get_soup', nav._get_soup)
        kwargs.setdefault('get_page', nav._get_page)
        kwargs.setdefault('print_fn', quiet)
        return cf.enrich_citations_with_cite_formats(citations, **kwargs)

    def test_cache_file_is_written_per_fetched_citation(self):
        nav = _FakeNav(pages={CID_PATH: default_page()}, bibs={BIB_URL: BIBTEX_TEXT})
        self._run([citation()], nav, fetched_at='2026-10-14T00:00:00')
        cache = read_json(self.cache_path)
        self.assertEqual(cache[CID]['formats']['chicago'], CHICAGO)
        self.assertEqual(cache[CID]['bib_url'], BIB_URL)
        self.assertEqual(cache[CID]['bibtex']['pages'], '55')
        self.assertEqual(cache[CID]['fetched_at'], '2026-10-14T00:00:00')

    def test_a_cached_page_is_not_requested_again(self):
        nav = _FakeNav(pages={CID_PATH: default_page()}, bibs={BIB_URL: BIBTEX_TEXT})
        self._run([citation()], nav)
        self.assertEqual(len(nav.calls), 1)
        self.assertEqual(len(nav.bib_calls), 1)

        rows = [citation()]
        second = _FakeNav()
        stats = self._run(rows, second)
        self.assertEqual(second.calls, [])
        self.assertEqual(second.bib_calls, [])
        self.assertEqual(stats['cache_hits'], 1)
        self.assertEqual(stats['bib_from_cache'], 1)
        self.assertEqual(rows[0]['cite']['chicago'], CHICAGO)
        self.assertEqual(rows[0]['cite']['bibtex']['journal'], VENUE_FULL)

    def test_cache_is_keyed_by_cid(self):
        nav = _FakeNav(pages={CID_PATH: default_page()},
                       bibs={BIB_URL: BIBTEX_TEXT})
        self._run([citation()], nav)
        self.assertEqual(list(read_json(self.cache_path)), [CID])

    def test_two_runs_accumulate_in_one_cache_file(self):
        nav = _FakeNav(pages={cf.cite_path('cid-one'): default_page()},
                       bibs={BIB_URL: BIBTEX_TEXT})
        self._run([citation('cid-one')], nav)
        nav2 = _FakeNav(pages={cf.cite_path('cid-two'): default_page()},
                        bibs={BIB_URL: BIBTEX_TEXT})
        self._run([citation('cid-two')], nav2)
        self.assertEqual(sorted(read_json(self.cache_path)), ['cid-one', 'cid-two'])

    def test_corrupt_cache_file_starts_empty(self):
        with open(self.cache_path, 'w', encoding='utf-8') as handle:
            handle.write('{not json')
        with patch('builtins.print'):
            self.assertEqual(cf.load_cite_cache(self.cache_path), {})

    def test_missing_cache_file_is_empty(self):
        self.assertEqual(cf.load_cite_cache(self.cache_path), {})

    def test_cache_entries_that_are_not_dicts_are_dropped(self):
        cf.save_cite_cache(self.cache_path, {CID: 'plain string'})
        self.assertEqual(cf.load_cite_cache(self.cache_path), {})

    def test_cache_write_is_atomic(self):
        """write_json_atomic leaves no .tmp file behind."""
        cf.save_cite_cache(self.cache_path, {CID: {'formats': {'mla': 'x'}}})
        leftovers = [name for name in os.listdir(self._tmp.name)
                     if name.endswith('.tmp')]
        self.assertEqual(leftovers, [])
        self.assertEqual(read_json(self.cache_path)[CID]['formats']['mla'], 'x')


# ---------------------------------------------------------------------------
# 7. CLI + fetcher wiring
# ---------------------------------------------------------------------------

class CliWiringTests(unittest.TestCase):

    def test_the_flag_is_unspecified_by_default(self):
        """None means "not given", so the config file / default decides."""
        from code.cli import parse_args
        args = parse_args(['--author', 'abc'])
        self.assertIsNone(args.cite_formats)

    def test_the_flag_enables_the_step(self):
        from code.cli import parse_args
        args = parse_args(['--author', 'abc', '--cite-formats'])
        self.assertTrue(args.cite_formats)

    def test_the_opt_out_disables_the_step(self):
        from code.cli import parse_args
        args = parse_args(['--author', 'abc', '--no-cite-formats'])
        self.assertFalse(args.cite_formats)

    def test_the_fetcher_has_it_on_by_default(self):
        """The Cite dialog is the only source of full authors + full venue."""
        fetcher = sc.PaperCitationFetcher('test-author', output_dir='.')
        self.assertTrue(fetcher.cite_formats)

    def test_the_fetcher_takes_an_explicit_override(self):
        self.assertTrue(sc.PaperCitationFetcher('test-author', output_dir='.',
                                                cite_formats=True).cite_formats)
        self.assertFalse(sc.PaperCitationFetcher('test-author', output_dir='.',
                                                 cite_formats=False).cite_formats)


class SaveOutputWiringTests(unittest.TestCase):
    """``_save_output`` enrichment is additive; the step is on unless opted out."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_path = os.path.join(self._tmp.name, cf.CACHE_FILENAME)
        self.pub = {
            'no': 1, 'title': TITLE, 'year': '2025', 'venue': VENUE_FULL,
            'authors': AUTHORS_ABBREV, 'num_citations': 7,
            'url': 'https://example.org/paper.html',
            'citedby_url': '/scholar?hl=en&cites=1234',
            'citation_for_view': '', 'venue_full': '', 'venue_full_source': '',
        }

    def _fetcher(self, cite_formats=None):
        # cite_formats=None means "do not pass it at all", i.e. exercise the default.
        kwargs = {} if cite_formats is None else {'cite_formats': cite_formats}
        fetcher = sc.PaperCitationFetcher('test-author', output_dir=self._tmp.name,
                                          **kwargs)
        fetcher._paper_states = {}
        fetcher._mid_paper_state = {}
        fetcher._profile_data = {'publications': [dict(self.pub)]}
        fetcher._session_ctx = SimpleNamespace(curl_page_count=1, total_page_count=1)
        fetcher._papers_fetched_count = 0
        fetcher._run_new_citations_total = 0
        return fetcher

    def _save(self, fetcher, citations, nav):
        results = [{'pub': dict(self.pub), 'citations': citations}]
        with patch.object(sc.scholarly, '_Scholarly__nav', nav), \
             patch('sys.stdout', new_callable=StringIO) as stdout:
            fetcher._save_output(results)
        return read_json(fetcher.out_json), stdout.getvalue()

    def test_output_keeps_the_cite_field_when_enabled(self):
        nav = _FakeNav(pages={CID_PATH: default_page()}, bibs={BIB_URL: BIBTEX_TEXT})
        saved, output = self._save(self._fetcher(True), [citation()], nav)
        cite = saved['papers'][0]['citations'][0]['cite']
        self.assertEqual(cite['chicago'], CHICAGO)
        self.assertEqual(cite['bibtex']['volume'], '8')
        self.assertIn('citation formats resolved: 1/1', output)
        self.assertTrue(os.path.exists(self.cache_path))

    def test_the_default_fetcher_writes_the_cite_field(self):
        """No flag at all: new rows must still carry the Cite-dialog data."""
        nav = _FakeNav(pages={CID_PATH: default_page()}, bibs={BIB_URL: BIBTEX_TEXT})
        saved, output = self._save(self._fetcher(), [citation()], nav)
        cite = saved['papers'][0]['citations'][0]['cite']
        self.assertEqual(cite['chicago'], CHICAGO)
        # the BibTeX record carries the full author list of the citing paper
        self.assertIn('Zhang, Jie and Bu, Haoyu', cite['bibtex']['author'])
        self.assertIn('and Meng, Dan', cite['bibtex']['author'])
        self.assertIn('citation formats resolved: 1/1', output)

    def test_the_derived_venue_key_follows_the_dialog_venue(self):
        """The BibTeX journal is untruncated, so it replaces the list-page key."""
        row = citation()
        self.assertEqual(row['venue_key'], VENUE_FULL)  # from the list page, pre-dialog
        nav = _FakeNav(pages={CID_PATH: default_page()}, bibs={BIB_URL: BIBTEX_TEXT})
        saved, _ = self._save(self._fetcher(), [row], nav)
        saved_row = saved['papers'][0]['citations'][0]
        self.assertEqual(saved_row['venue_key'], 'Cybersecurity')  # bibtex journal
        self.assertEqual(saved_row['venue_key_source'], 'raw')

    def test_a_known_conference_booktitle_yields_a_normalized_key(self):
        bibtex = BIBTEX_TEXT.replace('journal={Cybersecurity}',
                                     'booktitle={Proceedings of the 2024 Conference on '
                                     'Empirical Methods in Natural Language Processing}')
        nav = _FakeNav(pages={CID_PATH: default_page()}, bibs={BIB_URL: bibtex})
        saved, _ = self._save(self._fetcher(), [citation()], nav)
        saved_row = saved['papers'][0]['citations'][0]
        # the venue-string year (the conference edition) wins over the record year
        self.assertEqual(saved_row['venue_key'], 'EMNLP2024')
        self.assertEqual(saved_row['venue_key_source'], 'normalized')

    def test_cid_is_persisted_in_the_output(self):
        nav = _FakeNav(pages={CID_PATH: default_page()}, bibs={BIB_URL: BIBTEX_TEXT})
        saved, _ = self._save(self._fetcher(True), [citation()], nav)
        row = saved['papers'][0]['citations'][0]
        self.assertEqual(row['cid'], CID)
        self.assertEqual(row['cites_id'], '1234')
        self.assertEqual(row['authors'], AUTHORS_ABBREV)

    def test_nothing_is_requested_when_the_flag_is_off(self):
        nav = _FakeNav(pages={CID_PATH: default_page()}, bibs={BIB_URL: BIBTEX_TEXT})
        saved, output = self._save(self._fetcher(False), [citation()], nav)
        self.assertEqual(nav.calls, [])
        self.assertNotIn('cite', saved['papers'][0]['citations'][0])
        self.assertNotIn('citation formats resolved', output)

    def test_a_missing_navigator_still_saves_the_output(self):
        saved, output = self._save(self._fetcher(True), [citation()], None)
        self.assertNotIn('cite', saved['papers'][0]['citations'][0])
        self.assertIn('Cite dialogs unavailable', output)

    def test_an_interrupt_during_the_format_step_still_writes_the_output(self):
        """Ctrl+C is how a rate-limited run is meant to end; keep the citations."""
        fetcher = self._fetcher(True)
        with patch.object(fetcher, '_enrich_cite_formats',
                          side_effect=KeyboardInterrupt), \
             patch('sys.stdout', new_callable=StringIO) as stdout:
            fetcher._save_output([{'pub': dict(self.pub), 'citations': [citation()]}])
        saved = read_json(fetcher.out_json)
        self.assertEqual(len(saved['papers'][0]['citations']), 1)
        self.assertIn('Citation-format step interrupted', stdout.getvalue())

    def test_a_failing_format_step_still_writes_the_output(self):
        fetcher = self._fetcher(True)
        with patch.object(fetcher, '_enrich_cite_formats',
                          side_effect=OSError('disk full')), \
             patch('sys.stdout', new_callable=StringIO) as stdout:
            fetcher._save_output([{'pub': dict(self.pub), 'citations': [citation()]}])
        saved = read_json(fetcher.out_json)
        self.assertEqual(len(saved['papers'][0]['citations']), 1)
        self.assertIn('citation-format step failed', stdout.getvalue())

    def test_output_venue_is_never_rewritten(self):
        nav = _FakeNav(pages={CID_PATH: cite_page(rows=(('Vancouver', VANCOUVER),))},
                       bibs={BIB_URL: BIBTEX_TEXT})
        saved, _ = self._save(self._fetcher(True), [citation()], nav)
        row = saved['papers'][0]['citations'][0]
        self.assertEqual(row['venue'], VENUE_FULL)
        self.assertEqual(saved['papers'][0]['pub']['venue'], VENUE_FULL)


if __name__ == '__main__':
    unittest.main()
