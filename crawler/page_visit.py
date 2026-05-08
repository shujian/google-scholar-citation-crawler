"""
crawler/page_visit.py — PageVisit: single Scholar page fetch with error recovery.

Every page-level HTTP request goes through PageVisit.fetch().  It handles:
  - captcha detection & interactive cookie injection
  - rate-limit waits with randomised delays
  - proxy-switch prompts (non-interactive mode)
  - transient network errors

KeyboardInterrupt always propagates immediately so the caller can save
state before exiting.
"""

import time
from datetime import datetime

from crawler.common import rand_delay, now_str


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
          - wait_proxy_switch_fn : callable(max_hours) -> None (raises on timeout)
          - wait_status_fn       : callable() -> str
          - delay_scale          : float
        """
        self.ctx = ctx

    # -- main entry point --------------------------------------------------

    def fetch(self, fn, url, label="page"):
        """Call *fn()* and return its result.

        On failure the method loops through recovery steps until either
        *fn* succeeds or all options are exhausted, at which point the
        original exception is re-raised.

        *url* is used for captcha-recovery and proxy-switch messages.
        *label* appears in log messages (e.g. "probe", "page 3").
        """
        captcha_attempted = False
        retries = 0
        max_retries = 2

        while True:
            try:
                return fn()
            except KeyboardInterrupt:
                raise
            except Exception as e:
                # -- captcha recovery (interactive mode) -----------------
                if (self.ctx.interactive_captcha
                        and self.ctx.try_interactive_captcha_fn
                        and not captcha_attempted):
                    captcha_attempted = True
                    d = rand_delay(self.ctx.delay_scale)
                    wait_str = self.ctx.wait_status_fn() if self.ctx.wait_status_fn else ""
                    print(f"        {now_str()} Blocked fetching {label}: {e}", flush=True)
                    print(f"        {now_str()} Waiting {d:.0f}s before captcha prompt... [{wait_str}]",
                          flush=True)
                    time.sleep(d)
                    solved = self.ctx.try_interactive_captcha_fn(url)
                    if solved:
                        retries = 0
                        captcha_attempted = False
                        continue

                # -- automatic retry (transient errors) -------------------
                if retries < max_retries:
                    retries += 1
                    d = rand_delay(self.ctx.delay_scale)
                    wait_str = self.ctx.wait_status_fn() if self.ctx.wait_status_fn else ""
                    print(f"        {now_str()} Error fetching {label} "
                          f"(retry {retries}/{max_retries}): {e}", flush=True)
                    print(f"        {now_str()} Waiting {d:.0f}s... [{wait_str}]", flush=True)
                    time.sleep(d)
                    continue

                # -- proxy switch (non-interactive mode) ------------------
                if self.ctx.wait_proxy_switch_fn:
                    print(f"        {now_str()} All retries exhausted for {label}: {e}",
                          flush=True)
                    self.ctx.wait_proxy_switch_fn(max_hours=24)
                    retries = 0
                    captcha_attempted = False
                    continue

                raise
