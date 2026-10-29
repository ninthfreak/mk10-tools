#!/bin/sh
# Keeps one helper of the call-control test running. usbconfig.sh starts it and stops it with killall.
#   relay    mk10hid on /dev/hidg0
#   speaker  PC -> MK10 speaker: the gadget's sound card into the default playback (Waveshare's own pipeline)
#   mic      MK10 mic -> PC: the shared mic capture Waveshare defines (CaptureMic) into the gadget's sound card
# A capture with nothing streaming ends after about 10 s, so they are simply started again.

R=${MK10HID_ROOT:-}             # tests only: run against a fake tree
D=$R/tmp/mk10hid
LOG=$D/log.txt
say() { echo "$(cut -d' ' -f1 /proc/uptime) $1: $2" >> "$LOG"; }

run=0
while [ $run -lt 5000 ]; do
    run=$((run + 1))
    err=/dev/null
    [ $run -le 3 ] && err=$LOG          # keep the first errors, not thousands of repeats
    case "$1" in
        relay)
            wait=0
            while [ ! -e "$R/dev/hidg0" ] && [ $wait -lt 30 ]; do sleep 1; wait=$((wait + 1)); done
            "$D/mk10hid" relay "$R/dev/hidg0" --log "$LOG" 2>> "$LOG" ;;
        speaker)
            arecord -q -D hw:UAC1Gadget -f S16_LE -r 48000 -c 2 -t raw -B 80000 2>> "$err" |
                aplay -q -f S16_LE -r 48000 -c 2 -t raw -B 80000 2>> "$err" ;;
        mic)
            arecord -q -D CaptureMic -f S16_LE -r 16000 -c 2 -t raw -B 80000 2>> "$err" |
                aplay -q -D hw:UAC1Gadget -f S16_LE -r 16000 -c 2 -t raw -B 80000 2>> "$err" ;;
        *)
            say "$1" "unknown helper"
            exit 2 ;;
    esac
    [ $run -le 5 ] || [ $((run % 100)) -eq 0 ] && say "$1" "stopped after run $run; starting again"
    sleep 2
done
say "$1" "gave up after $run runs"

exit 0
