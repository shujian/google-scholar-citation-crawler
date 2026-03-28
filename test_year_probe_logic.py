import sys
import types
import unittest
from unittest import mock

# Minimal stubs so scholar_citation imports without external deps.
if "scholarly" not in sys.modules:
    scholarly_mod = types.ModuleType("scholarly")
    scholarly_mod.scholarly = types.SimpleNamespace(_Scholarly__nav=types.SimpleNamespace())
    scholarly_mod.ProxyGenerator = object
    sys.modules["scholarly"] = scholarly_mod

    proxy_mod = types.ModuleType("scholarly._proxy_generator")
    class _MaxTriesExceededException(Exception):
        pass
    proxy_mod.MaxTriesExceededException = _MaxTriesExceededException
    sys.modules["scholarly._proxy_generator"] = proxy_mod

    publication_mod = types.ModuleType("scholarly.publication_parser")
    class _DummyIterator:
        def __init__(self, *args, **kwargs):
            pass
        def __iter__(self):
            return self
        def __next__(self):
            raise StopIteration
    publication_mod._SearchScholarIterator = _DummyIterator
    sys.modules["scholarly.publication_parser"] = publication_mod

if "openpyxl" not in sys.modules:
    openpyxl_mod = types.ModuleType("openpyxl")
    openpyxl_mod.Workbook = object
    styles_mod = types.ModuleType("openpyxl.styles")
    styles_mod.PatternFill = object
    styles_mod.Font = object
    styles_mod.Alignment = object
    sys.modules["openpyxl"] = openpyxl_mod
    sys.modules["openpyxl.styles"] = styles_mod

from scholar_citation import PaperCitationFetcher, parse_args


class YearProbeLogicTests(unittest.TestCase):
    def make_fetcher(self, recheck=False):
        fetcher = PaperCitationFetcher(
            author_id="test",
            output_dir=".",
            recheck_citations=recheck,
        )
        fetcher._completed_year_segments = set()
        fetcher._partial_year_start = {}
        fetcher._new_citations_count = 0
        fetcher._total_page_count = 0
        fetcher._run_start_time = 0
        return fetcher

    def test_force_refresh_reprobes_even_with_cached_years(self):
        fetcher = self.make_fetcher(recheck=True)
        citations = [{"year": "2017", "title": "old"}]

        with mock.patch.object(fetcher, "_probe_citation_start_year", return_value=2022) as probe:
            with mock.patch("scholar_citation._SearchScholarIterator", side_effect=RuntimeError("stop")):
                with self.assertRaises(RuntimeError):
                    fetcher._fetch_by_year(
                        citedby_url="/scholar?cites=1",
                        citations=citations,
                        save_progress=lambda complete: None,
                        num_citations=10,
                        pub_year="2017",
                        prev_scholar_count=0,
                    )
        probe.assert_called_once()

    def test_new_run_without_completed_years_should_still_probe(self):
        fetcher = self.make_fetcher(recheck=False)
        citations = [{"year": "2017", "title": "old"}]

        with mock.patch.object(fetcher, "_probe_citation_start_year", return_value=2017) as probe:
            with mock.patch("scholar_citation._SearchScholarIterator", side_effect=RuntimeError("stop")):
                with self.assertRaises(RuntimeError):
                    fetcher._fetch_by_year(
                        citedby_url="/scholar?cites=1",
                        citations=citations,
                        save_progress=lambda complete: None,
                        num_citations=10,
                        pub_year="2017",
                        prev_scholar_count=0,
                    )
        # Desired behavior: every fresh fetch/run re-checks year range once,
        # even if cached citations already exist.
        probe.assert_called_once()

    def test_same_run_resume_with_completed_years_skips_probe(self):
        fetcher = self.make_fetcher(recheck=False)
        fetcher._completed_year_segments = {2017, 2018}
        citations = [{"year": "2017", "title": "old"}]

        with mock.patch.object(fetcher, "_probe_citation_start_year", return_value=2022) as probe:
            with mock.patch("scholar_citation._SearchScholarIterator", side_effect=RuntimeError("stop")):
                with self.assertRaises(RuntimeError):
                    fetcher._fetch_by_year(
                        citedby_url="/scholar?cites=1",
                        citations=citations,
                        save_progress=lambda complete: None,
                        num_citations=10,
                        pub_year="2017",
                        prev_scholar_count=0,
                    )
        probe.assert_not_called()




class CliFlagTests(unittest.TestCase):
    def test_parse_args_accepts_recheck_citations(self):
        with mock.patch.object(sys, 'argv', [
            'scholar_citation.py', '--author', 'abc', '--recheck-citations'
        ]):
            args = parse_args()
        self.assertTrue(args.recheck_citations)

    def test_parse_args_accepts_accelerate(self):
        with mock.patch.object(sys, 'argv', [
            'scholar_citation.py', '--author', 'abc', '--accelerate', '0.1'
        ]):
            args = parse_args()
        self.assertEqual(args.accelerate, 0.1)



if __name__ == "__main__":
    unittest.main()
