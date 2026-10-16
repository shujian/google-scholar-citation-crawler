"""
code/scholarly_session.py — scholarly HTTP session setup and year probe.

patch_scholarly() monkey-patches scholarly internals to:
  - use HTTP/2 sessions with browser-like headers
  - enforce randomised delays and mandatory long breaks
  - track pagination and update Referer dynamically (citation-list pagination
    and the Cite dialogs, which are sub-navigations of a list page)
  - support soft session refresh and year-segment skipping

_probe_citation_start_year() fetches the base citedby page once to read the
per-year histogram and determine the earliest year with citations.

Both functions accept a SessionContext dataclass so they do not need to access
a PaperCitationFetcher instance.  PaperCitationFetcher keeps thin wrapper
methods for backward compatibility with existing call sites.
"""

import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import httpx

from scholarly import scholarly
from scholarly._proxy_generator import MaxTriesExceededException
from scholarly.publication_parser import _SearchScholarIterator, PublicationParser

from code.log_utils import run_log
from code.common import (
    MANDATORY_BREAK_EVERY_MAX,
    MANDATORY_BREAK_EVERY_MIN,
    MANDATORY_BREAK_MAX,
    MANDATORY_BREAK_MIN,
    SESSION_REFRESH_MAX,
    SESSION_REFRESH_MIN,
    _scholar_request_url,
    now_str,
    rand_delay,
)
from code.config import DETAIL_PAGE_DELAY_FACTOR
from code.page_visit import PageVisit


# ---------------------------------------------------------------------------
# Pre-request pacing
# ---------------------------------------------------------------------------

def is_cite_dialog_url(url):
    """True for a citing entry's Cite dialog URL (``output=cite``).

    The single definition of "this request is a Cite dialog", shared by the
    pacing decision (``detail_path_delay_factor``) and the Referer refresh in
    ``patched_get_page`` — the two must agree on which URLs those are.
    """
    return bool(url) and 'output=cite' in str(url)


def detail_path_delay_factor(url):
    """Pre-request-delay factor for *url* — the single definition of the pacing.

    The **detail-style** paths are one-shot, non-paginated pages:

    * a publication detail page (``view_op=view_citation``),
    * a citing entry's Cite dialog (``output=cite``) — the signed BibTeX download
      was dropped from this list on 2026-10-15, when BibTeX itself was abandoned.

    They run at ``code.config.DETAIL_PAGE_DELAY_FACTOR`` (1/10) of the normal
    delay.  Every other URL — notably citation-list pagination and the profile
    page — keeps the full 45-90 s.  ``code/venue_detail.py`` and
    ``code/cite_format.py`` deliberately contain no delay of their own: both go
    through this function via the patched navigator.
    """
    if not url:
        return 1.0
    text = str(url)
    if 'view_op=view_citation' in text or is_cite_dialog_url(text):
        return DETAIL_PAGE_DELAY_FACTOR
    return 1.0


def is_detail_style_path(url):
    """True for the one-shot detail paths (pub detail page, Cite dialog).

    The same single classifier as the pacing (``detail_path_delay_factor``),
    read for a different purpose: on these URLs a 404/410 means "this record
    does not exist" rather than "we are blocked" — Scholar serves the citation
    list row but may have no Cite dialog for its cluster handle (measured
    2026-10-16; see ``code/page_visit.py::RecordUnavailable``).
    """
    return detail_path_delay_factor(url) < 1.0


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
    curl_page_count: int = 0       # pages accessed with current curl/cookies
    current_paper_page_count: int = 0  # resets per paper, not per year-segment or retry
    next_break_at: int = 0
    next_refresh_at: int = 0
    last_scholar_url: str = ""
    current_attempt_url: Optional[str] = None
    # Evidence for the last citation page that came back with no result rows.
    # Set by patched_load_url; read by callers that know how many rows were
    # expected, so an empty/blocked response is not silently recorded as
    # "fetched, 0 citations".
    last_page_evidence: Optional[dict] = None
    # HTTP status of the last response our httpx sessions received (final
    # response, i.e. after redirects).  scholarly's own retry loop swallows the
    # status — a 404 simply means "retry with the same session" — so the patch
    # layer records it here and PageVisit can tell "this record does not exist"
    # (404/410 on a detail path) apart from "we are being blocked".  None means
    # "no response seen for the current request" (network error).
    last_response_status: Optional[int] = None
    last_response_url: str = ""

    # injected cookies / headers (populated by _inject_cookies_from_curl)
    injected_cookies: dict = field(default_factory=dict)
    injected_header_overrides: dict = field(default_factory=dict)
    curl_header_allowlist: frozenset = field(default_factory=frozenset)

    # year-segment tracking (per-paper, reset before each fetch) lives in
    # citation_fetch.YearFetchSession; this context only carries the probe.
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
    first_page_prompt_fn: object = None        # callable() -> bool, fired once before first request


# ---------------------------------------------------------------------------
# Citation page evidence
#
# A citation page can come back as HTTP 200 with zero result rows: scholarly
# only rejects pages carrying a captcha id, so an empty result set, a consent
# page or an unrecognised interstitial all reach the parser as "no rows".  The
# iterator then simply ends, no exception is raised, and the paper is recorded
# as "fetched, saw 0" — indistinguishable from a genuinely empty citation list,
# and re-fetched forever.  These helpers keep the page-level evidence so the run
# log and the persisted diagnostics can tell those cases apart.
# ---------------------------------------------------------------------------

# Same ids scholarly treats as a captcha (code/scholarly_session.py mirrors
# scholarly/_navigator.py _has_captcha) plus the dos-captcha body class.
_PAGE_CAPTCHA_IDS = ('gs_captcha_ccl', 'recaptcha', 'captcha-form')
_PAGE_CAPTCHA_CLASSES = ('rc-doscaptcha-body',)

# Text markers for pages that are not a citation list at all.
_PAGE_TEXT_MARKERS = (
    ('consent', ('before you continue', 'consent.google.com')),
    ('unusual_traffic', ('unusual traffic', 'our systems have detected')),
    ('no_results', ('did not match any', 'no results found')),
)


def is_citation_list_url(url):
    """Return True for Scholar citation-list URLs (both direct and year-based)."""
    return bool(url) and 'cites=' in str(url)


def citation_page_evidence(soup):
    """Return page-level evidence for a citation page, or None.

    Keys: rows (int), results_container (bool), result_banner (str),
    markers (list of str).  Never raises: diagnostics must not break fetching.
    """
    try:
        if soup is None:
            return None
        rows = (len(soup.select('div.gs_r.gs_or'))
                + len(soup.find_all('div', class_='gsc_mpat_ttl')))
        container = soup.find(id='gs_res_ccl_mid') is not None
        banner_el = soup.find('div', class_='gs_ab_mdw')
        banner = ' '.join(banner_el.get_text(' ', strip=True).split())[:60] if banner_el else ''
        markers = []
        if (any(soup.find(id=cap_id) is not None for cap_id in _PAGE_CAPTCHA_IDS)
                or any(soup.find(class_=cls) is not None for cls in _PAGE_CAPTCHA_CLASSES)):
            markers.append('captcha')
        text = soup.get_text(' ', strip=True).lower()
        for name, needles in _PAGE_TEXT_MARKERS:
            if any(needle in text for needle in needles):
                markers.append(name)
        return {
            'rows': rows,
            'results_container': container,
            'result_banner': banner,
            'markers': markers,
        }
    except Exception:
        return None


def format_page_evidence(evidence):
    """Return ' [no results container; banner=...; markers=...]' or ''."""
    if not isinstance(evidence, dict):
        return ''
    parts = []
    if not evidence.get('results_container'):
        parts.append('no results container')
    if evidence.get('result_banner'):
        parts.append(f"banner={evidence['result_banner']!r}")
    if evidence.get('markers'):
        parts.append('markers=' + ','.join(evidence['markers']))
    return f" [{'; '.join(parts)}]" if parts else ''


def record_zero_row_citation_page(ctx, soup):
    """Store and log the evidence for a citation page that returned no rows.

    Called from the page loader — the only place that sees the raw page — so a
    zero-row response always leaves a trace instead of looking like a genuinely
    empty citation list.  Returns the evidence dict (or None).
    """
    evidence = citation_page_evidence(soup)
    ctx.last_page_evidence = evidence
    run_log.warn(f"Page items: 0 (no citation rows){format_page_evidence(evidence)}",
                 module="http", indent=2)
    return evidence


# ---------------------------------------------------------------------------
# scholarly monkey-patch
# ---------------------------------------------------------------------------

# Browser identity headers.  These are applied on top of scholarly's native
# httpx.Client — no TLS impersonation library is used (curl_cffi was dropped
# because Scholar's /scholar?cites= endpoint returns 429 for every curl_cffi
# impersonation target, while scholarly's own httpx client is accepted).
# Keep the Chrome version consistent across user-agent and sec-ch-ua-*.
_BROWSER_HEADERS = {
    'user-agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/145.0.0.0 Safari/537.36'),
    'accept': ('text/html,application/xhtml+xml,application/xml;q=0.9,'
               'image/avif,image/webp,image/apng,*/*;q=0.8,'
               'application/signed-exchange;v=b3;q=0.7'),
    'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
    'priority': 'u=0, i',
    'sec-ch-ua': '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
    'sec-ch-ua-arch': '"arm"',
    'sec-ch-ua-bitness': '"64"',
    'sec-ch-ua-full-version-list': ('"Not:A-Brand";v="99.0.0.0", '
                                    '"Google Chrome";v="145.0.3800.97", '
                                    '"Chromium";v="145.0.7632.160"'),
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-model': '""',
    'sec-ch-ua-platform': '"macOS"',
    'sec-ch-ua-platform-version': '"27.0.0"',
    'sec-ch-ua-wow64': '?0',
    'sec-fetch-dest': 'document',
    'sec-fetch-mode': 'navigate',
    # Start with 'none' — the first request is a direct navigation
    # (address bar / bookmark).  After the first successful page load
    # patched_get_page switches this to 'same-origin' because all
    # subsequent Scholar pages share the same origin.
    'sec-fetch-site': 'none',
    'sec-fetch-user': '?1',
    'upgrade-insecure-requests': '1',
}

# Headers that together identify the browser brand/version.  They are adopted
# from a pasted cURL only as a coherent unit (all-or-nothing, see
# complete_browser_fingerprint) so the running fingerprint always matches the
# browser that issued the injected cookies.  The _BROWSER_HEADERS values are
# just the cold-start default used when no cURL has been injected.
_BROWSER_IDENTITY_HEADERS = frozenset({
    'user-agent',
    'sec-ch-ua',
    'sec-ch-ua-full-version-list',
})

_CURL_HEADER_ALLOWLIST = frozenset({
    # The browser-brand trio (user-agent / sec-ch-ua / sec-ch-ua-full-version
    # -list) enters through *_BROWSER_IDENTITY_HEADERS and is enforced as a
    # coherent unit by complete_browser_fingerprint(), so it is not listed
    # individually here.  sec-fetch-site is managed dynamically (none →
    # same-origin).
    'accept',
    'accept-language',
    'priority',
    'sec-ch-ua-arch',
    'sec-ch-ua-bitness',
    'sec-ch-ua-mobile',
    'sec-ch-ua-model',
    'sec-ch-ua-platform',
    'sec-ch-ua-platform-version',
    'sec-ch-ua-wow64',
    'sec-fetch-dest',
    'sec-fetch-mode',
    'sec-fetch-user',
    'upgrade-insecure-requests',
    *_BROWSER_IDENTITY_HEADERS,
})


_UA_VERSION_RE = re.compile(r'\b(?:Edg|Edge|Firefox|OPR|Chrome)/(\d+)')
_SEC_CH_UA_VERSION_RE = re.compile(r'v="(\d+)"')


def complete_browser_fingerprint(header_overrides):
    """
    Enforce that the browser-identity trio is adopted as a coherent unit.

    *header_overrides* is the dict of allowlisted headers parsed from a pasted
    cURL (lowercased names).  If all of user-agent, sec-ch-ua and
    sec-ch-ua-full-version-list are present AND their major versions agree, the
    trio stays — the crawler then presents the same fingerprint as the browser
    that issued the cookies.  Otherwise the whole trio is dropped so the
    hardcoded _BROWSER_HEADERS default remains in force; a partial or
    contradictory override would be worse than none (e.g. Edge cookies sent
    with a Chrome sec-ch-ua).
    """
    present = [h for h in _BROWSER_IDENTITY_HEADERS if h in header_overrides]
    if not present:
        return
    if len(present) != len(_BROWSER_IDENTITY_HEADERS):
        missing = sorted(_BROWSER_IDENTITY_HEADERS - set(present))
        print(f"  (Incomplete browser identity in cURL — missing "
              f"{', '.join(missing)}; keeping hardcoded default fingerprint.)",
              flush=True)
        for h in _BROWSER_IDENTITY_HEADERS:
            header_overrides.pop(h, None)
        return

    ua = header_overrides['user-agent']
    ua_m = _UA_VERSION_RE.search(ua)
    ua_ver = ua_m.group(1) if ua_m else None
    sec_ch_ua = header_overrides['sec-ch-ua']
    sec_vers = set(_SEC_CH_UA_VERSION_RE.findall(sec_ch_ua))
    if ua_ver and sec_vers and ua_ver not in sec_vers:
        print(f"  (Browser identity version mismatch: user-agent "
              f"Chrome/Edg/Firefox {ua_ver} vs sec-ch-ua "
              f"{', '.join(sorted(sec_vers))}; keeping hardcoded default "
              f"fingerprint.)", flush=True)
        for h in _BROWSER_IDENTITY_HEADERS:
            header_overrides.pop(h, None)
        return

    print(f"  Adopted browser fingerprint from cURL "
          f"(user-agent v{ua_ver or '?'}, sec-ch-ua {sec_ch_ua})", flush=True)


def assert_scholarly_patches_active(checks):
    """Raise RuntimeError when a scholarly monkey-patch did not take effect.

    *checks* is an iterable of (label, actual, expected) triples.  Comparison is
    by equality, which for functions/classes means identity.
    """
    failed = [label for label, actual, expected in checks if actual != expected]
    if failed:
        raise RuntimeError(
            "Scholarly patch ineffective for: " + ", ".join(failed) + ". The "
            "installed scholarly version is incompatible with this crawler "
            "(code/scholarly_session.py patches its internals). Refusing to "
            "run with browser headers, rate-limit waits or pagination parsing "
            "silently disabled — pin a compatible scholarly version or update "
            "the patch layer."
        )


def patch_scholarly(ctx: SessionContext) -> None:
    """
    Monkey-patch scholarly internals for browser simulation + rate-limit safety.

    All closures capture *ctx* (a SessionContext), not a fetcher instance, so
    this function can be called from any context that provides one.

    Side-effects:
      - scholarly._Scholarly__nav sessions replaced with HTTP/2 clients
      - nav._get_page, nav._new_session, pm._handle_captcha2 patched
      - _SearchScholarIterator.__init__, __next__, _load_url patched
      - PublicationParser._citation_pub patched

    The patched attributes are verified at the end (see
    assert_scholarly_patches_active): a scholarly upgrade that renames or
    replaces one of them must fail loudly at startup instead of silently
    disabling browser headers, rate-limit waits or pagination parsing.
    """
    nav = scholarly._Scholarly__nav
    nav._set_retries(1)
    # Scholar pages can take >5 s through a proxy.  scholarly's default
    # timeout is 5 s, which causes false-positive timeouts → MaxTries.
    # Set the attribute directly (not via set_timeout) so that test mocks
    # without set_timeout don't break.
    nav._TIMEOUT = 30
    original_get_page = nav._get_page

    profile_url = f'https://scholar.google.com/citations?user={ctx.author_id}&hl=en'
    ctx.last_scholar_url = profile_url
    ctx.curl_header_allowlist = _CURL_HEADER_ALLOWLIST
    ctx.injected_cookies = {}
    ctx.injected_header_overrides = {}

    def _remember_response(response):
        # Fires once per request, on the final response (after redirects).
        # scholarly's `_get_page` throws the status away — it treats 404 as
        # "retry with the same session" and raises the same
        # MaxTriesExceededException it raises for a captcha or a 429 — so this
        # hook is the only place the status survives for PageVisit.
        ctx.last_response_status = response.status_code
        ctx.last_response_url = str(response.url)

    def _make_http2_session():
        # Use httpx (scholarly's native client, HTTP/2) instead of curl_cffi.
        # Scholar's citation-search endpoint (/scholar?cites=) rate-limits
        # curl_cffi sessions regardless of impersonation target, but accepts
        # httpx — verified empirically via 7890 proxy.
        return httpx.Client(http2=True, follow_redirects=True,
                            event_hooks={'response': [_remember_response]})

    def _apply_browser_headers(session):
        session.headers.update(_BROWSER_HEADERS)
        session.headers.update(ctx.injected_header_overrides)
        session.headers['referer'] = ctx.last_scholar_url

    def _full_session_setup(session):
        _apply_browser_headers(session)
        for k, v in ctx.injected_cookies.items():
            session.cookies.set(k, v)
        # When cookies from a real browser session are injected, the
        # session is already established on Scholar — subsequent
        # requests are same-origin navigations, not cross-site.
        # _BROWSER_HEADERS defaults sec-fetch-site to 'none' (correct
        # for a cold start), but a rebuilt session with injected
        # cookies must advertise 'same-origin'.
        if ctx.injected_cookies:
            session.headers['sec-fetch-site'] = 'same-origin'

    def _close_quietly(session):
        # httpx clients own a connection pool: every client we drop must be
        # closed or its sockets stay open for the rest of the run.  A session
        # without close() (test doubles) or a double close must never break the
        # run.
        try:
            session.close()
        except Exception:
            pass

    # Replace HTTP/1.1 sessions with HTTP/2, preserving existing cookies
    old_cookies = {}
    for old_session in (nav._session1, nav._session2):
        try:
            old_cookies.update(dict(old_session.cookies))
        except Exception:
            pass
        _close_quietly(old_session)
    nav._session1 = _make_http2_session()
    nav._session2 = _make_http2_session()
    for session in (nav._session1, nav._session2):
        _apply_browser_headers(session)
        for k, v in old_cookies.items():
            session.cookies.set(k, v)
    print(f"  Browser headers applied (HTTP/2, Chrome/145, sec-ch-ua-*, referer; "
          f"{len(old_cookies)} profile cookies preserved)", flush=True)

    # Patch _new_session: re-apply browser identity after 403-triggered rebuild
    original_new_session = nav._new_session

    def patched_new_session(premium=True, **kwargs):
        original_new_session(premium=premium, **kwargs)
        # original_new_session() just built a client that is discarded right
        # away (scholarly reads nav._session1/_session2, never pm._session, so
        # nothing keeps using it).
        if premium:
            _close_quietly(nav._session1)
            nav._session1 = _make_http2_session()
        else:
            _close_quietly(nav._session2)
            nav._session2 = _make_http2_session()
        for session in (nav._session1, nav._session2):
            _full_session_setup(session)

    nav._new_session = patched_new_session

    # Patch pm._handle_captcha2: rebuild session with full browser identity
    def patched_handle_captcha2(pagerequest):
        new_session = _make_http2_session()
        _full_session_setup(new_session)
        # One client serves both slots from here on.  Close the replaced ones
        # (closing the same client twice is harmless) so no slot keeps a client
        # whose pool was dropped.
        for old_session in (nav._session1, nav._session2):
            if old_session is not new_session:
                _close_quietly(old_session)
        nav._session1 = new_session
        nav._session2 = new_session
        return new_session

    nav.pm1._handle_captcha2 = patched_handle_captcha2
    nav.pm2._handle_captcha2 = patched_handle_captcha2

    # Patch _get_page: URL logging + PageVisit-based error recovery.
    # PageVisit handles captcha, proxy-switch, and retry internally.

    def patched_get_page(pagerequest, premium=False):
        request_url = _scholar_request_url(pagerequest)
        if request_url:
            ctx.current_attempt_url = request_url
        # A Cite dialog is a sub-navigation *from* the citation-list page its row
        # was seen on, but it never passes through
        # _SearchScholarIterator._load_url — the hook that refreshes the session's
        # Referer while a citation list is being walked.  Left alone the dialog
        # therefore carries the page *before* the row's page (observed 2026-10-15:
        # referer was the as_ylo=2023 list while the row came from the as_ylo=2025
        # one), whereas a browser sends the page the row is on.  Refresh it before
        # the request goes out, so what is sent and what the log line reports agree.
        if is_cite_dialog_url(request_url) and ctx.last_scholar_url:
            for session in (nav._session1, nav._session2):
                session.headers['referer'] = ctx.last_scholar_url
        referer = None
        for session in (nav._session1, nav._session2):
            referer = session.headers.get('referer')
            if referer:
                break
        ctx.total_page_count += 1
        ctx.curl_page_count += 1
        if ctx.total_page_count == 1 and ctx.first_page_prompt_fn:
            fn = ctx.first_page_prompt_fn
            ctx.first_page_prompt_fn = None
            fn()
        if request_url:
            show_referer = referer and referer != ctx.last_scholar_url
            referer_str = f" (referer: {referer})" if show_referer else ""
            run_log.info(f"Request URL: {request_url}{referer_str}",
                         module="http", indent=2)

        # Wrap original_get_page so that scholarly's internal 60-120 s
        # sleeps are replaced by our rand_delay (45-90 s, or 1/10 of that on the
        # detail-style URLs — see detail_path_delay_factor), and excessive
        # retries are aborted so PageVisit handles higher-level recovery.
        #
        # Skip the pre-request delay on the very first page of the run
        # AND on the first page after injecting fresh cookies — no prior
        # request with those credentials exists to space apart from.
        if ctx.total_page_count == 1 or ctx.curl_page_count <= 1:
            run_log.info("Fetching (first page with these credentials, no delay)...",
                         module="http", indent=2)

        # Allow 1 sleep (the normal pre-request delay).  The second sleep
        # is scholarly's internal retry — kill it so PageVisit takes over
        # and triggers captcha recovery immediately instead of waiting.
        MAX_SLEEPS_PER_PAGE = 1

        # Pace of the *detail-style* paths (publication detail page, Cite dialog
        # and its signed BibTeX download): one-shot, non-paginated pages, so they
        # run at DETAIL_PAGE_DELAY_FACTOR of the normal delay instead of the full
        # 45-90 s.  `--accelerate` stacks on top (the factors multiply) and
        # citation-list pagination keeps the full delay.  The factor is defined
        # once, in code.config; detail_path_delay_factor() is the single place
        # that decides which URLs qualify.
        delay_scale = ctx.delay_scale * detail_path_delay_factor(request_url)

        def _fetch_with_sleep_limit():
            # A fresh request: drop the previous response's status so PageVisit
            # can never attribute an old 404 to a failure that produced no
            # response at all (the response hook records the new one).
            ctx.last_response_status = None
            ctx.last_response_url = ''
            original_sleep = time.sleep
            sleep_count = [0]

            def _limited_sleep(seconds):
                sleep_count[0] += 1
                if sleep_count[0] > MAX_SLEEPS_PER_PAGE:
                    time.sleep = original_sleep
                    raise MaxTriesExceededException(
                        f"Too many retries ({sleep_count[0]}) "
                        f"for single page request")
                # First sleep on the very first page of the run, or the
                # first sleep after injecting fresh cookies — no prior
                # request with those credentials exists.  Check ctx
                # dynamically so that a cookie reset between PageVisit
                # retries takes effect immediately.
                #   total_page_count == 1  → first page of the run
                #   curl_page_count  <= 1  → first page with these cookies
                #     (0 = just reset, retry within PageVisit;
                #      1 = first patched_get_page call after reset)
                if sleep_count[0] == 1 and (ctx.total_page_count == 1
                                            or ctx.curl_page_count <= 1):
                    original_sleep(seconds)
                    return
                d = rand_delay(delay_scale)
                retry_note = (f" (retry {sleep_count[0]})"
                              if sleep_count[0] > 1 else "")
                wait_str = ctx.wait_status_fn() if ctx.wait_status_fn else ""
                run_log.info(f"Waiting {d:.0f}s before request"
                             f"{retry_note}... [{wait_str}]", module="http", indent=2)
                original_sleep(d)

            time.sleep = _limited_sleep
            try:
                return original_get_page(pagerequest, premium)
            finally:
                time.sleep = original_sleep

        page_visit = PageVisit(ctx)
        label = request_url[:80] if request_url else "scholar page"
        result = page_visit.fetch(
            _fetch_with_sleep_limit,
            url=request_url or f'https://scholar.google.com{ctx.last_scholar_url}',
            label=label,
            # On a one-shot detail page a 404 means "this record has no such
            # page" (a Cite dialog for a cluster handle Scholar does not serve),
            # not "we are blocked": PageVisit raises RecordUnavailable instead of
            # prompting for a captcha and burning 20-35 minutes of cooldowns.
            missing_record_on_404=is_detail_style_path(request_url),
        )
        # After first successful Scholar page, switch to same-origin for
        # subsequent requests (real browsers only send 'none' on the
        # very first navigation from outside the origin).
        if ctx.total_page_count == 1:
            for session in (nav._session1, nav._session2):
                session.headers['sec-fetch-site'] = 'same-origin'
        return result

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
        # Guard against recursive calls: original_next may enter its
        # "elif _pos >= len(_rows)" branch and recursively call
        # self.__next__() (== patched_next).  The inner call already
        # counts the new-page item; the outer call must not double-count.
        if getattr(self_iter, '_patched_next_active', False):
            return original_next(self_iter)
        self_iter._patched_next_active = True
        try:
            result = original_next(self_iter)
            self_iter._items_in_current_page = getattr(self_iter, '_items_in_current_page', 0) + 1
            page_size = getattr(self_iter, '_page_size', None)
            if page_size and self_iter._items_in_current_page == page_size:
                self_iter._finished_current_page = True
            return result
        finally:
            self_iter._patched_next_active = False

    def patched_load_url(self_iter, url):
        self_iter._page_num = getattr(self_iter, '_page_num', 0) + 1
        self_iter._items_in_current_page = 0
        self_iter._finished_current_page = False
        ctx.current_paper_page_count += 1
        # Derive page number from URL start= param so it matches the actual page
        # being fetched regardless of retries or iterator resets.
        import re as _re2
        _start_m = _re2.search(r'[?&]start=(\d+)', url)
        _url_page = int(_start_m.group(1)) // 10 + 1 if _start_m else 1
        if _url_page > 1:
            run_log.debug(f"Pagination (page {_url_page})", module="http", indent=2)

        for session in (nav._session1, nav._session2):
            session.headers['referer'] = ctx.last_scholar_url

        if ctx.total_page_count >= ctx.next_break_at:
            d = random.uniform(MANDATORY_BREAK_MIN, MANDATORY_BREAK_MAX) * ctx.delay_scale
            wait_str = ctx.wait_status_fn() if ctx.wait_status_fn else ""
            run_log.info(f"Mandatory break after {ctx.total_page_count} pages "
                         f"({d/60:.1f} min)... [{wait_str}]", module="http", indent=2)
            time.sleep(d)
            ctx.next_break_at = (ctx.total_page_count
                                 + random.randint(MANDATORY_BREAK_EVERY_MIN, MANDATORY_BREAK_EVERY_MAX))
            if not ctx.interactive_captcha and ctx.refresh_session_fn:
                run_log.info("Refreshing session after break...", module="http", indent=2)
                ctx.refresh_session_fn()
            ctx.next_refresh_at = (ctx.total_page_count
                                   + random.randint(SESSION_REFRESH_MIN, SESSION_REFRESH_MAX))
        elif ctx.total_page_count >= ctx.next_refresh_at:
            if not ctx.interactive_captcha and ctx.refresh_session_fn:
                run_log.info(f"Refreshing session (after {ctx.total_page_count} pages)...",
                             module="http", indent=1)
                ctx.refresh_session_fn()
            ctx.next_refresh_at = (ctx.total_page_count
                                   + random.randint(SESSION_REFRESH_MIN, SESSION_REFRESH_MAX))

        ctx.current_attempt_url = (
            f'https://scholar.google.com{url}' if url.startswith('/') else url)

        result = original_load_url(self_iter, url)

        # scholarly's _load_url only captures 'gs_r gs_or gs_scl' and
        # 'gsc_mpat_ttl'.  Some results use 'gs_r gs_or' without the
        # 'gs_scl' suffix and are silently dropped.  Expand to 'gs_r gs_or'
        # (which also matches gs_r gs_or gs_scl) + 'gsc_mpat_ttl'.
        # Keep it narrower than plain 'div.gs_r' so error-page elements
        # are not mistaken for result rows.
        try:
            soup = getattr(self_iter, '_soup', None)
            if soup:
                self_iter._rows = (
                    soup.select('div.gs_r.gs_or')
                    + soup.find_all('div', class_='gsc_mpat_ttl')
                )
        except Exception:
            pass

        page_size = None
        try:
            page_size = len(getattr(self_iter, '_rows', []) or [])
        except Exception:
            page_size = None
        self_iter._page_size = page_size if page_size and page_size > 0 else None
        if page_size:
            run_log.debug(f"Page items: {page_size}", module="http", indent=2)
        elif is_citation_list_url(url):
            # A citation page with zero result rows used to be logged silently,
            # which made a blocked/empty response look like a genuinely empty
            # citation list.
            record_zero_row_citation_page(ctx, soup)

        ctx.last_scholar_url = ctx.current_attempt_url
        return result

    _SearchScholarIterator.__init__ = patched_init
    _SearchScholarIterator.__next__ = patched_next
    _SearchScholarIterator._load_url = patched_load_url

    # Year-segment skipping/tracking deliberately lives in
    # citation_fetch.YearFetchSession.  The old scholarly._citedby_long patch
    # never ran: scholarly only reaches _citedby_long for objects with a 'bib'
    # key (citation_fetch builds the citedby stub without it) and the year path
    # constructs _SearchScholarIterator directly.  Removed rather than kept as
    # unreachable code.

    # Patch _citation_pub: also extract pub_url and bib['author'].
    # Scholarly's _citation_pub (used for AUTHOR_PUBLICATION_ENTRY) only
    # reads the venue from gs_gray[1] and discards the authors in
    # gs_gray[0]; it also never saves the title link href as pub_url.
    # Both are available in the HTML that is already parsed here, so
    # we inject them without any additional network requests.
    original_citation_pub = PublicationParser._citation_pub

    def patched_citation_pub(self, __data, publication):
        result = original_citation_pub(self, __data, publication)
        title_link = __data.find('a', class_='gsc_a_at')
        if title_link and title_link.get('href'):
            href = title_link['href']
            if href.startswith('/'):
                href = f'https://scholar.google.com{href}'
            result['pub_url'] = href
        author_divs = __data.find_all('div', class_='gs_gray')
        if len(author_divs) >= 1:
            author_text = author_divs[0].text.strip()
            if author_text:
                result['bib']['author'] = author_text
        return result

    PublicationParser._citation_pub = patched_citation_pub

    # Fail loudly at startup when a scholarly upgrade renamed (or re-created)
    # one of the patched hooks: a silently ineffective patch would disable
    # browser headers, rate-limit waits or pagination parsing without any
    # visible error.
    assert_scholarly_patches_active([
        ('nav._max_retries', getattr(nav, '_max_retries', None), 1),
        ('nav._TIMEOUT', getattr(nav, '_TIMEOUT', None), 30),
        ('nav._get_page', nav._get_page, patched_get_page),
        ('nav._new_session', nav._new_session, patched_new_session),
        ('nav.pm1._handle_captcha2', nav.pm1._handle_captcha2, patched_handle_captcha2),
        ('nav.pm2._handle_captcha2', nav.pm2._handle_captcha2, patched_handle_captcha2),
        ('_SearchScholarIterator.__next__', _SearchScholarIterator.__next__, patched_next),
        ('_SearchScholarIterator._load_url', _SearchScholarIterator._load_url, patched_load_url),
        ('PublicationParser._citation_pub', PublicationParser._citation_pub, patched_citation_pub),
    ])


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

    # Clear any previous probe result up front.  The reset used to happen only
    # after a page came back, so a probe that failed on every attempt returned
    # None while leaving the previous paper's histogram on the context — the
    # next paper then judged its years against someone else's counts.
    ctx.probed_year_counts = None
    ctx.probed_year_count_complete = False

    while True:
        attempt += 1
        for session in (nav._session1, nav._session2):
            session.headers['referer'] = ctx.last_scholar_url
        ctx.current_attempt_url = full_url

        try:
            # nav._get_soup() goes through nav._get_page(), i.e. through
            # patched_get_page, which already counts this page exactly once.
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
