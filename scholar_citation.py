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


# ============================================================
# Shared Utilities
# ============================================================

# Unified delay: all deliberate waits use a random value in this range
DELAY_MIN = 30
DELAY_MAX = 60

# Proactively refresh session every N pages to avoid session-based blocking
SESSION_REFRESH_MIN = 10   # refresh session after random 10-20 pages
SESSION_REFRESH_MAX = 20

# Papers with >= this many citations use year-based fetching for better resume
YEAR_BASED_THRESHOLD = 50

# Retry: when Scholar blocks a citation fetch, retry with fresh session
MAX_RETRIES = 3


def rand_delay():
    """Return a random delay between DELAY_MIN and DELAY_MAX seconds."""
    return random.uniform(DELAY_MIN, DELAY_MAX)


def setup_proxy():
    """Configure proxy from environment variables."""
    proxy_url = os.environ.get('https_proxy') or os.environ.get('http_proxy')
    if not proxy_url:
        print("Warning: No proxy detected, connecting directly")
        return
    clean = proxy_url.replace('http://', '').replace('https://', '')
    print(f"Proxy detected: {clean}")
    try:
        pg = ProxyGenerator()
        pg.SingleProxy(http=proxy_url, https=proxy_url)
        scholarly.use_proxy(pg)
        print("Proxy configured successfully")
    except TypeError:
        print("Warning: scholarly proxy API incompatible with current httpx version, using system env proxy")
    except Exception as e:
        print(f"Warning: Proxy config failed ({e}), connecting directly")


def extract_author_id(author_input):
    """Extract author ID from a Google Scholar URL or bare ID string."""
    # Try to extract from URL
    match = re.search(r'user=([^&]+)', author_input)
    if match:
        return match.group(1)
    # Treat as bare ID if it looks like one (alphanumeric + dashes/underscores)
    if re.match(r'^[\w-]+$', author_input):
        return author_input
    raise ValueError(f"Cannot extract author ID from: {author_input}")


# ============================================================
# Author Profile Fetcher
# ============================================================

class AuthorProfileFetcher:
    def __init__(self, author_id, output_dir="."):
        self.author_id = author_id
        self.output_dir = output_dir

        # Cache directory
        self.cache_dir = os.path.join(output_dir, "scholar_cache", f"author_{author_id}")
        os.makedirs(self.cache_dir, exist_ok=True)

        # Cache files
        self.basics_cache = os.path.join(self.cache_dir, "basics.json")
        self.pubs_cache = os.path.join(self.cache_dir, "publications.json")

        # Output files
        self.profile_json = os.path.join(output_dir, f"author_{author_id}_profile.json")
        self.profile_xlsx = os.path.join(output_dir, f"author_{author_id}_profile.xlsx")
        self.history_json = os.path.join(output_dir, f"author_{author_id}_history.json")

        print(f"Cache dir: {self.cache_dir}")
        print(f"Output files: author_{author_id}_profile.json / .xlsx")
        print(f"History file: author_{author_id}_history.json")

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

    def fetch_basics(self, force_refresh=False):
        """
        Phase 1: Fetch author basic info + citation stats.
        force_refresh=True ignores cache and re-fetches.
        Returns (basics_dict, fetched_from_network).
        """
        if not force_refresh:
            cached = self.load_basics_cache()
            if cached:
                return cached, False

        print("\nPhase 1: Fetching author basic info...")
        print(f"  Author ID: {self.author_id}")
        print("Connecting to Google Scholar...")

        try:
            author = scholarly.search_author_id(self.author_id)
            print("Author found, filling basic info...")

            d = rand_delay()
            print(f"Waiting {d:.0f} seconds...")
            time.sleep(d)

            author_filled = scholarly.fill(author, sections=['basics', 'indices', 'counts'])

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

            d = rand_delay()
            print(f"Waiting {d:.0f} seconds...")
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

    def load_history(self):
        """Load change history."""
        if os.path.exists(self.history_json):
            with open(self.history_json, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []

    def append_history(self, basics, publications, prev_profile=None):
        """Append a history record with change tracking."""
        history = self.load_history()

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

        with open(self.history_json, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

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

        return record

    def load_prev_profile(self):
        """Load previous profile for incremental comparison."""
        if os.path.exists(self.profile_json):
            with open(self.profile_json, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None

    def save_profile_json(self, basics, publications):
        """Save complete profile as JSON."""
        profile = {
            'author_info': basics,
            'publications': publications,
            'fetch_time': datetime.now().isoformat(),
            'total_publications': len(publications),
            'total_citations': basics.get('citedby', 0),
        }
        with open(self.profile_json, 'w', encoding='utf-8') as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
        print(f"Saved JSON: {self.profile_json}")
        return profile

    def save_profile_xlsx(self, basics, publications):
        """
        Save Excel file with 3 sheets:
          Sheet1: Author Overview
          Sheet2: Publications (sorted by citation count)
          Sheet3: Change History
        """
        wb = openpyxl.Workbook()

        # ===== Sheet1: Author Overview =====
        ws1 = wb.active
        ws1.title = "Author Overview"

        title_fill = PatternFill(start_color="2F75B6", end_color="2F75B6", fill_type="solid")
        title_font = Font(bold=True, color="FFFFFF", size=13)
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF", size=11)
        label_fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
        label_font = Font(bold=True, size=11)
        center = Alignment(horizontal="center", vertical="center")
        left = Alignment(horizontal="left", vertical="center", wrap_text=True)

        ws1.column_dimensions['A'].width = 22
        ws1.column_dimensions['B'].width = 55

        row = 1
        ws1.merge_cells(f'A{row}:B{row}')
        c = ws1.cell(row=row, column=1, value="Google Scholar Author Overview")
        c.fill = title_fill
        c.font = title_font
        c.alignment = center
        ws1.row_dimensions[row].height = 30
        row += 1

        info_items = [
            ("Name", basics.get('name', 'N/A')),
            ("Affiliation", basics.get('affiliation', 'N/A')),
            ("Research Interests", ', '.join(basics.get('interests', [])) or 'N/A'),
            ("Scholar ID", basics.get('scholar_id', 'N/A')),
            ("Fetch Time", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ]

        for label, value in info_items:
            lc = ws1.cell(row=row, column=1, value=label)
            lc.fill = label_fill
            lc.font = label_font
            lc.alignment = center
            vc = ws1.cell(row=row, column=2, value=value)
            vc.alignment = left
            ws1.row_dimensions[row].height = 22
            row += 1

        row += 1

        ws1.merge_cells(f'A{row}:B{row}')
        c = ws1.cell(row=row, column=1, value="Citation Statistics")
        c.fill = header_fill
        c.font = header_font
        c.alignment = center
        ws1.row_dimensions[row].height = 24
        row += 1

        stats_items = [
            ("Total Citations", basics.get('citedby', 0)),
            (f"Citations This Year ({datetime.now().year})", basics.get('citedby_this_year', 0)),
            ("Citations (5-year)", basics.get('citedby5y', 0)),
            ("h-index", basics.get('hindex', 0)),
            ("h-index (5-year)", basics.get('hindex5y', 0)),
            ("i10-index", basics.get('i10index', 0)),
            ("i10-index (5-year)", basics.get('i10index5y', 0)),
            ("Total Publications", len(publications)),
        ]

        for label, value in stats_items:
            lc = ws1.cell(row=row, column=1, value=label)
            lc.fill = label_fill
            lc.font = label_font
            lc.alignment = center
            vc = ws1.cell(row=row, column=2, value=value)
            vc.alignment = center
            ws1.row_dimensions[row].height = 22
            row += 1

        row += 1

        ws1.merge_cells(f'A{row}:B{row}')
        c = ws1.cell(row=row, column=1, value="Citations Per Year")
        c.fill = header_fill
        c.font = header_font
        c.alignment = center
        ws1.row_dimensions[row].height = 24
        row += 1

        cites_per_year = basics.get('cites_per_year', {})
        sorted_years = sorted(cites_per_year.keys(), reverse=True)
        for year in sorted_years:
            lc = ws1.cell(row=row, column=1, value=str(year))
            lc.alignment = center
            vc = ws1.cell(row=row, column=2, value=cites_per_year[year])
            vc.alignment = center
            ws1.row_dimensions[row].height = 20
            row += 1

        # ===== Sheet2: Publications =====
        ws2 = wb.create_sheet("Publications")
        ws2.column_dimensions['A'].width = 6
        ws2.column_dimensions['B'].width = 55
        ws2.column_dimensions['C'].width = 12
        ws2.column_dimensions['D'].width = 25
        ws2.column_dimensions['E'].width = 12
        ws2.column_dimensions['F'].width = 50

        headers2 = ["No.", "Title", "Year", "Venue", "Citations", "Link"]
        for col, h in enumerate(headers2, 1):
            c = ws2.cell(row=1, column=col, value=h)
            c.fill = header_fill
            c.font = header_font
            c.alignment = center
        ws2.row_dimensions[1].height = 28

        content_align = Alignment(vertical="center", wrap_text=True)

        for pub in publications:
            r = pub['no'] + 1
            ws2.cell(row=r, column=1, value=pub['no']).alignment = center
            ws2.cell(row=r, column=2, value=pub['title']).alignment = content_align
            ws2.cell(row=r, column=3, value=pub['year']).alignment = center
            ws2.cell(row=r, column=4, value=pub['venue']).alignment = content_align
            ws2.cell(row=r, column=5, value=pub['num_citations']).alignment = center

            url = pub.get('url', 'N/A')
            link_cell = ws2.cell(row=r, column=6, value=url)
            if url and url != 'N/A':
                try:
                    link_cell.hyperlink = url
                    link_cell.font = Font(color="0563C1", underline="single")
                except Exception:
                    pass
            link_cell.alignment = content_align
            ws2.row_dimensions[r].height = 40

        # ===== Sheet3: Change History =====
        ws3 = wb.create_sheet("Change History")
        ws3.column_dimensions['A'].width = 22
        ws3.column_dimensions['B'].width = 14
        ws3.column_dimensions['C'].width = 16
        ws3.column_dimensions['D'].width = 12
        ws3.column_dimensions['E'].width = 12
        ws3.column_dimensions['F'].width = 14
        ws3.column_dimensions['G'].width = 12
        ws3.column_dimensions['H'].width = 50

        headers3 = ["Fetch Time", "Total Citations", "Citations This Year", "h-index", "i10-index", "Total Papers", "New Papers", "New Paper Titles (top 3)"]
        for col, h in enumerate(headers3, 1):
            c = ws3.cell(row=1, column=col, value=h)
            c.fill = header_fill
            c.font = header_font
            c.alignment = center
        ws3.row_dimensions[1].height = 28

        history = self.load_history()
        for i, rec in enumerate(history, 2):
            new_titles = '; '.join(rec.get('new_papers', [])[:3])
            if len(rec.get('new_papers', [])) > 3:
                new_titles += f" ... (+{len(rec['new_papers'])-3})"

            ws3.cell(row=i, column=1, value=rec.get('fetch_time', 'N/A')).alignment = center
            ws3.cell(row=i, column=2, value=rec.get('citedby', 0)).alignment = center
            ws3.cell(row=i, column=3, value=rec.get('citedby_this_year', 0)).alignment = center
            ws3.cell(row=i, column=4, value=rec.get('hindex', 0)).alignment = center
            ws3.cell(row=i, column=5, value=rec.get('i10index', 0)).alignment = center
            ws3.cell(row=i, column=6, value=rec.get('total_publications', 0)).alignment = center
            ws3.cell(row=i, column=7, value=len(rec.get('new_papers', []))).alignment = center
            ws3.cell(row=i, column=8, value=new_titles).alignment = Alignment(vertical="center", wrap_text=True)
            ws3.row_dimensions[i].height = 22

        wb.save(self.profile_xlsx)
        print(f"Saved Excel: {self.profile_xlsx}")

    def run(self, force_refresh_basics=False, force_refresh_pubs=False):
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
        basics, basics_fetched = self.fetch_basics(force_refresh=force_refresh_basics)
        if not basics:
            print("Failed to fetch basic info, exiting")
            return False

        # Only wait between phases if we actually made network requests
        if basics_fetched:
            d = rand_delay()
            print(f"\nWaiting {d:.0f} seconds before continuing...")
            time.sleep(d)

        # Phase 2: Publications
        publications = self.fetch_publications(force_refresh=force_refresh_pubs)

        print(f"\nFetch complete: {len(publications)} publications")

        # Incremental comparison + history
        print("\n" + "=" * 70)
        print("  Incremental Update Analysis")
        print("=" * 70)
        self.append_history(basics, publications, prev_profile)

        # Save output files
        print("\n" + "=" * 70)
        print("  Saving Output Files")
        print("=" * 70)
        self.save_profile_json(basics, publications)
        self.save_profile_xlsx(basics, publications)

        print("\n" + "=" * 70)
        print(f"  Done!")
        print(f"  Author: {basics.get('name', 'N/A')}")
        print(f"  Total citations: {basics.get('citedby', 0)}")
        print(f"  Total publications: {len(publications)}")
        print("=" * 70)
        print(f"\nOutput files:")
        print(f"  JSON   : {self.profile_json}")
        print(f"  Excel  : {self.profile_xlsx}")
        print(f"  History: {self.history_json}")
        print()

        return True


# ============================================================
# Paper Citation Fetcher
# ============================================================

class PaperCitationFetcher:
    def __init__(self, author_id, output_dir=".",
                 limit=None, skip=0, save_every=10):
        self.author_id = author_id
        self.output_dir = output_dir
        self.limit = limit
        self.skip = skip
        self.save_every = save_every

        # Paths
        self.cache_dir = os.path.join(output_dir, "scholar_cache", f"author_{author_id}", "citations")
        self.pubs_cache = os.path.join(output_dir, "scholar_cache", f"author_{author_id}", "publications.json")
        self.profile_json = os.path.join(output_dir, f"author_{author_id}_profile.json")
        self.out_json = os.path.join(output_dir, f"author_{author_id}_paper_citations.json")
        self.out_xlsx = os.path.join(output_dir, f"author_{author_id}_paper_citations.xlsx")
        os.makedirs(self.cache_dir, exist_ok=True)

    def _patch_scholarly(self):
        """
        Monkey-patch scholarly internals for rate-limit safety:
        1. Patch _get_page to wait 30-60s before EVERY HTTP request
        2. Track pagination and proactively refresh session every N pages
        3. Log year-segment switches in _citedby_long
        """
        nav = scholarly._Scholarly__nav
        nav._set_retries(1)
        original_get_page = nav._get_page

        # --- Patch 1: _get_page pre-request delay + retry limit ---
        MAX_SLEEPS_PER_PAGE = 3  # max retries within a single _get_page call

        def patched_get_page(pagerequest, premium=False):
            sleep_count = [0]
            original_sleep = time.sleep
            def unified_sleep(seconds):
                sleep_count[0] += 1
                if sleep_count[0] > MAX_SLEEPS_PER_PAGE:
                    time.sleep = original_sleep
                    raise MaxTriesExceededException(
                        f"Too many retries ({sleep_count[0]}) for single page request")
                d = rand_delay()
                print(f"      Waiting {d:.0f}s before request...", flush=True)
                original_sleep(d)
            time.sleep = unified_sleep
            try:
                return original_get_page(pagerequest, premium)
            finally:
                time.sleep = original_sleep

        nav._get_page = patched_get_page

        # --- Patch 2: pagination tracking + session refresh ---
        original_load_url = _SearchScholarIterator._load_url
        original_init = _SearchScholarIterator.__init__
        self._total_page_count = 0
        self._next_refresh_at = random.randint(SESSION_REFRESH_MIN, SESSION_REFRESH_MAX)

        def patched_init(self, nav, url):
            self._page_num = 0
            return original_init(self, nav, url)

        fetcher_self = self  # capture for closure

        def patched_load_url(self_iter, url):
            self_iter._page_num = getattr(self_iter, '_page_num', 0) + 1
            fetcher_self._total_page_count += 1
            if self_iter._page_num > 1:
                print(f"      Pagination (page {self_iter._page_num})", flush=True)
            # Refresh session at randomized intervals (10-20 pages)
            if fetcher_self._total_page_count >= fetcher_self._next_refresh_at:
                print(f"      Refreshing session (after {fetcher_self._total_page_count} pages)...", flush=True)
                fetcher_self._refresh_scholarly_session()
                fetcher_self._next_refresh_at = fetcher_self._total_page_count + random.randint(SESSION_REFRESH_MIN, SESSION_REFRESH_MAX)
            return original_load_url(self_iter, url)

        _SearchScholarIterator.__init__ = patched_init
        _SearchScholarIterator._load_url = patched_load_url

        # --- Patch 3: year-segment tracking in _citedby_long ---
        original_citedby_long = scholarly._citedby_long
        self._current_year_segment = None       # year being processed
        self._completed_year_segments = set()   # years fully fetched

        def patched_citedby_long(obj, years):
            first = True
            for y_hi, y_lo in years:
                if y_lo in fetcher_self._completed_year_segments:
                    print(f"      Skipping completed year {y_lo}", flush=True)
                    continue
                if not first:
                    print(f"      Switching year range {y_lo}-{y_hi}", flush=True)
                first = False
                fetcher_self._current_year_segment = y_lo
                yield from original_citedby_long(obj, [(y_hi, y_lo)])
                fetcher_self._completed_year_segments.add(y_lo)

        scholarly._citedby_long = patched_citedby_long

    @staticmethod
    def _refresh_scholarly_session():
        """Refresh scholarly internal session to clear flagged cookies."""
        nav = scholarly._Scholarly__nav
        nav._new_session(premium=True)
        nav._new_session(premium=False)
        nav.got_403 = False

    def _citation_cache_path(self, title):
        key = hashlib.md5(title.encode('utf-8')).hexdigest()[:16]
        return os.path.join(self.cache_dir, f"{key}.json")

    @staticmethod
    def _extract_citation_info(pub):
        bib = pub.get('bib', {})
        authors = bib.get('author', [])
        return {
            'title':   bib.get('title', 'N/A'),
            'authors': ', '.join(authors) if isinstance(authors, list) else str(authors),
            'venue':   bib.get('venue', 'N/A'),
            'year':    str(bib.get('pub_year', 'N/A')),
            'url':     pub.get('pub_url', pub.get('eprint_url', 'N/A')),
        }

    def _fetch_citations_with_progress(self, citedby_url, cache_path, title,
                                        num_citations, pub_url, pub_year, resume_from,
                                        completed_years=None):
        """
        Stream-fetch citations with periodic progress saves.
        resume_from: previously saved citation list (for resume after interruption).
        completed_years: list of years already fully fetched (for resume).
        """
        citations = list(resume_from)

        # Load completed years into patch state for _citedby_long to skip
        self._completed_year_segments = set(completed_years or [])
        self._current_year_segment = None

        def save_progress(complete):
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump({
                    'title': title,
                    'pub_url': pub_url,
                    'citedby_url': citedby_url,
                    'num_citations_on_scholar': num_citations,
                    'num_citations_cached': len(citations),
                    'complete': complete,
                    'completed_years': sorted(self._completed_year_segments),
                    'fetched_at': datetime.now().isoformat(),
                    'citations': citations,
                }, f, ensure_ascii=False, indent=2)

        # Year-based fetch: for papers with many citations, fetch by year
        # so that completed years are tracked and resume is efficient
        if num_citations >= YEAR_BASED_THRESHOLD:
            return self._fetch_by_year(
                citedby_url, citations, save_progress,
                num_citations, pub_year
            )

        # Simple fetch for small citation counts
        pub_obj = {
            'citedby_url': citedby_url,
            'container_type': 'Publication',
            'num_citations': num_citations,
            'filled': True,
            'source': 'PUBLICATION_SEARCH_SNIPPET',
            'bib': {
                'title': title,
                'pub_year': pub_year,
            },
        }

        try:
            for citing in scholarly.citedby(pub_obj):
                info = self._extract_citation_info(citing)
                citations.append(info)
                count = len(citations)

                print(f"  [{count}] {info['title'][:55]}...", flush=True)

                if count % self.save_every == 0:
                    save_progress(complete=False)
                    print(f"  Progress saved ({count} citations)", flush=True)
        except KeyboardInterrupt:
            save_progress(complete=False)
            raise

        save_progress(complete=True)
        return citations

    def _fetch_by_year(self, citedby_url, citations, save_progress,
                        num_citations, pub_year):
        """
        Fetch citations year-by-year. Skips completed years and uses
        start_index within partially completed years for efficient resume.
        """
        import re as _re
        m = _re.search(r"cites=([\d,]+)", citedby_url)
        if not m:
            raise ValueError(f"Cannot extract publication ID from citedby_url: {citedby_url}")
        pub_id = m.group(1)

        # Build year -> cached count from existing citations
        year_counts = {}
        for c in citations:
            y = c.get('year', 'N/A')
            if y and y != 'N/A' and y != 'NA':
                try:
                    year_counts[int(y)] = year_counts.get(int(y), 0) + 1
                except ValueError:
                    pass

        current_year = datetime.now().year
        try:
            start_year = int(pub_year) if pub_year and pub_year not in ('N/A', '?') else None
        except (ValueError, TypeError):
            start_year = None
        if start_year is None:
            # Fallback: earliest year in cached citations, minus 1
            if year_counts:
                start_year = min(year_counts.keys()) - 1
            else:
                start_year = current_year - 30

        total_years = current_year - start_year + 1
        skipped_years = 0

        print(f"  Year-based resume: {start_year}-{current_year} "
              f"({len(self._completed_year_segments)} years already done)", flush=True)

        try:
            for year in range(current_year, start_year - 1, -1):
                if year in self._completed_year_segments:
                    skipped_years += 1
                    continue

                cached_for_year = year_counts.get(year, 0)
                start_index = cached_for_year

                # Refresh session on each year switch
                self._refresh_scholarly_session()
                self._next_refresh_at = self._total_page_count + random.randint(SESSION_REFRESH_MIN, SESSION_REFRESH_MAX)

                if start_index > 0:
                    print(f"      Year {year}: resuming from position {start_index}", flush=True)
                else:
                    print(f"      Year {year}: fetching", flush=True)

                year_new_count = 0
                for citing in scholarly.search_citedby(pub_id,
                                                       year_low=year, year_high=year,
                                                       start_index=start_index):
                    info = self._extract_citation_info(citing)
                    citations.append(info)
                    year_new_count += 1
                    count = len(citations)

                    print(f"  [{count}] {info['title'][:55]}...", flush=True)

                    if count % self.save_every == 0:
                        save_progress(complete=False)
                        print(f"  Progress saved ({count} citations)", flush=True)

                self._completed_year_segments.add(year)
                if year_new_count > 0 or cached_for_year > 0:
                    print(f"      Year {year} done: {cached_for_year + year_new_count} citations "
                          f"({year_new_count} new)", flush=True)
                # Save after each completed year
                save_progress(complete=False)

        except KeyboardInterrupt:
            save_progress(complete=False)
            raise

        save_progress(complete=True)
        return citations

    def _save_xlsx(self, results):
        wb = openpyxl.Workbook()

        hdr_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        hdr_font = Font(bold=True, color="FFFFFF", size=11)
        center   = Alignment(horizontal="center", vertical="center")
        wrap     = Alignment(vertical="center", wrap_text=True)

        # Sheet1: Summary
        ws1 = wb.active
        ws1.title = "Summary"
        for col, (w, h) in enumerate(zip(
            [6, 55, 12, 25, 16, 16],
            ["No.", "Title", "Year", "Venue", "Citations (Scholar)", "Citations Collected"]
        ), 1):
            ws1.column_dimensions[chr(64 + col)].width = w
            c = ws1.cell(row=1, column=col, value=h)
            c.fill, c.font, c.alignment = hdr_fill, hdr_font, center
        ws1.row_dimensions[1].height = 28

        for i, item in enumerate(results, 2):
            pub = item['pub']
            ws1.cell(row=i, column=1, value=pub['no']).alignment = center
            ws1.cell(row=i, column=2, value=pub['title']).alignment = wrap
            ws1.cell(row=i, column=3, value=pub.get('year', 'N/A')).alignment = center
            ws1.cell(row=i, column=4, value=pub.get('venue', 'N/A')).alignment = wrap
            ws1.cell(row=i, column=5, value=pub['num_citations']).alignment = center
            ws1.cell(row=i, column=6, value=len(item['citations'])).alignment = center
            ws1.row_dimensions[i].height = 36

        # Sheet2: All Citations
        ws2 = wb.create_sheet("All Citations")
        for col, (w, h) in enumerate(zip(
            [45, 50, 35, 25, 10, 55],
            ["Cited Paper", "Citing Paper Title", "Authors", "Venue", "Year", "Link"]
        ), 1):
            ws2.column_dimensions[chr(64 + col)].width = w
            c = ws2.cell(row=1, column=col, value=h)
            c.fill, c.font, c.alignment = hdr_fill, hdr_font, center
        ws2.row_dimensions[1].height = 28

        row = 2
        for item in results:
            for cite in item['citations']:
                ws2.cell(row=row, column=1, value=item['pub']['title']).alignment = wrap
                ws2.cell(row=row, column=2, value=cite['title']).alignment = wrap
                ws2.cell(row=row, column=3, value=cite['authors']).alignment = wrap
                ws2.cell(row=row, column=4, value=cite['venue']).alignment = wrap
                ws2.cell(row=row, column=5, value=cite['year']).alignment = center
                url = cite['url']
                lc = ws2.cell(row=row, column=6, value=url)
                if url and url != 'N/A':
                    try:
                        lc.hyperlink = url
                        lc.font = Font(color="0563C1", underline="single")
                    except Exception:
                        pass
                lc.alignment = wrap
                ws2.row_dimensions[row].height = 32
                row += 1

        wb.save(self.out_xlsx)

    def _load_citation_cache(self, title):
        """Load citation cache for a paper by title."""
        path = self._citation_cache_path(title)
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None

    def _citation_status(self, pub):
        """Return cache status for a publication: 'skip_zero' | 'complete' | 'partial' | 'missing'."""
        if pub['num_citations'] == 0:
            return 'skip_zero'
        cached = self._load_citation_cache(pub['title'])
        if not cached:
            return 'missing'
        if cached.get('complete') and cached.get('num_citations_on_scholar', cached.get('num_citations_cached')) == pub['num_citations']:
            return 'complete'
        return 'partial'

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

        self._patch_scholarly()

        # Load profile
        if not os.path.exists(self.profile_json):
            print(f"Error: {self.profile_json} not found. Profile must be fetched first.")
            return False
        with open(self.profile_json, 'r', encoding='utf-8') as f:
            profile = json.load(f)
        publications = profile.get('publications', [])

        # Load citedby_url mapping from publications cache
        if not os.path.exists(self.pubs_cache):
            print(f"Error: {self.pubs_cache} not found. Profile must be fetched first.")
            return False
        with open(self.pubs_cache, 'r', encoding='utf-8') as f:
            pubs_data = json.load(f)
        url_map = {p['title']: {
            'citedby_url': p.get('citedby_url', ''),
            'pub_url':     p.get('url', 'N/A'),
        } for p in pubs_data.get('publications', [])}

        def cache_status(pub):
            st = self._citation_status(pub)
            cached = self._load_citation_cache(pub['title']) if st != 'skip_zero' else None
            return st, cached

        statuses = [cache_status(p) for p in publications]
        need_fetch = [(pub, st, cached) for pub, (st, cached) in zip(publications, statuses)
                      if st in ('missing', 'partial')]

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

        for idx, pub in enumerate(publications, 1):
            title        = pub['title']
            num_citations = pub['num_citations']
            st, cached   = cache_status(pub)

            if st == 'skip_zero':
                print(f"[{idx}/{len(publications)}] {title[:55]}... -> skip (0 citations)")
                results.append({'pub': pub, 'citations': []})
                continue

            if st == 'complete':
                print(f"[{idx}/{len(publications)}] {title[:55]}... -> cached ({len(cached['citations'])} citations)")
                results.append({'pub': pub, 'citations': cached['citations']})
                continue

            need_fetch_idx = next(
                (i for i, (p, _, _) in enumerate(need_fetch) if p['title'] == title), -1
            )

            if need_fetch_idx < self.skip:
                citations = cached['citations'] if cached else []
                print(f"[{idx}/{len(publications)}] {title[:55]}... -> skip (--skip {need_fetch_idx+1}/{self.skip})")
                results.append({'pub': pub, 'citations': citations})
                continue

            if self.limit and fetch_idx >= self.limit:
                citations = cached['citations'] if cached else []
                results.append({'pub': pub, 'citations': citations})
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
                results.append({'pub': pub, 'citations': cached['citations'] if cached else []})
                continue

            if st == 'partial' and cached.get('num_citations_on_scholar', cached.get('num_citations_cached')) == num_citations:
                resume_from = cached.get('citations', [])
                completed_years = cached.get('completed_years', [])
                action = f"resume ({len(resume_from)} cached, fetching remaining)"
            else:
                resume_from = []
                completed_years = []
                old = cached.get('num_citations_on_scholar', cached.get('num_citations_cached', 0)) if cached else 0
                action = f"re-fetch (citations {old} -> {num_citations})" if cached else "first fetch"

            print(f"[{idx}/{len(publications)}] {title[:55]}...")
            print(f"  {action}")

            citations = None
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    self._refresh_scholarly_session()
                    self._next_refresh_at = self._total_page_count + random.randint(SESSION_REFRESH_MIN, SESSION_REFRESH_MAX)
                    if attempt > 1:
                        # Reload cache in case previous attempt saved partial progress
                        latest_cache = self._load_citation_cache(title)
                        if latest_cache and latest_cache.get('num_citations_on_scholar', latest_cache.get('num_citations_cached')) == num_citations:
                            resume_from = latest_cache.get('citations', [])
                            completed_years = latest_cache.get('completed_years', [])
                            print(f"  Retrying with {len(resume_from)} cached citations from previous attempt")
                    citations = self._fetch_citations_with_progress(
                        citedby_url, cache_path, title, num_citations,
                        pub_url, pub.get('year', 'N/A'), resume_from,
                        completed_years=completed_years
                    )
                    print(f"  Done: {len(citations)} citations cached")
                    break
                except Exception as e:
                    print(f"  Error (attempt {attempt}/{MAX_RETRIES}, total pages fetched: {self._total_page_count}): {e}")
                    if attempt >= MAX_RETRIES:
                        # Final attempt failed — print time and terminate
                        traceback.print_exc()
                        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        print(f"\n  All retry attempts exhausted at {now}. Terminating.", flush=True)
                        # Save whatever we have before exiting
                        self._save_output(results)
                        sys.exit(1)
                    elif attempt == MAX_RETRIES - 1:
                        # Second failure — save progress, then wait 6 hours
                        if os.path.exists(cache_path):
                            with open(cache_path, 'r', encoding='utf-8') as f:
                                latest = json.load(f)
                            saved_count = len(latest.get('citations', []))
                            print(f"  Saved progress ({saved_count} citations)")
                        wait_hours = 6
                        print(f"  Will retry with fresh session after {wait_hours} hours...", flush=True)
                        time.sleep(wait_hours * 3600)
                    else:
                        # First failure — wait 3 hours
                        wait_hours = 3
                        print(f"  Will retry with fresh session after {wait_hours} hours...", flush=True)
                        time.sleep(wait_hours * 3600)

            results.append({'pub': pub, 'citations': citations})

            if fetch_idx < (self.limit or len(need_fetch)):
                d = rand_delay()
                print(f"  Waiting {d:.0f}s...", flush=True)
                time.sleep(d)

        # Save output
        self._save_output(results)

        return True

    def _save_output(self, results):
        """Save citation results to JSON and Excel."""
        print("\n" + "=" * 70)
        total_cites = sum(len(r['citations']) for r in results)
        with open(self.out_json, 'w', encoding='utf-8') as f:
            json.dump({
                'author_id': self.author_id,
                'fetch_time': datetime.now().isoformat(),
                'total_papers': len(results),
                'total_citations_collected': total_cites,
                'papers': results,
            }, f, ensure_ascii=False, indent=2)
        print(f"Saved JSON : {self.out_json}")

        self._save_xlsx(results)
        print(f"Saved Excel: {self.out_xlsx}")
        print(f"\nDone! {len(results)} papers, {total_cites} total citation records\n")

        return True


# ============================================================
# CLI Entry Point
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description='Google Scholar Citation Crawler - fetch author profiles and paper citations'
    )
    parser.add_argument('--author', required=True,
                        help='Google Scholar author ID or full profile URL')
    parser.add_argument('--output-dir', default='./output',
                        help='Output directory (default: ./output)')
    parser.add_argument('--limit', type=int, default=None,
                        help='Only process first N papers needing fetch')
    parser.add_argument('--skip', type=int, default=0,
                        help='Skip first N papers in fetch list')
    parser.add_argument('--force-refresh-basics', action='store_true',
                        help='Re-fetch author basics ignoring cache')
    parser.add_argument('--force-refresh-pubs', action='store_true',
                        help='Re-fetch publications ignoring cache')
    return parser.parse_args()


def main():
    args = parse_args()
    setup_proxy()

    author_id = extract_author_id(args.author)
    print(f"Author ID: {author_id}")

    os.makedirs(args.output_dir, exist_ok=True)

    # Always run profile first
    fetcher = AuthorProfileFetcher(author_id, args.output_dir)
    prev_profile = fetcher.load_prev_profile()
    success = fetcher.run(
        force_refresh_basics=args.force_refresh_basics,
        force_refresh_pubs=args.force_refresh_pubs,
    )
    if not success:
        sys.exit(1)

    # Check if citations or publication count changed since last run
    curr_profile = fetcher.load_prev_profile()  # just saved
    if prev_profile and curr_profile:
        prev_citations = prev_profile.get('total_citations', prev_profile.get('author_info', {}).get('citedby', -1))
        curr_citations = curr_profile.get('total_citations', curr_profile.get('author_info', {}).get('citedby', -2))
        prev_pubs = prev_profile.get('total_publications', -1)
        curr_pubs = curr_profile.get('total_publications', -2)

        if prev_citations == curr_citations and prev_pubs == curr_pubs:
            # Even if totals haven't changed, check if all citations are fully cached
            citation_fetcher = PaperCitationFetcher(
                author_id=author_id,
                output_dir=args.output_dir,
                limit=args.limit,
                skip=args.skip,
            )
            if not citation_fetcher.has_pending_work():
                print("\n" + "=" * 70)
                print(f"  No changes detected (citations: {curr_citations}, publications: {curr_pubs})")
                print("  All citation caches are complete. Skipping citation fetch.")
                print("=" * 70 + "\n")
                return
            else:
                print("\nNo changes in totals, but some citations are incomplete. Continuing fetch...")

    # Run citation fetcher
    citation_fetcher = PaperCitationFetcher(
        author_id=author_id,
        output_dir=args.output_dir,
        limit=args.limit,
        skip=args.skip,
    )
    success = citation_fetcher.run()
    if not success:
        sys.exit(1)


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
