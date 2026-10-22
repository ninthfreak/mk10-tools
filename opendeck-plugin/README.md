# opendeck-mk10

Unofficial [OpenDeck](https://github.com/nekename/OpenDeck) device plugin for the **Waveshare MK10**
(10 LCD keys + a small screen). Not affiliated with Waveshare. Needs MK10 firmware **V2.32 or newer**.

## Install
1. Quit ScreenKey completely (only one program can use the MK10's serial port).
2. Optional check, without OpenDeck running: unzip, then run `opendeck-mk10-win.exe --test`
   (Linux: `./opendeck-mk10-linux --test`). Keys should show 1–10, the small screen a clock,
   and each key turns white while held.
3. OpenDeck: **Plugins → Install from file** → `opendeck-mk10.plugin.zip`. Restart OpenDeck if the MK10
   doesn't appear.
4. Linux only: copy `60-opendeck-mk10.rules` to `/etc/udev/rules.d/`, then
   `sudo udevadm control --reload-rules && sudo udevadm trigger`, and replug the MK10.

## How it works
The MK10 appears as a USB serial port (USB ID `1d6b:0104`). The plugin:
- handshakes with `getInfo` (JSON, command 101) and reads the device's own layout
  (896×304 canvas: keys at 128×128, small screen 240×296 at x=656),
- keeps one 896×304 picture, paints each key image OpenDeck sends into its slot,
  and sends the whole picture as one JPEG (command 100) after changes settle,
- turns `keyStateChanged` messages into OpenDeck key down/up (position = row × 5 + column),
- maps OpenDeck brightness to `setBacklight`, and shows a clock on the small screen.

Frame format (little-endian): `A1 A5 5A 5E | seq | cmd | len | crc32(len) | payload | crc32(payload)`.

## Known limits
- The MK10's keyboard chip also types keystrokes. Linux: the udev rule above stops that.
  Windows: disable the MK10's "HID Keyboard Device" (USB ID 4250:426F) in Device Manager (needs admin).
- OpenDeck has no slot for the small screen yet, so it shows a clock.
- Older firmware (e.g. V2.29) is detected and refused with a log message.

## Build
Rust 1.85+. `cargo build --release` (Linux needs `libudev-dev`); Windows cross-build:
`cargo build --release --target x86_64-pc-windows-gnu` with MinGW (`.cargo/config.toml` sets the linker).
`./package.sh` builds both and makes `dist/opendeck-mk10.plugin.zip`.

## Test
- `cargo test` checks the packet code.
- `python tests/fake_opendeck.py target/release/opendeck-mk10` runs the real plugin against a fake OpenDeck and a
  simulated MK10 (Linux; needs `pip install websockets pillow`).
