"""Centralised configuration for the Google Scholar citation crawler.

All tunable parameters — rate-limiting, thresholds, paths — live here so future
maintainers have a single place to discover them.

Constants that previously lived in ``crawler.common`` are imported here and
re-exported from ``common`` for backward compatibility.  New code should import
directly from ``crawler.config``.
"""

import os

# ============================================================
# Directory paths
# ============================================================

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(ROOT_DIR, "output")
LOGS_DIR = os.path.join(OUTPUT_DIR, "logs")

# ============================================================
# Rate-limiting & anti-ban
# ============================================================

# Randomised delay between successive page requests (seconds).
DELAY_MIN = 45
DELAY_MAX = 90

# Soft session refresh every N pages to avoid session-based blocking.
SESSION_REFRESH_MIN = 10
SESSION_REFRESH_MAX = 20

# Mandatory long break after every N pages to let Scholar's rate-limit
# window reset.
MANDATORY_BREAK_EVERY_MIN = 8
MANDATORY_BREAK_EVERY_MAX = 12
MANDATORY_BREAK_MIN = 180   # 3 minutes
MANDATORY_BREAK_MAX = 360   # 6 minutes

# ============================================================
# Fetch thresholds
# ============================================================

# Papers with >= this many total citations switch to year-by-year fetch
# mode (better resume granularity).
YEAR_BASED_THRESHOLD = 50

# Google Scholar returns 10 results per page.
SCHOLAR_PAGE_SIZE = 10

# Maximum retries when Scholar blocks a citation fetch.
MAX_RETRIES = 3

# A paper whose fetch keeps producing the same outcome (same seen/target, e.g.
# Scholar lists fewer rows than its own "Cited by" count) stops being retried
# once this many consecutive identical outcomes have been recorded.  The stall
# clears itself as soon as the outcome changes — a larger Scholar count changes
# the signature — so a stalled paper is picked up again when Scholar's numbers
# move.
STALL_AFTER_ATTEMPTS = 2

# ============================================================
# API / network
# ============================================================

SCHOLAR_BASE_URL = "https://scholar.google.com"

# ============================================================
# Output
# ============================================================

OUTPUT_ENCODING = "utf-8"
