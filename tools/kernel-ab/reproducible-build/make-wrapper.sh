#!/bin/sh
set -eu

if [ -d ./initramfs ] && [ -f ./initramfs/usr/lib/firmware/qcom/a740_sqe.fw ]; then
    /usr/bin/chown -hR 1000:135 ./initramfs
    /usr/bin/find ./initramfs -type f -exec /usr/bin/touch -d @1767225600 -- {} +
    /usr/bin/find ./initramfs -type d -exec /usr/bin/touch -d @1785432757 -- {} +
    /usr/bin/find ./initramfs -type l -exec /usr/bin/touch -h -d @1785432757 -- {} +
fi

/usr/bin/env \
    FAKETIME='@2026-07-31 02:32:37' \
    FAKETIME_DONT_FAKE_MONOTONIC=1 \
    FAKETIME_ONLY_CMDS=gen_init_cpio \
    NO_FAKE_STAT=1 \
    LD_PRELOAD=/usr/lib64/libfaketime.so.1 \
    /usr/bin/make "$@"

if [ -f ./arch/arm64/boot/Image ]; then
    /usr/bin/touch -d @1785432861 -- ./arch/arm64/boot/Image
fi
