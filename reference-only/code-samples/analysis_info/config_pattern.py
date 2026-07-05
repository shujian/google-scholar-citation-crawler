"""
Reference: centralized config pattern from citation_analysis_info/code/config.py

Purpose: Shows how to centralize all configuration constants in one place.
All thresholds, paths, and tunable parameters are discoverable in one file.
"""

import os

# ---- Directory paths ----
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(ROOT_DIR, "output")
LOGS_DIR = os.path.join(OUTPUT_DIR, "logs")

# ---- Rate limiting ----
DELAY_MIN = 45       # seconds
DELAY_MAX = 90
BREAK_EVERY_MIN = 8  # pages between long breaks
BREAK_EVERY_MAX = 12
BREAK_MIN = 180      # 3 minutes
BREAK_MAX = 360      # 6 minutes

# ---- Thresholds ----
YEAR_BASED_THRESHOLD = 50  # citations → switch to year-by-year fetch
MAX_RETRIES = 3

# ---- API endpoints ----
SCHOLAR_BASE_URL = "https://scholar.google.com"

# ---- Output format ----
OUTPUT_ENCODING = "utf-8"

# Usage:
#   from crawler.config import DELAY_MIN, DELAY_MAX, YEAR_BASED_THRESHOLD
