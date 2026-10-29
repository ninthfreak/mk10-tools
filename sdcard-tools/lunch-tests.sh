#!/bin/sh
# One-boot tests that put everything back - without the SD card leaving the device.
#
# Upload this as /mnt/SDCARD/lunch.sh. Waveshare's /etc/init.d/qt_app1 runs it as root at every boot,
# before KeyboardDevice starts. Each test is armed by uploading a small file, which this deletes as it acts.
#
# Theme test: /mnt/SDCARD/theme-test-once holds the full path of a .Theme already on the device.
#   boot after arming : config.json is saved, then pointed at that theme.
#   the boot after    : config.json is put back exactly as it was before the test.
#
# Call-control (HID) test: /mnt/SDCARD/hid-test-once holds "hid audio" (or just "hid"), and the files from
# sdcard-tools/hidtest are in /mnt/SDCARD/hidtest/. For this boot only, /data/setusbconfig is covered by a
# bind mount of hidtest/usbconfig.sh, so the MK10 comes up as serial + HID headset (+ sound card). Everything
# it uses lives in /tmp. A reboot drops the bind mount and /tmp, so there is nothing to restore.
#
# So any second power cycle undoes a test, whether it worked or left the screen broken, and undoing it
# needs neither the card nor a working serial link. Nothing here loops or waits: the boot waits for this
# script, so it must always finish quickly.

SD=/mnt/SDCARD
CONF=$SD/config.json
SAVED=$SD/config.json.before-test
ONCE=$SD/theme-test-once
ACTIVE=$SD/theme-test-active
LOG=$SD/theme-test.log

say() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

# 1. The last boot ran a test theme: restore the config exactly as it was before it.
if [ -f "$ACTIVE" ]; then
    if [ -s "$SAVED" ]; then
        cp "$SAVED" "$CONF" && say "restored config.json from before the test"
    else
        say "WANTED to restore config.json but the saved copy is missing or empty"
    fi
    rm -f "$ACTIVE"
fi

# 2. A test is armed: switch to that theme for this boot only.
if [ -f "$ONCE" ]; then
    THEME=$(tr -d '\r\n' < "$ONCE")
    rm -f "$ONCE"
    case "$THEME" in
        *'#'*|*'&'*|*'"'*)
            say "refused: theme path has a character this script won't put in sed: $THEME" ;;
        *)
            if [ -z "$THEME" ] || [ ! -f "$THEME" ]; then
                say "not switching: no theme file at '$THEME'"
            elif [ ! -s "$CONF" ]; then
                say "not switching: config.json is missing or empty"
            else
                cp "$CONF" "$SAVED"
                sed "s#\"defaultTheme\"[[:space:]]*:[[:space:]]*\"[^\"]*\"#\"defaultTheme\":\"$THEME\"#" \
                    "$SAVED" > "$CONF"
                if grep -q "$THEME" "$CONF"; then
                    touch "$ACTIVE"
                    say "testing $THEME for this boot"
                else
                    cp "$SAVED" "$CONF"
                    say "switch did not take (no defaultTheme in config.json?); left it as it was"
                fi
            fi ;;
    esac
fi

# 3. A call-control test is armed: stand in for Waveshare's USB setup for this boot only.
HIDONCE=$SD/hid-test-once
HIDSRC=$SD/hidtest
HID=/tmp/mk10hid
HIDLOG=$SD/hid-test.log
hsay() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$HIDLOG"; echo "$(cut -d' ' -f1 /proc/uptime) lunch: $*" >> "$HID/log.txt"; }
if [ -f "$HIDONCE" ]; then
    MODE=$(tr -d '\r' < "$HIDONCE" | head -n 1)
    rm -f "$HIDONCE"
    mkdir -p "$HID"
    case " $MODE " in
        *[!a-z\ ]*|"  ")
            hsay "refused: hid-test-once should hold words like 'hid audio', not '$MODE'" ;;
        *)
            if [ ! -f "$HIDSRC/mk10hid" ] || [ ! -f "$HIDSRC/usbconfig.sh" ] || [ ! -f "$HIDSRC/mk10loop.sh" ]; then
                hsay "not armed: $HIDSRC needs mk10hid, usbconfig.sh and mk10loop.sh"
            elif [ ! -f /data/setusbconfig ]; then
                hsay "not armed: there is no /data/setusbconfig to stand in for"
            elif grep -q "call-control test" /data/setusbconfig; then
                hsay "not armed: /data/setusbconfig is already ours (a bind mount left from this boot?)"
            else
                cp "$HIDSRC/mk10hid" "$HID/mk10hid"
                # Scripts lose any CR on the way: '#!/bin/sh\r' would silently never run.
                tr -d '\r' < "$HIDSRC/usbconfig.sh" > "$HID/usbconfig.sh"
                tr -d '\r' < "$HIDSRC/mk10loop.sh" > "$HID/mk10loop.sh"
                cp /data/setusbconfig "$HID/setusbconfig.orig"
                chmod 755 "$HID/mk10hid" "$HID/usbconfig.sh" "$HID/mk10loop.sh" "$HID/setusbconfig.orig"
                echo "$MODE" > "$HID/mode"
                if mount -o bind "$HID/usbconfig.sh" /data/setusbconfig; then
                    hsay "armed for this boot: $MODE"
                else
                    hsay "not armed: the bind mount failed, so nothing changed"
                fi
            fi ;;
    esac
fi

sync

# Stop here, always. Uploading this over an older, longer lunch.sh may leave that file's tail behind if
# saveToFile doesn't truncate (untested), and nothing past this line must ever run.
exit 0
# padding so this file is longer than any lunch.sh it may be uploaded over                         
# padding so this file is longer than any lunch.sh it may be uploaded over                         
# padding so this file is longer than any lunch.sh it may be uploaded over                         
# padding so this file is longer than any lunch.sh it may be uploaded over                         
# padding so this file is longer than any lunch.sh it may be uploaded over                         
# padding so this file is longer than any lunch.sh it may be uploaded over                         
# padding so this file is longer than any lunch.sh it may be uploaded over                         
# padding so this file is longer than any lunch.sh it may be uploaded over                         
# padding so this file is longer than any lunch.sh it may be uploaded over                         
# padding so this file is longer than any lunch.sh it may be uploaded over                         
# padding so this file is longer than any lunch.sh it may be uploaded over                         
# padding so this file is longer than any lunch.sh it may be uploaded over                         
# padding so this file is longer than any lunch.sh it may be uploaded over                         
# padding so this file is longer than any lunch.sh it may be uploaded over                         
# padding so this file is longer than any lunch.sh it may be uploaded over                         
