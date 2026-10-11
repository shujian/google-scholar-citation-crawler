#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if command -v conda &>/dev/null; then
    exec conda run -n scholar python -m code.scholar_citation "$@"
else
    exec python -m code.scholar_citation "$@"
fi
