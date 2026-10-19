"""
code/author_fetcher.py — AuthorProfileFetcher: fetch author profile data
from Google Scholar and persist as author_<ID>_profile.json / .xlsx.

Phase 2 of this class also fills ``pub['venue_full']`` from each publication's
Scholar detail page (one request per publication, cached in
``output/venue_detail_cache.json``); see ``code/venue_detail.py`` for why the
truncated ``venue`` field is never overwritten.
"""

import os
import time
import traceback
from datetime import datetime

from scholarly import scholarly

from code.common import rand_delay
from code.log_utils import INFO, line_logger, run_log
from code.pub_info import PubInfo
from code.profile_io import (
    AuthorProfile,
    build_profile_count_summary,
    save_profile_xlsx as write_profile_xlsx,
)
from code.venue_detail import (
    CACHE_FILENAME as VENUE_DETAIL_CACHE_FILENAME,
    enrich_publications_with_venue_full,
)
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment


class AuthorProfileFetcher:
    def __init__(self, author_id, output_dir=".", delay_scale=1.0):
        self.author_id = author_id
        self.output_dir = output_dir
        self.delay_scale = delay_scale

        # Output files
        self.profile_json = os.path.join(output_dir, f"author_{author_id}_profile.json")
        self.profile_xlsx = os.path.join(output_dir, f"author_{author_id}_profile.xlsx")
        self.venue_detail_cache = os.path.join(output_dir, VENUE_DETAIL_CACHE_FILENAME)

        run_log.info(f"Output files: author_{author_id}_profile.json / .xlsx",
                     module="output", indent=1)

    # ------------------------------------------------------------------
    # Phase 1
    # ------------------------------------------------------------------

    def fetch_basics(self):
        """
        Phase 1: Fetch author basic info + citation stats.
        Always fetches from network (only 1 request) to detect citation changes.
        Returns (basics_dict, fetched_from_network).
        """
        run_log.info(f"Author ID: {self.author_id}", module="profile", indent=1)

        try:
            # Build the author stub locally (no HTTP request) so that
            # basics + indices + counts are fetched in a single network call.
            # search_author_id() would make a redundant first request just to
            # get a stub we can construct ourselves from the known author_id.
            author = {
                'container_type': 'Author',
                'filled': [],
                'scholar_id': self.author_id,
                'source': 'AUTHOR_PROFILE_PAGE',
            }
            author_filled = scholarly.fill(author, sections=['basics', 'indices', 'counts'])
            if author_filled is None:
                raise ValueError("fill() returned None — Scholar may be rate-limiting")
            run_log.debug("Author found", module="profile", indent=1)
            self._author_stub = author_filled  # cache for fetch_publications to reuse

            current_year = datetime.now().year
            cites_per_year = author_filled.get('cites_per_year', {})

            basics = {
                'name': author_filled.get('name', 'N/A'),
                'scholar_id': self.author_id,
                'affiliation': author_filled.get('affiliation', 'N/A'),
                'interests': author_filled.get('interests', []),
                'citedby': author_filled.get('citedby', 0),
                'citedby_this_year': cites_per_year.get(current_year, 0),
                'citedby5y': author_filled.get('citedby5y', 0),
                'hindex': author_filled.get('hindex', 0),
                'hindex5y': author_filled.get('hindex5y', 0),
                'i10index': author_filled.get('i10index', 0),
                'i10index5y': author_filled.get('i10index5y', 0),
                'cites_per_year': {str(k): v for k, v in cites_per_year.items()},
            }

            run_log.info(f"Author: {basics['name']} · {basics['affiliation']}",
                         module="profile", indent=1)
            run_log.info(f"citations: {basics['citedby']} total, "
                         f"{basics['citedby_this_year']} this year; "
                         f"h-index {basics['hindex']}, i10-index {basics['i10index']}",
                         module="profile", indent=2)

            return basics, True

        except (AttributeError, TypeError) as e:
            run_log.warn(f"Failed to fetch basic info: network issue or Scholar "
                         f"rate-limiting (unexpected None in response: {e})",
                         module="profile", indent=1)
            run_log.info("check the network connection or proxy, then retry",
                         module="profile", indent=2)
            return None, False
        except Exception as e:
            run_log.error(f"Failed to fetch basic info: {e}", module="profile", indent=1)
            traceback.print_exc()
            return None, False

    # ------------------------------------------------------------------
    # Phase 2
    # ------------------------------------------------------------------

    def fetch_publications(self, force_refresh=False, prev_publications=None):
        """
        Phase 2: Fetch all publications (scholarly handles pagination).

        When *force_refresh* is False and *prev_publications* is provided,
        the list from the previous output file is returned directly, avoiding
        a network request.

        Returns None when the fetch failed.  An empty list means "this author
        has no publications", which must never be confused with a failure.
        """
        if not force_refresh:
            if prev_publications:
                return prev_publications

        run_log.section("Phase 1b · publications", module="profile")

        try:
            # Reuse the stub from fetch_basics if available, avoiding a redundant
            # search_author_id request to the same profile URL.
            author = getattr(self, '_author_stub', None)
            if author is None:
                author = scholarly.search_author_id(self.author_id)

            d = rand_delay(self.delay_scale)
            run_log.debug(f"Waiting {d:.0f}s...", module="http", indent=1)
            time.sleep(d)

            run_log.debug("fetching the full publication list (may take several "
                          "minutes)", module="profile", indent=1)
            author_with_pubs = scholarly.fill(author, sections=['publications'])

            raw_pubs = author_with_pubs.get('publications', [])
            run_log.info(f"Found {len(raw_pubs)} publications",
                         module="profile", indent=1)

            publications = []
            for i, pub in enumerate(raw_pubs, 1):
                publications.append(PubInfo.from_scholarly(pub, i).to_dict())

                if i % 20 == 0:
                    run_log.debug(f"Processed {i}/{len(raw_pubs)} papers...",
                                  module="profile", indent=2)

            # Sort by citation count (descending) and renumber
            publications.sort(key=lambda x: x['num_citations'], reverse=True)
            for i, pub in enumerate(publications, 1):
                pub['no'] = i

            run_log.info(f"Publication list fetched ({len(publications)} papers)",
                         module="profile", indent=1)

            return publications

        except Exception as e:
            run_log.error(f"Failed to fetch publications: {e}",
                          module="profile", indent=1)
            traceback.print_exc()
            return None

    # ------------------------------------------------------------------
    # I/O helpers
    # ------------------------------------------------------------------

    def fetch_venue_details(self, publications):
        """Fill ``pub['venue_full']`` from each publication's Scholar detail page.

        One request per publication (hundreds, not one per citing entry),
        issued through the patched scholarly session that ``main()`` installs
        *before* this class runs, so the standard waits, ``--accelerate``
        scaling, captcha recovery and proxy prompts all still apply.  Pages
        parsed by an earlier run come from ``output/venue_detail_cache.json``.

        Returns the stats dict, or None when no navigator is available (in which
        case ``venue_full`` simply stays empty — the profile is still written).
        """
        nav = getattr(scholarly, '_Scholarly__nav', None)
        get_soup = getattr(nav, '_get_soup', None)
        if not callable(get_soup):
            run_log.warn("Publication detail pages unavailable (no scholarly "
                         "navigator); leaving venue_full empty",
                         module="venue", indent=1)
            return None
        return enrich_publications_with_venue_full(
            publications,
            cache_path=self.venue_detail_cache,
            get_soup=get_soup,
        )

    def load_prev_profile(self):
        """Load the previous profile from the output JSON file."""
        return AuthorProfile.load(self.profile_json)

    def save_profile(self, profile, print_fn=None):
        """Save an AuthorProfile to JSON and Excel output files."""
        _pn = print_fn or line_logger(INFO, "output")
        profile.save_json(self.profile_json, print_fn=_pn)

        # The Excel file is a convenience view; its failure must not discard a
        # profile whose JSON is already on disk.
        try:
            workbook = write_profile_xlsx(
                self.profile_xlsx,
                profile,
                datetime_module=datetime,
                openpyxl_module=openpyxl,
                font_cls=Font,
                pattern_fill_cls=PatternFill,
                alignment_cls=Alignment,
                print_fn=_pn,
            )
        except Exception as e:
            _pn(f"Warning: could not write {self.profile_xlsx}: "
                f"{type(e).__name__}: {e}")
            self._last_profile_workbook = None
            return None
        self._last_profile_workbook = workbook
        return workbook

    # ------------------------------------------------------------------
    # Main workflow
    # ------------------------------------------------------------------

    def run(self):
        """Main workflow."""
        run_log.section(f"Phase 1 · author profile · {self.author_id}", module="phase")

        prev_profile = self.load_prev_profile()
        if prev_profile:
            prev_time = prev_profile.fetch_time or 'N/A'
            run_log.info(f"Found previous profile ({prev_time}), will compare "
                         f"incrementally", module="profile", indent=1)
        else:
            run_log.info("No previous profile found, this is the first fetch",
                         module="profile", indent=1)

        # Phase 1: Basic info
        basics, basics_fetched = self.fetch_basics()
        if not basics:
            run_log.error("Failed to fetch basic info, exiting",
                          module="profile", indent=1)
            return False

        # Phase 2: Publications
        # Auto-refresh if total citations changed
        force_refresh = False
        if prev_profile is not None:
            old_citedby = prev_profile.total_citations
            new_citedby = basics.get('citedby', 0)
            if new_citedby != old_citedby:
                run_log.info(f"Total citations changed ({old_citedby} -> "
                             f"{new_citedby}), refreshing publications...",
                             module="profile", indent=1)
                force_refresh = True

        publications = self.fetch_publications(
            force_refresh=force_refresh,
            prev_publications=prev_profile.publications if prev_profile else None,
        )
        if publications is None:
            prev_publications = prev_profile.publications if prev_profile else []
            if prev_publications:
                # A failed fetch is not evidence that the author has no papers.
                # Writing it would replace a good publication list with zero
                # papers and the failure would be invisible in the output.
                run_log.warn(f"Failed to fetch publications; keeping the previous "
                             f"{len(prev_publications)} publication(s) from "
                             f"{self.profile_json}", module="profile", indent=1)
                return True
            publications = []

        run_log.info(f"Fetch complete: {len(publications)} publications",
                     module="profile", indent=1)

        # Full venue strings: one detail-page request per publication (cached
        # across runs).  Pure addition — pub['venue'] is left alone.
        run_log.section("Fetching full venue strings (publication detail pages)",
                        module="venue")
        venue_stats = self.fetch_venue_details(publications)
        if venue_stats:
            run_log.info(
                f"venue_full resolved: {venue_stats['resolved']}/{venue_stats['total']} "
                f"(fetched {venue_stats['fetched']}, cached {venue_stats['cache_hits']}, "
                f"no venue field {venue_stats['no_venue_field']}, "
                f"failed {venue_stats['failed']}, "
                f"no token {venue_stats['missing_token']}, "
                f"already complete {venue_stats['skipped_complete']})",
                module="venue", indent=1)

        # Build the new profile
        profile = AuthorProfile(
            author_info=basics,
            publications=publications,
            fetch_time=datetime.now().isoformat(),
            change_history=list(prev_profile.change_history) if prev_profile else [],
        )

        # Incremental comparison + history
        run_log.section("Incremental Update Analysis", module="profile")
        profile.append_history(prev_profile)

        # Save output files
        run_log.section("Saving Output Files", module="output")
        self.save_profile(profile)

        run_log.section("Done", module="summary")
        run_log.info(f"Author: {basics.get('name', 'N/A')}", module="summary")
        run_log.info(f"Total citations: {basics.get('citedby', 0)}", module="summary")
        run_log.info(f"Total publications: {len(publications)}", module="summary")
        run_log.info("Output files:", module="summary")
        run_log.info(f"JSON: {self.profile_json}", module="summary", indent=1)
        run_log.info(f"Excel: {self.profile_xlsx}", module="summary", indent=1)

        return True
