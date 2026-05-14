import json
import os
from dataclasses import dataclass, field
from datetime import datetime

from crawler.pub_info import PubInfo


@dataclass
class AuthorProfile:
    """Complete author profile as stored in author_<ID>_profile.json.

    Encapsulates author info, publications list, change history, and
    all derived summary fields.  Replaces the previous loose dicts passed
    among fetch_basics / fetch_publications / save_profile_json / append_history.
    """

    author_info: dict = field(default_factory=dict)
    publications: list = field(default_factory=list)
    fetch_time: str = ""
    change_history: list = field(default_factory=list)

    # -- computed properties ------------------------------------------------

    @property
    def total_publications(self):
        return len(self.publications)

    @property
    def total_citations(self):
        return self.author_info.get('citedby', 0)

    @property
    def citation_count_summary(self):
        return build_profile_count_summary(self.author_info)

    # -- serialisation ------------------------------------------------------

    @classmethod
    def from_dict(cls, d):
        """Construct from a profile JSON dict.  Publications are normalised
        through PubInfo so old formats are upgraded transparently."""
        if not isinstance(d, dict):
            return cls()
        pubs_raw = d.get('publications', [])
        publications = [PubInfo.from_dict(p).to_dict() for p in pubs_raw]
        return cls(
            author_info=dict(d.get('author_info', {})),
            publications=publications,
            fetch_time=d.get('fetch_time', ''),
            change_history=list(d.get('change_history', [])),
        )

    def to_dict(self):
        """Produce the dict written to author_<ID>_profile.json."""
        resolved_time = self.fetch_time or datetime.now().isoformat()
        return {
            'author_info': self.author_info,
            'publications': self.publications,
            'fetch_time': resolved_time,
            'total_publications': self.total_publications,
            'total_citations': self.total_citations,
            'citation_count_summary': self.citation_count_summary,
            'change_history': self.change_history,
        }

    @classmethod
    def load(cls, path):
        """Load from a profile JSON file, or None."""
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # Migrate legacy history.json if change_history is missing
            if 'change_history' not in data:
                dirname = os.path.dirname(path)
                basename = os.path.basename(path)
                author_id = basename.rsplit('_profile.json', 1)[0]
                history_json = os.path.join(dirname, f"{author_id}_history.json")
                if os.path.exists(history_json):
                    with open(history_json, 'r', encoding='utf-8') as fh:
                        data['change_history'] = json.load(fh)
            return cls.from_dict(data)
        return None

    def save_json(self, path, print_fn=None):
        """Write the profile to a JSON file."""
        payload = self.to_dict()
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        if print_fn:
            print_fn(f'Saved JSON: {path}')
        return payload

    # -- history ------------------------------------------------------------

    def append_history(self, prev_profile=None):
        """Compare against *prev_profile* (an AuthorProfile or None) and
        append a change record to self.change_history.  Returns the record."""
        record = {
            'fetch_time': self.fetch_time or datetime.now().isoformat(),
            'citedby': self.author_info.get('citedby', 0),
            'citedby_this_year': self.author_info.get('citedby_this_year', 0),
            'hindex': self.author_info.get('hindex', 0),
            'i10index': self.author_info.get('i10index', 0),
            'total_publications': self.total_publications,
            'new_papers': [],
            'changed_citations': [],
        }

        if prev_profile is not None:
            prev_pubs = {p['title']: p['num_citations'] for p in prev_profile.publications}
            prev_titles = set(prev_pubs.keys())
            curr_titles = set(p['title'] for p in self.publications)

            record['new_papers'] = sorted(curr_titles - prev_titles)

            changed = []
            for pub in self.publications:
                title = pub['title']
                if title in prev_pubs:
                    old_cite = prev_pubs[title]
                    new_cite = pub['num_citations']
                    if new_cite != old_cite:
                        changed.append({'title': title, 'old': old_cite, 'new': new_cite})
            record['changed_citations'] = changed

            # Print summary
            new_papers = record['new_papers']
            changed_citations = record['changed_citations']
            if new_papers:
                print(f"\nNew papers ({len(new_papers)}):")
                for t in new_papers[:5]:
                    print(f"  + {t[:70]}")
                if len(new_papers) > 5:
                    print(f"  ... {len(new_papers)} total")
            if changed_citations:
                print(f"\nCitation changes ({len(changed_citations)}):")
                for c in changed_citations:
                    print(f"  {c['title'][:60]}... {c['old']} -> {c['new']}")
            if not new_papers and not changed_citations:
                print("  (No changes in this run)")

        self.change_history.append(record)
        return record


def build_profile_count_summary(basics):
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


def save_profile_xlsx(
    profile_xlsx_path,
    profile,
    *,
    datetime_module,
    openpyxl_module,
    font_cls,
    pattern_fill_cls,
    alignment_cls,
    print_fn=print,
):
    """Save Excel file with 3 sheets: overview, publications, and history.

    *profile* is an AuthorProfile instance.
    """
    basics = profile.author_info
    publications = profile.publications
    change_history = profile.change_history
    count_summary = profile.citation_count_summary
    wb = openpyxl_module.Workbook()

    display_fetch_time = profile.fetch_time
    if not display_fetch_time:
        now = datetime_module.now()
        display_fetch_time = now.isoformat() if hasattr(now, 'isoformat') else str(now)

    ws1 = wb.active
    ws1.title = 'Author Overview'

    title_fill = pattern_fill_cls(start_color='2F75B6', end_color='2F75B6', fill_type='solid')
    title_font = font_cls(bold=True, color='FFFFFF', size=13)
    header_fill = pattern_fill_cls(start_color='4472C4', end_color='4472C4', fill_type='solid')
    header_font = font_cls(bold=True, color='FFFFFF', size=11)
    label_fill = pattern_fill_cls(start_color='D9E1F2', end_color='D9E1F2', fill_type='solid')
    label_font = font_cls(bold=True, size=11)
    center = alignment_cls(horizontal='center', vertical='center')
    left = alignment_cls(horizontal='left', vertical='center', wrap_text=True)

    ws1.column_dimensions['A'].width = 22
    ws1.column_dimensions['B'].width = 55

    row = 1
    ws1.merge_cells(f'A{row}:B{row}')
    cell = ws1.cell(row=row, column=1, value='Google Scholar Author Overview')
    cell.fill = title_fill
    cell.font = title_font
    cell.alignment = center
    ws1.row_dimensions[row].height = 30
    row += 1

    info_items = [
        ('Name', basics.get('name', 'N/A')),
        ('Affiliation', basics.get('affiliation', 'N/A')),
        ('Research Interests', ', '.join(basics.get('interests', [])) or 'N/A'),
        ('Scholar ID', basics.get('scholar_id', 'N/A')),
        ('Fetch Time', display_fetch_time.replace('T', ' ')[:19]),
    ]

    for label, value in info_items:
        label_cell = ws1.cell(row=row, column=1, value=label)
        label_cell.fill = label_fill
        label_cell.font = label_font
        label_cell.alignment = center
        value_cell = ws1.cell(row=row, column=2, value=value)
        value_cell.alignment = left
        ws1.row_dimensions[row].height = 22
        row += 1

    row += 1

    ws1.merge_cells(f'A{row}:B{row}')
    cell = ws1.cell(row=row, column=1, value='Citation Statistics')
    cell.fill = header_fill
    cell.font = header_font
    cell.alignment = center
    ws1.row_dimensions[row].height = 24
    row += 1

    stats_items = [
        ('Total Citations (Scholar profile)', basics.get('citedby', 0)),
        ('Year-table subtotal (cites_per_year)', count_summary['year_table_total_citations']),
        ('Year-table gap vs total', count_summary['year_table_gap']),
        (f'Citations This Year ({datetime_module.now().year})', basics.get('citedby_this_year', 0)),
        ('Citations (5-year)', basics.get('citedby5y', 0)),
        ('h-index', basics.get('hindex', 0)),
        ('h-index (5-year)', basics.get('hindex5y', 0)),
        ('i10-index', basics.get('i10index', 0)),
        ('i10-index (5-year)', basics.get('i10index5y', 0)),
        ('Total Publications', len(publications)),
    ]

    for label, value in stats_items:
        label_cell = ws1.cell(row=row, column=1, value=label)
        label_cell.fill = label_fill
        label_cell.font = label_font
        label_cell.alignment = center
        value_cell = ws1.cell(row=row, column=2, value=value)
        value_cell.alignment = center
        ws1.row_dimensions[row].height = 22
        row += 1

    row += 1

    ws1.merge_cells(f'A{row}:B{row}')
    cell = ws1.cell(row=row, column=1, value='Citations Per Year (Scholar cites_per_year)')
    cell.fill = header_fill
    cell.font = header_font
    cell.alignment = center
    ws1.row_dimensions[row].height = 24
    row += 1

    ws1.merge_cells(f'A{row}:B{row}')
    note_cell = ws1.cell(row=row, column=1, value=count_summary['year_table_note'])
    note_cell.alignment = left
    ws1.row_dimensions[row].height = 34
    row += 1

    cites_per_year = basics.get('cites_per_year', {})
    for year in sorted(cites_per_year.keys(), reverse=True):
        label_cell = ws1.cell(row=row, column=1, value=str(year))
        label_cell.alignment = center
        value_cell = ws1.cell(row=row, column=2, value=cites_per_year[year])
        value_cell.alignment = center
        ws1.row_dimensions[row].height = 20
        row += 1

    ws2 = wb.create_sheet('Publications')
    ws2.column_dimensions['A'].width = 6
    ws2.column_dimensions['B'].width = 55
    ws2.column_dimensions['C'].width = 12
    ws2.column_dimensions['D'].width = 25
    ws2.column_dimensions['E'].width = 20
    ws2.column_dimensions['F'].width = 12
    ws2.column_dimensions['G'].width = 50

    headers2 = ['No.', 'Title', 'Year', 'Venue', 'Authors', 'Citations', 'Link']
    for col, header in enumerate(headers2, 1):
        cell = ws2.cell(row=1, column=col, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center
    ws2.row_dimensions[1].height = 28

    content_align = alignment_cls(vertical='center', wrap_text=True)
    for pub in publications:
        row_index = pub['no'] + 1
        ws2.cell(row=row_index, column=1, value=pub['no']).alignment = center
        ws2.cell(row=row_index, column=2, value=pub['title']).alignment = content_align
        ws2.cell(row=row_index, column=3, value=pub['year'] or 'N/A').alignment = center
        ws2.cell(row=row_index, column=4, value=pub['venue'] or 'N/A').alignment = content_align
        ws2.cell(row=row_index, column=5, value=pub['authors'] or 'N/A').alignment = content_align
        ws2.cell(row=row_index, column=6, value=pub['num_citations']).alignment = center

        url = pub.get('url') or 'N/A'
        link_cell = ws2.cell(row=row_index, column=7, value=url)
        if url and url != 'N/A':
            try:
                link_cell.hyperlink = url
                link_cell.font = font_cls(color='0563C1', underline='single')
            except Exception:
                pass
        link_cell.alignment = content_align
        ws2.row_dimensions[row_index].height = 40

    ws3 = wb.create_sheet('Change History')
    ws3.column_dimensions['A'].width = 22
    ws3.column_dimensions['B'].width = 14
    ws3.column_dimensions['C'].width = 16
    ws3.column_dimensions['D'].width = 12
    ws3.column_dimensions['E'].width = 12
    ws3.column_dimensions['F'].width = 14
    ws3.column_dimensions['G'].width = 12
    ws3.column_dimensions['H'].width = 50

    headers3 = [
        'Fetch Time',
        'Total Citations',
        'Citations This Year',
        'h-index',
        'i10-index',
        'Total Papers',
        'New Papers',
        'New Paper Titles (top 3)',
    ]
    for col, header in enumerate(headers3, 1):
        cell = ws3.cell(row=1, column=col, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center
    ws3.row_dimensions[1].height = 28

    history = change_history or []
    for row_index, record in enumerate(history, 2):
        new_titles = '; '.join(record.get('new_papers', [])[:3])
        if len(record.get('new_papers', [])) > 3:
            new_titles += f" ... (+{len(record['new_papers']) - 3})"

        ws3.cell(row=row_index, column=1, value=record.get('fetch_time', 'N/A')).alignment = center
        ws3.cell(row=row_index, column=2, value=record.get('citedby', 0)).alignment = center
        ws3.cell(row=row_index, column=3, value=record.get('citedby_this_year', 0)).alignment = center
        ws3.cell(row=row_index, column=4, value=record.get('hindex', 0)).alignment = center
        ws3.cell(row=row_index, column=5, value=record.get('i10index', 0)).alignment = center
        ws3.cell(row=row_index, column=6, value=record.get('total_publications', 0)).alignment = center
        ws3.cell(row=row_index, column=7, value=len(record.get('new_papers', []))).alignment = center
        ws3.cell(row=row_index, column=8, value=new_titles).alignment = alignment_cls(vertical='center', wrap_text=True)
        ws3.row_dimensions[row_index].height = 22

    wb.save(profile_xlsx_path)
    print_fn(f'Saved Excel: {profile_xlsx_path}')
    return wb
