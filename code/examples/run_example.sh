#!/bin/bash
# Example: fetch author profile and citations
# Replace YOUR_AUTHOR_ID with an actual Google Scholar author ID
# (found in the URL: https://scholar.google.com/citations?user=YOUR_AUTHOR_ID)
#
# Run from the project root (this script cd's there itself).
set -euo pipefail
cd "$(dirname "$0")/../.."

python -m code.scholar_citation \
    --author YOUR_AUTHOR_ID \
    --output-dir ./output
