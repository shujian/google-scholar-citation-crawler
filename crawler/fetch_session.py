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

    def run(self, fetcher, fallback_year=None, seen_keys=None,
            old_cache_identity_keys=None, on_page_complete=None,
            on_citation=None, iterator=None):
        """Execute paginated fetch.

        Yields control via callbacks:
          on_citation(info, identity_keys, is_new) — each citation
          on_page_complete(self) — after each full page (for progress save)

        If *iterator* is not given, a default _SearchScholarIterator is
        created.  Callers should pass a pre-built iterator (e.g. via
        fetcher._iter_direct_citedby) so that test mocks are honoured.
        Returns self (for chaining).
        """
        from scholarly import scholarly
        from scholarly.publication_parser import _SearchScholarIterator
        from crawler.citation_fetch import _wrap_direct_citedby_iterator, _append_start_param, _page_aligned_start

        if iterator is None:
            page_start = _page_aligned_start(self.start_index)
            in_page_skip = self.start_index - page_start
            url = _append_start_param(self.url, page_start) if page_start > 0 else self.url
            nav = scholarly._Scholarly__nav
            iterator = _wrap_direct_citedby_iterator(
                _SearchScholarIterator(nav, url), in_page_skip,
            )
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
                # Reset page-complete flag so next page can be detected
                if hasattr(iterator, '_finished_current_page'):
                    iterator._finished_current_page = False
                _base = getattr(iterator, '_base_iterator', None)
                if _base is not None and hasattr(_base, '_finished_current_page'):
                    _base._finished_current_page = False

        # Determine termination reason
        final_items = getattr(iterator, '_items_in_current_page', 0)
        if 0 < final_items < SCHOLAR_PAGE_SIZE:
            self.termination_reason = 'short_page_stop'
        else:
            self.termination_reason = 'iterator_exhausted'
        self.finished = True
        return self


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
    """Year-mode fetch for one paper.

    Processes years in order (old→new or new→old, depending on update mode).
    Each year is a BatchFetchSession.  Only the current year's batch is live
    at any time; completed years are recorded in year_records.
    """

    baseline: PaperFetchState
    pending_years: list = field(default_factory=list)
    current_batch: Optional[BatchFetchSession] = None
    year_records: dict = field(default_factory=dict)   # {year: YearRecord}
    resume_from: list = field(default_factory=list)
    fetch_policy: FetchPolicy = field(default_factory=lambda: FetchPolicy(strategy='year'))
    dedup_count: int = 0
    probed_year_counts: dict = field(default_factory=dict)
    probed_year_count_complete: bool = False
    cached_year_counts: dict = field(default_factory=dict)

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
