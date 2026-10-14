"""``code/venue_key.py`` — **thin re-export** of the shared host module.

The *unique entity* of the venue-identity rule lives in
``google-scholar-citation-crawler/code/shared/venue_key.py``; this project is the
host (it is the earliest stage that needs the rule) and the other stages consume
the same file through their own ``code/_shared.py`` as ``shared.venue_key``.

This module only re-exports it so the historical import path
``from code.venue_key import …`` keeps working.  **Never add an implementation
here** — ``tests/test_shared_venue_key.py`` asserts that this file contains no
copy of the rule (see root ``AGENTS.md`` → 「跨仓库共享代码」).
"""

from code.shared.venue_key import *  # noqa: F401,F403  (public names)
from code.shared.venue_key import (  # noqa: F401  (private / non-`__all__` names)
    _BIBTEX_VENUE_FIELDS,
    _MATCH_TABLE,
    _MISSING_VENUE_VALUES,
    _VENUE_YEAR_RE,
    _YEAR_MAX,
    _YEAR_MIN,
    _build_match_table,
)
