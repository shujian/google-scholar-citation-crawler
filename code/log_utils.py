"""Structured logging for the citation crawler.

Provides ``setup_logging()`` to configure the root logger with console and
optional file output.  Replaces the ad-hoc ``TeeStream`` pattern for new code
while remaining backward-compatible with existing ``print()``-based output.

Usage::

    from code.log_utils import setup_logging, get_logger

    logger = get_logger(__name__)
    logger.info("Starting crawl for author %s", author_id)
    logger.debug("Year histogram: %s", histogram)
    logger.warning("Rate-limit hit; waiting %d s", wait_seconds)
    logger.error("Failed to fetch page %d: %s", page_num, err)
"""

import logging
import os
import sys
from datetime import datetime
from typing import Optional

LOG_FORMAT_CONSOLE = "%(asctime)s [%(levelname)s] %(message)s"
LOG_FORMAT_FILE = "%(asctime)s [%(levelname)s] %(message)s"
LOG_DATEFMT_CONSOLE = "%H:%M:%S"
LOG_DATEFMT_FILE = "%Y-%m-%d %H:%M:%S"

#: Third-party loggers that must not speak for themselves on the console.
#: `httpx` logs every request at INFO (and every redirect hop with it, full URL
#: included — seen 2026-10-19: `HTTP Request: GET https://www.google.com/sorry/
#: index?…&q=<token> "HTTP/2 429 Too Many Requests"`) and `scholarly` logs
#: `Getting <url>`; both bypass `run_log`, and because the root handler writes
#: **stderr** (stdout is the TeeStream that mirrors into the run log) such lines
#: never reached the archived log at all.  At WARNING they only surface when
#: something is genuinely wrong; `-v` gives their own DEBUG detail back.
_THIRD_PARTY_LOGGERS = ("httpx", "httpcore", "scholarly")


def setup_logging(
    verbose: bool = False,
    log_file: Optional[str] = None,
) -> None:
    """Configure the root logger with console and optional file handlers.

    Args:
        verbose: If ``True``, set level to ``DEBUG``; otherwise ``INFO``.
        log_file: Optional path for a rotating-free file handler.  When given,
            every log record is also appended to this file in a format that
            includes the full date (useful for multi-day crawls).
    """
    level = logging.DEBUG if verbose else logging.INFO

    # ``--verbose`` also decides whether the run-log facade prints its per-item
    # detail (per request, per page, per year) — see ``RunLog``.
    run_log.set_threshold(DEBUG if verbose else INFO)

    # Their own record must not duplicate ours (run_log already prints one line
    # per request/page at DEBUG), and their URLs carry tokens we do not want
    # echoing.  Explicit level, so it also works when this is called twice.
    for name in _THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.DEBUG if verbose else logging.WARNING)

    root = logging.getLogger()
    root.setLevel(level)

    # Remove any handlers left over from previous calls (idempotent).
    root.handlers.clear()

    # ---- console handler (stderr) ----
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level)
    console.setFormatter(logging.Formatter(LOG_FORMAT_CONSOLE, datefmt=LOG_DATEFMT_CONSOLE))
    root.addHandler(console)

    # ---- file handler (optional) ----
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)          # always write DEBUG+ to file
        fh.setFormatter(logging.Formatter(LOG_FORMAT_FILE, datefmt=LOG_DATEFMT_FILE))
        root.addHandler(fh)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a logger for the given *name* (default: ``"citation_crawler"``)."""
    return logging.getLogger(name or "citation_crawler")


# ===========================================================================
# Run-log facade — one shape for every line of a run (2026-10-16)
# ===========================================================================
#
# The run log *is* the stdout stream (``TeeStream`` mirrors it into
# ``output/logs/``), and most of the code still writes with plain ``print()``.
# That produced a flat log: no levels, no module tags, hand-written 2/4/6/8
# space indents, timestamps on only some lines, and the same fact printed by two
# layers in a row (``URL:`` followed by ``Request URL:``, 26 times in one run).
#
# This facade is the single place that gives a line the run's shape::
#
#     11:40:13 INFO  fetch     Year 1996 status: year_total=0, year_new=0, …
#     └─ time  └─ level └─ module  └─ two spaces per nesting level
#
# Levels keep the default log readable: INFO is "state changed / conclusion",
# DEBUG (``-v``) is the per-request, per-page and per-year detail, TRACE (not
# used yet) would be parse-level detail.  ``setup_logging(verbose=…)`` sets the
# threshold, so ``--verbose`` now actually changes what the log contains —
# before this, the ``logging`` module it configured was never called by the
# crawl itself.

TRACE, DEBUG, INFO, WARN, ERROR = 5, 10, 20, 30, 40

_LEVEL_NAMES = {TRACE: "TRACE", DEBUG: "DEBUG", INFO: "INFO ", WARN: "WARN ", ERROR: "ERROR"}
_MODULE_WIDTH = 9
_SECTION_WIDTH = 76


class RunLog:
    """Writes every run-log line in one shape, gated by a level threshold."""

    def __init__(self, threshold: int = INFO):
        self.threshold = threshold

    def set_threshold(self, level: int) -> None:
        self.threshold = level

    def enabled(self, level: int) -> bool:
        return level >= self.threshold

    def write(self, level: int, message: str, module: str = "run", indent: int = 0) -> bool:
        """Emit one line; returns False when the level is filtered out."""
        if not self.enabled(level):
            return False
        stamp = datetime.now().strftime("%H:%M:%S")
        print(f"{stamp} {_LEVEL_NAMES.get(level, 'INFO ')} {module:<{_MODULE_WIDTH}} "
              f"{'  ' * max(0, indent)}{message}", flush=True)
        return True

    def section(self, title: str, module: str = "run") -> bool:
        """One line where the run used to print a 70-char ``=`` banner.

        A run printed 21 of those banners, six of them back to back; the phase
        boundary is the only thing they carried, so it becomes one ruled line.
        """
        rule = "─" * max(4, _SECTION_WIDTH - len(title) - 3)
        return self.write(INFO, f"── {title} {rule}", module=module)

    def info(self, message: str, module: str = "run", indent: int = 0) -> bool:
        return self.write(INFO, message, module=module, indent=indent)

    def warn(self, message: str, module: str = "run", indent: int = 0) -> bool:
        return self.write(WARN, message, module=module, indent=indent)

    def error(self, message: str, module: str = "run", indent: int = 0) -> bool:
        return self.write(ERROR, message, module=module, indent=indent)

    def debug(self, message: str, module: str = "run", indent: int = 0) -> bool:
        return self.write(DEBUG, message, module=module, indent=indent)

    def trace(self, message: str, module: str = "run", indent: int = 0) -> bool:
        return self.write(TRACE, message, module=module, indent=indent)


#: The run's log facade: ``from code.log_utils import run_log``.
run_log = RunLog()


def line_logger(level: int = INFO, module: str = "run", indent: int = 1):
    """Return a ``print``-shaped callable that writes through the run log.

    Older modules take a ``print_fn`` seam so their callers (and tests) can
    capture the lines they emit.  This keeps that seam while giving each line the
    run's shape: ``print_fn='…'`` becomes one run-log line at *level*, and a
    multi-line message becomes one line per message line.
    """
    def write_line(message="", **_ignored):
        for part in str(message).split("\n"):
            run_log.write(level, part.lstrip(), module=module, indent=indent)
    return write_line


def prune_run_logs(logs_dir: str, keep: int = None, archive_name: str = None,
                   keep_path: Optional[str] = None) -> tuple:
    """Keep the newest *keep* run logs, move the rest into an archive subdir.

    Never deletes: an old run log is the only record of what a crawl did, so it
    is moved to ``<logs_dir>/<archive_name>/``.  ``keep_path`` (the log of the
    run that is starting) always counts as kept.  Returns ``(kept, moved)``.
    """
    from code import config as _config

    keep = _config.LOG_KEEP_RUNS if keep is None else keep
    archive_name = _config.LOG_ARCHIVE_DIRNAME if archive_name is None else archive_name
    if keep < 0 or not os.path.isdir(logs_dir):
        return (0, 0)
    entries = []
    for name in os.listdir(logs_dir):
        path = os.path.join(logs_dir, name)
        if not name.endswith(".log") or not os.path.isfile(path):
            continue
        entries.append((os.path.getmtime(path), path))
    entries.sort(reverse=True)
    protected = os.path.abspath(keep_path) if keep_path else None
    stale = [path for _, path in entries[keep:]
             if protected is None or os.path.abspath(path) != protected]
    if not stale:
        return (min(len(entries), keep), 0)
    archive_dir = os.path.join(logs_dir, archive_name)
    os.makedirs(archive_dir, exist_ok=True)
    moved = 0
    for path in stale:
        target = os.path.join(archive_dir, os.path.basename(path))
        if os.path.exists(target):
            continue
        os.replace(path, target)
        moved += 1
    return (len(entries) - moved, moved)
