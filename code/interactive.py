"""
code/interactive.py — Manual captcha recovery and proxy-switch prompts.

All public functions are pure (no class state).  PaperCitationFetcher keeps
thin wrapper methods so existing call sites and tests don't need to change.
"""

import os
import re
import sys
import time
from datetime import datetime

from scholarly import scholarly

from code.scholarly_session import (
    complete_browser_fingerprint as _int_complete_browser_fingerprint,
)


# ---------------------------------------------------------------------------
# Cookie file persistence
# ---------------------------------------------------------------------------

def save_curl_to_file(curl_str, path):
    """Save a raw cURL string to *path* for reuse in future sessions.

    Written 0600: the string carries live Scholar session cookies (and the
    browser-identity headers), so it must not be world-readable — same rule the
    downloader applies to its cookies.txt.
    """
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(curl_str)
        try:
            os.chmod(path, 0o600)      # 已有文件可能是宽松权限，纠正过来
        except OSError:
            pass
    except Exception as e:
        print(f"  (Could not save curl to {path}: {e})", flush=True)


def load_curl_from_file(path):
    """Return the raw cURL string saved by save_curl_to_file, or None."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
        return content if content else None
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"  (Could not load curl from {path}: {e})", flush=True)
        return None


def _strip_control(value):
    """Drop CR/LF/TAB from a pasted cookie name/value.

    A value carrying a newline would otherwise leak into the saved cURL file and
    into every later request header, so it can corrupt a whole session.
    """
    return re.sub(r'[\r\n\t]+', '', str(value))


# ---------------------------------------------------------------------------
# Cookie / header injection from a cURL command
# ---------------------------------------------------------------------------

def inject_cookies_from_curl(
    curl_str,
    *,
    curl_header_allowlist,
    last_scholar_url,
    injected_cookies_ref,       # dict to update in-place
    injected_header_overrides_ref,  # dict to update in-place
    curl_save_path=None,        # if set, save curl_str here after success
):
    """
    Parse cookies and allowlisted headers from a pasted cURL command and inject
    them into the scholarly HTTP sessions.

    Returns the number of cookies injected (0 on failure).

    *_ref parameters are mutable containers whose first element/key set is
    updated in-place so the caller's state is reflected after the call.
    """
    def _parse_cookies(raw):
        cookies = {}
        for pair in raw.split(';'):
            pair = pair.strip()
            if '=' in pair:
                k, v = pair.split('=', 1)
                k = _strip_control(k).strip()
                v = _strip_control(v).strip()
                if k:
                    cookies[k] = v
        return cookies

    m = (re.search(r"(?:-b|--cookie) '([^']+)'", curl_str) or
         re.search(r'(?:-b|--cookie) "([^"]+)"', curl_str))
    cookies = {}
    if m:
        cookies = _parse_cookies(m.group(1))
    else:
        # Modern browsers (Chrome/Edge) use -H 'cookie: ...' format
        cookie_header = (re.search(r"(?:-H|--header)\s+'cookie:\s*([^']+)'", curl_str, re.IGNORECASE) or
                         re.search(r'(?:-H|--header)\s+"cookie:\s*([^"]+)"', curl_str, re.IGNORECASE))
        if cookie_header:
            cookies = _parse_cookies(cookie_header.group(1))
    if not cookies:
        print("  (Could not find cookies in input — expected -b/--cookie or -H 'cookie:' format)", flush=True)
        return 0

    nav = scholarly._Scholarly__nav
    header_overrides = {}
    header_matches = re.findall(
        r"(?:-H|--header) '([^']+)'|(?:-H|--header) \"([^\"]+)\"", curl_str
    )
    for single_quoted, double_quoted in header_matches:
        header_line = (single_quoted or double_quoted or '').strip()
        if not header_line or ':' not in header_line:
            continue
        name, value = header_line.split(':', 1)
        header_name = name.strip().lower()
        if header_name in curl_header_allowlist:
            header_overrides[header_name] = value.strip()

    # Adopt the browser-identity trio (user-agent / sec-ch-ua /
    # sec-ch-ua-full-version-list) from this cURL as a coherent unit, so the
    # fingerprint always matches the browser that issued the cookies.
    _int_complete_browser_fingerprint(header_overrides)

    for session in (nav._session1, nav._session2):
        # Clear stale cookies from previous (failed) requests so only
        # the freshly injected cookies are sent.
        try:
            session.cookies.clear()
        except Exception:
            pass
        for k, v in cookies.items():
            session.cookies.set(k, v)
        session.headers.update(header_overrides)
        session.headers['referer'] = last_scholar_url
        # Cookies come from a browser already on Scholar → same-origin.
        # Sending 'none' (cross-site) with valid session cookies is an
        # inconsistency that Google's bot detection flags.
        session.headers['sec-fetch-site'] = 'same-origin'

    # Persist so patched session rebuilds can reuse them after a 403
    injected_cookies_ref.clear()
    injected_cookies_ref.update(cookies)
    injected_header_overrides_ref.clear()
    injected_header_overrides_ref.update(header_overrides)

    nav.got_403 = False

    header_note = (f", {len(header_overrides)} allowlisted headers"
                   if header_overrides else '')
    print(
        f"  Injected {len(cookies)} cookies{header_note} (no domain restriction).",
        flush=True,
    )
    if curl_save_path:
        save_curl_to_file(curl_str, curl_save_path)
        print(f"  Saved cookies to {curl_save_path}", flush=True)
    return len(cookies)


# ---------------------------------------------------------------------------
# Optional: harvest cookies from a real browser instead of copy-as-cURL
# ---------------------------------------------------------------------------

def _playwright_available():
    """True only if playwright can actually be imported in this interpreter."""
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except Exception:
        return False


def _build_curl_from_page(page, cookies, url):
    """Synthesize a `curl ... -H 'cookie: ...'` string from a live browser page.

    Reusing the project's own cURL format means the harvested session goes
    through exactly the same parse/inject/save path as a manual paste
    (allowlist headers, browser-identity trio, curl.txt persistence).
    """
    import shlex

    cookie_header = '; '.join(f"{c['name']}={c['value']}" for c in cookies
                             if c.get("name") and c.get("value") is not None)
    ident = {}
    try:
        ident = page.evaluate("""async () => {
            const d = navigator.userAgentData;
            const out = {ua: navigator.userAgent};
            if (d && d.getHighEntropyValues) {
                const h = await d.getHighEntropyValues(['fullVersionList', 'platform', 'mobile']);
                out.brands = (h.brands || d.brands || []).map(b => `"${b.brand}";v="${b.version}"`).join(', ');
                out.full = (h.fullVersionList || []).map(b => `"${b.brand}";v="${b.version}"`).join(', ');
                out.mobile = h.mobile ? '?1' : '?0';
                out.platform = `"${h.platform || ''}"`;
            }
            return out;
        }""")
    except Exception as e:
        print(f"  (Could not read browser identity: {e})", flush=True)

    parts = ["curl", shlex.quote(url or "")]
    headers = [("cookie", cookie_header)]
    if ident.get("ua"):
        headers.append(("user-agent", ident["ua"]))
    if ident.get("brands"):
        headers.append(("sec-ch-ua", ident["brands"]))
    if ident.get("full"):
        headers.append(("sec-ch-ua-full-version-list", ident["full"]))
    if ident.get("mobile"):
        headers.append(("sec-ch-ua-mobile", ident["mobile"]))
    if ident.get("platform"):
        headers.append(("sec-ch-ua-platform", ident["platform"]))
    for name, value in headers:
        if value:
            parts.append(f"-H {shlex.quote(f'{name}: {value}')}")
    return ' '.join(parts)


def try_browser_harvest(url, *, inject_fn):
    """Open a visible browser, let the human solve the block, then harvest.

    Returns True when cookies were harvested and injected.  Returns False when
    playwright is unavailable / stdin is not a TTY / the human chose the manual
    paste / the browser could not be driven — every one of those paths falls
    back to the existing "paste a cURL" flow, so nothing regresses.

    The point: on a machine that has playwright, the human no longer has to find
    the request in DevTools and copy it — solving the challenge in the window is
    enough.  (The downloader gained the same trick for publisher sites.)
    """
    if not _playwright_available() or not sys.stdin.isatty():
        return False

    from playwright.sync_api import sync_playwright

    edge = "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    executable = edge if os.path.isfile(edge) else chrome
    args = ["--disable-blink-features=AutomationControlled"]
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        args.append("--no-sandbox")

    print("  Opening a visible browser so you can solve the block...", flush=True)
    print("  (If the browser cannot be opened, just paste a cURL as usual.)", flush=True)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=False,
                executable_path=executable if os.path.isfile(executable) else None,
                args=args,
            )
            ctx = browser.new_context()
            page = ctx.new_page()
            try:
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
            except Exception as e:
                print(f"  (Navigation: {e})", flush=True)
            print("  Solve the captcha / log in there, then press Enter here.", flush=True)
            print("  > ", end='', flush=True)
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                print(flush=True)
                browser.close()
                return False
            cookies = ctx.cookies(url) or []
            if not cookies:
                cookies = ctx.cookies() or []
            curl_str = _build_curl_from_page(page, cookies, url)
            browser.close()
    except Exception as e:
        print(f"  (Browser harvest unavailable: {type(e).__name__}: {e})", flush=True)
        return False

    if not cookies:
        print("  (No cookies in that browser session — falling back to paste.)", flush=True)
        return False
    print(f"  Harvested {len(cookies)} cookies from the browser session.", flush=True)
    return inject_fn(curl_str) > 0

# ---------------------------------------------------------------------------
# Shared multiline input reader (used by both first-page prompt and captcha)
# ---------------------------------------------------------------------------

def _read_multiline_input(timeout_after_silence=3.0):
    """
    Read a multiline paste from stdin using non-canonical TTY mode.

    Returns a list of non-empty lines, or [] if the user pressed Enter
    immediately (skip) or stdin is not a TTY.
    """
    import select as _sel
    import os as _os
    import re as _re
    _ANSI = _re.compile(r'\x1b(?:\[[0-9;?]*[a-zA-Z]|[()][AB012])')

    fd = None
    try:
        fd = sys.stdin.fileno()
    except (AttributeError, OSError, ValueError):
        # stdin is not a real file descriptor (pipe/StringIO/closed): there is
        # nobody who could paste a cURL, so report "no input" instead of
        # raising out of the caller's exception handler.
        return []
    old_attrs = None
    try:
        import termios as _termios
        old_attrs = _termios.tcgetattr(fd)
    except Exception:
        pass

    chunks = []
    try:
        if old_attrs is not None:
            new = list(old_attrs)
            new[3] = new[3] & ~_termios.ICANON
            new[6] = list(new[6])
            new[6][_termios.VMIN] = 1
            new[6][_termios.VTIME] = 0
            _termios.tcsetattr(fd, _termios.TCSAFLUSH, new)

        _sel.select([sys.stdin], [], [])

        while True:
            ready = _sel.select([sys.stdin], [], [], timeout_after_silence)[0]
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
        return []
    finally:
        if old_attrs is not None:
            import termios as _t
            _t.tcsetattr(fd, _t.TCSADRAIN, old_attrs)

    print(flush=True)
    raw = ''.join(chunks)
    raw = _ANSI.sub('', raw)
    raw = raw.replace('\r\n', '\n').replace('\r', '\n')

    lines = []
    for line in raw.split('\n'):
        line = line.rstrip()
        if not line.strip():
            if lines:
                break
            continue
        lines.append(line)
    return lines


def _inject_with_cookie_retry(inject_fn, skip_message, retry_message,
                              harvest_fn=None, harvest_url=None, reader=None):
    """
    Read a pasted cURL and inject its cookies, re-prompting if the paste
    contains no cookies (a common mis-copy).

    *inject_fn* returns the number of cookies injected (0 = none found).
    Pressing Enter immediately skips and returns False.  Otherwise loops
    until cookies are found (returns True) or the user skips.
    """
    while True:
        print("  > ", end='', flush=True)
        lines = (reader or _read_multiline_input)()
        if not lines:
            print(f"  {skip_message}", flush=True)
            return False
        if harvest_fn is not None and len(lines) == 1 and lines[0].strip().lower() == 'browser':
            if harvest_fn(harvest_url):
                return True
            print("  (Browser harvest did not produce cookies — paste a cURL instead.)",
                  flush=True)
            continue
        print(f"  Received {len(lines)} lines. Processing...", flush=True)
        result = inject_fn('\n'.join(lines))
        if result > 0:
            return True
        # inject_fn returned 0 — no cookies in the paste.  Re-prompt so a
        # mis-copied request (e.g. a non-Scholar resource) can be re-entered.
        print(f"  {retry_message}", flush=True)


# ---------------------------------------------------------------------------
# First-page proactive cookie prompt
# ---------------------------------------------------------------------------

def prompt_first_curl(*, inject_fn, harvest_fn=None):
    """
    Prompt the user to paste a browser cURL before the first Scholar request.

    Called automatically when no saved cookies are available.  The user can
    press Enter immediately to skip; the program then proceeds without cookies
    (possibly receiving fewer results from Scholar).

    If the paste contains no cookies (a common mis-copy), the prompt repeats
    until the user pastes a valid cURL or presses Enter to skip.

    Returns True if cookies were successfully injected.
    """
    if not sys.stdin.isatty():
        return False

    sep = "  " + "=" * 62
    print(f"\n{sep}", flush=True)
    print(f"  No saved Scholar cookies found.", flush=True)
    print(f"  Providing cookies may give more complete results.", flush=True)
    print(f"  1. Open any Scholar page in your browser", flush=True)
    print(f"  2. F12 → Network → right-click the Scholar request", flush=True)
    print(f"     → Copy as cURL (bash)", flush=True)
    print(f"  3. Paste here (detected automatically after 3s silence)", flush=True)
    print(f"     Press Enter immediately to skip.", flush=True)
    if _playwright_available():
        print(f"  — or type 'browser' + Enter: I open a browser window, you solve it,"
              f" and I harvest the cookies automatically.", flush=True)
    print(f"{sep}", flush=True)

    return _inject_with_cookie_retry(
        inject_fn,
        harvest_fn=harvest_fn,
        harvest_url=None,
        skip_message="(Skipped — proceeding without cookies)",
        retry_message=("(No cookies found in your paste — you may have copied "
                        "the wrong request.  Re-copy from DevTools and paste "
                        "again, or press Enter to skip.)"),
    )


# ---------------------------------------------------------------------------
# Interactive captcha prompt
# ---------------------------------------------------------------------------

def try_interactive_captcha(url, *, inject_fn, harvest_fn=None):
    """
    Prompt the user to solve a captcha manually and inject the resulting
    cookies via *inject_fn(curl_str) -> int*.

    *inject_fn* is a callable that accepts a raw cURL string and returns the
    number of cookies injected (0 = failure).  Passing a lambda lets callers
    bind their session state without this module knowing about it.

    If the paste contains no cookies (a common mis-copy), the prompt repeats
    until the user pastes a valid cURL or presses Enter to skip.

    Returns True if cookies were successfully injected.
    """
    if not sys.stdin.isatty():
        # Unattended run (cron/nohup/redirected stdin): prompting is impossible
        # and would raise out of the recovery path.  Fall back to automatic
        # waiting instead.
        print("\n  Captcha / block detected, but stdin is not a terminal — "
              "cannot prompt for a cURL. Falling back to automatic waiting.",
              flush=True)
        return False

    sep = "  " + "=" * 62
    print(f"\n{sep}", flush=True)
    print(f"  Captcha / block detected. Resolve it manually:", flush=True)
    print(f"  1. Open this URL in your browser:", flush=True)
    print(f"       {url}", flush=True)
    print(f"  2. Solve the captcha, then let the page load fully", flush=True)
    print(f"  3. F12 → Network → find the Scholar request", flush=True)
    print(f"     → right-click → Copy as cURL (bash)", flush=True)
    print(
        f"  4. Paste the cURL here "
        f"(cookies + selected headers reused; detected automatically after 3s silence)",
        flush=True,
    )
    print(f"     (Press Enter on blank line to skip)", flush=True)
    if _playwright_available():
        print(f"  — or type 'browser' + Enter: I open this URL in a browser window,"
              f" you solve it, and I harvest the cookies automatically.", flush=True)
    print(f"{sep}", flush=True)

    return _inject_with_cookie_retry(
        inject_fn,
        harvest_fn=harvest_fn,
        harvest_url=url,
        skip_message="(Skipped — using automatic wait)",
        retry_message=("(No cookies found in your paste — you may have copied "
                        "the wrong request.  Re-copy from DevTools and paste "
                        "again, or press Enter to skip.)"),
    )


# ---------------------------------------------------------------------------
# Proxy-switch wait loop
# ---------------------------------------------------------------------------

def wait_proxy_switch(max_hours=24):
    """
    Block up to *max_hours* waiting for the user to type 'ok' to confirm a
    proxy switch.  Returns True if confirmed, False if timed out.
    """
    import select as _select
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"\n  [{now}] Scholar is blocking this IP.", flush=True)
    print(f"  Please switch your proxy/IP, then type  ok  and press Enter.", flush=True)
    print(f"  (Program will retry automatically after {max_hours}h if no input.)",
          flush=True)

    deadline = time.time() + max_hours * 3600
    last_reminder = time.time()
    CHECK_SEC = 60
    REMIND_SEC = 3600

    while time.time() < deadline:
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
                line = sys.stdin.readline()
                if line == '':
                    # EOF: stdin is closed or redirected from an empty file.
                    # Nobody can ever type 'ok', and select() would keep
                    # reporting readable, so stop instead of spinning for hours.
                    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    print(f"  [{ts}] stdin closed — not waiting for a proxy switch.",
                          flush=True)
                    return False
                if line.strip().lower() == 'ok':
                    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    print(f"  [{ts}] Proxy switch confirmed. Resuming...", flush=True)
                    return True
        except Exception:
            time.sleep(wait)

    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"  [{ts}] {max_hours}h elapsed. Resuming...", flush=True)
    return False
