#!/bin/sh
# Try a theme for ONE boot, then put everything back - without the SD card leaving the device.
#
# Upload this as /mnt/SDCARD/lunch.sh. Waveshare's /etc/init.d/qt_app1 runs it as root at every boot,
# before KeyboardDevice reads config.json. To arm a test, upload a file /mnt/SDCARD/theme-test-once
# containing the full path of a .Theme that is already on the device.
#
#   boot after arming : config.json is saved, then pointed at that theme. The arm file is deleted.
#   the boot after    : config.json is put back exactly as it was before the test.
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
