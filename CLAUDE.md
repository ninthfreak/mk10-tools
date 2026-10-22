# mk10-tools: notes for Claude Code

Unofficial software for the Waveshare MK10 (10 LCD keys + a 240×296 small screen, one 896×304 canvas, USB serial).
Read `docs/protocol.md` before touching device code, and `docs/research-notes.md` for why things are the way they are.

## Layout

| Folder | What | Language |
|---|---|---|
| `manager/` | **MK10 Manager**, the main app: Qt desktop editor + tray, pages, context rules, faces | Python 3.10+, PySide6 |
| `opendeck-plugin/` | OpenDeck device plugin (works; secondary now) | Rust |
| `probe/` | `mk10probe.py`: probing, test patterns, file-transfer rehearsal, firmware update | Python |
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

## Hardware safety rules

- Only one program can open the MK10's serial port. Quit OpenDeck, ScreenKey and MK10 Manager before using another.
- **Ask the user first** before sending anything to a real device that writes or deletes files (`saveToFile`,
  `setFileCRC`, `deleteFiles`, V1 FILE_START/FILE_END outside `/data/mk10probe/`, DELETE_THEME) or updates firmware.
  - A firmware update has no rollback, and no recovery path is known yet.
- Don't probe unknown JSON methods or parameters on the real device without asking. `getInfo` and `setBacklight`
  are known to be safe.
- Never commit device data or vendor files: `magic.bin` (device identity), SD-card dumps, Waveshare's
  `KeyboardDevice` firmware, or the ScreenKey installer. `.gitignore` covers the obvious ones.
- This repository is public.

## Working style the user wants

- They assess risk and tradeoffs themselves. Give accurate feasibility reads, including for hard or dead-end paths.
  Don't steer toward easier options or soften difficulty.
- Rate **difficulty (x/5)** and **confidence** explicitly. Present recommendations as recommendations, not decisions.
  Keep uncertainty visible.
- Keep text short and skimmable: short sentences, headers, lists.
- **Colours: blue and orange for status and highlights, never red against green.** The app's palette is built
  around this.
- The user wants GUIs, not config files, and desktop apps rather than local web pages unless there's a strong reason.
- Targets: Windows now (work PC), Linux at home (the final target).

## Manager architecture, in one breath

`device/` runs serial I/O on one background thread, handles reconnects, and emits Qt signals.
`render/` draws the whole canvas with QPainter: keys, then faces from `render/faces.py`, whose option lists drive the
editor forms through `schema.Field`. `render/meters.py` draws level meters behind keys and on the small screen.
`system/audio.py` reads and changes the speakers and microphone (Core Audio through ctypes on Windows, `pactl` and
`parec` on Linux) on its own thread; live levels are only measured while a page on screen shows one.
`conditions.py` holds the one condition evaluator, shared by context rules (which switch pages) and key *states*
(which repaint a key while something is true — that's what makes a mute key show the real mute state).
`controller.py` is the hub. It owns pages, presses, rules (edge-triggered: they act only when the result changes),
variables, debounced saving and undo snapshots (device settings are kept out of undo).
`actions.py` runs key actions on a worker thread; actions that touch the app (page, variable, message, brightness,
open) come back through a signal. The `audio` action goes straight to the audio monitor, which is shared.
`ui/` holds the window. Editors edit the config dicts in place and call `controller.edited()`.
Wrap any dialog in `widgets.dialog_open()` so a page switch can't reload the editor underneath it.
`ui/condition_row.py` is the one condition editor widget, used by both context rules and key states.

## Commits

Small, focused commits with clear messages. Run the manager tests before committing manager changes.
