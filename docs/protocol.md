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

Confirmed twice over: on a real MK10 by asking for a method that can't exist (the firmware answers by listing
every method it has — `mk10probe.py explore` does this), and against **Waveshare's own host-side reference
code**, `OpenSourceLicenseDemo_V1.0`, which calls every one of them. The parameter names below are theirs,
not guesses. There are no undocumented methods, and **nothing that draws part of the display**.

| Method | Parameters | Reply |
|---|---|---|
| `getInfo` | `{}` | `result` with `deviceModel`, `deviceVersion`, `deviceWidth`, `deviceHeight`, `devicePanel` |
| `setBacklight` | `{"level": int}` | |
| `setVolume` | `{"level": int}` | the device's own speaker |
| `playAudio` | `{"filePath": "/data/sound/temp.wav"}` | a path on the device |
| `stopAudio` | `{}` | |
| `keyboardInput` | `{"inputString": "Hello! 7895123"}` | the device types it as a USB keyboard |
| `getFilesBySuffix` | `{"suffixs": ["wav"]}` | `result.filePaths` is an array of **objects**: `{"filePath": ..., "crc": ...}`. The suffix must be **bare**: a leading dot is never stripped and matches nothing. `[]` means no filter. It lists only what *this host* uploaded with `saveToFile`, with the CRC the host declared — not the device's own files |
| `saveToFile` | `{"filePath": ..., "seek": int, "data": "<base64>"}` | `result.seek` — see below |
| `setFileCRC` | `{"filePath": ..., "crc": "<decimal as a string>"}` | ends an upload |
| `deleteFiles` | `{"filePaths": [...]}` | an array |

### The firmware's own validation messages

Read out of `/data/KeyboardDevice`. Useful because they say exactly what each method checks, and the wording
identifies which check failed when the device refuses something:

```
Missing 'method' field              Field 'method' must be a string      Field 'method' cannot be empty
Field 'parameters' must be an object                                     Empty JSON data provided
JSON parse error: %1 at offset %2   Invalid JSON: expected object
Unknown method: %1. Available methods: [%2]                              Method execution error: %1
Missing 'level' parameter           'level' must be a number
Backlight level must be between 0 and 100, got %1
Volume level must be between 0 and 7, got %1
Missing 'filePath' parameter        'filePath' must be a string          Missing 'crc' parameter
'seek' must be a number             'data' must be a string              'seek' position cannot be negative
Missing 'filePaths' parameter       'filePaths' must be an array
'suffixs' must be an array
'inputString' cannot be empty       'inputString' must be a string       inputLength
```

`deleteFiles` answers with `deletedFiles` and `failedFiles`. Replies carry `ack_method`, and `setVolume`'s
range is confirmed as **0–7**.

### Beyond the ten JSON methods

The same binary carries a much wider internal command vocabulary, reached over the serial link by ScreenKey
rather than through the V2 JSON API. Not verified against hardware, listed because it says what the device
is capable of:

- **Keyboard chip (QMK/VIA-style):** `dynamic_keymap_set_keycode`, `dynamic_keymap_set_keycodes`,
  `dynamic_keymap_macro_set_buffer`, `request_upload_key`, `request_upload_string`, `request_mouse_control`,
  `request_bootloader_jump`, with `row`, `col`, `layer`, `column`, `keycode`, `keyCodeH`/`keyCodeL`.
- **Mouse:** `controlMouse`, `qmk_mouse_key`, `mouse_x`, `mouse_y`, `mouse_h`, `mouse_v`, `qmk_mouse_event`.
- **Wi-Fi:** `wifissid`, `wifipsk`, `getDeviceNetworkState`, and a `NetworkManager` driving `wpa_supplicant`
  and `udhcpc` on `wlan0`.
- **OBS Studio, built into the firmware:** `ToggleRecord`, `ToggleStream`, `SetCurrentProgramScene`,
  `ToggleSceneItemEnabled`, `GetRecordStatus`, `GetStreamStatus`, `sceneName`, `sceneItemId`.
- **Home Assistant, likewise:** `homeAssistantControl`, `homeAssistantStateChanged`, `entity_id`, `GetState`.
- **Encoders:** `encoder_qmk_mouse`, `encoder_keyboard`, `encoder_device_volume`, `encoder_device_brightness`,
  `encoder_system_volume`, `encoder_system_brightness`, `encoder_system_media`.
- **Weather:** OpenWeatherMap icon codes (`01d`, `04n`, `shower rain`, …) plus a 564-file HeWeather icon set
  at `/data/static/icon/weather-icon-S1/`.
- **Other:** `setDeviceName`, `ShowImage`, `deviceMessage`, `playAudio`/`stopAudio` over `tinyplay`,
  `keyboardInput`, and an AI assistant (`xiaozhiAI`) with its own two binaries.

### Uploading a file the V2 way

There is a whole file-transfer path in the JSON protocol, separate from V1's FILE_START/FILE_END, and it is
**acknowledgement-driven**:

1. Read 64 KB from the local file at offset 0. Send `saveToFile` with `seek: 0` and the chunk base64-encoded.
2. The reply carries `result.seek` — the position just written. Add 64 KB, read the next chunk, send again.
3. When there is nothing left to read, send `setFileCRC` with the CRC-32 of the whole local file, as a decimal
   string. That commits it.

`MAX_READ_SIZE` in the reference is exactly `64 * 1024`.

`saveToFile` **creates missing folders**: an upload to `/data/mk10probe2/` worked with nothing having made
that directory first. So a path does not have to exist before writing to it.

`mk10probe.py upload FILE [--dest PATH]` does this, and `delete PATH...` takes it back. Both ask you to type
a word first, and the probe refuses to send any write method until the command that asked for it unlocks it
by name — so `explore`, `files` and `pixmap` cannot write by accident. `keyboardInput` is never unlocked.

One thing the reference leaves ambiguous: whether the `result.seek` coming back is the start of the chunk
just written or the position after it. The two differ by a whole chunk, and guessing wrong would write the
file inside out with neither side complaining, so `upload_v2` keeps its own offset and only warns if the
device's answer matches neither end.

### Pictures are acknowledged, and that is the intended pacing

The reference does **not** push frames on a timer. It sends one `cmd 100`, and when the device acknowledges
that frame it captures and sends the next. Flow control comes from the device, so the host never runs ahead of
what the panel can draw.

MK10 Manager does the same since the ack-pacing change: `DeviceManager` keeps one picture in flight, and the
acknowledgement is what releases the next one. The acknowledgement is a packet carrying **the same seq as the
picture** — on a real V2.32 device it comes back as `cmd 100` with an empty payload. Three useful consequences:

- The device, not a guessed frame rate, decides how fast the canvas updates. The "Updates a second" setting is
  now only a ceiling.
- The round trip is the first honest measure of what a redraw costs the device. It's on the Device and app tab
  next to "Pictures sent"; the old number there was only how long `write()` took on this PC.
- Firmware that never answers must not go dark, so after `ACK_GIVE_UP` (3) unanswered pictures at
  `ACK_TIMEOUT` (0.4 s) each, the manager stops expecting answers, says so in the status line, and falls back
  to sending on the timer.

Two oddities worth copying or at least knowing about:

- Before the first `getInfo`, the reference writes **1 MB of ASCII `0`** to the port. Most likely it flushes a
  half-finished file transfer, since the firmware swallows raw bytes while one is in progress.
- It accepts a second USB id besides `1d6b:0104` — **`1234:5678`**.

The CRC is standard zlib CRC-32 throughout (the table is the usual `0xEDB88320` one, and the streaming helpers
init to `0xFFFFFFFF` and xor at the end, which comes to the same thing).


| Method | What it does | Safe to use? |
|---|---|---|
| `getInfo` | Model, version, canvas size, layout (the handshake) | Yes |
| `setBacklight` | `{"level": 0-100}` | Yes |
| `setVolume` | `{"level"?: 0-7}` *(unverified parameter name)* | Probably |
| `playAudio`, `stopAudio` | Device speaker *(unverified parameters)* | Untested |
| `keyboardInput` | Probably makes the device type keystrokes as a USB keyboard *(unverified)* | Untested |
| `getFilesBySuffix` | Lists files — see the table above for the exact shape. It takes **no folder argument** (`path`, `dir`, `directory`, `folder`, `root`, `dirPath` are all accepted and silently ignored), and on the device tested it returned an empty list for every suffix including `[""]` and `[]`. Waveshare's own demo asks for `"wav"` without a dot and offers the result as things to `playAudio`, so its search root is probably the device's sound or upload folder rather than anywhere a theme lives | Read-only |
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
| 1 | SEND_SYSTEM_DATA | `QMap<QString,QString>` of live-data name → value. The firmware's own names are Chinese (CPU, GPU, RAM, time), but **any name works**: a theme item bound to a made-up name shows what the host pushes under it (tested October 2026). **Acknowledged every time**, as a type-2 packet with the same cmd, in ~6 ms. Text items follow it at 20 a second with no backlog; arc gauges (type 109) did not move at all |
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
| 15 | SEND_JSON | Device to host: the theme asks for live data (`deviceRequestSystemData`). From the firmware decode; **not seen** from a real V2.32 device, which takes cmd 1 values unasked |

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
