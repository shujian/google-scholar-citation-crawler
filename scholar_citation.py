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
DELAY_MIN = 45
DELAY_MAX = 90

# Proactively refresh session every N pages to avoid session-based blocking
SESSION_REFRESH_MIN = 10   # refresh session after random 10-20 pages
SESSION_REFRESH_MAX = 20

# Mandatory long break every 8-12 pages to let Scholar's rate-limit window reset
MANDATORY_BREAK_EVERY_MIN = 8
MANDATORY_BREAK_EVERY_MAX = 12
MANDATORY_BREAK_MIN = 180  # 3 minutes
MANDATORY_BREAK_MAX = 360  # 6 minutes

# Papers with >= this many citations use year-based fetching for better resume
YEAR_BASED_THRESHOLD = 50

# Retry: when Scholar blocks a citation fetch, retry with fresh session
MAX_RETRIES = 3


def rand_delay():
    """Return a random delay between DELAY_MIN and DELAY_MAX seconds."""
    return random.uniform(DELAY_MIN, DELAY_MAX)


def setup_proxy():
    """Configure proxy from environment variables.

    scholarly's proxy API (ProxyGenerator/use_proxy) is NOT used: it passes
    proxies in {'http': url} format which httpx 0.27.x doesn't recognise,
    causing requests to go out without a proxy.  Instead we rely on httpx
    picking up HTTPS_PROXY / HTTP_PROXY automatically (trust_env=True default).
    """
    proxy_url = os.environ.get('https_proxy') or os.environ.get('http_proxy')
    if not proxy_url:
        print("Warning: No proxy detected, connecting directly")
        return
    clean = proxy_url.replace('http://', '').replace('https://', '')
    print(f"Proxy detected: {clean} (using system env proxy)")


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

            d = rand_delay()
            print(f"Waiting {d:.0f} seconds...")
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

    def save_profile_json(self, basics, publications, change_history=None):
        """Save complete profile as JSON."""
        profile = {
            'author_info': basics,
            'publications': publications,
            'fetch_time': datetime.now().isoformat(),
            'total_publications': len(publications),
            'total_citations': basics.get('citedby', 0),
            'change_history': change_history or [],
        }
        with open(self.profile_json, 'w', encoding='utf-8') as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
        print(f"Saved JSON: {self.profile_json}")
        return profile

    def save_profile_xlsx(self, basics, publications, change_history=None):
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

        history = change_history or []
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
            d = rand_delay()
            print(f"\nWaiting {d:.0f} seconds before continuing...")
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

class PaperCitationFetcher:
    def __init__(self, author_id, output_dir=".",
                 limit=None, skip=0, save_every=10,
                 force_refresh_citations=False,
                 interactive_captcha=False):
        self.author_id = author_id
        self.output_dir = output_dir
        self.limit = limit
        self.skip = skip
        self.save_every = save_every
        self.force_refresh_citations = force_refresh_citations
        self.interactive_captcha = interactive_captcha

        # Paths
        self.cache_dir = os.path.join(output_dir, "scholar_cache", f"author_{author_id}", "citations")
        self.pubs_cache = os.path.join(output_dir, "scholar_cache", f"author_{author_id}", "publications.json")
        self.profile_json = os.path.join(output_dir, f"author_{author_id}_profile.json")
        self.out_json = os.path.join(output_dir, f"author_{author_id}_paper_citations.json")
        self.out_xlsx = os.path.join(output_dir, f"author_{author_id}_paper_citations.xlsx")
        os.makedirs(self.cache_dir, exist_ok=True)

    def _patch_scholarly(self):
        """
        Monkey-patch scholarly internals for browser simulation + rate-limit safety:
        1. Apply browser-like headers (sec-fetch-*, sec-ch-ua, referer) to httpx sessions
        2. Patch _new_session to re-apply browser headers after session recreation (e.g. on 403)
        3. Patch _get_page to wait 45-90s before every HTTP request
        4. Track pagination, update Referer dynamically, take mandatory long breaks
        5. Log year-segment switches in _citedby_long
        """
        nav = scholarly._Scholarly__nav
        nav._set_retries(1)
        original_get_page = nav._get_page
        fetcher_self = self  # capture for all closures below

        # --- Browser headers + HTTP/2: match curl.txt reference request exactly ---
        _BROWSER_HEADERS = {
            'user-agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                           'AppleWebKit/537.36 (KHTML, like Gecko) '
                           'Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0'),
            'accept': ('text/html,application/xhtml+xml,application/xml;q=0.9,'
                       'image/avif,image/webp,image/apng,*/*;q=0.8,'
                       'application/signed-exchange;v=b3;q=0.7'),
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'priority': 'u=0, i',
            'sec-ch-ua': '"Not:A-Brand";v="99", "Microsoft Edge";v="145", "Chromium";v="145"',
            'sec-ch-ua-arch': '"arm"',
            'sec-ch-ua-bitness': '"64"',
            'sec-ch-ua-full-version-list': ('"Not:A-Brand";v="99.0.0.0", '
                                            '"Microsoft Edge";v="145.0.3800.97", '
                                            '"Chromium";v="145.0.7632.160"'),
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-model': '""',
            'sec-ch-ua-platform': '"macOS"',
            'sec-ch-ua-platform-version': '"15.7.4"',
            'sec-ch-ua-wow64': '?0',
            'sec-fetch-dest': 'document',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'same-origin',
            'sec-fetch-user': '?1',
            'upgrade-insecure-requests': '1',
        }
        _PROFILE_URL = f'https://scholar.google.com/citations?user={self.author_id}&hl=en'
        self._last_scholar_url = _PROFILE_URL  # tracks current page for Referer

        def _make_http2_session():
            """Create an httpx.Client with HTTP/2 enabled.
            trust_env=True (httpx default) picks up HTTPS_PROXY automatically.
            """
            return __import__('httpx').Client(http2=True)

        def _apply_browser_headers(session):
            session.headers.update(_BROWSER_HEADERS)
            session.headers['referer'] = fetcher_self._last_scholar_url

        def _full_session_setup(session):
            """Apply browser headers + injected cookies to a session."""
            _apply_browser_headers(session)
            for k, v in fetcher_self._injected_cookies.items():
                session.cookies.set(k, v)   # no domain = sent to all requests

        # Replace scholarly's default HTTP/1.1 sessions with HTTP/2 ones
        nav._session1 = _make_http2_session()
        nav._session2 = _make_http2_session()
        for session in (nav._session1, nav._session2):
            _apply_browser_headers(session)
        print(f"  Browser headers applied (HTTP/2, Edge/145, sec-ch-ua-*, referer)", flush=True)

        # Patch _new_session: on 403 scholarly recreates the httpx client;
        # replace it with an HTTP/2 session and re-apply full browser identity.
        original_new_session = nav._new_session
        fetcher_self._injected_cookies = {}   # populated by _inject_cookies_from_curl
        def patched_new_session(premium=True, **kwargs):
            original_new_session(premium=premium, **kwargs)   # lets scholarly reset got_403
            if premium:
                nav._session1 = _make_http2_session()
            else:
                nav._session2 = _make_http2_session()
            for session in (nav._session1, nav._session2):
                _full_session_setup(session)
        nav._new_session = patched_new_session

        # Patch pm._handle_captcha2: scholarly calls this when it detects a captcha
        # page in the response (200 OK but HTML contains captcha).  The returned
        # session is used for the immediate retry *within the same _get_page call*,
        # bypassing nav._session1/2.  Without this patch the retry uses a plain
        # HTTP/1.1 session with no browser headers or injected cookies, making
        # cookie injection completely ineffective against captcha blocks.
        def patched_handle_captcha2(pagerequest):
            new_session = _make_http2_session()
            _full_session_setup(new_session)
            # Also update nav sessions so subsequent _get_page calls use the same
            nav._session1 = new_session
            nav._session2 = new_session
            return new_session
        nav.pm1._handle_captcha2 = patched_handle_captcha2
        nav.pm2._handle_captcha2 = patched_handle_captcha2

        # --- Patch 1: _get_page pre-request delay + retry limit ---
        # Allow exactly 1 sleep (the intentional 45-90s wait before the request).
        # Any further sleep from scholarly's internal retry logic raises immediately
        # so we fail fast and let the paper-level retry handle recovery.
        MAX_SLEEPS_PER_PAGE = 1

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
                retry_note = f" (retry {sleep_count[0]})" if sleep_count[0] > 1 else ""
                print(f"      Waiting {d:.0f}s before request{retry_note}... "
                      f"[{fetcher_self._wait_status()}]", flush=True)
                original_sleep(d)
            time.sleep = unified_sleep
            try:
                return original_get_page(pagerequest, premium)
            finally:
                time.sleep = original_sleep

        nav._get_page = patched_get_page

        # --- Patch 2: pagination tracking + dynamic Referer + mandatory break ---
        original_load_url = _SearchScholarIterator._load_url
        original_init = _SearchScholarIterator.__init__
        self._total_page_count = 0
        self._next_break_at = random.randint(MANDATORY_BREAK_EVERY_MIN, MANDATORY_BREAK_EVERY_MAX)
        self._next_refresh_at = self._next_break_at + random.randint(SESSION_REFRESH_MIN, SESSION_REFRESH_MAX)

        def patched_init(self, nav, url):
            self._page_num = 0
            return original_init(self, nav, url)

        def patched_load_url(self_iter, url):
            self_iter._page_num = getattr(self_iter, '_page_num', 0) + 1
            fetcher_self._total_page_count += 1
            if self_iter._page_num > 1:
                print(f"      Pagination (page {self_iter._page_num})", flush=True)

            # Set Referer = previous Scholar page (mimics browser navigation chain)
            for session in (nav._session1, nav._session2):
                session.headers['referer'] = fetcher_self._last_scholar_url

            # Mandatory long break (higher priority than soft refresh)
            # Resets Scholar's sliding-window rate limit
            if fetcher_self._total_page_count >= fetcher_self._next_break_at:
                d = random.uniform(MANDATORY_BREAK_MIN, MANDATORY_BREAK_MAX)
                print(f"      Mandatory break after {fetcher_self._total_page_count} pages "
                      f"({d/60:.1f} min)... [{fetcher_self._wait_status()}]", flush=True)
                time.sleep(d)
                fetcher_self._next_break_at = (fetcher_self._total_page_count
                                               + random.randint(MANDATORY_BREAK_EVERY_MIN, MANDATORY_BREAK_EVERY_MAX))
                print(f"      Refreshing session after break...", flush=True)
                fetcher_self._refresh_scholarly_session()
                fetcher_self._next_refresh_at = (fetcher_self._total_page_count
                                                 + random.randint(SESSION_REFRESH_MIN, SESSION_REFRESH_MAX))
            # Soft session refresh between breaks
            elif fetcher_self._total_page_count >= fetcher_self._next_refresh_at:
                print(f"      Refreshing session (after {fetcher_self._total_page_count} pages)...", flush=True)
                fetcher_self._refresh_scholarly_session()
                fetcher_self._next_refresh_at = (fetcher_self._total_page_count
                                                 + random.randint(SESSION_REFRESH_MIN, SESSION_REFRESH_MAX))

            # Record the URL we are about to load so _try_interactive_captcha
            # can show it even if this request fails before _last_scholar_url
            # is updated (e.g. captcha on the very first citation page).
            fetcher_self._current_attempt_url = (
                f'https://scholar.google.com{url}' if url.startswith('/') else url)

            result = original_load_url(self_iter, url)

            # Only update _last_scholar_url (used as Referer) on success
            fetcher_self._last_scholar_url = fetcher_self._current_attempt_url

            return result

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
        """Soft session reset: only clear the 403 flag, preserve the httpx session
        and its accumulated cookies.  Creating a new httpx client would discard
        cookies that Scholar uses to recognise returning (legitimate) users.
        Browser headers are maintained via the patched _new_session; if scholarly
        internally creates a new session on a real 403, headers are re-applied there.
        """
        nav = scholarly._Scholarly__nav
        nav.got_403 = False
        print("      (Session reset: got_403 cleared, cookies preserved)", flush=True)

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
                f"{self._total_page_count} pages")

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
                                        completed_years=None, prev_scholar_count=0):
        """
        Stream-fetch citations with periodic progress saves.
        resume_from: previously saved citation list (for resume after interruption).
        completed_years: list of years already fully fetched (for resume).
        prev_scholar_count: Scholar citation count from last completed scan (for early stop).
        """
        citations = list(resume_from)
        self._dedup_count = 0  # duplicates encountered during this paper's fetch

        # Load completed years into patch state for _citedby_long to skip
        self._completed_year_segments = set(completed_years or [])
        self._current_year_segment = None

        # Note: completed_years are preserved. When Scholar count increases,
        # new citations are typically in recent years, so we only need to
        # re-check years not marked as completed.

        def save_progress(complete):
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump({
                    'title': title,
                    'pub_url': pub_url,
                    'citedby_url': citedby_url,
                    'num_citations_on_scholar': num_citations,
                    'num_citations_cached': len(citations),
                    'num_citations_seen': len(citations) + self._dedup_count,
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
                num_citations, pub_year, prev_scholar_count
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

        # Build dedup map from existing citations: key -> brief info for logging
        cached_titles = {c.get('title', '').strip().lower() for c in citations}
        seen_titles = {k: f"{k[:50]} (cached)" for k in cached_titles}
        # new_seen_titles: keys seen for the first time in THIS fetch (not from cache)
        # _dedup_count only counts duplicates within Scholar's own results, not cache hits

        try:
            for citing in scholarly.citedby(pub_obj):
                info = self._extract_citation_info(citing)
                dedup_key = info['title'].strip().lower()
                if dedup_key in seen_titles:
                    if dedup_key not in cached_titles:
                        # Duplicate within Scholar's own results (not a cache hit)
                        self._dedup_count += 1
                    print(f"  [dedup] Skipping duplicate: {info['title'][:50]}... ({info.get('year', '?')})"
                          f"\n          Existing: {seen_titles[dedup_key]}", flush=True)
                    continue
                seen_titles[dedup_key] = f"{info['title'][:50]} ({info.get('year', '?')})"
                citations.append(info)
                self._new_citations_count += 1
                count = len(citations)

                print(f"  [{count}] {info['title'][:55]}...", flush=True)

                if count % self.save_every == 0:
                    save_progress(complete=False)
                    print(f"  Progress saved ({count} citations, {self._new_citations_count} new in this run)", flush=True)
        except KeyboardInterrupt:
            save_progress(complete=False)
            raise

        save_progress(complete=True)
        return citations

    def _fetch_by_year(self, citedby_url, citations, save_progress,
                        num_citations, pub_year, prev_scholar_count=0):
        """
        Fetch citations year-by-year. Skips completed years and uses
        start_index within partially completed years for efficient resume.
        prev_scholar_count: Scholar count from last completed scan, used for early stop.
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

        # Fetch direction depends on mode:
        # - Force/full rescan: old→new (stable old years first, new years last)
        # - Normal update (Scholar count increased): new→old (new citations in recent years,
        #   early stop kicks in quickly)
        if prev_scholar_count > 0 and prev_scholar_count < num_citations:
            # Normal update mode: new→old for efficient early stop
            year_range = range(current_year, start_year - 1, -1)
            print(f"  Direction: newest→oldest (update mode, early stop enabled)", flush=True)
        else:
            # Force/full rescan mode: old→new for stable progress saving
            year_range = range(start_year, current_year + 1)
            print(f"  Direction: oldest→newest (full scan mode)", flush=True)

        # Build dedup map from existing citations: key -> brief info for logging
        cached_titles = {c.get('title', '').strip().lower() for c in citations}
        seen_titles = {k: f"{k[:50]} (cached)" for k in cached_titles}
        paper_new_count = 0  # new citations found for THIS paper in this fetch

        try:
            for year in year_range:
                if year in self._completed_year_segments:
                    skipped_years += 1
                    continue

                # Always fetch from start_index=0 and rely on dedup to skip
                # already-cached citations. Using year_counts as start_index is
                # unreliable because some citations have N/A year fields, causing
                # year_counts to undercount and real citations to be missed.
                start_index = 0

                # Refresh session on each year switch
                self._refresh_scholarly_session()
                self._next_refresh_at = self._total_page_count + random.randint(SESSION_REFRESH_MIN, SESSION_REFRESH_MAX)

                print(f"      Year {year}: fetching", flush=True)

                # URL matches browser navigation: as_sdt=2005 is Scholar's internal
                # citation-search flag (identical to what Scholar's own year-filter
                # links use); sciodt=0,5 and scipsc= are also present in browser URLs.
                year_url = (f'/scholar?as_ylo={year}&as_yhi={year}&hl=en'
                            f'&as_sdt=2005&sciodt=0,5&cites={pub_id}&scipsc=')
                if start_index > 0:
                    year_url += f'&start={start_index}'
                print(f"      URL: https://scholar.google.com{year_url}", flush=True)
                nav = scholarly._Scholarly__nav

                year_new_count = 0
                for citing in _SearchScholarIterator(nav, year_url):
                    info = self._extract_citation_info(citing)
                    dedup_key = info['title'].strip().lower()
                    if dedup_key in seen_titles:
                        if dedup_key not in cached_titles:
                            # Duplicate within Scholar's own results (not a cache hit)
                            self._dedup_count += 1
                        print(f"  [dedup] Skipping duplicate: {info['title'][:50]}... ({info.get('year', '?')})"
                              f"\n          Existing: {seen_titles[dedup_key]}", flush=True)
                        continue
                    seen_titles[dedup_key] = f"{info['title'][:50]} ({info.get('year', '?')})"
                    citations.append(info)
                    year_new_count += 1
                    paper_new_count += 1
                    self._new_citations_count += 1
                    count = len(citations)

                    print(f"  [{count}] {info['title'][:55]}...", flush=True)

                    if count % self.save_every == 0:
                        save_progress(complete=False)
                        print(f"  Progress saved ({count} citations, {self._new_citations_count} new in this run)", flush=True)

                self._completed_year_segments.add(year)
                if year_new_count > 0:
                    print(f"      Year {year} done: {year_new_count} new citations", flush=True)
                else:
                    print(f"      Year {year} done: no new citations", flush=True)
                # Save after each completed year
                save_progress(complete=False)

                # Early stop: skip older years when we have enough
                if len(citations) >= num_citations:
                    print(f"  Reached target ({len(citations)} >= {num_citations}), "
                          f"skipping older years", flush=True)
                    break
                # When updating (Scholar count increased), new citations are typically
                # in recent years. Once we've found enough to cover the increase,
                # trust cached data for older years and stop.
                scholar_increase = num_citations - prev_scholar_count if prev_scholar_count > 0 else 0
                if scholar_increase > 0 and paper_new_count >= scholar_increase:
                    print(f"  Found {paper_new_count} new (Scholar increase: {scholar_increase}), "
                          f"skipping older years", flush=True)
                    break

        except KeyboardInterrupt:
            save_progress(complete=False)
            raise
        except Exception:
            # Save completed_years progress on any network/other error
            # so the next retry/run doesn't re-fetch already-completed years
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
        """Return (cache status, cached_data) for a publication.
        Status: 'skip_zero' | 'complete' | 'partial' | 'missing'.
        """
        if pub['num_citations'] == 0:
            return 'skip_zero'
        cached = self._load_citation_cache(pub['title'])
        if not cached:
            return 'missing'
        if not cached.get('complete'):
            return 'partial'

        current = pub['num_citations']

        # Primary check: num_citations_seen = cached + deduped.
        # If we've seen >= Scholar count, we have everything (dedup accounts for the gap).
        # This works for both normal and force mode without ambiguity.
        num_seen = cached.get('num_citations_seen')
        if num_seen is not None:
            if num_seen >= current:
                return 'complete'
            return 'partial'

        # Fallback for caches without num_citations_seen (fetched before this change):
        # Normal mode: compare Scholar count at last completion vs current.
        # Force mode: compare actual cached count vs current.
        actual_cached = cached.get('num_citations_cached', len(cached.get('citations', [])))
        if self.force_refresh_citations:
            if actual_cached >= current:
                return 'complete'
            return 'partial'

        scholar_at_completion = cached.get('num_citations_on_scholar', 0)
        if current <= scholar_at_completion:
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
        self._new_citations_count = 0  # genuinely new citations (not in cache before)
        self._papers_fetched_count = 0  # papers that went through full fetch this run
        self._run_start_time = time.time()  # track elapsed time

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
            if st == 'partial' and cached:
                resume_from = cached.get('citations', [])
                old_scholar = cached.get('num_citations_on_scholar', cached.get('num_citations_cached', 0))
                prev_scholar_count = old_scholar
                if self.force_refresh_citations:
                    # Force mode: clear completed_years to re-check all years
                    completed_years = []
                    action = f"force re-check ({len(resume_from)} cached, scholar={num_citations})"
                elif old_scholar == num_citations:
                    # Scholar count unchanged, resume from where we left off
                    completed_years = cached.get('completed_years', [])
                    action = f"resume ({len(resume_from)} cached, fetching remaining)"
                else:
                    # Scholar count changed: keep cached citations but clear completed_years
                    # so all years are re-checked for new citations
                    completed_years = []
                    action = f"update ({len(resume_from)} cached, citations {old_scholar} -> {num_citations})"
            else:
                resume_from = []
                completed_years = []
                action = "first fetch"

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
                        if latest_cache:
                            resume_from = latest_cache.get('citations', [])
                            completed_years = latest_cache.get('completed_years', [])
                            print(f"  Retrying with {len(resume_from)} cached citations from previous attempt")
                    citations = self._fetch_citations_with_progress(
                        citedby_url, cache_path, title, num_citations,
                        pub_url, pub.get('year', 'N/A'), resume_from,
                        completed_years=completed_years,
                        prev_scholar_count=prev_scholar_count
                    )
                    seen_total = len(citations) + self._dedup_count
                    dedup_str = f", {self._dedup_count} dupes" if self._dedup_count else ""
                    print(f"  Done: {len(citations)} cached, {seen_total} seen{dedup_str} (Scholar: {num_citations})")
                    self._papers_fetched_count += 1
                    break
                except Exception as e:
                    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    print(f"  [{now}] Error (attempt {attempt}/{MAX_RETRIES}, "
                          f"total pages: {self._total_page_count}, "
                          f"new citations: {self._new_citations_count}): {e}")
                    if attempt >= MAX_RETRIES:
                        # Final attempt failed — print time and terminate
                        traceback.print_exc()
                        print(f"\n  [{now}] All retry attempts exhausted. Terminating.", flush=True)
                        # Save whatever we have before exiting
                        self._save_output(results)
                        sys.exit(1)
                    # Offer interactive captcha solve when --interactive-captcha is set.
                    # If the user pastes a fresh cURL from the browser, cookies are
                    # injected and we retry immediately without the long wait.
                    if self.interactive_captcha:
                        solved = self._try_interactive_captcha(
                            getattr(self, '_current_attempt_url',
                                    getattr(self, '_last_scholar_url',
                                            'https://scholar.google.com/scholar')))
                        if solved:
                            print(f"  Retrying with injected cookies (attempt {attempt + 1}/{MAX_RETRIES})...",
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
                d = rand_delay()
                print(f"  Waiting {d:.0f}s before next paper... [{self._wait_status()}]", flush=True)
                time.sleep(d)

    def _inject_cookies_from_curl(self, curl_str):
        """Parse cookies from a pasted cURL command and inject into scholarly sessions.
        Cookies are set without domain restriction so they are sent regardless of
        which regional Scholar domain (e.g. .com.hk vs .com) is used.
        Also stores them in _injected_cookies so patched_new_session can re-apply
        them if scholarly recreates the httpx client on a 403.
        Returns the number of cookies injected, or 0 on failure.
        """
        m = (re.search(r"-b '([^']+)'", curl_str) or
             re.search(r'-b "([^"]+)"', curl_str))
        if not m:
            print("  (Could not find -b '...' cookie string in input)", flush=True)
            return 0
        nav = scholarly._Scholarly__nav
        cookies = {}
        for pair in m.group(1).split(';'):
            pair = pair.strip()
            if '=' in pair:
                k, v = pair.split('=', 1)
                cookies[k.strip()] = v.strip()
        if not cookies:
            print("  (No valid cookies found in pasted input)", flush=True)
            return 0
        # Inject without domain so cookies apply to scholar.google.com AND any
        # regional variant (e.g. scholar.google.com.hk) after 302 redirects.
        for session in (nav._session1, nav._session2):
            for k, v in cookies.items():
                session.cookies.set(k, v)
        # Persist for re-application after scholarly recreates sessions on 403
        self._injected_cookies = cookies
        nav.got_403 = False
        print(f"  Injected {len(cookies)} cookies (no domain restriction).", flush=True)
        return len(cookies)

    def _try_interactive_captcha(self, url):
        """Prompt user to solve captcha manually and inject resulting cookies.
        Only called when --interactive-captcha is set.
        Returns True if cookies were successfully injected.

        Input strategy: read line by line via input(), stop automatically when
        the last cURL line is detected (no trailing backslash).  This is
        reliable across SSH, tmux, and local terminals — unlike sys.stdin.read()
        which requires Ctrl+D to send EOF (unreliable in SSH/tmux).
        """
        sep = "  " + "=" * 62
        print(f"\n{sep}", flush=True)
        print(f"  Captcha / block detected. Resolve it manually:", flush=True)
        print(f"  1. Open this URL in your browser:", flush=True)
        print(f"       {url}", flush=True)
        print(f"  2. Solve the captcha if shown, then let the page load fully", flush=True)
        print(f"  3. F12 → Network → find the Scholar request", flush=True)
        print(f"     → right-click → Copy as cURL (bash)", flush=True)
        print(f"  4. Paste the cURL — end is detected automatically", flush=True)
        print(f"     (Press Enter on empty line to skip)", flush=True)
        print(f"{sep}", flush=True)
        lines = []
        print("  > ", end='', flush=True)
        try:
            while True:
                line = input()
                if not line.strip():
                    # Empty line: skip if nothing pasted yet, else treat as confirm
                    break
                lines.append(line)
                # Chrome DevTools cURL: every line except the last ends with '\'
                # Detect end of paste automatically — no Ctrl+D needed
                if not line.rstrip().endswith('\\'):
                    break
        except (EOFError, KeyboardInterrupt):
            print(flush=True)
            return False
        if not lines:
            print("  (Skipped — using automatic wait)", flush=True)
            return False
        return self._inject_cookies_from_curl(' '.join(lines)) > 0

    def _wait_proxy_switch(self, max_hours=24):
        """Wait up to max_hours for the user to switch proxy/IP.
        Prints a prompt and checks stdin every minute for 'ok'.
        Prints an hourly reminder with time remaining.
        Returns True if user confirmed, False if timed out.
        """
        import select as _select
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"\n  [{now}] Scholar is blocking this IP.", flush=True)
        print(f"  Please switch your proxy/IP, then type  ok  and press Enter.", flush=True)
        print(f"  (Program will retry automatically after {max_hours}h if no input.)",
              flush=True)

        deadline      = time.time() + max_hours * 3600
        last_reminder = time.time()
        CHECK_SEC     = 60    # poll stdin every minute
        REMIND_SEC    = 3600  # hourly reminder

        while time.time() < deadline:
            # Hourly reminder
            if time.time() - last_reminder >= REMIND_SEC:
                remaining = (deadline - time.time()) / 3600
                ts = datetime.now().strftime('%H:%M:%S')
                print(f"  [{ts}] Still waiting — {remaining:.0f}h remaining. "
                      f"Type  ok  to resume now.", flush=True)
                last_reminder = time.time()

            wait = min(CHECK_SEC, max(0, deadline - time.time()))
            try:
                ready = _select.select([sys.stdin], [], [], wait)[0]
                if ready:
                    line = sys.stdin.readline().strip().lower()
                    if line == 'ok':
                        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        print(f"  [{ts}] Proxy switch confirmed. Resuming...",
                              flush=True)
                        return True
            except Exception:
                # select unavailable (Windows, piped stdin, etc.) — plain sleep
                time.sleep(wait)

        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"  [{ts}] {max_hours}h elapsed. Resuming...", flush=True)
        return False

    def _save_output(self, results):
        """Save citation results to JSON and Excel."""
        print("\n" + "=" * 70)
        # For None entries (not processed this run), fall back to cached data
        # so the total always reflects all known citations, not just this run's.
        with open(self.profile_json, 'r', encoding='utf-8') as f:
            publications = json.load(f).get('publications', [])
        final_results = []
        for i, r in enumerate(results):
            if r is not None:
                final_results.append(r)
            else:
                # Load from cache if available, otherwise empty
                pub = publications[i] if i < len(publications) else {}
                cached = self._load_citation_cache(pub.get('title', '')) if pub else None
                citations = cached.get('citations', []) if cached else []
                final_results.append({'pub': pub, 'citations': citations})
        total_cites = sum(len(r['citations']) for r in final_results)
        with open(self.out_json, 'w', encoding='utf-8') as f:
            json.dump({
                'author_id': self.author_id,
                'fetch_time': datetime.now().isoformat(),
                'total_papers': len(final_results),
                'total_citations_collected': total_cites,
                'papers': final_results,
            }, f, ensure_ascii=False, indent=2)
        print(f"Saved JSON : {self.out_json}")

        self._save_xlsx(final_results)
        print(f"Saved Excel: {self.out_xlsx}")

        total_papers = len(results)  # includes None slots (total publications)
        fetched_str = f", {self._papers_fetched_count} fetched" if self._papers_fetched_count else ""
        new_str = f", {self._new_citations_count} new" if self._new_citations_count else ""
        print(f"\nDone! {len(final_results)}/{total_papers} papers{fetched_str}, "
              f"{total_cites} total citation records{new_str}")
        print(f"Run summary: elapsed {self._elapsed_str()}"
              f" | {self._total_page_count} pages accessed"
              f" | {self._new_citations_count} new citations\n")

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
    parser.add_argument('--force-refresh-pubs', action='store_true',
                        help='Force re-fetch publications list from Scholar')
    parser.add_argument('--force-refresh-citations', action='store_true',
                        help='Re-check papers where cached count < Scholar count')
    parser.add_argument('--interactive-captcha', action='store_true',
                        help='When blocked, pause and prompt for browser cookie injection to bypass captcha')
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
    success = fetcher.run(force_refresh_pubs=args.force_refresh_pubs)
    if not success:
        sys.exit(1)

    # Check if citations or publication count changed since last run
    curr_profile = fetcher.load_prev_profile()  # just saved
    if prev_profile and curr_profile:
        prev_citations = prev_profile.get('total_citations', prev_profile.get('author_info', {}).get('citedby', -1))
        curr_citations = curr_profile.get('total_citations', curr_profile.get('author_info', {}).get('citedby', -2))
        prev_pubs = prev_profile.get('total_publications', -1)
        curr_pubs = curr_profile.get('total_publications', -2)

        if prev_citations == curr_citations and prev_pubs == curr_pubs and not args.force_refresh_citations:
            # Even if totals haven't changed, check if all citations are fully cached
            citation_fetcher = PaperCitationFetcher(
                author_id=author_id,
                output_dir=args.output_dir,
                limit=args.limit,
                skip=args.skip,
                force_refresh_citations=args.force_refresh_citations,
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
        force_refresh_citations=args.force_refresh_citations,
        interactive_captcha=args.interactive_captcha,
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
