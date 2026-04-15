"""
crawler/fetch_context.py — Per-paper fetch state dataclass.

FetchContext collects all mutable state that _fetch_citations_with_progress
and _fetch_by_year exchange through implicit self.xxx attributes.  Passing it
explicitly makes the data flow visible and lets the two functions live in a
separate module without depending on PaperCitationFetcher.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FetchContext:
    """
    All per-paper state for a single citation-fetch attempt.

    One FetchContext is created per paper at the start of
    _fetch_citations_with_progress and passed down to _fetch_by_year.
    Fields are read and written directly; no accessor methods.
    """

    # --- year-segment progress -------------------------------------------------
    # set of integer years already fully fetched in the current run
    completed_year_segments: set = field(default_factory=set)
    # {year_int: start_index} — resume position for the in-progress year
    partial_year_start: dict = field(default_factory=dict)
    # the year currently being streamed by _citedby_long (None outside fetch)
    current_year_segment: Optional[int] = None

    # --- probe metadata -------------------------------------------------------
    # {year_int: count} from the Scholar histogram; None until probed
    probed_year_counts: Optional[dict] = None
    # True when histogram sums to >= scholar total (complete coverage known)
    probed_year_count_complete: bool = False

    # --- cached year distribution (from the on-disk cache snapshot) ----------
    # {year_int: count} loaded at the start of each paper fetch
    cached_year_counts: Optional[dict] = None

    # --- dedup / progress counters -------------------------------------------
    # floor = dedup count from last save; incremented as new dupes are found
    dedup_count: int = 0
    # diagnostics written after each year completes
    year_fetch_diagnostics: dict = field(default_factory=dict)
