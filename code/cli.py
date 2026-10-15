"""
code/cli.py — Command-line argument parsing for the citation crawler.

The program entry point is ``code/scholar_citation.py: main()``, run as
``python -m code.scholar_citation``; this module defines only the parser, so it
can be imported (and tested) without pulling in ``scholarly``.

Configuration priority for every knob: command-line argument > JSON config file
(``crawler_config.json``, see ``code.config``) > code default.  No environment
variable is required by this program.
"""

import argparse


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog='python -m code.scholar_citation',
        description='Google Scholar Citation Crawler - fetch author profiles and paper citations',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''\
examples:
  python -m code.scholar_citation --author AUTHOR_ID
  python -m code.scholar_citation --author "https://scholar.google.com/citations?user=AUTHOR_ID"
  python -m code.scholar_citation --author AUTHOR_ID --limit 3
  python -m code.scholar_citation --author AUTHOR_ID --skip 10 --limit 5
  python -m code.scholar_citation --author AUTHOR_ID --fetch-mode rough
  python -m code.scholar_citation --author AUTHOR_ID --fetch-mode force --skip 0 --limit 5
  python -m code.scholar_citation --author AUTHOR_ID --interactive-captcha
  python -m code.scholar_citation --author AUTHOR_ID --cite-formats --skip 0 --limit 1
  python -m code.scholar_citation --author AUTHOR_ID --proxy-url http://127.0.0.1:7890
'''
    )
    parser.add_argument('--author', required=True,
                        help='Google Scholar author ID or full profile URL')
    parser.add_argument('--output-dir', default='./output', metavar='DIR',
                        help='Output directory (default: ./output)')
    parser.add_argument('--config', default=None, metavar='PATH',
                        help='JSON config file (default: crawler_config.json in the project '
                             'root; a missing default file is ignored)')
    parser.add_argument('--proxy-url', dest='proxy_url', default=None, metavar='URL',
                        help='Proxy URL, e.g. http://127.0.0.1:7890; overrides the config file. '
                             'Pass an empty string to connect directly. Default: '
                             'crawler_config.json, else the system https_proxy/http_proxy')
    parser.add_argument('--no-proxy', dest='no_proxy', default=None, metavar='LIST',
                        help='Comma-separated proxy bypass list, e.g. localhost,127.0.0.1,::1; '
                             'overrides the config file. Default: crawler_config.json, '
                             'else the system no_proxy')
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
    parser.add_argument('--cite-formats', dest='cite_formats', action='store_true',
                        default=None,
                        help='Resolve each citing entry\'s five citation formats '
                             '(MLA/APA/Chicago/Harvard/Vancouver) plus BibTeX from Scholar\'s '
                             'Cite dialog and store them in citation["cite"]. This is ON by '
                             'default (it is the only source of full author names and the '
                             'untruncated venue for a citing paper); pass --no-cite-formats '
                             'to skip it. Costs one request per citing entry (plus one for '
                             'BibTeX), cached in output/cite_format_cache.json across runs')
    parser.add_argument('--renumber-papers', dest='renumber_papers', default=None,
                        choices=['off', 'dry-run', 'apply'],
                        help='Papers-directory maintenance (the stage-1 side of the papers '
                             'tree): off, dry-run (default: report only) or apply -- also '
                             'creates missing directories with their paper_meta.json, renames '
                             'the ones whose index moved and rewrites progress.json')
    parser.add_argument('--no-cite-formats', dest='cite_formats', action='store_false',
                        default=None,
                        help='Skip the Cite-dialog step above (faster, but citation["cite"] '
                             'stays empty, so citing entries keep the citation-list page\'s '
                             'abbreviated authors and truncated venue')
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
