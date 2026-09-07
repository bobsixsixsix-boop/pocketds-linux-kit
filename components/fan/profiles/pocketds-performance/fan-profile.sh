#!/bin/sh
set -eu

case "${1:-}" in
    start)
        /usr/libexec/pocketds-fancontrol-set-profile aggressive
        ;;
    stop)
        /usr/libexec/pocketds-fancontrol-set-profile moderate
        ;;
esac
