# MK10 Manager

A desktop app for the Waveshare MK10. You set up the 10 keys and the small screen in a window, and it runs them.
Works on Windows and Linux. Unofficial: not made by Waveshare.

Needs MK10 firmware **V2.32 or newer** (the version ScreenKey installs).

## Start it

**Only one program can use the MK10 at a time.** Quit OpenDeck (or remove the MK10 plugin) and ScreenKey first.

### Windows

1. Install Python 3.10 or newer from python.org. Tick **Add python.exe to PATH** during install.
2. Double-click `run-windows.bat`.
   The first run sets things up (needs internet, about a minute, about 100 MB).
3. After that, double-click `run-windows.bat` to start it. (`launch.pyw` works too once setup is done.)
   If something goes wrong at start-up, a message box says why.

### Linux

1. Install the device rule once, then unplug and replug the MK10:
   ```
   sudo cp packaging/60-mk10-manager.rules /etc/udev/rules.d/
   sudo udevadm control --reload-rules && sudo udevadm trigger
   ```
2. Run `./run-linux.sh`. The first run sets things up (needs internet).

If it won't start and mentions "xcb", install `libxcb-cursor0` (Debian, Ubuntu) or `xcb-util-cursor` (Fedora, Arch).

## Using it

- **Click a key** in the picture to edit it on the right: label, colours, icon, and what it does.
  A key can do several steps in order.
- **Click the small screen** to choose what it shows: analogue clock, digital clock, text, the sound
  devices in use, what's playing, a picture, or a plain colour.
- **Drag** one key onto another to swap them. **Drop a picture** from your files onto a key.
  **Right-click** a key to copy, paste, clear or test it.
- **Pages** are at the top. Each page has its own keys and its own small screen.
  Right-click a page to rename, duplicate, delete it, or make it the default.
- **New page** offers ready-made templates as well as a blank one: a Sound and microphone page, and a Slack
  page and a Microsoft Teams page while those integrations are on. You see exactly what you'd get before you
  add it, and a template can offer to add its own context rule (the Slack one can switch to itself whenever
  you're in a huddle).
- **Integrations** (bottom left) switch the app-specific extras on and off: Slack and Microsoft Teams. What the
  system itself provides (sound, what's playing, the app in front) isn't an integration and is always there.
  One that's off adds nothing to the window: its key steps, conditions, small-screen faces, templates,
  placeholders and settings are hidden, and it reads nothing in the background. Keys, rules and screens that already use it are kept
  as they are, marked "off" in the editors, and work again when it's switched back on. A new setup starts
  with all of them off; settings from before keep on the ones your pages use.
- **Context rules** (bottom left) switch pages for you.
  For example: when firefox is active, show the Browser page. Rules higher in the list win.
- **Variables** are named values. A key can set one; the screen can show it with `{var:name}`;
  a rule can switch pages when it changes.
- **Keys can change with the context.** Under *Changes with context* a key gets states: while the
  microphone is muted, while an app is in front, while a variable is set, between two times. The first
  state that fits repaints the key's label, colours and icon. A mute key set up this way shows whether
  sound is *actually* muted, however it was muted.
- **Background meter.** A key can show a level behind its icon: how loud the speakers or microphone are
  right now, a volume, or a variable from 0 to 100. The two sample keys do this.
- Changes reach the MK10 straight away and are saved automatically. **Ctrl+Z** undoes.
- Closing the window keeps it running in the tray, so the keys keep working.
  Quit from the tray icon's menu.

### Things a key can do

Keyboard shortcut, type text, media keys, mute or change the volume of the speakers or the microphone,
Slack (status, away, snooze, post a message, jump to a channel) and Microsoft Teams (mute, camera, sharing)
when those integrations are on, open a website or file, run a command,
send a web request (Home Assistant and similar), change page, set a variable, show a message on the small
screen, change brightness, wait.

### Text placeholders

Use these in labels and on the small screen:
`{time}` `{seconds}` `{date}` `{day}` `{month}` `{year}` `{app}` `{title}` `{page}` `{var:name}`
and any strftime format, like `{time:%H:%M:%S}` or `{date:%d/%m}`.

For sound: `{out_device}` `{in_device}` (the speakers and microphone in use), `{volume}` `{mic_volume}`,
`{level}` `{mic_level}` (how loud they are right now, 0-100), `{mute}` `{mic_mute}` (the word "muted", or
nothing), and `{mute:Muted|Live}` for your own two words.

For Slack: `{slack_huddle}` `{slack_dnd}` `{slack_unread}` `{slack_mentions}` `{slack_presence}`
`{slack_status}` `{slack_team}` `{slack_user}`, and `{slack_huddle:Busy|Free}` for your own two words.

For what's playing: `{track}` `{artist}` `{album}` `{player}` `{track_time}` `{track_length}`.

For Teams, during a call: `{teams_call}`, `{teams_mic}` (muted / live), `{teams_camera}` (on / off),
`{teams_sharing}`, `{teams_hand}`, `{teams_meeting}`, `{teams_time}` (how long it has run) and `{teams_shared}`
(what Teams' sharing bar says you're sharing); any of the
on/off ones take your own two words, like `{teams_mic:Muted|Live}`. `{in_device:short}` and
`{out_device:short}` give just the part of a device's name in brackets, e.g. "Jabra Evolve2 65".

A Text face can also run a command every few seconds and show its output with `{output}`.

## Microsoft Teams

The **Microsoft Teams** template on New page gives one key each for the **microphone**, the **speakers** and the
**camera**, and a **share** key. The small screen shows the whole call: the meeting's name, how long it has run
(Teams' own clock), and the microphone, speakers, camera, sharing and raised hand. Orange is muted (or sharing, or
hand up); blue is live or on.

Everything shown comes from what Teams itself displays. When Teams doesn't show something, the keys don't
guess. That's why no key names a device: during a call Teams doesn't show which microphone, speakers or camera
it's using.

- **One microphone key.** In a call it's Teams' own mute; outside a call it mutes the PC microphone.
- **The PC microphone follows Teams** during a call, and Teams follows the PC: mute in Teams' window and the
  microphone itself mutes; mute with a keyboard's mic key and Teams mutes. When a call starts the PC takes
  Teams' state, because that's what people see. If the two still disagree after a follow, the live side is
  muted, never the muted side unmuted. Switch it off on the **Integrations** tab.
- The PC side of that, and the **speakers key**, use Windows' default devices. Teams may be using others.
  Plugging in a headset mid-call (a new default microphone) never unmutes you.
- **The camera key** turns your Teams camera on and off, in a call only.
- **The share key** shows you're sharing, with Teams' own words when its sharing bar has some ("Sharing your
  screen"), and stops it: a screen, a window, a whiteboard or an Excel sheet (those get a separate Stop sharing
  button in Teams). When you aren't sharing it opens Teams' share picker and brings the call window forward.
- Every Teams command reads first, presses Teams' own button only if it's needed, and reads again to check it
  changed. Outside a call, or when the state can't be read, it does nothing rather than guess. Single actions
  are there too for your own keys: mute me, unmute me, camera on, camera off, stop sharing.

How: Microsoft retired Teams' local API for hardware in 2026, so this reads the Teams window the way a screen
reader does (Windows UI Automation): the in-call mic button and its name, "Mute mic" or "Unmute mic". It presses
that same button, so Teams doesn't need focus and no shortcut is sent. The approach comes from
[shuush](https://github.com/supermem613/shuush) (MIT), which worked out that contract against the new Teams.

- **On Linux** it uses [teams-for-linux](https://github.com/IsmaelMartinez/teams-for-linux), a maintained Teams
  client with its own control interface, so keeping up with Teams is its job. Switch that interface on in its
  `config.json` with `"dbusControl": {"enabled": true}` and restart it. It reports in a call, muted, camera and
  screen sharing, and takes mute, unmute, camera and raise-hand commands. It doesn't report the meeting's name,
  the call time or what's shared, and can't stop sharing, so those stay blank there.
  - teams-for-linux judges "muted" from the call's sound, not Teams' button: a quiet microphone with its own
    noise gate can read as muted, and so does a microphone muted on the PC. That's why the PC microphone isn't
    kept in step with Teams on Linux.
  - `./run-linux.sh teams` prints what it reports.
  - Teams in a plain browser isn't read.
- **English Teams only**, for now: the state comes from the button's name. In another language the keys show
  "in a call" but not muted or live, and *Mute me* / *Unmute me* refuse to press.
- If a Teams update renames the buttons, the keys stop changing. Double-click **`teams-windows.bat`** (or run
  `run-windows.bat teams`) **during a call**: it lists what Teams exposes — the call buttons, their names, every
  button in the call window, the call clock, your video tile, the device entries in Teams' settings file and
  the cameras Windows knows — presses nothing, and saves it to
  `teams-report.txt` in the settings folder. It also saves the call window's whole accessibility tree to a
  `teams-tree-<time>.txt` file there. That one holds the names of the people in the meeting.
  **`teams-menu-windows.bat`** does the same after 15 seconds, so you can open a Teams menu first and it's read too.

Switch it on or off on the **Integrations** tab, which also has a line saying what the app sees right now.

## Now playing

The **Now playing** small-screen face shows whatever the system says is playing — Spotify, a browser tab,
VLC — with the album art, title, artist and how far through you are. No per-app setup: it reads the one
place every player reports into.

- **Windows:** the media controls (SMTC), the same source as the volume flyout's now-playing panel. This
  needs three Windows-only packages, which `run-windows.bat` installs for you. Without them everything else
  still works and this screen just stays empty.
- **Linux:** MPRIS through `playerctl`, which the media keys already use.

**Check it before you rely on it.** On Windows, double-click **`nowplaying-windows.bat`** (or run
`run-windows.bat nowplaying`). On Linux, run `./run-linux.sh nowplaying`.

It prints exactly what your machine reports — which packages were found, the title, artist, position and
whether artwork came back — and always saves the same thing to **`nowplaying-report.txt`** in the settings
folder, so the answer isn't lost even when it's started without a console.

Switches for it are on the **Device and app** tab. *Album art* is separate because some players hand over a
web address rather than a file, so fetching it means a small download each time the track changes.

## Slack

Switch on **Slack** on the **Integrations** tab to connect the app to one workspace. Slack has no local
control port, so this uses its Web API:

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**, and pick
   your workspace.
2. Under **OAuth & Permissions**, add the **User Token Scopes** listed in the Slack settings.
3. **Install to Workspace**, copy the **User OAuth Token** (it starts with `xoxp-`), paste it in and
   press **Check**.

Some workspaces need an admin to approve a new app first.

**The token is a password.** It's kept in a file of its own in the settings folder, readable only by you —
never in `config.json`, so it stays out of backups and undo. Press **Check** with the box empty to remove it.

What this gets you:

- Keys and rules that react to **being in a huddle**, **snoozed notifications**, **away**, and **unread
  messages**.
- Keys that **set your status**, **snooze**, **set you away or active**, **post a message** and **jump to a
  channel**.
- A **Slack** small-screen face showing all of that next to the real microphone.

What Slack doesn't allow, whatever we do:

- It can't tell us whether you're muted **inside** a huddle, and can't start, join or leave one. Use a
  microphone mute key — it mutes the microphone itself, so it works in Slack, Teams, Zoom and everything else.
- There's no participant list or "who's speaking" for huddles.
- Counting unread messages across *everything* uses an endpoint Slack doesn't document, so it may stop
  working. Listing the conversations you care about is the supported way.

## Control it from scripts

While the app is running:

```
python launch.pyw set mode=meeting        set a variable
python launch.pyw page Browser            show a page
python launch.pyw message "Build done" 3  show a message for 3 seconds
```

On Windows use the Python inside the app's folder: `.venv\Scripts\python.exe launch.pyw set mode=meeting`.

## Good to know

- **The MK10's keyboard chip types its own keystrokes** when you press a key, on top of what this app does.
  - Linux: the device rule above stops that.
  - Windows: in Device Manager, disable the MK10's keyboard (USB ID 4250:426F). This needs admin.
- **Keyboard shortcuts on Linux** use a helper program:
  - X11: `xdotool`.
  - Wayland: `ydotool` (its service must be running) or `wtype` (Sway, Hyprland).
  - The Device and app tab shows which one it found.
- **Switching pages by app on Linux** depends on your desktop:
  - X11: needs `xprop`.
  - KDE Wayland: needs `kdotool`.
  - Hyprland and Sway: work as they are.
  - GNOME Wayland: needs the "Window Calls" extension (untested).
- **Media keys on Linux** use `playerctl`, plus `wpctl` or `pactl` for volume. `playerctl` is also what
  reads the track on the Now playing screen.
- **Reading and changing the sound** uses Windows Core Audio on Windows, and `pactl` on Linux (install
  `pulseaudio-utils`; it works with PipeWire too). The Device and app tab says which one it found.
- **Live level meters** measure how loud the speakers and microphone are.
  - Windows asks the audio engine, so nothing is recorded.
  - Linux has to open a recording stream (`parec`, also in `pulseaudio-utils`). It only does this while a
    page on screen actually shows a live meter, and stops the moment you switch away. The *Live level
    meters* switch on the Device and app tab turns it off completely.
  - While a live meter is on screen the whole display picture is redrawn and sent to the MK10 several
    times a second. *Updates a second* on the Device and app tab sets how often (10 by default). The
    *Pictures sent* line right above shows the rate you're actually getting and how long it takes to hand
    one picture to the serial port. That time is this PC's side only — the MK10 decodes and draws it
    afterwards, which nothing here can measure. A small number means the PC isn't the limit, so turning
    *Updates a second* up is worth trying.
  - **Your setting is remembered**, so changing the default in a later version won't move it. If meters look
    jerky, raise the number yourself.
- **Your settings, pictures and log** are in the settings folder. The Device and app tab has a button that opens it.
  - Windows: `%APPDATA%\MK10 Manager`
  - Linux: `~/.config/mk10-manager`
- **Second hand.** A clock with a second hand sends the whole display picture once a second.

## Testing

`python -m pytest tests` runs the tests. They use a pretend MK10 (Linux or macOS only).

Icons: [Tabler Icons](https://tabler.io/icons), MIT licence (`mk10manager/resources/TABLER-ICONS-LICENSE.txt`).
