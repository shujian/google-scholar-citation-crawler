#!/bin/bash
# Example: fetch author profile and citations
# Replace YOUR_AUTHOR_ID with an actual Google Scholar author ID
# (found in the URL: https://scholar.google.com/citations?user=YOUR_AUTHOR_ID)

python scholar_citation.py \
    --author YOUR_AUTHOR_ID \
    --output-dir ./output
