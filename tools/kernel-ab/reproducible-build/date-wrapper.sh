#!/bin/sh
if [ "$#" -eq 1 ] && [ "$1" = '+%Y-%m' ]; then
    printf '%s\n' '2026-07'
    exit 0
fi
if [ "$#" -eq 0 ]; then
    printf '%s\n' 'Thu Jul 30 17:34:11 UTC 2026'
    exit 0
fi
exec /usr/bin/date "$@"
