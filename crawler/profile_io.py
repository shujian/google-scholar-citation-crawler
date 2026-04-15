import json


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


def build_profile_payload(basics, publications, change_history=None, fetch_time=None, *, datetime_module):
    resolved_fetch_time = fetch_time or datetime_module.now().isoformat()
    return {
        'author_info': basics,
        'publications': publications,
        'fetch_time': resolved_fetch_time,
        'total_publications': len(publications),
        'total_citations': basics.get('citedby', 0),
        'citation_count_summary': build_profile_count_summary(basics),
        'change_history': change_history or [],
    }


def save_profile_json(profile_path, basics, publications, change_history=None, fetch_time=None, *, datetime_module, print_fn=print):
    profile = build_profile_payload(
        basics,
        publications,
        change_history=change_history,
        fetch_time=fetch_time,
        datetime_module=datetime_module,
    )
    with open(profile_path, 'w', encoding='utf-8') as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)
    print_fn(f'Saved JSON: {profile_path}')
    return profile


def save_profile_xlsx(
    profile_xlsx_path,
    basics,
    publications,
    change_history=None,
    fetch_time=None,
    *,
    datetime_module,
    openpyxl_module,
    font_cls,
    pattern_fill_cls,
    alignment_cls,
    print_fn=print,
):
    """Save Excel file with 3 sheets: overview, publications, and history."""
    count_summary = build_profile_count_summary(basics)
    wb = openpyxl_module.Workbook()

    display_fetch_time = fetch_time
    if display_fetch_time is None:
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
    ws2.column_dimensions['E'].width = 12
    ws2.column_dimensions['F'].width = 50

    headers2 = ['No.', 'Title', 'Year', 'Venue', 'Citations', 'Link']
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
        ws2.cell(row=row_index, column=3, value=pub['year']).alignment = center
        ws2.cell(row=row_index, column=4, value=pub['venue']).alignment = content_align
        ws2.cell(row=row_index, column=5, value=pub['num_citations']).alignment = center

        url = pub.get('url', 'N/A')
        link_cell = ws2.cell(row=row_index, column=6, value=url)
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
