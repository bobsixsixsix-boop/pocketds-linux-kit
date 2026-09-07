#!/bin/sh
# Sourced by Plasma's login environment loader. It imports keys still present
# in the child shell, so unset would leave inherited parent values untouched.
if [ "${XDG_SESSION_TYPE:-}" = wayland ]; then
    export GTK_IM_MODULE=
    export QT_IM_MODULE=
    export XMODIFIERS=@im=fcitx
fi
