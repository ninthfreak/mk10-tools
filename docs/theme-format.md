# The MK10 `.Theme` format

Read out of the stock theme on a real MK10 — `默认主题-微雪-20250926/默认主题-微雪.Theme`, 13.2 MB, taken off
the device's own SD card. Everything here is from that file, not from guesswork. `mk10probe.py theme-read`
parses it.

Where it lives: `/data/theme/MK10/<theme folder>/<name>.Theme` on partition 6 (`UDISK`) of the SD card.
`config.json` on the 32 MB partition points at it with `defaultTheme`.

## Container

Three QDataStream pieces back to back, **big-endian** throughout:

```
QMap<QString,QVariant>    language (int), keyMacroValue (QByteArray), keyMacro (QByteArray, usually null)
qint64                    length of the JSON that follows
<bytes>                   UTF-8 JSON — the theme proper
QMap<QString,QByteArray>  asset path -> file bytes, 406 entries in the stock theme
```

The Qt primitives, for reference:

| Type | Encoding |
|---|---|
| `QString` | u32 byte length, then UTF-16**BE**. `0xFFFFFFFF` = null |
| `QByteArray` | u32 length, then raw bytes. `0xFFFFFFFF` = null |
| `QVariant` | u32 type id, 1-byte isNull flag, then the payload. Seen: 2 = int, 10 = QString, 12 = QByteArray |
| `QMap<K,V>` | u32 entry count, then key/value pairs |

Asset paths look like `/image/128x128/ApplicationIcon/original-….gif` and `/image/240x296/…`, and items
reference them by exactly that string. The stock theme's assets are GIF and PNG.

## The JSON

```json
{
  "main": { "currentPage": "<page uuid>", "version": "V3.0" },
  "pages": [ { "pageName": "<uuid>", "canvas": {...}, "items": [ ... ] } ]
}
```

**Every value is a string**, including numbers and booleans: `"x": "698"`, `"scale": "0.8"`, `"lock": "1"`.

`canvas`: `canvas_w`, `canvas_h` (896×304 here), `canvas_rotate`, `canvas_flip`, `showUnit`.

The stock theme has 13 pages and 176 items. Pages are referenced by UUID, not by name.

## Items

Common to every item: `id`, `type`, `x`, `y`, `z`, `rotate`, `scale`, and usually `lock`, `itemName`,
`opacity`, `backupX`, `backupY`, `backgroundType` (`"main"` / `"secondary"`).

`type` is a **number as a string**. What each one is, from the properties the stock theme's own items carry:

| `type` | What it is | Its own properties |
|---|---|---|
| `100` | image or video | `path`, `w`, `h`, `maxWidth`, `maxHeight` |
| `101` | circular bar | `margin`, `radius`, `back_color`, `front_color` |
| `102` | rectangular bar | `corner_radius`, `border_width`, `border_color`, `lineargradient_flag`, `lineargradient_color`, `w`, `h` |
| `109` | arc gauge | `angleMinValue`, `angleMaxValue`, `arcRadius`, `arcCircularInterval`, `gradientColor1..3`, `radius` |
| `111` | flip / digit display | `displayNum`, `displayType`, `transition`, `paths`, `text_font` |
| `113` | text | `text_str`, `text_font`, `front_color`, `text_customFont_flag`, `text_customFont_path` |
| `114` | state image | `path` **and** `paths`; the live value picks the frame |
| `115` | **a key** | `row`, `col`, `controlData`, `title`, `titleParam`, `soundFile`, `path`/`paths`, `scaledWidthTo`, `scaledHeightTo` |

Live data is wired up with `system_data_flag`, `system_data_name` (Chinese, e.g. `时` for the hour — see
`DATA_NAMES` in the probe), and `system_data_min_value` / `system_data_max_value` for the gauges.

### Value formats

- **Colour**: a string, `r=255,g=255,b=255,a=255`.
- **Font**: `QFont::toString()`, Qt 5.12's ten fields — `Arial,72,-1,5,75,0,0,0,0,0`.
- **`titleParam`**: JSON *inside* the JSON, as a string:
  `{"FontFamily":"Arial","FontSize":21,"FontStyle":"Bold","FontUnderline":false,"ShowImage":true,"ShowTitle":true,"TitleAlignment":"top","TitleColor":"#ffffff"}`
- **`frameDelays`**: `"200,200,200,200"` — milliseconds per animation frame.

## What a key does: `controlData`

Base64 of the same `QMap<QString,QVariant>`. A page-opening key decodes to:

```
type              "openPage"
pageName          "f50e4f2b-38b6-494b-b4bd-31250294e9e7"
iconPath          "/static/icon/dark/createFolder.png"
description       <text>
parentDescription <text>
```

Action types in the stock theme: `openPage`, `pageSwitch`, `oneLevelUp`, `openWeb`, `keyboard`,
`ControlFlow`.

## Reading one

```
python probe/mk10probe.py theme-read FILE            # summary: pages, item types, assets
python probe/mk10probe.py theme-read FILE --keys     # also every key and what it does
python probe/mk10probe.py theme-read FILE --out DIR  # write theme.json and all the assets out
```

Offline, no device needed.

## Writing one

`join_theme` in the probe is the inverse of `split_theme`, and `theme_json_bytes` serialises the JSON the
way Qt's `QJsonDocument::Indented` does — 4-space indent, empty arrays and objects split across two lines,
a trailing newline. Both are checked against the stock theme and reproduce it **byte for byte**: the
container (13,213,899 bytes) and the JSON alone (196,304 bytes). So an edited theme differs from the stock
one only where it was edited.

Qt writes a `QMap` in **descending** key order, which is why the header reads `language`, `keyMacroValue`,
`keyMacro`. The device reads maps back into a `QMap`, so order doesn't matter to it; the writer keeps
whatever order it was given so round trips stay exact.

## The custom-data-name experiment

Live data reaches a theme over V1: the device sends cmd 15 naming what its page shows
(`deviceRequestSystemData`), and the host answers with cmd 1 `SEND_SYSTEM_DATA`, a
`QMap<QString,QString>` of name → value. The stock theme only uses the firmware's own names (`时`,
`Cpu使用率` …). Whether the host can feed a name **it chooses** decides whether a theme can show
the manager's state — mute, levels, Slack, track titles — or only PC stats.

`mk10probe.py theme-make-test STOCK OUT` builds the test from the stock theme by changing exactly two items
on the start page's small screen:

| Item | Stock | Test | What it shows |
|---|---|---|---|
| label, id 138 | `GPU` | `TEST` | the test theme is the one that loaded |
| value, id 135 | bound to `Gpu使用率` | bound to `mk10test` | **the question** |
| value, id 134 | bound to `Cpu使用率` | unchanged | the control |

`mk10probe.py sysdata --test` then pushes `Cpu使用率` cycling 11…99 and `mk10test` counting 1, 2, 3, and
prints any cmd 15 the device sends.

| TEST label | CPU number moves | TEST number moves | Means |
|---|---|---|---|
| no | — | — | the theme didn't load; nothing else is learned |
| yes | no | no | our push format is wrong; the question is still open |
| yes | yes | no | **only built-in names work** |
| yes | yes | yes | **custom names work** |

### Result (October 2026, real MK10, firmware V2.32): custom names work

| TEST label shown | CPU number moved | TEST number moved |
|---|---|---|
| yes | yes, 11, 22, 33 … | **yes, 1, 2, 3 …** |

The number bound to `mk10test` — a name the firmware has never heard of — displayed what the host pushed
under that name. So a theme item can show **any value the host chooses to name**, which is what lets a
theme carry the manager's state (mute, levels, Slack, track titles) rather than only PC stats.

The device **never sent cmd 15** during the run. It did not ask for any name, built-in or custom, and
displayed the pushed values anyway: it accepts `SEND_SYSTEM_DATA` unprompted and binds by name. Whether it
sends cmd 15 in some other situation (at boot, before a host connects; on a page switch) is unknown.

Also proven by the same run, all for the first time on hardware:

- the device loads a theme our writer produced;
- `lunch-theme-test.sh` switched and restored `config.json` on the device's own busybox;
- `saveToFile` writes `/mnt/SDCARD` as well as `/data`.

Not yet tested: whether gauges, bars and state images (types 101, 102, 109, 114) take custom names the
same way text does; non-numeric strings; how fast values can be pushed before the device falls behind.

### Round 2: gauges, any string, and the push rate

`theme-make-test STOCK OUT --round 2` changes four items on the start page's small screen:

| Item | Stock | Round 2 | Tests |
|---|---|---|---|
| big top-left text, id 133 | `时` (the hour) | `mk10text` | any string: `OK`, `Hé`, `中文`, `Жж`, `ÆØ`, one too long to fit |
| middle arc gauge, id 2 | `Cpu使用率` | `mk10level` | a custom name on a gauge (type 109) |
| middle number, id 135 | `Gpu使用率` | `mk10count` | whether text keeps pace: one count per push |
| middle label, id 138 | `GPU` | `RATE` | this theme is the one that loaded |

**Result (October 2026, real MK10, V2.32)** — partly in:

| | Result |
|---|---|
| Theme loaded (`RATE` shown) | **yes** |
| Text keeps pace at 20 a second | **yes** — the middle number ended on exactly the last value sent (1201) and stopped when the probe did. No backlog |
| Device acknowledges `SEND_SYSTEM_DATA` | **yes, every one** — 1201 of 1201 as an empty V1 ACK with the same cmd. Round trip 6 ms median, 7 ms at the 99th percentile, **11 ms worst**; acknowledgements came every 50 ms like the pushes, never more than 56 ms apart. No malformed bytes in 60 s |
| Arc gauges (type 109) move | **probably yes — I was watching the wrong part.** A 109's gradient ring is a fixed track; what moves is a small **indicator dot** whose size is `arcRadius`. On the start page that is 5 px, easy to miss. On the PC Perf page (16 px) the photos show the dot at the right place for 7, 49, 96 and 97 — see below |
| Any string displays | **probably yes** — the strings changed and non-English characters appeared. What looked like text drawing over itself is a **layout collision**: the minute item beside the hour shows its placeholder `Text` (nobody pushed `分`), and the two overlap. Photos of the stock theme show the same thing with no strings involved: hour and minute both read `Text`, merged into `TexText` |
| Key presses reach the host while a theme runs | **yes** — both middle keys, down and up, as the usual V2 `keyStateChanged`. So the manager keeps its key actions with a theme loaded |

Photos taken afterwards, on the stock theme (the test had reverted on the next boot, as designed — the first
hardware confirmation that `lunch-theme-test.sh` undoes itself), added three things:

- **The device does not run the theme's clock itself.** The hour and minute items show their placeholder
  `Text` unless the host pushes `时` and `分`. ScreenKey must push the time.
- **The CPU ring looks the same at 98 and at 20**, consistent with arc gauges ignoring pushed values.
- **A state image does switch on a named value.** The robot on the small screen (id 132, type 114) changes
  expression by itself; it is bound to `randomNumbers`, a source inside the device. So type 114 picks its
  picture from a live-data name — the likeliest route to meters and mute icons if gauges stay unresponsive.

### Which widgets follow pushed values (stock pages, `sysdata --stock`, photos)

Photographed on the stock theme's own PC Perf and Clock pages while every name it uses was pushed:

| Type | Widget | Follows pushed values | Seen |
|---|---|---|---|
| `113` | text | **yes** | time `11:35`, date `2026-10-28`, every percentage |
| `111` | flip / digit display | **yes**, with its flip animation | `displayType` `1` is a dot-matrix clock on the keys, `0` plain digits |
| `101` | circular bar | **yes** | CPU 5 → a sliver, GPU 97 → full, RAM 49 → half |
| `102` | rectangular bar | **yes** | CPU 85 long and 35 short; GPU 57 then 83 |
| `109` | arc gauge | **yes — the dot moves, the ring doesn't** | indicator at the start for 7, the top for 49, the end for 96 and 97 |
| `114` | state image | yes, from a device-internal source (`randomNumbers`) | pushed values on one untested |

So every widget type the stock theme uses is driven by live data, and the device keeps no clock of its own:
`时`, `分`, `秒` and `日期` all come from the host.

Two stock-theme quirks, so they don't mislead anyone later: the start page's three arcs are all bound to
`Cpu使用率`, and PC Perf's **HDD** gauge is bound to `RAM使用率`.

### Page switching

A key whose action is `pageSwitch` carries `jumpToPage` as a **number**, not a page name: the 0-based index
among **top-level pages** — those with no `parentPageName`. In the stock theme, WIN HotKey → 1, PC Perf → 2 and
Clock → 3 land on the right pages only under that reading. `openPage` instead names its target by UUID and is
used for child pages (folders), which carry `parentPageName`; `oneLevelUp` returns from one.

**The host has no page-switch command** — about 80% confidence, from three places:

- **V1:** the firmware names its own commands (`CMD_VALUE_FIND_DEVICE`, `…_SEND_SYSTEM_DATA_TO_DEVICE`,
  `…_SET_DEVICE_RELOAD`, `…_GET_DEVICE_THEME`, `…_SET_DEVICE_BL`, `…_SET_DEVICE_SCAN_STATE`, `…_FILE_START`,
  `…_FILE_END`, `…_GET_DEVICE_VERSION`, `…_SET_DEVICE_CANVASFLIP`, `…_GET_DEVICE_SCREENMESSAGE`,
  `…_SET_DEVICE_DELETE_THEME`, `…_SEND_PIXMAP`, `…_DEVICE_ProactiveEscalationCMD`, `…_REQUEST_UPLOAD_KEY`,
  `…_SEND_JSON`, plus `…_JSON` and `…_SHOW_JPG` in V2.31). That is our table, complete, and none switches pages.
- **V2:** the device lists its ten JSON methods itself; none does.
- **JSON over V1 cmd 15:** the keys beside the serial code are device settings, mouse control, Wi-Fi and the
  keyboard chip (`setDeviceName`, `controlMouse`, `connect`/`wifissid`, `qmk_string`, `bootloader_jump`,
  `globalStatusUpdate`, `uploadAllState`). Inferred from how the strings cluster, so the least certain of the
  three. **`bootloader_jump` and the Wi-Fi keys must never be sent.**

`themePageSwitch`, `pageSwitch`, `jumpToPage`, `openPage` and `oneLevelUp` sit beside `videoplayer.cpp` with the
other key-action names: page switching is something the device does **when its own key is pressed**.

### `/data/info/ThemeMessage`

A `QMap<QString,QString>` register of installed themes, path → CRC32. On this unit it holds one entry, the
stock theme, with `2256378079` — exactly the stock `.Theme`'s CRC32. Themes put in place with `saveToFile`
are not in it and load anyway, so it is bookkeeping (most likely what V1 `GET_THEME` reports), not a
requirement for loading.

`sysdata --stock` pushes every name the stock theme uses (the PC's real time and date, and CPU, GPU and RAM
usage and temperatures each sweeping at a different speed), so the stock pages — PC Perf's bars and
circles, Clock's flip digits — show which widget types follow pushed values at all, without uploading
anything.

The acknowledgement matters as much as the rate. A pushed value costs the device ~6 ms, against ~29 ms to
decode a whole-canvas JPEG, and because every push is acknowledged the host can pace values exactly as it
already paces frames — send the next when the last is acknowledged.

`sysdata --test --round 2` pushes at 20 a second by default (`--every 0.02` for 50): CPU sweeping
0 → 100 → 0 as the control (it drives the left and right arcs and the left number), `mk10level` sweeping the
opposite way, `mk10count` counting, and `mk10text` changing every 2 s. It reports how many pushes went out
and how many the device acknowledged, if any, with their round trip.

The probe's serial reads normally wait up to 50 ms for input, which capped the first version of this loop at
8 a second — it would have blamed the device for the tool's own limit. `sysdata` now polls without waiting
and holds a fixed schedule; against the simulator it sustains 20.1 a second at the default and 50 when asked.

## What is still unknown

- **The device's tolerance for a theme that differs more from stock.** An edited stock theme loads; a
  generated one with different pages and items has not been tried.
- **Whether the device reloads a theme** without a reboot, and what `DELETE_THEME` (V1 cmd 11) does.
- Item types other than the eight above. The firmware has more widget factories than the stock theme uses —
  audio visualisers and a line chart among them — so there are type numbers we have not seen.
