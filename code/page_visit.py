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

    def fetch(self, fn, url, label="page"):
        """Call *fn()* and return its result.

        Recovery order: interactive captcha prompt (once per cycle), two quick
        retries, then 5-10 min rate-limit cooldowns.  After *max_cooldowns*
        cooldowns the operator is offered a proxy switch (the documented
        non-interactive behaviour); if that does not help, the original
        exception is re-raised so the caller's retry budget can take over
        instead of looping here forever.

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
                # -- captcha recovery (interactive mode) -----------------
                if (self.ctx.interactive_captcha
                        and self.ctx.try_interactive_captcha_fn
                        and not captcha_attempted):
                    captcha_attempted = True
                    print(f"        {now_str()} Blocked fetching {label}: {e}", flush=True)
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
                          f"(retry {retries}/{max_retries}): {e}", flush=True)
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
                          f"{max_cooldowns} cooldowns: {e}", flush=True)
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
