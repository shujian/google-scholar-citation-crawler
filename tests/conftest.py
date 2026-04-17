"""
tests/conftest.py — Shared stubs and setUp helpers for all test modules.

Must be imported BEFORE scholar_citation so that the scholarly/openpyxl
module stubs are already in sys.modules when the main module is loaded.
"""

import sys
import types
import unittest
from unittest.mock import patch  # noqa: F401  (re-exported for convenience)
from io import StringIO  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
import tempfile  # noqa: F401


# ---------------------------------------------------------------------------
# Stub classes
# ---------------------------------------------------------------------------

class _CookieJar(dict):
    def set(self, key, value):
        self[key] = value


class _DummyNav:
    def __init__(self):
        self._session1 = types.SimpleNamespace(headers={}, cookies=_CookieJar())
        self._session2 = types.SimpleNamespace(headers={}, cookies=_CookieJar())
        self.pm1 = types.SimpleNamespace(_handle_captcha2=lambda pagerequest: None)
        self.pm2 = types.SimpleNamespace(_handle_captcha2=lambda pagerequest: None)
        self.got_403 = False

    def _set_retries(self, retries):
        self.retries = retries

    def _get_page(self, pagerequest, premium=False):
        return None

    def _new_session(self, premium=True, **kwargs):
        return None


class _DummyIterator:
    def __init__(self, *args, **kwargs):
        pass

    def __iter__(self):
        return self

    def __next__(self):
        raise StopIteration

    def _load_url(self, url):
        return None


class _DummyWorkbook:
    def __init__(self):
        self.active = _DummyWorksheet()
        self.sheets = [self.active]

    def create_sheet(self, title):
        ws = _DummyWorksheet()
        ws.title = title
        self.sheets.append(ws)
        return ws

    def save(self, path):
        self.saved_path = path


class _DummyWorksheet:
    def __init__(self):
        self.title = ""
        self.column_dimensions = _DimensionMap()
        self.row_dimensions = _DimensionMap()
        self.cells = {}
        self.merged_ranges = []

    def merge_cells(self, cell_range):
        self.merged_ranges.append(cell_range)

    def cell(self, row, column, value=None):
        key = (row, column)
        if key not in self.cells:
            self.cells[key] = _DummyCell()
        cell = self.cells[key]
        if value is not None:
            cell.value = value
        return cell


class _DummyCell:
    def __init__(self):
        self.value = None
        self.fill = None
        self.font = None
        self.alignment = None
        self.hyperlink = None


class _DimensionMap(dict):
    def __missing__(self, key):
        value = types.SimpleNamespace(width=None, height=None)
        self[key] = value
        return value


class _Style:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


# ---------------------------------------------------------------------------
# Install module stubs (must run at import time, before scholar_citation)
# ---------------------------------------------------------------------------

scholarly_mod = types.ModuleType("scholarly")
scholarly_mod.scholarly = types.SimpleNamespace(
    _Scholarly__nav=_DummyNav(),
    _citedby_long=lambda obj, years: iter(()),
    citedby=lambda obj: iter(()),
    search_author_id=lambda author_id: {"scholar_id": author_id},
    fill=lambda author, sections=None: author,
)
scholarly_mod.ProxyGenerator = object
sys.modules.setdefault("scholarly", scholarly_mod)

proxy_mod = types.ModuleType("scholarly._proxy_generator")
proxy_mod.MaxTriesExceededException = Exception
sys.modules.setdefault("scholarly._proxy_generator", proxy_mod)

pub_parser_mod = types.ModuleType("scholarly.publication_parser")
pub_parser_mod._SearchScholarIterator = _DummyIterator
sys.modules.setdefault("scholarly.publication_parser", pub_parser_mod)

openpyxl_mod = types.ModuleType("openpyxl")
openpyxl_mod.Workbook = _DummyWorkbook
sys.modules.setdefault("openpyxl", openpyxl_mod)

styles_mod = types.ModuleType("openpyxl.styles")
styles_mod.Font = _Style
styles_mod.PatternFill = _Style
styles_mod.Alignment = _Style
sys.modules.setdefault("openpyxl.styles", styles_mod)


# ---------------------------------------------------------------------------
# Base test case with shared setUp
# ---------------------------------------------------------------------------

class FetcherTestCase(unittest.TestCase):
    """
    Base class for PaperCitationFetcher tests.
    Imports scholar_citation lazily (after stubs are installed) and provides
    a fully-initialised self.fetcher with all runtime attributes zeroed out.
    """

    @classmethod
    def setUpClass(cls):
        import scholar_citation as sc
        cls.sc = sc

    def setUp(self):
        sc = self.sc
        scholarly_mod.scholarly._Scholarly__nav = _DummyNav()
        self.fetcher = sc.PaperCitationFetcher("test-author", output_dir=".")
        self.fetcher._completed_year_segments = set()
        self.fetcher._partial_year_start = {}
        self.fetcher._probed_year_counts = None
        self.fetcher._probed_year_count_complete = False
        self.fetcher._cached_year_counts = {}
        self.fetcher._dedup_count = 0
        self.fetcher._new_citations_count = 0
        self.fetcher._total_page_count = 0
        self.fetcher._papers_fetched_count = 0
        self.fetcher._delay_scale = 0
        self.fetcher._probed_year_counts = None
        self.fetcher._probed_year_count_complete = False
        self.fetcher.interactive_captcha = False
        self.fetcher.fetch_mode = 'normal'
        self.fetcher._last_scholar_url = (
            "https://scholar.google.com/citations?user=test-author&hl=en"
        )
        self.fetcher._current_attempt_url = None
        self.fetcher._probe_citation_start_year = (
            lambda citedby_url, fetch_ctx=None, num_citations=None, pub_year=None: 2025
        )
        self.fetcher._refresh_scholarly_session = lambda: None
        self.fetcher._try_interactive_captcha = lambda url: False
        self.fetcher._injected_cookies = {}
        self.fetcher._injected_header_overrides = {}
        self.fetcher._curl_header_allowlist = {
            'accept',
            'accept-language',
            'priority',
            'sec-ch-ua',
            'sec-ch-ua-arch',
            'sec-ch-ua-bitness',
            'sec-ch-ua-full-version-list',
            'sec-ch-ua-mobile',
            'sec-ch-ua-model',
            'sec-ch-ua-platform',
            'sec-ch-ua-platform-version',
            'sec-ch-ua-wow64',
        }

    def _paged_direct_iterator(self, pages):
        class FakeDirectIterator:
            def __init__(self, page_items):
                self.pages = [list(page) for page in page_items]
                self.page_index = 0
                self.item_index = 0
                self._finished_current_page = False

            def __iter__(self):
                return self

            def __next__(self):
                while self.page_index < len(self.pages):
                    page = self.pages[self.page_index]
                    if self.item_index >= len(page):
                        self.page_index += 1
                        self.item_index = 0
                        continue
                    item = page[self.item_index]
                    self.item_index += 1
                    self._finished_current_page = self.item_index >= len(page)
                    if self._finished_current_page:
                        self.page_index += 1
                        self.item_index = 0
                    return item
                self._finished_current_page = False
                raise StopIteration

        return FakeDirectIterator(pages)
