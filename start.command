#!/usr/bin/env bash
# macOS double-click wrapper for start.sh.
cd "$(dirname "$0")" || exit 1
./start.sh
status=$?
echo
read -r -p "Press Enter to close this window..." _
exit $status
