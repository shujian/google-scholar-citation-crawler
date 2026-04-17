#!/usr/bin/env python3
"""
Google Scholar Citation Crawler
- Fetches author profile (basic info, citation stats, publication list)
- Fetches per-paper citation lists with incremental caching and resume support
- Outputs JSON + Excel files

Usage:
  python scholar_citation.py --author YOUR_AUTHOR_ID
  python scholar_citation.py --author "https://scholar.google.com/citations?user=YOUR_AUTHOR_ID&hl=en"
  python scholar_citation.py --author YOUR_AUTHOR_ID --limit 1 --skip 1
"""

# Suppress Selenium telemetry before any other imports
import os
os.environ['SE_AVOID_STATS'] = 'true'
os.environ['WDM_LOG_LEVEL'] = '0'

import re
import json
import time
import sys
import argparse
import hashlib
import random
import traceback
from datetime import datetime
from scholarly import scholarly, ProxyGenerator
from scholarly._proxy_generator import MaxTriesExceededException
from scholarly.publication_parser import _SearchScholarIterator
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

from crawler.common import (
    DELAY_MAX,
    DELAY_MIN,
    MANDATORY_BREAK_EVERY_MAX,
    MANDATORY_BREAK_EVERY_MIN,
    MANDATORY_BREAK_MAX,
    MANDATORY_BREAK_MIN,
    MAX_RETRIES,
    SCHOLAR_PAGE_SIZE,
    SESSION_REFRESH_MAX,
    SESSION_REFRESH_MIN,
    TeeStream,
    YEAR_BASED_THRESHOLD,
    _scholar_request_url,
    extract_author_id,
    now_str,
    rand_delay,
    setup_proxy,
)
from crawler.profile_io import (
    build_profile_count_summary,
    build_profile_payload,
    save_profile_json as write_profile_json,
    save_profile_xlsx as write_profile_xlsx,
)
from crawler.citation_cache import (
    year_count_map as _cc_year_count_map,
    normalize_year_count_map as _cc_normalize_year_count_map,
    dump_year_count_map as _cc_dump_year_count_map,
    build_year_fetch_diagnostics as _cc_build_year_fetch_diagnostics,
    normalize_year_fetch_diagnostics as _cc_normalize_year_fetch_diagnostics,
    dump_year_fetch_diagnostics as _cc_dump_year_fetch_diagnostics,
    year_fetch_diagnostic_matches_total as _cc_year_fetch_diagnostic_matches_total,
    probed_year_counts_satisfied as _cc_probed_year_counts_satisfied,
    rehydrate_probe_metadata as _cc_rehydrate_probe_metadata,
    rehydrate_year_fetch_diagnostics as _cc_rehydrate_year_fetch_diagnostics,
)
from crawler.citation_strategy import (
    normalize_pub_year as _cs_normalize_pub_year,
    resolve_citation_fetch_policy as _cs_resolve_citation_fetch_policy,
    selective_refresh_candidate_years as _cs_selective_refresh_candidate_years,
    build_citation_count_summary as _cs_build_citation_count_summary,
    format_year_fetch_diagnostics_summary as _cs_format_year_fetch_diagnostics_summary,
)
from crawler.citation_identity import (
    normalize_cites_id as _ci_normalize_cites_id,
    normalize_identity_part as _ci_normalize_identity_part,
    citation_identity_keys as _ci_citation_identity_keys,
    citation_identity_key as _ci_citation_identity_key,
    extract_citation_info as _ci_extract_citation_info,
)
from crawler.citation_io import (
    citation_cache_path as _cio_citation_cache_path,
    load_citation_cache as _cio_load_citation_cache,
    derive_citation_cache_state as _cio_derive_citation_cache_state,
    resolve_citation_status_from_state as _cio_resolve_citation_status_from_state,
    save_citations_xlsx as _cio_save_citations_xlsx,
)


from crawler.author_fetcher import AuthorProfileFetcher  # noqa: F401
from crawler.interactive import (
    inject_cookies_from_curl as _int_inject_cookies,
    try_interactive_captcha as _int_try_interactive_captcha,
    wait_proxy_switch as _int_wait_proxy_switch,
)
from crawler.scholarly_session import (
    SessionContext,
    patch_scholarly as _ss_patch_scholarly,
    refresh_scholarly_session as _ss_refresh_scholarly_session,
    probe_citation_start_year as _ss_probe_citation_start_year,
)
import crawler.citation_fetch as _cf


# ============================================================
# Paper Citation Fetcher
# ============================================================

class PaperCitationFetcher:
    def __init__(self, author_id, output_dir=".",
                 limit=None, skip=0, save_every=10,
                 fetch_mode='normal',
                 interactive_captcha=False,
                 delay_scale=1.0):
        self.author_id = author_id
        self.output_dir = output_dir
        self.limit = limit
        self.skip = skip
        self.save_every = save_every
        self.fetch_mode = fetch_mode
        self.interactive_captcha = interactive_captcha
        self._captcha_solved_count = 0
        self._delay_scale = delay_scale
        self._injected_cookies = {}
        self._injected_header_overrides = {}
        self._session_patched = False
        self._run_start_time = time.time()
        self._new_citations_count = 0
        self._papers_fetched_count = 0

        # Paths
        self.cache_dir = os.path.join(output_dir, "scholar_cache", f"author_{author_id}", "citations")
        self.pubs_cache = os.path.join(output_dir, "scholar_cache", f"author_{author_id}", "publications.json")
        self.profile_json = os.path.join(output_dir, f"author_{author_id}_profile.json")
        self.out_json = os.path.join(output_dir, f"author_{author_id}_paper_citations.json")
        self.out_xlsx = os.path.join(output_dir, f"author_{author_id}_paper_citations.xlsx")
        os.makedirs(self.cache_dir, exist_ok=True)

        # Session context shared with the scholarly patch layer
        self._session_ctx = SessionContext(
            author_id=author_id,
            delay_scale=delay_scale,
            interactive_captcha=interactive_captcha,
            injected_cookies=self._injected_cookies,
            injected_header_overrides=self._injected_header_overrides,
        )

    def _patch_scholarly(self):
        """Install scholarly patches via SessionContext."""
        ctx = self._session_ctx
        ctx.refresh_session_fn = self._refresh_scholarly_session
        ctx.try_interactive_captcha_fn = self._try_interactive_captcha
        ctx.wait_proxy_switch_fn = self._wait_proxy_switch
        ctx.wait_status_fn = self._wait_status
        ctx.format_year_count_summary_fn = self._format_year_count_summary
        ctx.format_year_set_summary_fn = self._format_year_set_summary
        _ss_patch_scholarly(ctx)
        # Sync back attributes that pre-existing code accesses directly on self
        self._curl_header_allowlist = ctx.curl_header_allowlist
        self._last_scholar_url = ctx.last_scholar_url
        self._total_page_count = ctx.total_page_count
        self._next_break_at = ctx.next_break_at
        self._next_refresh_at = ctx.next_refresh_at
        self._current_year_segment = ctx.current_year_segment
        self._completed_year_segments = ctx.completed_year_segments
        self._session_patched = True
        self._run_start_time = time.time()


    @staticmethod
    def _year_count_map(citations):
        return _cc_year_count_map(citations)

    @staticmethod
    def _normalize_year_count_map(year_counts):
        return _cc_normalize_year_count_map(year_counts)

    @staticmethod
    def _dump_year_count_map(year_counts):
        return _cc_dump_year_count_map(year_counts)

    @staticmethod
    def _build_year_fetch_diagnostics(year, scholar_total, cached_total, dedup_count, termination_reason):
        return _cc_build_year_fetch_diagnostics(year, scholar_total, cached_total, dedup_count, termination_reason)

    @staticmethod
    def _normalize_year_fetch_diagnostics(year_fetch_diagnostics):
        return _cc_normalize_year_fetch_diagnostics(year_fetch_diagnostics)

    @staticmethod
    def _dump_year_fetch_diagnostics(year_fetch_diagnostics):
        return _cc_dump_year_fetch_diagnostics(year_fetch_diagnostics)

    @staticmethod
    def _year_fetch_diagnostic_matches_total(diagnostic, scholar_total, cached_total=None):
        return _cc_year_fetch_diagnostic_matches_total(diagnostic, scholar_total, cached_total)

    @staticmethod
    def _probed_year_counts_satisfied(cached_year_counts, probed_year_counts, year_fetch_diagnostics=None):
        return _cc_probed_year_counts_satisfied(cached_year_counts, probed_year_counts, year_fetch_diagnostics)

    @staticmethod
    def _normalize_direct_resume_state(state):
        return _cf._normalize_direct_resume_state(state)
    @staticmethod
    def _direct_resume_log_suffix(state):
        return _cf._direct_resume_log_suffix(state)
    @staticmethod
    def _build_direct_resume_state(next_index, scholar_total, citedby_url):
        return _cf._build_direct_resume_state(next_index, scholar_total, citedby_url)
    @staticmethod
    def _page_aligned_start(index):
        return _cf._page_aligned_start(index)
    @staticmethod
    def _direct_start_position(direct_resume_state):
        return _cf._direct_start_position(direct_resume_state)
    @staticmethod
    def _append_start_param(citedby_url, start):
        return _cf._append_start_param(citedby_url, start)
    @staticmethod
    def _direct_request_url(citedby_url, direct_resume_state=None):
        return _cf._direct_request_url(citedby_url, direct_resume_state)
    @staticmethod
    def _wrap_direct_citedby_iterator(iterator, in_page_skip=0):
        return _cf._wrap_direct_citedby_iterator(iterator, in_page_skip)
    def _iter_direct_citedby(self, citedby_url, direct_resume_state=None, num_citations=0):
        return _cf._iter_direct_citedby(citedby_url, direct_resume_state, num_citations,
                                        fetcher=self)
    @staticmethod
    def _build_direct_fetch_diagnostics(reported_total, yielded_total, dedup_count, termination_reason):
        return _cf._build_direct_fetch_diagnostics(reported_total, yielded_total, dedup_count, termination_reason)
    @staticmethod
    def _direct_fetch_diagnostics(reported_total, yielded_total, dedup_count, termination_reason):
        return _cf._direct_fetch_diagnostics(reported_total, yielded_total, dedup_count, termination_reason)
    @staticmethod
    def _direct_fetch_summary_message(diagnostics):
        return _cf._direct_fetch_summary_message(diagnostics)
    @staticmethod
    def _direct_fetch_log_message(diagnostics):
        return _cf._direct_fetch_log_message(diagnostics)
    @staticmethod
    def _effective_scholar_total(pub, cached=None):
        return int(pub.get('num_citations', 0) or 0)

    @staticmethod
    def _resort_publications(publications):
        publications.sort(key=lambda item: item.get('num_citations', 0), reverse=True)
        for index, publication in enumerate(publications, 1):
            publication['no'] = index

    @staticmethod
    def _citation_year_value(citation):
        year = citation.get('year', 'N/A') if citation else 'N/A'
        if year in (None, '', 'N/A', 'NA'):
            return None
        try:
            return int(year)
        except (TypeError, ValueError):
            return None

    def _replace_citation_year_bucket(self, citations, year, refreshed_year_citations):
        kept = [c for c in citations if self._citation_year_value(c) != year]
        return kept + list(refreshed_year_citations)

    def _overlay_citations_by_identity(self, base_citations, refreshed_citations):
        refreshed_map = {}
        refreshed_primary_keys = []
        for citation in refreshed_citations:
            keys = self._citation_identity_keys(citation)
            primary_key = keys[0]
            refreshed_primary_keys.append(primary_key)
            for key in keys:
                refreshed_map[key] = citation
        merged = []
        used_primary_keys = set()
        for citation in base_citations:
            replacement = None
            replacement_key = None
            for key in self._citation_identity_keys(citation):
                if key in refreshed_map:
                    replacement = refreshed_map[key]
                    replacement_key = self._citation_identity_key(replacement)
                    break
            if replacement is not None:
                merged.append(replacement)
                used_primary_keys.add(replacement_key)
            else:
                merged.append(citation)
        for citation, primary_key in zip(refreshed_citations, refreshed_primary_keys):
            if primary_key not in used_primary_keys:
                merged.append(citation)
                used_primary_keys.add(primary_key)
        return merged

    def _materialize_citation_cache(self, old_citations, fresh_citations, complete):
        if complete:
            return list(fresh_citations)
        return self._overlay_citations_by_identity(old_citations, fresh_citations)

    def _materialize_year_fetch_citations(self, old_citations, refreshed_year_buckets,
                                          refreshed_unyeared=None):
        materialized = list(old_citations)
        touched_years = sorted(year for year in refreshed_year_buckets.keys() if year is not None)
        for year in touched_years:
            materialized = self._replace_citation_year_bucket(
                materialized,
                year,
                refreshed_year_buckets.get(year, []),
            )
        if refreshed_unyeared is not None:
            materialized = self._replace_citation_year_bucket(materialized, None, refreshed_unyeared)
        return materialized

    @staticmethod
    def _citation_year_buckets(citations):
        buckets = {}
        for citation in citations:
            year = PaperCitationFetcher._citation_year_value(citation)
            buckets.setdefault(year, []).append(citation)
        return buckets

    @staticmethod
    def _normalize_pub_year(pub_year, current_year):
        return _cs_normalize_pub_year(pub_year, current_year)

    @classmethod
    def _resolve_citation_fetch_policy(cls, num_citations, pub_year, current_year=None):
        return _cs_resolve_citation_fetch_policy(num_citations, pub_year, YEAR_BASED_THRESHOLD, current_year)

    @staticmethod
    def _selective_refresh_candidate_years(cached_year_counts, probed_year_counts,
                                           year_range, partial_year_start=None,
                                           probe_complete=False,
                                           year_fetch_diagnostics=None):
        return _cs_selective_refresh_candidate_years(
            cached_year_counts, probed_year_counts, year_range,
            partial_year_start=partial_year_start,
            probe_complete=probe_complete,
            year_fetch_diagnostics=year_fetch_diagnostics,
        )

    @staticmethod
    def _build_citation_count_summary(citations, scholar_total=None, probed_year_counts=None,
                                      probe_complete=False, dedup_count=0):
        return _cs_build_citation_count_summary(
            citations, scholar_total=scholar_total,
            probed_year_counts=probed_year_counts,
            probe_complete=probe_complete,
            dedup_count=dedup_count,
        )


    @staticmethod
    def _rehydrate_probe_metadata(cached, current_scholar_total):
        return _cc_rehydrate_probe_metadata(cached, current_scholar_total)

    @staticmethod
    def _rehydrate_year_fetch_diagnostics(cached):
        return _cc_rehydrate_year_fetch_diagnostics(cached)

    @staticmethod
    def _format_year_fetch_diagnostics_summary(year_fetch_diagnostics):
        return _cs_format_year_fetch_diagnostics_summary(year_fetch_diagnostics)

    @staticmethod
    def _year_fetch_log_message(year_fetch_diagnostics):
        return (
            'Year fetch comparisons: '
            f"{PaperCitationFetcher._format_year_fetch_diagnostics_summary(year_fetch_diagnostics)}"
        )

    @staticmethod
    def _filter_citations_with_year(citations):
        return [
            citation for citation in (citations or [])
            if PaperCitationFetcher._citation_year_value(citation) is not None
        ]

    def _resolve_refresh_strategy(self, pub, cached, cache_status, citedby_url=None):
        num_citations = pub['num_citations']
        fetch_policy = self._resolve_citation_fetch_policy(num_citations, pub.get('year', 'N/A'))
        if cache_status in ('missing', None):
            return {
                'mode': 'first_fetch',
                'resume_from': [],
                'completed_years_in_current_run': [],
                'partial_year_start': {},
                'saved_dedup_count': 0,
                'prev_scholar_count': 0,
                'allow_incremental_early_stop': True,
                'force_year_rebuild': False,
                'selective_refresh_years': None,
                'rehydrated_probed_year_counts': None,
                'rehydrated_probe_complete': False,
                'rehydrated_year_fetch_diagnostics': None,
                'action': 'first fetch',
                'fetch_policy': fetch_policy,
                'direct_resume_state': None,
            }

        resume_from = cached.get('citations', [])
        saved_dedup_count = 0  # always reset per run; dedup is not cumulative across runs
        direct_fetch_diagnostics = cached.get('direct_fetch_diagnostics') or {}
        old_scholar = cached.get('num_citations_on_scholar', cached.get('num_citations_cached', 0))
        try:
            old_scholar_known = int(old_scholar)
        except (TypeError, ValueError):
            old_scholar_known = None
        completed_years_in_current_run = cached.get(
            'completed_years_in_current_run',
            cached.get('completed_years', []),
        )
        partial_year_start = {}
        force_year_rebuild = False
        selective_refresh_years = None
        rehydrated_probed_year_counts = None
        rehydrated_probe_complete = False
        rehydrated_year_fetch_diagnostics = self._rehydrate_year_fetch_diagnostics(cached)
        allow_incremental_early_stop = True
        mode = 'fetch'
        direct_resume_state = None
        completed_years_in_current_run = []
        # Drop unyeared cached citations for every year-based fetch:
        # unyeared entries have no year bucket to diff against histogram data.
        drop_cached_unyeared = fetch_policy['mode'] == 'year'
        if old_scholar_known is not None and old_scholar_known != num_citations:
            unyeared_note = "; drop unyeared" if drop_cached_unyeared else ""
            action = f"fetch ({len(resume_from)} cached, citations {old_scholar} -> {num_citations}{unyeared_note})"
        else:
            unyeared_note = "; drop unyeared" if drop_cached_unyeared else ""
            action = f"fetch ({len(resume_from)} cached; recheck by year{unyeared_note})"
            rehydrated_probed_year_counts, rehydrated_probe_complete = self._rehydrate_probe_metadata(
                cached,
                num_citations,
            )

        if drop_cached_unyeared:
            resume_from = self._filter_citations_with_year(resume_from)

        if cached.get('direct_resume_state') is not None and fetch_policy['mode'] == 'direct':
            action = f"{action}; direct fetch restarts from head"

        direct_resume_state = None

        return {
            'mode': mode,
            'resume_from': resume_from,
            'completed_years_in_current_run': completed_years_in_current_run,
            'partial_year_start': partial_year_start,
            'saved_dedup_count': saved_dedup_count,
            'prev_scholar_count': old_scholar,
            'allow_incremental_early_stop': allow_incremental_early_stop,
            'force_year_rebuild': force_year_rebuild,
            'selective_refresh_years': selective_refresh_years,
            'rehydrated_probed_year_counts': rehydrated_probed_year_counts,
            'rehydrated_probe_complete': rehydrated_probe_complete,
            'rehydrated_year_fetch_diagnostics': rehydrated_year_fetch_diagnostics,
            'action': action,
            'fetch_policy': fetch_policy,
            'direct_resume_state': direct_resume_state,
        }

    @staticmethod
    def _refresh_scholarly_session():
        """Soft session reset (delegated to crawler.scholarly_session)."""
        _ss_refresh_scholarly_session()

    def _probe_citation_start_year(self, citedby_url, fetch_ctx=None, num_citations=None, pub_year=None):
        """Probe Scholar to determine the earliest citation year (delegated).

        If fetch_ctx is provided (a FetchContext), probe results are written
        directly to it so callers don't need a separate sync step.
        """
        session_ctx = self._session_ctx
        result = _ss_probe_citation_start_year(citedby_url, session_ctx,
                                               num_citations=num_citations, pub_year=pub_year)
        # Write probe results to the FetchContext when provided; also keep self._ copies
        # for paths that don't use FetchContext directly.
        probed_year_counts = self._normalize_year_count_map(session_ctx.probed_year_counts) or None
        probe_complete = session_ctx.probed_year_count_complete
        self._probed_year_counts = probed_year_counts
        self._probed_year_count_complete = probe_complete
        if fetch_ctx is not None:
            fetch_ctx.probed_year_counts = probed_year_counts
            fetch_ctx.probed_year_count_complete = probe_complete
        self._last_scholar_url = session_ctx.last_scholar_url
        self._current_attempt_url = session_ctx.current_attempt_url
        self._total_page_count = session_ctx.total_page_count
        return result

    def _elapsed_str(self):
        """Return human-readable elapsed time since run started."""
        elapsed = int(time.time() - self._run_start_time)
        h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
        if h > 0:
            return f"{h}h{m:02d}m{s:02d}s"
        elif m > 0:
            return f"{m}m{s:02d}s"
        return f"{s}s"

    def _wait_status(self):
        """Return a status string for wait messages."""
        return (f"elapsed {self._elapsed_str()}, "
                f"{self._new_citations_count} new citations, "
                f"{self._session_ctx.total_page_count} pages, "
                f"{self._captcha_solved_count} captcha solves")

    def _citation_cache_path(self, title):
        return _cio_citation_cache_path(self.cache_dir, title)

    @staticmethod
    def _normalize_cites_id(cites_id):
        return _ci_normalize_cites_id(cites_id)

    @staticmethod
    def _normalize_identity_part(value):
        return _ci_normalize_identity_part(value)

    @classmethod
    def _citation_identity_keys(cls, info):
        return _ci_citation_identity_keys(info)

    @classmethod
    def _citation_identity_key(cls, info):
        return _ci_citation_identity_key(info)

    @staticmethod
    def _extract_citation_info(pub, fallback_year=None):
        return _ci_extract_citation_info(pub, fallback_year)

    @staticmethod
    def _format_year_count_summary(year_count_map):
        year_count_map = PaperCitationFetcher._normalize_year_count_map(year_count_map)
        if not year_count_map:
            return 'none'
        items = sorted(year_count_map.items())
        total = sum(count for _, count in items)
        nonzero = [(year, count) for year, count in items if count > 0]
        parts = [f"{year}:{count}" for year, count in items]
        return (f"{len(items)} years, total={total}, years_with_citations={len(nonzero)}, "
                f"range={items[0][0]}-{items[-1][0]} [{', '.join(parts)}]")

    @staticmethod
    def _format_year_set_summary(years):
        years = sorted(int(y) for y in set(years or []))
        if not years:
            return 'none'
        if len(years) <= 8:
            return ', '.join(str(year) for year in years)
        return (f"{', '.join(str(year) for year in years[:4])}, ..., "
                f"{', '.join(str(year) for year in years[-3:])} "
                f"({len(years)} total)")

    @staticmethod
    def _format_partial_year_start_summary(partial_year_start):
        partial_year_start = partial_year_start or {}
        if not partial_year_start:
            return 'none'
        items = sorted((int(year), start) for year, start in partial_year_start.items())
        parts = [f"{year}->{start}" for year, start in items[:8]]
        if len(items) > 8:
            parts.append('...')
        return ', '.join(parts)

    def _fetch_citations_with_progress(self, citedby_url, cache_path, title,
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
        from crawler.fetch_context import FetchContext
        ctx = FetchContext(
            completed_year_segments=set(completed_years_in_current_run or []),
            partial_year_start=dict(partial_year_start or {}),
            dedup_count=int(saved_dedup_count or 0),
            probed_year_counts=self._normalize_year_count_map(rehydrated_probed_year_counts) or None,
            probed_year_count_complete=bool(rehydrated_probe_complete and rehydrated_probed_year_counts),
            year_fetch_diagnostics=self._normalize_year_fetch_diagnostics(rehydrated_year_fetch_diagnostics) or {},
            cached_year_counts=self._year_count_map(list(resume_from)),
        )
        result = _cf.fetch_citations_with_progress(self, ctx, citedby_url, cache_path, title,
                                                   num_citations, pub_url, pub_year, resume_from,
                                                   completed_years_in_current_run=completed_years_in_current_run,
                                                   prev_scholar_count=prev_scholar_count,
                                                   partial_year_start=partial_year_start,
                                                   saved_dedup_count=saved_dedup_count,
                                                   allow_incremental_early_stop=allow_incremental_early_stop,
                                                   force_year_rebuild=force_year_rebuild,
                                                   selective_refresh_years=selective_refresh_years,
                                                   rehydrated_probed_year_counts=rehydrated_probed_year_counts,
                                                   rehydrated_probe_complete=rehydrated_probe_complete,
                                                   rehydrated_year_fetch_diagnostics=rehydrated_year_fetch_diagnostics,
                                                   pub_obj=pub_obj,
                                                   fetch_policy=fetch_policy,
                                                   direct_resume_state=direct_resume_state)
        # Sync per-paper ctx state back to self for code that reads these directly
        self._dedup_count = ctx.dedup_count
        self._cached_year_counts = ctx.cached_year_counts
        self._completed_year_segments = ctx.completed_year_segments
        self._probed_year_counts = ctx.probed_year_counts
        self._probed_year_count_complete = ctx.probed_year_count_complete
        self._year_fetch_diagnostics = ctx.year_fetch_diagnostics
        return result
    def _fetch_by_year(self, citedby_url, old_citations, fresh_citations, save_progress,
                       num_citations, pub_year, prev_scholar_count=0,
                       allow_incremental_early_stop=True,
                       force_year_rebuild=False,
                       selective_refresh_years=None,
                       year_fetch_diagnostics=None):
        from crawler.fetch_context import FetchContext
        ctx = FetchContext(
            completed_year_segments=self._completed_year_segments,
            partial_year_start=self._partial_year_start,
            dedup_count=self._dedup_count,
            probed_year_counts=self._probed_year_counts,
            probed_year_count_complete=self._probed_year_count_complete,
            year_fetch_diagnostics=year_fetch_diagnostics or {},
            cached_year_counts=self._cached_year_counts,
        )
        # Wrap save_progress to sync inner ctx's year_fetch_diagnostics to the fetcher
        # attribute before each save.  build_materialized_year_fetch_diagnostics (defined
        # in the outer fetch_citations_with_progress closure) reads that attribute so that
        # per-year dedup counts from the inner ctx are not silently discarded.
        _orig_save_progress = save_progress
        def _synced_save_progress(complete):
            self._live_year_fetch_diagnostics = ctx.year_fetch_diagnostics
            self._live_dedup_count = ctx.dedup_count
            _orig_save_progress(complete)

        result = _cf.fetch_by_year(self, ctx, citedby_url, old_citations, fresh_citations, _synced_save_progress,
                                   num_citations, pub_year, prev_scholar_count,
                                   allow_incremental_early_stop=allow_incremental_early_stop,
                                   force_year_rebuild=force_year_rebuild,
                                   selective_refresh_years=selective_refresh_years,
                                   year_fetch_diagnostics=year_fetch_diagnostics)
        self._dedup_count = ctx.dedup_count
        self._cached_year_counts = ctx.cached_year_counts
        self._completed_year_segments = ctx.completed_year_segments
        self._probed_year_counts = ctx.probed_year_counts
        self._probed_year_count_complete = ctx.probed_year_count_complete
        self._year_fetch_diagnostics = ctx.year_fetch_diagnostics
        self._partial_year_start = ctx.partial_year_start
        return result
    def _save_xlsx(self, results, metadata=None):
        _cio_save_citations_xlsx(
            self.out_xlsx,
            results,
            self.author_id,
            metadata=metadata,
            openpyxl_module=openpyxl,
            font_cls=Font,
            pattern_fill_cls=PatternFill,
            alignment_cls=Alignment,
        )

    def _load_citation_cache(self, title):
        return _cio_load_citation_cache(self.cache_dir, title)

    def _derive_citation_cache_state(self, pub, cached):
        return _cio_derive_citation_cache_state(pub, cached, YEAR_BASED_THRESHOLD)

    def _citation_status(self, pub):
        """Return cache status: 'skip_zero' | 'missing' | 'complete' | 'partial'."""
        if pub['num_citations'] == 0:
            return 'skip_zero'
        cached = self._load_citation_cache(pub['title'])
        if not cached:
            return 'missing'
        state = self._derive_citation_cache_state(pub, cached)
        return _cio_resolve_citation_status_from_state(state)

    def has_pending_work(self):
        """Check if there are any papers with incomplete citation caches."""
        if not os.path.exists(self.profile_json):
            return True
        with open(self.profile_json, 'r', encoding='utf-8') as f:
            profile = json.load(f)
        publications = profile.get('publications', [])
        for pub in publications:
            st = self._citation_status(pub)
            if st in ('missing', 'partial'):
                return True
        return False

    def run(self):
        """Main workflow for citation fetching."""
        print("\n" + "=" * 70)
        print("  Google Scholar Paper Citation Fetcher (incremental + resume)")
        limit_str = f" (first {self.limit} only, test mode)" if self.limit else ""
        skip_str  = f"  skip first {self.skip}" if self.skip else ""
        print(f"  Author ID: {self.author_id}{limit_str}{skip_str}")
        print("=" * 70 + "\n")

        if not self._session_patched:
            self._patch_scholarly()
        self._new_citations_count = 0
        self._papers_fetched_count = 0

        # Load profile
        if not os.path.exists(self.profile_json):
            print(f"Error: {self.profile_json} not found. Profile must be fetched first.")
            return False
        with open(self.profile_json, 'r', encoding='utf-8') as f:
            profile = json.load(f)
        publications = profile.get('publications', [])
        self._profile_data = profile

        # Load citedby_url mapping from publications cache
        if not os.path.exists(self.pubs_cache):
            print(f"Error: {self.pubs_cache} not found. Profile must be fetched first.")
            return False
        with open(self.pubs_cache, 'r', encoding='utf-8') as f:
            pubs_data = json.load(f)
        self._pubs_data = pubs_data
        url_map = {p['title']: {
            'citedby_url': p.get('citedby_url', ''),
            'pub_url':     p.get('url', 'N/A'),
        } for p in pubs_data.get('publications', [])}

        # force mode: wipe caches before status check so every in-range paper
        # is treated as 'missing' and starts a fresh first_fetch.
        if self.fetch_mode == 'force':
            end_idx = self.skip + self.limit if self.limit else len(publications)
            for pub in publications[self.skip:end_idx]:
                cache_path = self._citation_cache_path(pub['title'])
                if os.path.exists(cache_path):
                    os.remove(cache_path)
                    print(f"  Force mode: cleared cache for '{pub['title'][:55]}'")

        def cache_status(pub):
            st = self._citation_status(pub)
            cached = self._load_citation_cache(pub['title']) if st != 'skip_zero' else None
            return st, cached

        statuses = [cache_status(p) for p in publications]
        need_fetch = [(pub, st, cached) for pub, (st, cached) in zip(publications, statuses)
                      if st in ('missing', 'partial')]

        # Randomize fetch order only when skip/limit are not specified.
        # With --skip or --limit, original order must be preserved so users
        # can reliably target specific papers by position.
        if not self.skip and not self.limit:
            random.shuffle(need_fetch)

        print(f"Total papers: {len(publications)}")
        print(f"  Zero citations (skip):     {sum(1 for s, _ in statuses if s == 'skip_zero')}")
        print(f"  Cache complete (skip):     {sum(1 for s, _ in statuses if s == 'complete')}")
        print(f"  Need fetch/resume:         {len(need_fetch)}")
        if self.skip:
            print(f"  Skipping first {self.skip} (--skip)")
        if self.limit:
            print(f"  Processing limit (--limit): {self.limit}")
        print()

        results   = []
        fetch_idx = 0

        try:
            self._run_main_loop(publications, cache_status, url_map, need_fetch, results, fetch_idx)
        except KeyboardInterrupt:
            print(f"\n  Interrupted by user. Saving results...", flush=True)

        # Always save output (normal completion, interruption, or partial results)
        self._save_output(results)
        return True

    def _run_main_loop(self, publications, cache_status, url_map, need_fetch, results, fetch_idx):
        """Inner loop extracted so KeyboardInterrupt saves output."""
        results[:] = [None] * len(publications)

        # need_fetch is either shuffled (no skip/limit) or in original order (with skip/limit).
        # Build a set of titles that need fetching for quick lookup.
        need_fetch_set = {pub['title'] for pub, _, _ in need_fetch}

        papers_processed = 0  # counts papers after --skip (for --limit)

        for idx, pub in enumerate(publications, 1):
            title         = pub['title']
            num_citations = pub['num_citations']
            st, cached    = cache_status(pub)

            # Papers before --skip position: store cached data, don't fetch, don't count
            if idx <= self.skip:
                citations = cached['citations'] if cached else []
                print(f"[{idx}/{len(publications)}] {title[:55]}... -> skip (--skip {idx}/{self.skip})")
                results[idx - 1] = {'pub': pub, 'citations': citations}
                continue

            # --limit: stop after processing N papers past the skip point
            if self.limit and papers_processed >= self.limit:
                break
            papers_processed += 1

            if st == 'skip_zero':
                print(f"[{idx}/{len(publications)}] {title[:55]}... -> skip (0 citations)")
                results[idx - 1] = {'pub': pub, 'citations': []}
                continue

            if st == 'complete':
                print(f"[{idx}/{len(publications)}] {title[:55]}... -> cached ({len(cached['citations'])} citations)")
                results[idx - 1] = {'pub': pub, 'citations': cached['citations']}
                continue

            # rough mode: skip only when Scholar count is unchanged AND the last
            # fetch ran to completion (complete_fetch_attempt=True).
            # If the previous fetch was interrupted, re-fetch regardless.
            if self.fetch_mode == 'rough' and cached:
                last_known = cached.get('num_citations_on_scholar')
                try:
                    last_known_int = int(last_known) if last_known is not None else None
                except (TypeError, ValueError):
                    last_known_int = None
                if (last_known_int is not None
                        and last_known_int == num_citations
                        and cached.get('complete_fetch_attempt')):
                    print(f"[{idx}/{len(publications)}] {title[:55]}... -> skip-rough ({num_citations} unchanged, fetch complete)")
                    results[idx - 1] = {'pub': pub, 'citations': cached.get('citations', [])}
                    continue

            fetch_idx += 1
            urls        = url_map.get(title, {})
            citedby_url = urls.get('citedby_url', '')
            pub_url     = urls.get('pub_url', 'N/A')
            cache_path  = self._citation_cache_path(title)

            # scholarly internally prepends 'https://scholar.google.com'
            if citedby_url.startswith('https://scholar.google.com'):
                citedby_url = citedby_url[len('https://scholar.google.com'):]

            if not citedby_url:
                print(f"[{idx}/{len(publications)}] {title[:55]}... -> Warning: no citedby_url, skip")
                results[idx - 1] = {'pub': pub, 'citations': cached['citations'] if cached else []}
                continue

            prev_scholar_count = 0
            attempt_state = self._resolve_refresh_strategy(pub, cached, st, citedby_url=citedby_url)
            if attempt_state['prev_scholar_count']:
                prev_scholar_count = attempt_state['prev_scholar_count']
            partial_year_start = attempt_state['partial_year_start']
            saved_dedup_count = attempt_state['saved_dedup_count']
            allow_incremental_early_stop = attempt_state['allow_incremental_early_stop']
            resume_from = attempt_state['resume_from']
            completed_years_in_current_run = attempt_state['completed_years_in_current_run']
            force_year_rebuild = attempt_state['force_year_rebuild']
            selective_refresh_years = attempt_state['selective_refresh_years']
            rehydrated_probed_year_counts = attempt_state['rehydrated_probed_year_counts']
            rehydrated_probe_complete = attempt_state['rehydrated_probe_complete']
            rehydrated_year_fetch_diagnostics = attempt_state['rehydrated_year_fetch_diagnostics']
            direct_resume_state = attempt_state.get('direct_resume_state')
            fetch_policy = attempt_state.get('fetch_policy') or self._resolve_citation_fetch_policy(
                num_citations,
                pub.get('year', 'N/A'),
            )
            action = attempt_state['action']

            print(f"[{idx}/{len(publications)}] {title[:55]}...")
            print(f"  {action}")

            citations = None
            attempt = 0
            fetch_completed = False
            post_fetch_retry_attempted = False
            self._session_ctx.current_paper_page_count = 0
            while True:
                attempt += 1
                try:
                    if not self.interactive_captcha:
                        self._refresh_scholarly_session()
                    self._next_refresh_at = self._session_ctx.total_page_count + random.randint(SESSION_REFRESH_MIN, SESSION_REFRESH_MAX)
                    if attempt > 1:
                        if fetch_completed:
                            print(f"  {now_str()} Retrying post-fetch reconciliation with in-memory citations")
                        else:
                            # Reload citations and current-run completed years from file.
                            # partial_year_start is kept from memory (in-memory only, not persisted)
                            # so same-run retries resume from the exact page where the error occurred.
                            latest_cache = self._load_citation_cache(title)
                            if latest_cache:
                                retry_attempt_state = self._resolve_refresh_strategy(
                                    pub,
                                    latest_cache,
                                    'partial',
                                    citedby_url=citedby_url,
                                )
                                latest_resume_from = retry_attempt_state['resume_from']
                                retry_mode = retry_attempt_state['mode']
                                latest_retry_scholar_total = latest_cache.get('num_citations_on_scholar')
                                try:
                                    latest_retry_scholar_total = int(latest_retry_scholar_total)
                                except (TypeError, ValueError):
                                    latest_retry_scholar_total = None
                                if latest_retry_scholar_total is not None and latest_retry_scholar_total != prev_scholar_count:
                                    retry_mode = 'update'
                                    latest_resume_from = self._filter_citations_with_year(latest_cache.get('citations', []))
                                    completed_years_in_current_run = []
                                    rehydrated_probed_year_counts = None
                                    rehydrated_probe_complete = False
                                    rehydrated_year_fetch_diagnostics = None
                                else:
                                    if rehydrated_probed_year_counts is not None or rehydrated_probe_complete:
                                        completed_years_in_current_run = latest_cache.get(
                                            'completed_years_in_current_run',
                                            retry_attempt_state['completed_years_in_current_run'],
                                        )
                                        rehydrated_probed_year_counts, rehydrated_probe_complete = self._rehydrate_probe_metadata(
                                            latest_cache,
                                            num_citations,
                                        )
                                        rehydrated_year_fetch_diagnostics = self._rehydrate_year_fetch_diagnostics(latest_cache)
                                    else:
                                        completed_years_in_current_run = retry_attempt_state['completed_years_in_current_run']
                                        rehydrated_probed_year_counts = retry_attempt_state['rehydrated_probed_year_counts']
                                        rehydrated_probe_complete = retry_attempt_state['rehydrated_probe_complete']
                                        rehydrated_year_fetch_diagnostics = retry_attempt_state['rehydrated_year_fetch_diagnostics']
                                resume_from = latest_resume_from
                                num_citations = pub.get('num_citations', num_citations)
                                fetch_policy = retry_attempt_state.get('fetch_policy') or fetch_policy
                                allow_incremental_early_stop = retry_attempt_state['allow_incremental_early_stop']
                                force_year_rebuild = retry_attempt_state['force_year_rebuild']
                                selective_refresh_years = retry_attempt_state['selective_refresh_years']
                                saved_dedup_count = retry_attempt_state['saved_dedup_count']
                                partial_year_start = retry_attempt_state['partial_year_start']
                                direct_resume_state = retry_attempt_state.get('direct_resume_state')
                                retry_suffix = self._direct_resume_log_suffix(direct_resume_state)
                                print(f"  {now_str()} Retrying with {len(resume_from)} cached citations from previous attempt{retry_suffix}")
                    if not fetch_completed:
                        citations = self._fetch_citations_with_progress(
                            citedby_url, cache_path, title, num_citations,
                            pub_url, pub.get('year', 'N/A'), resume_from,
                            completed_years_in_current_run=completed_years_in_current_run,
                            prev_scholar_count=prev_scholar_count,
                            partial_year_start=partial_year_start,
                            saved_dedup_count=saved_dedup_count,
                            allow_incremental_early_stop=allow_incremental_early_stop,
                            force_year_rebuild=force_year_rebuild,
                            selective_refresh_years=selective_refresh_years,
                            rehydrated_probed_year_counts=rehydrated_probed_year_counts,
                            rehydrated_probe_complete=rehydrated_probe_complete,
                            rehydrated_year_fetch_diagnostics=rehydrated_year_fetch_diagnostics,
                            pub_obj=pub,
                            fetch_policy=fetch_policy,
                            direct_resume_state=direct_resume_state,
                        )
                        fetch_completed = True
                    num_citations = pub['num_citations']
                    # Use per-year diagnostics for year-based fetch (authoritative, per-run).
                    # Only use them when fetch_policy is year to avoid stale state from
                    # a previous paper's year-based fetch polluting direct fetch totals.
                    year_fetch_diagnostics = (
                        getattr(self, '_year_fetch_diagnostics', None)
                        if fetch_policy.get('mode') == 'year'
                        else None
                    )
                    if year_fetch_diagnostics:
                        seen_total = sum(d.get('seen_total', 0) for d in year_fetch_diagnostics.values())
                        run_dedup = sum(d.get('dedup_count', 0) for d in year_fetch_diagnostics.values())
                    else:
                        run_dedup = self._dedup_count
                        seen_total = len(citations) + run_dedup
                    dedup_str = f", {run_dedup} dupes" if run_dedup else ""
                    print(f"  Done: {len(citations)} cached, {seen_total} seen{dedup_str} (Scholar: {num_citations})")
                    year_counts = self._year_count_map(citations)
                    if year_counts:
                        year_total = sum(year_counts.values())
                        unyeared = max(0, len(citations) - year_total)
                        year_summary = self._format_year_count_summary(year_counts)
                        unyeared_suffix = f", unyeared={unyeared}" if unyeared else ""
                        print(f"  Year summary: {year_summary}{unyeared_suffix}", flush=True)
                    latest_cache_snapshot = self._load_citation_cache(title)
                    direct_fetch_diagnostics = (latest_cache_snapshot or {}).get('direct_fetch_diagnostics') or {}
                    has_direct_fetch_summary = direct_fetch_diagnostics.get('mode') == 'direct'
                    direct_underfetched = has_direct_fetch_summary and direct_fetch_diagnostics.get('underfetched')
                    if has_direct_fetch_summary:
                        if direct_underfetched:
                            print("  Direct fetch under-fetched; recording current results", flush=True)
                        else:
                            print(f"  {self._direct_fetch_summary_message(direct_fetch_diagnostics)}", flush=True)
                    break

                except Exception as e:
                    is_post_fetch_failure = fetch_completed
                    if is_post_fetch_failure:
                        if post_fetch_retry_attempted:
                            raise RuntimeError(
                                f"Post-fetch reconciliation failed after retry: {type(e).__name__}: {e}"
                            ) from e
                        post_fetch_retry_attempted = True
                    else:
                        post_fetch_retry_attempted = False
                    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    # In interactive mode show attempt number only; in non-interactive
                    # mode show X/MAX_RETRIES so the user knows when it will give up.
                    attempt_str = (str(attempt) if self.interactive_captcha
                                   else f"{attempt}/{MAX_RETRIES}")
                    print(f"  [{now}] Error (attempt {attempt_str}, "
                          f"total pages: {self._session_ctx.total_page_count}, "
                          f"new citations: {self._new_citations_count}): {e}")
                    if is_post_fetch_failure:
                        continue
                    # Non-interactive: give up after MAX_RETRIES attempts
                    if not self.interactive_captcha and attempt >= MAX_RETRIES:
                        traceback.print_exc()
                        print(f"\n  [{now}] All retry attempts exhausted. Terminating.", flush=True)
                        self._save_output(results)
                        sys.exit(1)
                    # Offer interactive captcha solve when --interactive-captcha is set.
                    # In interactive mode we loop indefinitely — the user decides when
                    # to give up by killing the program.
                    if self.interactive_captcha:
                        solved = self._try_interactive_captcha(
                            getattr(self, '_current_attempt_url',
                                    getattr(self, '_last_scholar_url',
                                            'https://scholar.google.com/scholar')))
                        if solved:
                            print(f"  {now_str()} Retrying with injected cookies (attempt {attempt + 1})...",
                                  flush=True)
                            continue  # skip wait, go to next attempt
                    # Save partial progress before the long wait
                    if os.path.exists(cache_path):
                        with open(cache_path, 'r', encoding='utf-8') as f:
                            latest = json.load(f)
                        saved_count = len(latest.get('citations', []))
                        print(f"  [{now}] Saved progress ({saved_count} citations)")
                    self._wait_proxy_switch(max_hours=24)

            results[idx - 1] = {'pub': pub, 'citations': citations or []}

            if fetch_idx < (self.limit or len(need_fetch)):
                d = rand_delay(self._delay_scale)
                print(f"  {now_str()} Waiting {d:.0f}s before next paper... [{self._wait_status()}]", flush=True)
                time.sleep(d)

    def _inject_cookies_from_curl(self, curl_str):
        """Parse cookies and selected headers from a pasted cURL command."""
        counter = [self._captcha_solved_count]
        result = _int_inject_cookies(
            curl_str,
            curl_header_allowlist=self._curl_header_allowlist,
            last_scholar_url=self._last_scholar_url,
            injected_cookies_ref=self._injected_cookies,
            injected_header_overrides_ref=self._injected_header_overrides,
            captcha_solved_count_ref=counter,
        )
        self._captcha_solved_count = counter[0]
        return result

    def _try_interactive_captcha(self, url):
        """Prompt user to solve captcha manually and inject resulting cookies."""
        return _int_try_interactive_captcha(
            url,
            inject_fn=self._inject_cookies_from_curl,
        )

    def _wait_proxy_switch(self, max_hours=24):
        """Wait up to max_hours for the user to switch proxy/IP."""
        return _int_wait_proxy_switch(max_hours)

    def _save_output(self, results):
        """Save citation results to JSON and Excel."""
        print("\n" + "=" * 70)
        profile = getattr(self, '_profile_data', None)
        pubs_data = getattr(self, '_pubs_data', None)
        if profile is None and os.path.exists(self.profile_json):
            with open(self.profile_json, 'r', encoding='utf-8') as f:
                profile = json.load(f)
        if pubs_data is None and os.path.exists(self.pubs_cache):
            with open(self.pubs_cache, 'r', encoding='utf-8') as f:
                pubs_data = json.load(f)
        publications = profile.get('publications', []) if profile else []
        final_results = []
        for i, r in enumerate(results):
            if r is not None:
                pub = r['pub']
                cached = self._load_citation_cache(pub.get('title', '')) if pub else None
                final_results.append({
                    **r,
                    'fetch_complete': bool((cached or {}).get('complete_fetch_attempt')),
                })
            else:
                # Load from cache if available, otherwise empty
                pub = publications[i] if i < len(publications) else {}
                cached = self._load_citation_cache(pub.get('title', '')) if pub else None
                citations = cached.get('citations', []) if cached else []
                final_results.append({
                    'pub': pub,
                    'citations': citations,
                    'fetch_complete': bool((cached or {}).get('complete_fetch_attempt')),
                })
        total_cites = sum(len(r['citations']) for r in final_results)
        output_payload = {
            'author_id': self.author_id,
            'fetch_time': datetime.now().isoformat(),
            'total_papers': len(final_results),
            'total_citations_collected': total_cites,
            'papers': final_results,
        }
        with open(self.out_json, 'w', encoding='utf-8') as f:
            json.dump(output_payload, f, ensure_ascii=False, indent=2)
        print(f"Saved JSON : {self.out_json}")

        self._save_xlsx(final_results, metadata=output_payload)
        print(f"Saved Excel: {self.out_xlsx}")

        total_papers = len(results)  # includes None slots (total publications)
        fetched_str = f", {self._papers_fetched_count} fetched" if self._papers_fetched_count else ""
        new_str = f", {self._new_citations_count} new" if self._new_citations_count else ""
        print(f"\nDone! {len(final_results)}/{total_papers} papers{fetched_str}, "
              f"{total_cites} collected citation records{new_str}")
        print(f"Run summary: elapsed {self._elapsed_str()}"
              f" | {self._session_ctx.total_page_count} pages accessed"
              f" | {self._new_citations_count} new citations"
              f" | output total = collected per-paper citation records\n")

        return True


# ============================================================
# CLI Entry Point
# ============================================================

from crawler.cli import parse_args  # noqa: F401


def main():
    """
    CLI entry point.  Defined here (not in crawler.cli) so that tests can patch
    scholar_citation.parse_args / setup_proxy / AuthorProfileFetcher /
    PaperCitationFetcher and have those patches take effect.
    """
    args = parse_args()
    author_id = extract_author_id(args.author)

    os.makedirs(args.output_dir, exist_ok=True)

    original_stdout = sys.stdout
    log_file = None
    log_path = None
    try:
        logs_dir = os.path.join(args.output_dir, "logs")
        os.makedirs(logs_dir, exist_ok=True)
        log_name = f"author_{author_id}_run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        log_path = os.path.join(logs_dir, log_name)
        log_file = open(log_path, 'w', encoding='utf-8')
        sys.stdout = TeeStream(original_stdout, log_file)
        print(f"Run log: {log_path}")
    except OSError as e:
        sys.stdout = original_stdout
        print(f"Warning: Failed to open run log file: {e}")
        log_file = None
        log_path = None

    try:
        setup_proxy()
        print(f"Author ID: {author_id}")

        delay_scale = args.accelerate if args.interactive_captcha else 1.0

        # Patch scholarly before profile fetch so both phases share the same
        # HTTP/2 session — avoids the cold-session block on the first citation page.
        citation_fetcher = PaperCitationFetcher(
            author_id=author_id,
            output_dir=args.output_dir,
            limit=args.limit,
            skip=args.skip,
            fetch_mode=args.fetch_mode,
            interactive_captcha=args.interactive_captcha,
            delay_scale=delay_scale,
        )
        citation_fetcher._patch_scholarly()

        fetcher = AuthorProfileFetcher(author_id, args.output_dir, delay_scale=delay_scale)
        prev_profile = fetcher.load_prev_profile()
        success = fetcher.run(force_refresh_pubs=args.force_refresh_pubs)
        if not success:
            sys.exit(1)

        curr_profile = fetcher.load_prev_profile()
        if prev_profile and curr_profile:
            prev_citations = prev_profile.get('total_citations', prev_profile.get('author_info', {}).get('citedby', -1))
            curr_citations = curr_profile.get('total_citations', curr_profile.get('author_info', {}).get('citedby', -2))
            prev_pubs = prev_profile.get('total_publications', -1)
            curr_pubs = curr_profile.get('total_publications', -2)

            if prev_citations == curr_citations and prev_pubs == curr_pubs and args.fetch_mode != 'force':
                if not citation_fetcher.has_pending_work():
                    print("\n" + "=" * 70)
                    print(f"  No changes detected (citations: {curr_citations}, publications: {curr_pubs})")
                    print("  All citation caches are complete. Skipping citation fetch.")
                    print("=" * 70 + "\n")
                    return
                else:
                    print("\nNo changes in totals, but some citations are incomplete. Continuing fetch...")

        success = citation_fetcher.run()
        if not success:
            sys.exit(1)
    finally:
        sys.stdout = original_stdout
        if log_file is not None:
            log_file.flush()
            log_file.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Progress has been saved to cache.")
        sys.exit(0)
    except Exception as e:
        print(f"\nError: {e}")
        traceback.print_exc()
        sys.exit(1)
