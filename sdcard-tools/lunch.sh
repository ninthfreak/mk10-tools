#!/bin/sh
# Put this file on the MK10's SD card as /lunch.sh (the card's top level).
#
# /etc/init.d/qt_app1 on the device copies it to /data/lunch.sh and runs it AS ROOT at boot, before
# KeyboardDevice starts. That hook is Waveshare's, not ours. Take the card out and it stops happening.
#
# This script only reads. It writes nothing outside /mnt/SDCARD/mk10dump/ and /data/mk10dump-report.txt,
# deletes nothing, and starts no services. Keep it that way unless you mean otherwise: it runs before the
# UI and the boot waits for it, so anything that loops or blocks here stops the device from starting.
#
# v3. The first run left every file after the first one empty, which is what a full filesystem looks
# like: the redirection makes the file, the write fails for want of space, and the error message has
# nowhere to go either. So this version builds the report in RAM, keeps a copy on the device itself,
# and only touches the card once, at the end, with one small file.

TMP=/tmp/mk10report.txt
KEEP=/data/mk10dump-report.txt
OUT=/mnt/SDCARD/mk10dump

say() { echo ""; echo "===== $* ====="; }

{
    say "when";             date
    say "kernel";           uname -a
    say "cmdline";          cat /proc/cmdline
    say "model";            cat /sys/firmware/devicetree/base/model

    # The card first, because running out of room on it is what broke the last run.
    say "free space";       df -h
    say "free space (raw)"; df
    say "partitions";       cat /proc/partitions
    say "mmc devices";      ls -la /dev/mmcblk*
    say "mounts";           mount
    say "card contents";    ls -la /mnt/SDCARD
    say "card usage";       du -sh /mnt/SDCARD/* 2>&1

    say "processes";        ps
    say "/mnt";             ls -la /mnt
    say "/data";            ls -la /data
    say "/data sizes";      du -sh /data/* 2>&1

    # The question the whole exercise is for.
    say "/data/theme";      find /data/theme
    say "/data/info";       find /data/info
    say "/data/fonts";      find /data/fonts

    say "/data, whole";     find /data -maxdepth 3
    say "/overlay";         find /overlay -maxdepth 4
    say "KeyboardInfo";     cat /data/KeyboardInfo
    say "file_info.json";   cat /data/file_info.json
    say "appLunch.sh";      cat /data/appLunch.sh
    say "qt_app1";          cat /etc/init.d/qt_app1
    say "report complete";  date
} > "$TMP" 2>&1

# Keep a copy on the device's own storage, which has room, so a full card can't lose it. Next run
# picks it up even if this one can't write to the card at all.
cp "$TMP" "$KEEP" 2>/dev/null

# Now the card, once, with one small file. Only if there is somewhere to put it.
if mkdir -p "$OUT" 2>/dev/null; then
    cp "$TMP" "$OUT/report.txt" 2>/dev/null
    cp "$KEEP" "$OUT/report-previous.txt" 2>/dev/null
    sync
fi

# --- the slow part, only if you ask for it, and only if the card has room ------------------------
# Create an empty file called "copy-everything" next to lunch.sh on the card to turn this on.
[ -f /mnt/SDCARD/copy-everything ] || exit 0

# Refuse if the card has less than 64 MB free. Filling it is what caused the empty files.
FREE=$(df -k /mnt/SDCARD 2>/dev/null | awk 'NR==2 {print $4}')
case "$FREE" in ''|*[!0-9]*) FREE=0 ;; esac
if [ "$FREE" -lt 65536 ]; then
    echo "" >> "$OUT/report.txt"
    echo "===== copy skipped: only ${FREE} KB free on the card =====" >> "$OUT/report.txt"
    sync
    exit 0
fi

cp -r /data/theme "$OUT/theme" 2>>"$OUT/report.txt"
sync
cp -r /data/info  "$OUT/info"  2>>"$OUT/report.txt"
cp -r /data/fonts "$OUT/fonts" 2>>"$OUT/report.txt"
sync
cp /data/KeyboardDevice "$OUT/KeyboardDevice" 2>>"$OUT/report.txt"
echo "===== copies complete =====" >> "$OUT/report.txt"
sync
