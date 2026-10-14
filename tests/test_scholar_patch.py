import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.conftest import (
    FetcherTestCase, scholarly_mod, _DummyNav, _DummyIterator,
    patch, StringIO, json, tempfile,
)
import types
import unittest
from code import scholar_citation

class ScholarPatchAndIdentityTests(FetcherTestCase):
    def test_curl_save_path_defaults_to_output_dir(self):
        """The fixture redirects the save path; the real default must stay output_dir/curl.txt."""
        with tempfile.TemporaryDirectory() as d:
            fetcher = scholar_citation.PaperCitationFetcher("test-author", output_dir=d)
            self.assertEqual(fetcher._curl_save_path, os.path.join(d, "curl.txt"))

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
             patch("code.scholar_citation.rand_delay", return_value=0), \
             patch("code.scholar_citation.time.sleep", return_value=None), \
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
from code.scholarly_session import (
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


class StartupRobustnessTests(FetcherTestCase):
    """NO_PROXY normalisation and patch self-check (startup hardening)."""

    def test_sanitize_no_proxy_unbrackets_ipv6_entries(self):
        from code.common import sanitize_no_proxy
        env = {'NO_PROXY': 'localhost,127.0.0.1,::1,[::1]',
               'no_proxy': 'localhost,[2001:db8::1]:8080'}
        changed = sanitize_no_proxy(env)
        self.assertEqual(changed, 2)
        self.assertEqual(env['NO_PROXY'], 'localhost,127.0.0.1,::1,::1')
        # A port suffix is dropped: httpx cannot express IPv6+port in NO_PROXY.
        self.assertEqual(env['no_proxy'], 'localhost,2001:db8::1')

    def test_sanitize_no_proxy_is_idempotent_and_leaves_other_entries(self):
        from code.common import sanitize_no_proxy
        env = {'NO_PROXY': 'localhost,127.0.0.1,::1'}
        self.assertEqual(sanitize_no_proxy(env), 0)
        self.assertEqual(env['NO_PROXY'], 'localhost,127.0.0.1,::1')
        self.assertEqual(sanitize_no_proxy({}), 0)

    def test_httpx_client_survives_bracketed_no_proxy_after_sanitize(self):
        """Regression: httpx 0.27.2 raises InvalidURL on a bracketed IPv6 entry."""
        import httpx
        from code.common import sanitize_no_proxy
        with patch.dict(os.environ, {'NO_PROXY': 'localhost,[::1]'}, clear=False):
            sanitize_no_proxy()
            with httpx.Client(http2=True, follow_redirects=True):
                pass

    def test_assert_scholarly_patches_active_reports_ineffective_hooks(self):
        from code.scholarly_session import assert_scholarly_patches_active

        def hook():
            return None

        assert_scholarly_patches_active([('ok', hook, hook), ('retries', 1, 1)])
        with self.assertRaises(RuntimeError) as ctx:
            assert_scholarly_patches_active([('nav._get_page', hook, hook), ('nav._max_retries', 5, 1)])
        self.assertIn('nav._max_retries', str(ctx.exception))

    def test_probe_failure_clears_previous_paper_histogram(self):
        """A failed probe must not leave the previous paper's counts behind.

        scholar_citation writes ctx.probed_year_counts into every paper's fetch
        context unconditionally, so a stale histogram makes the next paper skip
        or mis-judge whole years.
        """
        from code.scholarly_session import probe_citation_start_year

        nav = scholarly_mod.scholarly._Scholarly__nav
        ctx = scholar_citation.SessionContext(author_id="test-author")

        hist_html = (
            '<html><body>'
            '<div class="gs_rs_hist_dialog-g_bar_wrapper">'
            '<a class="gs_hist_g_a" data-year="2021" data-count="7"></a>'
            '</div></body></html>'
        )

        def soup_ok(url):
            from bs4 import BeautifulSoup
            return BeautifulSoup(hist_html, 'html.parser')

        nav._get_soup = soup_ok
        start = probe_citation_start_year("/scholar?cites=1", ctx, num_citations=7)
        self.assertEqual(start, 2021)
        self.assertEqual(ctx.probed_year_counts, {2021: 7})
        self.assertTrue(ctx.probed_year_count_complete)

        def soup_blocked(url):
            raise RuntimeError("blocked")

        nav._get_soup = soup_blocked
        with patch("sys.stdout", new_callable=StringIO):
            start = probe_citation_start_year("/scholar?cites=2", ctx, num_citations=9)
        self.assertIsNone(start)
        self.assertIsNone(ctx.probed_year_counts)
        self.assertFalse(ctx.probed_year_count_complete)

    def test_citation_year_normalization_uses_one_sentinel(self):
        """'NA', '', None and friends all mean "no year" and must agree.

        The old readers disagreed ('N/A' vs 'NA' vs ''), so the same record could
        count as unknown in one place and as a year in another.
        """
        from code.citation_cache import is_unknown_year, normalize_citation_year, year_count_map
        from code.citation_identity import extract_citation_info

        for value in ('', 'NA', 'n/a', 'N/A', '?', None, 'none'):
            self.assertTrue(is_unknown_year(value), value)
            self.assertEqual(normalize_citation_year(value), 'N/A')

        self.assertEqual(normalize_citation_year(None, fallback_year=2025), '2025')
        self.assertEqual(normalize_citation_year('2024', fallback_year=2025), '2024')
        self.assertFalse(is_unknown_year('2024'))

        counts = year_count_map([{"year": "2024"}, {"year": "NA"}, {"year": ""},
                                 {"year": "N/A"}, {"year": None}])
        self.assertEqual(counts, {2024: 1})

        info = extract_citation_info({"bib": {"title": "T"}}, fallback_year=2025)
        self.assertEqual(info["year"], "2025")
        self.assertEqual(extract_citation_info({"bib": {"title": "T"}})["year"], "N/A")

    def test_real_scholarly_still_exposes_every_patched_hook(self):
        """Contract test against the installed scholarly.

        The rest of the suite replaces scholarly with a stub, so a scholarly
        upgrade that renames one of these internals would keep the tests green
        while the crawler lost headers, waits or pagination parsing.
        """
        import importlib
        stubbed = {name: sys.modules.pop(name)
                   for name in list(sys.modules)
                   if name == 'scholarly' or name.startswith('scholarly.')}
        try:
            real_scholarly = importlib.import_module('scholarly')
            pub_parser = importlib.import_module('scholarly.publication_parser')
            # The crawler patches the singleton instance (`from scholarly import
            # scholarly`), not the module.
            nav = real_scholarly.scholarly._Scholarly__nav

            for attribute in ('_get_page', '_new_session', '_set_retries',
                              '_TIMEOUT', '_max_retries', '_session1', '_session2',
                              'pm1', 'pm2'):
                self.assertTrue(hasattr(nav, attribute),
                                f"scholarly.Navigator lost {attribute}")
            self.assertTrue(hasattr(nav.pm1, '_handle_captcha2'))
            for attribute in ('__init__', '__next__', '_load_url'):
                self.assertTrue(hasattr(pub_parser._SearchScholarIterator, attribute),
                                f"_SearchScholarIterator lost {attribute}")
            self.assertTrue(hasattr(pub_parser.PublicationParser, '_citation_pub'))
        finally:
            for name in list(sys.modules):
                if name == 'scholarly' or name.startswith('scholarly.'):
                    del sys.modules[name]
            sys.modules.update(stubbed)

    def test_parse_args_rejects_out_of_range_values(self):
        from code.cli import parse_args
        for argv in (['--author', 'a', '--skip', '-1'],
                     ['--author', 'a', '--limit', '0'],
                     ['--author', 'a', '--accelerate', '0'],
                     ['--author', 'a', '--accelerate', '-2']):
            with patch('sys.stderr', new_callable=StringIO), self.assertRaises(SystemExit):
                parse_args(argv)

    def test_parse_args_accepts_valid_bounds(self):
        from code.cli import parse_args
        args = parse_args(['--author', 'a', '--skip', '0', '--limit', '1',
                           '--accelerate', '0.5', '-v'])
        self.assertEqual((args.skip, args.limit, args.accelerate), (0, 1, 0.5))
        self.assertTrue(args.verbose)

    def test_has_pending_work_loads_output_state_before_deciding(self):
        """main() asks this before run(); without loading the state every paper
        looks missing and the 'no changes → skip' shortcut can never fire."""
        pub = {"no": 1, "title": "Done Paper", "num_citations": 5,
               "year": "2024", "venue": "V"}
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(__import__('shutil').rmtree, tmpdir, ignore_errors=True)
        self.fetcher.profile_json = os.path.join(tmpdir, "author_test-author_profile.json")
        self.fetcher.out_json = os.path.join(tmpdir, "author_test-author_paper_citations.json")
        with open(self.fetcher.profile_json, "w", encoding="utf-8") as f:
            json.dump({"publications": [pub]}, f)
        with open(self.fetcher.out_json, "w", encoding="utf-8") as f:
            json.dump({"papers": [{
                "pub": pub,
                "citations": [{"title": f"C{idx}", "authors": "A", "venue": "V",
                               "year": "2024", "url": f"u{idx}"} for idx in range(5)],
                "_fetch_state": {
                    "title": pub["title"], "fetch_strategy": "direct",
                    "num_citations_on_scholar": 5, "complete_fetch_attempt": True,
                    "direct_fetch_diagnostics": {
                        "scholar_total": 5, "cached_total": 5, "seen_total": 5,
                        "dedup_count": 0, "termination_reason": "iterator_exhausted"},
                },
            }]}, f)

        self.assertFalse(hasattr(self.fetcher, "_paper_states"))
        self.assertFalse(self.fetcher.has_pending_work())
        self.assertTrue(getattr(self.fetcher, "_paper_states"))

    def test_patch_scholarly_verifies_all_hook_points(self):
        nav = scholarly_mod.scholarly._Scholarly__nav
        nav._get_page = lambda pagerequest, premium=False: None
        self.fetcher._patch_scholarly()
        # The patched hooks must be the ones patch_scholarly installed.
        self.assertEqual(getattr(nav, '_max_retries', None), 1)
        self.assertEqual(getattr(nav, '_TIMEOUT', None), 30)
        self.assertIsNot(nav._get_page, None)
        self.assertTrue(callable(nav._new_session))
        self.assertTrue(callable(nav.pm1._handle_captcha2))

    def test_patch_scholarly_fails_loudly_when_a_hook_is_replaced(self):
        """A scholarly upgrade that ignores _set_retries must abort startup."""
        nav = scholarly_mod.scholarly._Scholarly__nav
        original_set_retries = nav._set_retries

        def ignore_retries(retries):
            return None

        nav._set_retries = ignore_retries
        try:
            with self.assertRaises(RuntimeError) as ctx:
                self.fetcher._patch_scholarly()
            self.assertIn('nav._max_retries', str(ctx.exception))
        finally:
            nav._set_retries = original_set_retries

    def test_probe_counts_one_page_through_real_get_soup(self):
        """The probe must not count its own request twice.

        nav._get_soup() calls nav._get_page(), i.e. patched_get_page, which
        already increments ctx.total_page_count; the extra increment in
        probe_citation_start_year made the mandatory 8-12 page break fire after
        about half the intended number of pages.
        """
        import importlib
        from code.scholarly_session import probe_citation_start_year

        nav = scholarly_mod.scholarly._Scholarly__nav
        hist_html = (
            '<html><body>'
            '<div class="gs_rs_hist_dialog-g_bar_wrapper">'
            '<a class="gs_hist_g_a" data-year="2021" data-count="7"></a>'
            '</div></body></html>'
        )
        nav._get_page = lambda pagerequest, premium=False: hist_html
        self.fetcher._patch_scholarly()
        ctx = self.fetcher._session_ctx
        ctx.first_page_prompt_fn = None

        # The real scholarly Navigator._get_soup, applied to the stub nav: the
        # probe's own call path must not be stubbed away.
        stubbed = {name: sys.modules.pop(name)
                   for name in list(sys.modules)
                   if name == 'scholarly' or name.startswith('scholarly.')}
        try:
            real_scholarly = importlib.import_module('scholarly')
            real_get_soup = type(real_scholarly.scholarly._Scholarly__nav)._get_soup
        finally:
            for name in list(sys.modules):
                if name == 'scholarly' or name.startswith('scholarly.'):
                    del sys.modules[name]
            sys.modules.update(stubbed)
        nav._get_soup = types.MethodType(real_get_soup, nav)

        with patch("sys.stdout", new_callable=StringIO):
            start = probe_citation_start_year("/scholar?cites=1", ctx, num_citations=7)

        self.assertEqual(start, 2021)
        self.assertEqual(ctx.total_page_count, 1)
        self.assertEqual(ctx.curl_page_count, 1)

    def test_replaced_httpx_clients_are_closed(self):
        """Dropping a client without closing it leaks its connection pool."""
        nav = scholarly_mod.scholarly._Scholarly__nav

        class _Recorder:
            def __init__(self):
                self.headers = {}
                self.cookies = {}
                self.closed = False

            def close(self):
                self.closed = True

        def _is_closed(session):
            # A recorder marks itself closed; a real httpx.Client reports it.
            return session.closed if hasattr(session, "closed") else session.is_closed

        old1, old2 = _Recorder(), _Recorder()
        nav._session1, nav._session2 = old1, old2

        self.fetcher._patch_scholarly()
        self.fetcher._session_ctx.first_page_prompt_fn = None
        self.assertTrue(old1.closed)
        self.assertTrue(old2.closed)
        self.assertIsNot(nav._session1, old1)
        self.assertIsNot(nav._session1, nav._session2)

        # patched _new_session() builds a client and replaces it immediately
        previous = nav._session1
        nav._new_session(premium=True)
        self.assertTrue(_is_closed(previous))
        self.assertIsNot(nav._session1, previous)

        # captcha rebuild: both old slots are replaced by one shared client
        prev1, prev2 = nav._session1, nav._session2
        shared = nav.pm1._handle_captcha2(None)
        self.assertTrue(_is_closed(prev1))
        self.assertTrue(_is_closed(prev2))
        self.assertIs(shared, nav._session1)
        self.assertIs(shared, nav._session2)
        self.assertFalse(_is_closed(shared))

    def test_wait_proxy_switch_scales_with_accelerate(self):
        calls = []
        self.fetcher._delay_scale = 0.25
        with patch.object(scholar_citation, "_int_wait_proxy_switch",
                          side_effect=lambda max_hours=24, scale=1.0:
                          calls.append((max_hours, scale)) or True):
            result = self.fetcher._wait_proxy_switch()

        self.assertTrue(result)
        self.assertEqual(calls, [(24, 0.25)])

    def test_requirements_declare_the_httpx_http2_extra(self):
        """httpx.Client(http2=True) needs the h2 package.

        A clean `pip install -r requirements.txt` must pull h2 in, otherwise the
        crawler raises ImportError the moment it builds its first session.
        """
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "requirements.txt"), "r", encoding="utf-8") as f:
            requirements = f.read()
        self.assertIn("httpx[http2]==", requirements)


if __name__ == '__main__':
    unittest.main()
