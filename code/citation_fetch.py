"""
code/citation_fetch.py — Citation fetch engine: direct and year-based modes.

Public API:
  fetch_citations_with_progress(fetcher, year_ctx, citedby_url, cache_path, title, ...)
  fetch_by_year(fetcher, year_ctx, citedby_url, old_citations, fresh_citations, ...)

Both functions accept:
  fetcher  — the PaperCitationFetcher instance (for method calls and run-level state)
  year_ctx      — a YearFetchSession dataclass (for per-paper mutable state)

All direct-fetch helper functions are pure module-level functions.
"""

import random
import re
import time
from datetime import datetime

from code.citation_cache import (
    is_data_complete,
    iter_year_records,
    year_record_effective_histogram as _year_effective_histogram,
    year_records_data_complete,
)
from code.fetch_session import BatchFetchSession

from scholarly import scholarly
import scholarly.publication_parser as _pub_parser

from code.log_utils import run_log
from code.common import (
    RESCAN_AFTER_SHORT_ATTEMPTS,
    SCHOLAR_PAGE_SIZE,
    SESSION_REFRESH_MAX,
    SESSION_REFRESH_MIN,
    _scholar_request_url,
    rand_delay,
)
from code.fetch_session import YearFetchSession  # replaces FetchContext
from code.citation_models import ResumeState, _page_align


def _normalize_direct_resume_state(state):
    rs = ResumeState.from_dict(state)
    return rs.to_dict() if rs else None

def _direct_resume_log_suffix(state):
    rs = ResumeState.from_dict(state)
    return f" (direct offset={rs.next_index})" if rs else ""

def _build_direct_resume_state(next_index, scholar_total, citedby_url):
    try:
        next_index = int(next_index)
        scholar_total = int(scholar_total)
    except (TypeError, ValueError):
        return None
    if next_index < 0 or scholar_total < 0 or next_index > scholar_total or not citedby_url:
        return None
    rs = ResumeState(
        next_index=_page_align(next_index),
        source_scholar_total=scholar_total,
        citedby_url=citedby_url,
    )
    return rs.to_dict()

def _resume_start_after_short_attempts(start_index, short_attempts):
    """年份续跑起点：连续 `RESCAN_AFTER_SHORT_ATTEMPTS` 次没收全 → **从第 1 页重扫** ✓。

    为什么（用户 2026-10-19）：偏移续跑省时间 ✓，但它是"可变列表里的位置" ✗ —— Scholar 在两次
    运行之间往那一年列表上方插入/调整条目时，从旧偏移续跑会掠过被挤下去的行 ✗，而那一年又永远
    "差几条"，于是每次都从同一个偏移续 ✗、永远补不上。连续几次都没收全 = 偏移不再可信 → 清零重扫
    一遍（按身份键去重，代价是多翻几页）✓。

    未到阈值、或本来就没有偏移 → 原样返回 ✓（0 就是从头）。
    """
    try:
        start_index = int(start_index or 0)
        short_attempts = int(short_attempts or 0)
    except (TypeError, ValueError):
        return 0
    if start_index > 0 and short_attempts >= RESCAN_AFTER_SHORT_ATTEMPTS:
        return 0
    return start_index


def _page_aligned_start(index):
    return _page_align(index)

def _direct_start_position(direct_resume_state):
    rs = ResumeState.from_dict(direct_resume_state)
    if rs is None:
        return 0, 0
    return rs.page_start(), rs.in_page_skip()

def _append_start_param(citedby_url, start):
    if start <= 0:
        return citedby_url
    separator = '&' if '?' in citedby_url else '?'
    if re.search(r'([?&])start=\d+', citedby_url):
        return re.sub(r'([?&])start=\d+', lambda match: f"{match.group(1)}start={start}", citedby_url)
    return f"{citedby_url}{separator}start={start}"

def _direct_request_url(citedby_url, direct_resume_state=None):
    rs = ResumeState.from_dict(direct_resume_state)
    if rs is None:
        return citedby_url
    return _append_start_param(citedby_url, rs.page_start())

def _wrap_direct_citedby_iterator(iterator, in_page_skip=0):
    class _WrappedDirectIterator:
        def __init__(self, base_iterator, skip_count):
            self._base_iterator = iter(base_iterator)
            self._remaining_skip = max(0, int(skip_count or 0))
            self._finished_current_page = False

        def __iter__(self):
            return self

        def __next__(self):
            while True:
                citing = next(self._base_iterator)
                self._finished_current_page = bool(
                    getattr(self._base_iterator, '_finished_current_page', False)
                )
                self._items_in_current_page = getattr(
                    self._base_iterator, '_items_in_current_page', 0)
                if self._remaining_skip > 0:
                    self._remaining_skip -= 1
                    continue
                return citing

    return _WrappedDirectIterator(iterator, in_page_skip)

def _iter_direct_citedby(citedby_url, direct_resume_state=None, num_citations=0, fetcher=None):
    normalized = _normalize_direct_resume_state(direct_resume_state)
    request_url = _direct_request_url(citedby_url, normalized)
    if not normalized:
        direct_fetch_pub = {
            'citedby_url': request_url,
            'container_type': 'Publication',
            'num_citations': int(num_citations or 0),
            'filled': True,
            'source': 'PUBLICATION_SEARCH_SNIPPET',
        }
        return _wrap_direct_citedby_iterator(
            scholarly.citedby(direct_fetch_pub)
        )

    nav = scholarly._Scholarly__nav
    _, in_page_skip = _direct_start_position(normalized)
    # Use _SearchScholarIterator from fetcher's module so that tests patching
    # scholar_citation._SearchScholarIterator can intercept this call.
    import sys as _sys
    _SSI = (
        getattr(_sys.modules.get(type(fetcher).__module__, None), '_SearchScholarIterator', None)
        if fetcher is not None else None
    ) or _pub_parser._SearchScholarIterator
    return _wrap_direct_citedby_iterator(
        _SSI(nav, request_url),
        in_page_skip=in_page_skip,
    )

def _build_direct_fetch_diagnostics(scholar_total, cached_total, seen_total, dedup_count, termination_reason):
    try:
        scholar_total = int(scholar_total or 0)
    except (TypeError, ValueError):
        scholar_total = 0
    try:
        cached_total = int(cached_total or 0)
    except (TypeError, ValueError):
        cached_total = 0
    try:
        seen_total = int(seen_total or 0)
    except (TypeError, ValueError):
        seen_total = 0
    try:
        dedup_count = int(dedup_count or 0)
    except (TypeError, ValueError):
        dedup_count = 0
    return {
        'scholar_total': scholar_total,
        'cached_total': cached_total,
        'seen_total': seen_total,
        'dedup_count': dedup_count,
        'termination_reason': termination_reason or 'iterator_exhausted',
    }

def _direct_fetch_gap(diagnostics):
    """Compute the under-fetch gap from the summary sub-dict."""
    s = diagnostics
    return max(0, (s.get('scholar_total') or 0) - (s.get('seen_total') or 0))

def _direct_fetch_is_underfetched(diagnostics):
    """Return True when the direct fetch did not retrieve all expected citations."""
    s = diagnostics
    return (s.get('seen_total') or 0) < (s.get('scholar_total') or 0)

def _direct_fetch_diagnostics_message(diagnostics, prefix="Direct fetch summary "):
    s = diagnostics
    return (
        f"{prefix}"
        f"(scholar_total={s.get('scholar_total')}, "
        f"cached_total={s.get('cached_total')}, "
        f"seen_total={s.get('seen_total')}, "
        f"dedup_num={s.get('dedup_count')}, "
        f"gap={_direct_fetch_gap(diagnostics)}, "
        f"termination={s.get('termination_reason')})"
    )

def _run_page_cite_hook(fetcher, batch):
    """把"刚抓完的这一页"交给逐页 cite/BibTeX 钩子（用户 2026-10-15 定，逐页粒度）。

    **为什么逐页**：cite 对话框（`output=cite`）本来是在整篇抓完后集中发的，一次几十上百条
    连着的 detail 请求很容易被 Scholar 看成异常流量；并进每页之后，请求序列变成
    "列表页 → 少数几条对话框 → 列表页 …"，与人工翻页的节奏一致。

    `citation_fetch` **不认识** `cite_format`（分层）：钩子是 `fetcher._on_citations_page`
    （由 `scholar_citation` 在开始抓取前挂上，见那里的 docstring），没挂就什么都不做 ——
    于是 `--no-cite-formats`、离线补齐等路径行为不变。钩子自身吞掉异常，坏一条不许影响抓取。
    """
    hook = getattr(fetcher, '_on_citations_page', None)
    if hook is None:
        return
    rows = getattr(batch, 'citations', None) or []
    if not rows:
        return
    hook(rows)


def fetch_citations_with_progress(fetcher, year_ctx, citedby_url, _cache_path, title,
                                    num_citations, pub_url, pub_year, resume_from,
                                    completed_years_in_current_run=None, prev_scholar_count=0,
                                    partial_year_start=None, saved_dedup_count=0,
                                    allow_incremental_early_stop=True,
                                    force_year_rebuild=False,
                                    selective_refresh_years=None,
                                    pub_obj=None,
                                    fetch_policy=None,
                                    direct_resume_state=None):
    """
    Stream-fetch citations with periodic progress saves.
    resume_from: previously saved citation list (for resume after interruption).
    completed_years_in_current_run: list of years already fully fetched in this run (for resume).
    partial_year_start: dict {year: start_index} for the in-progress year on last run.
    prev_scholar_count: Scholar citation count from last completed scan (for early stop).
    saved_dedup_count: dedup count from the last save; used as a floor so we never
        undercount Scholar's self-duplicates when resuming or force-refreshing.
    allow_incremental_early_stop: when True, update-mode year fetches may stop once
        the observed Scholar increase has been recovered. Recheck/full-scan flows
        should pass False so all remaining years are revalidated.
    force_year_rebuild: when True, year-based fetch ignores cached year-bucket contents
        and rebuilds fetched years from Scholar.
    selective_refresh_years: optional list of years to refetch authoritatively.
    """
    old_citations = list(resume_from)
    year_ctx.cached_year_counts = fetcher._year_count_map(old_citations)
    fresh_citations = []
    effective_num_citations = int(num_citations or 0)
    if pub_obj is not None:
        effective_num_citations = fetcher._effective_scholar_total(pub_obj)
        pub_obj['num_citations'] = effective_num_citations
    # _dedup_count tracks same-run duplicate rows observed from Scholar within the
    # current fetch flow. Seed from cached direct diagnostics only so resumed direct
    # fetches preserve already-seen duplicate rows from the same in-progress scan.
    year_ctx.dedup_count = int(saved_dedup_count or 0)

    # Load completed years into patch state for _citedby_long to skip
    year_ctx.completed_year_segments = set(completed_years_in_current_run or [])
    # Track the page offset (start_index) for the year currently in progress.
    # Saved to cache on exception so retry can skip already-fetched pages.
    # Align to page boundary so old caches with non-aligned values (e.g. 101)
    # don't cause request_in_page_skip > 0 and silently drop items.
    year_ctx.partial_year_start = {
        int(year): _page_aligned_start(idx)
        for year, idx in (partial_year_start or {}).items()
    }
    fetcher._live_year_fetch_diagnostics = None  # reset; set by _fetch_by_year wrapper during year-based fetch
    fetcher._live_dedup_count = None             # reset; set by _fetch_by_year wrapper during year-based fetch
    fetcher._live_probed_year_counts = None      # reset; set by _fetch_by_year wrapper
    fetcher._live_probe_complete = None          # reset; set by _fetch_by_year wrapper

    def current_scholar_total():
        if pub_obj is not None:
            return fetcher._effective_scholar_total(pub_obj)
        return effective_num_citations

    def maybe_promote_scholar_total(live_total, source=None):
        nonlocal effective_num_citations
        try:
            live_total = int(live_total)
        except (TypeError, ValueError):
            return current_scholar_total()
        if live_total > effective_num_citations:
            effective_num_citations = live_total
        return effective_num_citations

    direct_fetch_diagnostics = None
    normalized_direct_resume_state = _normalize_direct_resume_state(direct_resume_state)
    direct_next_index = normalized_direct_resume_state['next_index'] if normalized_direct_resume_state else 0
    # A direct scan that resumes at an offset only returns the suffix of the
    # citation list, so its result must be merged with the cached prefix instead
    # of replacing it.
    resumed_mid_list = bool(normalized_direct_resume_state
                            and normalized_direct_resume_state.get('next_index'))

    # Year mode rebuilds each year's bucket itself (replace/merge per year), so
    # its materialized list is final even when it is smaller than the cache.
    is_year_mode = fetch_policy.get('strategy') == 'year' if fetch_policy else False

    def direct_materialized_citations(complete):
        return fetcher._materialize_citation_cache(
            old_citations, fresh_citations,
            complete and not resumed_mid_list,
            authoritative=is_year_mode)

    def materialized_citations(complete):
        return direct_materialized_citations(complete)

    def build_year_records(citations_to_save):
        # During year-based fetch, _fetch_by_year creates a separate inner year_ctx
        # and syncs its year_fetch_diagnostics to fetcher._live_year_fetch_diagnostics
        # just before calling save_progress.  Use that when available so that per-year
        # dedup counts accumulated in the inner year_ctx are not lost.
        live = getattr(fetcher, '_live_year_fetch_diagnostics', None)
        source = live if live is not None else year_ctx.year_fetch_diagnostics
        diagnostics = dict(fetcher._normalize_year_fetch_diagnostics(source))
        year_counts = fetcher._year_count_map(citations_to_save)
        # Histogram data only exists in year mode (probe is done before fetch).
        is_year = fetch_policy.get('strategy') == 'year' if fetch_policy else False
        # Probe counts live on the inner year_ctx (the outer one never probes —
        # _fetch_by_year creates its own ctx and syncs the counts to
        # fetcher._live_probed_year_counts before each save).  Prefer those:
        # reading the outer ctx here drops every per-year record when a fetch
        # comes back with no citations at all, losing the page evidence
        # (e.g. termination_reason=empty_page) for exactly the interesting case.
        probed_year_counts = getattr(fetcher, '_live_probed_year_counts', None)
        if probed_year_counts is None:
            probed_year_counts = year_ctx.probed_year_counts
        probed_year_counts = probed_year_counts or {}
        for year, diagnostic in list(diagnostics.items()):
            if year not in year_counts and year not in probed_year_counts:
                diagnostics.pop(year, None)
        for year, cached_total in year_counts.items():
            existing = diagnostics.get(year)
            if existing is not None:
                histogram_count = existing.get('histogram_count', existing.get('scholar_total'))
                if histogram_count is None:
                    histogram_count = probed_year_counts.get(year, 0 if not is_year else cached_total)
                diagnostics[year] = fetcher._build_year_fetch_diagnostics(
                    year,
                    histogram_count,
                    cached_total,
                    existing.get('dedup_count', 0),
                    existing.get('termination_reason'),
                    existing.get('short_attempts', 0),
                    existing.get('stalled', False),
                    existing.get('stalled_probe', 0),
                )
            else:
                hc = probed_year_counts.get(year, 0 if not is_year else cached_total)
                diagnostics[year] = fetcher._build_year_fetch_diagnostics(
                    year, hc, cached_total, 0, None,
                )
        for year, probe_total in probed_year_counts.items():
            if year not in diagnostics:
                diagnostics[year] = fetcher._build_year_fetch_diagnostics(
                    year, probe_total, 0, 0, None,
                )
        return fetcher._normalize_year_fetch_diagnostics(diagnostics)

    # Termination reasons that mean "the page came back empty/blocked", i.e. the
    # attempt finished without an error but must not be trusted as a clean read.
    BLOCKED_TERMINATIONS = ('empty_page', 'empty_first_page')

    def _compute_fetch_attempt_complete(fetch_finished, is_year, diag_summary, year_records=None):
        """True when the attempt ran to the end without a blocked/empty page.

        Deliberately NOT the data-completeness verdict: that is `fetch_complete`
        in the output, computed by PaperFetchState.is_complete().  This flag
        answers "may this attempt be trusted as a clean, finished read?", which
        is what --fetch-mode rough asks before skipping an unchanged paper, and
        what decides whether a direct resume offset is kept.
        """
        if not fetch_finished:
            return False
        reasons = []
        if is_year:
            # year_records may be a list of per-year dicts or a {year: dict} map.
            for record in iter_year_records(year_records):
                reasons.append(record.get('termination_reason'))
            if not reasons and isinstance(diag_summary, dict):
                reasons.append(diag_summary.get('termination_reason'))
        elif isinstance(diag_summary, dict):
            reasons.append(diag_summary.get('termination_reason'))
        return not any(reason in BLOCKED_TERMINATIONS for reason in reasons)

    def save_progress(fetch_finished, batch=None):
        # fetch_finished: whether the fetch loop ran to completion.
        # batch: optional BatchFetchSession for intermediate saves.
        nonlocal direct_next_index
        is_year = fetch_policy.get('strategy') == 'year' if fetch_policy else False
        if batch is not None:
            fresh_citations[:] = list(batch.citations)
            year_ctx.dedup_count = (int(saved_dedup_count or 0)) + batch.dedup_count
            if not is_year:
                # Remember how far the direct scan got, so a retry resumes at the
                # next page instead of re-walking the list from the head (the
                # offset is written into direct_resume_state below).
                direct_next_index = batch.start_index
        diagnostics_to_save = direct_fetch_diagnostics
        if diagnostics_to_save is None and not is_year:
            # K78 — a direct scan saves per page and on Ctrl+C, i.e. *before* the
            # post-loop build, so the snapshot used to be written with
            # direct_fetch_diagnostics=None and fetch_strategy=None.  Reloading it
            # inferred 'year' from the year_fetch_diagnostics every paper carries
            # (its direct-mode histogram_total is 0), so an interrupted 2/25 fetch
            # read as COMPLETE and the remaining citations were never fetched.
            # Record what is known now; once the scan finishes, the post-loop build
            # replaces this with the real termination reason.
            diagnostics_to_save = _build_direct_fetch_diagnostics(
                scholar_total=current_scholar_total(),
                cached_total=len(fresh_citations),
                seen_total=len(fresh_citations) + year_ctx.dedup_count,
                dedup_count=year_ctx.dedup_count,
                termination_reason='interrupted',
            )
        citations_to_save = materialized_citations(fetch_finished)
        if not citations_to_save and old_citations:
            citations_to_save = list(old_citations)
        if not is_year:
            # Direct mode: fresh_citations is the whole citation list, and the
            # caller returns it as this paper's citations.  Publish the
            # materialized result so a merged (resumed/truncated) scan is not
            # reported as the suffix alone.
            fresh_citations[:] = list(citations_to_save)
        year_ctx.cached_year_counts = fetcher._year_count_map(citations_to_save)
        # Always build year diagnostics from cached citations so every paper
        # has per-year entries and a summary, even direct-mode papers.
        year_records_to_save = build_year_records(citations_to_save)
        year_ctx.year_fetch_diagnostics = year_records_to_save
        # During year-based fetch, use inner-year_ctx dedup count / probe data synced
        # by _fetch_by_year wrapper.  The outer year_ctx.probed_year_counts is always
        # None because the probe runs inside the inner fetch_by_year flow.
        live_dedup = getattr(fetcher, '_live_dedup_count', None)
        effective_dedup = live_dedup if live_dedup is not None else year_ctx.dedup_count
        live_probe = getattr(fetcher, '_live_probed_year_counts', None)
        effective_probe = live_probe if live_probe is not None else year_ctx.probed_year_counts
        live_probe_complete = getattr(fetcher, '_live_probe_complete', None)
        effective_probe_complete = live_probe_complete if live_probe_complete is not None else year_ctx.probed_year_count_complete
        count_summary = fetcher._build_citation_count_summary(
            citations_to_save,
            scholar_total=current_scholar_total(),
            probed_year_counts=effective_probe,
            probe_complete=effective_probe_complete,
            dedup_count=effective_dedup,
            year_fetch_diagnostics=year_records_to_save,
        )
        # year_fetch_diagnostics IS the summary (no 'summary' sub-key).
        # Per-year entries live in year_records (top-level).
        # 已判死年份的陈旧声称不计入汇总（2026-10-19）：否则 is_data_complete() 永远 False ✗
        _effective_hist_total = None
        for _record in iter_year_records(year_records_to_save):
            _effective_hist_total = (_effective_hist_total or 0) + _year_effective_histogram(_record)
        year_fetch_diagnostics_save = {
            'scholar_total': count_summary['scholar_total'],
            'histogram_total': (_effective_hist_total
                                if _effective_hist_total is not None
                                else count_summary['histogram_total']),
            'cached_total': count_summary['cached_total'],
            'cached_year_total': count_summary['cached_year_total'],
            'seen_total': count_summary['seen_total'],
            'cached_unyeared_count': count_summary['cached_unyeared_count'],
            'dedup_count': count_summary['dedup_count'],
            'scholar_unyeared_count': count_summary['scholar_unyeared_count'],
        }
        if diagnostics_to_save:
            diagnostics_to_save['scholar_total'] = count_summary['scholar_total']
            diagnostics_to_save['cached_total'] = len(citations_to_save)
            diagnostics_to_save['seen_total'] = len(citations_to_save) + effective_dedup
            diagnostics_to_save['dedup_count'] = effective_dedup
            diagnostics_to_save.setdefault('termination_reason', 'iterator_exhausted')
        cache_data = {
            'title': title,
            'pub_url': pub_url,
            'citedby_url': citedby_url,
            'num_citations_on_scholar': current_scholar_total(),
            'num_citations_cached': len(citations_to_save),
            'num_citations_seen': len(citations_to_save) + effective_dedup,
            'dedup_count': effective_dedup,
            'complete_fetch_attempt': _compute_fetch_attempt_complete(
                fetch_finished, is_year,
                year_fetch_diagnostics_save if is_year else diagnostics_to_save,
                year_records=year_records_to_save,
            ),
            'completed_years': sorted(year_ctx.completed_year_segments),
            'partial_year_start': fetcher._dump_year_count_map(
                getattr(fetcher, '_live_partial_year_start', None)
                or year_ctx.partial_year_start
            ),
            'probed_year_counts': fetcher._dump_year_count_map(
                fetcher._normalize_year_count_map(year_ctx.probed_year_counts)
            ),
            'cached_year_counts': fetcher._dump_year_count_map(year_ctx.cached_year_counts),
            'fetch_strategy': (
                'year' if is_year else
                'direct' if diagnostics_to_save and diagnostics_to_save.get('scholar_total') is not None else
                None
            ),
            'year_fetch_diagnostics': year_fetch_diagnostics_save,
            'year_records': year_records_to_save,
            'direct_fetch_diagnostics': diagnostics_to_save,
            'fetched_at': datetime.now().isoformat(),
            'citations': citations_to_save,
        }
        direct_resume = (
            _build_direct_resume_state(
                direct_next_index,
                current_scholar_total(),
                citedby_url,
            )
            if fetch_policy['strategy'] == 'direct' and not _compute_fetch_attempt_complete(
                fetch_finished, is_year,
                diagnostics_to_save,
            )
            else None
        )
        if direct_resume is not None:
            cache_data['direct_resume_state'] = direct_resume
        if not getattr(fetcher, '_mid_paper_state', None):
            fetcher._mid_paper_state = {}
        fetcher._mid_paper_state[title] = cache_data

    fetch_policy = fetch_policy or fetcher._resolve_citation_fetch_policy(
        current_scholar_total(),
        pub_year,
    )

    # Year-based fetch: for papers with many citations, fetch by year
    # so current-run completed years are tracked and resume is efficient
    if fetch_policy['strategy'] == 'year':
        # Sync year_ctx state to fetcher before calling _fetch_by_year so that
        # tests patching fetcher._fetch_by_year can read the correct state.
        fetcher._cached_year_counts = year_ctx.cached_year_counts
        fetcher._dedup_count = year_ctx.dedup_count
        fetcher._completed_year_segments = year_ctx.completed_year_segments
        fetcher._partial_year_start = year_ctx.partial_year_start
        fetcher._probed_year_counts = year_ctx.probed_year_counts
        fetcher._probed_year_count_complete = year_ctx.probed_year_count_complete
        fetcher._year_fetch_diagnostics = year_ctx.year_fetch_diagnostics
        return fetcher._fetch_by_year(
            citedby_url, old_citations, fresh_citations, save_progress,
            current_scholar_total(), pub_year, prev_scholar_count,
            allow_incremental_early_stop=allow_incremental_early_stop,
            force_year_rebuild=force_year_rebuild,
            selective_refresh_years=selective_refresh_years,
            year_fetch_diagnostics=year_ctx.year_fetch_diagnostics,
        )

    # Direct fetch — single BatchFetchSession
    direct_batch = BatchFetchSession(
        _url=_direct_request_url(citedby_url, normalized_direct_resume_state),
    )

    run_log.debug("Direct fetch mode: no year probe, summary shown after fetch",
                  module="fetch", indent=2)
    run_log.debug(f"Direct fetch target: scholar_total={current_scholar_total()}, "
                  f"prev_scholar={prev_scholar_count}, "
                  f"cached_total={len(old_citations)}"
                  f"{_direct_resume_log_suffix(normalized_direct_resume_state)}",
                  module="fetch", indent=2)
    fetcher._current_attempt_url = _scholar_request_url(direct_batch.url)

    old_cache_id_keys = set()
    for citation in old_citations:
        old_cache_id_keys.update(fetcher._citation_identity_keys(citation))
    counted_new_keys = set()  # prevent double-counting across retries

    def _direct_on_citation(info, identity_keys, is_new, is_dupe, existing_label):
        if is_dupe:
            run_log.debug(f"[dedup] Skipping duplicate: {info['title'][:50]}... "
                          f"({info.get('venue', 'N/A')}, {info.get('year', '?')}); "
                          f"existing: {existing_label}",
                          module="fetch", indent=3)
        else:
            if is_new:
                if not any(k in counted_new_keys for k in identity_keys):
                    fetcher._new_citations_count += 1
                    for k in identity_keys:
                        counted_new_keys.add(k)
            count = len(direct_batch.citations)
            run_log.debug(f"[{count}] {info['title'][:55]}...",
                          module="fetch", indent=3)

    def _direct_on_page_complete(batch):
        _run_page_cite_hook(fetcher, batch)
        save_progress(fetch_finished=False, batch=batch)
        if batch.items_on_page >= SCHOLAR_PAGE_SIZE:
            run_log.debug(f"Progress saved: {len(batch.citations)} fetched this "
                          f"paper, {fetcher._new_citations_count} new this paper",
                          module="fetch", indent=2)

    direct_iterator = fetcher._iter_direct_citedby(
        citedby_url,
        normalized_direct_resume_state,
        num_citations=current_scholar_total(),
    )
    try:
        direct_batch.run(
            fetcher,
            old_cache_identity_keys=old_cache_id_keys,
            on_citation=_direct_on_citation,
            on_page_complete=_direct_on_page_complete,
            iterator=direct_iterator,
            max_retries=0,
        )
    except KeyboardInterrupt:
        save_progress(fetch_finished=False, batch=direct_batch)
        raise

    fresh_citations[:] = list(direct_batch.citations)
    year_ctx.dedup_count += direct_batch.dedup_count
    direct_fetch_termination_reason = direct_batch.termination_reason
    if (not direct_batch.citations
            and current_scholar_total() > 0
            and direct_fetch_termination_reason == 'iterator_exhausted'):
        # Scholar reports citations for this paper yet the list came back with
        # no rows at all: the response was empty or unrecognised instead of the
        # list being exhausted.  Record it distinguishably so the state does not
        # read as a normal end-of-list fetch forever (the page-level evidence is
        # in the 'Page items: 0' line above).
        direct_fetch_termination_reason = 'empty_first_page'
        run_log.warn(f"Citation list returned 0 rows for "
                     f"scholar_total={current_scholar_total()} "
                     f"({_scholar_request_url(direct_batch.url)}) "
                     f"— recording termination as empty_first_page",
                     module="fetch", indent=2)

    direct_fetch_diagnostics = _build_direct_fetch_diagnostics(
        scholar_total=current_scholar_total(),
        cached_total=len(fresh_citations),
        seen_total=len(fresh_citations) + year_ctx.dedup_count,
        dedup_count=year_ctx.dedup_count,
        termination_reason=direct_fetch_termination_reason,
    )
    direct_materialized_cache = materialized_citations(False)
    direct_materialized_total = len(direct_materialized_cache)
    cached_year_map = fetcher._year_count_map(direct_materialized_cache)
    cached_year_sum = sum(cached_year_map.values())
    run_log.debug("Probe summary: none", module="fetch", indent=2)
    run_log.debug(f"Probe totals: scholar_total={current_scholar_total()}, "
                  f"year_sum=0, missing_from_histogram=?", module="fetch", indent=2)
    run_log.debug(f"Cache summary: "
                  f"{fetcher._format_year_count_summary(cached_year_map)}",
                  module="fetch", indent=2)
    run_log.debug(f"Cache totals: cached_total={direct_materialized_total}, "
                  f"cached_year_sum={cached_year_sum}, "
                  f"cached_unyeared={direct_materialized_total - cached_year_sum}, "
                  f"dedup_num={year_ctx.dedup_count}", module="fetch", indent=2)
    s = direct_fetch_diagnostics
    new_count = len(fresh_citations)
    total_count = direct_materialized_total
    run_log.info(f"Direct fetch totals: scholar_total={s['scholar_total']}, "
                 f"new={new_count}, total_cached={total_count}, "
                 f"seen_total={s['seen_total']}", module="fetch", indent=2)
    if _direct_fetch_is_underfetched(direct_fetch_diagnostics):
        run_log.warn(_direct_fetch_diagnostics_message(
            direct_fetch_diagnostics, prefix='Direct fetch under-fetched '),
            module="fetch", indent=2)
    save_progress(fetch_finished=True, batch=direct_batch)
    return list(fresh_citations)

def _build_year_fetch_plan(start_year, current_year, prev_scholar_count, num_citations,
                           allow_incremental_early_stop=True):
    is_update_mode = (
        allow_incremental_early_stop
        and prev_scholar_count > 0
        and prev_scholar_count < num_citations
    )
    if is_update_mode:
        return {
            'year_range': range(current_year, start_year - 1, -1),
            'is_update_mode': True,
            'direction_label': 'newest→oldest',
            'direction_reason': 'update mode, incremental early stop enabled',
        }
    return {
        'year_range': range(start_year, current_year + 1),
        'is_update_mode': False,
        'direction_label': 'oldest→newest',
        'direction_reason': ('recheck mode, full year revalidation'
                             if not allow_incremental_early_stop
                             else 'full scan mode'),
    }


def fetch_by_year(fetcher, year_ctx, citedby_url, old_citations, fresh_citations, save_progress,
                    num_citations, pub_year, prev_scholar_count=0,
                    allow_incremental_early_stop=True,
                    force_year_rebuild=False,
                    selective_refresh_years=None,
                    year_fetch_diagnostics=None,
                    fetch_mode='normal'):
    """
    Fetch citations year-by-year. Skips completed years and uses
    start_index within partially completed years for efficient resume.
    prev_scholar_count: Scholar count from last completed scan, used for early stop.
    allow_incremental_early_stop: controls both update-mode incremental early stop
        and the corresponding newest→oldest fetch direction. Recheck/full-scan flows
        should pass False to force full revalidation order.
    force_year_rebuild: when True, fetched years replace cached year slices.
    selective_refresh_years: optional iterable of years to revalidate.
    """
    import re as _re
    m = _re.search(r"cites=([\d,]+)", citedby_url)
    if not m:
        raise ValueError(f"Cannot extract publication ID from citedby_url: {citedby_url}")
    pub_id = m.group(1)

    old_year_buckets = fetcher._citation_year_buckets(old_citations)
    old_cache_identity_keys = set()
    for citation in old_citations:
        old_cache_identity_keys.update(fetcher._citation_identity_keys(citation))
    fresh_year_buckets = fetcher._citation_year_buckets(fresh_citations)
    fresh_unyeared = list(fresh_year_buckets.pop(None, []))

    def current_citations(complete=False):
        if complete or force_year_rebuild:
            refreshed_unyeared = fresh_unyeared if fresh_unyeared else None
            return fetcher._materialize_year_fetch_citations(
                old_citations,
                fresh_year_buckets,
                refreshed_unyeared=refreshed_unyeared,
            )
        return fetcher._overlay_citations_by_identity(
            old_citations,
            fresh_unyeared + [
                citation
                for year in sorted(fresh_year_buckets.keys())
                for citation in fresh_year_buckets[year]
            ],
        )

    year_count_map = fetcher._year_count_map(old_citations)
    probed_year_counts = fetcher._normalize_year_count_map(year_ctx.probed_year_counts)
    cached_year_counts = fetcher._normalize_year_count_map(year_ctx.cached_year_counts)
    if not cached_year_counts:
        cached_year_counts = fetcher._year_count_map(old_citations)
    year_fetch_diagnostics = fetcher._normalize_year_fetch_diagnostics(
        year_fetch_diagnostics if year_fetch_diagnostics is not None else year_ctx.year_fetch_diagnostics
    )
    year_ctx.year_fetch_diagnostics = dict(year_fetch_diagnostics)

    import sys as _sys
    _dt = getattr(_sys.modules.get(type(fetcher).__module__, None), 'datetime', None) or datetime
    current_year = _dt.now().year
    selective_refresh_years = None if selective_refresh_years is None else set(selective_refresh_years)
    explicit_refresh_years = set(selective_refresh_years or ())
    explicit_refresh_years.update(int(year) for year in year_ctx.partial_year_start.keys())
    if year_ctx.completed_year_segments and explicit_refresh_years:
        start_year = min(min(year_ctx.completed_year_segments), min(explicit_refresh_years))
        if year_count_map:
            start_year = min(start_year, min(year_count_map.keys()))
    else:
        start_year = fetcher._probe_citation_start_year(
            citedby_url,
            fetch_ctx=year_ctx,
            num_citations=num_citations,
            pub_year=pub_year,
        )
        if start_year is None:
            if year_count_map:
                start_year = min(year_count_map.keys())
            else:
                try:
                    start_year = int(pub_year) - 5 if pub_year and pub_year not in ('N/A', '?') else None
                except (ValueError, TypeError):
                    start_year = None
                if start_year is None:
                    start_year = current_year - 5
        elif year_count_map:
            cache_min = min(year_count_map.keys())
            if cache_min < start_year:
                run_log.info(f"Using cache min year {cache_min} "
                             f"(probe returned {start_year})",
                             module="year", indent=2)
                start_year = cache_min

    total_years = current_year - start_year + 1
    skipped_years = 0

    run_log.info(f"Year-based plan: {start_year}-{current_year} "
                 f"(current-run completed={len(year_ctx.completed_year_segments)})",
                 module="plan", indent=1)

    year_range = range(start_year, current_year + 1)

    probed_year_counts = fetcher._normalize_year_count_map(year_ctx.probed_year_counts)
    is_year = True  # fetch_by_year is only entered in year mode
    count_summary = fetcher._build_citation_count_summary(
        old_citations,
        scholar_total=num_citations,
        probed_year_counts=probed_year_counts,
        probe_complete=is_year,
        dedup_count=year_ctx.dedup_count,
        year_fetch_diagnostics=year_fetch_diagnostics,
    )
    cached_total_citations = count_summary['cached_total']
    cached_year_total = count_summary['cached_year_total']
    cached_seen_total = count_summary['seen_total']
    cached_unyeared_citations = count_summary['cached_unyeared_count']
    probed_hist_total = count_summary['histogram_total']
    probed_missing_from_histogram = count_summary['scholar_unyeared_count']
    histogram_authoritative = probed_hist_total > 0
    run_log.debug(f"Probe summary: "
                  f"{fetcher._format_year_count_summary(probed_year_counts)}",
                  module="year", indent=2)
    if num_citations is None:
        run_log.debug(f"Probe totals: scholar_total=?, "
                      f"year_sum={probed_hist_total}, "
                      f"missing_from_histogram=?", module="year", indent=2)
    else:
        run_log.debug(f"Probe totals: scholar_total={num_citations}, "
                      f"year_sum={probed_hist_total}, "
                      f"missing_from_histogram={probed_missing_from_histogram}",
                      module="year", indent=2)
    run_log.debug(f"Cache summary: "
                  f"{fetcher._format_year_count_summary(cached_year_counts)}",
                  module="year", indent=2)
    run_log.debug(f"Cache totals: cached_total={cached_total_citations}, "
                  f"seen_total={cached_seen_total}, "
                  f"cached_year_sum={cached_year_total}, "
                  f"cached_unyeared={cached_unyeared_citations}, "
                  f"dedup_num={year_ctx.dedup_count}", module="year", indent=2)
    effective_target = probed_hist_total if histogram_authoritative else num_citations
    probe_note = "" if is_year else " (no histogram probe)"
    run_log.debug(f"Fetch context: prev_scholar={prev_scholar_count}, "
                  f"target={effective_target}, total_years={total_years}{probe_note}",
                  module="year", indent=2)
    run_log.info(f"Current-run completed years: "
                 f"{fetcher._format_year_set_summary(year_ctx.completed_year_segments)}",
                 module="year", indent=2)
    run_log.info(f"Partial resume points: "
                 f"{fetcher._format_partial_year_start_summary(year_ctx.partial_year_start)}",
                 module="year", indent=2)
    # Sync stale diagnostics with current probe/cache values so that
    # year_fetch_diagnostic_matches_total can correctly recognise years
    # that were completed in a prior run even when the cite count has grown.
    if year_fetch_diagnostics and is_year:
        for year, diag in list(year_fetch_diagnostics.items()):
            current_probe = probed_year_counts.get(year)
            current_cached = cached_year_counts.get(year)
            if current_probe is not None and diag.get('histogram_count', diag.get('scholar_total')) != current_probe:
                diag['histogram_count'] = current_probe
            if current_cached is not None and diag.get('cached_total') != current_cached:
                dedup = diag.get('dedup_count', 0)
                diag['cached_total'] = current_cached
                diag['seen_total'] = current_cached + max(0, dedup)
        year_ctx.year_fetch_diagnostics = dict(year_fetch_diagnostics)

    stop_partial_resume_once_satisfied = (
        is_year
        and bool(year_ctx.partial_year_start)
        and cached_year_counts == probed_year_counts
    )

    try:
        for year in year_range:
            # Rule: skip only if already completed in this run, OR
            # the previous record's seen >= current probe's histogram_count.
            if year in year_ctx.completed_year_segments:
                skipped_years += 1
                existing_diag = year_fetch_diagnostics.get(year)
                if existing_diag:
                    year_fetch_diagnostics[year] = fetcher._build_year_fetch_diagnostics(
                        year,
                        existing_diag.get('histogram_count', existing_diag.get('scholar_total', probed_year_counts.get(year, 0))),
                        existing_diag.get('cached_total', cached_year_counts.get(year, 0)),
                        existing_diag.get('dedup_count', 0),
                        'completed_earlier_in_run',
                    )
                    year_ctx.year_fetch_diagnostics = dict(year_fetch_diagnostics)
                run_log.info(f"Year {year}: skip (already completed earlier in this run)",
                             module="year", indent=2)
                continue

            live_count = (probed_year_counts or {}).get(year)
            existing_diag = year_fetch_diagnostics.get(year)
            prev_seen = (existing_diag or {}).get('seen_total') if existing_diag else None
            if prev_seen is None:
                prev_seen = cached_year_counts.get(year, 0)
            if live_count is not None:
                if fetch_mode == 'exact':
                    should_skip = (prev_seen == live_count)
                else:
                    should_skip = (prev_seen >= live_count)
                if should_skip:
                    skipped_years += 1
                    reason = 'seen_total_exact_match' if fetch_mode == 'exact' else 'seen_total_match_skip'
                    year_fetch_diagnostics[year] = fetcher._build_year_fetch_diagnostics(
                        year,
                        live_count,
                        cached_year_counts.get(year, 0),
                        (existing_diag or {}).get('dedup_count', 0),
                        reason,
                    )
                    year_ctx.year_fetch_diagnostics = dict(year_fetch_diagnostics)
                    verb = '==' if fetch_mode == 'exact' else '>='
                    run_log.info(f"Year {year}: skip (seen={prev_seen} {verb} probe={live_count})",
                                 module="year", indent=2)
                    save_progress(fetch_finished=False)
                    continue
            elif prev_seen is not None and prev_seen > 0 and histogram_authoritative:
                # Probe has no data for this year — the histogram
                # doesn't list it at all, so there's nothing new.
                # Only valid while the histogram is authoritative for this
                # paper: when the probe failed (no per-year counts at all),
                # "absent from the histogram" says nothing about the year, and
                # skipping it would silently stop fetching a year that has
                # cached citations but newly added rows.
                skipped_years += 1
                year_fetch_diagnostics[year] = fetcher._build_year_fetch_diagnostics(
                    year,
                    prev_seen,
                    cached_year_counts.get(year, 0),
                    (existing_diag or {}).get('dedup_count', 0),
                    'no_probe_data_skip',
                )
                year_ctx.year_fetch_diagnostics = dict(year_fetch_diagnostics)
                run_log.info(f"Year {year}: skip (seen={prev_seen}, "
                             f"not in histogram)", module="year", indent=2)
                save_progress(fetch_finished=False)
                continue
            elif prev_seen is not None and prev_seen > 0:
                run_log.info(f"Year {year}: fetching (seen={prev_seen}, no histogram "
                             f"probe — an absent year is not evidence of completeness)",
                             module="year", indent=2)

            year_ctx.current_paper_page_count = 0  # reset per year in year-based mode
            prev_short_attempts = 0
            prev_stalled = False
            prev_stalled_probe = 0
            prev_seen_for_stall = 0
            _prev_diag = year_fetch_diagnostics.get(year)
            if isinstance(_prev_diag, dict):
                try:
                    prev_short_attempts = int(_prev_diag.get('short_attempts') or 0)
                except (TypeError, ValueError):
                    prev_short_attempts = 0
                prev_stalled = bool(_prev_diag.get('stalled'))
                try:
                    prev_stalled_probe = int(_prev_diag.get('stalled_probe') or 0)
                except (TypeError, ValueError):
                    prev_stalled_probe = 0
                try:
                    prev_seen_for_stall = int(_prev_diag.get('seen_total') or 0)
                except (TypeError, ValueError):
                    prev_seen_for_stall = 0
            if prev_stalled and (live_count is None or live_count <= prev_stalled_probe):
                # 走完两遍都只有这么多条 → "网页信息与 probe 不符"（用户 2026-10-19 定），
                # 不是没抓全：跳过这一年 ✗，等 probe 涨了再说 ✓。
                year_ctx.year_fetch_diagnostics = dict(year_fetch_diagnostics)
                run_log.info(f"Year {year}: settled (listing holds {prev_seen_for_stall}, "
                             f"Scholar claims {live_count if live_count is not None else '?'} "
                             f"— the listing is what it is)", module="year", indent=2)
                save_progress(fetch_finished=False)
                continue
            start_index = _resume_start_after_short_attempts(
                year_ctx.partial_year_start.get(year, 0), prev_short_attempts)
            rescanned_from_zero = (start_index == 0
                                   and year_ctx.partial_year_start.get(year, 0) > 0)
            if rescanned_from_zero:
                # 偏移续跑快、但"位置"会随列表变动而漏行 ✗（用户 2026-10-19）。
                # 连续这么多次都没收全 → 干脆从第 1 页重扫一遍，用身份键去重 ✓。
                run_log.warn(f"Year {year}: re-scanning from page 1 "
                             f"(stayed short {prev_short_attempts} times; "
                             f"the resume offset is not trusting itself again)",
                             module="year", indent=2)
                start_index = 0
            resume_page_start = _page_aligned_start(start_index)
            initial_in_page_skip = start_index - resume_page_start
            resuming_partial_year = year in year_ctx.partial_year_start
            cached_count = cached_year_counts.get(year)
            live_count = probed_year_counts.get(year)
            # Compute seen the same way as the skip check above so the log
            # matches what was actually compared.
            existing_diag_for_log = year_fetch_diagnostics.get(year)
            seen_for_log = ((existing_diag_for_log or {}).get('seen_total')
                            if existing_diag_for_log else None)
            if seen_for_log is None:
                seen_for_log = cached_count

            if not fetcher.interactive_captcha:
                fetcher._refresh_scholarly_session()
            fetcher._next_refresh_at = fetcher._total_page_count + random.randint(SESSION_REFRESH_MIN, SESSION_REFRESH_MAX)

            if start_index > 0:
                resume_note = f"position {start_index}"
                if resume_page_start != start_index:
                    resume_note += (
                        f" via page start {resume_page_start} "
                        f"(skip first {initial_in_page_skip})"
                    )
                run_log.info(f"Year {year}: resuming from {resume_note} "
                             f"(seen={seen_for_log}, "
                             f"probe={live_count if live_count is not None else '?'})",
                             module="year", indent=2)
            else:
                run_log.info(f"Year {year}: fetching "
                             f"(seen={seen_for_log}, "
                             f"probe={live_count if live_count is not None else '?'})",
                             module="year", indent=2)

            year_url = (f'/scholar?as_ylo={year}&as_yhi={year}&hl=en'
                        f'&as_sdt=2005&sciodt=0,5&cites={pub_id}&scipsc=')
            # The URL itself is logged once, by the session layer's
            # "Request URL:" line — printing it here too duplicated it on
            # every single year.

            year_new_count = 0
            year_dedup_count = 0
            year_progress_saved = False
            counted_new_keys = year_ctx.counted_new_keys  # cross-year dedup within this run
            existing_year_fresh = list(old_year_buckets.get(year, [])) if start_index > 0 else []
            year_seen_keys = {}
            for c in existing_year_fresh:
                label = f"{c.get('title', '')[:50]} ({c.get('venue', 'N/A')}, {c.get('year', '?')}) [cached]"
                for key in fetcher._citation_identity_keys(c):
                    year_seen_keys[key] = label

            old_year_identity_keys = set()
            for citation in (old_year_buckets.get(year) or []):
                old_year_identity_keys.update(fetcher._citation_identity_keys(citation))

            year_batch = BatchFetchSession(
                _url=year_url,
                _citations=list(existing_year_fresh),
                _start_index=start_index,
            )

            def _on_year_citation(info, identity_keys, is_new, is_dupe, existing_label):
                nonlocal year_new_count
                if is_dupe:
                    run_log.debug(f"[dedup] Skipping duplicate: "
                                  f"{info['title'][:50]}... "
                                  f"({info.get('venue', 'N/A')}, "
                                  f"{info.get('year', '?')}); existing: {existing_label}",
                                  module="year", indent=3)
                else:
                    if is_new:
                        # Cross-year dedup within this run: only count if not
                        # already counted as new in another year.
                        if not any(k in counted_new_keys for k in identity_keys):
                            fetcher._new_citations_count += 1
                            for k in identity_keys:
                                counted_new_keys.add(k)
                        year_new_count += 1
                    elif not any(k in old_year_identity_keys for k in identity_keys):
                        year_new_count += 1
                    fresh_year_buckets[year] = list(year_batch.citations)
                    fresh_citations[:] = current_citations(complete=True)
                    count = len(fresh_citations)
                    run_log.debug(f"[{count}] {info['title'][:55]}...",
                                  module="year", indent=3)

            def _on_year_page_complete(batch):
                nonlocal year_progress_saved, year_dedup_count, year_new_count
                _run_page_cite_hook(fetcher, batch)
                year_dedup_count = batch.dedup_count
                year_ctx.partial_year_start[year] = _page_aligned_start(batch.start_index)
                save_progress(fetch_finished=False, batch=batch)
                year_progress_saved = True
                if batch.items_on_page >= SCHOLAR_PAGE_SIZE:
                    run_log.debug(f"Progress saved: {len(batch.citations)} fetched "
                                  f"for year {year}, {year_new_count} new in year, "
                                  f"{fetcher._new_citations_count} truly new this paper",
                                  module="year", indent=2)

            year_batch.run(
                fetcher,
                fallback_year=year,
                seen_keys=year_seen_keys,
                old_cache_identity_keys=old_cache_identity_keys,
                on_citation=_on_year_citation,
                on_page_complete=_on_year_page_complete,
                max_retries=1,
            )
            year_dedup_count = year_batch.dedup_count
            year_termination_reason = year_batch.termination_reason
            if (live_count is not None and live_count > 0
                    and not year_batch.citations
                    and year_termination_reason == 'iterator_exhausted'):
                # The probe says this year has citations but its page came back
                # with no rows at all — empty/unrecognised rather than exhausted.
                year_termination_reason = 'empty_page'
                run_log.warn(f"Year {year}: page returned 0 rows but the probe says "
                             f"{live_count} — recording termination as empty_page",
                             module="year", indent=2)

            cached_bucket = list(old_year_buckets.get(year) or [])
            year_bucket_count = len(year_batch.citations)
            if year_termination_reason == 'empty_page':
                # A page with no rows while the histogram promises some is an
                # empty/blocked/interstitial response, not an authoritative
                # "this year has nothing".  Do not touch the year's bucket (the
                # cached citations stay exactly where they are) and leave the
                # year incomplete so it is retried.
                fresh_year_buckets.pop(year, None)
                year_bucket_count = len(cached_bucket)
                run_log.warn(f"Year {year}: keeping {year_bucket_count} cached "
                             f"citation(s); an empty page is not an authoritative rebuild",
                             module="year", indent=2)
            elif len(year_batch.citations) < len(cached_bucket) and not force_year_rebuild:
                # A fetch must never delete citations it did not return.  A full
                # re-scan started at page 0 seeds the batch empty, so a truncated
                # or blocked page (or a lossy year filter) comes back with fewer
                # rows than the cache; keep the cached rows and add the new ones.
                # exact mode (force_year_rebuild) deliberately rebuilds the
                # bucket from Scholar, so it is exempt.
                cached_by_key = {}
                for citation in cached_bucket:
                    for key in fetcher._citation_identity_keys(citation):
                        cached_by_key.setdefault(key, citation)
                extra = []
                for citation in year_batch.citations:
                    target = next((cached_by_key[key]
                                   for key in fetcher._citation_identity_keys(citation)
                                   if key in cached_by_key), None)
                    if target is None:
                        extra.append(citation)
                    else:
                        # 同一身份：缓存行留下 ✓，但**把这一行的 Cite 数据并进去** ✓
                        # （用户 2026-10-19：逐页抓来的 `cite` 不许因为"只保留缓存"而丢 ✗）
                        _merge_cite_into(target, citation)
                merged_bucket = cached_bucket + extra
                fresh_year_buckets[year] = merged_bucket
                year_bucket_count = len(merged_bucket)
                run_log.warn(f"Year {year}: fetch returned {len(year_batch.citations)} < "
                             f"{len(cached_bucket)} cached; keeping the cached rows "
                             f"({len(merged_bucket)} total)", module="year", indent=2)
            else:
                fresh_year_buckets[year] = list(year_batch.citations)
            fresh_citations[:] = current_citations(complete=True)
            if year_termination_reason != 'empty_page':
                year_ctx.partial_year_start.pop(year, None)
                year_ctx.completed_year_segments.add(year)
            year_ctx.dedup_count += year_batch.dedup_count
            live_count_for_diag = live_count if live_count is not None else len(year_batch.citations)
            _short_this_attempt = (live_count is not None
                                   and year_bucket_count < live_count)
            # 重扫（从第 1 页走完）拿到的条数与上一轮**完全相同** → 这是"网页信息与 probe
            # 不符"，不是没抓全（用户 2026-10-19 定）：记下 probe，之后 probe 不涨就不再抓 ✗。
            _stall_now = bool(rescanned_from_zero
                              and _short_this_attempt
                              and year_termination_reason != 'empty_page'
                              and year_bucket_count == prev_seen_for_stall)
            if _stall_now:
                run_log.warn(f"Year {year}: stalled — two full scans both hold "
                             f"{year_bucket_count} while Scholar claims {live_count}; "
                             f"treating the probe as stale", module="year", indent=2)
            year_diag = fetcher._build_year_fetch_diagnostics(
                year,
                live_count_for_diag,
                year_bucket_count,
                year_batch.dedup_count,
                year_termination_reason,
                0 if _stall_now else ((prev_short_attempts + 1) if _short_this_attempt else 0),
                _stall_now,
                (live_count if live_count is not None else prev_stalled_probe) if _stall_now else 0,
            )
            if year_diag is not None:
                # seen_total is the number of DISTINCT rows the year holds.
                # _build_year_fetch_diagnostics computes cached_total + dedup, but
                # when the year scan resumes mid-list the batch is seeded from the
                # cached bucket, so the rows it reports as duplicates are already
                # inside cached_total.  Adding them again could report a short year
                # as complete.  The duplicate count stays in the log line below.
                year_diag['seen_total'] = year_bucket_count
                year_fetch_diagnostics[year] = year_diag
            year_ctx.year_fetch_diagnostics = dict(year_fetch_diagnostics)
            if year_new_count > 0 or fetcher._new_citations_count > 0:
                run_log.info(f"Year {year} done: {year_new_count} new in year, "
                             f"{fetcher._new_citations_count} truly new this paper",
                             module="year", indent=2)
            else:
                run_log.info(f"Year {year} done: no new citations",
                             module="year", indent=2)
            diag = year_fetch_diagnostics[year]
            underfetched = diag['seen_total'] < diag.get('histogram_count', diag.get('scholar_total', 0))
            # One line per year: the counters and the diagnosis they came from.
            # These used to be two ("compare:" and "status:") describing the
            # same year in the same breath.
            run_log.info(
                f"Year {year} status: year_total={len(year_batch.citations)}, "
                f"year_new={year_new_count}, pages={year_ctx.current_paper_page_count}, "
                f"skipped_years={skipped_years} | "
                f"histogram={diag.get('histogram_count', diag.get('scholar_total'))}, "
                f"seen={diag['seen_total']}, cached={diag['cached_total']}, "
                f"dedup={diag['dedup_count']}, underfetched={underfetched}, "
                f"termination={diag['termination_reason']}",
                module="year", indent=2,
            )
            if not year_progress_saved:
                save_progress(fetch_finished=False)

            if stop_partial_resume_once_satisfied and resuming_partial_year and live_count is not None and len(year_batch.citations) >= live_count:
                year_fetch_diagnostics[year] = fetcher._build_year_fetch_diagnostics(
                    year,
                    live_count,
                    len(year_batch.citations),
                    year_dedup_count,
                    'partial_resume_completed',
                )
                year_ctx.year_fetch_diagnostics = dict(year_fetch_diagnostics)
                run_log.info(f"Year {year}: completed resumed year segment; "
                             f"skipping the remaining years",
                             module="year", indent=1)
                break

    except KeyboardInterrupt:
        save_progress(fetch_finished=False)
        raise
    except Exception:
        save_progress(fetch_finished=False)
        raise

    fresh_citations[:] = current_citations(complete=True)
    for _line in fetcher._year_fetch_log_message(year_fetch_diagnostics).split("\n"):
        run_log.info(_line, module="year", indent=2)
    save_progress(fetch_finished=True)
    return list(fresh_citations)


def _merge_cite_into(cached, fresh):
    """把新抓那一行的 **Cite 数据**并进缓存行（只补/升级 ✓；身份字段一律不碰 ✗）。

    用户 2026-10-19 定：逐页抓到的 `cite` 是为那几条付过钱的 —— 若同一身份已在缓存里，
    **不能整条丢掉** ✗，至少要把 `cite`（与寻址用的 `cid`）补进缓存行 ✓。

    * 只认 `cite` 与 `cid` 两个键 ✓；`title` / `authors` / `venue` / `year` / `url` /
      `cites_id` / `venue_key*` 一律不动 ✗（前者由 Chicago 修正那条路管，后两者是派生字段、
      保存时重算 ✓）。
    * **永不降级**：缓存已有 `cite` 且格式数不少于新的 → 保持原样 ✓（新的为空/更少时更不该清空 ✗）。
    """
    if not isinstance(cached, dict) or not isinstance(fresh, dict):
        return False
    changed = False
    fresh_cite = fresh.get("cite")
    if isinstance(fresh_cite, dict) and fresh_cite:
        current = cached.get("cite")
        if not isinstance(current, dict) or len(fresh_cite) > len(current):
            cached["cite"] = dict(fresh_cite)
            changed = True
    if not cached.get("cid") and fresh.get("cid"):
        cached["cid"] = fresh["cid"]
        changed = True
    return changed
