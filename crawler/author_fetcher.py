"""
crawler/author_fetcher.py — AuthorProfileFetcher: fetch and cache author
profile data from Google Scholar.
"""

import json
import os
import time
import traceback
from datetime import datetime

from scholarly import scholarly

from crawler.common import rand_delay, now_str
from crawler.profile_io import (
    build_profile_count_summary,
    build_profile_payload,
    save_profile_json as write_profile_json,
    save_profile_xlsx as write_profile_xlsx,
)
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

class AuthorProfileFetcher:
    def __init__(self, author_id, output_dir=".", delay_scale=1.0):
        self.author_id = author_id
        self.output_dir = output_dir
        self.delay_scale = delay_scale

        # Cache directory
        self.cache_dir = os.path.join(output_dir, "scholar_cache", f"author_{author_id}")
        os.makedirs(self.cache_dir, exist_ok=True)

        # Cache files
        self.basics_cache = os.path.join(self.cache_dir, "basics.json")
        self.pubs_cache = os.path.join(self.cache_dir, "publications.json")

        # Output files
        self.profile_json = os.path.join(output_dir, f"author_{author_id}_profile.json")
        self.profile_xlsx = os.path.join(output_dir, f"author_{author_id}_profile.xlsx")

        print(f"Cache dir: {self.cache_dir}")
        print(f"Output files: author_{author_id}_profile.json / .xlsx")

    def load_basics_cache(self):
        """Load cached basic info."""
        if os.path.exists(self.basics_cache):
            with open(self.basics_cache, 'r', encoding='utf-8') as f:
                data = json.load(f)
            print(f"Loaded basic info from cache (last: {data.get('_cached_at', 'N/A')})")
            return data
        return None

    def save_basics_cache(self, data):
        """Save basic info to cache."""
        data['_cached_at'] = datetime.now().isoformat()
        with open(self.basics_cache, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_pubs_cache(self):
        """Load cached publication list."""
        if os.path.exists(self.pubs_cache):
            with open(self.pubs_cache, 'r', encoding='utf-8') as f:
                data = json.load(f)
            print(f"Loaded publications from cache ({len(data.get('publications', []))} papers, last: {data.get('_cached_at', 'N/A')})")
            return data
        return None

    def save_pubs_cache(self, data):
        """Save publication list to cache."""
        data['_cached_at'] = datetime.now().isoformat()
        with open(self.pubs_cache, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

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
            author = scholarly.search_author_id(self.author_id)
            if author is None:
                raise ValueError("search_author_id returned None — Scholar may be rate-limiting")
            print("Author found, filling basic info...")

            d = rand_delay(self.delay_scale)
            print(f"{now_str()} Waiting {d:.0f}s...")
            time.sleep(d)

            author_filled = scholarly.fill(author, sections=['basics', 'indices', 'counts'])
            if author_filled is None:
                raise ValueError("fill() returned None — Scholar may be rate-limiting")

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

            self.save_basics_cache(basics)
            print("Basic info cached")

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

    def fetch_publications(self, force_refresh=False):
        """
        Phase 2: Fetch all publications (scholarly handles pagination).
        """
        if not force_refresh:
            cached = self.load_pubs_cache()
            if cached:
                return cached.get('publications', [])

        print("\nPhase 2: Fetching all publications (auto-pagination)...")
        print("Connecting to Google Scholar...")

        try:
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
                bib = pub.get('bib', {})
                pub_info = {
                    'no': i,
                    'title': bib.get('title', 'N/A'),
                    'year': str(bib.get('pub_year', 'N/A')),
                    'venue': bib.get('citation', bib.get('venue', 'N/A')),
                    'authors': bib.get('author', 'N/A'),
                    'num_citations': pub.get('num_citations', 0),
                    'url': pub.get('pub_url', pub.get('eprint_url', 'N/A')),
                    'citedby_url': pub.get('citedby_url', ''),
                }
                publications.append(pub_info)

                if i % 20 == 0:
                    print(f"  Processed {i}/{len(raw_pubs)} papers...")

            # Sort by citation count (descending) and renumber
            publications.sort(key=lambda x: x['num_citations'], reverse=True)
            for i, pub in enumerate(publications, 1):
                pub['no'] = i

            pubs_data = {'publications': publications}
            self.save_pubs_cache(pubs_data)
            print(f"Publication list cached ({len(publications)} papers)")

            return publications

        except Exception as e:
            print(f"Failed to fetch publications: {e}")
            traceback.print_exc()
            return []

    def append_history(self, basics, publications, prev_profile=None):
        """Append a history record with change tracking. History is stored in profile.json."""
        history = prev_profile.get('change_history', []) if prev_profile else []

        new_papers = []
        changed_citations = []

        if prev_profile:
            prev_pubs = {p['title']: p['num_citations'] for p in prev_profile.get('publications', [])}
            prev_titles = set(prev_pubs.keys())
            curr_titles = set(p['title'] for p in publications)

            for title in curr_titles - prev_titles:
                new_papers.append(title)

            for pub in publications:
                title = pub['title']
                if title in prev_pubs:
                    old_cite = prev_pubs[title]
                    new_cite = pub['num_citations']
                    if new_cite != old_cite:
                        changed_citations.append({
                            'title': title,
                            'old': old_cite,
                            'new': new_cite,
                        })

        record = {
            'fetch_time': datetime.now().isoformat(),
            'citedby': basics.get('citedby', 0),
            'citedby_this_year': basics.get('citedby_this_year', 0),
            'hindex': basics.get('hindex', 0),
            'i10index': basics.get('i10index', 0),
            'total_publications': len(publications),
            'new_papers': new_papers,
            'changed_citations': changed_citations,
        }

        history.append(record)

        if new_papers:
            print(f"\nNew papers ({len(new_papers)}):")
            for t in new_papers[:5]:
                print(f"  + {t[:70]}")
            if len(new_papers) > 5:
                print(f"  ... {len(new_papers)} total")
        if changed_citations:
            print(f"\nCitation changes ({len(changed_citations)}):")
            for c in changed_citations[:5]:
                print(f"  {c['title'][:50]}... {c['old']} -> {c['new']}")
            if len(changed_citations) > 5:
                print(f"  ... {len(changed_citations)} total")
        if not new_papers and not changed_citations and prev_profile:
            print("  (No changes in this run)")

        return history

    def load_prev_profile(self):
        """Load previous profile for incremental comparison."""
        if os.path.exists(self.profile_json):
            with open(self.profile_json, 'r', encoding='utf-8') as f:
                profile = json.load(f)
            # Migrate: if old history.json exists and profile has no change_history, import it
            if 'change_history' not in profile:
                history_json = os.path.join(self.output_dir, f"author_{self.author_id}_history.json")
                if os.path.exists(history_json):
                    with open(history_json, 'r', encoding='utf-8') as f:
                        profile['change_history'] = json.load(f)
                    print(f"Migrated history from {history_json} into profile")
            return profile
        return None

    def _build_profile_count_summary(self, basics):
        return build_profile_count_summary(basics)

    def save_profile_json(self, basics, publications, change_history=None, fetch_time=None):
        """Save complete profile as JSON."""
        return write_profile_json(
            self.profile_json,
            basics,
            publications,
            change_history=change_history,
            fetch_time=fetch_time,
            datetime_module=datetime,
            print_fn=print,
        )

    def save_profile_xlsx(self, basics, publications, change_history=None, fetch_time=None):
        """Save Excel file with 3 sheets: overview, publications, and history."""
        workbook = write_profile_xlsx(
            self.profile_xlsx,
            basics,
            publications,
            change_history=change_history,
            fetch_time=fetch_time,
            datetime_module=datetime,
            openpyxl_module=openpyxl,
            font_cls=Font,
            pattern_fill_cls=PatternFill,
            alignment_cls=Alignment,
            print_fn=print,
        )
        self._last_profile_workbook = workbook
        return workbook

    def run(self, force_refresh_pubs=False):
        """Main workflow."""
        print("\n" + "=" * 70)
        print("  Google Scholar Author Profile Fetcher")
        print(f"  Author ID: {self.author_id}")
        print("=" * 70)
        print()

        prev_profile = self.load_prev_profile()
        if prev_profile:
            prev_time = prev_profile.get('fetch_time', 'N/A')
            print(f"Found previous profile ({prev_time}), will compare incrementally")
        else:
            print("No previous profile found, this is the first fetch")

        # Phase 1: Basic info
        basics, basics_fetched = self.fetch_basics()
        if not basics:
            print("Failed to fetch basic info, exiting")
            return False

        # Only wait between phases if we actually made network requests
        if basics_fetched:
            d = rand_delay(self.delay_scale)
            print(f"\n{now_str()} Waiting {d:.0f}s before continuing...")
            time.sleep(d)

        # Phase 2: Publications
        # Auto-refresh if total citations changed, or if forced via CLI
        if not force_refresh_pubs and prev_profile:
            old_citedby = prev_profile.get('total_citations', 0)
            new_citedby = basics.get('citedby', 0)
            if new_citedby != old_citedby:
                print(f"\nTotal citations changed ({old_citedby} -> {new_citedby}), refreshing publications...")
                force_refresh_pubs = True

        publications = self.fetch_publications(force_refresh=force_refresh_pubs)

        print(f"\nFetch complete: {len(publications)} publications")

        # Incremental comparison + history
        print("\n" + "=" * 70)
        print("  Incremental Update Analysis")
        print("=" * 70)
        change_history = self.append_history(basics, publications, prev_profile)

        # Save output files
        print("\n" + "=" * 70)
        print("  Saving Output Files")
        print("=" * 70)
        self.save_profile_json(basics, publications, change_history)
        self.save_profile_xlsx(basics, publications, change_history)

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


# ============================================================
# Paper Citation Fetcher
# ============================================================

