#!/bin/sh
set -eu

case "${1:-}" in
    start|stop)
        /usr/libexec/pocketds-fancontrol-set-profile moderate
        ;;
esac
