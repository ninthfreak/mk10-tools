#!/bin/sh
# Stands in for Waveshare's /data/setusbconfig for ONE boot. lunch.sh bind-mounts this file over it, and a
# reboot drops the bind mount, so nothing on the device's own storage changes.
#
# KeyboardDevice runs "/data/setusbconfig serial" when it starts. Instead of the serial port alone, this builds
# the serial port + a HID telephony headset + a USB sound card on the MK10's own speaker and mic: the shape of
# a USB headset, which is what meeting apps look for. /tmp/mk10hid/mode says which of "hid" and "audio" to add.
# Anything but "serial" goes to Waveshare's real script, which lunch.sh saved as /tmp/mk10hid/setusbconfig.orig.
#
# If the full set won't bind (the USB controller has only so many endpoints), it drops the sound card, then
# the HID function, so the serial port - and so the MK10 itself - keeps working whatever happens.

R=${MK10HID_ROOT:-}             # tests only: run against a fake tree
D=$R/tmp/mk10hid
GADGETS=$R/sys/kernel/config/usb_gadget
G=$GADGETS/g1
LOG=$D/log.txt
MODE=" $(cat "$D/mode" 2>/dev/null) "

say() { echo "$(cut -d' ' -f1 /proc/uptime) gadget: $*" >> "$LOG"; }

stop_helpers() {
    # The relay must not hold /dev/hidg0 across an unbind (older kernels' HID gadget can crash on that),
    # and the audio loops hold the gadget's sound card. Stop the loops first so they don't restart them.
    killall mk10loop.sh 2>/dev/null
    killall mk10hid arecord aplay 2>/dev/null
}

if [ "$1" != serial ]; then
    say "passing '$*' to Waveshare's setusbconfig"
    stop_helpers
    exec "$D/setusbconfig.orig" "$@"
fi

stop_helpers
if [ ! -d "$GADGETS" ]; then
    say "no configfs USB gadgets; handing over to Waveshare's setusbconfig"
    exec "$D/setusbconfig.orig" serial
fi
say "KeyboardDevice asked for serial; building serial +$MODE"

UDC_NAME=$(ls "$R/sys/class/udc" 2>/dev/null | head -n 1)
mkdir -p "$G/strings/0x409" "$G/configs/c.1/strings/0x409" "$G/functions/acm.usb0"

unlink_all() {
    for link in "$G"/configs/c.1/*; do
        [ -L "$link" ] && rm -f "$link"
    done
}

[ -n "$(cat "$G/UDC" 2>/dev/null)" ] && echo "" > "$G/UDC"
unlink_all

# Same IDs as Waveshare's serial mode, so MK10 Manager and the probe still find it; a new bcdDevice so Windows
# doesn't reuse what it remembers about the serial-only device. 0xEF/2/1 says "composite, read the interface
# association descriptors", which Windows needs to keep the serial port's two interfaces together.
echo 0x1d6b > "$G/idVendor"
echo 0x0104 > "$G/idProduct"
echo 0x0110 > "$G/bcdDevice"
echo 0xEF > "$G/bDeviceClass"
echo 0x02 > "$G/bDeviceSubClass"
echo 0x01 > "$G/bDeviceProtocol"
echo "MK10 call-control test" > "$G/strings/0x409/product"

WANT="acm.usb0"
case "$MODE" in *" hid "*)
    F=$G/functions/hid.usb0
    mkdir -p "$F"
    echo 0 > "$F/protocol"
    echo 0 > "$F/subclass"
    echo 64 > "$F/report_length"
    if "$D/mk10hid" descriptor > "$F/report_desc"; then
        WANT="$WANT hid.usb0"
    else
        say "could not write the HID report descriptor; leaving HID out"
    fi ;;
esac
case "$MODE" in *" audio "*)
    F=$G/functions/uac1.usb0
    mkdir -p "$F"
    # PC -> MK10 speaker: 48 kHz stereo, as Waveshare's own USB-speaker mode.
    echo 3 > "$F/c_chmask"; echo 48000 > "$F/c_srate"; echo 2 > "$F/c_ssize"
    # MK10 mic -> PC: 16 kHz, the rate the mic already runs at for the voice assistant (asound.conf).
    echo 3 > "$F/p_chmask"; echo 16000 > "$F/p_srate"; echo 2 > "$F/p_ssize"
    WANT="$WANT uac1.usb0" ;;
esac

bind() {
    unlink_all
    for f in $1; do
        ln -s "$G/functions/$f" "$G/configs/c.1/$f" || return 1
    done
    echo "$UDC_NAME" > "$G/UDC" 2>> "$LOG"
}

if bind "$WANT"; then
    GOT=$WANT
elif [ "$WANT" = "acm.usb0 hid.usb0 uac1.usb0" ] && bind "acm.usb0 hid.usb0"; then
    GOT="acm.usb0 hid.usb0"
    say "the full set would not bind; left the sound card out"
else
    echo "MK10 call-control test FAILED" > "$G/strings/0x409/product"
    if bind "acm.usb0"; then
        GOT="acm.usb0"
        say "only the serial port would bind"
    else
        say "even the serial port alone would not bind; handing over to Waveshare's setusbconfig"
        unlink_all
        exec "$D/setusbconfig.orig" serial
    fi
fi
say "bound to $UDC_NAME with: $GOT"

# In the background with nothing of the caller's left on stdin/stdout/stderr: KeyboardDevice waits for this
# script to finish. (The MK10's BusyBox has no setsid or nohup, and a non-interactive shell needs neither.)
for helper in relay speaker mic; do
    case "$helper $GOT " in
        "relay "*" hid.usb0 "*|"speaker "*" uac1.usb0 "*|"mic "*" uac1.usb0 "*)
            "$D/mk10loop.sh" "$helper" < /dev/null >> "$LOG" 2>&1 & ;;
    esac
done
exit 0
