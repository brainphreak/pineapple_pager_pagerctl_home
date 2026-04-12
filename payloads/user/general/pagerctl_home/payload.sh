#!/bin/sh
# Title: Pagerctl Home
# Description: Custom home screen with theme engine
# Author: brAinphreAk
# Version: 1.0
# Category: General
# Library: libpagerctl.so (pagerctl)

_PAYLOAD_TITLE="Pagerctl Home"
_PAYLOAD_AUTHOR_NAME="brAinphreAk"
_PAYLOAD_VERSION="1.0"
_PAYLOAD_DESCRIPTION="Custom home screen with theme engine"

PAYLOAD_DIR="/root/payloads/user/general/pagerctl_home"

cd "$PAYLOAD_DIR" || exit 1

export PATH="/mmc/usr/bin:$PAYLOAD_DIR/bin:$PATH"
export PYTHONPATH="$PAYLOAD_DIR/lib:$PAYLOAD_DIR:$PYTHONPATH"
export LD_LIBRARY_PATH="/mmc/usr/lib:$PAYLOAD_DIR/lib:$LD_LIBRARY_PATH"

if ! command -v python3 >/dev/null 2>&1; then
    LOG "red" "Python3 not found"
    exit 1
fi

/etc/init.d/pineapplepager stop 2>/dev/null
sleep 0.3

python3 pagerctl_home.py

exit 0
