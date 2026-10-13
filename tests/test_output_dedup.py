"""Output-stage dedup must only collapse rows Scholar itself calls the same.

K123: when Scholar lists one title under two clusters (typically the arXiv
preprint and the published version, with different ``cites_id``), the pipeline
records two entries and every stage keeps them apart.  The output-stage dedup
used to add an unconditional normalised-title key, which merged exactly those
rows and silently dropped the extra entry.  These tests pin the stricter rule:

* real ``cites_id`` equal  -> one row;
* real ``cites_id`` different, or missing on either side -> both rows;
* both rows without a real id -> they collapse only when ``title + venue``
  (or ``title + authors``) are fully equal.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import FetcherTestCase  # noqa: E402


def cit(title, cites_id=None, venue='', authors='', year='2024', url=''):
    """Build a minimal citation row as the crawler stores it."""
    return {
        'title': title,
        'cites_id': cites_id,
        'venue': venue,
        'authors': authors,
        'year': year,
        'url': url,
    }


def norm(value):
    """Lower-case and collapse whitespace, the way identity parts are compared."""
    return ' '.join(str(value or '').strip().lower().split())


def independent_meta_key(row):
    """title+venue / title+authors / title — reimplemented test-side on purpose."""
    title = norm(row.get('title'))
    venue = norm(row.get('venue'))
    authors = norm(row.get('authors'))
    if venue:
        return ('meta', title, venue)
    if authors:
        return ('meta', title, authors)
    return ('meta', title, None)


def has_real_cites_id(row):
    value = row.get('cites_id')
    if isinstance(value, (list, tuple, set)):
        value = ','.join(str(part) for part in value)
    text = '' if value is None else str(value).strip()
    return bool(text) and text.lower() not in ('none', 'null', 'n/a', 'na')


class OutputDedupIdentityTest(FetcherTestCase):
    def test_same_title_with_different_real_cites_ids_keeps_both_rows(self):
        rows = [
            cit('DuPO: Enabling Reliable LLM Self-Verification',
                '12264350754761864216', venue='arXiv preprint', year='2025',
                url='https://arxiv.org/abs/2508.14460'),
            cit('DuPO: Enabling Reliable Self-Verification',
                '9876543210987654321', venue='ICLR', year='2026',
                url='https://proceedings.iclr.cc/x'),
        ]
        out = self.fetcher._dedup_citations_for_output(rows, 'source paper')
        self.assertEqual(len(out), 2)
        self.assertEqual(
            {r['cites_id'] for r in out},
            {'12264350754761864216', '9876543210987654321'},
        )

    def test_same_real_cites_id_written_twice_collapses_to_one_row(self):
        rows = [
            cit('DuPO: Enabling Reliable LLM Self-Verification',
                '12264350754761864216', venue='arXiv preprint', year='2025'),
            cit('DuPO: Enabling Reliable Self-Verification',
                '12264350754761864216', venue='ICLR', year='2026',
                url='https://proceedings.iclr.cc/x'),
        ]
        out = self.fetcher._dedup_citations_for_output(rows, 'source paper')
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['cites_id'], '12264350754761864216')
        # The row with the richer metadata is the one that survives.
        self.assertEqual(out[0]['venue'], 'ICLR')

    def test_rows_without_real_id_need_full_metadata_equality_to_collapse(self):
        rows = [
            cit('Survey on Aspect Term Extraction', None,
                venue='Journal of Software', authors='QT XU, Y HONG', year='2023'),
            # same title, different venue -> different work for us, keep both
            cit('Survey on Aspect Term Extraction', None,
                venue='Other Journal', authors='QT XU, Y HONG', year='2022'),
            # same title, no venue, different authors -> keep both
            cit('Attention Is All You Need', None, authors='A Vaswani, N Shazeer'),
            cit('Attention Is All You Need', None, authors='Someone Else'),
        ]
        out = self.fetcher._dedup_citations_for_output(rows, 'source paper')
        self.assertEqual(len(out), 4)

        # Fully equal title+venue collapses.
        equal_venue = [
            cit('Survey on Aspect Term Extraction', None,
                venue='Journal of Software', authors='QT XU', year='2023'),
            cit('Survey on Aspect Term Extraction', None,
                venue='Journal of Software', authors='QT XU', year='2023'),
        ]
        self.assertEqual(
            len(self.fetcher._dedup_citations_for_output(equal_venue, 'src')), 1)

        # Fully equal title+authors (no venue at all) collapses too.
        equal_authors = [
            cit('Attention Is All You Need', None, authors='A Vaswani'),
            cit('Attention Is All You Need', None, authors='A Vaswani'),
        ]
        self.assertEqual(
            len(self.fetcher._dedup_citations_for_output(equal_authors, 'src')), 1)

    def test_real_id_row_is_never_merged_against_an_id_less_row(self):
        rows = [
            cit('Some Work', '123456', venue='Journal of Software'),
            cit('Some Work', None, venue='Journal of Software'),
        ]
        out = self.fetcher._dedup_citations_for_output(rows, 'source paper')
        self.assertEqual(len(out), 2)

    def test_identity_keys_use_cites_id_only_when_a_real_id_exists(self):
        with_id = self.fetcher._citation_output_identity_keys(
            cit('Some Work', '123456', venue='Journal of Software'))
        self.assertEqual(with_id, ['cites_id\t123456'])

        without_id = self.fetcher._citation_output_identity_keys(
            cit('Some Work', None, venue='Journal of Software'))
        self.assertEqual(without_id, ['meta\tsome work\tjournal of software'])
        # A normalised title is never an identity key on its own for id-less rows
        self.assertFalse(any(k.startswith('title\t') for k in without_id))


class OutputDedupSnapshotRegressionTest(FetcherTestCase):
    """Read-only regression against the real stage-1 snapshot, when present.

    The file lives outside this repository (stage 2 owns it), so the test skips
    on machines that do not have the workspaces checked out.
    """

    SNAPSHOT = (Path(__file__).resolve().parents[2]
                / 'paper_downloader' / 'main' / 'paper_citations.json')

    def test_real_snapshot_only_collapses_rows_with_the_same_real_cites_id(self):
        if not self.SNAPSHOT.exists():
            self.skipTest(f'stage-1 snapshot not present: {self.SNAPSHOT}')

        with open(self.SNAPSHOT, encoding='utf-8') as handle:
            data = json.load(handle)

        total_in = 0
        total_out = 0
        idless_in = 0
        idless_out = 0
        expected_out = 0
        for paper in data.get('papers', []):
            rows = paper.get('citations') or []
            total_in += len(rows)

            real_ids = set()
            meta_keys = set()
            for row in rows:
                if has_real_cites_id(row):
                    real_ids.add(str(row['cites_id']).strip())
                else:
                    idless_in += 1
                    meta_keys.add(independent_meta_key(row))
            expected_out += len(real_ids) + len(meta_keys)

            kept = self.fetcher._dedup_citations_for_output(
                rows, (paper.get('pub') or {}).get('title', ''))
            total_out += len(kept)
            idless_out += sum(1 for row in kept if not has_real_cites_id(row))

        print(f'\n[snapshot regression] {total_in} in -> {total_out} out '
              f'(collapsed {total_in - total_out}; legacy title-key rule '
              f'collapsed 229 and produced 8971)')

        self.assertEqual(
            total_out, expected_out,
            f'{total_in} in -> {total_out} out; expected {expected_out} '
            f'(distinct real cites_id + distinct id-less title+venue/authors)',
        )
        # No row without a real cites_id may disappear.
        self.assertEqual(idless_out, idless_in)
        # The legacy title-key dedup produced 8971 rows on this snapshot
        # (9200 in, 229 collapsed); coming anywhere near that means the
        # unconditional title key came back.
        self.assertGreater(
            total_out, total_in - 100,
            f'title-only merging looks reintroduced: {total_in} in -> {total_out} out',
        )
