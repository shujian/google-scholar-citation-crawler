"""
Reference: logging pattern from citation_analysis_content/code/utils.py

Purpose: Shows how to set up structured logging with level control.
Replace TeeStream-based logging with this pattern.
"""

import logging

logger = logging.getLogger("citation_analysis")


def setup_logging(verbose: bool = False) -> None:
    """Configure logging with appropriate level."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


# Usage in other files:
#   from .utils import logger, setup_logging
#   logger.info("Starting citation fetch...")
#   logger.debug("Year histogram: %s", histogram)
#   logger.warning("Rate limit hit, waiting %ds", wait_seconds)
#   logger.error("Failed to fetch paper: %s", paper_title)
