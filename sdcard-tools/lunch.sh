#!/bin/sh
# Put this file on the MK10's SD card as /lunch.sh (the card's top level).
#
# /etc/init.d/qt_app1 on the device copies it to /data/lunch.sh and runs it AS ROOT at boot, before
# KeyboardDevice starts. That hook is Waveshare's, not ours. Take the card out and it stops happening.
#
# This script only reads. It writes nothing outside /mnt/SDCARD/mk10dump/, deletes nothing, and starts
# no services. Keep it that way unless you mean otherwise: it runs before the UI and the boot waits for
# it, so anything that loops or blocks here stops the device from starting.
#
# v2. The first version left every file after the first one empty: the writes were still in cache when
# the card went away. So now everything goes into ONE file, each section is flushed as it is written,
# and the slow copying only happens if you ask for it.

OUT=/mnt/SDCARD/mk10dump
R="$OUT/report.txt"
mkdir -p "$OUT"

say() { echo "" >> "$R"; echo "===== $* =====" >> "$R"; }
run() { say "$1"; shift; "$@" >> "$R" 2>&1; sync; }

: > "$R"
run "when"            date
run "kernel"          uname -a
run "cmdline"         cat /proc/cmdline
run "model"           cat /sys/firmware/devicetree/base/model
run "mounts"          mount
run "disk"            df -h
run "processes"       ps
run "block devices"   ls -la /dev/mmcblk0p1 /dev/mmcblk0p5 /dev/mmcblk0p6 /dev/mmcblk1 /dev/mmcblk1p1
run "/mnt"            ls -la /mnt
run "/data"           ls -la /data
run "/data/theme"     find /data/theme
run "/data sizes"     du -sh /data/theme /data/image /data/video /data/fonts /data/info /data/audio
run "/data, whole"    find /data
run "/overlay"        find /overlay -maxdepth 4
run "KeyboardInfo"    cat /data/KeyboardInfo
run "file_info.json"  cat /data/file_info.json
run "appLunch.sh"     cat /data/appLunch.sh
run "qt_app1"         cat /etc/init.d/qt_app1
run "previous log"    tail -c 4000 /mnt/SDCARD/log.txt

say "report complete"
sync

# --- the slow part, only if you ask for it -------------------------------------------------------
# Create an empty file called "copy-everything" next to lunch.sh on the card to turn this on. It is
# off by default because copying a provisioned /data can take long enough that pulling the card or
# cutting the power loses the lot - which is exactly what happened the first time.
[ -f /mnt/SDCARD/copy-everything ] || exit 0

say "copying /data/theme"
cp -r /data/theme "$OUT/theme" >> "$R" 2>&1
sync
say "copying the rest"
cp -r /data/info  "$OUT/info"  >> "$R" 2>&1
cp -r /data/fonts "$OUT/fonts" >> "$R" 2>&1
sync
say "copying KeyboardDevice (2.3 MB)"
cp /data/KeyboardDevice "$OUT/KeyboardDevice" >> "$R" 2>&1
sync
say "copies complete"
sync
