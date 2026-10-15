#!/bin/sh
""":"
if ! command -v python3 >/dev/null 2>&1; then
    echo 'ccprofile: Python 3.11 or later is required (brew install python).' >&2
    exit 1
fi
exec python3 "$0" "$@"
": """

