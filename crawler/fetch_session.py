"""
crawler/fetch_session.py — Per-fetch session dataclasses.

BatchFetchSession  — single paginated fetch from one URL.
DirectFetchSession — wraps a BatchFetchSession for direct-mode papers.
YearFetchSession   — wraps one BatchFetchSession per year for year-mode papers.

These replace the previous FetchContext + closure-based state management.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from crawler.citation_models import ResumeState, FetchPolicy, YearRecord
from crawler.output_state import PaperFetchState
from crawler.common import SCHOLAR_PAGE_SIZE


# ---------------------------------------------------------------------------
# BatchFetchSession — single paginated fetch
# ---------------------------------------------------------------------------

@dataclass
class BatchFetchSession:
    """A single paginated fetch from one URL.

    run() iterates through all pages, collecting citation info dicts.
    The fetcher parameter supplies extraction helpers and the HTTP session.
    """

    url: str
    citations: list = field(default_factory=list)
    dedup_count: int = 0              # duplicate citations seen in this batch
    new_count: int = 0                # new citations (not in old cache)
    items_on_page: int = 0            # items on the most recently completed page
    start_index: int = 0              # resume position (page-aligned)
    finished: bool = False
    termination_reason: str = ""

    # -- pagination helpers --------------------------------------------------

    @staticmethod
    def _page_url(base_url, start_index):
        from crawler.citation_fetch import _append_start_param, _page_aligned_start
        page = _page_aligned_start(start_index)
        return _append_start_param(base_url, page) if page > 0 else base_url

    @staticmethod
    def _in_page_skip(start_index):
        from crawler.citation_fetch import _page_aligned_start
        return start_index - _page_aligned_start(start_index)

    def _make_iterator(self, fetcher=None):
        import sys as _sys
        import scholarly.publication_parser as _pub_parser
        from scholarly import scholarly
        from crawler.citation_fetch import _wrap_direct_citedby_iterator
        # Look up _SearchScholarIterator via the fetcher's module so that
        # tests can patch it (same pattern as the old _iter_direct_citedby).
        _SSI = (
            getattr(_sys.modules.get(type(fetcher).__module__, None),
                    '_SearchScholarIterator', None)
            if fetcher is not None else None
        ) or _pub_parser._SearchScholarIterator
        nav = scholarly._Scholarly__nav
        url = self._page_url(self.url, self.start_index)
        return _wrap_direct_citedby_iterator(
            _SSI(nav, url),
            self._in_page_skip(self.start_index),
        )

    def _blocked_url(self):
        """URL of the page that would be blocked (for captcha recovery)."""
        return f'https://scholar.google.com{self._page_url(self.url, self.start_index)}'

    # -- main entry point ----------------------------------------------------

    def run(self, fetcher, fallback_year=None, seen_keys=None,
            old_cache_identity_keys=None, on_page_complete=None,
            on_citation=None, iterator=None, max_retries=0):
        """Execute paginated fetch.

        Callbacks:
          on_citation(info, identity_keys, is_new, is_dupe, existing_label)
          on_page_complete(self)  — after each full page (save progress)

        *iterator* is for initial fetch only; retries create their own.
        *max_retries* controls automatic retries (captcha + transient errors).
        Returns self (for chaining).
        """
        attempt = 0

        while True:
            attempt += 1
            cur_iter = iterator if attempt == 1 and iterator is not None else self._make_iterator(fetcher)
            try:
                self._run_once(fetcher, cur_iter, fallback_year, seen_keys,
                              old_cache_identity_keys, on_page_complete,
                              on_citation)
                break
            except KeyboardInterrupt:
                if on_page_complete:
                    on_page_complete(self)
                raise
            except Exception:
                if on_page_complete:
                    on_page_complete(self)
                # Captcha recovery: try browser cookie injection once.
                blocked_url = self._blocked_url()
                if (getattr(fetcher, 'interactive_captcha', False)
                        and getattr(fetcher, '_try_interactive_captcha', None)):
                    solved = fetcher._try_interactive_captcha(blocked_url)
                    if solved:
                        continue
                # Automatic retry with a fresh iterator (transient errors).
                if attempt <= max_retries:
                    continue
                raise

        return self

    def _run_once(self, fetcher, iterator, fallback_year, seen_keys,
                  old_cache_identity_keys, on_page_complete, on_citation):
        """Single attempt at paginated fetch (called by run() within retry loop)."""
        seen = dict(seen_keys or {})

        for citing in iterator:
            self.start_index += 1
            info = fetcher._extract_citation_info(citing, fallback_year=fallback_year)
            identity_keys = fetcher._citation_identity_keys(info)

            matched = next((k for k in identity_keys if k in seen), None)
            if matched is not None:
                self.dedup_count += 1
                if on_citation:
                    on_citation(info, identity_keys, is_new=False, is_dupe=True,
                                existing_label=seen.get(matched))
                continue

            label = f"{info['title'][:50]} ({info.get('venue', 'N/A')}, {info.get('year', '?')})"
            for k in identity_keys:
                seen[k] = label
            self.citations.append(info)

            is_new = not any(k in (old_cache_identity_keys or set()) for k in identity_keys)
            if is_new:
                self.new_count += 1

            if on_citation:
                on_citation(info, identity_keys, is_new=is_new, is_dupe=False,
                            existing_label=None)

            if getattr(iterator, '_finished_current_page', False):
                self.items_on_page = getattr(iterator, '_items_in_current_page', 0)
                if on_page_complete:
                    on_page_complete(self)
                if hasattr(iterator, '_finished_current_page'):
                    iterator._finished_current_page = False
                _base = getattr(iterator, '_base_iterator', None)
                if _base is not None and hasattr(_base, '_finished_current_page'):
                    _base._finished_current_page = False

        final_items = getattr(iterator, '_items_in_current_page', 0)
        if 0 < final_items < SCHOLAR_PAGE_SIZE:
            self.termination_reason = 'short_page_stop'
        else:
            self.termination_reason = 'iterator_exhausted'
        self.finished = True


# ---------------------------------------------------------------------------
# DirectFetchSession
# ---------------------------------------------------------------------------

@dataclass
class DirectFetchSession:
    """Direct-mode fetch for one paper — a single BatchFetchSession."""

    baseline: PaperFetchState
    batch: BatchFetchSession
    resume_from: list = field(default_factory=list)
    fetch_policy: FetchPolicy = field(default_factory=lambda: FetchPolicy(strategy='direct'))

    @property
    def all_citations(self):
        """Merge resume_from with fresh batch citations via overlay."""
        # The _overlay_citations_by_identity method lives on the fetcher,
        # so this is called by the fetcher's fetch method.
        return self.batch.citations

    @classmethod
    def from_baseline(cls, baseline, citedby_url, num_citations, resume_from=None):
        """Create a DirectFetchSession from a PaperFetchState baseline."""
        return cls(
            baseline=baseline,
            batch=BatchFetchSession(url=citedby_url),
            resume_from=list(resume_from or []),
            fetch_policy=FetchPolicy(strategy='direct'),
        )


# ---------------------------------------------------------------------------
# YearFetchSession
# ---------------------------------------------------------------------------

@dataclass
class YearFetchSession:
    """Year-mode fetch for one paper — replaces FetchContext for year mode.

    Contains all per-paper mutable state for a year-based fetch, plus the
    cross-run baseline (PaperFetchState).  This merges what was previously
    split between FetchContext + closure variables.
    """

    # Cross-run baseline
    baseline: PaperFetchState

    # --- year-segment progress (was FetchContext) --------------------------
    completed_year_segments: set = field(default_factory=set)
    partial_year_start: dict = field(default_factory=dict)

    # --- probe metadata ----------------------------------------------------
    probed_year_counts: Optional[dict] = None
    probed_year_count_complete: bool = False

    # --- cached year distribution ------------------------------------------
    cached_year_counts: dict = field(default_factory=dict)

    # --- dedup / progress counters -----------------------------------------
    dedup_count: int = 0
    year_fetch_diagnostics: dict = field(default_factory=dict)

    # --- additional session state ------------------------------------------
    pending_years: list = field(default_factory=list)
    current_batch: Optional[BatchFetchSession] = None
    year_records: dict = field(default_factory=dict)   # {year: YearRecord}
    resume_from: list = field(default_factory=list)
    fetch_policy: FetchPolicy = field(default_factory=lambda: FetchPolicy(strategy='year'))

    @property
    def completed_years(self):
        return sorted(self.year_records.keys())

    @property
    def all_citations(self):
        """Merge resume_from with all completed year batches."""
        result = list(self.resume_from)
        for year in self.completed_years:
            batch = self.year_records[year].get('_batch')
            if batch:
                result = self._replace_year_bucket(result, year, batch.citations)
        return result

    @staticmethod
    def _replace_year_bucket(citations, year, refreshed):
        kept = [c for c in citations if c.get('year', None) != year]
        return kept + list(refreshed)

    @classmethod
    def from_baseline(cls, baseline, pending_years, resume_from=None,
                      fetch_policy=None):
        """Create a YearFetchSession from a PaperFetchState baseline."""
        return cls(
            baseline=baseline,
            pending_years=list(pending_years),
            resume_from=list(resume_from or []),
            fetch_policy=fetch_policy or FetchPolicy(strategy='year'),
        )
