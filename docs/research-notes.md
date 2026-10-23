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
- **Built-in theme clocks.** The firmware has its own analogue, digital, flip and image-based clocks (see
  `protocol.md`). If one can run on the small screen while the PC sends key pictures, we'd get an on-device clock
  that keeps going without the PC. Needs the SD card's `config.json` (theme format) and experiments with V1
  SEND_PIXMAP.
- **`getFilesBySuffix`.** The real device wants a parameter spelled `suffixs`; every other name answers
  `Missing 'suffixs' parameter`. The value's shape is still unknown — `explore` now reads the name out of the
  complaint and retries with a string, a list and a comma-separated list. Read-only, so it is safe to poke.
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

## Status (October 2026)

| Tool | State |
|---|---|
| `probe/mk10probe.py` | Used on the real device (probe, storage, show-test). The device went from V2.29 to V2.32, most likely through ScreenKey; the `update` command hasn't been used on real hardware |
| `opendeck-plugin/` | Works on the real device on Windows (keys, brightness, clock on the small screen) |
| `manager/` | First version. Tested only against a fake MK10 on Linux; not yet on real hardware or Windows |
