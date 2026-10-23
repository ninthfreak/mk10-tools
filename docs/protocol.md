# MK10 protocol

Unofficial. Worked out by reading the program that runs on the MK10 (`KeyboardDevice`, firmware V2.32, shipped
inside Waveshare's ScreenKey v1.1 installer), then confirmed on a real MK10. Facts marked *(unverified)* come only
from reading the firmware.

## Transport

- The MK10 shows up as a USB serial port (CDC-ACM), USB ID `1d6b:0104`.
  That's the generic Linux "multifunction composite gadget" ID, so other devices can share it. Always confirm with
  the `getInfo` handshake.
- Windows: a COM port (COM6 on the first test machine). Linux: `/dev/ttyACM*`.
- The baud rate doesn't matter (USB). Tools here use 115200 with DTR and RTS on.
- One program at a time: the official ScreenKey app, the OpenDeck plugin and MK10 Manager all open the same port.

## New format (V2), firmware V2.32

All integers little-endian.

| Bytes | Field |
|---|---|
| 4 | magic `A1 A5 5A 5E` |
| 4 | seq |
| 4 | cmd |
| 4 | payload length |
| 4 | crc32 of the 4 length bytes |
| n | payload |
| 4 | crc32 of the payload |

CRC is standard CRC-32 (zlib). A reference frame is in `opendeck-plugin/src/protocol.rs` tests.

- **seq:** the host picks it, usually counting up. Replies echo it.
  Events the device starts (key presses) count down from `0xFFFFFFFE`. Error replies use `0xFFFFFFFF`.
- **cmd 100, show picture:** the payload is one JPEG of the whole canvas, exactly `deviceWidth × deviceHeight`
  (896×304). Any other size gets an error: "Incorrect resolution, the device requires the resolution to be 896*304".
  Baseline JPEGs from Qt and from the Rust `image` crate both work.
- **cmd 101, JSON:** `{"method": "...", "parameters": {...}, "requestId": seq}`.
  Reply: `{"ack_method": "...", "success": true, "result": {...}}` or `"success": false` with `"errorString"`.
  An unknown method's error lists the available methods.

### JSON methods in V2.32

Confirmed on a real MK10 (V2.32) by asking for a method that can't exist — the firmware answers by listing
every method it has. The list below is complete; there are no undocumented methods, and **nothing that draws
part of the display**. `mk10probe.py explore` does this.


| Method | What it does | Safe to use? |
|---|---|---|
| `getInfo` | Model, version, canvas size, layout (the handshake) | Yes |
| `setBacklight` | `{"level": 0-100}` | Yes |
| `setVolume` | `{"level"?: 0-7}` *(unverified parameter name)* | Probably |
| `playAudio`, `stopAudio` | Device speaker *(unverified parameters)* | Untested |
| `keyboardInput` | Probably makes the device type keystrokes as a USB keyboard *(unverified)* | Untested |
| `getFilesBySuffix` | Lists files. Wants a parameter spelled **`suffixs`** (confirmed on the real device: anything else answers `Missing 'suffixs' parameter`). The value's shape isn't known yet | Read-only |
| `saveToFile`, `setFileCRC`, `deleteFiles` | Write or delete files on the device | **No** without a clear reason |

### getInfo result (V2.32, real device)

```json
{"deviceModel": "MK10", "deviceVersion": "V2.32", "deviceWidth": 896, "deviceHeight": 304,
 "devicePanel": {"rectCols": 5, "rectRows": 2, "rects": [
   {"row": 0, "col": 0, "x": 0, "y": 0, "width": 128, "height": 128, "isKey": true},
   "... 10 keys at x = col*128, y = row*128 ...",
   {"x": 656, "y": 0, "width": 240, "height": 296, "isKey": false}]}}
```

- Keys: 128×128 at `x = col × 128`, `y = row × 128`, in a 2×5 grid. Key position = `row × 5 + col`, numbered 1-10
  left to right, top to bottom on the device. The key area ends at x = 640. Nothing visible sits between x = 640 and 656.
- Small screen: 240×296 at x = 656, y = 0. Portrait, upright, rounded corners (keep content about 10 px in).
  It isn't a key and doesn't report touches.
- Confirmed on the real device with a numbered test pattern (`probe/mk10probe.py show-test`).

### Key events

```json
{"method": "keyStateChanged", "parameters": {"row": 1, "col": 3, "pressed": true}}
```

A down event, then an up event (`pressed: false`).

## Old format (V1)

Used by firmware V2.29 for everything. V2.32 still answers parts of it, including file transfer and storage info.

| Bytes | Field |
|---|---|
| 22 | ASCII `AA551234 FIXEDCMDHEAD ` |
| 4 | type: 0 = command, 2 = acknowledgement |
| 4 | cmd |
| 4 | payload length |
| 4 | crc32 of the payload |
| n | payload |

Payload: Qt5 `QDataStream` `QMap<QString,QString>`. That's a big-endian count, then pairs. Each QString is a
big-endian u32 byte length followed by UTF-16BE. Pairs are written last key first. Every command gets an
acknowledgement with the same cmd number.

| cmd | Name | Notes |
|---|---|---|
| 0 | FIND_DEVICE | Reply has `version`, `screen_width`, `screen_height`, `screen_model`, `deviceBl`, `deviceVolume` |
| 1 | SEND_SYSTEM_DATA | PC stats for themes (CPU, GPU, RAM, time; names are in Chinese in the firmware) |
| 2 | RELOAD | |
| 3 | GET_THEME | Theme files, plus `bytesTotal` and `bytesAvailable` in MB. On a device with no theme installed it returns the two sizes and nothing else (seen: 2648 MB total, 2598 MB free) |
| 4 | SET_BL | Backlight |
| 5 | SCAN_STATE | Acknowledged with an empty payload on a real V2.32 device |
| 6 | FILE_START | `{device_path: size}` |
| 7 | FILE_END | `{device_path: crc32 as decimal}`, reply `{"res": "1" or "0", "fileName": path}` |
| 8 | GET_VERSION | |
| 9 | CANVASFLIP | |
| 10 | SCREENMESSAGE | |
| 11 | DELETE_THEME | |
| 12 | SEND_PIXMAP | Named JPEGs, `QMap<QString,QByteArray>`. Tried on a real V2.32 device with no theme installed: every name acknowledged with an **empty payload and no visible change**. Looks theme-scoped and useless while the host drives the screen with cmd 100 |
| 13 | DEVICE_CMD | Device to host: key events on V2.29 (firmware name `ProactiveEscalationCMD`) |
| 14 | REQUEST_UPLOAD_KEY | |
| 15 | SEND_JSON | Device to host: the theme asks for live data (`deviceRequestSystemData`) |

V2.29 key events arrive as cmd 13 with a `QList<QMap<QString,QVariant>>`:
`[{type: "keyState", row, col, pressed}, {the device's own configured action for that key}]`.

## File transfer (V1)

1. Send FILE_START `{path: size}`. Wait for its acknowledgement.
2. Send the raw file bytes with no framing. The device treats everything as file data until it has `size` bytes.
3. Send FILE_END `{path: crc32 as decimal}`. The reply says `res` 1 (kept) or 0 (refused).

Sending `AA551234 Abort file transfer 123455AA` resets a half-finished transfer; send it first to be safe.
`probe/mk10probe.py send-test` rehearses this with two small files in `/data/mk10probe/`, one with a good checksum
and one with a bad one.

## Firmware update

- If the file path contains `KeyboardDevice`, the device runs its update routine: rename `/data/KeyboardDevice` to
  `/data/DeviceUpgradeFirmware`, copy the new file in, delete both temporaries, then `sync`.
  **There is no rollback.**
- `probe/mk10probe.py update` sends to `/data/Upgrade/KeyboardDevice`. It checks the file, the version and free
  space, rehearses first, and asks you to type UPDATE.
- Official update source: `https://www.cbytecat.com/appData/Keyboard/soft/windows/manifest.json` and `.../KeyboardDevice`.
  The manifest looks like `{"version": "V2.32", "fileMessage": [{"name": "KeyboardDevice", "path": "data/KeyboardDevice"}]}`.
- If a new program failed to start, the USB link (and so the update path) would be gone. Recovery would need the
  chip's USB recovery mode (Allwinner FEL) or a serial console on the board. Neither has been found or tested yet.

## Inside the firmware (V2.32), for future work

- `KeyboardDevice` is a Qt5 program (ARM hard-float, glibc). It draws to `/dev/fb0` and probably talks to the keyboard
  chip over `/dev/ttyS1`.
- It configures Wi-Fi (`wpa_supplicant`) and restarts network time (`/etc/rc.d/S98sysntpd restart`). So the MK10 can
  probably keep its own time on Wi-Fi.
- It has built-in theme widgets: analogue, digital and flip clocks, and an image-based clock made of a dial picture
  plus hand pictures with pivot points (`QGraphicsPixmapItemClockMake`: `OriginPoint_ax`, `OriginPoint_ay`, `path`,
  `rotate`, `scale`, `type`). Also progress rings, text widgets, a media viewer and charts.
  - Themes come from ScreenKey. The SD card holds `/mnt/SDCARD/config.json`.
  - **Open question:** can a theme clock keep running on the small screen while the PC sends key pictures?
    V1 SEND_PIXMAP might allow that.
- No command was found that runs a program. `RemoteCommandExecutor` is only the JSON method dispatcher.
