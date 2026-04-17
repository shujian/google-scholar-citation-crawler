"""
crawler/interactive.py — Manual captcha recovery and proxy-switch prompts.

All public functions are pure (no class state).  PaperCitationFetcher keeps
thin wrapper methods so existing call sites and tests don't need to change.
"""

import re
import sys
import time
from datetime import datetime

from scholarly import scholarly


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
    captcha_solved_count_ref,   # list[int] as a mutable counter
):
    """
    Parse cookies and allowlisted headers from a pasted cURL command and inject
    them into the scholarly HTTP sessions.

    Returns the number of cookies injected (0 on failure).

    *_ref parameters are mutable containers whose first element/key set is
    updated in-place so the caller's state is reflected after the call.
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

    for session in (nav._session1, nav._session2):
        for k, v in cookies.items():
            session.cookies.set(k, v)
        session.headers.update(header_overrides)
        session.headers['referer'] = last_scholar_url

    # Persist so patched session rebuilds can reuse them after a 403
    injected_cookies_ref.clear()
    injected_cookies_ref.update(cookies)
    injected_header_overrides_ref.clear()
    injected_header_overrides_ref.update(header_overrides)

    nav.got_403 = False
    captcha_solved_count_ref[0] += 1

    header_note = (f", {len(header_overrides)} allowlisted headers"
                   if header_overrides else '')
    print(
        f"  Injected {len(cookies)} cookies{header_note} (no domain restriction). "
        f"Captcha solves: {captcha_solved_count_ref[0]}",
        flush=True,
    )
    return len(cookies)


# ---------------------------------------------------------------------------
# Interactive captcha prompt
# ---------------------------------------------------------------------------

def try_interactive_captcha(url, *, inject_fn):
    """
    Prompt the user to solve a captcha manually and inject the resulting
    cookies via *inject_fn(curl_str) -> int*.

    *inject_fn* is a callable that accepts a raw cURL string and returns the
    number of cookies injected (0 = failure).  Passing a lambda lets callers
    bind their session state without this module knowing about it.

    Returns True if cookies were successfully injected.
    """
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
    print(f"{sep}", flush=True)
    print("  > ", end='', flush=True)

    import select as _sel
    import os as _os
    import re as _re
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
            new = list(old_attrs)
            new[3] = new[3] & ~_termios.ICANON
            new[6] = list(new[6])
            new[6][_termios.VMIN] = 1
            new[6][_termios.VTIME] = 0
            _termios.tcsetattr(fd, _termios.TCSAFLUSH, new)

        _sel.select([sys.stdin], [], [])

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

    if not lines:
        print("  (Skipped — using automatic wait)", flush=True)
        return False
    print(f"  Received {len(lines)} lines. Processing...", flush=True)
    return inject_fn('\n'.join(lines)) > 0


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
                line = sys.stdin.readline().strip().lower()
                if line == 'ok':
                    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    print(f"  [{ts}] Proxy switch confirmed. Resuming...", flush=True)
                    return True
        except Exception:
            time.sleep(wait)

    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"  [{ts}] {max_hours}h elapsed. Resuming...", flush=True)
    return False
