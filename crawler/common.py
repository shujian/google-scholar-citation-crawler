import os
import random
import re
from datetime import datetime

# Unified delay: all deliberate waits use a random value in this range
DELAY_MIN = 45
DELAY_MAX = 90

# Proactively refresh session every N pages to avoid session-based blocking
SESSION_REFRESH_MIN = 10
SESSION_REFRESH_MAX = 20

# Mandatory long break every 8-12 pages to let Scholar's rate-limit window reset
MANDATORY_BREAK_EVERY_MIN = 8
MANDATORY_BREAK_EVERY_MAX = 12
MANDATORY_BREAK_MIN = 180
MANDATORY_BREAK_MAX = 360

# Papers with >= this many citations use year-based fetching for better resume
YEAR_BASED_THRESHOLD = 50

# Google Scholar citation pages are paginated in fixed 10-result chunks.
SCHOLAR_PAGE_SIZE = 10

# Retry: when Scholar blocks a citation fetch, retry with fresh session
MAX_RETRIES = 3


def rand_delay(scale=1.0):
    """Return a random delay between DELAY_MIN and DELAY_MAX seconds."""
    return random.uniform(DELAY_MIN, DELAY_MAX) * scale


def now_str():
    """Return current time as [HH:MM:SS] string for log prefixes."""
    return datetime.now().strftime('[%H:%M:%S]')


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


def setup_proxy():
    """Configure proxy from environment variables."""
    proxy_url = os.environ.get('https_proxy') or os.environ.get('http_proxy')
    if not proxy_url:
        print('Warning: No proxy detected, connecting directly')
        return
    clean = proxy_url.replace('http://', '').replace('https://', '')
    print(f'Proxy detected: {clean} (using system env proxy)')


def extract_author_id(author_input):
    """Extract author ID from a Google Scholar URL or bare ID string."""
    match = re.search(r'user=([^&]+)', author_input)
    if match:
        return match.group(1)
    if re.match(r'^[\w-]+$', author_input):
        return author_input
    raise ValueError(f'Cannot extract author ID from: {author_input}')
