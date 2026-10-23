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

from code.common import rand_delay
from code.log_utils import run_log


#: HTTP statuses that mean "this record does not exist", as opposed to
#: "you are blocked / rate-limited".  404 is what Scholar answers for a Cite
#: dialog whose cluster handle it does not serve (Google's own 404 page, while
#: sibling handles can answer 200 with five formats each in the same minute);
#: 410 is the same class of answer.
MISSING_RECORD_STATUSES = (404, 410)

#: Status of the response that produced the failure, when the caller could not
#: tell otherwise.  Attribution order: the exception itself (our patch layer
#: attaches the recorded status), then the session context.
_STATUS_ATTR = 'http_status'


class RecordUnavailable(Exception):
    """The server will not serve this record (HTTP 404/410, or a refusal that
    survives freshly injected cookies).

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
        injections = 0

        while True:
            try:
                return fn()
            except KeyboardInterrupt:
                raise
            except Exception as e:
                status = _status_of(e, self.ctx)
                if missing_record_on_404 and status in MISSING_RECORD_STATUSES:
                    # 不在这里打 WARN：调用方（`cite_format` 的逐行结论、`venue_detail`
                    # 的逐篇结论）知道是哪一行/哪一篇，它的那一行才是这条记录的唯一出处。
                    # 这里再打一遍只会把同一个 URL 和同一个 404 说两次。
                    raise RecordUnavailable(url, status) from e

                # A one-shot detail page that still refuses *after* fresh cookies
                # were injected is no longer "the session is blocked": a Cite
                # dialog can answer 429, then 403 twice with a fresh browser cURL,
                # while its siblings are served seconds earlier.  Asking for
                # another paste only loops the operator.  Give up on **this
                # record** and let the caller remember it — if the session itself
                # is blocked, the next request prompts as usual.
                if missing_record_on_404 and injections and status is not None:
                    run_log.warn(f"{label}: still refusing after fresh cookies "
                                 f"(HTTP {status}) — treating this record as "
                                 f"unavailable, not the session; the next request "
                                 f"will ask again if the session really is blocked",
                                 module="http", indent=1)
                    raise RecordUnavailable(url, status) from e

                # -- captcha recovery (interactive mode) -----------------
                if (self.ctx.interactive_captcha
                        and self.ctx.try_interactive_captcha_fn
                        and not captcha_attempted):
                    captcha_attempted = True
                    run_log.warn(f"Blocked fetching {label}: {e}"
                                 f"{_status_note(status)}",
                                 module="http", indent=1)
                    solved = self.ctx.try_interactive_captcha_fn(url)
                    if solved:
                        retries = 0
                        captcha_attempted = False
                        cooldowns = 0
                        injections += 1
                        continue

                # -- automatic retry (transient errors) -------------------
                if retries < max_retries:
                    retries += 1
                    d = rand_delay(self.ctx.delay_scale)
                    wait_str = self.ctx.wait_status_fn() if self.ctx.wait_status_fn else ""
                    run_log.debug(f"Error fetching {label} "
                                  f"(retry {retries}/{max_retries}): {e}"
                                  f"{_status_note(status)}",
                                  module="http", indent=1)
                    # INFO for the same reason as the pre-request wait in
                    # scholarly_session: it is the run's heartbeat while the
                    # crawler sits between attempts.  The *cause* stays DEBUG.
                    detail = f" · {wait_str}" if wait_str else ""
                    run_log.info(f"waiting {d:.0f}s (retry {retries}/{max_retries})"
                                 f"{detail}", module="http", indent=1)
                    time.sleep(d)
                    continue

                # -- rate-limit cooldown ----------------------------------
                # All retries exhausted — likely a 429 rate limit, not a
                # captcha.  Wait 5-10 min before retrying instead of
                # immediately asking for a proxy switch.
                cooldowns += 1
                if cooldowns > max_cooldowns:
                    run_log.warn(f"{label} still failing after "
                                 f"{max_cooldowns} cooldowns: {e}"
                                 f"{_status_note(status)}",
                                 module="http", indent=1)
                    if self.ctx.wait_proxy_switch_fn and self.ctx.wait_proxy_switch_fn():
                        retries = 0
                        captcha_attempted = False
                        cooldowns = 0
                        continue
                    raise
                d = random.uniform(300, 600) * self.ctx.delay_scale
                wait_str = self.ctx.wait_status_fn() if self.ctx.wait_status_fn else ""
                run_log.warn(f"Rate-limit cooldown after {label} failures: "
                             f"waiting {d/60:.0f} min... [{wait_str}]",
                             module="http", indent=1)
                time.sleep(d)
                retries = 0
                captcha_attempted = False
                continue
