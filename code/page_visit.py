"""
code/page_visit.py — PageVisit: single Scholar page fetch with error recovery.

Every page-level HTTP request goes through PageVisit.fetch().  It handles:
  - captcha detection & interactive cookie injection
  - rate-limit waits with randomised delays
  - proxy-switch prompts (non-interactive mode)
  - transient network errors

KeyboardInterrupt always propagates immediately so the caller can save
state before exiting.
"""

import random
import time
from datetime import datetime

from code.common import rand_delay, now_str


#: HTTP statuses that mean "this record does not exist", as opposed to
#: "you are blocked / rate-limited".  404 is what Scholar answers for a Cite
#: dialog whose cluster handle it does not serve (measured 2026-10-16:
#: ``info:44BcOmzPt1MJ:scholar.google.com/&output=cite`` returned Google's own
#: 404 page, while two sibling handles answered 200 with five formats each in
#: the same minute); 410 is the same class of answer.
MISSING_RECORD_STATUSES = (404, 410)

#: Status of the response that produced the failure, when the caller could not
#: tell otherwise.  Attribution order: the exception itself (our patch layer
#: attaches the recorded status), then the session context.
_STATUS_ATTR = 'http_status'


class RecordUnavailable(Exception):
    """The server says this record does not exist (HTTP 404/410).

    Raised instead of the retry/captcha path when the caller declared
    ``missing_record_on_404=True``: the one-shot detail pages (a Cite dialog, a
    publication detail page) can genuinely have no such record.  Retrying,
    cooling down, or asking the operator to solve a captcha cannot help — and
    used to park a whole run for 20-35 minutes per dead entry, because a 404 is
    indistinguishable from a rate limit once scholarly has swallowed the status
    code.

    The caller decides what to do with it (``code/cite_format.py`` remembers it
    with a timestamp and moves on).  It is deliberately *not* a permanent
    verdict: handles are Scholar's, and how it (re)builds clusters is not fully
    understood yet.
    """

    def __init__(self, url, status=None, message=None):
        self.url = url
        self.status = status
        super().__init__(message or f'no such record (HTTP {status}): {url}')


def _status_of(exc, ctx):
    """Return the HTTP status behind *exc* if any layer recorded one."""
    status = getattr(exc, _STATUS_ATTR, None)
    if status is None:
        status = getattr(ctx, 'last_response_status', None)
    try:
        return int(status)
    except (TypeError, ValueError):
        return None


def _status_note(status):
    return f" (HTTP {status})" if status is not None else ""


# ---------------------------------------------------------------------------
# PageVisit
# ---------------------------------------------------------------------------

class PageVisit:
    """Wraps a single Scholar page fetch with layered error recovery.

    Usage:
        visit = PageVisit(ctx)
        soup = visit.fetch(lambda: nav._get_soup(url), url,
                           label="probe histogram")
    """

    def __init__(self, ctx):
        """
        *ctx* is a SessionContext whose attributes provide:
          - interactive_captcha  : bool
          - try_interactive_captcha_fn : callable(url) -> bool
          - wait_proxy_switch_fn : callable() -> bool (False = gave up)
          - wait_status_fn       : callable() -> str
          - delay_scale          : float
        """
        self.ctx = ctx

    # -- main entry point --------------------------------------------------

    def fetch(self, fn, url, label="page", missing_record_on_404=False):
        """Call *fn()* and return its result.

        Recovery order: interactive captcha prompt (once per cycle), two quick
        retries, then 5-10 min rate-limit cooldowns.  After *max_cooldowns*
        cooldowns the operator is offered a proxy switch (the documented
        non-interactive behaviour); if that does not help, the original
        exception is re-raised so the caller's retry budget can take over
        instead of looping here forever.

        *missing_record_on_404* declares that a 404/410 on this URL means "this
        record does not exist" rather than "we are blocked" (one-shot detail
        pages: the Cite dialog, the publication detail page).  Then the first
        such answer raises `RecordUnavailable` immediately — no captcha prompt,
        no cooldowns — and the caller decides how to remember it.

        *url* is used for captcha-recovery and proxy-switch messages.
        *label* appears in log messages (e.g. "probe", "page 3").
        """
        captcha_attempted = False
        retries = 0
        max_retries = 2
        cooldowns = 0
        max_cooldowns = 3

        while True:
            try:
                return fn()
            except KeyboardInterrupt:
                raise
            except Exception as e:
                status = _status_of(e, self.ctx)
                if missing_record_on_404 and status in MISSING_RECORD_STATUSES:
                    print(f"        {now_str()} {label}: HTTP {status} — no such "
                          f"record (not a block); giving up on it", flush=True)
                    raise RecordUnavailable(url, status) from e

                # -- captcha recovery (interactive mode) -----------------
                if (self.ctx.interactive_captcha
                        and self.ctx.try_interactive_captcha_fn
                        and not captcha_attempted):
                    captcha_attempted = True
                    print(f"        {now_str()} Blocked fetching {label}: {e}"
                          f"{_status_note(status)}", flush=True)
                    solved = self.ctx.try_interactive_captcha_fn(url)
                    if solved:
                        retries = 0
                        captcha_attempted = False
                        cooldowns = 0
                        continue

                # -- automatic retry (transient errors) -------------------
                if retries < max_retries:
                    retries += 1
                    d = rand_delay(self.ctx.delay_scale)
                    wait_str = self.ctx.wait_status_fn() if self.ctx.wait_status_fn else ""
                    print(f"        {now_str()} Error fetching {label} "
                          f"(retry {retries}/{max_retries}): {e}"
                          f"{_status_note(status)}", flush=True)
                    print(f"        {now_str()} Waiting {d:.0f}s... [{wait_str}]", flush=True)
                    time.sleep(d)
                    continue

                # -- rate-limit cooldown ----------------------------------
                # All retries exhausted — likely a 429 rate limit, not a
                # captcha.  Wait 5-10 min before retrying instead of
                # immediately asking for a proxy switch.
                cooldowns += 1
                if cooldowns > max_cooldowns:
                    print(f"        {now_str()} {label} still failing after "
                          f"{max_cooldowns} cooldowns: {e}"
                          f"{_status_note(status)}", flush=True)
                    if self.ctx.wait_proxy_switch_fn and self.ctx.wait_proxy_switch_fn():
                        retries = 0
                        captcha_attempted = False
                        cooldowns = 0
                        continue
                    raise
                d = random.uniform(300, 600) * self.ctx.delay_scale
                wait_str = self.ctx.wait_status_fn() if self.ctx.wait_status_fn else ""
                print(f"        {now_str()} Rate-limit cooldown after "
                      f"{label} failures: waiting {d/60:.0f} min... [{wait_str}]",
                      flush=True)
                time.sleep(d)
                retries = 0
                captcha_attempted = False
                continue
