"""
crawler/cli.py — Command-line interface: argument parsing and main() entry point.

Run via:  python scholar_citation.py --author AUTHOR_ID
"""

import argparse
import os
import sys
import traceback
from datetime import datetime

from crawler.common import extract_author_id, TeeStream, setup_proxy
from crawler.author_fetcher import AuthorProfileFetcher
# PaperCitationFetcher is imported lazily inside _run_main() to avoid a
# circular import: scholar_citation imports crawler.cli, and if crawler.cli
# imported scholar_citation at module level the two would deadlock.

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
  python scholar_citation.py --author AUTHOR_ID --fetch-mode rough
  python scholar_citation.py --author AUTHOR_ID --fetch-mode force --skip 0 --limit 5
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
    parser.add_argument('--fetch-mode', dest='fetch_mode',
                        choices=['rough', 'normal', 'force'], default='normal',
                        help='Controls citation re-fetch aggressiveness. '
                             'rough: skip papers whose Scholar count has not changed since last fetch, '
                             'even if the cache is incomplete. '
                             'normal (default): fetch papers with missing or incomplete caches. '
                             'force: delete the cache and re-fetch from scratch; '
                             'recommended with --skip/--limit to limit scope.')
    parser.add_argument('--interactive-captcha', action='store_true',
                        help='When blocked by Scholar, pause and prompt you to paste a browser '
                             'cURL (Chrome DevTools → Copy as cURL) to inject fresh cookies and '
                             'selected headers; retries indefinitely instead of giving up after '
                             'MAX_RETRIES')
    parser.add_argument('--accelerate', type=float, default=1.0, metavar='SCALE',
                        help='Scale all deliberate waits by SCALE. Example: --accelerate 0.1 '
                             'runs waits at 1/10 of the normal duration. Default: 1.0')
    return parser.parse_args()


def _run_main(args):
    """Execute the main crawl workflow given a parsed args namespace."""
    from scholar_citation import PaperCitationFetcher  # lazy to avoid circular import
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

            if prev_citations == curr_citations and prev_pubs == curr_pubs and args.fetch_mode != 'force':
                # Even if totals haven't changed, check if all citations are fully cached
                citation_fetcher = PaperCitationFetcher(
                    author_id=author_id,
                    output_dir=args.output_dir,
                    limit=args.limit,
                    skip=args.skip,
                    fetch_mode=args.fetch_mode,
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
            fetch_mode=args.fetch_mode,
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


def main():
    """Convenience entry point that calls parse_args() then _run_main()."""
    _run_main(parse_args())
