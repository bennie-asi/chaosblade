#!/bin/bash
# Temporary helper for the S-phase refactor regression runs.
cd /Users/jiangzelin/program/Python/fault-drill-agent || exit 2
if [ "$#" -eq 0 ]; then set -- tests/; fi
.venv/bin/python -m pytest "$@" -p no:cacheprovider -q --no-header
echo "EXIT=$?"
