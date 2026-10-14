import json
import os
import random
import re
import tempfile
from datetime import datetime

# ---- Backward-compatible re-exports from code.config ----
# Constants have been centralised in code.config.  Existing imports from
# code.common continue to work.  New code should import from code.config
# directly.
from code.config import (   # noqa: F401 — re-exported for compatibility
    CITE_FORMATS,
    DELAY_MAX,
    DELAY_MIN,
    MANDATORY_BREAK_EVERY_MAX,
    MANDATORY_BREAK_EVERY_MIN,
    MANDATORY_BREAK_MAX,
    MANDATORY_BREAK_MIN,
    MAX_RETRIES,
    SCHOLAR_PAGE_SIZE,
    SESSION_REFRESH_MAX,
    SESSION_REFRESH_MIN,
    STALL_AFTER_ATTEMPTS,
    YEAR_BASED_THRESHOLD,
    resolve_cite_formats,
    resolve_proxy_config,
)
from code.lib_env import (   # noqa: F401 — re-exported for compatibility
    NO_PROXY_ENV_KEYS,
    PROXY_ENV_KEYS,
    export_proxy_env,
    sanitize_no_proxy,
)


def rand_delay(scale=1.0):
    """Return a random delay between DELAY_MIN and DELAY_MAX seconds."""
    return random.uniform(DELAY_MIN, DELAY_MAX) * scale


def now_str():
    """Return current time as [HH:MM:SS] string for log prefixes."""
    return datetime.now().strftime('[%H:%M:%S]')


def write_json_atomic(path, payload, **dump_kwargs):
    """Write *payload* as JSON to *path* through a temp file + atomic rename.

    A crash, Ctrl+C or full disk during the write can then never leave a
    truncated file behind.  That matters because a half-written state file is
    read back as "no state at all" (the citation output) or as a hard parse
    error (the profile), costing a full re-crawl.
    """
    directory = os.path.dirname(os.path.abspath(path)) or '.'
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=directory, prefix=os.path.basename(path) + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, **dump_kwargs)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _scholar_request_url(pagerequest):
    """Best-effort extraction of the target Google Scholar URL for logging."""
    if pagerequest is None:
        return None
    candidate = getattr(pagerequest, 'url', None)
    if candidate is None and isinstance(pagerequest, dict):
        candidate = pagerequest.get('url') or pagerequest.get('URL')
    if candidate is None:
        candidate = pagerequest
    if not isinstance(candidate, str):
        candidate = str(candidate)
    candidate = candidate.strip()
    if not candidate:
        return None
    if candidate.startswith('/'):
        return f'https://scholar.google.com{candidate}'
    return candidate


class TeeStream:
    """Mirror stdout writes to both terminal and a log file."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            stream.write(data)
        return len(data)

    def flush(self):
        for stream in self.streams:
            stream.flush()

    def isatty(self):
        return any(getattr(stream, 'isatty', lambda: False)() for stream in self.streams)


def setup_proxy(proxy_url=None, no_proxy=None):
    """Report the effective proxy configuration.

    The values come from ``--proxy-url`` / ``--no-proxy`` (command line) or from
    ``crawler_config.json``; see ``code.config.resolve_proxy_config``.  When both
    arguments are ``None`` the config file and, as a last resort, the ambient
    ``https_proxy`` / ``http_proxy`` / ``no_proxy`` values are consulted — that
    environment fallback is the HTTP ecosystem convention (``httpx`` and
    ``requests`` read it themselves), **not** a requirement of this program.
    """
    if proxy_url is None and no_proxy is None:
        resolved = resolve_proxy_config()
        proxy_url, no_proxy = resolved.proxy_url, resolved.no_proxy
    if not proxy_url:
        print('Proxy: none configured, connecting directly')
    else:
        clean = proxy_url.replace('http://', '').replace('https://', '')
        print(f'Proxy: {clean} (configured via --proxy-url / crawler_config.json / system proxy)')
    if no_proxy:
        print(f'Proxy bypass (no_proxy): {no_proxy}')


def extract_author_id(author_input):
    """Extract author ID from a Google Scholar URL or bare ID string."""
    match = re.search(r'user=([^&]+)', author_input)
    if match:
        return match.group(1)
    if re.match(r'^[\w-]+$', author_input):
        return author_input
    raise ValueError(f'Cannot extract author ID from: {author_input}')
