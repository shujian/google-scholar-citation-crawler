import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.conftest import (
    FetcherTestCase, scholarly_mod, _DummyNav, _DummyIterator,
    patch, StringIO, json, tempfile,
)
import types
import unittest
import scholar_citation

class ScholarPatchAndIdentityTests(FetcherTestCase):
    def test_patch_scholarly_logs_request_url_before_page_fetch(self):
        nav = scholarly_mod.scholarly._Scholarly__nav
        seen_requests = []

        def fake_get_page(pagerequest, premium=False):
            seen_requests.append((pagerequest, premium))
            return {"ok": True}

        nav._get_page = fake_get_page
        self.fetcher._patch_scholarly()

        with patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            result = nav._get_page("/scholar?start=10&cites=123", premium=False)

        output = fake_stdout.getvalue()
        self.assertEqual(result, {"ok": True})
        self.assertEqual(seen_requests, [("/scholar?start=10&cites=123", False)])
        self.assertIn("Request URL: https://scholar.google.com/scholar?start=10&cites=123", output)
        # referer equals last_scholar_url (natural navigation) → not shown
        self.assertNotIn("referer:", output)

    def test_fetch_basics_logs_profile_request_url(self):
        author_fetcher = scholar_citation.AuthorProfileFetcher("test-author", output_dir=".", delay_scale=0)
        search_calls = []
        fill_calls = []

        def fake_search(author_id):
            search_calls.append(author_id)
            return {"scholar_id": author_id}

        def fake_fill(author, sections=None):
            fill_calls.append(tuple(sections or []))
            nav = scholarly_mod.scholarly._Scholarly__nav
            nav._get_page(f"/citations?user={author['scholar_id']}&hl=en")
            return {
                "name": "Author",
                "affiliation": "Aff",
                "citedby": 12,
                "cites_per_year": {2026: 3},
                "hindex": 4,
                "i10index": 2,
            }

        nav = scholarly_mod.scholarly._Scholarly__nav
        nav._session1.headers['referer'] = "https://scholar.google.com/"
        nav._session2.headers['referer'] = "https://scholar.google.com/"
        nav._get_page = lambda pagerequest, premium=False: None

        with patch.object(scholar_citation.scholarly, "search_author_id", side_effect=fake_search), \
             patch.object(scholar_citation.scholarly, "fill", side_effect=fake_fill), \
             patch("scholar_citation.rand_delay", return_value=0), \
             patch("scholar_citation.time.sleep", return_value=None), \
             patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            paper_fetcher = scholar_citation.PaperCitationFetcher("test-author", output_dir=".")
            paper_fetcher._delay_scale = 0
            paper_fetcher._patch_scholarly()
            basics, fetched = author_fetcher.fetch_basics()

        output = fake_stdout.getvalue()
        self.assertTrue(fetched)
        self.assertEqual(basics["name"], "Author")
        # search_author_id is no longer called; stub is constructed locally
        self.assertEqual(search_calls, [])
        self.assertEqual(fill_calls, [("basics", "indices", "counts")])
        self.assertIn("Request URL: https://scholar.google.com/citations?user=test-author&hl=en", output)

    def test_extract_citation_info_keeps_cites_id_and_fallback_year(self):
        info = self.fetcher._extract_citation_info(
            {
                "bib": {"title": "Paper", "author": ["A", "B"], "venue": "Venue", "pub_year": "N/A"},
                "pub_url": "https://example.com/cite",
                "cites_id": ["123", "456"],
            },
            fallback_year=2025,
        )

        self.assertEqual(info["title"], "Paper")
        self.assertEqual(info["authors"], "A, B")
        self.assertEqual(info["venue"], "Venue")
        self.assertEqual(info["year"], "2025")
        self.assertEqual(info["url"], "https://example.com/cite")
        self.assertEqual(info["cites_id"], "123,456")

    def test_extract_citation_info_falls_back_to_citedby_url(self):
        # scholarly _scholar_pub does not set cites_id; only citedby_url is present.
        info = self.fetcher._extract_citation_info(
            {
                "bib": {"title": "Paper", "author": ["A"], "venue": "Venue", "pub_year": "2024"},
                "pub_url": "https://example.com/cite",
                "citedby_url": "/scholar?cites=7890123456789012345&as_sdt=5,33&hl=en",
            }
        )
        self.assertEqual(info["cites_id"], "7890123456789012345")

    def test_citation_identity_prefers_cites_id_with_metadata_fallback(self):
        citation = {
            "title": "Paper",
            "authors": "A",
            "venue": "Venue",
            "year": "2025",
            "url": "u",
            "cites_id": "cid-1",
        }

        keys = self.fetcher._citation_identity_keys(citation)

        self.assertEqual(keys[0], "cites_id\tcid-1")
        self.assertIn("meta\tpaper\tvenue", keys)
        self.assertEqual(self.fetcher._citation_identity_key(citation), "cites_id\tcid-1")

    def test_overlay_citations_matches_legacy_cache_to_new_cites_id(self):
        old_citations = [
            {"title": "Paper", "authors": "A", "venue": "Venue", "year": "2025", "url": "old-u"},
        ]
        refreshed_citations = [
            {"title": "Paper", "authors": "A", "venue": "Venue", "year": "2025", "url": "new-u", "cites_id": "cid-1"},
        ]

        merged = self.fetcher._overlay_citations_by_identity(old_citations, refreshed_citations)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["url"], "new-u")
        self.assertEqual(merged[0]["cites_id"], "cid-1")

        curl = "curl 'https://scholar.google.com/scholar?cites=123' -b 'SID=abc; HSID=def'"

        injected = self.fetcher._inject_cookies_from_curl(curl)

        nav = scholarly_mod.scholarly._Scholarly__nav
        self.assertEqual(injected, 2)
        self.assertEqual(self.fetcher._injected_cookies, {'SID': 'abc', 'HSID': 'def'})
        self.assertEqual(self.fetcher._injected_header_overrides, {})
        self.assertEqual(nav._session1.cookies['SID'], 'abc')
        self.assertEqual(nav._session2.cookies['HSID'], 'def')
        self.assertEqual(nav._session1.headers['referer'], self.fetcher._last_scholar_url)

    def test_inject_curl_persists_allowlisted_headers(self):
        curl = (
            "curl 'https://scholar.google.com/scholar?cites=123' "
            "-b 'SID=abc' "
            "-H 'Accept-Language: en-US,en;q=0.9' "
            "-H 'sec-ch-ua-platform: \"Windows\"' "
            "-H 'Priority: u=1, i'"
        )

        injected = self.fetcher._inject_cookies_from_curl(curl)

        nav = scholarly_mod.scholarly._Scholarly__nav
        self.assertEqual(injected, 1)
        self.assertEqual(
            self.fetcher._injected_header_overrides,
            {
                'accept-language': 'en-US,en;q=0.9',
                'sec-ch-ua-platform': '"Windows"',
                'priority': 'u=1, i',
            },
        )
        self.assertEqual(nav._session1.headers['accept-language'], 'en-US,en;q=0.9')
        self.assertEqual(nav._session1.headers['sec-ch-ua-platform'], '"Windows"')
        self.assertEqual(nav._session2.headers['priority'], 'u=1, i')
        self.assertEqual(nav._session1.headers['referer'], self.fetcher._last_scholar_url)

    def test_inject_curl_adopts_full_browser_fingerprint(self):
        # user-agent + sec-ch-ua + sec-ch-ua-full-version-list, all Edge 151:
        # the whole trio must be adopted so the fingerprint matches the browser
        # that issued the cookies.
        curl = (
            "curl 'https://scholar.google.com/scholar?cites=123' "
            "-b 'SID=abc' "
            "-H 'user-agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 "
            "Safari/537.36 Edg/151.0.0.0' "
            "-H 'sec-ch-ua: \"Not=A?Brand\";v=\"99\", \"Microsoft Edge\";v=\"151\", \"Chromium\";v=\"151\"' "
            "-H 'sec-ch-ua-full-version-list: \"Not=A?Brand\";v=\"99.0.0.0\", "
            "\"Microsoft Edge\";v=\"151.0.4129.72\", \"Chromium\";v=\"151.0.7922.76\"'"
        )

        injected = self.fetcher._inject_cookies_from_curl(curl)

        nav = scholarly_mod.scholarly._Scholarly__nav
        self.assertEqual(injected, 1)
        self.assertIn('user-agent', self.fetcher._injected_header_overrides)
        self.assertIn('sec-ch-ua', self.fetcher._injected_header_overrides)
        self.assertIn('sec-ch-ua-full-version-list', self.fetcher._injected_header_overrides)
        self.assertIn('Edg/151.0.0.0', nav._session1.headers['user-agent'])
        self.assertEqual(nav._session2.headers['sec-ch-ua'], self.fetcher._injected_header_overrides['sec-ch-ua'])

    def test_inject_curl_partial_identity_falls_back_to_default(self):
        # Only user-agent + sec-ch-ua present, sec-ch-ua-full-version-list
        # missing → the whole trio is dropped (all-or-nothing), so the
        # hardcoded default fingerprint stays in force.
        curl = (
            "curl 'https://scholar.google.com/scholar?cites=123' "
            "-b 'SID=abc' "
            "-H 'user-agent: Mozilla/5.0 Chrome/151.0.0.0 Safari/537.36 Edg/151.0.0.0' "
            "-H 'sec-ch-ua: \"Microsoft Edge\";v=\"151\", \"Chromium\";v=\"151\"'"
        )

        self.fetcher._inject_cookies_from_curl(curl)

        self.assertNotIn('user-agent', self.fetcher._injected_header_overrides)
        self.assertNotIn('sec-ch-ua', self.fetcher._injected_header_overrides)
        self.assertNotIn('sec-ch-ua-full-version-list', self.fetcher._injected_header_overrides)

    def test_inject_curl_identity_version_mismatch_falls_back_to_default(self):
        # All three present but user-agent is Chrome/145 while sec-ch-ua is
        # Edge/151 → contradictory fingerprint, dropped entirely.
        curl = (
            "curl 'https://scholar.google.com/scholar?cites=123' "
            "-b 'SID=abc' "
            "-H 'user-agent: Mozilla/5.0 Chrome/145.0.0.0 Safari/537.36' "
            "-H 'sec-ch-ua: \"Microsoft Edge\";v=\"151\", \"Chromium\";v=\"151\"' "
            "-H 'sec-ch-ua-full-version-list: \"Microsoft Edge\";v=\"151.0.4129.72\"'"
        )

        self.fetcher._inject_cookies_from_curl(curl)

        self.assertNotIn('user-agent', self.fetcher._injected_header_overrides)
        self.assertNotIn('sec-ch-ua', self.fetcher._injected_header_overrides)
        self.assertNotIn('sec-ch-ua-full-version-list', self.fetcher._injected_header_overrides)

    def test_inject_curl_ignores_disallowed_headers(self):
        curl = (
            "curl 'https://scholar.google.com/scholar?cites=123' "
            "-b 'SID=abc' "
            "-H 'Referer: https://example.com/' "
            "-H 'Host: scholar.google.com' "
            "-H 'cookie: injected=yes'"
        )

        self.fetcher._inject_cookies_from_curl(curl)

        nav = scholarly_mod.scholarly._Scholarly__nav
        self.assertEqual(self.fetcher._injected_header_overrides, {})
        self.assertNotIn('host', nav._session1.headers)
        self.assertEqual(nav._session1.headers['referer'], self.fetcher._last_scholar_url)

    def test_inject_curl_without_cookie_still_fails(self):
        curl = "curl 'https://scholar.google.com/scholar?cites=123' -H 'Accept-Language: en-US,en;q=0.9'"

        injected = self.fetcher._inject_cookies_from_curl(curl)

        self.assertEqual(injected, 0)
        self.assertEqual(self.fetcher._injected_cookies, {})
        self.assertEqual(self.fetcher._injected_header_overrides, {})

    def test_parse_args_mentions_selected_headers_for_interactive_captcha(self):
        with patch.object(sys, 'argv', ['scholar_citation.py', '--author', 'test', '--help']):
            with self.assertRaises(SystemExit), patch('sys.stdout', new_callable=StringIO) as fake_stdout:
                scholar_citation.parse_args()

        help_text = fake_stdout.getvalue()
        self.assertIn('inject fresh cookies and selected headers', help_text)



import bs4
import types as _types
from crawler.scholarly_session import (
    citation_page_evidence,
    format_page_evidence,
    is_citation_list_url,
    record_zero_row_citation_page,
)


class CitationPageEvidenceTests(unittest.TestCase):
    """Page-level diagnostics for citation pages that return no rows."""

    @staticmethod
    def _soup(html):
        return bs4.BeautifulSoup(html, 'html.parser')

    def test_is_citation_list_url_matches_direct_and_year_urls(self):
        self.assertTrue(is_citation_list_url('/scholar?oi=bibs&hl=en&cites=1,2'))
        self.assertTrue(is_citation_list_url(
            '/scholar?as_ylo=2021&as_yhi=2021&hl=en&as_sdt=2005&cites=9&scipsc='))
        self.assertFalse(is_citation_list_url('/citations?user=X&hl=en'))
        self.assertFalse(is_citation_list_url(''))
        self.assertFalse(is_citation_list_url(None))

    def test_full_page_reports_rows_container_and_no_markers(self):
        rows = ''.join(
            '<div class="gs_r gs_or gs_scl"><div class="gs_ri">'
            f'<h3 class="gs_rt">Title {idx}</h3></div></div>'
            for idx in range(3)
        )
        evidence = citation_page_evidence(
            self._soup(f'<div id="gs_res_ccl_mid">{rows}</div>'))
        self.assertEqual(evidence['rows'], 3)
        self.assertTrue(evidence['results_container'])
        self.assertEqual(evidence['markers'], [])
        self.assertEqual(format_page_evidence(evidence), '')

    def test_empty_results_page_reports_banner_and_no_results_marker(self):
        evidence = citation_page_evidence(self._soup(
            '<div id="gs_res_ccl_mid"></div>'
            '<div class="gs_ab_mdw">Your search did not match any documents.</div>'))
        self.assertEqual(evidence['rows'], 0)
        self.assertIn('did not match', evidence['result_banner'])
        self.assertIn('no_results', evidence['markers'])

    def test_captcha_page_is_marked_and_reports_no_container(self):
        evidence = citation_page_evidence(self._soup('<div id="gs_captcha_ccl"></div>'))
        self.assertEqual(evidence['rows'], 0)
        self.assertFalse(evidence['results_container'])
        self.assertIn('captcha', evidence['markers'])

    def test_interstitial_and_consent_markers(self):
        cases = [
            ('<html><body>Our systems have detected unusual traffic</body></html>',
             'unusual_traffic'),
            ('<html><body>Before you continue to Google</body></html>', 'consent'),
        ]
        for html, marker in cases:
            with self.subTest(marker=marker):
                evidence = citation_page_evidence(self._soup(html))
                self.assertIn(marker, evidence['markers'])
                self.assertIn(marker, format_page_evidence(evidence))

    def test_evidence_returns_none_instead_of_raising(self):
        self.assertIsNone(citation_page_evidence(None))
        # An object without the soup API must not break the fetch path.
        self.assertIsNone(citation_page_evidence(object()))

    def test_format_page_evidence_lists_every_signal(self):
        text = format_page_evidence({
            'rows': 0, 'results_container': False,
            'result_banner': 'no results', 'markers': ['captcha'],
        })
        self.assertIn('no results container', text)
        self.assertIn("banner='no results'", text)
        self.assertIn('markers=captcha', text)
        self.assertEqual(format_page_evidence(None), '')

    def test_record_zero_row_citation_page_logs_and_stores_evidence(self):
        ctx = _types.SimpleNamespace(last_page_evidence=None)
        soup = self._soup('<html><body>Our systems have detected unusual traffic</body></html>')
        with patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            evidence = record_zero_row_citation_page(ctx, soup)
        output = fake_stdout.getvalue()
        self.assertIn('Page items: 0 (no citation rows)', output)
        self.assertIn('markers=unusual_traffic', output)
        self.assertIs(ctx.last_page_evidence, evidence)

    def test_record_zero_row_citation_page_without_soup_still_logs(self):
        ctx = _types.SimpleNamespace(last_page_evidence='stale')
        with patch("sys.stdout", new_callable=StringIO) as fake_stdout:
            evidence = record_zero_row_citation_page(ctx, None)
        self.assertIsNone(evidence)
        self.assertIsNone(ctx.last_page_evidence)
        self.assertIn('Page items: 0 (no citation rows)', fake_stdout.getvalue())


if __name__ == '__main__':
    unittest.main()
