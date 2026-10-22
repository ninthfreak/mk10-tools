# mk10-tools

Unofficial software for the **Waveshare MK10**, a USB macro pad with 10 LCD keys and a small 2" screen.
Not affiliated with Waveshare.

Everything here needs MK10 firmware **V2.32 or newer** (the version Waveshare's ScreenKey app installs).

| Folder | What it is | Status |
|---|---|---|
| [`manager/`](manager/) | **MK10 Manager**: a desktop app to set up keys, pages, the small screen and context rules (Windows, Linux) | First version; testing on real hardware next |
| [`opendeck-plugin/`](opendeck-plugin/) | A device plugin so [OpenDeck](https://github.com/nekename/OpenDeck) can use the MK10 | Works |
| [`probe/`](probe/) | `mk10probe.py`: talk to the MK10 directly, show test patterns, rehearse file transfers, update firmware | Works |
| [`docs/`](docs/) | [Protocol](docs/protocol.md), [hardware notes](docs/hardware.md), [research notes](docs/research-notes.md) | |

**Only one program can use the MK10 at a time.** Quit the others (ScreenKey, OpenDeck, MK10 Manager) before
starting one.

## Quick start

- **MK10 Manager:** see [`manager/README.md`](manager/README.md). On Windows, double-click
  `manager/run-windows.bat`.
- **OpenDeck plugin:** see [`opendeck-plugin/README.md`](opendeck-plugin/README.md).
- **Probe:** `python probe/mk10probe.py --help`.

## Be careful with

- **Firmware updates** (`mk10probe.py update`) have no automatic way back.
- **The MK10's keyboard chip** types keystrokes on its own when you press keys. See
  [docs/hardware.md](docs/hardware.md) for how to stop that.

Icons in MK10 Manager: [Tabler Icons](https://tabler.io/icons), MIT licence.
