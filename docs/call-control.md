# The MK10 as a USB headset (call control)

Goal: ride on **USB HID telephony**, the semi-standard that headsets use to talk to meeting apps. The app
tells the device the call state through "lights" (off-hook, mute, ring, hold). The device answers, hangs up
and mutes through "buttons" (hook switch, phone mute). Teams, Meet, Zoom and Webex all speak it. Nothing has
to be scraped out of the apps.

Status (October 2026):
- **Round 1 on a real MK10 did not come up.** The product name stayed `Serial`. Cause: a bug in
  `usbconfig.sh`, explained under [Round 1](#round-1-configfs-wasnt-mounted).
- **Round 2 got further and hit a hardware-driver limit.** The serial number came back `mk10hid-serial-only`:
  HID would not bind, with or without the sound card. The USB controller has only **one interrupt endpoint and
  one isochronous endpoint**, and the serial port already holds the interrupt one. See
  [Round 2](#round-2-one-interrupt-endpoint). The test as built cannot work on the stock kernel.

## Why the MK10 can do this without new firmware

All of it comes from the firmware image (`MK10_V1.0.img`) and the running V2.32 `KeyboardDevice`.

- **The kernel has the parts built in.** The configfs USB gadget functions `CONFIG_USB_CONFIGFS_F_HID=y`
  and `CONFIG_USB_CONFIGFS_F_UAC1=y` (a USB Audio Class 1 sound card) are both there. So are ACM, RNDIS,
  mass storage and FunctionFS.
- **The board has a mic and a speaker.** Waveshare's docs list both, and the holes are on the case. The
  firmware agrees:
  - `xiaozhiAISound` records from `hw:audiocodec`. That is the XiaoZhi voice assistant.
  - `config.json` has `deviceVolume`.
  - The mixer has `HpSpeaker Switch`.
- **Waveshare already ships a USB-speaker mode.** `KeyboardDevice` reads `usbTYpe` (sic) from
  `/mnt/SDCARD/config.json`, then runs one of two things:
  - `/data/setusbconfig serial`, the normal mode.
  - `/data/setusbconfig uac1` followed by `arecord -Dhw:UAC1Gadget -f S16_LE -r 48000 -c 2 | aplay`. In this
    mode PC audio plays out of the MK10's speaker.
  - **The catch:** uac1 mode *replaces* the serial port, so no host app can talk to the device while it is on.
  - The value of `usbTYpe` that selects uac1 is not known.
- **Waveshare's `setusbconfig` can already build `uac1,hid`.** It sets that up with a boot-keyboard
  descriptor, but never combines it with the serial port. So the shape we want is one step from what the
  vendor built.

## What the test builds

For **one boot**, the MK10 comes up as a composite USB device:

| Function | What the PC sees | Used for |
|---|---|---|
| `acm.usb0` | the usual serial port, same `1D6B:0104` | MK10 Manager, unchanged |
| `hid.usb0` | a HID device with two collections, see below | call control |
| `uac1.usb0` | a sound card: 48 kHz stereo speaker, 16 kHz stereo mic | makes it a *headset*, see below |

The HID function has two collections.

**Telephony headset** (report 1). Meeting apps look for this:
- Input bit 0 is Hook Switch, absolute: 1 means off hook.
- Input bit 1 is Phone Mute, relative: 1 means toggle.
- Output bits 0 to 3 are the lights: Off-Hook, Mute, Ring and Hold.

**Vendor page 0xFF00** (report 2). This is our own 63-byte channel to `mk10hid` on the device.

`mk10hid` only relays; every decision is made on the host:
- It hands the lights the meeting app sets back to the host.
- When the host asks, it sends hook and mute presses.
- It can send its log back, so no file ever has to come off the device.

**Why include the sound card.** Meeting apps (Teams most of all) tie call control to the *selected audio
device*. They match the HID device and the sound card by USB container, i.e. "the same physical device".
That is moderate confidence. A HID-only device may simply be ignored. With UAC1 in the same composite device,
the MK10 *is* the headset: pick it as the speaker and mic, and its call-control collection belongs to it.
Audio goes through the MK10's own speaker and mic using Waveshare's own `arecord | aplay` pipeline.

## How it gets onto the device without the card leaving it

1. `probe hid-install` uploads four files with `saveToFile`, then arms the next boot:
   - `lunch-tests.sh` becomes `/mnt/SDCARD/lunch.sh`.
   - `hidtest/{mk10hid, usbconfig.sh, mk10loop.sh}` go to `/mnt/SDCARD/hidtest/`.
   - `hid-test-once` is uploaded last, so a partial install arms nothing.
2. At boot, `lunch.sh` (root, before `KeyboardDevice`) does three things:
   - Copies the files to `/tmp/mk10hid` and saves the real `setusbconfig` there.
   - **Bind-mounts `usbconfig.sh` over `/data/setusbconfig`.**
   - Deletes the arm file.
3. `KeyboardDevice` starts and runs `/data/setusbconfig serial`, as it always does. It gets our script, which
   then:
   - Builds serial + HID + sound card.
   - Starts the relay and the two audio loops.
   - Passes any other mode straight to Waveshare's script.
4. The next boot has no bind mount and an empty `/tmp`, so it is back to normal. **Nothing is written to the
   device's own storage**, so there is nothing to restore.

### If something goes wrong

**If the set won't bind** (the UDC has a limited number of endpoints):
- `usbconfig.sh` first drops the sound card, then the HID function.
- Only the serial port is left, under the product name `MK10 call-control test FAILED`. The MK10 keeps working.
- If even that fails, it hands over to Waveshare's own script.

**Breadcrumbs.** Each stage writes `mk10hid-<word>` into the gadget's USB **serial number**. Waveshare's
`setusbconfig` never touches that field, so it survives even when Waveshare's script does the final setup.
`probe hid` reads it (sysfs on Linux, the PnP instance ID on Windows) and explains it:

| Serial number | Meaning |
|---|---|
| (none) | `lunch.sh`'s call-control section didn't run: no arm file, or an old `lunch.sh` on the card |
| `mk10hid-armed` | armed, but the stand-in for `/data/setusbconfig` was never called |
| `mk10hid-refused-arm-file`, `-missing-files`, `-no-setusbconfig`, `-already-ours`, `-bind-mount-failed` | `lunch.sh` didn't arm, for that reason |
| `mk10hid-ran` | `usbconfig.sh` started, but stopped before trying to bind |
| `mk10hid-hid-audio`, `-hid`, `-hid-without-audio`, `-serial-only` | the set that bound |
| `mk10hid-handed-over` | nothing it built would bind, so Waveshare's script took over |

A new serial number makes Windows treat the MK10 as a new device. It may get a new COM port number; MK10
Manager and the probe find it by USB ID, so that doesn't matter to them.

### Round 1: configfs wasn't mounted

The first run on a real MK10 came up as plain `Serial`. The card image explains why:
- `adbd` normally mounts configfs at boot (`S80adbd`).
- On the running device, `/etc/rc.d/S80adbd` is an overlay **whiteout**. `qt_app1` disables `adbd`
  on every boot, so after the first boot it never starts again.
- So when `KeyboardDevice` runs `setusbconfig serial`, `/sys/kernel/config/usb_gadget` doesn't exist yet.
  Waveshare's script mounts configfs itself.
- `usbconfig.sh` took the missing directory as "no gadget support" and handed over to Waveshare's script.
  That is exactly what produced `Serial`.

Now `usbconfig.sh` mounts configfs itself, as Waveshare's script does. The other stages may or may not have
worked in round 1; round 1 left no way to tell, which is why the breadcrumbs exist now.

### Round 2: one interrupt endpoint

Result: product `MK10 call-control test FAILED`, serial number `mk10hid-serial-only`. Everything up to binding
worked: `lunch.sh` armed, `KeyboardDevice` called the stand-in, configfs mounted. Then neither
serial + HID + sound card nor serial + HID would bind, and only the serial port did.

Allwinner's UDC driver has a static endpoint table. These are the capability bits read out of the kernel image
(`boot.fex`, `Linux version 5.4.61 #229 Wed Oct 15 2025`, the `struct usb_ep` array in `.data`):

| Endpoint | Type | Direction | maxpacket |
|---|---|---|---|
| ep1in, ep2in, ep5in | bulk | in | 512 |
| ep1out, ep2out, ep5out | bulk | out | 512 |
| ep3 | **iso** (the only one) | either | 1024 |
| ep4 | **interrupt** (the only one) | either | 512 |

What each function needs, and why that fails:
- **ACM** (the serial port) needs bulk in, bulk out and **interrupt in** for its notifications. It takes ep4.
- **The kernel's HID function** (5.4) needs **interrupt in and interrupt out**. Two interrupt endpoints, on a
  controller that has one, so it can't bind even alone. (Making the OUT endpoint optional came in a later
  kernel.)
- **UAC1** with speaker and mic needs **two** iso endpoints, and there is one. That is why Waveshare's
  `setusbconfig uac1` sets `p_chmask=0`: speaker only.
- Gadget endpoint matching goes by these capability bits, so a bulk endpoint is never offered for interrupt
  or iso.

The `struct usb_ep` layout read out of the table matches mainline 5.4 exactly. That matters for the module
route below.

Ways forward, none built yet:

| Route | What it keeps | Cost | Difficulty | Confidence |
|---|---|---|---|---|
| **Kernel module** that adds interrupt (and maybe iso) to some bulk endpoints' capability bits before the gadget binds, loaded only on an armed boot | everything: COM port, the kernel's HID, speaker; mic if iso works on a bulk endpoint | build against 5.4 sources with the MK10's `.config`; a mistake oopses that one boot | 4/5 | HID binds ~55%; mic ~35% |
| **No kernel change:** serial over bulk only (`gser`, or a FunctionFS link with MS OS descriptors so Windows installs WinUSB itself) + HID written on FunctionFS with only an interrupt-in endpoint (lights arrive as SET_REPORT, which FunctionFS hands to userspace) | HID, speaker | MK10 Manager, the probe and the OpenDeck plugin move from a COM port to libusb; no mic (one iso endpoint) | 4/5 | ~60% |
| **Host-side instead:** Teams' local third-party app API through the Manager; a virtual HID device (uhid) on Linux for Meet in Chrome | nothing on the device | no headset; different per app | 2–3/5 | per app |

## Running it

```
pip install hidapi          (Windows: py -m pip install hidapi)
python mk10probe.py hid-install            asks you to type INSTALL; then power-cycle the MK10
python mk10probe.py hid                    lists the HID collections, pings the relay, then watches
python mk10probe.py hid --log-only         just the device's test log, saved as mk10hid-log.txt
```

- `hid` uses HID, not the serial port, so MK10 Manager can stay open.
- While it watches:
  - `a` goes off hook (answer).
  - `e` hangs up.
  - `m` toggles mute.
  - `l` fetches the log.
- On Linux the hidraw node is root-only unless a udev rule says otherwise.
- **Windows may make the MK10 its default speaker and mic** while the test is on. Use `--no-audio` for HID only.

## What it answers, and the risks going in

| Question | How we'll know | Prior |
|---|---|---|
| Does the composite device bind and enumerate? | `hid` finds two collections; Manager still works | ~70% (endpoint budget, Windows composite handling) |
| Does `KeyboardDevice` call `setusbconfig` at boot? | product name is the test's, not `Serial` | ~85% (adjacent strings; nothing else builds `1D6B:0104`) |
| Does Teams accept it as a headset? | it appears in Teams' device list | ~60% |
| Does Teams send the lights? | `hid` prints "PC set lights" during a call | ~50%, see below |
| Do our presses control Teams? | `m` mutes Teams, `e` hangs up | ~55% |
| Is the audio usable? | a test call | works but laggy: dmix adds ~85 ms, the mic's dsnoop ~64–256 ms |

**The biggest known risk** is in the kernel's HID gadget (5.4), which stalls a `SET_REPORT` control request.
That affects how the lights reach the device:
- Lights sent as interrupt-OUT reports arrive. That is what Windows does for `WriteFile`, Chrome's WebHID and
  Linux hidraw.
- Lights sent with `HidD_SetOutputReport` would be refused. That call always uses the control pipe.
- If Teams uses the second call, buttons may work while the lights never show up. The fix would be a kernel
  patch: hard (4/5).

## Bluetooth through the MK10's USB port

The question was: plug a USB Bluetooth adapter into the MK10, and use the MK10 as the PC's audio device with a
Bluetooth headset.

- **The USB port is a real host port.** `usbc1` is host-only (`usb_port_type = 1`), with EHCI and OHCI.
  Waveshare's optional Wi-Fi stick is an RTL8188GU: `qt_app1` runs `usb_modeswitch` on `0bda:1a2b`.
- **A plain USB Bluetooth adapter won't work with this kernel.** `CONFIG_BT=y` with BR/EDR and RFCOMM, but
  `# CONFIG_BT_HCIBTUSB is not set`. The firmware's own Bluetooth script expects a UART chip (RTL8723DS on
  `ttyS1`) that the MK10 doesn't have.
- **A module is possible in principle.** Modules are on: `CONFIG_MODULES=y`, with no module versioning and no
  signing. vermagic is `5.4.61 SMP preempt mod_unload ARMv7 p2v8`. So an out-of-tree `btusb.ko` could load.
  - It has to match Allwinner's patched 5.4 kernel structures, or it crashes the kernel.
  - After that the device still needs an audio stack for Bluetooth: bluez-alsa or PulseAudio, cross-built
    against glibc 2.23. Neither is on the device.
  - Then it needs routing between the gadget sound card and the headset, plus HFP for the mic.
  - **Difficulty 5/5, maybe 25–35% to end up working.**
  - HFP limits the mic to 8–16 kHz mono, the same as any Bluetooth headset on a PC.
- **The piggyback route is a dongle that is already a USB sound card.** Examples: Creative BT-W5, Avantree
  DG80, Jabra Link 380, Poly BT700.
  - These keep the whole Bluetooth stack inside the dongle and show up as standard USB Audio (plus HID
    telephony on the Jabra and Poly ones).
  - The MK10's kernel already has `CONFIG_SND_USB_AUDIO=y`, so no custom kernel and no BlueZ.
  - The MK10 would bridge the gadget sound card to the dongle's sound card, as it does to its own speaker in
    this test. It could also read the headset's buttons, though `CONFIG_HIDRAW` is off, so they would come in
    as input events.
  - **Difficulty 3/5 on top of this test, ~55–65%.**
  - Plugging the same dongle straight into the PC already gives a working (often certified) headset. The MK10
    in the middle is only worth it for the screen and keys showing the real call state of that headset.
