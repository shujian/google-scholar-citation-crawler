"""
crawler/scholarly_session.py — scholarly HTTP session setup and year probe.

patch_scholarly() monkey-patches scholarly internals to:
  - use HTTP/2 sessions with browser-like headers
  - enforce randomised delays and mandatory long breaks
  - track pagination and update Referer dynamically
  - support soft session refresh and year-segment skipping

_probe_citation_start_year() fetches the base citedby page once to read the
per-year histogram and determine the earliest year with citations.

Both functions accept a SessionContext dataclass so they do not need to access
a PaperCitationFetcher instance.  PaperCitationFetcher keeps thin wrapper
methods for backward compatibility with existing call sites.
"""

import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from scholarly import scholarly
from scholarly.publication_parser import _SearchScholarIterator

from crawler.common import (
    MANDATORY_BREAK_EVERY_MAX,
    MANDATORY_BREAK_EVERY_MIN,
    MANDATORY_BREAK_MAX,
    MANDATORY_BREAK_MIN,
    MAX_RETRIES,
    SESSION_REFRESH_MAX,
    SESSION_REFRESH_MIN,
    _scholar_request_url,
    now_str,
    rand_delay,
)
from scholarly._proxy_generator import MaxTriesExceededException


# ---------------------------------------------------------------------------
# Session context (shared mutable state between closures)
# ---------------------------------------------------------------------------

@dataclass
class SessionContext:
    """
    Mutable state that is shared between the scholarly patch closures and
    the rest of PaperCitationFetcher.

    The fetcher creates one SessionContext at startup and passes it to
    patch_scholarly().  All closures capture ctx (not the fetcher) so this
    module does not depend on PaperCitationFetcher.
    """
    author_id: str
    delay_scale: float = 1.0
    interactive_captcha: bool = False

    # pagination / session health
    total_page_count: int = 0
    current_paper_page_count: int = 0  # resets per paper, not per year-segment or retry
    next_break_at: int = 0
    next_refresh_at: int = 0
    last_scholar_url: str = ""
    current_attempt_url: Optional[str] = None

    # injected cookies / headers (populated by _inject_cookies_from_curl)
    injected_cookies: dict = field(default_factory=dict)
    injected_header_overrides: dict = field(default_factory=dict)
    curl_header_allowlist: frozenset = field(default_factory=frozenset)

    # year-segment tracking (per-paper, reset before each fetch)
    current_year_segment: Optional[int] = None
    completed_year_segments: set = field(default_factory=set)

    # per-paper state needed during probe
    probed_year_counts: Optional[dict] = None
    probed_year_count_complete: bool = False

    # callbacks injected by PaperCitationFetcher so this module stays decoupled
    refresh_session_fn: object = None          # callable() -> None
    try_interactive_captcha_fn: object = None  # callable(url) -> bool
    wait_proxy_switch_fn: object = None        # callable() -> bool
    wait_status_fn: object = None              # callable() -> str
    format_year_count_summary_fn: object = None  # callable(counts) -> str
    format_year_set_summary_fn: object = None    # callable(years) -> str


# ---------------------------------------------------------------------------
# scholarly monkey-patch
# ---------------------------------------------------------------------------

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

_CURL_HEADER_ALLOWLIST = frozenset({
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
})


def patch_scholarly(ctx: SessionContext) -> None:
    """
    Monkey-patch scholarly internals for browser simulation + rate-limit safety.

    All closures capture *ctx* (a SessionContext), not a fetcher instance, so
    this function can be called from any context that provides one.

    Side-effects:
      - scholarly._Scholarly__nav sessions replaced with HTTP/2 clients
      - nav._get_page, nav._new_session, pm._handle_captcha2 patched
      - _SearchScholarIterator.__init__, __next__, _load_url patched
      - scholarly._citedby_long patched for year-segment tracking
    """
    nav = scholarly._Scholarly__nav
    nav._set_retries(1)
    original_get_page = nav._get_page

    profile_url = f'https://scholar.google.com/citations?user={ctx.author_id}&hl=en'
    ctx.last_scholar_url = profile_url
    ctx.curl_header_allowlist = _CURL_HEADER_ALLOWLIST
    ctx.injected_cookies = {}
    ctx.injected_header_overrides = {}

    def _make_http2_session():
        return __import__('httpx').Client(http2=True)

    def _apply_browser_headers(session):
        session.headers.update(_BROWSER_HEADERS)
        session.headers.update(ctx.injected_header_overrides)
        session.headers['referer'] = ctx.last_scholar_url

    def _full_session_setup(session):
        _apply_browser_headers(session)
        for k, v in ctx.injected_cookies.items():
            session.cookies.set(k, v)

    # Replace HTTP/1.1 sessions with HTTP/2, preserving existing cookies
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

    # Patch _new_session: re-apply browser identity after 403-triggered rebuild
    original_new_session = nav._new_session

    def patched_new_session(premium=True, **kwargs):
        original_new_session(premium=premium, **kwargs)
        if premium:
            nav._session1 = _make_http2_session()
        else:
            nav._session2 = _make_http2_session()
        for session in (nav._session1, nav._session2):
            _full_session_setup(session)

    nav._new_session = patched_new_session

    # Patch pm._handle_captcha2: rebuild session with full browser identity
    def patched_handle_captcha2(pagerequest):
        new_session = _make_http2_session()
        _full_session_setup(new_session)
        nav._session1 = new_session
        nav._session2 = new_session
        return new_session

    nav.pm1._handle_captcha2 = patched_handle_captcha2
    nav.pm2._handle_captcha2 = patched_handle_captcha2

    # Patch _get_page: inject pre-request delay, URL logging, retry limit
    MAX_SLEEPS_PER_PAGE = 1

    def patched_get_page(pagerequest, premium=False):
        sleep_count = [0]
        original_sleep = time.sleep
        request_url = _scholar_request_url(pagerequest)
        if request_url:
            ctx.current_attempt_url = request_url
        referer = None
        for session in (nav._session1, nav._session2):
            referer = session.headers.get('referer')
            if referer:
                break
        if request_url:
            # Only show referer when it differs from the last visited URL
            # (i.e. injected externally, not the natural previous-page referer)
            show_referer = referer and referer != ctx.last_scholar_url
            referer_str = f" (referer: {referer})" if show_referer else ""
            print(f"      Request URL: {request_url}{referer_str}", flush=True)

        def unified_sleep(seconds):
            sleep_count[0] += 1
            if sleep_count[0] > MAX_SLEEPS_PER_PAGE:
                time.sleep = original_sleep
                raise MaxTriesExceededException(
                    f"Too many retries ({sleep_count[0]}) for single page request")
            d = rand_delay(ctx.delay_scale)
            retry_note = f" (retry {sleep_count[0]})" if sleep_count[0] > 1 else ""
            wait_str = ctx.wait_status_fn() if ctx.wait_status_fn else ""
            print(f"      {now_str()} Waiting {d:.0f}s before request{retry_note}... "
                  f"[{wait_str}]", flush=True)
            original_sleep(d)

        time.sleep = unified_sleep
        try:
            return original_get_page(pagerequest, premium)
        finally:
            time.sleep = original_sleep

    nav._get_page = patched_get_page

    # Patch _SearchScholarIterator: pagination tracking + Referer + break logic
    original_load_url = _SearchScholarIterator._load_url
    original_init = _SearchScholarIterator.__init__
    original_next = _SearchScholarIterator.__next__

    ctx.total_page_count = 0
    ctx.next_break_at = random.randint(MANDATORY_BREAK_EVERY_MIN, MANDATORY_BREAK_EVERY_MAX)
    ctx.next_refresh_at = ctx.next_break_at + random.randint(SESSION_REFRESH_MIN, SESSION_REFRESH_MAX)

    def patched_init(self_iter, nav_arg, url):
        self_iter._page_num = 0
        self_iter._items_in_current_page = 0
        self_iter._page_size = None
        self_iter._stop_after_current_page = False
        self_iter._finished_current_page = False
        return original_init(self_iter, nav_arg, url)

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
        ctx.total_page_count += 1
        ctx.current_paper_page_count += 1
        if self_iter._page_num > 1:
            print(f"      Pagination (page {ctx.current_paper_page_count})", flush=True)

        for session in (nav._session1, nav._session2):
            session.headers['referer'] = ctx.last_scholar_url

        if ctx.total_page_count >= ctx.next_break_at:
            d = random.uniform(MANDATORY_BREAK_MIN, MANDATORY_BREAK_MAX) * ctx.delay_scale
            wait_str = ctx.wait_status_fn() if ctx.wait_status_fn else ""
            print(f"      {now_str()} Mandatory break after {ctx.total_page_count} pages "
                  f"({d/60:.1f} min)... [{wait_str}]", flush=True)
            time.sleep(d)
            ctx.next_break_at = (ctx.total_page_count
                                 + random.randint(MANDATORY_BREAK_EVERY_MIN, MANDATORY_BREAK_EVERY_MAX))
            if not ctx.interactive_captcha and ctx.refresh_session_fn:
                print(f"      {now_str()} Refreshing session after break...", flush=True)
                ctx.refresh_session_fn()
            ctx.next_refresh_at = (ctx.total_page_count
                                   + random.randint(SESSION_REFRESH_MIN, SESSION_REFRESH_MAX))
        elif ctx.total_page_count >= ctx.next_refresh_at:
            if not ctx.interactive_captcha and ctx.refresh_session_fn:
                print(f"      {now_str()} Refreshing session "
                      f"(after {ctx.total_page_count} pages)...", flush=True)
                ctx.refresh_session_fn()
            ctx.next_refresh_at = (ctx.total_page_count
                                   + random.randint(SESSION_REFRESH_MIN, SESSION_REFRESH_MAX))

        ctx.current_attempt_url = (
            f'https://scholar.google.com{url}' if url.startswith('/') else url)

        result = original_load_url(self_iter, url)

        page_size = None
        try:
            page_size = len(getattr(self_iter, '_rows', []) or [])
        except Exception:
            page_size = None
        self_iter._page_size = page_size if page_size and page_size > 0 else None

        ctx.last_scholar_url = ctx.current_attempt_url
        return result

    _SearchScholarIterator.__init__ = patched_init
    _SearchScholarIterator.__next__ = patched_next
    _SearchScholarIterator._load_url = patched_load_url

    # Patch _citedby_long: year-segment skipping + tracking
    original_citedby_long = scholarly._citedby_long
    ctx.current_year_segment = None
    ctx.completed_year_segments = set()

    def patched_citedby_long(obj, years):
        first = True
        for y_hi, y_lo in years:
            if y_lo in ctx.completed_year_segments:
                print(f"      Skipping completed year {y_lo}", flush=True)
                continue
            if not first:
                print(f"      Switching year range {y_lo}-{y_hi}", flush=True)
            first = False
            ctx.current_year_segment = y_lo
            yield from original_citedby_long(obj, [(y_hi, y_lo)])
            ctx.completed_year_segments.add(y_lo)

    scholarly._citedby_long = patched_citedby_long


# ---------------------------------------------------------------------------
# Soft session reset
# ---------------------------------------------------------------------------

def refresh_scholarly_session() -> None:
    """Clear got_403 without discarding the httpx session or its cookies."""
    nav = scholarly._Scholarly__nav
    nav.got_403 = False
    print("      (Session reset: got_403 cleared, cookies preserved)", flush=True)


# ---------------------------------------------------------------------------
# Year-range probe
# ---------------------------------------------------------------------------

def probe_citation_start_year(
    citedby_url: str,
    ctx: SessionContext,
    num_citations=None,
    pub_year=None,
) -> Optional[int]:
    """
    Fetch the base citedby URL once to determine the earliest year with citations.

    Returns the start year (int) or None on complete failure.
    """
    import re as _re

    nav = scholarly._Scholarly__nav
    full_url = (f'https://scholar.google.com{citedby_url}'
                if citedby_url.startswith('/') else citedby_url)
    MAX_PROBE_RETRIES = 3
    attempt = 0

    while True:
        attempt += 1
        ctx.total_page_count += 1
        for session in (nav._session1, nav._session2):
            session.headers['referer'] = ctx.last_scholar_url
        ctx.current_attempt_url = full_url

        try:
            soup = nav._get_soup(citedby_url)
            ctx.last_scholar_url = full_url
        except Exception as e:
            print(f"      {now_str()} Probe blocked (attempt {attempt}): {e}", flush=True)
            if ctx.interactive_captcha and ctx.try_interactive_captcha_fn:
                solved = ctx.try_interactive_captcha_fn(full_url)
                if solved:
                    continue
            if attempt >= MAX_PROBE_RETRIES:
                print(f"      Probe gave up after {MAX_PROBE_RETRIES} attempts, "
                      f"falling back to pub_year heuristic", flush=True)
                return None
            if ctx.wait_proxy_switch_fn:
                ctx.wait_proxy_switch_fn()
            continue

        try:
            current_year = datetime.now().year
            years = set()
            probed_year_counts = {}
            ctx.probed_year_counts = None
            ctx.probed_year_count_complete = False

            try:
                pub_year_int = (int(pub_year)
                                if pub_year and pub_year not in ('N/A', '?') else None)
            except (TypeError, ValueError):
                pub_year_int = None

            # Primary: full histogram dialog DOM
            for bar in soup.select(
                '.gs_rs_hist_dialog-g_bar_wrapper .gs_hist_g_a[data-year][data-count], '
                '#gs_md_hist .gs_hist_g_a[data-year][data-count]'
            ):
                try:
                    y = int(bar.get('data-year', ''))
                    count = int(bar.get('data-count', '0'))
                    if 1990 <= y <= current_year:
                        probed_year_counts[y] = count
                        if count > 0:
                            years.add(y)
                except (TypeError, ValueError):
                    pass

            if years:
                ctx.probed_year_counts = probed_year_counts
                hist_total = sum(probed_year_counts.values())
                hist_summary = (ctx.format_year_count_summary_fn(probed_year_counts)
                                if ctx.format_year_count_summary_fn else str(probed_year_counts))
                earliest = min(years)

                if num_citations is not None and hist_total >= num_citations:
                    ctx.probed_year_count_complete = True
                    print(f"      Scholar year range probe: start_year = {earliest} "
                          f"(from full histogram DOM, {len(years)} year values found, "
                          f"total={hist_total})", flush=True)
                    print(f"      Year histogram summary: {hist_summary}", flush=True)
                    return earliest

                conservative_start = earliest
                used_pub_year_fallback = False
                if pub_year_int is not None and pub_year_int < conservative_start:
                    conservative_start = pub_year_int
                    used_pub_year_fallback = True
                if num_citations is not None:
                    unyeared = num_citations - hist_total
                    print(f"      Scholar year range probe: histogram incomplete "
                          f"(hist_total={hist_total}, scholar_total={num_citations}, unyeared={unyeared}), "
                          f"using conservative start_year = {conservative_start}", flush=True)
                else:
                    print(f"      Scholar year range probe: histogram total unavailable, "
                          f"using conservative start_year = {conservative_start}", flush=True)
                print(f"      Year histogram summary: {hist_summary}", flush=True)
                if pub_year_int is not None:
                    fallback_note = ('pub_year fallback applied' if used_pub_year_fallback
                                     else 'pub_year fallback not needed')
                    print(f"      Conservative year traversal: pub_year={pub_year_int} "
                          f"({fallback_note})", flush=True)
                else:
                    print("      Conservative year traversal: pub_year unavailable", flush=True)
                return conservative_start

            # Fallback 1: single-year histogram links
            for a in soup.find_all('a', href=True):
                href = a['href']
                m_lo = _re.search(r'[?&]as_ylo=(\d{4})', href)
                m_hi = _re.search(r'[?&]as_yhi=(\d{4})', href)
                if m_lo and m_hi and m_lo.group(1) == m_hi.group(1):
                    years.add(int(m_lo.group(1)))

            # Fallback 2: coarse as_ylo preset links
            for a in soup.find_all('a', href=True):
                href = a['href']
                m = _re.search(r'[?&]as_ylo=(\d{4})', href)
                if m:
                    years.add(int(m.group(1)))

            # Fallback 3: year text in citation snippets
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
                year_summary = (ctx.format_year_set_summary_fn(years)
                                if ctx.format_year_set_summary_fn else str(sorted(years)))
                print(f"      Scholar year range probe: start_year = {earliest} "
                      f"(from fallback, {len(years)} year values found)", flush=True)
                print(f"      Fallback year summary: {year_summary}", flush=True)
                print("      Conservative year traversal: no complete histogram available",
                      flush=True)
                return earliest

            if pub_year_int is not None:
                print(f"      (Year range probe: no year data found on page, "
                      f"using pub_year {pub_year_int})", flush=True)
                print("      Conservative year traversal: using pub_year fallback only",
                      flush=True)
                return pub_year_int

            print(f"      (Year range probe: no year data found on page)", flush=True)
            return None

        except Exception as e:
            print(f"      (Year range probe: parsing failed: {e})", flush=True)
            return None
