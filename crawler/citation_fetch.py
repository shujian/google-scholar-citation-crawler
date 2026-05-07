"""
crawler/citation_fetch.py — Citation fetch engine: direct and year-based modes.

Public API:
  fetch_citations_with_progress(fetcher, ctx, citedby_url, cache_path, title, ...)
  fetch_by_year(fetcher, ctx, citedby_url, old_citations, fresh_citations, ...)

Both functions accept:
  fetcher  — the PaperCitationFetcher instance (for method calls and run-level state)
  ctx      — a FetchContext dataclass (for per-paper mutable state)

All direct-fetch helper functions are pure module-level functions.
"""

import json
import random
import re
import time
from datetime import datetime

from scholarly import scholarly
import scholarly.publication_parser as _pub_parser

from crawler.common import (
    SCHOLAR_PAGE_SIZE,
    SESSION_REFRESH_MAX,
    SESSION_REFRESH_MIN,
    _scholar_request_url,
    now_str,
    rand_delay,
)
from crawler.fetch_context import FetchContext


def _normalize_direct_resume_state(state):
    if not isinstance(state, dict):
        return None
    if state.get('mode') != 'direct':
        return None
    try:
        next_index = int(state.get('next_index'))
        source_scholar_total = int(state.get('source_scholar_total'))
    except (TypeError, ValueError):
        return None
    citedby_url = state.get('citedby_url')
    if not isinstance(citedby_url, str) or not citedby_url:
        return None
    if next_index < 0 or source_scholar_total < 0 or next_index > source_scholar_total:
        return None
    return {
        'mode': 'direct',
        'next_index': next_index,
        'source_scholar_total': source_scholar_total,
        'citedby_url': citedby_url,
    }

def _direct_resume_log_suffix(state):
    normalized = _normalize_direct_resume_state(state)
    if not normalized:
        return ""
    return f" (direct offset={normalized['next_index']})"

def _build_direct_resume_state(next_index, scholar_total, citedby_url):
    try:
        next_index = int(next_index)
        scholar_total = int(scholar_total)
    except (TypeError, ValueError):
        return None
    if next_index < 0 or scholar_total < 0 or next_index > scholar_total:
        return None
    if not isinstance(citedby_url, str) or not citedby_url:
        return None
    # Align to page boundary so the retry re-fetches the whole page
    # from its start.  The items already saved by save_progress serve
    # as old_citations for dedup, so duplicates are filtered out.
    next_index = _page_aligned_start(next_index)
    return {
        'mode': 'direct',
        'next_index': next_index,
        'source_scholar_total': scholar_total,
        'citedby_url': citedby_url,
    }

def _page_aligned_start(index):
    try:
        index = int(index or 0)
    except (TypeError, ValueError):
        return 0
    if index <= 0:
        return 0
    return (index // SCHOLAR_PAGE_SIZE) * SCHOLAR_PAGE_SIZE

def _direct_start_position(direct_resume_state):
    normalized = _normalize_direct_resume_state(direct_resume_state)
    if not normalized:
        return 0, 0
    next_index = normalized['next_index']
    page_start = _page_aligned_start(next_index)
    return page_start, next_index - page_start

def _append_start_param(citedby_url, start):
    if start <= 0:
        return citedby_url
    separator = '&' if '?' in citedby_url else '?'
    if re.search(r'([?&])start=\d+', citedby_url):
        return re.sub(r'([?&])start=\d+', lambda match: f"{match.group(1)}start={start}", citedby_url)
    return f"{citedby_url}{separator}start={start}"

def _direct_request_url(citedby_url, direct_resume_state=None):
    normalized = _normalize_direct_resume_state(direct_resume_state)
    if not normalized:
        return citedby_url
    page_start, _ = _direct_start_position(normalized)
    return _append_start_param(citedby_url, page_start)

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
        'summary': {
            'scholar_total': scholar_total,
            'cached_total': cached_total,
            'seen_total': seen_total,
            'dedup_count': dedup_count,
            'termination_reason': termination_reason or 'iterator_exhausted',
        },
    }

def _direct_fetch_diagnostics(scholar_total, cached_total, seen_total, dedup_count, termination_reason):
    return _build_direct_fetch_diagnostics(
        scholar_total,
        cached_total,
        seen_total,
        dedup_count,
        termination_reason,
    )

def _direct_fetch_gap(diagnostics):
    """Compute the under-fetch gap from the summary sub-dict."""
    s = diagnostics.get('summary', diagnostics)
    return max(0, (s.get('scholar_total') or 0) - (s.get('seen_total') or 0))

def _direct_fetch_is_underfetched(diagnostics):
    """Return True when the direct fetch did not retrieve all expected citations."""
    s = diagnostics.get('summary', diagnostics)
    return (s.get('seen_total') or 0) < (s.get('scholar_total') or 0)

def _direct_fetch_summary_message(diagnostics):
    s = diagnostics.get('summary', diagnostics)
    return (
        "Direct fetch summary "
        f"(scholar_total={s.get('scholar_total')}, "
        f"cached_total={s.get('cached_total')}, "
        f"seen_total={s.get('seen_total')}, "
        f"dedup_num={s.get('dedup_count')}, "
        f"gap={_direct_fetch_gap(diagnostics)}, "
        f"termination={s.get('termination_reason')})"
    )

def _direct_fetch_log_message(diagnostics):
    s = diagnostics.get('summary', diagnostics)
    return (
        "Direct fetch under-fetched "
        f"(scholar_total={s.get('scholar_total')}, "
        f"cached_total={s.get('cached_total')}, "
        f"seen_total={s.get('seen_total')}, "
        f"dedup_num={s.get('dedup_count')}, "
        f"gap={_direct_fetch_gap(diagnostics)}, "
        f"termination={s.get('termination_reason')})"
    )

def fetch_citations_with_progress(fetcher, ctx, citedby_url, cache_path, title,
                                    num_citations, pub_url, pub_year, resume_from,
                                    completed_years_in_current_run=None, prev_scholar_count=0,
                                    partial_year_start=None, saved_dedup_count=0,
                                    allow_incremental_early_stop=True,
                                    force_year_rebuild=False,
                                    selective_refresh_years=None,
                                    rehydrated_probed_year_counts=None,
                                    rehydrated_probe_complete=False,
                                    rehydrated_year_fetch_diagnostics=None,
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
    ctx.cached_year_counts = fetcher._year_count_map(old_citations)
    fresh_citations = []
    effective_num_citations = int(num_citations or 0)
    if pub_obj is not None:
        effective_num_citations = fetcher._effective_scholar_total(pub_obj)
        pub_obj['num_citations'] = effective_num_citations
    # _dedup_count tracks same-run duplicate rows observed from Scholar within the
    # current fetch flow. Seed from cached direct diagnostics only so resumed direct
    # fetches preserve already-seen duplicate rows from the same in-progress scan.
    ctx.dedup_count = int(saved_dedup_count or 0)

    # Load completed years into patch state for _citedby_long to skip
    ctx.completed_year_segments = set(completed_years_in_current_run or [])
    ctx.current_year_segment = None
    # Track the page offset (start_index) for the year currently in progress.
    # Saved to cache on exception so retry can skip already-fetched pages.
    # Align to page boundary so old caches with non-aligned values (e.g. 101)
    # don't cause request_in_page_skip > 0 and silently drop items.
    ctx.partial_year_start = {
        int(year): _page_aligned_start(idx)
        for year, idx in (partial_year_start or {}).items()
    }
    ctx.probed_year_counts = fetcher._normalize_year_count_map(rehydrated_probed_year_counts) or None
    ctx.probed_year_count_complete = bool(rehydrated_probe_complete and ctx.probed_year_counts)
    ctx.year_fetch_diagnostics = fetcher._normalize_year_fetch_diagnostics(
        rehydrated_year_fetch_diagnostics
    )
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

    def direct_materialized_citations(complete):
        return fetcher._materialize_citation_cache(old_citations, fresh_citations, complete)

    direct_fetch_diagnostics = None
    normalized_direct_resume_state = _normalize_direct_resume_state(direct_resume_state)
    direct_next_index = normalized_direct_resume_state['next_index'] if normalized_direct_resume_state else 0

    def materialized_citations(complete):
        return direct_materialized_citations(complete)

    def build_materialized_year_fetch_diagnostics(citations_to_save):
        # During year-based fetch, _fetch_by_year creates a separate inner FetchContext
        # and syncs its year_fetch_diagnostics to fetcher._live_year_fetch_diagnostics
        # just before calling save_progress.  Use that when available so that per-year
        # dedup counts accumulated in the inner ctx are not lost.
        live = getattr(fetcher, '_live_year_fetch_diagnostics', None)
        source = live if live is not None else ctx.year_fetch_diagnostics
        diagnostics = dict(fetcher._normalize_year_fetch_diagnostics(source))
        year_counts = fetcher._year_count_map(citations_to_save)
        # Histogram data only exists in year mode (probe is done before fetch).
        is_year = fetch_policy.get('strategy') == 'year' if fetch_policy else False
        for year, diagnostic in list(diagnostics.items()):
            if year not in year_counts and year not in (ctx.probed_year_counts or {}):
                diagnostics.pop(year, None)
        for year, cached_total in year_counts.items():
            existing = diagnostics.get(year)
            if existing is not None:
                histogram_count = existing.get('histogram_count', existing.get('scholar_total'))
                if histogram_count is None:
                    histogram_count = (ctx.probed_year_counts or {}).get(year, 0 if not is_year else cached_total)
                diagnostics[year] = fetcher._build_year_fetch_diagnostics(
                    year,
                    histogram_count,
                    cached_total,
                    existing.get('dedup_count', 0),
                    existing.get('termination_reason'),
                )
            else:
                hc = (ctx.probed_year_counts or {}).get(year, 0 if not is_year else cached_total)
                diagnostics[year] = fetcher._build_year_fetch_diagnostics(
                    year, hc, cached_total, 0, None,
                )
        for year, probe_total in (ctx.probed_year_counts or {}).items():
            if year not in diagnostics:
                diagnostics[year] = fetcher._build_year_fetch_diagnostics(
                    year, probe_total, 0, 0, None,
                )
        return fetcher._normalize_year_fetch_diagnostics(diagnostics)

    def save_progress(complete):
        effective_complete = complete
        diagnostics_to_save = direct_fetch_diagnostics
        if diagnostics_to_save and _direct_fetch_is_underfetched(diagnostics_to_save):
            effective_complete = False
        citations_to_save = materialized_citations(effective_complete)
        if not citations_to_save and old_citations:
            citations_to_save = list(old_citations)
        ctx.cached_year_counts = fetcher._year_count_map(citations_to_save)
        is_year = fetch_policy.get('strategy') == 'year' if fetch_policy else False
        # Always build year diagnostics from cached citations so every paper
        # has per-year entries and a summary, even direct-mode papers.
        year_fetch_diagnostics_to_save = build_materialized_year_fetch_diagnostics(citations_to_save)
        ctx.year_fetch_diagnostics = year_fetch_diagnostics_to_save
        # During year-based fetch, use inner-ctx dedup count / probe data synced
        # by _fetch_by_year wrapper.  The outer ctx.probed_year_counts is always
        # None because the probe runs inside the inner fetch_by_year flow.
        live_dedup = getattr(fetcher, '_live_dedup_count', None)
        effective_dedup = live_dedup if live_dedup is not None else ctx.dedup_count
        live_probe = getattr(fetcher, '_live_probed_year_counts', None)
        effective_probe = live_probe if live_probe is not None else ctx.probed_year_counts
        live_probe_complete = getattr(fetcher, '_live_probe_complete', None)
        effective_probe_complete = live_probe_complete if live_probe_complete is not None else ctx.probed_year_count_complete
        count_summary = fetcher._build_citation_count_summary(
            citations_to_save,
            scholar_total=current_scholar_total(),
            probed_year_counts=effective_probe,
            probe_complete=effective_probe_complete,
            dedup_count=effective_dedup,
            year_fetch_diagnostics=year_fetch_diagnostics_to_save,
        )
        # Build summary (formerly citation_count_summary) and nest it inside
        # the appropriate diagnostics object so callers can find it without
        # inspecting both.
        summary = {
            'scholar_total': count_summary['scholar_total'],
            'histogram_total': count_summary['histogram_total'],
            'cached_total': count_summary['cached_total'],
            'cached_year_total': count_summary['cached_year_total'],
            'seen_total': count_summary['seen_total'],
            'cached_unyeared_count': count_summary['cached_unyeared_count'],
            'dedup_count': count_summary['dedup_count'],
            'scholar_unyeared_count': count_summary['scholar_unyeared_count'],
        }
        diag_with_summary = fetcher._dump_year_fetch_diagnostics(year_fetch_diagnostics_to_save)
        diag_with_summary['summary'] = summary
        if diagnostics_to_save:
            # Direct-fetch diagnostics uses top-level recorded counters,
            # not per-year-derived values.  Sync from the actual
            # recorded totals (citations_to_save + effective_dedup)
            # rather than recomputing.
            existing = diagnostics_to_save.setdefault('summary', {})
            existing['scholar_total'] = count_summary['scholar_total']
            existing['cached_total'] = len(citations_to_save)
            existing['seen_total'] = len(citations_to_save) + effective_dedup
            existing['dedup_count'] = effective_dedup
            existing.setdefault('termination_reason', 'iterator_exhausted')
        cache_data = {
            'title': title,
            'pub_url': pub_url,
            'citedby_url': citedby_url,
            'num_citations_on_scholar': current_scholar_total(),
            'num_citations_cached': len(citations_to_save),
            'num_citations_seen': len(citations_to_save) + effective_dedup,
            'dedup_count': effective_dedup,
            'complete': effective_complete,
            'complete_fetch_attempt': complete,
            'completed_years': sorted(ctx.completed_year_segments),
            'probed_year_counts': fetcher._dump_year_count_map(
                fetcher._normalize_year_count_map(ctx.probed_year_counts)
            ),
            'cached_year_counts': fetcher._dump_year_count_map(ctx.cached_year_counts),
            'fetch_strategy': (
                'year' if is_year else
                'direct' if diagnostics_to_save and (diagnostics_to_save.get('summary') or {}).get('scholar_total') is not None else
                None
            ),
            'year_fetch_diagnostics': diag_with_summary,
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
            if fetch_policy['strategy'] == 'direct' and not effective_complete
            else None
        )
        if direct_resume is not None:
            cache_data['direct_resume_state'] = direct_resume
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)

    fetch_policy = fetch_policy or fetcher._resolve_citation_fetch_policy(
        current_scholar_total(),
        pub_year,
    )

    # Year-based fetch: for papers with many citations, fetch by year
    # so current-run completed years are tracked and resume is efficient
    if fetch_policy['strategy'] == 'year':
        # Sync ctx state to fetcher before calling _fetch_by_year so that
        # tests patching fetcher._fetch_by_year can read the correct state.
        fetcher._cached_year_counts = ctx.cached_year_counts
        fetcher._dedup_count = ctx.dedup_count
        fetcher._completed_year_segments = ctx.completed_year_segments
        fetcher._partial_year_start = ctx.partial_year_start
        fetcher._probed_year_counts = ctx.probed_year_counts
        fetcher._probed_year_count_complete = ctx.probed_year_count_complete
        fetcher._year_fetch_diagnostics = ctx.year_fetch_diagnostics
        return fetcher._fetch_by_year(
            citedby_url, old_citations, fresh_citations, save_progress,
            current_scholar_total(), pub_year, prev_scholar_count,
            allow_incremental_early_stop=allow_incremental_early_stop,
            force_year_rebuild=force_year_rebuild,
            selective_refresh_years=selective_refresh_years,
            year_fetch_diagnostics=ctx.year_fetch_diagnostics,
        )

    # Simple fetch for small citation counts
    direct_fetch_pub = {
        'citedby_url': citedby_url,
        'container_type': 'Publication',
        'num_citations': current_scholar_total(),
        'filled': True,
        'source': 'PUBLICATION_SEARCH_SNIPPET',
        'bib': {
            'title': title,
            'pub_year': pub_year,
        },
    }
    paper_new_citations_count = 0

    print("        Direct fetch mode: no year probe, summary shown after fetch", flush=True)
    print(f"        Direct fetch target: scholar_total={current_scholar_total()}, prev_scholar={prev_scholar_count}, "
          f"cached_total={len(old_citations)}{_direct_resume_log_suffix(normalized_direct_resume_state)}", flush=True)
    fetcher._current_attempt_url = _scholar_request_url(
        _direct_request_url(citedby_url, normalized_direct_resume_state)
    )

    old_cache_identity_keys = set()
    for citation in old_citations:
        old_cache_identity_keys.update(fetcher._citation_identity_keys(citation))
    fresh_seen = {}

    try:
        direct_fetch_termination_reason = 'iterator_exhausted'
        # Call via fetcher so tests patching fetcher._iter_direct_citedby can intercept.
        direct_iterator = fetcher._iter_direct_citedby(
            citedby_url,
            normalized_direct_resume_state,
            num_citations=current_scholar_total(),
        )
        page_items_seen = 0
        for citing in direct_iterator:
            direct_next_index += 1
            page_items_seen += 1
            info = fetcher._extract_citation_info(citing)
            identity_keys = fetcher._citation_identity_keys(info)
            matched_key = next((key for key in identity_keys if key in fresh_seen), None)
            if matched_key is not None:
                ctx.dedup_count += 1
                print(f"          [dedup] Skipping duplicate: {info['title'][:50]}... ({info.get('venue', 'N/A')}, {info.get('year', '?')})"
                      f"\n                    Existing: {fresh_seen[matched_key]}", flush=True)
            else:
                label = f"{info['title'][:50]} ({info.get('venue', 'N/A')}, {info.get('year', '?')})"
                for key in identity_keys:
                    fresh_seen[key] = label
                fresh_citations.append(info)
                is_new_citation = not any(key in old_cache_identity_keys for key in identity_keys)
                if is_new_citation:
                    fetcher._new_citations_count += 1
                    paper_new_citations_count += 1
                direct_fetch_pub['num_citations'] = current_scholar_total()
                yielded_total = len(fresh_citations)
                count = yielded_total

                print(f"          [{count}] {info['title'][:55]}...", flush=True)

            if not getattr(direct_iterator, '_finished_current_page', False):
                continue

            yielded_total = len(fresh_citations)
            save_progress(complete=False)
            # Only log "Progress saved" for full pages; short pages save silently to
            # avoid noisy consecutive saves when Scholar returns sub-10-item pages.
            items_on_page = getattr(direct_iterator, '_items_in_current_page', 0)
            if items_on_page >= SCHOLAR_PAGE_SIZE:
                print(f"        Progress saved: {yielded_total} fetched this paper, "
                      f"{fetcher._new_citations_count} new across run", flush=True)
            # Reset the per-page flag on both the wrapper and the underlying
            # iterator.  The underlying _SearchScholarIterator's flag is copied
            # into the wrapper on every __next__; if we don't clear it at the
            # source, the wrapper will re-inherit the stale True value.
            if hasattr(direct_iterator, '_finished_current_page'):
                direct_iterator._finished_current_page = False
            _base = getattr(direct_iterator, '_base_iterator', None)
            if _base is not None and hasattr(_base, '_finished_current_page'):
                _base._finished_current_page = False
            page_items_seen = 0
        else:
            if page_items_seen > 0:
                yielded_total = len(fresh_citations)
    except KeyboardInterrupt:
        save_progress(complete=False)
        raise

    direct_fetch_diagnostics = _build_direct_fetch_diagnostics(
        scholar_total=current_scholar_total(),
        cached_total=len(fresh_citations),
        seen_total=len(fresh_citations) + ctx.dedup_count,
        dedup_count=ctx.dedup_count,
        termination_reason=direct_fetch_termination_reason,
    )
    direct_materialized_cache = materialized_citations(complete=False)
    direct_materialized_total = len(direct_materialized_cache)
    direct_materialized_seen_total = direct_materialized_total + ctx.dedup_count
    cached_year_map = fetcher._year_count_map(direct_materialized_cache)
    cached_year_sum = sum(cached_year_map.values())
    print("        Probe summary: none", flush=True)
    print(f"        Probe totals: scholar_total={current_scholar_total()}, year_sum=0, missing_from_histogram=?", flush=True)
    print(f"        Cache summary: {fetcher._format_year_count_summary(cached_year_map)}", flush=True)
    print(f"        Cache totals: cached_total={direct_materialized_total}, cached_year_sum={cached_year_sum}, cached_unyeared={direct_materialized_total - cached_year_sum}, dedup_num={ctx.dedup_count}", flush=True)
    s = direct_fetch_diagnostics.get('summary', direct_fetch_diagnostics)
    new_count = len(fresh_citations)
    total_count = direct_materialized_total
    print(f"        Direct fetch totals: scholar_total={s['scholar_total']}, new={new_count}, total_cached={total_count}, seen_total={s['seen_total']}", flush=True)
    if _direct_fetch_is_underfetched(direct_fetch_diagnostics):
        print(f"        {_direct_fetch_log_message(direct_fetch_diagnostics)}", flush=True)
    save_progress(complete=True)
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


def fetch_by_year(fetcher, ctx, citedby_url, old_citations, fresh_citations, save_progress,
                    num_citations, pub_year, prev_scholar_count=0,
                    allow_incremental_early_stop=True,
                    force_year_rebuild=False,
                    selective_refresh_years=None,
                    year_fetch_diagnostics=None):
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
    probed_year_counts = fetcher._normalize_year_count_map(ctx.probed_year_counts)
    cached_year_counts = fetcher._normalize_year_count_map(ctx.cached_year_counts)
    if not cached_year_counts:
        cached_year_counts = fetcher._year_count_map(old_citations)
    year_fetch_diagnostics = fetcher._normalize_year_fetch_diagnostics(
        year_fetch_diagnostics if year_fetch_diagnostics is not None else ctx.year_fetch_diagnostics
    )
    ctx.year_fetch_diagnostics = dict(year_fetch_diagnostics)

    import sys as _sys
    _dt = getattr(_sys.modules.get(type(fetcher).__module__, None), 'datetime', None) or datetime
    current_year = _dt.now().year
    selective_refresh_years = None if selective_refresh_years is None else set(selective_refresh_years)
    explicit_refresh_years = set(selective_refresh_years or ())
    explicit_refresh_years.update(int(year) for year in ctx.partial_year_start.keys())
    if ctx.completed_year_segments and explicit_refresh_years:
        start_year = min(min(ctx.completed_year_segments), min(explicit_refresh_years))
        if year_count_map:
            start_year = min(start_year, min(year_count_map.keys()))
    else:
        start_year = fetcher._probe_citation_start_year(
            citedby_url,
            fetch_ctx=ctx,
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
                print(f"      Using cache min year {cache_min} (probe returned {start_year})", flush=True)
                start_year = cache_min

    total_years = current_year - start_year + 1
    skipped_years = 0

    print(f"  Year-based plan: {start_year}-{current_year} "
          f"(current-run completed={len(ctx.completed_year_segments)})", flush=True)

    year_range = range(start_year, current_year + 1)

    paper_new_count = 0

    probed_year_counts = fetcher._normalize_year_count_map(ctx.probed_year_counts)
    is_year = True  # fetch_by_year is only entered in year mode
    count_summary = fetcher._build_citation_count_summary(
        old_citations,
        scholar_total=num_citations,
        probed_year_counts=probed_year_counts,
        probe_complete=is_year,
        dedup_count=ctx.dedup_count,
        year_fetch_diagnostics=year_fetch_diagnostics,
    )
    cached_total_citations = count_summary['cached_total']
    cached_year_total = count_summary['cached_year_total']
    cached_seen_total = count_summary['seen_total']
    cached_unyeared_citations = count_summary['cached_unyeared_count']
    probed_hist_total = count_summary['histogram_total']
    probed_missing_from_histogram = count_summary['scholar_unyeared_count']
    histogram_authoritative = probed_hist_total > 0
    print(f"    Probe summary: {fetcher._format_year_count_summary(probed_year_counts)}", flush=True)
    if num_citations is None:
        print(f"    Probe totals: scholar_total=?, year_sum={probed_hist_total}, missing_from_histogram=?", flush=True)
    else:
        print(f"    Probe totals: scholar_total={num_citations}, year_sum={probed_hist_total}, missing_from_histogram={probed_missing_from_histogram}", flush=True)
    print(f"    Cache summary: {fetcher._format_year_count_summary(cached_year_counts)}", flush=True)
    print(f"    Cache totals: cached_total={cached_total_citations}, seen_total={cached_seen_total}, "
          f"cached_year_sum={cached_year_total}, cached_unyeared={cached_unyeared_citations}, dedup_num={ctx.dedup_count}", flush=True)
    effective_target = probed_hist_total if histogram_authoritative else num_citations
    _fetch_mode_label = 'full-rebuild' if force_year_rebuild else 'selective'
    probe_note = "" if is_year else " (no histogram probe)"
    print(f"    Fetch context: strategy={_fetch_mode_label}, "
          f"prev_scholar={prev_scholar_count}, target={effective_target}, total_years={total_years}{probe_note}", flush=True)
    print(f"    Current-run completed years: {fetcher._format_year_set_summary(ctx.completed_year_segments)}", flush=True)
    print(f"    Partial resume points: {fetcher._format_partial_year_start_summary(ctx.partial_year_start)}", flush=True)
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
        ctx.year_fetch_diagnostics = dict(year_fetch_diagnostics)

    stop_partial_resume_once_satisfied = (
        is_year
        and bool(ctx.partial_year_start)
        and cached_year_counts == probed_year_counts
    )

    try:
        for year in year_range:
            # Rule: skip only if already completed in this run, OR
            # the previous record's seen >= current probe's histogram_count.
            if year in ctx.completed_year_segments:
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
                    ctx.year_fetch_diagnostics = dict(year_fetch_diagnostics)
                print(f"      Year {year}: skip (already completed earlier in this run)", flush=True)
                continue

            live_count = (probed_year_counts or {}).get(year)
            if live_count is not None:
                existing_diag = year_fetch_diagnostics.get(year)
                prev_seen = (existing_diag or {}).get('seen_total') if existing_diag else None
                if prev_seen is None:
                    prev_seen = cached_year_counts.get(year, 0)
                if prev_seen >= live_count:
                    skipped_years += 1
                    year_fetch_diagnostics[year] = fetcher._build_year_fetch_diagnostics(
                        year,
                        live_count,
                        cached_year_counts.get(year, 0),
                        (existing_diag or {}).get('dedup_count', 0),
                        'seen_total_match_skip',
                    )
                    ctx.year_fetch_diagnostics = dict(year_fetch_diagnostics)
                    print(f"      Year {year}: skip (seen={prev_seen} >= probe={live_count})", flush=True)
                    save_progress(complete=False)
                    continue

            ctx.current_paper_page_count = 0  # reset per year in year-based mode
            start_index = ctx.partial_year_start.get(year, 0)
            resume_page_start = _page_aligned_start(start_index)
            initial_in_page_skip = start_index - resume_page_start
            resuming_partial_year = year in ctx.partial_year_start
            cached_count = cached_year_counts.get(year)
            live_count = probed_year_counts.get(year)

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
                print(f"      Year {year}: resuming from {resume_note} "
                      f"(cached={cached_count if cached_count is not None else '?'}, "
                      f"probe={live_count if live_count is not None else '?'})", flush=True)
            else:
                print(f"      Year {year}: fetching "
                      f"(cached={cached_count if cached_count is not None else '?'}, "
                      f"probe={live_count if live_count is not None else '?'})", flush=True)

            year_url = (f'/scholar?as_ylo={year}&as_yhi={year}&hl=en'
                        f'&as_sdt=2005&sciodt=0,5&cites={pub_id}&scipsc=')
            if resume_page_start > 0:
                year_url += f'&start={resume_page_start}'
            print(f"        URL: https://scholar.google.com{year_url}", flush=True)
            nav = scholarly._Scholarly__nav

            year_new_count = 0
            year_items_seen = 0
            year_dedup_count = 0
            year_termination_reason = 'iterator_exhausted'
            stop_after_current_page = False
            year_progress_saved = False
            existing_year_fresh = list(old_year_buckets.get(year, [])) if start_index > 0 else []
            year_seen_keys = {}
            for c in existing_year_fresh:
                label = f"{c.get('title', '')[:50]} ({c.get('venue', 'N/A')}, {c.get('year', '?')}) [cached]"
                for key in fetcher._citation_identity_keys(c):
                    year_seen_keys[key] = label
            year_fetched_citations = list(existing_year_fresh)

            while True:
                processed_in_this_run = 0
                resume_page_start = _page_aligned_start(start_index)
                initial_in_page_skip = start_index - resume_page_start
                year_url_cur = (f'/scholar?as_ylo={year}&as_yhi={year}&hl=en'
                                f'&as_sdt=2005&sciodt=0,5&cites={pub_id}&scipsc=')
                if resume_page_start > 0:
                    year_url_cur += f'&start={resume_page_start}'
                try:
                    import sys as _sys
                    _SSI = getattr(_sys.modules.get(type(fetcher).__module__, None),
                                   '_SearchScholarIterator', None) or _pub_parser._SearchScholarIterator
                    iterator = _SSI(nav, year_url_cur)
                    wrapped = _wrap_direct_citedby_iterator(iterator, initial_in_page_skip)
                    for citing in wrapped:
                        processed_in_this_run += 1
                        ctx.partial_year_start[year] = _page_aligned_start(
                            start_index + processed_in_this_run)
                        info = fetcher._extract_citation_info(citing, fallback_year=year)
                        identity_keys = fetcher._citation_identity_keys(info)
                        matched_key = next((key for key in identity_keys if key in year_seen_keys), None)
                        if matched_key is not None:
                            ctx.dedup_count += 1
                            year_dedup_count += 1
                            print(f"          [dedup] Skipping duplicate: {info['title'][:50]}... ({info.get('venue', 'N/A')}, {info.get('year', '?')})"
                                  f"\n                    Existing: {year_seen_keys[matched_key]}", flush=True)
                        else:
                            label = f"{info['title'][:50]} ({info.get('venue', 'N/A')}, {info.get('year', '?')})"
                            for key in identity_keys:
                                year_seen_keys[key] = label
                            year_fetched_citations.append(info)
                            fresh_year_buckets[year] = list(year_fetched_citations)
                            fresh_citations[:] = current_citations(complete=True)
                            if not any(key in old_cache_identity_keys for key in identity_keys):
                                year_new_count += 1
                                paper_new_count += 1
                                fetcher._new_citations_count += 1
                            count = len(fresh_citations)

                            print(f"          [{count}] {info['title'][:55]}...", flush=True)

                        if getattr(wrapped, '_finished_current_page', False):
                            save_progress(complete=False)
                            year_progress_saved = True
                            items_on_page = getattr(wrapped, '_items_in_current_page', 0)
                            if items_on_page >= SCHOLAR_PAGE_SIZE:
                                _saved_count = len(year_fetched_citations)
                                print(f"        Progress saved: {_saved_count} fetched for year {year}, "
                                      f"{fetcher._new_citations_count} new across run", flush=True)
                            if hasattr(wrapped, '_finished_current_page'):
                                wrapped._finished_current_page = False
                            _base = getattr(wrapped, '_base_iterator', None)
                            if _base is not None and hasattr(_base, '_finished_current_page'):
                                _base._finished_current_page = False
                    else:
                        final_items = getattr(wrapped, '_items_in_current_page', 0)
                        if 0 < final_items < SCHOLAR_PAGE_SIZE:
                            year_termination_reason = 'short_page_stop'
                    break
                except KeyboardInterrupt:
                    save_progress(complete=False)
                    raise
                except Exception as e:
                    now_s = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    print(f"        [{now_s}] Blocked at year {year} "
                          f"position {start_index + processed_in_this_run}: {e}", flush=True)
                    save_progress(complete=False)
                    if fetcher.interactive_captcha:
                        # year_url_cur was set for the initial page of this
                        # iterator.  The actual blocked page is at the
                        # position where the iterator was trying to advance
                        # to, not the one we originally constructed.
                        blocked_pos = start_index + processed_in_this_run
                        blocked_page = _page_aligned_start(blocked_pos)
                        cur_url = (f'https://scholar.google.com/scholar?'
                                   f'as_ylo={year}&as_yhi={year}&hl=en'
                                   f'&as_sdt=2005&sciodt=0,5'
                                   f'&cites={pub_id}&scipsc=')
                        if blocked_page > 0:
                            cur_url += f'&start={blocked_page}'
                        solved = fetcher._try_interactive_captcha(cur_url)
                        if solved:
                            start_index = ctx.partial_year_start.get(year, start_index)
                            continue
                    raise

            fresh_year_buckets[year] = list(year_fetched_citations)
            fresh_citations[:] = current_citations(complete=True)
            ctx.partial_year_start.pop(year, None)
            ctx.completed_year_segments.add(year)
            live_count_for_diag = live_count if live_count is not None else len(year_fetched_citations)
            year_fetch_diagnostics[year] = fetcher._build_year_fetch_diagnostics(
                year,
                live_count_for_diag,
                len(year_fetched_citations),
                year_dedup_count,
                year_termination_reason,
            )
            ctx.year_fetch_diagnostics = dict(year_fetch_diagnostics)
            if year_new_count > 0:
                print(f"      Year {year} done: {year_new_count} new citations", flush=True)
            else:
                print(f"      Year {year} done: no new citations", flush=True)
            diag = year_fetch_diagnostics[year]
            underfetched = diag['seen_total'] < diag.get('histogram_count', diag.get('scholar_total', 0))
            print(
                f"      Year {year} compare: histogram={diag.get('histogram_count', diag.get('scholar_total'))}, "
                f"seen={diag['seen_total']}, cached={diag['cached_total']}, "
                f"dedup={diag['dedup_count']}, underfetched={underfetched}, "
                f"termination={diag['termination_reason']}",
                flush=True,
            )
            print(f"      Year {year} status: year_total={len(year_fetched_citations)}, year_new={year_new_count}, "
                  f"pages={ctx.current_paper_page_count}, skipped_years={skipped_years}", flush=True)
            if not year_progress_saved:
                save_progress(complete=False)

            if stop_partial_resume_once_satisfied and resuming_partial_year and live_count is not None and len(year_fetched_citations) >= live_count:
                year_fetch_diagnostics[year] = fetcher._build_year_fetch_diagnostics(
                    year,
                    live_count,
                    len(year_fetched_citations),
                    year_dedup_count,
                    'partial_resume_completed',
                )
                ctx.year_fetch_diagnostics = dict(year_fetch_diagnostics)
                print(f"  Year {year}: Completed resumed year segment, skipping remaining years after current year", flush=True)
                break

    except KeyboardInterrupt:
        save_progress(complete=False)
        raise
    except Exception:
        save_progress(complete=False)
        raise

    fresh_citations[:] = current_citations(complete=True)
    year_log = fetcher._year_fetch_log_message(year_fetch_diagnostics)
    year_log_indented = year_log.replace("\n", "\n        ")
    print(f"    {year_log_indented}", flush=True)
    save_progress(complete=True)
    return list(fresh_citations)

