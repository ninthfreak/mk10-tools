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

## What is still unknown

- **Writing one.** The reader is verified against the stock theme; nothing writes a `.Theme` yet, and the
  device's tolerance for a hand-made one is untested.
- **Whether the device reloads a theme** without a reboot, and what `DELETE_THEME` (V1 cmd 11) does.
- Item types other than the eight above. The firmware has more widget factories than the stock theme uses —
  audio visualisers and a line chart among them — so there are type numbers we have not seen.
