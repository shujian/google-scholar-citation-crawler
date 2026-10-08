"""
crawler/cli.py — Command-line argument parsing for the citation crawler.

The program entry point is ``scholar_citation.py: main()``; this module defines
only the parser, so it can be imported (and tested) without pulling in
``scholarly``.
"""

import argparse


def parse_args(argv=None):
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
    parser.add_argument('--fetch-mode', dest='fetch_mode',
                        choices=['rough', 'normal', 'exact', 'force'], default='normal',
                        help='Controls citation re-fetch aggressiveness. '
                             'rough: skip papers whose Scholar count has not changed AND whose '
                             'last fetch ran to completion. '
                             'normal (default): fetch papers with missing or incomplete caches. '
                             'exact: like normal, but also re-fetch years whose cached count '
                             'exceeds the probe histogram (seen > probe). '
                             'force: clear output state and re-fetch from scratch; '
                             'recommended with --skip/--limit to limit scope.')
    parser.add_argument('--interactive-captcha', action='store_true',
                        help='When blocked by Scholar, pause and prompt you to paste a browser '
                             'cURL (Chrome DevTools → Copy as cURL) to inject fresh cookies and '
                             'selected headers; retries indefinitely instead of giving up after '
                             'MAX_RETRIES')
    parser.add_argument('--accelerate', type=float, default=1.0, metavar='SCALE',
                        help='Scale all deliberate waits by SCALE. Example: --accelerate 0.1 '
                             'runs waits at 1/10 of the normal duration. Default: 1.0. '
                             'Shortening waits makes Scholar rate-limit sooner; '
                             '--interactive-captcha is recommended when using it')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Enable debug-level logging for troubleshooting')
    args = parser.parse_args(argv)

    if args.skip < 0:
        parser.error('--skip must be >= 0')
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be >= 1 (omit --limit to process every paper)')
    if args.accelerate <= 0:
        parser.error('--accelerate must be > 0')
    return args
