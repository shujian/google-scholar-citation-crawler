"""
code/fetch_session.py — Per-fetch session dataclasses.

BatchFetchSession — single paginated fetch from one URL.
YearFetchSession  — wraps one BatchFetchSession per year for year-mode papers.

These replace the previous FetchContext + closure-based state management.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from code.citation_models import ResumeState, FetchPolicy, YearRecord
from code.output_state import PaperFetchState
from code.common import SCHOLAR_PAGE_SIZE
from code.config import SHORT_WALK_REWIND_ROWS
from code.log_utils import run_log


# ---------------------------------------------------------------------------
# BatchFetchSession — single paginated fetch
# ---------------------------------------------------------------------------

@dataclass
class BatchFetchSession:
    """A single paginated fetch from one URL.

    run() iterates through all pages, collecting citation info dicts.
    The fetcher parameter supplies extraction helpers and the HTTP session.
    """

    _url: str = ""
    _citations: list = field(default_factory=list)
    _dedup_count: int = 0
    _new_count: int = 0
    _items_on_page: int = 0
    _start_index: int = 0
    _finished: bool = False
    _termination_reason: str = ""
    # 走完却明显少于应有条数时，本轮**再走一次**；这里记下是否已经重走过，
    # 让调用方在"两次结果一样"时按 stall 处理，而不是继续重走。
    _short_walk_retried: bool = False
    # 重走时从哪里开始：默认接着 `_start_index`，短走重试则从尾部倒回几页
    # （见 config.SHORT_WALK_REWIND_ROWS）。已抓到的行不回滚。
    _resume_offset: Optional[int] = None
    # 尾部重走时，开头这么多行是**已经抓过**的（同一段页面再取一遍拿的是同样的行），
    # 直接跳过，避免把重复行当成新行加进来。
    _resume_skip: int = 0

    # -- properties ----------------------------------------------------------

    @property
    def url(self): return self._url

    @property
    def citations(self): return self._citations

    @property
    def dedup_count(self): return self._dedup_count

    @property
    def new_count(self): return self._new_count

    @property
    def items_on_page(self): return self._items_on_page

    @property
    def start_index(self): return self._start_index

    @property
    def finished(self): return self._finished

    @property
    def termination_reason(self): return self._termination_reason

    @property
    def short_walk_retried(self): return self._short_walk_retried

    # -- pagination helpers --------------------------------------------------

    @staticmethod
    def _page_url(base_url, start_index):
        from code.citation_fetch import _append_start_param, _page_aligned_start
        page = _page_aligned_start(start_index)
        return _append_start_param(base_url, page) if page > 0 else base_url

    @staticmethod
    def _in_page_skip(start_index):
        from code.citation_fetch import _page_aligned_start
        return start_index - _page_aligned_start(start_index)

    def _make_iterator(self, fetcher=None):
        import sys as _sys
        import scholarly.publication_parser as _pub_parser
        from scholarly import scholarly
        from code.citation_fetch import _wrap_direct_citedby_iterator
        # Look up _SearchScholarIterator via the fetcher's module so that
        # tests can patch it (same pattern as the old _iter_direct_citedby).
        _SSI = (
            getattr(_sys.modules.get(type(fetcher).__module__, None),
                    '_SearchScholarIterator', None)
            if fetcher is not None else None
        ) or _pub_parser._SearchScholarIterator
        nav = scholarly._Scholarly__nav
        offset = self._resume_offset if self._resume_offset is not None else self._start_index
        self._resume_offset = None          # 只对这一次重走生效
        url = self._page_url(self._url, offset)
        return _wrap_direct_citedby_iterator(
            _SSI(nav, url),
            self._in_page_skip(offset),
        )

    def _blocked_url(self):
        """URL of the page that would be blocked (for captcha recovery)."""
        return f'https://scholar.google.com{self._page_url(self._url, self._start_index)}'

    # -- main entry point ----------------------------------------------------

    def run(self, fetcher, fallback_year=None, seen_keys=None,
            old_cache_identity_keys=None, on_page_complete=None,
            on_citation=None, iterator=None, max_retries=0, min_rows=None):
        """Execute paginated fetch.

        Callbacks:
          on_citation(info, identity_keys, is_new, is_dupe, existing_label)
          on_page_complete(self)  — after each full page (save progress)

        *iterator* is for initial fetch only; retries create their own.
        *max_retries* controls automatic retries (captcha + transient errors).
        *min_rows* (year mode): rows this walk is expected to touch.  A walk that
        ends "exhausted" well below it looks like a truncated response (a page can
        come back with 3 rows and no next link while the year holds 220), so it is
        re-walked **once**; the caller turns two identical results into the usual
        `stalled` verdict.
        Returns self (for chaining).
        """
        attempt = 0
        initial = (list(self._citations), self._start_index,
                   self._dedup_count, self._new_count)

        while True:
            attempt += 1
            cur_iter = iterator if attempt == 1 and iterator is not None else self._make_iterator(fetcher)
            try:
                self._run_once(fetcher, cur_iter, fallback_year, seen_keys,
                              old_cache_identity_keys, on_page_complete,
                              on_citation)
                walked = self._start_index - initial[1]
                if (min_rows and walked < min_rows and not self._short_walk_retried):
                    # 页面被截断（短页且没有翻页链接）：再走一次，只一次。
                    # 但**从尾部重试**：已抓到的行全部保留，只倒回最后
                    # SHORT_WALK_REWIND_ROWS 行（两页）重新取 —— 提前结束通常
                    # 只是最后一两页里有一个空页，整段重来等于把几百页重下一遍。
                    self._short_walk_retried = True
                    self._termination_reason = ""
                    self._items_on_page = 0
                    resume = max(initial[1],
                                 self._start_index - SHORT_WALK_REWIND_ROWS)
                    if resume >= self._start_index:
                        resume = initial[1]         # 什么都没抓到：只能从头来
                    self._resume_offset = resume
                    self._resume_skip = self._start_index - resume
                    where = (f"re-fetching the last {(self._start_index - resume) // 10} "
                             f"page(s) from row {resume}" if resume > initial[1]
                             else "starting over")
                    run_log.warn(f"walk ended after {walked} row(s) but {min_rows} "
                                 f"expected — retrying once, {where}",
                                 module="year", indent=2)
                    continue
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
            if self._resume_skip > 0:
                # 尾部重走：这一段页面已经抓过，跳过重复行（只是把响应再取一次）。
                self._resume_skip -= 1
                continue
            self._start_index += 1
            try:
                info = fetcher._extract_citation_info(citing, fallback_year=fallback_year)
                identity_keys = fetcher._citation_identity_keys(info)
            except Exception:
                # The item was consumed from the iterator but never saved, so the
                # cursor must step back: a retry resumes at this item instead of
                # silently skipping it (the skip count drives the page-aligned
                # re-fetch).  Failures after the append below are fine — the item
                # is in self._citations and will be skipped on retry.
                self._start_index -= 1
                raise

            matched = next((k for k in identity_keys if k in seen), None)
            if matched is not None:
                self._dedup_count += 1
                if on_citation:
                    on_citation(info, identity_keys, is_new=False, is_dupe=True,
                                existing_label=seen.get(matched))
                continue

            label = f"{info['title'][:50]} ({info.get('venue', 'N/A')}, {info.get('year', '?')})"
            for k in identity_keys:
                seen[k] = label
            self._citations.append(info)

            is_new = not any(k in (old_cache_identity_keys or set()) for k in identity_keys)
            if is_new:
                self._new_count += 1

            if on_citation:
                on_citation(info, identity_keys, is_new=is_new, is_dupe=False,
                            existing_label=None)

            if getattr(iterator, '_finished_current_page', False):
                self._items_on_page = getattr(iterator, '_items_in_current_page', 0)
                if on_page_complete:
                    on_page_complete(self)
                if hasattr(iterator, '_finished_current_page'):
                    iterator._finished_current_page = False
                _base = getattr(iterator, '_base_iterator', None)
                if _base is not None and hasattr(_base, '_finished_current_page'):
                    _base._finished_current_page = False

        final_items = getattr(iterator, '_items_in_current_page', 0)
        if 0 < final_items < SCHOLAR_PAGE_SIZE:
            self._termination_reason = 'short_page_stop'
        else:
            self._termination_reason = 'iterator_exhausted'
        self._finished = True


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
    counted_new_keys: set = field(default_factory=set)  # cross-year dedup within this paper

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
