#!/bin/sh
# Builds mk10hid for the MK10 (32-bit ARM, hard float).
#
# Static, because the MK10 runs glibc 2.23 and any current toolchain links against a newer one: a dynamic
# build would refuse to start there. On Debian or Ubuntu (WSL works):
#   sudo apt-get install gcc-arm-linux-gnueabihf libc6-dev-armhf-cross
#   sh build.sh
# The result is committed, so you only need this after changing mk10hid.c. It contains GNU libc
# (LGPL-2.1+, Ubuntu's glibc 2.39 for armhf); mk10hid.c here is everything needed to relink it.
set -e
cd "$(dirname "$0")"
arm-linux-gnueabihf-gcc -Os -Wall -Wextra -static -s -o mk10hid mk10hid.c
ls -l mk10hid
