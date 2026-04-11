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


def rand_delay(scale=1.0):
    """Return a random delay between DELAY_MIN and DELAY_MAX seconds."""
    return random.uniform(DELAY_MIN, DELAY_MAX) * scale


def now_str():
    """Return current time as [HH:MM:SS] string for log prefixes."""
    return datetime.now().strftime('[%H:%M:%S]')


class TeeStream:
    """Mirror stdout writes to both terminal and a log file."""
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            stream.write(data)
        return len(data)

    def flush(self):
        for stream in self.streams:
            stream.flush()

    def isatty(self):
        return any(getattr(stream, 'isatty', lambda: False)() for stream in self.streams)


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
        """Summarize how author-level citation totals relate to the yearly histogram."""
        cites_per_year = basics.get('cites_per_year', {}) or {}
        year_total = sum(int(v or 0) for v in cites_per_year.values())
        scholar_total = int(basics.get('citedby', 0) or 0)
        gap = scholar_total - year_total
        return {
            'scholar_total_citations': scholar_total,
            'year_table_total_citations': year_total,
            'year_table_gap': gap,
            'year_table_matches_total': gap == 0,
            'year_table_note': (
                'Year-table totals come from Scholar cites_per_year and may exclude '
                'citations without usable year metadata.'
            ),
        }

    def save_profile_json(self, basics, publications, change_history=None, fetch_time=None):
        """Save complete profile as JSON."""
        count_summary = self._build_profile_count_summary(basics)
        profile = {
            'author_info': basics,
            'publications': publications,
            'fetch_time': fetch_time or datetime.now().isoformat(),
            'total_publications': len(publications),
            'total_citations': basics.get('citedby', 0),
            'citation_count_summary': count_summary,
            'change_history': change_history or [],
        }
        with open(self.profile_json, 'w', encoding='utf-8') as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
        print(f"Saved JSON: {self.profile_json}")
        return profile

    def save_profile_xlsx(self, basics, publications, change_history=None, fetch_time=None):
        """
        Save Excel file with 3 sheets:
          Sheet1: Author Overview
          Sheet2: Publications (sorted by citation count)
          Sheet3: Change History
        """
        count_summary = self._build_profile_count_summary(basics)
        wb = openpyxl.Workbook()
        self._last_profile_workbook = wb

        display_fetch_time = fetch_time
        if display_fetch_time is None:
            now = datetime.now()
            display_fetch_time = now.isoformat() if hasattr(now, 'isoformat') else str(now)

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
            ("Fetch Time", display_fetch_time.replace('T', ' ')[:19]),
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
            ("Total Citations (Scholar profile)", basics.get('citedby', 0)),
            ("Year-table subtotal (cites_per_year)", count_summary['year_table_total_citations']),
            ("Year-table gap vs total", count_summary['year_table_gap']),
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
        c = ws1.cell(row=row, column=1, value="Citations Per Year (Scholar cites_per_year)")
        c.fill = header_fill
        c.font = header_font
        c.alignment = center
        ws1.row_dimensions[row].height = 24
        row += 1

        ws1.merge_cells(f'A{row}:B{row}')
        note_cell = ws1.cell(row=row, column=1, value=count_summary['year_table_note'])
        note_cell.alignment = left
        ws1.row_dimensions[row].height = 34
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

class PaperCitationFetcher:
    def __init__(self, author_id, output_dir=".",
                 limit=None, skip=0, save_every=10,
                 recheck_citations=False,
                 interactive_captcha=False,
                 delay_scale=1.0):
        self.author_id = author_id
        self.output_dir = output_dir
        self.limit = limit
        self.skip = skip
        self.save_every = save_every
        self.recheck_citations = recheck_citations
        self.interactive_captcha = interactive_captcha
        self._captcha_solved_count = 0
        self._delay_scale = delay_scale
        self._injected_cookies = {}
        self._injected_header_overrides = {}

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
        self._curl_header_allowlist = {
            'accept',
            'accept-language',
            'priority',
            'sec-ch-ua',
            'sec-ch-ua-arch',
            'sec-ch-ua-bitness',
            'sec-ch-ua-full-version-list',
            'sec-ch-ua-mobile',
            'sec-ch-ua-model',
            'sec-ch-ua-platform',
            'sec-ch-ua-platform-version',
            'sec-ch-ua-wow64',
        }
        self._last_scholar_url = _PROFILE_URL  # tracks current page for Referer

        def _make_http2_session():
            """Create an httpx.Client with HTTP/2 enabled.
            trust_env=True (httpx default) picks up HTTPS_PROXY automatically.
            """
            return __import__('httpx').Client(http2=True)

        def _apply_browser_headers(session):
            session.headers.update(_BROWSER_HEADERS)
            session.headers.update(fetcher_self._injected_header_overrides)
            session.headers['referer'] = fetcher_self._last_scholar_url

        def _full_session_setup(session):
            """Apply browser headers + injected cookies to a session."""
            _apply_browser_headers(session)
            for k, v in fetcher_self._injected_cookies.items():
                session.cookies.set(k, v)   # no domain = sent to all requests

        # Replace scholarly's default HTTP/1.1 sessions with HTTP/2 ones,
        # preserving any cookies Scholar set during the profile fetch phase.
        old_cookies = {}
        for old_session in (nav._session1, nav._session2):
            try:
                old_cookies.update(dict(old_session.cookies))
            except Exception:
                pass
        nav._session1 = _make_http2_session()
        nav._session2 = _make_http2_session()
        for session in (nav._session1, nav._session2):
            _apply_browser_headers(session)
            for k, v in old_cookies.items():
                session.cookies.set(k, v)
        print(f"  Browser headers applied (HTTP/2, Edge/145, sec-ch-ua-*, referer; "
              f"{len(old_cookies)} profile cookies preserved)", flush=True)

        # Patch _new_session: on 403 scholarly recreates the httpx client;
        # replace it with an HTTP/2 session and re-apply full browser identity.
        original_new_session = nav._new_session
        fetcher_self._injected_cookies = {}   # populated by _inject_cookies_from_curl
        fetcher_self._injected_header_overrides = {}
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
                d = rand_delay(fetcher_self._delay_scale)
                retry_note = f" (retry {sleep_count[0]})" if sleep_count[0] > 1 else ""
                print(f"      {now_str()} Waiting {d:.0f}s before request{retry_note}... "
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
        original_next = _SearchScholarIterator.__next__
        self._total_page_count = 0
        self._next_break_at = random.randint(MANDATORY_BREAK_EVERY_MIN, MANDATORY_BREAK_EVERY_MAX)
        self._next_refresh_at = self._next_break_at + random.randint(SESSION_REFRESH_MIN, SESSION_REFRESH_MAX)

        def patched_init(self, nav, url):
            self._page_num = 0
            self._items_in_current_page = 0
            self._page_size = None
            self._stop_after_current_page = False
            self._finished_current_page = False
            return original_init(self, nav, url)

        def patched_next(self_iter):
            result = original_next(self_iter)
            self_iter._items_in_current_page = getattr(self_iter, '_items_in_current_page', 0) + 1
            page_size = getattr(self_iter, '_page_size', None)
            if page_size and self_iter._items_in_current_page >= page_size:
                self_iter._finished_current_page = True
            return result

        def patched_load_url(self_iter, url):
            self_iter._page_num = getattr(self_iter, '_page_num', 0) + 1
            self_iter._items_in_current_page = 0
            self_iter._finished_current_page = False
            fetcher_self._total_page_count += 1
            if self_iter._page_num > 1:
                print(f"      Pagination (page {self_iter._page_num})", flush=True)

            # Set Referer = previous Scholar page (mimics browser navigation chain)
            for session in (nav._session1, nav._session2):
                session.headers['referer'] = fetcher_self._last_scholar_url

            # Mandatory long break (higher priority than soft refresh)
            # Resets Scholar's sliding-window rate limit
            # In interactive mode, skip session resets — the user has already
            # injected fresh cookies; resetting would discard them.
            if fetcher_self._total_page_count >= fetcher_self._next_break_at:
                d = random.uniform(MANDATORY_BREAK_MIN, MANDATORY_BREAK_MAX) * fetcher_self._delay_scale
                print(f"      {now_str()} Mandatory break after {fetcher_self._total_page_count} pages "
                      f"({d/60:.1f} min)... [{fetcher_self._wait_status()}]", flush=True)
                time.sleep(d)
                fetcher_self._next_break_at = (fetcher_self._total_page_count
                                               + random.randint(MANDATORY_BREAK_EVERY_MIN, MANDATORY_BREAK_EVERY_MAX))
                if not fetcher_self.interactive_captcha:
                    print(f"      {now_str()} Refreshing session after break...", flush=True)
                    fetcher_self._refresh_scholarly_session()
                fetcher_self._next_refresh_at = (fetcher_self._total_page_count
                                                 + random.randint(SESSION_REFRESH_MIN, SESSION_REFRESH_MAX))
            # Soft session refresh between breaks
            elif fetcher_self._total_page_count >= fetcher_self._next_refresh_at:
                if not fetcher_self.interactive_captcha:
                    print(f"      {now_str()} Refreshing session (after {fetcher_self._total_page_count} pages)...", flush=True)
                    fetcher_self._refresh_scholarly_session()
                fetcher_self._next_refresh_at = (fetcher_self._total_page_count
                                                 + random.randint(SESSION_REFRESH_MIN, SESSION_REFRESH_MAX))

            # Record the URL we are about to load so _try_interactive_captcha
            # can show it even if this request fails before _last_scholar_url
            # is updated (e.g. captcha on the very first citation page).
            fetcher_self._current_attempt_url = (
                f'https://scholar.google.com{url}' if url.startswith('/') else url)

            result = original_load_url(self_iter, url)

            page_size = None
            try:
                page_size = len(getattr(self_iter, '_rows', []) or [])
            except Exception:
                page_size = None
            self_iter._page_size = page_size if page_size and page_size > 0 else None

            # Only update _last_scholar_url (used as Referer) on success
            fetcher_self._last_scholar_url = fetcher_self._current_attempt_url

            return result

        _SearchScholarIterator.__init__ = patched_init
        _SearchScholarIterator.__next__ = patched_next
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
    def _year_count_map(citations):
        counts = {}
        for c in citations:
            y = c.get('year', 'N/A')
            if y and y != 'N/A' and y != 'NA':
                try:
                    year = int(y)
                    counts[year] = counts.get(year, 0) + 1
                except ValueError:
                    pass
        return counts

    @staticmethod
    def _normalize_year_count_map(year_counts):
        normalized = {}
        for year, count in (year_counts or {}).items():
            try:
                y = int(year)
                c = int(count)
            except (TypeError, ValueError):
                continue
            if c < 0:
                continue
            normalized[y] = c
        return normalized

    @staticmethod
    def _dump_year_count_map(year_counts):
        return {str(year): count for year, count in sorted(year_counts.items())}

    @staticmethod
    def _effective_scholar_total(pub, cached=None):
        current = int(pub.get('num_citations', 0) or 0)
        if cached:
            for key in ('num_citations_on_scholar',):
                try:
                    cached_total = int(cached.get(key))
                except (TypeError, ValueError):
                    continue
                current = max(current, cached_total)
        return current

    def _promote_live_citation_count(self, pub, live_total, source=None):
        try:
            promoted_total = int(live_total)
        except (TypeError, ValueError):
            return int(pub.get('num_citations', 0) or 0)
        current_total = int(pub.get('num_citations', 0) or 0)
        if promoted_total <= current_total:
            return current_total
        pub['num_citations'] = promoted_total
        updates = getattr(self, '_updated_publication_counts', None)
        if updates is not None:
            title = pub.get('title')
            if title:
                updates[title] = promoted_total
        if source:
            print(f"    Live citation count promoted: {current_total} -> {promoted_total} ({source})", flush=True)
        return promoted_total

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
        if pub_year in (None, '', 'N/A', 'NA', '?'):
            return None
        try:
            year = int(str(pub_year).strip())
        except (TypeError, ValueError):
            return None
        if year > int(current_year):
            return None
        return year

    @classmethod
    def _resolve_citation_fetch_policy(cls, num_citations, pub_year, current_year=None):
        current_year = current_year or datetime.now().year
        total = int(num_citations or 0)
        if total < YEAR_BASED_THRESHOLD:
            return {
                'mode': 'direct',
                'covered_years': None,
                'avg_citations_per_year': None,
                'pub_year': cls._normalize_pub_year(pub_year, current_year),
                'reason': 'below_year_threshold',
            }

        normalized_pub_year = cls._normalize_pub_year(pub_year, current_year)
        if normalized_pub_year is None:
            return {
                'mode': 'year',
                'covered_years': None,
                'avg_citations_per_year': None,
                'pub_year': None,
                'reason': 'invalid_pub_year',
            }

        covered_years = max(1, int(current_year) - normalized_pub_year + 1)
        avg_citations_per_year = total / covered_years
        return {
            'mode': 'direct' if avg_citations_per_year <= 20 else 'year',
            'covered_years': covered_years,
            'avg_citations_per_year': avg_citations_per_year,
            'pub_year': normalized_pub_year,
            'reason': 'low_average_per_year' if avg_citations_per_year <= 20 else 'high_average_per_year',
        }

    @staticmethod
    def _selective_refresh_candidate_years(cached_year_counts, probed_year_counts,
                                           year_range, partial_year_start=None,
                                           probe_complete=False):
        cached_year_counts = PaperCitationFetcher._normalize_year_count_map(cached_year_counts)
        probed_year_counts = PaperCitationFetcher._normalize_year_count_map(probed_year_counts)
        partial_years = {int(year) for year in (partial_year_start or {}).keys()}
        candidate_years = set(partial_years)

        if probe_complete:
            candidate_years.update(
                year for year in year_range
                if cached_year_counts.get(year, 0) != probed_year_counts.get(year, 0)
            )
            return [year for year in year_range if year in candidate_years]

        candidate_years.update(
            year for year in year_range
            if year in probed_year_counts and cached_year_counts.get(year, 0) < probed_year_counts[year]
        )
        if not candidate_years:
            return None
        return [year for year in year_range if year in candidate_years]

    @staticmethod
    def _build_citation_count_summary(citations, scholar_total=None, probed_year_counts=None,
                                      probe_complete=False, dedup_count=0):
        cached_total = len(citations or [])
        cached_year_counts = PaperCitationFetcher._year_count_map(citations or [])
        cached_year_total = sum(cached_year_counts.values())
        cached_unyeared_count = max(0, cached_total - cached_year_total)
        normalized_probed_year_counts = PaperCitationFetcher._normalize_year_count_map(probed_year_counts)
        histogram_total = sum(normalized_probed_year_counts.values())
        unyeared_count = None
        if scholar_total is not None:
            unyeared_count = max(0, scholar_total - histogram_total)
        return {
            'scholar_total': scholar_total,
            'histogram_total': histogram_total,
            'cached_total': cached_total,
            'cached_year_total': cached_year_total,
            'cached_unyeared_count': cached_unyeared_count,
            'dedup_count': int(dedup_count or 0),
            'probe_complete': bool(probe_complete),
            'unyeared_count': unyeared_count,
            'cached_year_counts': cached_year_counts,
            'probed_year_counts': normalized_probed_year_counts,
        }

    def _refresh_reconciliation_status(self, citations, num_citations,
                                       probed_year_counts=None, probe_complete=False):
        count_summary = self._build_citation_count_summary(
            citations,
            scholar_total=num_citations,
            probed_year_counts=probed_year_counts,
            probe_complete=probe_complete,
            dedup_count=getattr(self, '_dedup_count', 0),
        )
        status = {
            'ok': False,
            'reason': 'histogram_incomplete',
            **count_summary,
        }

        if probe_complete:
            if count_summary['cached_year_counts'] == count_summary['probed_year_counts']:
                status.update({
                    'ok': True,
                    'reason': 'matched_complete_histogram',
                })
            else:
                status.update({
                    'reason': 'year_count_mismatch',
                })
            return status

        if count_summary['probed_year_counts']:
            if count_summary['cached_year_counts'] == count_summary['probed_year_counts']:
                status.update({
                    'ok': True,
                    'reason': 'matched_incomplete_histogram',
                })
                return status
            return status

        if count_summary['cached_total'] == count_summary['scholar_total']:
            status.update({
                'ok': True,
                'reason': 'count_matched_without_histogram',
            })
            return status

        return status

    @staticmethod
    def _rehydrate_probe_metadata(cached, current_scholar_total):
        normalized_counts = PaperCitationFetcher._normalize_year_count_map(
            (cached or {}).get('probed_year_counts')
        )
        probe_complete = False
        if normalized_counts:
            cached_probe_complete = (cached or {}).get('probe_complete') is True
            histogram_total = (cached or {}).get('probed_year_total')
            try:
                histogram_total = int(histogram_total)
            except (TypeError, ValueError):
                histogram_total = sum(normalized_counts.values())
            if cached_probe_complete and current_scholar_total is not None and histogram_total == current_scholar_total:
                probe_complete = True
        return normalized_counts or None, probe_complete

    def _resolve_refresh_strategy(self, pub, cached, cache_status):
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
                'action': 'first fetch',
                'fetch_policy': fetch_policy,
            }

        resume_from = cached.get('citations', [])
        saved_dedup_count = cached.get('dedup_count', 0)
        old_scholar = cached.get('num_citations_on_scholar', cached.get('num_citations_cached', 0))
        completed_years_in_current_run = cached.get(
            'completed_years_in_current_run',
            cached.get('completed_years', []),
        )
        partial_year_start = {}
        force_year_rebuild = False
        selective_refresh_years = None
        rehydrated_probed_year_counts = None
        rehydrated_probe_complete = False
        allow_incremental_early_stop = True
        mode = 'resume'
        action = f"resume ({len(resume_from)} cached, fetching remaining)"

        if self.recheck_citations:
            mode = 'recheck'
            completed_years_in_current_run = []
            allow_incremental_early_stop = False
            force_year_rebuild = fetch_policy['mode'] == 'year'
            action = f"recheck ({len(resume_from)} cached, scholar={num_citations})"
        elif old_scholar != num_citations:
            mode = 'update'
            completed_years_in_current_run = []
            action = f"update ({len(resume_from)} cached, citations {old_scholar} -> {num_citations})"
        else:
            rehydrated_probed_year_counts, rehydrated_probe_complete = self._rehydrate_probe_metadata(
                cached,
                num_citations,
            )

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
            'action': action,
            'fetch_policy': fetch_policy,
        }

    @staticmethod
    def _refresh_log_message(prefix, status):
        return (
            f"{prefix}: {status['reason']} "
            f"(scholar_total={status.get('scholar_total')}, year_sum={status.get('histogram_total', '?')}, "
            f"cached_total={status.get('cached_total')}, cached_year_sum={status.get('cached_year_total', '?')}, "
            f"dedup_num={status.get('dedup_count', 0)})"
        )

    @staticmethod
    def _refresh_escalation_message(status):
        return (
            f"Escalating to full revalidation: {status['reason']} "
            f"(scholar_total={status.get('scholar_total')}, year_sum={status.get('histogram_total', '?')}, "
            f"cached_total={status.get('cached_total')}, cached_year_sum={status.get('cached_year_total', '?')}, "
            f"dedup_num={status.get('dedup_count', 0)})"
        )

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

    def _probe_citation_start_year(self, citedby_url, num_citations=None, pub_year=None):
        """Fetch the base citedby URL once and determine the earliest year with
        citations.

        Primary source (most reliable): already-rendered histogram bars in the
        page DOM, e.g. `span.gs_hist_g_a[data-year][data-count]` under
        `#gs_res_sb_hist_wrp`.  These contain the per-year citation distribution
        directly (`data-year="2022" data-count="2"`).

        Fallbacks (less reliable):
          1. as_ylo=X&as_yhi=X single-year links from the histogram UI
          2. as_ylo=YYYY preset sidebar filter links (coarse fixed intervals)
          3. Year text in visible citation snippets

        Captcha / access errors are handled in-place (same interactive/proxy-switch
        flow as the main fetch) so the caller never needs to rebuild state just
        because a single probe request was blocked.  Returns None on total failure.
        """
        import re as _re
        nav = scholarly._Scholarly__nav
        full_url = (f'https://scholar.google.com{citedby_url}'
                    if citedby_url.startswith('/') else citedby_url)
        MAX_PROBE_RETRIES = 3
        attempt = 0
        while True:
            attempt += 1
            self._total_page_count += 1
            for session in (nav._session1, nav._session2):
                session.headers['referer'] = self._last_scholar_url
            self._current_attempt_url = full_url
            try:
                soup = nav._get_soup(citedby_url)
                self._last_scholar_url = full_url
            except Exception as e:
                print(f"      {now_str()} Probe blocked (attempt {attempt}): {e}", flush=True)
                if self.interactive_captcha:
                    solved = self._try_interactive_captcha(full_url)
                    if solved:
                        continue
                if attempt >= MAX_PROBE_RETRIES:
                    print(f"      Probe gave up after {MAX_PROBE_RETRIES} attempts, "
                          f"falling back to pub_year heuristic", flush=True)
                    return None
                self._wait_proxy_switch(max_hours=24)
                continue

            try:
                current_year = datetime.now().year
                years = set()
                probed_year_counts = {}
                self._probed_year_counts = None
                self._probed_year_count_complete = False

                try:
                    pub_year_int = int(pub_year) if pub_year and pub_year not in ('N/A', '?') else None
                except (TypeError, ValueError):
                    pub_year_int = None

                # Primary source: full histogram dialog DOM nodes with explicit
                # year/count. The sidebar mini-chart can be truncated to recent
                # years only, so do NOT use it as the authoritative source.
                for bar in soup.select('.gs_rs_hist_dialog-g_bar_wrapper .gs_hist_g_a[data-year][data-count], #gs_md_hist .gs_hist_g_a[data-year][data-count]'):
                    try:
                        y = int(bar.get('data-year', ''))
                        count = int(bar.get('data-count', '0'))
                        if 1990 <= y <= current_year:
                            probed_year_counts[y] = count
                            if count > 0:
                                years.add(y)
                    except (TypeError, ValueError):
                        pass

                # If full histogram DOM is present, validate that the summed counts
                # match Scholar's citation total before trusting it for start-year
                # selection or count-based year skipping.
                if years:
                    self._probed_year_counts = probed_year_counts
                    hist_total = sum(probed_year_counts.values())
                    hist_summary = self._format_year_count_summary(probed_year_counts)
                    earliest = min(years)
                    if num_citations is not None and hist_total >= num_citations:
                        self._probed_year_count_complete = True
                        if hist_total > num_citations and getattr(self, '_current_pub_for_live_promotion', None) is not None:
                            self._promote_live_citation_count(
                                self._current_pub_for_live_promotion,
                                hist_total,
                                source='year_histogram_total',
                            )
                        print(f"      Scholar year range probe: start_year = {earliest} "
                              f"(from full histogram DOM, {len(years)} year values found, total={hist_total})", flush=True)
                        print(f"      Year histogram summary: {hist_summary}", flush=True)
                        return earliest

                    conservative_start = earliest
                    used_pub_year_fallback = False
                    if pub_year_int is not None and pub_year_int < conservative_start:
                        conservative_start = pub_year_int
                        used_pub_year_fallback = True
                    if num_citations is not None:
                        print(f"      Scholar year range probe: histogram incomplete "
                              f"(hist_total={hist_total}, scholar_total={num_citations}), "
                              f"using conservative start_year = {conservative_start}", flush=True)
                    else:
                        print(f"      Scholar year range probe: histogram total unavailable, "
                              f"using conservative start_year = {conservative_start}", flush=True)
                    print(f"      Year histogram summary: {hist_summary}", flush=True)
                    if pub_year_int is not None:
                        fallback_note = 'pub_year fallback applied' if used_pub_year_fallback else 'pub_year fallback not needed'
                        print(f"      Conservative year traversal: pub_year={pub_year_int} ({fallback_note})", flush=True)
                    else:
                        print("      Conservative year traversal: pub_year unavailable", flush=True)
                    return conservative_start

                # Fallback 1: single-year histogram links (as_ylo=X&as_yhi=X)
                for a in soup.find_all('a', href=True):
                    href = a['href']
                    m_lo = _re.search(r'[?&]as_ylo=(\d{4})', href)
                    m_hi = _re.search(r'[?&]as_yhi=(\d{4})', href)
                    if m_lo and m_hi and m_lo.group(1) == m_hi.group(1):
                        years.add(int(m_lo.group(1)))

                # Fallback 2: as_ylo=YYYY preset sidebar filter links (coarse)
                for a in soup.find_all('a', href=True):
                    href = a['href']
                    m = _re.search(r'[?&]as_ylo=(\d{4})', href)
                    if m:
                        years.add(int(m.group(1)))

                # Fallback 3: year text in visible citation snippets
                for el in soup.find_all(True):
                    cls = ' '.join(el.get('class', []))
                    if any(k in cls for k in ('gs_age', 'gs_gray', 'gs_a')):
                        for m in _re.finditer(r'\b((?:19|20)\d{2})\b', el.get_text()):
                            y = int(m.group(1))
                            if 1990 <= y <= current_year:
                                years.add(y)

                if years:
                    earliest = min(years)
                    if pub_year_int is not None:
                        earliest = min(earliest, pub_year_int)
                    print(f"      Scholar year range probe: start_year = {earliest} "
                          f"(from fallback, {len(years)} year values found)", flush=True)
                    print(f"      Fallback year summary: {self._format_year_set_summary(years)}", flush=True)
                    print("      Conservative year traversal: no complete histogram available", flush=True)
                    return earliest
                if pub_year_int is not None:
                    print(f"      (Year range probe: no year data found on page, using pub_year {pub_year_int})", flush=True)
                    print("      Conservative year traversal: using pub_year fallback only", flush=True)
                    return pub_year_int
                print(f"      (Year range probe: no year data found on page)", flush=True)
                return None
            except Exception as e:
                print(f"      (Year range probe: parsing failed: {e})", flush=True)
                return None

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
                f"{self._total_page_count} pages, "
                f"{self._captcha_solved_count} captcha solves")

    def _citation_cache_path(self, title):
        key = hashlib.md5(title.encode('utf-8')).hexdigest()[:16]
        return os.path.join(self.cache_dir, f"{key}.json")

    @staticmethod
    def _normalize_cites_id(cites_id):
        if cites_id in (None, '', [], ()):
            return None
        if isinstance(cites_id, (list, tuple, set)):
            parts = [str(part).strip() for part in cites_id if str(part).strip()]
            return ','.join(parts) if parts else None
        value = str(cites_id).strip()
        return value or None

    @staticmethod
    def _normalize_identity_part(value):
        if value is None:
            return ''
        return ' '.join(str(value).strip().lower().split())

    @classmethod
    def _citation_identity_keys(cls, info):
        keys = []
        cites_id = cls._normalize_cites_id(info.get('cites_id'))
        if cites_id:
            keys.append(f"cites_id\t{cites_id}")
        title = cls._normalize_identity_part(info.get('title'))
        venue = cls._normalize_identity_part(info.get('venue'))
        authors = cls._normalize_identity_part(info.get('authors'))
        if venue:
            keys.append(f"meta\t{title}\t{venue}")
        elif authors:
            keys.append(f"meta\t{title}\t{authors}")
        else:
            keys.append(f"meta\t{title}")
        deduped = []
        for key in keys:
            if key not in deduped:
                deduped.append(key)
        return deduped

    @classmethod
    def _citation_identity_key(cls, info):
        return cls._citation_identity_keys(info)[0]

    @staticmethod
    def _extract_citation_info(pub, fallback_year=None):
        bib = pub.get('bib', {})
        authors = bib.get('author', [])
        year = str(bib.get('pub_year', 'N/A'))
        if str(year).strip().lower() in ('', 'n/a', 'na', '?') and fallback_year is not None:
            year = str(fallback_year)
        return {
            'title':   bib.get('title', 'N/A'),
            'authors': ', '.join(authors) if isinstance(authors, list) else str(authors),
            'venue':   bib.get('venue', 'N/A'),
            'year':    year,
            'url':     pub.get('pub_url', pub.get('eprint_url', 'N/A')),
            'cites_id': PaperCitationFetcher._normalize_cites_id(pub.get('cites_id')),
        }

    @staticmethod
    def _format_year_count_summary(year_count_map, limit=8):
        year_count_map = PaperCitationFetcher._normalize_year_count_map(year_count_map)
        if not year_count_map:
            return 'none'
        items = sorted(year_count_map.items())
        total = sum(count for _, count in items)
        nonzero = [(year, count) for year, count in items if count > 0]
        display_items = items
        if len(items) > limit:
            head = items[: max(1, limit // 2)]
            tail = items[-max(1, limit - len(head)) :]
            display_items = head + [('...', '...')] + tail
        parts = []
        for year, count in display_items:
            if year == '...':
                parts.append('...')
            else:
                parts.append(f"{year}:{count}")
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
                                        pub_obj=None,
                                        fetch_policy=None):
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
        fresh_citations = []
        effective_num_citations = int(num_citations or 0)
        if pub_obj is not None:
            effective_num_citations = self._effective_scholar_total(pub_obj)
            pub_obj['num_citations'] = effective_num_citations
        # _dedup_count tracks Scholar's own duplicate results (same title, different metadata).
        # Initialise from the saved value so resume/force-refresh never loses the history.
        self._dedup_count = saved_dedup_count

        # Load completed years into patch state for _citedby_long to skip
        self._completed_year_segments = set(completed_years_in_current_run or [])
        self._current_year_segment = None
        # Track the page offset (start_index) for the year currently in progress.
        # Saved to cache on exception so retry can skip already-fetched pages.
        self._partial_year_start = dict(partial_year_start or {})
        self._probed_year_counts = self._normalize_year_count_map(rehydrated_probed_year_counts) or None
        self._probed_year_count_complete = bool(rehydrated_probe_complete and self._probed_year_counts)

        def current_scholar_total():
            if pub_obj is not None:
                return self._effective_scholar_total(pub_obj)
            return effective_num_citations

        def maybe_promote_scholar_total(live_total, source=None):
            nonlocal effective_num_citations
            try:
                live_total = int(live_total)
            except (TypeError, ValueError):
                return current_scholar_total()
            if pub_obj is not None:
                effective_num_citations = self._promote_live_citation_count(pub_obj, live_total, source=source)
            elif live_total > effective_num_citations:
                effective_num_citations = live_total
            return effective_num_citations

        def direct_materialized_citations(complete):
            return self._materialize_citation_cache(old_citations, fresh_citations, complete)

        def materialized_citations(complete):
            return direct_materialized_citations(complete)

        def save_progress(complete):
            citations_to_save = materialized_citations(complete)
            if not citations_to_save and old_citations:
                citations_to_save = list(old_citations)
            self._cached_year_counts = self._year_count_map(citations_to_save)
            count_summary = self._build_citation_count_summary(
                citations_to_save,
                scholar_total=current_scholar_total(),
                probed_year_counts=self._probed_year_counts,
                probe_complete=self._probed_year_count_complete,
                dedup_count=self._dedup_count,
            )
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump({
                    'title': title,
                    'pub_url': pub_url,
                    'citedby_url': citedby_url,
                    'num_citations_on_scholar': current_scholar_total(),
                    'num_citations_cached': len(citations_to_save),
                    # num_citations_seen = unique citations Scholar has for this paper.
                    # Use max(current, saved) so the floor is never lost on resume/force-refresh.
                    'num_citations_seen': len(citations_to_save) + self._dedup_count,
                    'dedup_count': self._dedup_count,
                    'complete': complete,
                    'completed_years': sorted(self._completed_year_segments),
                    'completed_years_in_current_run': sorted(self._completed_year_segments),
                    'probe_complete': bool(self._probed_year_count_complete),
                    'probed_year_counts': self._dump_year_count_map(
                        self._normalize_year_count_map(self._probed_year_counts)
                    ),
                    'probed_year_total': count_summary['histogram_total'],
                    'cached_year_counts': self._dump_year_count_map(self._cached_year_counts),
                    'cached_unyeared_count': count_summary['cached_unyeared_count'],
                    'citation_count_summary': {
                        'scholar_total': count_summary['scholar_total'],
                        'histogram_total': count_summary['histogram_total'],
                        'cached_total': count_summary['cached_total'],
                        'cached_year_total': count_summary['cached_year_total'],
                        'cached_unyeared_count': count_summary['cached_unyeared_count'],
                        'dedup_count': count_summary['dedup_count'],
                        'unyeared_count': count_summary['unyeared_count'],
                        'probe_complete': count_summary['probe_complete'],
                    },
                    'fetched_at': datetime.now().isoformat(),
                    'citations': citations_to_save,
                }, f, ensure_ascii=False, indent=2)

        fetch_policy = fetch_policy or self._resolve_citation_fetch_policy(
            current_scholar_total(),
            pub_year,
        )

        # Year-based fetch: for papers with many citations, fetch by year
        # so current-run completed years are tracked and resume is efficient
        if fetch_policy['mode'] == 'year':
            return self._fetch_by_year(
                citedby_url, old_citations, fresh_citations, save_progress,
                current_scholar_total(), pub_year, prev_scholar_count,
                allow_incremental_early_stop=allow_incremental_early_stop,
                force_year_rebuild=force_year_rebuild,
                selective_refresh_years=selective_refresh_years,
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
        direct_fetch_allow_early_stop = (
            not self.recheck_citations
            and not force_year_rebuild
        )
        has_cached_citations = bool(old_citations)
        scholar_increase = (
            max(0, current_scholar_total() - int(prev_scholar_count or 0))
            if has_cached_citations else 0
        )

        print("    Direct fetch mode: no year probe, summary shown after fetch", flush=True)
        print(f"    Direct fetch target: scholar_total={current_scholar_total()}, prev_scholar={prev_scholar_count}, "
              f"cached_total={len(old_citations)}, allow_early_stop={direct_fetch_allow_early_stop}", flush=True)

        old_cache_identity_keys = set()
        for citation in old_citations:
            old_cache_identity_keys.update(self._citation_identity_keys(citation))
        fresh_seen = {}

        try:
            for citing in scholarly.citedby(direct_fetch_pub):
                info = self._extract_citation_info(citing)
                identity_keys = self._citation_identity_keys(info)
                matched_key = next((key for key in identity_keys if key in fresh_seen), None)
                if matched_key is not None:
                    self._dedup_count += 1
                    print(f"  [dedup] Skipping duplicate: {info['title'][:50]}... ({info.get('venue', 'N/A')}, {info.get('year', '?')})"
                          f"\n          Existing: {fresh_seen[matched_key]}", flush=True)
                    continue
                label = f"{info['title'][:50]} ({info.get('venue', 'N/A')}, {info.get('year', '?')})"
                for key in identity_keys:
                    fresh_seen[key] = label
                fresh_citations.append(info)
                is_new_citation = not any(key in old_cache_identity_keys for key in identity_keys)
                if is_new_citation:
                    self._new_citations_count += 1
                seen_total = len(fresh_citations) + self._dedup_count
                maybe_promote_scholar_total(seen_total, source='direct_fetch_seen_total')
                direct_fetch_pub['num_citations'] = current_scholar_total()
                materialized_total = len(direct_materialized_citations(complete=False))
                count = len(fresh_citations)

                print(f"  [{count}] {info['title'][:55]}...", flush=True)

                if count % self.save_every == 0:
                    save_progress(complete=False)
                    print(f"  Progress saved ({count} citations, {self._new_citations_count} new in this run)", flush=True)

                if direct_fetch_allow_early_stop and materialized_total >= current_scholar_total():
                    print(f"  Direct fetch: reached target ({materialized_total} >= {current_scholar_total()}), stopping early", flush=True)
                    break
                if direct_fetch_allow_early_stop and scholar_increase > 0 and is_new_citation and self._new_citations_count >= scholar_increase:
                    print(f"  Direct fetch: recovered Scholar increase ({self._new_citations_count} >= {scholar_increase}), stopping early", flush=True)
                    break
        except KeyboardInterrupt:
            save_progress(complete=False)
            raise

        direct_summary = self._build_citation_count_summary(
            fresh_citations,
            scholar_total=current_scholar_total(),
            probed_year_counts=None,
            probe_complete=False,
            dedup_count=getattr(self, '_dedup_count', 0),
        )
        print("    Probe summary: none", flush=True)
        print(f"    Probe totals: scholar_total={current_scholar_total()}, year_sum=0, missing_from_histogram=?", flush=True)
        print(f"    Cache summary: {self._format_year_count_summary(direct_summary['cached_year_counts'])}", flush=True)
        print(f"    Cache totals: cached_total={direct_summary['cached_total']}, cached_year_sum={direct_summary['cached_year_total']}, cached_unyeared={direct_summary['cached_unyeared_count']}, dedup_num={self._dedup_count}", flush=True)
        save_progress(complete=True)
        return list(fresh_citations)

    @staticmethod
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

    @staticmethod
    def _get_early_stop_status(citations_count, num_citations, paper_new_count,
                               prev_scholar_count, allow_incremental_early_stop=True,
                               suppress_target_reached=False,
                               stop_after_partial_resume=False,
                               disable_target_reached=False):
        scholar_increase = num_citations - prev_scholar_count if prev_scholar_count > 0 else 0
        if stop_after_partial_resume:
            return {
                'should_stop': True,
                'reason': 'partial_resume_completed',
                'message': 'Completed resumed year segment',
                'scholar_increase': scholar_increase,
            }
        if citations_count >= num_citations and not suppress_target_reached and not disable_target_reached:
            return {
                'should_stop': True,
                'reason': 'target_reached',
                'message': f"Reached target ({citations_count} >= {num_citations})",
                'scholar_increase': scholar_increase,
            }
        if allow_incremental_early_stop and scholar_increase > 0 and paper_new_count >= scholar_increase:
            return {
                'should_stop': True,
                'reason': 'scholar_increase_recovered',
                'message': f"Found {paper_new_count} new (Scholar increase: {scholar_increase})",
                'scholar_increase': scholar_increase,
            }
        return {
            'should_stop': False,
            'reason': None,
            'message': '',
            'scholar_increase': scholar_increase,
        }

    def _fetch_by_year(self, citedby_url, old_citations, fresh_citations, save_progress,
                        num_citations, pub_year, prev_scholar_count=0,
                        allow_incremental_early_stop=True,
                        force_year_rebuild=False,
                        selective_refresh_years=None):
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

        old_year_buckets = self._citation_year_buckets(old_citations)
        old_cache_identity_keys = set()
        for citation in old_citations:
            old_cache_identity_keys.update(self._citation_identity_keys(citation))
        fresh_year_buckets = self._citation_year_buckets(fresh_citations)
        fresh_unyeared = list(fresh_year_buckets.pop(None, []))

        def current_citations(complete=False):
            if complete or force_year_rebuild:
                refreshed_unyeared = fresh_unyeared if fresh_unyeared else None
                return self._materialize_year_fetch_citations(
                    old_citations,
                    fresh_year_buckets,
                    refreshed_unyeared=refreshed_unyeared,
                )
            return self._overlay_citations_by_identity(
                old_citations,
                fresh_unyeared + [
                    citation
                    for year in sorted(fresh_year_buckets.keys())
                    for citation in fresh_year_buckets[year]
                ],
            )

        def current_count_for_stop_and_status():
            citations = current_citations(complete=True)
            if effective_target:
                return len(self._year_count_map(citations))
            return len(citations)

        year_count_map = self._year_count_map(old_citations)
        probed_year_counts = self._normalize_year_count_map(self._probed_year_counts)
        can_skip_by_probe_counts = getattr(self, '_probed_year_count_complete', False)
        cached_summary = self._build_citation_count_summary(
            old_citations,
            scholar_total=num_citations,
            probed_year_counts=probed_year_counts,
            probe_complete=can_skip_by_probe_counts,
            dedup_count=getattr(self, '_dedup_count', 0),
        )
        cached_year_counts = self._normalize_year_count_map(getattr(self, '_cached_year_counts', None))
        if not cached_year_counts:
            cached_year_counts = cached_summary['cached_year_counts']

        current_year = datetime.now().year
        selective_refresh_years = None if selective_refresh_years is None else set(selective_refresh_years)
        explicit_refresh_years = set(selective_refresh_years or ())
        explicit_refresh_years.update(int(year) for year in self._partial_year_start.keys())
        if self._completed_year_segments and explicit_refresh_years:
            start_year = min(min(self._completed_year_segments), min(explicit_refresh_years))
            if year_count_map:
                start_year = min(start_year, min(year_count_map.keys()))
        else:
            start_year = self._probe_citation_start_year(
                citedby_url,
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
              f"(current-run completed={len(self._completed_year_segments)})", flush=True)

        year_fetch_plan = self._build_year_fetch_plan(
            start_year, current_year, prev_scholar_count, num_citations,
            allow_incremental_early_stop=False,
        )
        year_range = year_fetch_plan['year_range']
        print(f"    Direction: {year_fetch_plan['direction_label']} "
              f"({year_fetch_plan['direction_reason']})", flush=True)

        paper_new_count = 0

        probed_year_counts = self._normalize_year_count_map(self._probed_year_counts)
        can_skip_by_probe_counts = getattr(self, '_probed_year_count_complete', False)
        count_summary = self._build_citation_count_summary(
            old_citations,
            scholar_total=num_citations,
            probed_year_counts=probed_year_counts,
            probe_complete=can_skip_by_probe_counts,
            dedup_count=getattr(self, '_dedup_count', 0),
        )
        cached_total_citations = count_summary['cached_total']
        cached_year_total = count_summary['cached_year_total']
        cached_unyeared_citations = count_summary['cached_unyeared_count']
        probed_hist_total = count_summary['histogram_total']
        probed_missing_from_histogram = count_summary['unyeared_count']
        histogram_authoritative = probed_hist_total > 0
        print(f"    Probe summary: {self._format_year_count_summary(probed_year_counts)}", flush=True)
        if num_citations is None:
            print(f"    Probe totals: scholar_total=?, year_sum={probed_hist_total}, missing_from_histogram=?", flush=True)
        else:
            print(f"    Probe totals: scholar_total={num_citations}, year_sum={probed_hist_total}, missing_from_histogram={probed_missing_from_histogram}", flush=True)
        print(f"    Cache summary: {self._format_year_count_summary(cached_year_counts)}", flush=True)
        print(f"    Cache totals: cached_total={cached_total_citations}, cached_year_sum={cached_year_total}, cached_unyeared={cached_unyeared_citations}, dedup_num={self._dedup_count}", flush=True)
        effective_target = probed_hist_total if histogram_authoritative else num_citations
        print(f"    Fetch context: mode={'incremental' if allow_incremental_early_stop else 'full-recheck'}, "
              f"probe_complete={self._probed_year_count_complete}, "
              f"prev_scholar={prev_scholar_count}, target={effective_target}, total_years={total_years}", flush=True)
        print(f"    Current-run completed years: {self._format_year_set_summary(self._completed_year_segments)}", flush=True)
        print(f"    Partial resume points: {self._format_partial_year_start_summary(self._partial_year_start)}", flush=True)
        if selective_refresh_years is None and probed_year_counts and allow_incremental_early_stop:
            selective_refresh_years = self._selective_refresh_candidate_years(
                cached_year_counts,
                probed_year_counts,
                year_range,
                partial_year_start=self._partial_year_start,
                probe_complete=can_skip_by_probe_counts,
            )
        if selective_refresh_years is not None and not selective_refresh_years and self._partial_year_start:
            selective_refresh_years = {int(year) for year in self._partial_year_start.keys()}
        effective_refresh_years = set(selective_refresh_years or ())
        effective_refresh_years.update(int(year) for year in self._partial_year_start.keys())
        suppress_target_reached = (
            can_skip_by_probe_counts
            and bool(self._partial_year_start)
            and cached_year_counts == probed_year_counts
        )
        stop_partial_resume_once_satisfied = (
            can_skip_by_probe_counts
            and bool(self._partial_year_start)
            and cached_year_counts == probed_year_counts
        )
        suppress_final_histogram_target_stop = (
            histogram_authoritative
            and can_skip_by_probe_counts
            and bool(self._partial_year_start)
            and cached_total_citations >= effective_target
        )
        if selective_refresh_years is None:
            print("    Selective refresh years: none", flush=True)
        else:
            print(f"    Selective refresh years: {self._format_year_set_summary(selective_refresh_years)}", flush=True)

        if can_skip_by_probe_counts and cached_year_counts == probed_year_counts and not self._partial_year_start:
            years_to_mark = [year for year in year_range if year not in self._completed_year_segments]
            if years_to_mark:
                self._completed_year_segments.update(years_to_mark)
            print(f"  Year fetch skipped: histogram-authoritative match (scholar_total={num_citations}, year_sum={probed_hist_total}, cached_total={cached_total_citations}, cached_year_sum={cached_year_total}, dedup_num={self._dedup_count})", flush=True)
            save_progress(complete=False)
            save_progress(complete=True)
            return current_citations(complete=True)

        target_reached_by_histogram = lambda: (
            effective_target is not None
            and effective_target > 0
            and current_count_for_stop_and_status() >= effective_target
            and not suppress_final_histogram_target_stop
        )

        try:
            for year in year_range:
                if selective_refresh_years is not None and year not in effective_refresh_years:
                    skipped_years += 1
                    if force_year_rebuild:
                        self._completed_year_segments.add(year)
                    print(f"      Year {year}: skip (not selected for refresh)", flush=True)
                    continue
                if year in self._completed_year_segments and year not in effective_refresh_years:
                    skipped_years += 1
                    print(f"      Year {year}: skip (already completed earlier in this run)", flush=True)
                    continue
                if can_skip_by_probe_counts and probed_year_counts.get(year) == 0:
                    skipped_years += 1
                    self._completed_year_segments.add(year)
                    print(f"      Year {year}: skip (probe count=0, probe_complete=True)", flush=True)
                    save_progress(complete=False)
                    continue
                if can_skip_by_probe_counts and year not in self._partial_year_start:
                    live_count = probed_year_counts.get(year)
                    cached_count = cached_year_counts.get(year)
                    if live_count is not None and cached_count == live_count:
                        skipped_years += 1
                        self._completed_year_segments.add(year)
                        print(f"      Year {year}: skip (histogram count match; cached={cached_count}, probe={live_count}, probe_complete=True)", flush=True)
                        save_progress(complete=False)
                        continue

                start_index = self._partial_year_start.get(year, 0)
                resuming_partial_year = year in self._partial_year_start
                cached_count = cached_year_counts.get(year)
                live_count = probed_year_counts.get(year)

                if not self.interactive_captcha:
                    self._refresh_scholarly_session()
                self._next_refresh_at = self._total_page_count + random.randint(SESSION_REFRESH_MIN, SESSION_REFRESH_MAX)

                if start_index > 0:
                    print(f"      Year {year}: resuming from position {start_index} "
                          f"(cached={cached_count if cached_count is not None else '?'}, "
                          f"probe={live_count if live_count is not None else '?'})", flush=True)
                else:
                    print(f"      Year {year}: fetching "
                          f"(cached={cached_count if cached_count is not None else '?'}, "
                          f"probe={live_count if live_count is not None else '?'})", flush=True)

                year_url = (f'/scholar?as_ylo={year}&as_yhi={year}&hl=en'
                            f'&as_sdt=2005&sciodt=0,5&cites={pub_id}&scipsc=')
                if start_index > 0:
                    year_url += f'&start={start_index}'
                print(f"      URL: https://scholar.google.com{year_url}", flush=True)
                nav = scholarly._Scholarly__nav

                year_new_count = 0
                year_items_seen = 0
                stop_after_current_page = False
                year_progress_saved = False
                existing_year_fresh = list(fresh_year_buckets.get(year, [])) if start_index > 0 else []
                year_seen_keys = {}
                for c in existing_year_fresh:
                    label = f"{c.get('title', '')[:50]} ({c.get('venue', 'N/A')}, {c.get('year', '?')}) [fresh]"
                    for key in self._citation_identity_keys(c):
                        year_seen_keys[key] = label
                year_fetched_citations = list(existing_year_fresh)

                while True:
                    resume_start = start_index + year_items_seen
                    if year_items_seen > 0:
                        year_url_cur = (f'/scholar?as_ylo={year}&as_yhi={year}&hl=en'
                                        f'&as_sdt=2005&sciodt=0,5&cites={pub_id}&scipsc='
                                        f'&start={resume_start}')
                        print(f"      Year {year}: retrying from position {resume_start}", flush=True)
                    else:
                        year_url_cur = year_url
                    try:
                        iterator = _SearchScholarIterator(nav, year_url_cur)
                        page_save_emitted = False
                        for citing in iterator:
                            year_items_seen += 1
                            self._partial_year_start[year] = start_index + year_items_seen
                            info = self._extract_citation_info(citing, fallback_year=year)
                            identity_keys = self._citation_identity_keys(info)
                            matched_key = next((key for key in identity_keys if key in year_seen_keys), None)
                            if matched_key is not None:
                                self._dedup_count += 1
                                print(f"  [dedup] Skipping duplicate: {info['title'][:50]}... ({info.get('venue', 'N/A')}, {info.get('year', '?')})"
                                      f"\n          Existing: {year_seen_keys[matched_key]}", flush=True)
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
                                    self._new_citations_count += 1
                                count = len(fresh_citations)

                                print(f"  [{count}] {info['title'][:55]}...", flush=True)

                            if getattr(iterator, '_finished_current_page', False) and not page_save_emitted:
                                save_progress(complete=False)
                                page_save_emitted = True
                                year_progress_saved = True
                        if year_items_seen > 0 and page_save_emitted:
                            continue
                        break
                    except KeyboardInterrupt:
                        save_progress(complete=False)
                        raise
                    except Exception as e:
                        now_s = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        print(f"  [{now_s}] Blocked at year {year} "
                              f"position {resume_start}: {e}", flush=True)
                        save_progress(complete=False)
                        if self.interactive_captcha:
                            cur_url = (f'https://scholar.google.com{year_url_cur}'
                                       if year_url_cur.startswith('/') else year_url_cur)
                            solved = self._try_interactive_captcha(cur_url)
                            if solved:
                                continue
                        raise

                fresh_year_buckets[year] = list(year_fetched_citations)
                fresh_citations[:] = current_citations(complete=True)
                self._partial_year_start.pop(year, None)
                self._completed_year_segments.add(year)
                if year_new_count > 0:
                    print(f"      Year {year} done: {year_new_count} new citations", flush=True)
                else:
                    print(f"      Year {year} done: no new citations", flush=True)
                print(f"      Year {year} status: paper_total={len(current_citations(complete=True))}, paper_new={paper_new_count}, "
                      f"pages={self._total_page_count}, skipped_years={skipped_years}", flush=True)
                if not year_progress_saved:
                    save_progress(complete=False)

                if stop_partial_resume_once_satisfied and resuming_partial_year and live_count is not None and len(year_fetched_citations) >= live_count:
                    print(f"  Year {year}: Completed resumed year segment, skipping remaining years after current year", flush=True)
                    break

        except KeyboardInterrupt:
            save_progress(complete=False)
            raise
        except Exception:
            save_progress(complete=False)
            raise

        fresh_citations[:] = current_citations(complete=True)
        save_progress(complete=True)
        return list(fresh_citations)

    def _save_xlsx(self, results, metadata=None):
        wb = openpyxl.Workbook()
        metadata = metadata or {}

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
            [45, 50, 35, 25, 10, 18, 55],
            ["Cited Paper", "Citing Paper Title", "Authors", "Venue", "Year", "Cites ID", "Link"]
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
                ws2.cell(row=row, column=6, value=cite.get('cites_id', 'N/A') or 'N/A').alignment = wrap
                url = cite['url']
                lc = ws2.cell(row=row, column=7, value=url)
                if url and url != 'N/A':
                    try:
                        lc.hyperlink = url
                        lc.font = Font(color="0563C1", underline="single")
                    except Exception:
                        pass
                lc.alignment = wrap
                ws2.row_dimensions[row].height = 32
                row += 1

        ws3 = wb.create_sheet("Run Metadata")
        ws3.column_dimensions['A'].width = 26
        ws3.column_dimensions['B'].width = 50
        for row, (key, value) in enumerate([
            ("Author ID", metadata.get('author_id', self.author_id)),
            ("Fetch Time", metadata.get('fetch_time', 'N/A')),
            ("Total Papers", metadata.get('total_papers', len(results))),
            ("Total Citations Collected", metadata.get('total_citations_collected', sum(len(item['citations']) for item in results))),
        ], 1):
            label = ws3.cell(row=row, column=1, value=key)
            label.fill, label.font, label.alignment = hdr_fill, hdr_font, center
            ws3.cell(row=row, column=2, value=value).alignment = wrap

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
        actual_cached = cached.get('num_citations_cached', len(cached.get('citations', [])))
        promoted_scholar_total = 0
        try:
            promoted_scholar_total = int(cached.get('num_citations_on_scholar', 0) or 0)
        except (TypeError, ValueError):
            promoted_scholar_total = 0
        if current <= promoted_scholar_total and actual_cached >= current:
            return 'complete'
        probed_year_counts = self._normalize_year_count_map(cached.get('probed_year_counts'))
        probe_complete = cached.get('probe_complete') is True and bool(probed_year_counts)
        if probe_complete:
            cached_year_counts = self._normalize_year_count_map(cached.get('cached_year_counts'))
            if not cached_year_counts:
                cached_year_counts = self._year_count_map(cached.get('citations', []))
            cached_hist_total = sum(cached_year_counts.values())
            probed_hist_total = cached.get('probed_year_total')
            try:
                probed_hist_total = int(probed_hist_total)
            except (TypeError, ValueError):
                probed_hist_total = sum(probed_year_counts.values())
            if cached_year_counts == probed_year_counts and current >= probed_hist_total:
                return 'complete'
            return 'partial'

        # Legacy fallback for caches without authoritative histogram metadata.
        num_seen = cached.get('num_citations_seen')
        if num_seen is not None:
            if num_seen >= current:
                return 'complete'
            return 'partial'

        actual_cached = cached.get('num_citations_cached', len(cached.get('citations', [])))
        if self.recheck_citations:
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
            attempt_state = self._resolve_refresh_strategy(pub, cached, st)
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
            fetch_policy = attempt_state.get('fetch_policy') or self._resolve_citation_fetch_policy(
                num_citations,
                pub.get('year', 'N/A'),
            )
            action = attempt_state['action']

            print(f"[{idx}/{len(publications)}] {title[:55]}...")
            print(f"  {action}")

            citations = None
            attempt = 0
            preserve_escalated_state_once = False
            fetch_completed = False
            post_fetch_retry_attempted = False
            while True:
                attempt += 1
                try:
                    if not self.interactive_captcha:
                        self._refresh_scholarly_session()
                    self._next_refresh_at = self._total_page_count + random.randint(SESSION_REFRESH_MIN, SESSION_REFRESH_MAX)
                    if attempt > 1:
                        if preserve_escalated_state_once:
                            preserve_escalated_state_once = False
                            print(f"  {now_str()} Retrying escalated full revalidation with in-memory state")
                        elif fetch_completed:
                            print(f"  {now_str()} Retrying post-fetch reconciliation with in-memory citations")
                        else:
                            # Reload citations and current-run completed years from file.
                            # partial_year_start is kept from memory (in-memory only, not persisted)
                            # so same-run retries resume from the exact page where the error occurred.
                            latest_cache = self._load_citation_cache(title)
                            if latest_cache:
                                resume_from = latest_cache.get('citations', [])
                                completed_years_in_current_run = latest_cache.get(
                                    'completed_years_in_current_run',
                                    latest_cache.get('completed_years', []),
                                )
                                saved_dedup_count = latest_cache.get('dedup_count', 0)
                                rehydrated_probed_year_counts, rehydrated_probe_complete = self._rehydrate_probe_metadata(
                                    latest_cache,
                                    num_citations,
                                )
                                partial_year_start = dict(self._partial_year_start)
                                print(f"  {now_str()} Retrying with {len(resume_from)} cached citations from previous attempt")
                    self._current_pub_for_live_promotion = pub
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
                            pub_obj=pub,
                            fetch_policy=fetch_policy,
                        )
                        fetch_completed = True
                    num_citations = pub['num_citations']
                    seen_total = len(citations) + self._dedup_count
                    dedup_str = f", {self._dedup_count} dupes" if self._dedup_count else ""
                    print(f"  Done: {len(citations)} cached, {seen_total} seen{dedup_str} (Scholar: {num_citations})")
                    self._current_pub_for_live_promotion = None
                    year_counts = self._year_count_map(citations)
                    if year_counts:
                        year_total = sum(year_counts.values())
                        unyeared = max(0, len(citations) - year_total)
                        year_summary = self._format_year_count_summary(year_counts)
                        unyeared_suffix = f", unyeared={unyeared}" if unyeared else ""
                        print(f"  Year summary: {year_summary}{unyeared_suffix}", flush=True)
                    if citations is not None:
                        refresh_status = self._refresh_reconciliation_status(
                            citations,
                            num_citations,
                            probed_year_counts=getattr(self, '_probed_year_counts', None),
                            probe_complete=getattr(self, '_probed_year_count_complete', False),
                        )
                        if not refresh_status['ok']:
                            print(f"  {self._refresh_log_message('Refresh check', refresh_status)}")
                            is_selective_refresh_attempt = bool(selective_refresh_years)
                            is_incomplete_histogram = (
                                refresh_status['reason'] == 'histogram_incomplete'
                                and not refresh_status.get('probe_complete', False)
                            )
                            if is_selective_refresh_attempt:
                                print("  Selective refresh reconciliation failed; recording current results without escalation", flush=True)
                                break
                            if is_incomplete_histogram:
                                print("  Histogram is incomplete; recording current results without escalation", flush=True)
                                break
                            fetch_policy = self._resolve_citation_fetch_policy(
                                num_citations,
                                pub.get('year', 'N/A'),
                            )
                            should_escalate = (
                                fetch_policy['mode'] == 'year'
                                and not force_year_rebuild
                                and refresh_status['reason'] != 'year_count_mismatch'
                            )
                            if should_escalate:
                                allow_incremental_early_stop = False
                                force_year_rebuild = True
                                completed_years_in_current_run = []
                                selective_refresh_years = None
                                partial_year_start = {}
                                rehydrated_probed_year_counts = None
                                rehydrated_probe_complete = False
                                fetch_completed = False
                                post_fetch_retry_attempted = False
                                citations = None
                                preserve_escalated_state_once = True
                                print(f"  {self._refresh_escalation_message(refresh_status)}")
                                print(f"  {now_str()} Retrying escalated full revalidation with in-memory state")
                                continue
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
                          f"total pages: {self._total_page_count}, "
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
        """Parse cookies and selected headers from a pasted cURL command.
        Cookies are set without domain restriction so they are sent regardless of
        which regional Scholar domain (e.g. .com.hk vs .com) is used.
        Selected allowlisted headers are stored so patched session rebuilds can
        reuse browser identity details from the pasted request while keeping the
        crawler's dynamic Referer handling intact.
        Returns the number of cookies injected, or 0 on failure.
        """
        m = (re.search(r"(?:-b|--cookie) '([^']+)'", curl_str) or
             re.search(r'(?:-b|--cookie) "([^"]+)"', curl_str))
        if not m:
            print("  (Could not find -b/--cookie '...' string in input)", flush=True)
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

        header_overrides = {}
        header_matches = re.findall(r"(?:-H|--header) '([^']+)'|(?:-H|--header) \"([^\"]+)\"", curl_str)
        for single_quoted, double_quoted in header_matches:
            header_line = (single_quoted or double_quoted or '').strip()
            if not header_line or ':' not in header_line:
                continue
            name, value = header_line.split(':', 1)
            header_name = name.strip().lower()
            if header_name in self._curl_header_allowlist:
                header_overrides[header_name] = value.strip()

        # Inject without domain so cookies apply to scholar.google.com AND any
        # regional variant (e.g. scholar.google.com.hk) after 302 redirects.
        for session in (nav._session1, nav._session2):
            for k, v in cookies.items():
                session.cookies.set(k, v)
            session.headers.update(header_overrides)
            session.headers['referer'] = self._last_scholar_url
        # Persist for re-application after scholarly recreates sessions on 403
        self._injected_cookies = cookies
        self._injected_header_overrides = header_overrides
        nav.got_403 = False
        self._captcha_solved_count += 1
        header_note = (f", {len(header_overrides)} allowlisted headers"
                       if header_overrides else '')
        print(f"  Injected {len(cookies)} cookies{header_note} (no domain restriction). "
              f"Captcha solves: {self._captcha_solved_count}", flush=True)
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
        print(f"  2. Solve the captcha, then let the page load fully", flush=True)
        print(f"  3. F12 → Network → find the Scholar request", flush=True)
        print(f"     → right-click → Copy as cURL (bash)", flush=True)
        print(f"  4. Paste the cURL here (cookies + selected headers reused; detected automatically after 3s silence)", flush=True)
        print(f"     (Press Enter on blank line to skip)", flush=True)
        print(f"{sep}", flush=True)
        print("  > ", end='', flush=True)

        import select as _sel, os as _os, re as _re
        _ANSI = _re.compile(r'\x1b(?:\[[0-9;?]*[a-zA-Z]|[()][AB012])')

        fd = sys.stdin.fileno()
        old_attrs = None
        try:
            import termios as _termios
            old_attrs = _termios.tcgetattr(fd)
        except Exception:
            pass

        chunks = []
        try:
            if old_attrs is not None:
                # Disable ICANON (line buffering) but keep ECHO so the user
                # can see what they paste.  This prevents the pty input buffer
                # from filling up (which would freeze SSH flow control) while
                # still giving visual feedback.
                new = list(old_attrs)
                new[3] = new[3] & ~_termios.ICANON
                new[6] = list(new[6])
                new[6][_termios.VMIN] = 1
                new[6][_termios.VTIME] = 0
                _termios.tcsetattr(fd, _termios.TCSAFLUSH, new)

            # Phase 1: wait indefinitely for first byte (user takes their time)
            _sel.select([sys.stdin], [], [])

            # Phase 2: drain all bytes; 3s of silence = paste is done
            while True:
                ready = _sel.select([sys.stdin], [], [], 3.0)[0]
                if not ready:
                    break
                try:
                    chunk = _os.read(fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                chunks.append(chunk.decode('utf-8', errors='replace'))
        except KeyboardInterrupt:
            if old_attrs is not None:
                import termios as _t
                _t.tcsetattr(fd, _t.TCSADRAIN, old_attrs)
            print(flush=True)
            return False
        finally:
            if old_attrs is not None:
                import termios as _t
                _t.tcsetattr(fd, _t.TCSADRAIN, old_attrs)

        print(flush=True)   # newline after pasted content
        raw = ''.join(chunks)
        raw = _ANSI.sub('', raw)
        raw = raw.replace('\r\n', '\n').replace('\r', '\n')

        lines = []
        for line in raw.split('\n'):
            line = line.rstrip()
            if not line.strip():
                if lines:
                    break   # blank line after content = user confirmed end
                continue
            lines.append(line)

        if not lines:
            print("  (Skipped — using automatic wait)", flush=True)
            return False
        print(f"  Received {len(lines)} lines. Processing...", flush=True)
        return self._inject_cookies_from_curl('\n'.join(lines)) > 0

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

    def _flush_publication_count_updates(self, profile, pubs_data):
        updates = getattr(self, '_updated_publication_counts', None) or {}
        if not updates:
            return

        profile_publications = profile.get('publications', [])
        cache_publications = pubs_data.get('publications', [])
        updated_titles = set()
        changed = False
        for publication in profile_publications:
            title = publication.get('title')
            if title in updates and updates[title] > int(publication.get('num_citations', 0) or 0):
                publication['num_citations'] = updates[title]
                updated_titles.add(title)
                changed = True
        for publication in cache_publications:
            title = publication.get('title')
            if title in updates and updates[title] > int(publication.get('num_citations', 0) or 0):
                publication['num_citations'] = updates[title]
                updated_titles.add(title)
                changed = True
        if not changed:
            return

        for result in getattr(self, '_results_in_progress', []) or []:
            if not result:
                continue
            publication = result.get('pub')
            if not publication:
                continue
            title = publication.get('title')
            if title in updated_titles and title in updates:
                publication['num_citations'] = updates[title]

        results_in_progress = getattr(self, '_results_in_progress', []) or []
        if results_in_progress and profile_publications:
            profile_by_title = {publication.get('title'): publication for publication in profile_publications}
            for result in results_in_progress:
                if not result:
                    continue
                publication = result.get('pub')
                if not publication:
                    continue
                synced_publication = profile_by_title.get(publication.get('title'))
                if synced_publication is not None:
                    result['pub'] = dict(synced_publication)

        self._resort_publications(profile_publications)
        self._resort_publications(cache_publications)
        profile['total_publications'] = len(profile_publications)

        basics = profile.get('author_info', {})
        change_history = profile.get('change_history', [])
        preserved_fetch_time = profile.get('fetch_time')
        rebuilt_profile = {
            'author_info': basics,
            'publications': profile_publications,
            'fetch_time': preserved_fetch_time,
            'total_publications': len(profile_publications),
            'total_citations': basics.get('citedby', 0),
            'citation_count_summary': AuthorProfileFetcher(self.author_id, self.output_dir)._build_profile_count_summary(basics),
            'change_history': change_history,
        }
        profile.clear()
        profile.update(rebuilt_profile)
        with open(self.profile_json, 'w', encoding='utf-8') as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
        AuthorProfileFetcher(self.author_id, self.output_dir).save_profile_xlsx(
            basics,
            profile_publications,
            change_history=change_history,
            fetch_time=preserved_fetch_time,
        )
        with open(self.pubs_cache, 'w', encoding='utf-8') as f:
            json.dump(pubs_data, f, ensure_ascii=False, indent=2)

    def _save_output(self, results):
        """Save citation results to JSON and Excel."""
        print("\n" + "=" * 70)
        profile = getattr(self, '_profile_data', None)
        pubs_data = getattr(self, '_pubs_data', None)
        self._results_in_progress = results
        if profile is None and os.path.exists(self.profile_json):
            with open(self.profile_json, 'r', encoding='utf-8') as f:
                profile = json.load(f)
        if pubs_data is None and os.path.exists(self.pubs_cache):
            with open(self.pubs_cache, 'r', encoding='utf-8') as f:
                pubs_data = json.load(f)
        if profile is not None and pubs_data is not None:
            self._flush_publication_count_updates(profile, pubs_data)
        publications = profile.get('publications', []) if profile else []
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
              f" | {self._total_page_count} pages accessed"
              f" | {self._new_citations_count} new citations"
              f" | output total = collected per-paper citation records\n")

        return True


# ============================================================
# CLI Entry Point
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description='Google Scholar Citation Crawler - fetch author profiles and paper citations',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''\
examples:
  python scholar_citation.py --author AUTHOR_ID
  python scholar_citation.py --author "https://scholar.google.com/citations?user=AUTHOR_ID"
  python scholar_citation.py --author AUTHOR_ID --limit 3
  python scholar_citation.py --author AUTHOR_ID --skip 10 --limit 5
  python scholar_citation.py --author AUTHOR_ID --force-refresh-citations
  python scholar_citation.py --author AUTHOR_ID --interactive-captcha
'''
    )
    parser.add_argument('--author', required=True,
                        help='Google Scholar author ID or full profile URL')
    parser.add_argument('--output-dir', default='./output', metavar='DIR',
                        help='Output directory (default: ./output)')
    parser.add_argument('--skip', type=int, default=0, metavar='M',
                        help='Skip the first M papers in the full list (sorted by citations desc)')
    parser.add_argument('--limit', type=int, default=None, metavar='N',
                        help='Process exactly N papers after --skip (papers M+1 to M+N), '
                             'regardless of whether each needs fetching')
    parser.add_argument('--force-refresh-pubs', action='store_true',
                        help='Force re-fetch the publications list from Scholar '
                             '(useful when profile updated but citations fetch was interrupted)')
    parser.add_argument('--recheck-citations', dest='recheck_citations', action='store_true',
                        help='Re-check citation completeness for papers in the selected range and '
                             're-fetch only those whose cached citations are incomplete relative to Scholar')
    parser.add_argument('--force-refresh-citations', dest='recheck_citations', action='store_true',
                        help='Deprecated alias for --recheck-citations')
    parser.add_argument('--interactive-captcha', action='store_true',
                        help='When blocked by Scholar, pause and prompt you to paste a browser '
                             'cURL (Chrome DevTools → Copy as cURL) to inject fresh cookies and '
                             'selected headers; retries indefinitely instead of giving up after '
                             'MAX_RETRIES')
    parser.add_argument('--accelerate', type=float, default=1.0, metavar='SCALE',
                        help='Scale all deliberate waits by SCALE. Example: --accelerate 0.1 '
                             'runs waits at 1/10 of the normal duration. Default: 1.0')
    return parser.parse_args()


def main():
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

        # Always run profile first
        delay_scale = args.accelerate if args.interactive_captcha else 1.0
        fetcher = AuthorProfileFetcher(author_id, args.output_dir, delay_scale=delay_scale)
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

            if prev_citations == curr_citations and prev_pubs == curr_pubs and not args.recheck_citations:
                # Even if totals haven't changed, check if all citations are fully cached
                citation_fetcher = PaperCitationFetcher(
                    author_id=author_id,
                    output_dir=args.output_dir,
                    limit=args.limit,
                    skip=args.skip,
                    recheck_citations=args.recheck_citations,
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
            recheck_citations=args.recheck_citations,
            interactive_captcha=args.interactive_captcha,
            delay_scale=delay_scale,
        )
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
