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

## Microsoft Teams: what a desk device can and can't get (investigated October 2026, nothing built)

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
| UI Automation scraping (what MuteDeck does now) | mute, camera, in-a-call; roster count with effort | breaks on Teams UI changes, language-dependent | Windows |
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

### Why the stock theme can't be had

`theme-dig` over the whole 355 MB installer: the widget names are in there (they are in the firmware's symbol
table — `FlipClock` 56 times, `CircularProgressBar` 94), but **no JSON object parses around any of them**, and
the Chinese name of the theme the device is actually running — 默认主题-微雪 — does not appear **even once**.

If the installer shipped that theme, its name would be in it. It isn't. The device's theme was put there at
the factory, the firmware contains no URLs to fetch one from, and there is no read-file method in the
protocol. So the stock theme cannot be read by any route available to us.

**Which leaves authoring one from the table above.** Difficulty 4/5, and the first attempt will be wrong in
ways only the device can reveal — with `DELETE_THEME` (V1 cmd 11) as the way back.

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
