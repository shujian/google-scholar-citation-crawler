#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if command -v conda &>/dev/null; then
    exec conda run -n scholar python scholar_citation.py "$@"
else
    exec python scholar_citation.py "$@"
fi
