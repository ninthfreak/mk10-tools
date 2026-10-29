# mk10-tools: notes for Claude Code

Unofficial software for the Waveshare MK10 (10 LCD keys + a 240×296 small screen, one 896×304 canvas, USB serial).
Read `docs/protocol.md` before touching device code, `docs/theme-format.md` for the on-device theme
format, `docs/call-control.md` for the USB headset test, and `docs/research-notes.md` for why things are the way they are.

## Layout

| Folder | What | Language |
|---|---|---|
| `manager/` | **MK10 Manager**, the main app: Qt desktop editor + tray, pages, context rules, faces | Python 3.10+, PySide6 |
| `opendeck-plugin/` | OpenDeck device plugin (works; secondary now) | Rust |
| `probe/` | `mk10probe.py`: probing, test patterns, file-transfer rehearsal, firmware update | Python |
| `sdcard-tools/` | `lunch.sh`: Waveshare's own firmware runs this off the SD card **as root at boot**. Read `sdcard-tools/README.md`. (`sdcard/` is .gitignored for dumps pulled *off* the card — don't put tooling there) | sh |
| `docs/` | Protocol, hardware, research notes | |

## Running and testing

- **Manager tests:** `cd manager && python -m pytest -q tests`. They use a fake MK10 on a pseudo-terminal, so
  Linux or macOS only. Set `QT_QPA_PLATFORM=offscreen` when there's no display.
- **Manager screenshots:** `python manager/tests/shots.py OUTDIR` opens the real window offscreen against the fake
  device and saves PNGs. Look at them after UI changes.
- **Manager by hand:** `manager/run-windows.bat` or `manager/run-linux.sh` (set up `.venv` on first run).
  - `MK10_PORT` forces a serial port.
  - `MK10_CONFIG_DIR` points at a throwaway settings folder.
  - The log is `mk10-manager.log` in the settings folder: `%APPDATA%\MK10 Manager` or `~/.config/mk10-manager`.
- **Plugin:**
  - `cd opendeck-plugin && cargo test && cargo build --release`.
  - End-to-end: `python tests/fake_opendeck.py target/release/opendeck-mk10` (needs `websockets` and `Pillow`).
- **Probe:** `python probe/mk10probe.py selftest` (no device), `demo` (simulated device), `probe --port COM6`.
  - `explore` (read-only) lists the firmware's JSON methods by asking for one that can't exist, then runs the
    read-only V1 commands. `files` lists **what this host has uploaded**, not the device's own files
    (`getFilesBySuffix` takes `{"suffixs": ["jpg"]}` — an array, yes it is spelled that way, and the suffix
    must be **bare**: a leading dot is never stripped and matches nothing, which is why years of sweeps came
    back empty. `[]` means no filter. The `crc` it returns is the one we declared in `setFileCRC`.) `pixmap` tests whether SEND_PIXMAP
    can redraw part of the display. `theme-strings FILE` is offline and needs no device: it mines a ScreenKey
    installer or the extracted `KeyboardDevice` for the theme format's vocabulary, and `unpack FILE --out DIR`
    lists or extracts archives packed inside the installer, and `theme-dig FILE` hunts for the theme itself as
    plain JSON (the installer keeps some of its payload uncompressed — that is how the firmware is extracted).
  - `theme-read FILE [--keys] [--out DIR]` is offline: it takes a `.Theme` apart — pages, items, every key's
    action, and the embedded assets. **`docs/theme-format.md` is the format**, read off the stock theme.
  - `theme-make-test STOCK OUT [--round 2]` (offline) builds the test themes; `sysdata --test [--round 2]` pushes
    live data to a loaded theme over V1 cmd 1 and reports the rate achieved and any acknowledgements;
    `sysdata --stock` pushes every name the stock theme uses, so its own pages show which widgets follow data.
    `reload [--theme PATH | --stock]` asks the device to load a theme without a reboot (V1 RELOAD), trying the
    likely layouts of its one argument in turn and stopping at the first the device reports as a success. The writer
    (`join_theme`, `theme_json_bytes`) reproduces the stock theme byte for byte — keep it that way.
  - `upload FILE [--dest PATH]` puts a file on the device the V2 way (`saveToFile` in 64 KB base64 chunks, then
    `setFileCRC`), defaulting to `/data/mk10probe/`; `delete PATH...` removes files. **Both write to the device**
    and ask you to type a word first. The probe refuses any write method until the running command unlocks it by
    name, so the read-only commands can't write by accident.
  - `hid-install [--no-audio] [--arm-only]` **writes to the device** (one INSTALL confirmation): it puts the
    call-control test on the card and arms one boot as serial + HID headset + sound card. `hid [--log-only]`
    then talks to it over HID (needs `hidapi`; no serial port, so the Manager can stay open).
    `docs/call-control.md` is the design. Tests: `python -m pytest -q sdcard-tools/hidtest` (needs a C compiler).
    `hid-list` (read-only) lists the PC's HID devices, call-control (Telephony) ones first.
  - On the running MK10, configfs isn't mounted at boot (`adbd` is disabled, an overlay whiteout). Anything
    touching `/sys/kernel/config` must mount it first. Round 1 of the USB test failed on exactly this.
  - The MK10's BusyBox (1.27.2) has no `setsid` or `nohup`. Check device scripts with its own ash:
    `qemu-arm-static -L <device /lib> -0 sh busybox -n script.sh`.
- **Teams:** `manager/teams-windows.bat` (double-click, during a call), `run-windows.bat teams` prints what UI
  Automation sees in Teams (the `microphone-button`, its name, every button in the call window, the call clock,
  the video tiles) and saves `teams-report.txt`, plus the whole tree. `teams-menu-windows.bat` waits 15 s first,
  so a Teams menu can be opened and captured. Read-only. Use it first when the Teams keys stop following Teams.
  On Linux `./run-linux.sh teams` prints what teams-for-linux's D-Bus control reports. Tests:
  `tests/test_teams_linux.py` runs the real `busctl` against `tests/fake_teams_for_linux.py` on a private bus.
- **What's playing:** `manager/nowplaying-windows.bat` (double-click), `run-windows.bat nowplaying` or
  `./run-linux.sh nowplaying` prints what this PC reports (packages found, track, position, artwork) and
  saves it to `nowplaying-report.txt` in the settings folder. Use it rather than guessing when the Now
  playing screen looks wrong.
  - `launch.pyw` hands over to **pythonw.exe** on Windows, which has no console, so anything that prints
    must be listed in its `CONSOLE_COMMANDS` or its output goes nowhere.

## Hardware safety rules

- **The SD card is the device's system disk**, not storage: `boot-resource`, `env`, `boot`, `rootfs` and
  `UDISK` are all partitions of it, and `/data` lives on `UDISK`. Image the card before changing anything on
  it — that image is a complete, restorable firmware backup. Windows mounts only the 32 MB `boot-resource`
  partition, which is also what the device calls `/mnt/SDCARD`; it holds the boot resources and fills fast.
- **Don't make the user pull the SD card.** The MK10's card slot spring may be wearing. Everything on the card
  can be reached without removing it: `saveToFile` (`probe upload`) writes `/mnt/SDCARD` and `/data` directly,
  and `lunch.sh` runs as root before the app starts. Prefer changes that revert themselves on the next boot —
  `sdcard-tools/lunch-tests.sh` is the pattern: arm files for one boot, and for the USB test a bind mount
  plus `/tmp`, which a reboot removes by itself.
- Only one program can open the MK10's serial port. Quit OpenDeck, ScreenKey and MK10 Manager before using another.
- **Ask the user first** before sending anything to a real device that writes or deletes files (`saveToFile`,
  `setFileCRC`, `deleteFiles`, V1 FILE_START/FILE_END outside `/data/mk10probe/`, DELETE_THEME) or updates firmware.
  - A firmware update has no rollback, and no recovery path is known yet.
- Don't probe unknown JSON methods or parameters on the real device without asking. `getInfo` and `setBacklight`
  are known to be safe.
- Never commit a Slack token, or any other credential. The Slack token belongs in the settings folder
  (`slack-token`, owner-only), never in the repository or in `config.json`.
- Never commit device data or vendor files: `magic.bin` (device identity), SD-card dumps, Waveshare's
  `KeyboardDevice` firmware, or the ScreenKey installer. `.gitignore` covers the obvious ones.
- This repository is public.

## Working style the user wants

- They assess risk and tradeoffs themselves. Give accurate feasibility reads, including for hard or dead-end paths.
  Don't steer toward easier options or soften difficulty.
- **Don't infer or divine data.** Show only what can be acquired directly from its source (for Teams: what Teams
  itself displays, or what a client like teams-for-linux reports). If it can't be, say so and discuss possible
  methods with the user before building any. Names must match what the source shows: a device name from Windows
  that Teams doesn't display only confuses.
- **Lean on maintained, pre-built systems** (libraries, apps' own control interfaces such as teams-for-linux's
  D-Bus, shuush's contract) over building and maintaining our own, so there's less for us to keep working.
- These preferences can pull against each other. Weigh all of them and show the tradeoff; don't pick the one
  that suits an option and leave the others out.
- Rate **difficulty (x/5)** and **confidence** explicitly. Present recommendations as recommendations, not decisions.
  Keep uncertainty visible.
- Keep text short and skimmable: short sentences, headers, lists.
- **Anything you want the user to do goes at the very end, as a numbered list under its own header.**
  Never inside or before the explanation, and never split across the message. Findings, reasoning and
  caveats come first; the asks are the last thing they read.
- **Colours: blue and orange for status and highlights, never red against green.** The app's palette is built
  around this.
- The user wants GUIs, not config files, and desktop apps rather than local web pages unless there's a strong reason.
- Targets: Windows now (work PC), Linux at home (the final target).

## Manager architecture, in one breath

`device/` runs serial I/O on one background thread, handles reconnects, and emits Qt signals. It keeps one
picture in flight: the device's acknowledgement of a frame is what releases the next, so the MK10 sets the
rate and `meter_fps` is only a ceiling (firmware that never answers is noticed and falls back to a timer).
`render/` draws the whole canvas with QPainter: keys, then faces from `render/faces.py`, whose option lists drive the
editor forms through `schema.Field`. `render/meters.py` draws level meters behind keys and on the small screen.
`system/audio.py` reads and changes the speakers and microphone (Core Audio through ctypes on Windows, `pactl` and
`parec` on Linux) on its own thread; live levels are only measured while a page on screen shows one.
`conditions.py` holds the one condition evaluator, shared by context rules (which switch pages) and key *states*
(which repaint a key while something is true — that's what makes a mute key show the real mute state).
`integrations.py` is the registry of optional integrations with specific apps (Slack, Teams): which action types,
conditions, faces, templates and placeholders each adds. What the OS itself provides (sound, now playing, the
active window) is never an integration. One that's switched off (`settings["integrations"]`, the
Integrations tab, `controller.set_integration`) is hidden from every editor and its monitor stops; what already
uses it is kept and marked "off". **A new integration gets an entry there**, a monitor with `configure(enabled,
...)` called from the controller's `apply_*_settings`, and its settings widget in `ui/integrations_panel.py`.
`system/slack.py` is the Slack Web API client and its polling monitor; the token lives in its own 0600 file in
the settings folder, never in `config.json`. `system/nowplaying.py` reads whatever the system says is playing
(SMTC through PyWinRT on Windows — optional packages, absent means the feature is simply empty; MPRIS through
`playerctl` on Linux) and caches the album art. `system/teams.py` reads Teams' in-call mic button through UI
Automation (ctypes, vtable slots checked against Wine's IDL; the contract is from shuush) on its own thread, and
does the explicit mute and camera on/off there too: read, press only if needed, read again to confirm.
`system/teams_linux.py` is the Linux backend: teams-for-linux's own D-Bus control through `busctl` (a subprocess,
because QtDBus's blocking call holds the GIL). Its "muted" comes from the call's audio, so it's `SYNC_SAFE = False`
and the PC microphone isn't synced with it. Only Teams' own call clock is shown; without one the time is unknown.
`micsync.py` decides how the PC microphone and Teams' mute follow each other during a call (the controller
carries it out); its rule is that a disagreement is settled by muting the live side, never by unmuting. A new
default microphone restarts it, so plugging in a headset mid-call can never unmute anyone. `templates.py` builds ready-made pages for the New page window.
`controller.py` is the hub. It owns pages, presses, rules (edge-triggered: they act only when the result changes),
variables, debounced saving and undo snapshots (device settings are kept out of undo).
`actions.py` runs key actions on a worker thread; actions that touch the app (page, variable, message, brightness,
open) come back through a signal. The `audio` action goes straight to the audio monitor, which is shared.
`ui/` holds the window. Editors edit the config dicts in place and call `controller.edited()`.
Wrap any dialog in `widgets.dialog_open()` so a page switch can't reload the editor underneath it.
`ui/condition_row.py` is the one condition editor widget, used by both context rules and key states.
`ui/new_page.py` is the New page window: it renders each template with the real renderer so the preview is
exactly what the device would show.

## Commits

Small, focused commits with clear messages. Run the manager tests before committing manager changes.
