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
        self.assertIn("referer: https://scholar.google.com/citations?user=test-author&hl=en", output)

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
        self.assertEqual(search_calls, ["test-author"])
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

    def test_inject_curl_ignores_disallowed_headers(self):
        curl = (
            "curl 'https://scholar.google.com/scholar?cites=123' "
            "-b 'SID=abc' "
            "-H 'User-Agent: injected-agent' "
            "-H 'Referer: https://example.com/' "
            "-H 'Host: scholar.google.com' "
            "-H 'sec-fetch-site: cross-site'"
        )

        self.fetcher._inject_cookies_from_curl(curl)

        nav = scholarly_mod.scholarly._Scholarly__nav
        self.assertEqual(self.fetcher._injected_header_overrides, {})
        self.assertNotIn('user-agent', nav._session1.headers)
        self.assertNotIn('host', nav._session1.headers)
        self.assertNotIn('sec-fetch-site', nav._session1.headers)
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



if __name__ == '__main__':
    unittest.main()
