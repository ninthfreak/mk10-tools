#!/bin/sh
# Put this file on the MK10's SD card as /lunch.sh (the card's top level).
#
# /etc/init.d/qt_app1 on the device copies it to /data/lunch.sh and runs it AS ROOT at boot, before
# KeyboardDevice starts. That hook is Waveshare's, not ours. Take the card out and it stops happening.
#
# This script only reads. It writes nothing outside /mnt/SDCARD/mk10dump/, deletes nothing, and starts
# no services. Keep it that way unless you mean otherwise: it runs before the UI and the boot waits for
# it, so anything that loops or blocks here stops the device from starting.

OUT=/mnt/SDCARD/mk10dump
mkdir -p "$OUT"

# Proof it ran at all, which is the first thing we want to know.
{
    date
    uname -a
    cat /proc/cmdline
    echo "--- model ---"
    cat /sys/firmware/devicetree/base/model
} > "$OUT/when.txt" 2>&1

# The theme. This is the whole point: /data/theme/<width>x<height>/ in the factory image, so 896x304
# for this device, but copy the lot in case this firmware names it differently.
cp -r /data/theme  "$OUT/theme"  2>/dev/null
cp -r /data/info   "$OUT/info"   2>/dev/null
cp -r /data/image  "$OUT/image"  2>/dev/null
cp -r /data/fonts  "$OUT/fonts"  2>/dev/null

# Where things are, so the next script doesn't have to guess.
ls -laR /data            > "$OUT/data-tree.txt"    2>&1
ls -laR /overlay         > "$OUT/overlay-tree.txt" 2>&1
ls -la  /mnt /mnt/*      > "$OUT/mnt-tree.txt"     2>&1
mount                    > "$OUT/mounts.txt"       2>&1
ps                       > "$OUT/ps.txt"           2>&1
df -h                    > "$OUT/df.txt"           2>&1

# The files the app keeps its own state in.
cp /data/KeyboardInfo    "$OUT/KeyboardInfo"    2>/dev/null
cp /data/file_info.json  "$OUT/file_info.json"  2>/dev/null
cp /data/appLunch.sh     "$OUT/appLunch.sh"     2>/dev/null
cp /etc/init.d/qt_app1   "$OUT/qt_app1"         2>/dev/null
cp /etc/init.d/qt_app2   "$OUT/qt_app2"         2>/dev/null

# Anything the app wrote to the card already (appLunch.sh logs dmesg here on every boot).
cp /mnt/SDCARD/log.txt   "$OUT/previous-log.txt" 2>/dev/null

# The running firmware, which is V2.32 on this device and newer than any copy we have. 2.3 MB.
# Delete this line if you'd rather not spend the space.
cp /data/KeyboardDevice  "$OUT/KeyboardDevice"  2>/dev/null

sync
