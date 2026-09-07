#!/bin/sh
# Desktop entry for the existing verified Panel boot switch; never bypass confirmation.
set -eu
[ "$#" -eq 0 ] || exit 2
if /usr/bin/kdialog --title '切换到 Android' --icon smartphone \
    --yes-label '重启到 Android' --no-label '取消' \
    --yesno '即将离开 Linux 并重启到原厂 Android。\n请先保存正在进行的工作。系统和游戏数据不会被删除。'; then
    if ! /usr/local/bin/pocketds-panelctl boot-android CONFIRM; then
        /usr/bin/kdialog --title '未能切换到 Android' --error \
            '切换请求失败，请先不要强制关机。可以继续使用 Linux，并查看切换日志。'
        exit 1
    fi
fi
