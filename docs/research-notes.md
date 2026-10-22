# Research notes and decisions

Notes from the first design sessions, kept so later work doesn't have to rediscover them.

## Goals

1. **Desktop macropad.** Windows for now (at work); Linux at home is the final target.
2. **Car accessory for an Android head unit,** driven by a companion app that reacts to what's going on
   (later; not started). The protocol code would port to Kotlin (for example with usb-serial-for-android).

## Why our own app (`manager/`)

Considered and set aside:

- **OpenDeck 2.14.2** (the `opendeck-plugin/` works for keys and brightness).
  - It draws every key picture in its own window on a fixed 144×144 canvas (infobar 248×58, dial strips 200×100),
    then sends JPEGs to the plugin. A full-resolution 240×296 small screen is impossible through it.
  - Device plugins can register `touchpoints` and `infobars` (not in the openaction 1.1.5 crate; send your own
    `registerDevice` JSON). Touchpoints are square key slots.
  - Profile switching by app: Windows works. On Linux only X11 and KDE Wayland (kdotool) work, through
    `active-win-pos-rs`.
  - Plugins aren't told when the active app changes.
- **Bitfocus Companion.**
  - Strengths: surfaces can declare a size per button (so the 240×296 screen could be one button), plus
    variables, triggers and 700+ modules.
  - Weak at desktop actions (shortcuts, launching apps). Can't run Stream Deck plugins.
  - Integration routes: the Satellite API (TCP 16622 / WS 16623) or a TypeScript surface module.
- **Pebble watch faces.**
  - Running PebbleOS in an emulator on the PC was rejected as a long-term solution.
  - Possible later: run Pebble apps' Thumb-2 code natively on the MK10's Cortex-A7, with our own implementation
    of the Pebble API jump table. That needs a safe way to run our own code on the MK10 first, which we don't have.
- **Web UI** for our app: rejected unless there's a strong reason. Desktop GUI (Qt) instead.
- **Config-file-only setup:** rejected. It has to be a GUI.

## Open leads

- **Built-in theme clocks.** The firmware has its own analogue, digital, flip and image-based clocks (see
  `protocol.md`). If one can run on the small screen while the PC sends key pictures, we'd get an on-device clock
  that keeps going without the PC. Needs the SD card's `config.json` (theme format) and experiments with V1
  SEND_PIXMAP.
- **`keyboardInput`.** If the device can type keystrokes itself, Wayland shortcut problems on Linux disappear. Untested.
- **Second hand.** A clock with a second hand sends the full 896×304 picture every second. Check for flicker on real
  hardware.
- **Running our own code on the MK10.** The only route found is replacing `/data/KeyboardDevice` with a launcher.
  That risks bricking the device until a recovery path is confirmed (FEL button or pads, or a UART header).

## Status (October 2026)

| Tool | State |
|---|---|
| `probe/mk10probe.py` | Used on the real device (probe, storage, show-test). The device went from V2.29 to V2.32, most likely through ScreenKey; the `update` command hasn't been used on real hardware |
| `opendeck-plugin/` | Works on the real device on Windows (keys, brightness, clock on the small screen) |
| `manager/` | First version. Tested only against a fake MK10 on Linux; not yet on real hardware or Windows |
