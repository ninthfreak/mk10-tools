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

- **Partial display updates: closed, as far as we can tell.** V2 has one drawing command and it wants the
  whole 896×304 canvas, so a moving level meter costs a full JPEG encode, transfer and device-side decode
  every frame — which is why meters look jerky on real hardware.
  - Asking a real V2.32 device for a method that can't exist makes it list every method it has. The list is
    exactly the ten already known, and **none of them draws anything**. There is no hidden partial-draw call.
  - V1 `SEND_PIXMAP` (cmd 12) was sent to the real device under six different image names. Every one was
    acknowledged with an empty payload and nothing on screen changed. The device had no theme installed
    (GET_THEME returned only the two storage figures), which fits the theory that it replaces images inside
    a theme. Retrying with a theme installed is the only way to settle it, and even then it would swap
    theme images rather than redraw an arbitrary key.
  - What's left for smoother meters: gentler ballistics (done), and sending fewer bytes per frame — JPEG
    quality is fixed at 90 in `render/canvas.py`.

### What the three probe runs settled (October 2026, real V2.32 hardware)

Everything theme-shaped on this device is inert, and all of it points the same way: **no theme is installed.**

| Asked | Answer |
|---|---|
| Which JSON methods exist? | Exactly the ten already known. None of them draws. |
| `GET_THEME` | Only `bytesTotal` 2648 MB and `bytesAvailable` 2598 MB. No theme files. |
| `SEND_PIXMAP`, six image names | Acknowledged with an empty payload every time; nothing on screen changed. |
| `getFilesBySuffix`, every suffix | Empty, and it won't be pointed at a folder. |
| `SCAN_STATE` | Acknowledged, empty. |

**Installing a theme and re-running is the only way to revive any of this** — the on-device clock idea included.

The SD card's `config.json` turned out to be a pointer, not a theme: themes sit on internal flash at
`/data/theme/MK10/<folder>/<name>.Theme` (see `hardware.md`). So:

- `getFilesBySuffix` returns nothing for `.Theme` either, so it finds nothing whatever it is asked. (Two
  reasons, found later: the leading dot is never stripped, and it only lists host uploads. See above.) Together
  with `saveToFile`, `setFileCRC` and `deleteFiles` it reads as a file-management API for content the *host*
  uploads, searching one fixed upload folder that nothing has ever been put in. The device cannot be made to
  list the theme it is actually running.
- Reading the stock theme is the real blocker — the format can't be designed against without it, and there is
  no read-file method in the protocol. The file is on flash, not the card, so pulling the card doesn't get it.
  Two offline ways at it, neither of which touches the device:
  - **Waveshare's ScreenKey installer** may carry a copy. `mk10probe.py theme-strings` reports any zip-like
    blobs inside it; 7-Zip opens most installers.
  - **The firmware contains the parser**, so its strings are the vocabulary a `.Theme` file is written in.
    `mk10probe.py theme-strings` pulls the paths, the theme words, the ones shaped like JSON keys and the Qt
    classes out of `KeyboardDevice` (extracting it from the installer first if needed). Enough of that may be
    sufficient to author a theme without ever seeing one.
- If `defaultTheme` accepts an SD-card path, a theme could be installed by writing to the card alone, with no
  file transfer to the device. That would make the whole experiment risk-free. Untested.
- **Built-in theme clocks.** The firmware has its own analogue, digital, flip and image-based clocks (see
  `protocol.md`). If one can run on the small screen while the PC sends key pictures, we'd get an on-device clock
  that keeps going without the PC. Needs the SD card's `config.json` (theme format) and experiments with V1
  SEND_PIXMAP.
- **`getFilesBySuffix`: solved, and it gives us nothing.** `{"suffixs": [".jpg", ".png"]}` answers
  `{"filePaths": [...]}`; the name is spelled `suffixs` and the value must be an array. On the real device it
  returned an empty list for **every** suffix tried, including `[""]` and `[]`, and it ignored a folder passed
  under six different parameter names. Its search root is fixed and was empty. Nothing to read, no way to
  point it somewhere else.
- **`keyboardInput`.** If the device can type keystrokes itself, Wayland shortcut problems on Linux disappear. Untested.
- **Second hand.** A clock with a second hand sends the full 896×304 picture every second. Check for flicker on real
  hardware.
- **Running our own code on the MK10.** The only route found is replacing `/data/KeyboardDevice` with a launcher.
  That risks bricking the device until a recovery path is confirmed (FEL button or pads, or a UART header).

## Microsoft Teams: what a desk device can and can't get (investigated October 2026)

**Built since (October 2026): in-call and mute state through UI Automation**, in `manager/mk10manager/system/teams.py`,
following [shuush](https://github.com/supermem613/shuush) (MIT, C#, started June 2026):
- AutomationId `microphone-button` exists in a Teams window only during a call. Its name is `Mute mic` (live) or
  `Unmute mic` (muted). Re-find it on every read, because WebView2 replaces its accessibility nodes on redraw
  and a kept node silently returns the old name.
- Pressing it with the Invoke pattern toggles mute with no focus and no hotkey. Explicit mute on/off is that
  press, done only after a read says it's needed and confirmed by reading again.
- shuush's history (17 June to 19 October 2026, about 25 commits) shows **no change to that contract**. Its
  Teams fixes were all about *when* to look: meetings that don't update Windows' microphone-in-use record,
  holding the state across a redraw that hides the button for one poll, and idle CPU. Four months is a short
  record. MuteDeck's changelog shows repeated breakage from Teams redesigns, so expect it eventually.
- The ctypes vtable slots were checked against Wine's `uiautomationclient.idl`.
- **Confirmed on the work PC (29 October 2026):** `microphone-button` found in 26 ms, name `Unmute mic` while
  muted, pressable by Invoke and LegacyIAccessible. During a call Teams has two windows, both class
  `TeamsWebView` in one `ms-teams.exe` process: the meeting (`Test | <org> | <account> | Microsoft Teams`) and the
  main one (`Calls | …`). A search of a window without the button took about 60 ms; listing the desktop's
  windows took about 200 ms in a fresh process (the reader keeps its process-name cache, so less in the app).
- The call window's other buttons, by AutomationId, as Teams named them then (camera off, hand down, not
  sharing): `video-button` "Turn camera on", `hangup-button` "Leave", `raisehands-button` "Raise",
  `share-button` "Share", `reaction-menu-button` "React", `chat-button`, `roster-button` "People",
  `view-mode-button`, `meeting-details-button` "Notes", `start-breakout-room-button` "Rooms",
  `audio-button-configure` / `video-button-configure` (the device menus), `hangup-button-more-options`.
- Second report, camera on and sharing: `video-button` "Turn camera off", `share-button` "Stop sharing". So those
  names flip with the state, like the mic's. `raisehands-button` still said "Raise" **with the hand raised**, so
  hand state isn't in the name; the reader tries the button's ARIA pressed state and Toggle pattern, which the
  next report shows. Sharing adds two windows the reader passes over: a `ScreenBorderWindow` from a
  second Teams process and a `Sharing control bar … | Pinned window`. The mic button was still found in 51 ms.
- Third report and the first whole-tree dump (hand raised, sharing, camera on):
  - **Hand:** no pressed state anywhere on `raisehands-button` (only `expanded=true`, meaning unproven). It is in
    your own video tile: an Image named `Myself video, <you>, Has control of production tools, Muted, video is
    on, Fill frame, Hand raised position 1, Has context menu`. Its AutomationId (`menur372`) is generated, so the
    reader searches images and takes the one starting `Myself`. Only while your tile is on screen.
  - **Call clock:** `Text [call-duration-custom] 'Elapsed time 04:04'` with a child `Text '04:34'`. The child is
    what's on screen; the name lagged 30 s behind it, which matches the user's "30 s behind". The reader takes
    the larger, and keeps the earliest start time its readings imply (the clock counts whole seconds).
  - **Sharing:** the `Sharing control bar` window holds a button `You're sharing your screen` (and its own
    `share-button`). What it says for a window share isn't known yet; the key shows whatever follows "sharing".
  - **Camera:** Teams' `LocalCache\Microsoft\MSTeams\cmd_settings.json` has `"Camera"` = a device interface path
    (`\\?\usb#vid_046d&pid_0825&…#{65e8773d-…}\global`, a Logitech C270) that wasn't among Windows' present
    cameras. The reader turns the path into an instance id and asks cfgmgr32 for the device's name, present
    devices only, so a camera that isn't plugged in names nothing rather than the wrong thing.
  - **Audio devices:** none of this says which microphone or speakers Teams picked, and the user saw the keys
    name Windows' defaults instead. Windows' audio sessions do: every endpoint lists the processes with a
    session on it and whether it's active. The Manager takes the endpoints where `ms-teams.exe` (or a process it
    started, such as WebView2) is streaming. Not yet seen on the work PC: which process the sessions belong
    to. The report lists them.

Asked for: which input/output devices Teams is using, the time, how long the meeting has been going,
how many people are in it, and who is speaking.

### The route that would have worked is closed

Teams used to run a local WebSocket on `ws://127.0.0.1:8124`, paired with a token from Teams' own settings.
It gave mute, camera, hand-raised, recording and blur state, and took commands back. That's what the Elgato
Teams plugin, MuteMe and Dell's collaboration keyboards used.
**Microsoft retired it on 30 June 2026** (message centre MC1266901) with no replacement for hardware, and
current clients don't accept connections. Don't build on it.

It never offered participant count, who is speaking, or which audio devices Teams had selected.

### What's left

| Route | Gives | Costs | Works on |
|---|---|---|---|
| UI Automation scraping (what MuteDeck does now) — **built, see below** | mute, camera, in-a-call; roster count with effort | breaks on Teams UI changes, language-dependent | Windows |
| `teams-for-linux` (Electron wrapper) MQTT / D-Bus | a status object (mic, camera), plus toggle-mute / toggle-video / raise-hand commands | you run their wrapper instead of the PWA; their own issue tracker shows mute detection breaking against current Teams | Linux |
| Graph `/me/presence` | availability and activity (`InAMeeting`, `InACall`, `Presenting`) | Entra app registration; delegated `Presence.Read`, or `Presence.Read.All` for change notifications (1-hour subscriptions, renewed by PATCH); no personal accounts; a work tenant will probably want admin consent | anywhere |
| Graph Communications calling bot | live roster **and** active speaker | an Azure-hosted bot with application permissions and admin consent, which **joins the meeting as a visible participant** | anywhere, but socially unacceptable at a desk |
| Teams **app platform** (apps that run *inside* Teams — see below) | meeting start/end and participant join/leave events, meeting details, "someone is speaking", and the active-speaker list under RSC | a Teams app package installed in the meeting or chat, a bot with a public HTTPS endpoint for the server-side half, and a relay you host to get any of it back to the desktop | anywhere, but it's a cloud service, not a local read |
| Local side channels, no Teams cooperation needed | "the microphone is open, so I'm in a call", and a timer from when it opened | nothing | both |

Local side channels are worth more than they look:

- **Microphone in use.** Windows: enumerate Core Audio sessions, or read
  `HKCU\...\CapabilityAccessManager\ConsentStore\microphone\NonPackaged\<app>` where `LastUsedTimeStop == 0`
  means in use right now. Linux: `pactl list source-outputs` says which app holds a capture stream.
  Language-independent and survives Teams updates. Worth having as a "something is listening right now"
  indicator and as a cross-app trigger (it fires for Zoom, Meet and Discord too), **not** as a clock: elapsed
  call time isn't the number anyone wants.
- **Window title** (we already watch it) sometimes carries the meeting subject.
- **Calendar.** The only source of what the meeting is *scheduled* for — subject, start, end, organiser, the
  invited attendee list, and whether it's an online meeting. Nothing in Teams is needed, and nothing here is
  being retired. Three ways in:
  - **Classic Outlook COM** on Windows (`Outlook.Application`, MAPI). No app registration, no OAuth, no admin
    consent — which matters a lot on a locked-down work PC. Dead end if the machine has "new Outlook", which
    has no COM.
  - **Graph `/me/calendarView`**, delegated `Calendars.Read`. Works on both platforms. Needs an Entra app
    registration; `Calendars.Read` is a common user-consentable scope, so less likely to need admin consent
    than `Presence.Read.All`, but a tenant can still block it.
  - **A published `.ics` URL** the manager polls. Trivial to consume, but many tenants disable calendar
    publishing.
- Teams' own `Ctrl+Shift+M` only works while Teams has focus, so sending keystrokes is not a global mute.

### Verdict per ask

- **Which devices Teams is using:** not exposed anywhere. The OS default devices (which the manager now shows)
  are what Teams uses unless someone overrode it inside Teams, and that override lives in Teams' own opaque
  settings store.
- **Time:** done.
- **Meeting length:** what's wanted is the *scheduled* time — "on until 10:15, 4 minutes left" — so this is a
  calendar job, not a Teams one. Classic Outlook COM on the work PC, Graph `calendarView` otherwise.
  Difficulty 2/5 via COM, 3/5 via Graph (the consent policy is the risk, not the code).
- **Number of people:** the *invited* count comes free with the calendar entry, which may be close enough to
  show. Who is actually in the room needs UI scraping (Windows-only, fragile), or a Teams app: participant
  join/leave real-time events plus `GetParticipant`, with RSC scopes in the manifest and the event switched on
  in the Developer Portal. Difficulty 4/5 for the real thing. Not worth it for this device.
- **Who is speaking:** not *only* a calling bot, as first written here. The Teams app platform has an RSC
  permission `OnlineMeetingActiveSpeaker.Read.Chat` — "read the participants who are sending audio into the
  meetings associated with this chat" — delegated, consented by installing the app in the chat rather than by
  a tenant admin. It does not cover standalone or channel meetings, and nobody seems to have posted working
  code for it. The in-client `meeting.registerSpeakingStateChangeHandler` is easy by comparison but only says
  *someone* is speaking (`isSpeakingDetected`), never who. Either way it needs a Teams app, so difficulty
  stays 4-5/5 for a desk device. Still not worth it, but the route exists.
- **Mute:** the OS microphone mute (shipped) is what hardware mute buttons do now that the Teams API is gone.
  Whether Teams shows "muted by your system" in current builds is untested on real hardware.
  The app platform does **not** change this: `meeting.toggleIncomingClientAudio` only mutes *incoming*
  (speaker) audio, and a developer on Microsoft's own forum reports finding no way to read Teams' mute state
  at all. `updateMicState` appears in the TeamsJS changelog from 2.8.0 but looks tied to apps that have taken
  over audio handling; unverified.

### Why the app platform doesn't rescue a desk device

`apps-in-teams-meetings/meeting-apps-apis` is the **Teams app platform**: code that runs *inside* Teams, as a
side panel, meeting stage or bot. There is no local endpoint. To reach the MK10 Manager the chain would be:
a Teams app package installed in the meeting (custom app upload is often blocked on a work tenant) → a bot
with a public HTTPS endpoint registered in Azure → a relay service you host → the manager polling it. That is
a cloud service you own and operate, to learn things the sound card can't tell you. Worth knowing the route
is real and supported; not worth building for ten keys and a small screen.

## The theme format, read out of the firmware

**A theme is JSON.** This is no longer a guess:

```
ThemeFile::switchPage_s(QJsonObject const&, int)
VideoPlayer::onThemeFileSwitchPage(QJsonObject const&, int)
```

Both come out of the Qt meta-object data in `/data/KeyboardDevice`, which survives stripping. That was the
one question that decided whether authoring a theme was feasible at all, and it fell the right way.

### Structure

Read off the string table in source order. Page level:

| Key | Notes |
|---|---|
| `pages` | the array of pages |
| `pageName`, `parentPageName` | pages form a tree |
| `currentPage`, `main` | `main` looks like the root page's name |
| `items` | the widgets on a page |
| `canvas` | page-level canvas settings |
| `V3.0` | sits with them; almost certainly a format version value |
| `canvas_w`, `canvas_h`, `canvas_rotate`, `canvas_flip`, `device_bl` | device-level settings |
| `pageSwitch`, `pageSwitchMode`, `jumpToPage`, `openPage`, `oneLevelUp` | how a key moves between pages |

### Widgets, with the properties each one reads

The factories are `QGraphics…Make`, and each one's property names sit immediately before its typeinfo in
`.rodata`, which gives the grouping below. Common to the items: `scale`, `rotate`.

| Factory | Properties |
|---|---|
| `QGraphicsPixmapItemMake` | `pixmap`, `OriginPoint_ax`, `OriginPoint_ay`, `canvasRotationAngle`, `scaledWidthTo`, `scaledHeightTo` |
| `QGraphicsPixmapItemClockMake` | as above, plus the clock digit fields (`hour ten figures`, `minute single digits`, …) |
| `QGraphicsRectItemMake` | `front_color` |
| `QGraphicsTextItemMake` | `text_str`, `text_family`, `text_size`, `text_IsBold`, `text_customFont_flag`, `text_customFont_path`, `system_data_flag`, `text_font`, `shadeSize`, `shadeColor` |
| `QGraphicsWidgetAudioType1Make` | `SoundColumnHeight`, `SoundSolidLineColor`, `SoundDashedColor`, `radius`, `DisplayDirection`, `Clockwise`, `SoundColumnWidth`, `SoundColumnWidthInterval`, `SoundRoundColor`, `RoundWidth`, `screen_w`, `screen_h` |
| `QGraphicsWidgetAudioRectType1Make` | `SoundColumnStartColor`, `SoundColumnEndColor`, `rectWidthCount`, `rectWidth`, `rectWidthInterval`, `rectHeightCount`, `rectHeight`, `rectHeightInterval` |
| `QGraphicsWidgetBarCircleMake` | `margin`, `back_color` |
| `QGraphicsWidgetBarCircleType1Make` | `system_data_min_value`, `system_data_max_value`, `angleMinValue`, `angleMaxValue`, `arcRadius`, `arcCircularInterval`, `gradientColor1..3` |
| `QGraphicsWidgetBarRectMake` | `corner_radius`, `lineargradient_flag`, `lineargradient_color` |
| `QGraphicsWidgetBarRectParagraphsMake` | `border_width`, `border_color` |
| `QGraphicsWidgetChartLineSeriesMake` | `series_color`, `grid_line_color`, `area_color`, `grid_line_flag`, `area_flag` |

### Value formats

- **Colours are strings**, not numbers: `r=%d,g=%d,b=%d,a=%d`. Literals in the binary include
  `r=0,g=255,b=0,a=255` and `r=255,g=0,b=0,a=255`.
- **Fonts are `QFont::toString()`**: `Microsoft YaHei,72,-1,5,50,0,0,0,0,0`. Qt 5.12's ten fields —
  family, point size, pixel size, style hint, weight, style, underline, strikeout, fixed pitch, raw mode.
- Images: `.jpg`, `.png`, `.bmp`, `.gif`; animations use `frame_%1.png` with `frameDelays`, `delayMs`,
  `loopCount`. Video: `.mp4` through a separate `videoplayerinterfaceAPP` process.
- Live values come through `system_data_flag` / `system_data_key` / `system_data_name` / `showUnit`, with
  min/max as `system_data_min_value` and `system_data_max_value`.

### What is still missing

**Per-item geometry.** No `x`/`y`/`width`/`height` keys turn up in the item band. Position is the one thing
we would still be guessing at. (`pageData` is in fact present in every build checked — I had said it was
V2.32-only, which was wrong. `item_offset` really is absent from the 2022 builds.)

### Two corrections to the earlier notes

- **`layer` is not a theme key.** It sits in the QMK block next to `dynamic_keymap_set_keycode`, `column` and
  `keycode` — it is a keymap layer. The old table had it under Structure.
- **`.Theme` as an extension appears nowhere in the 2022 firmware**, and its folders are
  `/data/theme/<width>x<height>/`. I took that to mean the older note about
  `/data/theme/MK10/<folder>/<name>.Theme` was wrong. It is the other way round: that note came from the
  **device's own `config.json`**, which is authoritative for the firmware it actually runs, and the 2022
  image is three years older. The convention changed between builds. Use the `MK10/` form.

### What the binary would not give up

I tried to read the parser itself and got nowhere worth reporting. The code is ARM (A32, ~484k instructions),
and none of these strings is referenced by an absolute address anywhere in the file — not even `getInfo`,
which is certainly used. They are not Qt `QByteArrayData` literals either; a scan found 446 of those headers
and none of them is ours. So the addresses are formed some other way, and without a real analysis tool
(Ghidra, IDA, radare2 — none installed, and capstone alone is not enough) chasing it further is not a good
use of time. It also matters less than it looks: this binary is **April 2022** and the device runs **V2.32**,
so even a perfect read here might not match the firmware on the desk.

## What the firmware says a theme is made of

From `mk10probe.py theme-strings` on `KeyboardDevice` V2.32 (pulled out of ScreenKey v1.1). These are class
and symbol names, so they are what the program *has*, not yet proof of what a `.Theme` file *says*.

**Widgets** — the catalogue a theme can draw from:

| Group | Classes |
|---|---|
| Clocks | `FlipClock`, `FlipClocks`, plus `QGraphicsPixmapItemClockMake` (the dial-and-hands clock, with `OriginPoint_ax` / `OriginPoint_ay` pivots, `rotate`, `scale`) |
| Progress | `RectProgressBar`, `CircularProgressBar`, `CircularProgressBarType1`, `CircularProgressBarType2`, `RectParagraphsProgressBar`, `CircleParagraphsProgressBar` |
| Text | `CustomTextWidget`, `ShadowTextWidget`, `MultipleLinesTextWidget` |
| Media | `AudioPlayer`, `VideoPlayer`, `UVideoPlayer`, `CustomImageshow` |
| Charts | `ChartLineSeries` |
| Plumbing | `ThemeFile`, `ThemeInfo_s`, `CustomWidgetInterface`, `controlItemWidget` |

Each widget has a matching factory: `QGraphicsPixmapItemMake`, `QGraphicsWidgetBarRectMake`,
`QGraphicsWidgetBarCircleMake`, `QGraphicsWidgetAudioType1Make`, `QGraphicsWidgetChartLineSeriesMake` and so
on, alongside `QGraphicsMake`, `QGraphicsDel` and `QGraphicsMake_in_map`. A static called `itemDescription`
sits with them and is the obvious candidate for the name → constructor table a parser would need.

**Two details worth keeping:**

- `getItemObject<CustomWidgetInterface>(const QMap<int, CustomWidgetInterface*>&, int)` — items are held
  against an **int**, not a name. So a theme probably numbers its items, and `SEND_PIXMAP`'s string keys may
  be filenames rather than item ids.
- `VideoPlayer::updateDynamicImage()`, beside `pixmapToByteArray(QPixmap)`. `VideoPlayer` is the main display
  class, not just a video widget — it also carries `keyStateChanged`, `updateKeyConfig`, `set_CanvasFlip` and
  `xiaozhiAICMDUpdate`. `updateDynamicImage` is the most likely place a host-sent picture enters a running
  theme, which is exactly what SEND_PIXMAP would need.

Human-readable labels in the same area: "Analog clock", "Digital clocks", "Progress bar", "Clockwise".
Paths: `/theme`, `/fonts`, `/data/KeyboardInfo`, `/data/DeviceUpgradeFirmware`.

### The theme file's actual keys

With the mangled C++ symbols filtered out, what's left reads like the vocabulary of the file itself:

| Area | Keys |
|---|---|
| Structure | `theme`, `defaultTheme`, `pages`, `pageData`, `pageName`, `pageSwitch`, `pageSwitchMode`, `items`, `item_offset`, `layer` |
| Placement | `pixmap`, `rotate`, `scale`, `scaleType`, `scaledWidthTo`, `scaledHeightTo`, `canvas_rotate`, `OriginPoint_ax`, `OriginPoint_ay`, `isClockwise` |
| Colour | `area_color`, `back_color`, `front_color`, `border_color`, `grid_line_color`, `series_color`, `lineargradient_color`, `m_color` |
| Text | `text_font`, `fontMapping` |
| Progress | `progress`, `progressValue`, `progressText` |
| Media | `player` |

Signals around it: `onThemeFileSwitchPage`, `themeFileLoadFinish`, `themePageSwitch`.

**`device_state_send_pixmap_to_device`** sits beside `device_state_theme`, `device_state_delete_themes` and
`device_proactive_theme_load_finish`. So SEND_PIXMAP is a step in ScreenKey's own theme workflow — which
settles why it acknowledged and did nothing on a device with no theme, and keeps alive the idea that with a
theme loaded it is how pictures get *into* one.

More classes worth knowing about: `ScreenCapture`, `FrameAnimator`, `AnimationPoint`, `CountdownTimer`,
`MultimediaViewer`, `QPixmapStore`, `FileSplitter`, `NetworkManager`, `AIManager`, `OperatingSystemControl`,
and a set of audio visualisers — `audioGuiRoundArc`, `audioGuiRoundPoint`, `audioGuiRoundRect`,
`audioGuiRectType1`, `audioGuiRoundType1`. Also `STM32CommunicationThread`, though `hardware.md` records the
keyboard chip as a GD32.

### The stock theme, found (October 2026)

**It was on the SD card all along, on partition 6.** `/upper/data/theme/MK10/默认主题-微雪-20250926/默认主题-微雪.Theme`,
13.2 MB. DiskInternals showed that partition as empty because its ext4 journal **needed recovery** — a dirty
journal that read-only tools won't replay. `e2fsck` on a copy of the image replayed it and the whole overlay
appeared: 517 files, ~148 MB.

The format is in `docs/theme-format.md` and `mk10probe.py theme-read` parses it. In short: a Qt QDataStream
container holding a `QMap<QString,QVariant>` header, a qint64-prefixed blob of **UTF-8 JSON**, and a
`QMap<QString,QByteArray>` of 406 embedded assets.

It settles the things we could not get at from the binary:

- **Item geometry is `x`, `y`, `z`** — the keys I searched for and could not find, because they are short
  and common and I was looking in the wrong string band.
- **`type` is a number as a string**, `100`–`115`, which is why items are held in a `QMap<int, …>`.
- **Every JSON value is a string**, numbers and booleans included.
- **A key is just an item**, `type` `115`, with `row`, `col` and a base64 `controlData` holding its action.

### A theme can show anything the host names (October 2026)

The decisive test for using themes at all: a copy of the stock theme with one value rebound from `Gpu使用率`
to the made-up name `mk10test`, installed for one boot by `lunch-tests.sh` (then called `lunch-theme-test.sh`) without the card leaving the
device. Pushing `mk10test` = 1, 2, 3 over V1 cmd 1 made that number count on the screen, beside the
untouched CPU control. The device never asked for any name first. Details in `docs/theme-format.md`.

So the theme route can carry the manager's own state, and the cost we measured — ~85 ms composing and
encoding each frame on the PC, ~29 ms decoding it on the device — becomes a few bytes per changed value.

### Every stock widget type follows live data (October 2026)

Pushing every name the stock theme uses and photographing its PC Perf and Clock pages showed text, flip
digits, circular bars, rectangular bars and arc gauges all following the values — the arc's moving part is a
small dot, which is why it looked dead on the start page. With custom names proven for text, a theme can
carry the manager's meters as real bars, not just numbers.

What is still open for building the manager on themes: whether the **host can switch pages** (needed for
context rules), state images driven by pushed values (mute icons), pictures that change at run time (album
art), and whether a theme generated from scratch loads.

The sections below were written before the file was found. They were right that the format is JSON and
mostly right about the vocabulary; they are kept because the reasoning is still how we would approach the
next unknown.

### Why the stock theme can't be had (superseded — see above)

`theme-dig` over the whole 355 MB installer: the widget names are in there (they are in the firmware's symbol
table — `FlipClock` 56 times, `CircularProgressBar` 94), but **no JSON object parses around any of them**, and
the Chinese name of the theme the device is actually running — 默认主题-微雪 — does not appear **even once**.

If the installer shipped that theme, its name would be in it. It isn't. The device's theme was put there at
the factory, the firmware contains no URLs to fetch one from, and there is no read-file method in the
protocol. So the stock theme cannot be read by any route available to us.

**Which leaves authoring one from the table above.** Difficulty 4/5, and the first attempt will be wrong in
ways only the device can reveal — with `DELETE_THEME` (V1 cmd 11) as the way back.

## The firmware image: MK10_V1.0.img

Waveshare's OpenSource package carries `MK10_V1.0.7z` (257 MB) holding `MK10_V1.0.img` (848 MB). It is an
**Allwinner `IMAGEWTY`** firmware image — so the MK10 is an Allwinner SoC running Tina Linux (OpenWrt), not
the bare-metal thing the serial protocol suggests. `probe/awimage.py` unpacks it; the index sits at 0x400 in
1024-byte entries, each `filename_len / header_size / maintype[8] / subtype[16] / pad / filename[256]`, then
u64 `stored_length`, `original_length`, `offset`.

36 files, of which the ones that matter are `rootfs.fex` (838 MB, plain **ext4**, readable with `debugfs`
without mounting) and `boot.fex` (Android bootimg).

What it settled:

- **`/data/KeyboardDevice` is stripped.** No `.symtab`, only `.dynsym`. So the demangled signature
  `theme-strings` found came from RTTI or Qt meta-object data, not a symbol table, and reading the theme
  parser means working from vtables. I had called that 60/40 the other way; it was the 40.
- **`/data/theme/` exists and is empty**, with one folder per screen resolution (`480x480`, `800x480`,
  `1024x600` …, none of them 896x304). Themes really are provisioned after the factory, so this image has no
  stock theme either. That part of the earlier conclusion holds.
- **The device is Linux with a shell, busybox, procd, and `adbd` installed** (stopped and disabled at boot).

### What the device actually is

| | |
|---|---|
| SoC | Allwinner **T113** (the build tree is `../Tina-t113-pro/...`) |
| OS | **Tina Linux** — OpenWrt with procd, busybox, `/etc/init.d` |
| UI | Qt **5.12.9** on the **linuxfb** platform plugin, straight to `/dev/fb0`. Qt Charts is installed |
| App | `/data/KeyboardDevice`, source project **`PCMonitorLinux2`** |
| Root | ext4, **overlay-mounted**: `/overlay` comes from the UDISK partition, `/rom` is the read-only base |
| Partitions | `boot-resource`, `env`, `env-redund`, `boot`, `rootfs`; UDISK is the remainder |
| Tools on board | `busybox`, `adbd`, `nc`, `wget`, `curl`, `tcpdump`, `ntfs-3g`. **No** telnetd, dropbear or ssh |
| USB gadget | configfs. `KeyboardDevice` builds the serial port we talk to with `/data/setusbconfig serial`; the same script also offers `uac1`, `hid`, `uac1,hid`, `rndis`, `mtp`, `adb` and more, each *instead of* serial. (Corrected: this used to say `/data/serial_adb.sh`, which nothing runs.) See [call-control.md](call-control.md) |

Two consequences worth holding on to:

- **`/data` is on the writable overlay.** Anything `saveToFile` puts there persists, and deleting it puts the
  device back. That is a much better safety story than it looked: a bad theme is a file, not a brick.
- **`rndis` is in `setusbconfig`.** USB ethernet to the device is one line in `lunch.sh`, which with `nc`
  already on board is a shell by another route if `adb` turns out to clash with the serial gadget.

### Reading the card: the running software is in the overlay

The card's **partition 5 (`rootfs`)** `/data` turns out to be the pristine factory base — `theme/`, `image/`,
`video/`, `fonts/`, `info/` and `audio/` all present and **all empty**, exactly matching the 2022 firmware
image, with a `KeyboardDevice` that reports **V2.29**.

The version ladder makes the structure obvious:

| Copy | Version |
|---|---|
| Card's `rootfs` partition (p5) | **V2.29** |
| Published `MK10_V1.0.img` | **V2.31** |
| What the device actually reports | **V2.32** |

So the binary the device runs is in neither: it is on **partition 6 (`UDISK`)**, the overlay. OpenWrt's
overlayfs merges `UDISK`'s upper directory over the read-only rootfs, so everything ScreenKey ever installed
— the firmware update, the theme, fonts, images — lives there and shadows the empty factory copies. Reading
p5 shows the device as it left the factory; reading p6 shows what it has become. **p6 is the one that
matters.**

This also finally explains `.Theme`. The string appears in no build because the path is **data, not code**:
`config.json` supplies `defaultTheme` as a complete path and the firmware just opens it. Neither my original
note nor my correction of it was right — there was nothing to correct.

### Pixel format, confirmed by arithmetic

The factory `/data` carries raw framebuffer dumps whose sizes settle the format exactly:

| File | Bytes | Works out as |
|---|---|---|
| `128x128.bin` | 32,768 | 128 × 128 × **2** — RGB565, one key |
| `240x296.bin` | 142,080 | 240 × 296 × **2** — RGB565, the small screen |
| `720x1280.bin` | 3,686,400 | 720 × 1280 × **4** — ARGB8888, a different device in the family |

RGB565 for this panel, which matches the `ARGB8888_TO_RGB565_Thread` class in the binary and is what an
on-device renderer would write to `/dev/fb0`.

### Running our own code on the device

Worth separating from theme mode, because it needs none of it.

**The keyboard is not the Linux side.** `4250:426F` is a separate USB device behind the internal hub
(`05E3:0610`) and types on its own. Replacing or killing `/data/KeyboardDevice` does **not** stop the keys
working as a keyboard. What it costs is the LCD and the key-press *notifications* the SoC forwards to the
host — the SoC learns presses over an internal UART (`/dev/ttyS1`, `STM32CommunicationObject`) and would
have to be re-read for our own actions.

**Building for it is a solved problem.** The device is glibc **2.23** (2016), far older than anything a
modern toolchain targets, so link statically and the version stops mattering:

```
apt-get install gcc-arm-linux-gnueabihf libc6-dev-armhf-cross
arm-linux-gnueabihf-gcc -O2 -static -o app app.c
```

That produces `ELF 32-bit LSB executable, ARM, EABI5, statically linked, for GNU/Linux 3.2.0` — a ~450 KB
binary for a small program, which the T113's kernel will run. Verified by building one, not assumed.

**Why it is interesting.** The 27–31 ms per frame is the device decoding a whole-canvas JPEG. Code running
on the device would take state from the host (a few bytes) and draw locally, which removes that cost
entirely rather than optimising it. The paths are all known from the image: `/dev/fb0` for the panel,
`/sys/kernel/debug/dispdbg/{name,command,param,start}` with `lcd0`/`setbl` for backlight, `tinymix` for
volume, `tinyplay` for sound.

**The reversible way to try it.** `qt_app1` runs `lunch.sh` before `qt_app2` starts the app, so a card with

```sh
( sleep 15; /etc/init.d/qt_app2 stop; /mnt/SDCARD/ourapp ) &
```

takes the screen over after boot and changes nothing on the device. Pull the card and it is stock again.
Using `overlayDir/` instead writes into the root filesystem and persists, which is the version to avoid
until the above has been tried.

### Recovery, if it comes to that

`MK10_V1.0.img`'s device tree has `model = "MK10"` at the root, so it is a genuine device-specific image for
this hardware, not a generic family one. With `cardtool.fex` / `cardscript.fex` (PhoenixCard makes a
bootable SD that reflashes), LiveSuit/PhoenixSuit over USB, and Allwinner FEL mode as the last resort, a
device with a wrecked overlay can be put back. Unknowns: how FEL is entered on this board, and that
reflashing returns the 2022 firmware rather than the V2.32 the device shipped with.

### The SD card is a root shell, by Waveshare's design

`/etc/init.d/qt_app1` runs **`/mnt/SDCARD/lunch.sh` as root** at every boot, before the UI starts, and copies
anything in **`/mnt/SDCARD/overlayDir/`** over the root filesystem first. Both are theirs, not a bug we found.

This changes the whole investigation. Everything we wanted from the device — the installed theme, `/data`'s
real contents, whatever `magic.bin` is — is a file copy away, with no writes to the device over the serial
protocol and no way to brick it: pull the card and the hook is gone. `sdcard-tools/lunch.sh` in this repo is a
read-only dump that proves the hook exists and takes the theme off the device.

It also means a theme can be *installed* by copying it into `/data/theme/`, without `saveToFile` at all.

**Confirmed on real hardware, October 2026.** `mk10dump/when.txt` appeared. The hook is live on the shipping
firmware, not just the 2022 image, and it reported:

```
Thu Jan  1 08:00:12 CST 1970
Linux TinaLinux 5.4.61 #216 SMP PREEMPT Tue Sep 23 05:38:52 UTC 2025 armv7l
root=/dev/mmcblk0p5 init=/sbin/init
partitions=boot-resource@mmcblk0p1:env@mmcblk0p2:env-redund@mmcblk0p3:boot@mmcblk0p4:rootfs@mmcblk0p5:UDISK@mmcblk0p6
androidboot.hardware=sun8iw20p1  androidboot.dramsize=128  console=
uboot_message=2018.05-g7e8280b(08/15/2025-10:00:55)
model: MK10
```

Four things worth keeping from that:

- **The firmware is current**: kernel built 23 September 2025, u-boot 15 August 2025. The hook survived three
  years of updates, which makes it a deliberate feature rather than a leftover.
- **`sun8iw20p1`** confirms the T113/R528 family, and **128 MB of RAM** — roomy for a framebuffer program,
  tight for anything that wants its own Qt.
- **UDISK is `mmcblk0p6`**, the writable overlay holding `/data`, exactly as the 2022 image's fstab implied.
- **The clock starts at 1 January 1970** and the timezone is CST. There is no working RTC at boot, so the
  device cannot know the time until something tells it. Anything drawing a clock on-device needs the host to
  set it first.

### Waveshare's own host code changes the picture

`OpenSourceLicenseDemo_V1.0` is a small Qt host application Waveshare publish, and it calls every JSON method
with real parameters. `protocol.md` now records all of them from that source rather than from guesswork. Three
things follow that matter beyond the table:

- **There is a V2 upload path.** `saveToFile` takes 64 KB base64 chunks at a `seek` offset and the device
  acknowledges each one; `setFileCRC` commits. That is almost certainly how ScreenKey installs a theme, and it
  is nothing to do with V1 FILE_START/FILE_END, which is what we had rehearsed.
- **`cmd 100` is acknowledged, and the official loop is driven by that acknowledgement** — send a frame, wait
  for the device to say it drew it, send the next. The device sets the pace. The manager now does this, and
  the acknowledgement turns out to be a `cmd 100` carrying the picture's own seq.

  What that measurement says, on a real MK10 over USB: **27–31 ms per picture**, so the device can take
  roughly 32–37 a second. At 30 updates a second asked for, the manager achieved 8–9. The device is therefore
  **not** the bottleneck any more; the remaining ~85 ms a frame is ours, composing the canvas with QPainter
  and encoding the JPEG on the Qt main thread. That is now the thing worth measuring, and it is a separate
  problem from the protocol.
- **`getFilesBySuffix` returns objects**, `{filePath, crc}`, and the demo asks for `"wav"` **without a dot**,
  listing results as things to hand to `playAudio`. So its search root is probably the sound or upload folder,
  not anywhere a theme lives — which fits every empty answer we got.

That makes the intended method legible at last: upload files with `saveToFile`/`setFileCRC`, point the SD
card's `config.json` at the theme, and let the device render it while the host feeds pictures in. Every step
is now a known call rather than a guess.

### The V2 upload works, and the file listing was our own bug

`mk10probe.py upload` put a 1671-byte JPEG on a real MK10 (V2.32) at `/data/mk10probe/OIP.jpg`. `setFileCRC`
accepted it and echoed `{"crc": ..., "filePath": ...}`. No folder had to be created first — though the V1
rehearsal has written there before, so this does not prove `saveToFile` would create one.

Then the interesting part. Immediately after the upload, asking with **`["jpg", ".jpg"]`** listed the file:

```
getFilesBySuffix {"suffixs":[".jpg","jpg"]}  ->  /data/mk10probe/OIP.jpg   crc 2006517171
```

A separate run moments later, asking with **`[".jpg"]`** alone, came back empty.

Every sweep we have ever run used a leading dot, and every one came back empty. Waveshare's own demo passes
`"wav"` with no dot. The obvious reading is that **the dot was never accepted and the empty answers were our
mistake, not the device being coy**. `files` now tests both forms against each other before sweeping, and the
sweep lists are bare.

Confirmed from a fresh connection, so it is not a per-session cache: `jpg` found it, `.jpg` found nothing.

### What `getFilesBySuffix` actually lists

The same sweep answered the bigger question, and the answer is not the hoped-for one.

| Asked | Got |
|---|---|
| `["jpg"]` | our uploaded `/data/mk10probe/OIP.jpg` |
| `[".jpg"]` | nothing — the dot is never stripped |
| `[""]` | nothing — an empty string is a suffix that matches no file |
| `[]` | **our uploaded file, and only that** |

An empty list means *no filter*, so that last row is the device showing everything it is willing to show.
It is one file: the one we uploaded. Not `/data/KeyboardDevice`, which certainly exists. Not
`/data/mk10probe/test-ok.txt` from the V1 rehearsal, which was written through FILE_START/FILE_END rather
than `saveToFile`. And `/data/mk10probe/` is a folder name **we invented**, so the firmware cannot be
special-casing it.

So `getFilesBySuffix` is **a registry of what the host has uploaded through `saveToFile`/`setFileCRC`**, not
a view of the filesystem. The `crc` it reports back is the one we declared in `setFileCRC` — the device is
repeating what it was told, not checksumming a file it found. That is exactly the API an installer wants:
*have I already sent this, and does it still match?* so it can skip files that are already there.

Two consequences:

- **It will never show us the stock theme, or anything else the device shipped with.** Every empty answer we
  collected was correct and meant "you have uploaded nothing", on top of the dot bug. Enumerating the device
  to find the theme is a dead end, and this closes it rather than opening it.
- **It is the right tool for the other direction.** Put a theme there and this is how we confirm it landed.

That test has been run. A second upload to `/data/mk10probe2/marker.jpg` — a folder that did not exist —
is listed beside the first, and the sweep from a fresh connection lists both. It is a registry of host
uploads. Settled.

### `saveToFile` creates the folders it needs

`/data/mk10probe2/` was never created by anything. The upload made it, and the file inside it is real enough
that the device lists it afterwards with the CRC we declared. Nothing had to make the directory first.

That matters more than it sounds. Themes live at `/data/theme/MK10/<folder>/<name>.Theme` and no theme is
installed, so those folders probably do not exist on this unit. We can write there anyway.

Those three answers are what stand between us and trying a theme.

### A new lead: the device does UDP

`handle_udp_connection` is in there, and the board has Wi-Fi the firmware configures. If the device listens on
the network, that is a second control path with none of the serial port's one-program-at-a-time problem.
Entirely unexplored; nothing here has looked for an open port.

## Status (October 2026)

| Tool | State |
|---|---|
| `probe/mk10probe.py` | Used on the real device (probe, storage, show-test). The device went from V2.29 to V2.32, most likely through ScreenKey; the `update` command hasn't been used on real hardware |
| `opendeck-plugin/` | Works on the real device on Windows (keys, brightness, clock on the small screen) |
| `manager/` | First version. Tested only against a fake MK10 on Linux; not yet on real hardware or Windows |
