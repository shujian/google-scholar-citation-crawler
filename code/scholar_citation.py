#!/usr/bin/env python3
"""
Google Scholar Citation Crawler
- Fetches author profile (basic info, citation stats, publication list)
- Fetches per-paper citation lists with incremental caching and resume support
- Outputs JSON + Excel files

Run from the project root as a module (never as a file path — the package
directory is named ``code``, which would shadow the standard-library module of
the same name):

  python -m code.scholar_citation --author YOUR_AUTHOR_ID
  python -m code.scholar_citation --author "https://scholar.google.com/citations?user=YOUR_AUTHOR_ID&hl=en"
  python -m code.scholar_citation --author YOUR_AUTHOR_ID --limit 1 --skip 1

Proxy and other machine-local settings come from command-line arguments or from
``crawler_config.json`` (see ``code.config``); this program requires no
environment variable.  The two ``os.environ`` writes below happen before any
third-party import and exist purely for those libraries (selenium telemetry);
they are centralised in ``code.lib_env`` and never read back as configuration.
"""

from code.lib_env import configure_third_party_env, export_proxy_env  # noqa: E402

# Suppress Selenium telemetry before any other imports.  Importing ``code``
# already normalised NO_PROXY, which must happen before ``scholarly`` is
# imported: scholarly constructs an httpx.Client at import time, and httpx
# 0.27.2 raises InvalidURL on a bracketed IPv6 literal such as [::1] in
# NO_PROXY.
configure_third_party_env()

import re
import json
import os
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

from code.common import (
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
    STALL_AFTER_ATTEMPTS,
    TeeStream,
    YEAR_BASED_THRESHOLD,
    _scholar_request_url,
    extract_author_id,
    now_str,
    rand_delay,
    resolve_proxy_config,
    setup_proxy,
    write_json_atomic,
)
from code.citation_cache import (
    year_count_map as _cc_year_count_map,
    normalize_year_count_map as _cc_normalize_year_count_map,
    dump_year_count_map as _cc_dump_year_count_map,
    build_year_fetch_diagnostics as _cc_build_year_fetch_diagnostics,
    normalize_year_fetch_diagnostics as _cc_normalize_year_fetch_diagnostics,
    is_unknown_year,
    year_records_gap as _cc_year_records_gap,
    year_records_shortfall_years as _cc_year_records_shortfall_years,
)
from code.citation_strategy import (
    normalize_pub_year as _cs_normalize_pub_year,
    resolve_citation_fetch_policy as _cs_resolve_citation_fetch_policy,
    selective_refresh_candidate_years as _cs_selective_refresh_candidate_years,
    build_citation_count_summary as _cs_build_citation_count_summary,
    format_year_fetch_diagnostics_summary as _cs_format_year_fetch_diagnostics_summary,
)
from code.citation_identity import (
    normalize_cites_id as _ci_normalize_cites_id,
    normalize_identity_part as _ci_normalize_identity_part,
    citation_identity_keys as _ci_citation_identity_keys,
    citation_identity_key as _ci_citation_identity_key,
    citation_output_identity_keys as _ci_citation_output_identity_keys,
    extract_citation_info as _ci_extract_citation_info,
)
from code.citation_io import (
    load_citation_cache as _cio_load_citation_cache,
    derive_citation_cache_state as _cio_derive_citation_cache_state,
    resolve_citation_status_from_state as _cio_resolve_citation_status_from_state,
    save_citations_xlsx as _cio_save_citations_xlsx,
)
from code.output_state import (
    PaperFetchState,
    PaperState,
    index_year_records as _os_index_year_records,
    load_paper_states as _os_load_paper_states,
    resolve_citation_status_from_output as _os_resolve_citation_status_from_output,
    to_paper_fetch_state,
)
from code.pub_info import PubInfo
from code.log_utils import setup_logging, get_logger
from code import cite_format as _cite_format

logger = get_logger(__name__)

from code.author_fetcher import AuthorProfileFetcher  # noqa: F401
from code.interactive import (
    inject_cookies_from_curl as _int_inject_cookies,
    load_curl_from_file as _int_load_curl_from_file,
    prompt_first_curl as _int_prompt_first_curl,
    try_browser_harvest as _int_try_browser_harvest,
    try_interactive_captcha as _int_try_interactive_captcha,
    wait_proxy_switch as _int_wait_proxy_switch,
)
from code.scholarly_session import (
    SessionContext,
    patch_scholarly as _ss_patch_scholarly,
    refresh_scholarly_session as _ss_refresh_scholarly_session,
    probe_citation_start_year as _ss_probe_citation_start_year,
)
import code.citation_fetch as _cf


# ============================================================
# Paper Citation Fetcher
# ============================================================

class PaperCitationFetcher:
    def __init__(self, author_id, output_dir=".",
                 limit=None, skip=0, save_every=10,
                 fetch_mode='normal',
                 interactive_captcha=False,
                 delay_scale=1.0,
                 cite_formats=False):
        self.author_id = author_id
        self.output_dir = output_dir
        self.limit = limit
        self.skip = skip
        self.save_every = save_every
        self.fetch_mode = fetch_mode
        self.interactive_captcha = interactive_captcha
        # Opt-in: one Cite-dialog request per citing entry (plus one for BibTeX)
        # is thousands of requests on a real profile, so a normal run must not
        # start paying for it.  See code/cite_format.py.
        self.cite_formats = bool(cite_formats)
        self._captcha_solved_count = 0
        self._delay_scale = delay_scale
        self._injected_cookies = {}
        self._injected_header_overrides = {}
        self._session_patched = False
        self._run_start_time = time.time()
        self._new_citations_count = 0
        self._run_new_citations_total = 0
        self._papers_fetched_count = 0

        # Paths
        self.profile_json = os.path.join(output_dir, f"author_{author_id}_profile.json")
        self.out_json = os.path.join(output_dir, f"author_{author_id}_paper_citations.json")
        self.out_xlsx = os.path.join(output_dir, f"author_{author_id}_paper_citations.xlsx")
        self._curl_save_path = os.path.join(output_dir, "curl.txt")

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
        sctx = self._session_ctx
        sctx.refresh_session_fn = self._refresh_scholarly_session
        sctx.try_interactive_captcha_fn = self._try_interactive_captcha
        sctx.wait_proxy_switch_fn = self._wait_proxy_switch
        sctx.wait_status_fn = self._wait_status
        sctx.format_year_count_summary_fn = self._format_year_count_summary
        sctx.format_year_set_summary_fn = self._format_year_set_summary
        _ss_patch_scholarly(sctx)
        # Sync back attributes that pre-existing code accesses directly on self
        self._curl_header_allowlist = sctx.curl_header_allowlist
        self._last_scholar_url = sctx.last_scholar_url
        self._total_page_count = sctx.total_page_count
        self._next_break_at = sctx.next_break_at
        self._next_refresh_at = sctx.next_refresh_at
        # Year-segment state belongs to citation_fetch.YearFetchSession; the
        # fetch engine overwrites this per paper.
        self._completed_year_segments = set()
        self._session_patched = True
        self._run_start_time = time.time()

        # Auto-load saved cookies from previous session
        saved_curl = _int_load_curl_from_file(self._curl_save_path)
        if saved_curl:
            print(f"  Loading saved cookies from {self._curl_save_path}", flush=True)
            self._inject_cookies_from_curl(saved_curl)
        else:
            # Prompt once on first Scholar request (only in interactive TTY sessions)
            sctx.first_page_prompt_fn = lambda: _int_prompt_first_curl(
                inject_fn=self._inject_cookies_from_curl,
                harvest_fn=self._harvest_cookies_from_browser,
            )


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
    def _build_year_fetch_diagnostics(year, histogram_count, cached_total, dedup_count, termination_reason):
        return _cc_build_year_fetch_diagnostics(year, histogram_count, cached_total, dedup_count, termination_reason)

    @staticmethod
    def _normalize_year_fetch_diagnostics(year_fetch_diagnostics):
        return _cc_normalize_year_fetch_diagnostics(year_fetch_diagnostics)

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
    def _build_direct_fetch_diagnostics(scholar_total, cached_total, seen_total, dedup_count, termination_reason):
        return _cf._build_direct_fetch_diagnostics(scholar_total, cached_total, seen_total, dedup_count, termination_reason)
    @staticmethod
    def _direct_fetch_diagnostics_message(diagnostics, prefix="Direct fetch summary "):
        return _cf._direct_fetch_diagnostics_message(diagnostics, prefix=prefix)
    @staticmethod
    def _effective_scholar_total(pub):
        return int(pub.get('num_citations', 0) or 0)

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

    def _materialize_citation_cache(self, old_citations, fresh_citations, complete,
                                    authoritative=False):
        # A completed fetch is authoritative only while it returns at least as
        # many rows as the cache.  A smaller result means the scan was truncated
        # (blocked/short page, lossy year filter), so merge by identity instead
        # of replacing: dropping rows we already collected is never correct.
        # *authoritative* says the caller already decided per bucket (year mode
        # rebuilds each year itself), so its list stands even when it is smaller.
        if complete and (authoritative or len(fresh_citations) >= len(old_citations or [])):
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
                                      probe_complete=False, dedup_count=0,
                                      year_fetch_diagnostics=None):
        return _cs_build_citation_count_summary(
            citations, scholar_total=scholar_total,
            probed_year_counts=probed_year_counts,
            probe_complete=probe_complete,
            dedup_count=dedup_count,
            year_fetch_diagnostics=year_fetch_diagnostics,
        )


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

    def _resolve_refresh_strategy(self, pub, cached, cache_status, citedby_url=None,
                                  paper_state=None):
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
                'rehydrated_year_fetch_diagnostics': None,
                'action': 'first fetch',
                'fetch_policy': fetch_policy,
                'direct_resume_state': None,
            }

        resume_from = cached.get('citations', [])
        saved_dedup_count = 0  # always reset per run; dedup is not cumulative across runs
        # Prefer PaperFetchState for stored diagnostics; fall back to cached dict.
        pst = to_paper_fetch_state(paper_state)
        if pst:
            direct_fetch_diagnostics = pst.direct_fetch_diagnostics or {}
            old_scholar = pst.num_citations_on_scholar or 0
        else:
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
        force_year_rebuild = (self.fetch_mode == 'exact')
        selective_refresh_years = None
        # Resume an interrupted year segment from where it stopped.  The offset is
        # persisted with the fetch state (see PaperFetchState.partial_year_start),
        # so an interrupted run no longer restarts the year at item 0.  Read it
        # from the PaperFetchState when one was passed, else from the cached dict
        # (the same-run retry path passes the cache snapshot).
        if pst and pst.partial_year_start:
            partial_year_start = dict(pst.partial_year_start)
        else:
            cached_pst = to_paper_fetch_state(cached)
            if cached_pst and cached_pst.partial_year_start:
                partial_year_start = dict(cached_pst.partial_year_start)
        # Build per-year diagnostics from year_records (PaperFetchState.to_dict()
        # already includes this field).  year_records is a list of per-year dicts;
        # convert to {year: diag} map for fetch_by_year skip logic.
        rehydrated_year_fetch_diagnostics = None
        # Read per-year diagnostics from PaperFetchState when available.
        if pst and pst.year_records:
            per_year = _os_index_year_records(pst.year_records)
            rehydrated_year_fetch_diagnostics = self._normalize_year_fetch_diagnostics(per_year) or None
        # Fallback: read from cached dict (same-run retry may lack PaperFetchState).
        if not rehydrated_year_fetch_diagnostics:
            year_records = cached.get('year_records')
            if isinstance(year_records, list) and year_records:
                per_year = _os_index_year_records(year_records)
                rehydrated_year_fetch_diagnostics = self._normalize_year_fetch_diagnostics(per_year) or None
        if not rehydrated_year_fetch_diagnostics:
            year_fetch_diag = cached.get('year_fetch_diagnostics')
            if isinstance(year_fetch_diag, dict):
                rehydrated_year_fetch_diagnostics = self._normalize_year_fetch_diagnostics(year_fetch_diag) or None
        # When transitioning from direct to year mode the cache has no year
        # diagnostics yet.  Synthesise per-year entries from the cached
        # citations so the year fetch path knows which years are already
        # covered and only probes / fetches what is missing.
        if fetch_policy['strategy'] == 'year' and not rehydrated_year_fetch_diagnostics and resume_from:
            year_counts = self._year_count_map(resume_from)
            if year_counts:
                synthesized = {}
                for year, count in sorted(year_counts.items()):
                    synthesized[year] = self._build_year_fetch_diagnostics(
                        year, count, count, 0, 'direct_to_year_migration',
                    )
                rehydrated_year_fetch_diagnostics = self._normalize_year_fetch_diagnostics(synthesized)
        allow_incremental_early_stop = True
        mode = 'fetch'
        direct_resume_state = None
        completed_years_in_current_run = []
        # Drop unyeared cached citations for every year-based fetch:
        # unyeared entries have no year bucket to diff against histogram data.
        drop_cached_unyeared = fetch_policy['strategy'] == 'year'
        # Derive seen total from diagnostics summary.
        num_seen = None
        if fetch_policy['strategy'] == 'year':
            if pst and pst.year_fetch_diagnostics:
                num_seen = pst.year_fetch_diagnostics.get('seen_total')
            else:
                yfd_summary = cached.get('year_fetch_diagnostics') or {}
                num_seen = yfd_summary.get('seen_total')
        else:
            if pst and pst.direct_fetch_diagnostics:
                num_seen = pst.direct_fetch_diagnostics.get('seen_total')
            else:
                dfd_summary = cached.get('direct_fetch_diagnostics') or {}
                num_seen = dfd_summary.get('seen_total')
        try:
            num_seen = int(num_seen) if num_seen is not None else None
        except (TypeError, ValueError):
            num_seen = None
        seen_str = f", seen={num_seen}" if num_seen is not None else ""

        if old_scholar_known is not None and old_scholar_known != num_citations:
            unyeared_note = "; drop unyeared" if drop_cached_unyeared else ""
            action = (f"fetch ({len(resume_from)} cached{seen_str}, "
                      f"scholar {old_scholar} -> {num_citations}{unyeared_note})")
        else:
            unyeared_note = "; drop unyeared" if drop_cached_unyeared else ""
            if fetch_policy['strategy'] == 'year':
                action = (f"fetch ({len(resume_from)} cached{seen_str}, "
                          f"scholar={num_citations} unchanged; recheck by year{unyeared_note})")
            else:
                action = (f"fetch ({len(resume_from)} cached{seen_str}, "
                          f"scholar={num_citations} unchanged; recheck (direct){unyeared_note})")

        if drop_cached_unyeared:
            resume_from = self._filter_citations_with_year(resume_from)

        if cached.get('direct_resume_state') is not None and fetch_policy['strategy'] == 'direct':
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
            'rehydrated_year_fetch_diagnostics': rehydrated_year_fetch_diagnostics,
            'action': action,
            'fetch_policy': fetch_policy,
            'direct_resume_state': direct_resume_state,
        }

    @staticmethod
    def _refresh_scholarly_session():
        """Soft session reset (delegated to code.scholarly_session)."""
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
        total = self._run_new_citations_total + max(0, self._new_citations_count)
        run_pages = self._session_ctx.total_page_count
        curl_pages = self._session_ctx.curl_page_count
        pages_str = f"{run_pages} pages ({curl_pages} this curl)"
        return (f"elapsed {self._elapsed_str()}, "
                f"{total} new citations, "
                f"{pages_str}, "
                f"{self._captcha_solved_count} captcha solves")

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
    def _citation_info_score(citation):
        """How much usable metadata a citation carries (duplicate tie-break)."""
        score = 0
        for field in ('venue', 'url', 'authors'):
            value = citation.get(field)
            if value is not None and str(value).strip().lower() not in ('', 'n/a', 'na', '?'):
                score += 1
        if not is_unknown_year(citation.get('year')):
            score += 1
        return score

    def _citation_output_identity_keys(self, citation):
        """Keys used to collapse duplicate rows in the output.

        Delegates to :func:`code.citation_identity.citation_output_identity_keys`:
        only a real ``cites_id`` (or, for rows without one, a fully equal
        ``title + venue`` / ``title + authors``) collapses rows.  Scholar
        listing one title under two different clusters means two entries, and
        both are kept (K123) — the unconditional normalised-title key that used
        to live here merged exactly those rows.
        """
        return _ci_citation_output_identity_keys(citation)

    def _dedup_citations_for_output(self, citations, title=''):
        """Keep one row per citation identity, preferring the richest metadata.

        Scholar can list the same cluster twice with contradictory metadata
        (different venue/year), which inflates the stored count and breaks the
        documented dedup contract.  The first occurrence wins ties so the order
        stays stable.

        Rows are only considered duplicates when Scholar's own identity says so
        (see :meth:`_citation_output_identity_keys`): a shared normalised title
        is **not** enough, because Scholar reports the preprint and the
        published version of one work as two clusters and the pipeline keeps
        them apart (K123).
        """
        rows = list(citations or [])
        if len(rows) < 2:
            return rows
        kept = []
        by_key = {}
        dropped = 0
        for citation in rows:
            keys = self._citation_output_identity_keys(citation)
            match = next((by_key[key] for key in keys if key in by_key), None)
            if match is None:
                by_key.update({key: len(kept) for key in keys})
                kept.append(citation)
                continue
            dropped += 1
            if self._citation_info_score(citation) > self._citation_info_score(kept[match]):
                kept[match] = citation
                by_key.update({key: match for key in keys})
        if not dropped:
            return rows
        print(f"  Deduplicated {dropped} duplicate citation row(s) for "
              f"'{title[:50]}' ({len(kept)} kept)", flush=True)
        return kept

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

    @staticmethod
    def _plan_fetch_gap(strategy, cached, scholar_total, cached_count):
        """Return (needed, seen, target, short_years) for a paper in the fetch plan.

        `needed` is the number of citations that still must be fetched before
        the paper is complete.  Uses diagnostics when available (seen_total vs
        histogram/scholar_total), falling back to cached-count vs scholar_total.
        `needed <= 0` means no new data is required.

        Year mode sums the per-year shortfalls: the aggregate seen vs histogram
        comparison hides a year whose own bucket is short whenever other years
        over-count the probe, so it would report "no gap" for a paper that still
        has unfetched citations.  `short_years` is a display string listing them.
        """
        if cached:
            if strategy == 'year':
                year_gap = _cc_year_records_gap(cached.get('year_records'))
                if year_gap is not None:
                    needed, seen, target = year_gap
                    short = ', '.join(
                        f'{year}({year_seen}/{year_histogram})'
                        for year, year_seen, year_histogram
                        in _cc_year_records_shortfall_years(cached.get('year_records'))
                    )
                    return needed, seen, target, short
                yfd = cached.get('year_fetch_diagnostics') or {}
                seen = yfd.get('seen_total')
                target = yfd.get('histogram_total')
            else:
                dfd = cached.get('direct_fetch_diagnostics') or {}
                seen = dfd.get('seen_total')
                target = dfd.get('scholar_total')
            if seen is not None and target is not None:
                try:
                    return max(0, int(target) - int(seen)), int(seen), int(target), ''
                except (TypeError, ValueError):
                    pass
        # Fallback: cached count vs scholar total.
        try:
            return max(0, int(scholar_total) - int(cached_count)), None, int(scholar_total), ''
        except (TypeError, ValueError):
            return 0, None, 0, ''

    def _fetch_citations_with_progress(self, citedby_url, cache_path, title,
                                        num_citations, pub_url, pub_year, resume_from,
                                        completed_years_in_current_run=None, prev_scholar_count=0,
                                        partial_year_start=None, saved_dedup_count=0,
                                        allow_incremental_early_stop=True,
                                        force_year_rebuild=False,
                                        selective_refresh_years=None,
                                        rehydrated_year_fetch_diagnostics=None,
                                        pub_obj=None,
                                        fetch_policy=None,
                                        direct_resume_state=None):
        from code.fetch_session import YearFetchSession
        # The inner per-year context (_fetch_by_year) seeds itself from
        # self._completed_year_segments, not from the outer context created here,
        # so the per-paper completed years must be published on the fetcher as
        # well — otherwise a same-run retry re-fetches years it already finished.
        self._completed_year_segments = set(completed_years_in_current_run or [])
        year_ctx = YearFetchSession(
            baseline=(ps.fetch if (ps := self._paper_states.get(title)) else None) if hasattr(self, '_paper_states') else None,
            completed_year_segments=set(completed_years_in_current_run or []),
            partial_year_start=dict(partial_year_start or {}),
            dedup_count=int(saved_dedup_count or 0),
            probed_year_counts=None,  # always probe fresh in fetch_by_year
            probed_year_count_complete=False,
            year_fetch_diagnostics=self._normalize_year_fetch_diagnostics(rehydrated_year_fetch_diagnostics) or {},
            cached_year_counts=self._year_count_map(list(resume_from)),
        )
        result = _cf.fetch_citations_with_progress(self, year_ctx, citedby_url, cache_path, title,
                                                   num_citations, pub_url, pub_year, resume_from,
                                                   completed_years_in_current_run=completed_years_in_current_run,
                                                   prev_scholar_count=prev_scholar_count,
                                                   partial_year_start=partial_year_start,
                                                   saved_dedup_count=saved_dedup_count,
                                                   allow_incremental_early_stop=allow_incremental_early_stop,
                                                   force_year_rebuild=force_year_rebuild,
                                                   selective_refresh_years=selective_refresh_years,
                                                   pub_obj=pub_obj,
                                                   fetch_policy=fetch_policy,
                                                   direct_resume_state=direct_resume_state)
        # Sync per-paper year_ctx state back to self for code that reads these directly
        self._dedup_count = year_ctx.dedup_count
        self._cached_year_counts = year_ctx.cached_year_counts
        self._completed_year_segments = year_ctx.completed_year_segments
        self._probed_year_counts = year_ctx.probed_year_counts
        self._probed_year_count_complete = year_ctx.probed_year_count_complete
        self._year_fetch_diagnostics = year_ctx.year_fetch_diagnostics
        return result
    def _fetch_by_year(self, citedby_url, old_citations, fresh_citations, save_progress,
                       num_citations, pub_year, prev_scholar_count=0,
                       allow_incremental_early_stop=True,
                       force_year_rebuild=False,
                       selective_refresh_years=None,
                       year_fetch_diagnostics=None):
        from code.fetch_session import YearFetchSession
        year_ctx = YearFetchSession(
            baseline=None,  # inner year_ctx — baseline handled by outer _fetch_citations_with_progress
            completed_year_segments=self._completed_year_segments,
            partial_year_start=self._partial_year_start,
            dedup_count=self._dedup_count,
            probed_year_counts=self._probed_year_counts,
            probed_year_count_complete=self._probed_year_count_complete,
            year_fetch_diagnostics=year_fetch_diagnostics or {},
            cached_year_counts=self._cached_year_counts,
        )
        # Wrap save_progress to sync inner year_ctx's year_fetch_diagnostics to the fetcher
        # attribute before each save.
        _orig_save_progress = save_progress
        def _synced_save_progress(fetch_finished, batch=None):
            self._live_year_fetch_diagnostics = year_ctx.year_fetch_diagnostics
            self._live_dedup_count = year_ctx.dedup_count
            self._live_probed_year_counts = year_ctx.probed_year_counts
            self._live_probe_complete = year_ctx.probed_year_count_complete
            self._live_partial_year_start = year_ctx.partial_year_start
            _orig_save_progress(fetch_finished, batch)

        result = _cf.fetch_by_year(self, year_ctx, citedby_url, old_citations, fresh_citations, _synced_save_progress,
                                   num_citations, pub_year, prev_scholar_count,
                                   allow_incremental_early_stop=allow_incremental_early_stop,
                                   force_year_rebuild=force_year_rebuild,
                                   selective_refresh_years=selective_refresh_years,
                                   year_fetch_diagnostics=year_fetch_diagnostics,
                                   fetch_mode=self.fetch_mode)
        self._dedup_count = year_ctx.dedup_count
        self._cached_year_counts = year_ctx.cached_year_counts
        self._completed_year_segments = year_ctx.completed_year_segments
        self._probed_year_counts = year_ctx.probed_year_counts
        self._probed_year_count_complete = year_ctx.probed_year_count_complete
        self._year_fetch_diagnostics = year_ctx.year_fetch_diagnostics
        self._partial_year_start = year_ctx.partial_year_start
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
        return _cio_load_citation_cache(self, title)

    def _derive_citation_cache_state(self, pub, cached):
        return _cio_derive_citation_cache_state(pub, cached, YEAR_BASED_THRESHOLD)

    def _citation_status(self, pub):
        """Return cache status: 'skip_zero' | 'missing' | 'complete' | 'partial' | 'stalled'."""
        if pub['num_citations'] == 0:
            return 'skip_zero'
        # Cross-run state comes exclusively from the output file.
        # Cache files are only for within-run interruption recovery
        # and are never read on a fresh program start.
        output_state = getattr(self, '_paper_states', {}).get(pub['title'])
        if output_state:
            return _os_resolve_citation_status_from_output(pub, output_state, YEAR_BASED_THRESHOLD)
        return 'missing'

    @staticmethod
    def _format_completeness_diag(st, cached, paper_state=None):
        """Return a one-line completeness diagnostic from cached state."""
        if st in ('skip_zero', None):
            return ''
        if st == 'missing':
            return '  no cached state → missing'
        pst = to_paper_fetch_state(paper_state)
        if pst:
            return pst.completeness_diag()
        cached_pst = to_paper_fetch_state(cached)
        if cached_pst:
            return cached_pst.completeness_diag()
        return ''

    def has_pending_work(self):
        """Check if there are any papers with incomplete citation caches."""
        if not os.path.exists(self.profile_json):
            return True
        if not getattr(self, '_paper_states', None):
            # main() calls this before run(), which is what normally loads the
            # output state.  Without loading it here every paper with citations
            # looks 'missing', so the "no changes → skip the citation phase"
            # shortcut could never fire.
            self._paper_states = _os_load_paper_states(self.out_json)
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
        self._run_new_citations_total = 0
        self._papers_fetched_count = 0
        # Mid-paper snapshots are per-run only: _save_output consumes them and
        # clears them, but reset here too so a reused fetcher can never persist
        # a snapshot left over from an earlier run.
        self._mid_paper_state = {}

        # Load profile
        if not os.path.exists(self.profile_json):
            print(f"Error: {self.profile_json} not found. Profile must be fetched first.")
            return False
        with open(self.profile_json, 'r', encoding='utf-8') as f:
            profile = json.load(f)
        publications = profile.get('publications', [])
        self._profile_data = profile

        # Build citedby_url mapping from profile publications.
        # Each publication in the profile already has citedby_url and url fields.
        if not publications:
            print(f"Error: {self.profile_json} has no publications. Profile must be fetched first.")
            return False
        url_map = {p['title']: {
            'citedby_url': p.get('citedby_url', ''),
            'pub_url':     p.get('url', 'N/A'),
        } for p in publications}

        # Load previous output file state so cross-run decisions are based on
        # the output file, not on stale per-paper cache files.
        self._paper_states = _os_load_paper_states(self.out_json)

        # force mode: clear _fetch_state from output file so every in-range
        # paper is treated as 'missing' and re-fetched from scratch.
        if self.fetch_mode == 'force':
            end_idx = self.skip + self.limit if self.limit else len(publications)
            for pub in publications[self.skip:end_idx]:
                title = pub['title']
                if title in self._paper_states:
                    del self._paper_states[title]
                    print(f"  Force mode: cleared output state for '{title[:55]}'")

        def cache_status(pub):
            # Update PaperFetchState with current profile counts BEFORE
            # computing status, so that scholar_changed takes effect
            # within the same run.
            paper_state = getattr(self, '_paper_states', {}).get(pub['title'])
            pst = to_paper_fetch_state(paper_state) if paper_state else None
            if pub['num_citations'] == 0:
                # Scholar reports no citations for this paper.  Keep whatever was
                # collected earlier: a transient 0 in the profile must not delete
                # the stored list.  Nothing to fetch either way.
                if pst is not None:
                    cached = pst.to_dict()
                    cached['citations'] = paper_state.citations
                    return 'skip_zero', cached, pst
                return 'skip_zero', None, None
            if not pst:
                return self._citation_status(pub), None, None
            current_total = pub.get('num_citations')
            if current_total is not None:
                try:
                    current_total = int(current_total)
                except (TypeError, ValueError):
                    current_total = None
                if current_total is not None:
                    old_total = pst.num_citations_on_scholar
                    pst._num_citations_on_scholar = current_total
                    if old_total is not None and old_total < current_total:
                        pst.mark_scholar_changed()
            # Now compute status with the up-to-date PaperFetchState.
            st = self._citation_status(pub)
            if st == 'skip_zero':
                return st, None, None
            # Build dict from PaperFetchState + extra runtime fields.
            cached = pst.to_dict()
            citations = paper_state.citations
            cached['citations'] = citations
            cached['num_citations_on_scholar'] = current_total
            # Sync scholar_total in diagnostics.
            for diag_key in ('year_fetch_diagnostics', 'direct_fetch_diagnostics'):
                diag = cached.get(diag_key)
                if isinstance(diag, dict) and current_total is not None:
                    diag['scholar_total'] = current_total
            # Consistency check: empty citations but diagnostics claims data.
            if len(citations) == 0 and pst.complete_fetch_attempt:
                pst._complete_fetch_attempt = False
                cached['complete_fetch_attempt'] = False
            # For direct-mode papers without diagnostics, derive from citations.
            if (len(citations) > 0 and pst.fetch_strategy != 'year'
                    and not isinstance(pst.direct_fetch_diagnostics, dict)):
                pst.restore_direct_diag_from_citations(citations)
                cached['direct_fetch_diagnostics'] = pst.to_dict().get('direct_fetch_diagnostics')
            # Bubble scholar_changed into the dict.
            cached['scholar_changed'] = pst.scholar_changed
            return st, cached, pst

        statuses = [cache_status(p) for p in publications]
        need_fetch = [(pub, st, cached) for pub, (st, cached, _)
                      in zip(publications, statuses)
                      if st in ('missing', 'partial')]

        # Papers are processed in profile order (citation count desc).  The old
        # random shuffle here had no effect: the main loop iterates
        # `publications` and recomputes each status, never the shuffled list.
        stalled_count = sum(1 for s, *_ in statuses if s == 'stalled')
        print(f"Total papers: {len(publications)}")
        print(f"  Zero citations (skip):     {sum(1 for s, *_ in statuses if s == 'skip_zero')}")
        print(f"  Cache complete (skip):     {sum(1 for s, *_ in statuses if s == 'complete')}")
        if stalled_count:
            print(f"  Stalled (skip):            {stalled_count}")
        print(f"  Need fetch/resume:         {len(need_fetch)}")
        output_state_count = len(getattr(self, '_paper_states', {}))
        if output_state_count:
            print(f"  Output state loaded:       {output_state_count} papers")
        else:
            print(f"  Output state:              none (will fall back to cache files)")
        if self.skip:
            print(f"  Skipping first {self.skip} (--skip)")
        if self.limit:
            print(f"  Processing limit (--limit): {self.limit}")
        print()

        # Pre-flight plan: list every paper that actually needs new data.
        # A paper whose cached data already meets the target (no gap) needs
        # no update and is omitted — e.g. a stale scholar_changed flag left on
        # otherwise complete data must not clutter the plan.
        if need_fetch:
            plan_lines = []
            recheck_count = 0
            for pub, st, cached in need_fetch:
                title = pub.get('title', '(unknown)')[:55]
                scholar_total = pub.get('num_citations', 0)
                cached_count = len(cached.get('citations', [])) if cached else 0
                old_total = cached.get('num_citations_on_scholar') if cached else None
                fetch_policy = self._resolve_citation_fetch_policy(
                    scholar_total, pub.get('year', 'N/A'))
                strategy = (fetch_policy['strategy']
                            if isinstance(fetch_policy, dict) else fetch_policy.strategy)

                needed, seen, target, short_years = self._plan_fetch_gap(
                    strategy, cached, scholar_total, cached_count)
                # Skip papers with no real gap: data is already complete.
                # They are still processed below (e.g. a stale scholar_changed
                # flag), so they are reported separately rather than silently
                # dropped from the plan.
                if st != 'missing' and needed <= 0:
                    recheck_count += 1
                    continue

                if st == 'missing':
                    gap = f"target={target} (first fetch)"
                else:
                    gap = f"cached={cached_count}"
                    if seen is not None and seen != cached_count:
                        gap += f", seen={seen}"
                    gap += f", target={target}"
                    if needed > 0:
                        gap += f", +{needed} needed"
                    if short_years:
                        gap += f" [short years: {short_years}]"

                change_note = ""
                if old_total is not None and old_total != scholar_total:
                    change_note = f" [Scholar {old_total}→{scholar_total}]"

                plan_lines.append(f"  [{strategy.upper():6s}] {title}\n"
                                  f"            {gap}{change_note}")
            if plan_lines or recheck_count or stalled_count:
                print("-" * 70)
                if plan_lines:
                    print(f"  Fetch plan ({len(plan_lines)} paper(s) need new data):")
                    print("-" * 70)
                    for line in plan_lines:
                        print(line)
                    print("-" * 70)
                if recheck_count:
                    print(f"  Also rechecking {recheck_count} paper(s) with no "
                          f"cached-data gap (Scholar counts changed)")
                if stalled_count:
                    print(f"  Skipping {stalled_count} stalled paper(s): same result "
                          f"{STALL_AFTER_ATTEMPTS}+ runs in a row (e.g. Scholar lists "
                          f"fewer citations than its own count)")
                print()

        results   = []
        fetch_idx = 0

        try:
            self._run_main_loop(publications, cache_status, url_map, need_fetch, results, fetch_idx)
        except KeyboardInterrupt:
            print(f"\n  Interrupted by user. Saving results...", flush=True)
        finally:
            # Always save output (normal completion, interruption, error, or partial results)
            self._save_output(results)
        return True

    def _run_main_loop(self, publications, cache_status, url_map, need_fetch, results, fetch_idx):
        """Inner loop extracted so KeyboardInterrupt saves output."""
        results[:] = [None] * len(publications)

        papers_processed = 0  # counts papers after --skip (for --limit)

        for idx, pub in enumerate(publications, 1):
            title         = pub['title']
            num_citations = pub['num_citations']
            cache_result = cache_status(pub)
            if len(cache_result) >= 3:
                st, cached, paper_state = cache_result[:3]
                paper_state = to_paper_fetch_state(paper_state)
            else:
                st, cached = cache_result
                paper_state = None

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
                kept = cached['citations'] if cached else []
                if kept:
                    print(f"[{idx}/{len(publications)}] {title[:55]}... -> skip "
                          f"(Scholar reports 0 citations; keeping {len(kept)} cached)")
                else:
                    print(f"[{idx}/{len(publications)}] {title[:55]}... -> skip (0 citations)")
                results[idx - 1] = {'pub': pub, 'citations': kept}
                continue

            if st == 'complete':
                print(f"[{idx}/{len(publications)}] {title[:55]}... -> cached ({len(cached['citations'])} citations)")
                if self.fetch_mode != 'force':
                    print(PaperCitationFetcher._format_completeness_diag(st, cached, paper_state=paper_state))
                results[idx - 1] = {'pub': pub, 'citations': cached['citations']}
                continue

            # Stalled: the fetch outcome has repeated unchanged (e.g. Scholar
            # lists fewer citations than it reports), so retrying cannot help
            # while Scholar's counts stay the same.  Kept in the output as-is.
            if st == 'stalled':
                stall_note = (f" (same result {paper_state.stall_count} runs in a row)"
                              if paper_state and paper_state.stall_count else "")
                print(f"[{idx}/{len(publications)}] {title[:55]}... -> stalled{stall_note}")
                print(PaperCitationFetcher._format_completeness_diag(st, cached, paper_state=paper_state))
                results[idx - 1] = {'pub': pub, 'citations': cached['citations'] if cached else []}
                continue

            # rough mode: skip only when Scholar count is unchanged AND the last
            # fetch ran to completion (complete_fetch_attempt=True).
            # If the previous fetch was interrupted, re-fetch regardless.
            if self.fetch_mode == 'rough' and cached:
                last_known = (paper_state.num_citations_on_scholar
                              if paper_state
                              else cached.get('num_citations_on_scholar'))
                complete = (paper_state.complete_fetch_attempt
                            if paper_state
                            else cached.get('complete_fetch_attempt'))
                try:
                    last_known_int = int(last_known) if last_known is not None else None
                except (TypeError, ValueError):
                    last_known_int = None
                if (last_known_int is not None
                        and last_known_int == num_citations
                        and complete):
                    print(f"[{idx}/{len(publications)}] {title[:55]}... -> skip-rough ({num_citations} unchanged, fetch complete)")
                    results[idx - 1] = {'pub': pub, 'citations': cached.get('citations', [])}
                    continue

            fetch_idx += 1
            self._new_citations_count = 0  # reset per paper
            urls        = url_map.get(title, {})
            citedby_url = urls.get('citedby_url', '')
            pub_url     = urls.get('pub_url', 'N/A')

            # scholarly internally prepends 'https://scholar.google.com'
            if citedby_url.startswith('https://scholar.google.com'):
                citedby_url = citedby_url[len('https://scholar.google.com'):]

            if not citedby_url:
                print(f"[{idx}/{len(publications)}] {title[:55]}... -> Warning: no citedby_url, skip")
                results[idx - 1] = {'pub': pub, 'citations': cached['citations'] if cached else []}
                continue

            prev_scholar_count = 0
            # Use the synthetic cache from cache_status() directly; it already
            # contains the citations array (from output state or cache file).
            attempt_state = self._resolve_refresh_strategy(pub, cached, st, citedby_url=citedby_url, paper_state=paper_state)
            if attempt_state['prev_scholar_count']:
                prev_scholar_count = attempt_state['prev_scholar_count']
            partial_year_start = attempt_state['partial_year_start']
            saved_dedup_count = attempt_state['saved_dedup_count']
            allow_incremental_early_stop = attempt_state['allow_incremental_early_stop']
            resume_from = attempt_state['resume_from']
            completed_years_in_current_run = attempt_state['completed_years_in_current_run']
            force_year_rebuild = attempt_state['force_year_rebuild']
            selective_refresh_years = attempt_state['selective_refresh_years']
            rehydrated_year_fetch_diagnostics = attempt_state['rehydrated_year_fetch_diagnostics']
            direct_resume_state = attempt_state.get('direct_resume_state')
            fetch_policy = attempt_state.get('fetch_policy') or self._resolve_citation_fetch_policy(
                num_citations,
                pub.get('year', 'N/A'),
            )
            action = attempt_state['action']

            print(f"[{idx}/{len(publications)}] {title[:55]}...")
            print(PaperCitationFetcher._format_completeness_diag(st, cached, paper_state=paper_state))
            print(f"  {action}")

            # Skip year-based fetch when all cached citations are unyeared and
            # the previous fetch ran to completion.  Year-based diff cannot
            # improve citations that carry no year.
            if fetch_policy['strategy'] == 'year' and cached:
                complete_check = (paper_state.complete_fetch_attempt
                                  if paper_state
                                  else cached.get('complete_fetch_attempt'))
                s_total_check = (paper_state.num_citations_on_scholar
                                 if paper_state
                                 else cached.get('num_citations_on_scholar', 0))
                if (complete_check
                        and not resume_from
                        and num_citations <= s_total_check):
                    all_citations = cached.get('citations', [])
                    if all_citations and not self._filter_citations_with_year(all_citations):
                        print(f"  -> skip (all {len(all_citations)} cached citations are unyeared)")
                        results[idx - 1] = {'pub': pub, 'citations': all_citations}
                        continue

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
                            latest_output_state = getattr(self, '_paper_states', {}).get(title)
                            # Within-run retry: prefer cache file (written by
                            # save_progress with the most recent citations) over
                            # output state (which lacks the citations array).
                            # _resolve_refresh_strategy expects a dict; convert
                            # PaperFetchState if needed.
                            if latest_output_state is not None:
                                pst = to_paper_fetch_state(latest_output_state)
                                latest_output_state = pst.to_dict() if pst else dict(latest_output_state)
                            retry_strategy_cached = latest_cache if latest_cache else latest_output_state
                            if retry_strategy_cached:
                                retry_attempt_state = self._resolve_refresh_strategy(
                                    pub,
                                    retry_strategy_cached,
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
                                    # Keep synthesised year diagnostics from the
                                    # retry strategy call (e.g. direct→year transition)
                                    # rather than discarding them.
                                    rehydrated_year_fetch_diagnostics = (
                                        retry_attempt_state.get('rehydrated_year_fetch_diagnostics')
                                    )
                                else:
                                    completed_years_in_current_run = retry_attempt_state['completed_years_in_current_run']
                                    rehydrated_year_fetch_diagnostics = retry_attempt_state['rehydrated_year_fetch_diagnostics']
                                    if not rehydrated_year_fetch_diagnostics and latest_cache:
                                        yr = latest_cache.get('year_records')
                                        if isinstance(yr, list) and yr:
                                            per_year = _os_index_year_records(yr)
                                            rehydrated_year_fetch_diagnostics = self._normalize_year_fetch_diagnostics(per_year) or None
                                resume_from = latest_resume_from
                                num_citations = pub.get('num_citations', num_citations)
                                fetch_policy = retry_attempt_state.get('fetch_policy') or fetch_policy
                                allow_incremental_early_stop = retry_attempt_state['allow_incremental_early_stop']
                                force_year_rebuild = retry_attempt_state['force_year_rebuild']
                                selective_refresh_years = retry_attempt_state['selective_refresh_years']
                                saved_dedup_count = retry_attempt_state['saved_dedup_count']
                                partial_year_start = retry_attempt_state['partial_year_start']
                                # Within-run resume: read the saved position from
                                # the cache file (written by save_progress), not from
                                # _resolve_refresh_strategy which always returns None.
                                direct_resume_state = latest_cache.get('direct_resume_state') if latest_cache else None
                                retry_suffix = self._direct_resume_log_suffix(direct_resume_state)
                                print(f"  {now_str()} Retrying with {len(resume_from)} cached citations from previous attempt{retry_suffix}")
                    if not fetch_completed:
                        citations = self._fetch_citations_with_progress(
                            citedby_url, None, title, num_citations,
                            pub_url, pub.get('year', 'N/A'), resume_from,
                            completed_years_in_current_run=completed_years_in_current_run,
                            prev_scholar_count=prev_scholar_count,
                            partial_year_start=partial_year_start,
                            saved_dedup_count=saved_dedup_count,
                            allow_incremental_early_stop=allow_incremental_early_stop,
                            force_year_rebuild=force_year_rebuild,
                            selective_refresh_years=selective_refresh_years,
                            rehydrated_year_fetch_diagnostics=rehydrated_year_fetch_diagnostics,
                            pub_obj=pub,
                            fetch_policy=fetch_policy,
                            direct_resume_state=direct_resume_state,
                        )
                        fetch_completed = True
                    num_citations = pub['num_citations']
                    # Update in-memory state from save_progress so Done log
                    # and _save_output use the latest diagnostics.
                    cache_snapshot = self._load_citation_cache(title)
                    if cache_snapshot:
                        if not getattr(self, '_paper_states', None):
                            self._paper_states = {}
                        # The snapshot is written mid-fetch and knows nothing
                        # about the cross-run stall counters, so carry them over
                        # from the pre-fetch state before replacing it.
                        previous_state = to_paper_fetch_state(
                            self._paper_states.get(title))
                        self._paper_states[title] = PaperState(
                            fetch=PaperFetchState.from_dict(cache_snapshot)
                            .inherit_stall_tracking(previous_state),
                            citations=citations or [],
                        )
                    # Use per-year diagnostics for year-based fetch (authoritative, per-run).
                    # Only use them when fetch_policy is year to avoid stale state from
                    # a previous paper's year-based fetch polluting direct fetch totals.
                    year_fetch_diagnostics = (
                        getattr(self, '_year_fetch_diagnostics', None)
                        if fetch_policy.get('strategy') == 'year'
                        else None
                    )
                    if year_fetch_diagnostics:
                        seen_total = sum(d.get('seen_total', 0) for d in year_fetch_diagnostics.values())
                        run_dedup = sum(d.get('dedup_count', 0) for d in year_fetch_diagnostics.values())
                    else:
                        run_dedup = self._dedup_count
                        seen_total = len(citations) + run_dedup
                    dedup_str = f", {run_dedup} dupes" if run_dedup else ""
                    histogram_total = sum(
                        d.get('histogram_count', d.get('scholar_total', 0))
                        for d in (year_fetch_diagnostics or {}).values()
                    ) if year_fetch_diagnostics else 0
                    new_str = f", {self._new_citations_count} new this paper" if self._new_citations_count else ""
                    # Show the fetch target comparison.
                    if year_fetch_diagnostics:
                        target = histogram_total
                        target_label = 'histogram_total'
                    else:
                        target = num_citations
                        target_label = 'scholar_total'
                    if target is not None:
                        cmp_sym = '≥' if seen_total >= target else '<'
                        diag = f"  {fetch_policy.get('strategy', 'direct')}: seen_total={seen_total} {cmp_sym} {target_label}={target}"
                    else:
                        diag = "  target unavailable"
                    if histogram_total > 0:
                        unyeared = max(0, num_citations - histogram_total)
                        print(f"  Done: {len(citations)} cached, {seen_total} seen{dedup_str}{new_str} "
                              f"(scholar={num_citations}, histogram={histogram_total}, unyeared={unyeared})")
                        print(diag, flush=True)
                    else:
                        print(f"  Done: {len(citations)} cached, {seen_total} seen{dedup_str}{new_str} "
                              f"(scholar_total={num_citations})")
                        print(diag, flush=True)
                    year_counts = self._year_count_map(citations)
                    if year_counts:
                        year_total = sum(year_counts.values())
                        unyeared = max(0, len(citations) - year_total)
                        year_summary = self._format_year_count_summary(year_counts)
                        unyeared_suffix = f", unyeared={unyeared}" if unyeared else ""
                        print(f"  Year summary: {year_summary}{unyeared_suffix}", flush=True)
                    # Read diagnostics from in-memory PaperFetchState (updated above).
                    pst = to_paper_fetch_state(getattr(self, '_paper_states', {}).get(title) if getattr(self, '_paper_states', None) else None)
                    direct_fetch_diagnostics = (pst.direct_fetch_diagnostics or {}) if pst else {}
                    has_direct_fetch_summary = direct_fetch_diagnostics.get('scholar_total') is not None
                    direct_underfetched = (
                        has_direct_fetch_summary
                        and (direct_fetch_diagnostics.get('seen_total') or 0) < direct_fetch_diagnostics['scholar_total']
                    )
                    if has_direct_fetch_summary:
                        if direct_underfetched:
                            print("  Direct fetch under-fetched; recording current results", flush=True)
                        else:
                            print(f"  {self._direct_fetch_diagnostics_message(direct_fetch_diagnostics)}", flush=True)
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
                    total_new = self._run_new_citations_total + max(0, self._new_citations_count)
                    print(f"  [{now}] Error (attempt {attempt_str}, "
                          f"pages: {self._session_ctx.total_page_count} total / "
                          f"{self._session_ctx.curl_page_count} this curl, "
                          f"new citations: {total_new}): {e}")
                    if is_post_fetch_failure:
                        continue
                    # Non-interactive: give up after MAX_RETRIES attempts
                    if not self.interactive_captcha and attempt >= MAX_RETRIES:
                        traceback.print_exc()
                        print(f"\n  [{now}] All retry attempts exhausted. Terminating.", flush=True)
                        # run()'s finally writes the output (including the
                        # in-flight paper's partial citations); saving here too
                        # would write a second, less complete file on the way out.
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
                    print(f"  [{now}] Progress already saved to cache by PageVisit/batch")

            results[idx - 1] = {'pub': pub, 'citations': citations or []}
            self._run_new_citations_total += self._new_citations_count
            self._new_citations_count = 0  # consumed by _run_new_citations_total
            # Clear scholar_changed after a successful fetch (state was already
            # updated above, inside the while loop), then fold this attempt's
            # outcome into the stall tracking — an interrupted fetch never
            # reaches here, so it cannot count as an identical result.
            pst = to_paper_fetch_state(getattr(self, '_paper_states', {}).get(title) if getattr(self, '_paper_states', None) else None)
            if pst:
                pst.clear_scholar_changed()
                pst.record_fetch_outcome()

            if fetch_idx < (self.limit or len(need_fetch)):
                print(f"  {now_str()} Next paper... [{self._wait_status()}]", flush=True)

    def _inject_cookies_from_curl(self, curl_str):
        """Parse cookies and selected headers from a pasted cURL command."""
        result = _int_inject_cookies(
            curl_str,
            curl_header_allowlist=self._curl_header_allowlist,
            last_scholar_url=self._last_scholar_url,
            injected_cookies_ref=self._injected_cookies,
            injected_header_overrides_ref=self._injected_header_overrides,
            curl_save_path=self._curl_save_path,
        )
        if result > 0:
            self._session_ctx.curl_page_count = 1
        return result

    def _harvest_cookies_from_browser(self, url=None):
        """Optional: open a visible browser and harvest the session automatically.

        Only used when the human types 'browser' at a cookie prompt *and* this
        interpreter has playwright.  Everywhere else the manual paste flow is
        untouched, so the crawler gains no hard dependency.
        """
        target = url or getattr(self, '_last_scholar_url', '') or 'https://scholar.google.com/'
        return _int_try_browser_harvest(target, inject_fn=self._inject_cookies_from_curl)

    def _try_interactive_captcha(self, url):
        """Prompt user to solve captcha manually and inject resulting cookies."""
        result = _int_try_interactive_captcha(
            url,
            inject_fn=self._inject_cookies_from_curl,
            harvest_fn=self._harvest_cookies_from_browser,
        )
        if result:
            self._captcha_solved_count += 1
        return result

    def _wait_proxy_switch(self, max_hours=24):
        """Wait up to max_hours (shortened by --accelerate) for the user to switch proxy/IP."""
        return _int_wait_proxy_switch(max_hours, scale=self._delay_scale)

    def _enrich_cite_formats(self, final_results):
        """Fill ``citation['cite']`` from Scholar's Cite dialog (--cite-formats).

        One request per citation that carries a ``cid``, plus one for the signed
        BibTeX download.  Requests reuse the patched scholarly session that
        ``_patch_scholarly()`` installed, so the usual waits, captcha recovery and
        proxy prompts apply; anything already parsed is served from
        ``output/cite_format_cache.json`` without a request.  Returns the stats
        dict, or None when no navigator is available (the output is still written).
        """
        nav = getattr(scholarly, '_Scholarly__nav', None)
        get_soup = getattr(nav, '_get_soup', None)
        get_page = getattr(nav, '_get_page', None)
        if not callable(get_soup):
            print("  Cite dialogs unavailable (no scholarly navigator); leaving "
                  "citation['cite'] empty.", flush=True)
            return None
        citations = [c for result in final_results
                     for c in (result.get('citations') or [])
                     if isinstance(c, dict)]
        print("\n" + "=" * 70)
        print("  Fetching citation formats (Cite dialog, --cite-formats)")
        print("=" * 70)
        stats = _cite_format.enrich_citations_with_cite_formats(
            citations,
            cache_path=os.path.join(self.output_dir, _cite_format.CACHE_FILENAME),
            get_soup=get_soup,
            get_page=get_page,
        )
        print(f"  citation formats resolved: {stats['resolved']}/{stats['with_cid']} "
              f"(fetched {stats['fetched']}, cached {stats['cache_hits']}, "
              f"failed {stats['failed']}, no cid {stats['missing_cid']})")
        print(f"  bibtex: {stats['bib_fetched']} fetched, "
              f"{stats['bib_from_cache']} cached, {stats['bib_failed']} failed, "
              f"{stats['bib_missing']} unavailable")
        return stats

    def _save_output(self, results):
        """Save citation results to JSON and Excel."""
        print("\n" + "=" * 70)
        profile = getattr(self, '_profile_data', None)
        if profile is None and os.path.exists(self.profile_json):
            with open(self.profile_json, 'r', encoding='utf-8') as f:
                profile = json.load(f)
        publications = profile.get('publications', []) if profile else []
        paper_states = getattr(self, '_paper_states', {})
        # In-run snapshots written by save_progress.  For a paper whose fetch was
        # interrupted or aborted this run, the mid-paper snapshot is the only
        # place holding the live probe and the citations fetched so far — the
        # start-of-run state would persist "complete" for a paper that still has
        # unfetched citations, so those citations would never be resumed.
        mid_paper_states = getattr(self, '_mid_paper_state', None) or {}

        def _freshest_fetch_state(title):
            snapshot = mid_paper_states.get(title)
            # Only a self-describing snapshot may replace the persisted state.
            # Year-mode snapshots always carry fetch_strategy plus the live probe;
            # a direct-mode snapshot from a mid-fetch save has neither (they are
            # only built once the batch finishes), so it would drop the direct
            # diagnostics the completeness check needs and slot the paper into
            # the year rule instead — reporting "complete" for a paper that is
            # still missing citations.
            using_snapshot = bool(snapshot and snapshot.get('fetch_strategy'))
            source = snapshot if using_snapshot else paper_states.get(title)
            pst = to_paper_fetch_state(source)
            if pst is None:
                return {}
            if using_snapshot:
                # The snapshot knows the live probe but nothing about the
                # cross-run stall counters: save_progress() builds it without
                # them and record_fetch_outcome() updates _paper_states after the
                # fetch.  Carry them over, or the persisted count restarts at 0
                # every run and stall detection never fires.
                stored = to_paper_fetch_state(paper_states.get(title))
                if stored is not None:
                    pst._stall_signature = stored.stall_signature
                    pst._stall_count = stored.stall_count
            return pst.to_dict()
            return pst.to_dict() if pst else {}

        def _build_entry(pub, citations):
            """Build output entry with correct _fetch_state."""
            title = pub.get('title', '') if pub else ''
            citations = self._dedup_citations_for_output(citations, title)
            fetch_state = _freshest_fetch_state(title)
            current_total = pub.get('num_citations') if pub else None
            if current_total is not None and fetch_state:
                fetch_state['num_citations_on_scholar'] = current_total
                for diag_key in ('year_fetch_diagnostics', 'direct_fetch_diagnostics'):
                    diag = fetch_state.get(diag_key)
                    if isinstance(diag, dict):
                        diag['scholar_total'] = current_total
                        diag['cached_total'] = len(citations)
            state_obj = PaperFetchState.from_dict(fetch_state)
            fetch_state = state_obj.to_dict()
            pub_out = PubInfo.from_dict(pub).to_dict() if pub else {}
            return {
                'pub': pub_out,
                'citations': citations,
                'fetch_complete': state_obj.is_complete(),
                **({'_fetch_state': fetch_state} if fetch_state else {}),
            }

        final_results = []
        for i, r in enumerate(results):
            if r is not None:
                pub = r['pub']
                citations = r.get('citations', [])
                final_results.append(_build_entry(pub, citations))
            else:
                pub = publications[i] if i < len(publications) else {}
                title = pub.get('title', '')
                # Prefer this run's partial progress (mid-paper snapshot), then
                # the last known state, so an interrupted fetch keeps whatever
                # it already collected.
                snapshot = mid_paper_states.get(title)
                if snapshot and snapshot.get('citations') is not None:
                    citations = snapshot['citations']
                else:
                    ps = paper_states.get(title)
                    citations = getattr(ps, 'citations', None) or []
                final_results.append(_build_entry(pub, citations))
        total_cites = sum(len(r['citations']) for r in final_results)
        # Papers that exist in the previous output but not in the current profile
        # are not written any more.  Surface it instead of dropping citations
        # that took hours to collect.
        current_titles = {r['pub'].get('title', '') for r in final_results}
        dropped_titles = [title for title in getattr(self, '_paper_states', {})
                          if title and title not in current_titles]
        if dropped_titles:
            print(f"Warning: {len(dropped_titles)} paper(s) present in the previous "
                  f"output are missing from the current profile and are dropped "
                  f"(e.g. {', '.join(sorted(dropped_titles)[:2])[:110]})", flush=True)

        # Opt-in: resolve every citation's five formats (MLA/APA/Chicago/Harvard/
        # Vancouver) + BibTeX from Scholar's Cite dialog, keyed by the row's cid.
        # Before the JSON write so the new fields land in the same save.  This is
        # the longest step of the save, and Ctrl+C is exactly how a run that hit
        # Scholar's rate limit is meant to end — so an interrupt (or a failure)
        # here must never cost the citation data that is already on hand: the
        # resolved formats are in output/cite_format_cache.json and the JSON is
        # written without them.
        if getattr(self, 'cite_formats', False):
            try:
                self._enrich_cite_formats(final_results)
            except KeyboardInterrupt:
                print("  Citation-format step interrupted; writing the output with "
                      "what was already resolved (the cache keeps the rest).", flush=True)
            except Exception as e:
                print(f"Warning: citation-format step failed "
                      f"({type(e).__name__}: {e}); writing the output without it", flush=True)

        output_payload = {
            'author_id': self.author_id,
            'fetch_time': datetime.now().isoformat(),
            'total_papers': len(final_results),
            'total_citations_collected': total_cites,
            'papers': final_results,
        }
        write_json_atomic(self.out_json, output_payload,
                          ensure_ascii=False, indent=2)
        print(f"Saved JSON : {self.out_json}")

        # The Excel file is a convenience view; a failure there must not abort a
        # run whose data is already on disk.
        try:
            self._save_xlsx(final_results, metadata=output_payload)
            print(f"Saved Excel: {self.out_xlsx}")
        except Exception as e:
            print(f"Warning: could not write {self.out_xlsx}: "
                  f"{type(e).__name__}: {e}", flush=True)

        total_papers = len(results)  # includes None slots (total publications)
        fetched_str = f", {self._papers_fetched_count} fetched" if self._papers_fetched_count else ""
        new_str = f", {self._run_new_citations_total} new" if self._run_new_citations_total else ""
        print(f"\nDone! {len(final_results)}/{total_papers} papers{fetched_str}, "
              f"{total_cites} collected citation records{new_str}")
        curl_pages = self._session_ctx.curl_page_count
        pages_info = (f"{self._session_ctx.total_page_count} pages "
                      f"({curl_pages} this curl)")
        print(f"Run summary: elapsed {self._elapsed_str()}"
              f" | {pages_info}"
              f" | {self._run_new_citations_total} new citations"
              f" | output total = collected per-paper citation records\n")

        return True


# ============================================================
# CLI Entry Point
# ============================================================

from code.cli import parse_args  # noqa: F401


def main():
    """
    CLI entry point.  Defined here (not in code.cli) so that tests can patch
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
        # Proxy settings: command line > crawler_config.json > system default.
        # The resolved values are also exported to the process environment
        # because httpx (and thus scholarly) only understands env proxies.
        proxy = resolve_proxy_config(
            getattr(args, 'proxy_url', None),
            getattr(args, 'no_proxy', None),
            getattr(args, 'config', None),
        )
        logger.debug("Proxy resolution: %s", proxy.describe())
        setup_proxy(proxy.proxy_url, proxy.no_proxy)
        export_proxy_env(proxy.proxy_url, proxy.no_proxy)
        # Console-only logging: sys.stdout is the TeeStream that mirrors into the
        # run log, so a FileHandler here would be a second writer on the same
        # file and the two would overwrite each other's lines.
        setup_logging(verbose=getattr(args, 'verbose', False))
        logger.info("Author ID: %s", author_id)
        logger.debug("fetch mode=%s skip=%s limit=%s interactive=%s accelerate=%s",
                     args.fetch_mode, args.skip, args.limit,
                     args.interactive_captcha, args.accelerate)
        print(f"Author ID: {author_id}")

        delay_scale = args.accelerate
        if args.accelerate != 1.0 and not args.interactive_captcha:
            print(f"Warning: --accelerate {args.accelerate} shortens every wait "
                  f"(Scholar rate-limits sooner). Use it with --interactive-captcha, "
                  f"which lets you solve a captcha without restarting.")

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
            cite_formats=getattr(args, 'cite_formats', False),
        )
        citation_fetcher._patch_scholarly()

        fetcher = AuthorProfileFetcher(author_id, args.output_dir, delay_scale=delay_scale)
        prev_profile = fetcher.load_prev_profile()
        success = fetcher.run()
        if not success:
            sys.exit(1)

        curr_profile = fetcher.load_prev_profile()
        if prev_profile and curr_profile:
            prev_citations = prev_profile.total_citations if prev_profile else -1
            curr_citations = curr_profile.total_citations if curr_profile else -2
            prev_pubs = prev_profile.total_publications if prev_profile else -1
            curr_pubs = curr_profile.total_publications if curr_profile else -2

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
