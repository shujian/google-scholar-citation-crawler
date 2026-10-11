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
import sys
from typing import Optional

LOG_FORMAT_CONSOLE = "%(asctime)s [%(levelname)s] %(message)s"
LOG_FORMAT_FILE = "%(asctime)s [%(levelname)s] %(message)s"
LOG_DATEFMT_CONSOLE = "%H:%M:%S"
LOG_DATEFMT_FILE = "%Y-%m-%d %H:%M:%S"


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
