"""
crawler/author_fetcher.py — AuthorProfileFetcher: fetch author profile data
from Google Scholar and persist as author_<ID>_profile.json / .xlsx.
"""

import os
import time
import traceback
from datetime import datetime

from scholarly import scholarly

from crawler.common import rand_delay, now_str
from crawler.pub_info import PubInfo
from crawler.profile_io import (
    AuthorProfile,
    build_profile_count_summary,
    save_profile_xlsx as write_profile_xlsx,
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

        print(f"Output files: author_{author_id}_profile.json / .xlsx")

    # ------------------------------------------------------------------
    # Phase 1
    # ------------------------------------------------------------------

    def fetch_basics(self):
        """
        Phase 1: Fetch author basic info + citation stats.
        Always fetches from network (only 1 request) to detect citation changes.
        Returns (basics_dict, fetched_from_network).
        """
        print("\nPhase 1: Fetching author basic info...")
        print(f"  Author ID: {self.author_id}")
        print("Connecting to Google Scholar...")

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
            print("Author found.")
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

            print(f"\nAuthor: {basics['name']}")
            print(f"  Affiliation: {basics['affiliation']}")
            print(f"  Total citations: {basics['citedby']}")
            print(f"  Citations this year: {basics['citedby_this_year']}")
            print(f"  h-index: {basics['hindex']}")
            print(f"  i10-index: {basics['i10index']}")

            return basics, True

        except (AttributeError, TypeError) as e:
            print(f"Failed to fetch basic info: network issue or Scholar rate-limiting "
                  f"(got unexpected None in response: {e})")
            print("Please check your network connection or proxy and try again.")
            return None, False
        except Exception as e:
            print(f"Failed to fetch basic info: {e}")
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
        """
        if not force_refresh:
            if prev_publications:
                return prev_publications

        print("\nPhase 2: Fetching all publications (auto-pagination)...")
        print("Connecting to Google Scholar...")

        try:
            # Reuse the stub from fetch_basics if available, avoiding a redundant
            # search_author_id request to the same profile URL.
            author = getattr(self, '_author_stub', None)
            if author is None:
                author = scholarly.search_author_id(self.author_id)

            d = rand_delay(self.delay_scale)
            print(f"{now_str()} Waiting {d:.0f}s...")
            time.sleep(d)

            print("Fetching full publication list (may take several minutes)...")
            author_with_pubs = scholarly.fill(author, sections=['publications'])

            raw_pubs = author_with_pubs.get('publications', [])
            print(f"Found {len(raw_pubs)} publications")

            publications = []
            for i, pub in enumerate(raw_pubs, 1):
                publications.append(PubInfo.from_scholarly(pub, i).to_dict())

                if i % 20 == 0:
                    print(f"  Processed {i}/{len(raw_pubs)} papers...")

            # Sort by citation count (descending) and renumber
            publications.sort(key=lambda x: x['num_citations'], reverse=True)
            for i, pub in enumerate(publications, 1):
                pub['no'] = i

            print(f"Publication list fetched ({len(publications)} papers)")

            return publications

        except Exception as e:
            print(f"Failed to fetch publications: {e}")
            traceback.print_exc()
            return []

    # ------------------------------------------------------------------
    # I/O helpers
    # ------------------------------------------------------------------

    def load_prev_profile(self):
        """Load the previous profile from the output JSON file."""
        return AuthorProfile.load(self.profile_json)

    def save_profile(self, profile, print_fn=None):
        """Save an AuthorProfile to JSON and Excel output files."""
        _pn = print_fn or print
        profile.save_json(self.profile_json, print_fn=_pn)

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
        self._last_profile_workbook = workbook
        return workbook

    # ------------------------------------------------------------------
    # Main workflow
    # ------------------------------------------------------------------

    def run(self):
        """Main workflow."""
        print("\n" + "=" * 70)
        print("  Google Scholar Author Profile Fetcher")
        print(f"  Author ID: {self.author_id}")
        print("=" * 70)
        print()

        prev_profile = self.load_prev_profile()
        if prev_profile:
            prev_time = prev_profile.fetch_time or 'N/A'
            print(f"Found previous profile ({prev_time}), will compare incrementally")
        else:
            print("No previous profile found, this is the first fetch")

        # Phase 1: Basic info
        basics, basics_fetched = self.fetch_basics()
        if not basics:
            print("Failed to fetch basic info, exiting")
            return False

        # Phase 2: Publications
        # Auto-refresh if total citations changed
        force_refresh = False
        if prev_profile is not None:
            old_citedby = prev_profile.total_citations
            new_citedby = basics.get('citedby', 0)
            if new_citedby != old_citedby:
                print(f"\nTotal citations changed ({old_citedby} -> {new_citedby}), refreshing publications...")
                force_refresh = True

        publications = self.fetch_publications(
            force_refresh=force_refresh,
            prev_publications=prev_profile.publications if prev_profile else None,
        )

        print(f"\nFetch complete: {len(publications)} publications")

        # Build the new profile
        profile = AuthorProfile(
            author_info=basics,
            publications=publications,
            fetch_time=datetime.now().isoformat(),
            change_history=list(prev_profile.change_history) if prev_profile else [],
        )

        # Incremental comparison + history
        print("\n" + "=" * 70)
        print("  Incremental Update Analysis")
        print("=" * 70)
        profile.append_history(prev_profile)

        # Save output files
        print("\n" + "=" * 70)
        print("  Saving Output Files")
        print("=" * 70)
        self.save_profile(profile)

        print("\n" + "=" * 70)
        print(f"  Done!")
        print(f"  Author: {basics.get('name', 'N/A')}")
        print(f"  Total citations: {basics.get('citedby', 0)}")
        print(f"  Total publications: {len(publications)}")
        print("=" * 70)
        print(f"\nOutput files:")
        print(f"  JSON   : {self.profile_json}")
        print(f"  Excel  : {self.profile_xlsx}")
        print()

        return True
