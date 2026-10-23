# mk10probe.py

A command-line tool for talking to the MK10 directly. It needs Python 3 and `pyserial`; `show-test` also needs
`Pillow`.

```
pip install pyserial pillow
python mk10probe.py list                 # serial ports and their USB IDs
python mk10probe.py probe                # read-only first contact; prints key presses until Ctrl+C
python mk10probe.py backlight 50         # brightness 0-100
python mk10probe.py show-test            # numbered test pattern on every key and the small screen
python mk10probe.py storage              # firmware version and free space
python mk10probe.py send-test            # rehearse a file transfer into /data/mk10probe/
python mk10probe.py update FILE          # firmware update (asks you to type UPDATE first; no way back)
python mk10probe.py explore              # read-only: what methods the firmware has, and what the read ones say
python mk10probe.py files                # read-only: ask the device which files it can see
python mk10probe.py theme-strings FILE   # offline: mine the installer or firmware for the theme format
python mk10probe.py pixmap               # experiment: can SEND_PIXMAP redraw part of the display?
python mk10probe.py selftest             # check the packet code, no device needed
python mk10probe.py demo                 # run against a simulated MK10 (Linux/macOS)
```

Add `--port COM6` (or `/dev/ttyACM0`) to pick the port, and `--log FILE` to record everything sent and received.

`update` accepts either the `KeyboardDevice` program or the ScreenKey installer `.exe`, and extracts the program
from the installer. It checks the file, the device's version and free space, and runs the `send-test` rehearsal
before sending.

## Exploring the parts nobody has worked out

`explore` is read-only. It asks for a method that can't exist, because the firmware answers that by listing
every method it does have — then it runs the old-format commands that only read (GET_VERSION, GET_THEME,
SCAN_STATE) and prints whatever comes back. Nothing it sends writes a file, deletes anything or changes a
setting. `--try-volume` additionally guesses `setVolume`'s parameter name, which can only change the device's
own speaker.

`pixmap` is the one experiment that changes what's on screen. V2 has a single drawing command that takes a
JPEG of the whole 896×304 canvas, so updating one key costs a full re-encode and re-decode; V1's `SEND_PIXMAP`
(cmd 12) carries *named* JPEGs and is the only hint that something cheaper exists. The command asks GET_THEME
which image names the device knows, sends a small picture under each, and prints every reply — watch the
device while it runs. Nothing changing is a result too. Anything it does to the display is undone by starting
MK10 Manager or running `show-test`.

`files` uses `getFilesBySuffix`, whose call shape came out of `explore`: `{"suffixs": [".jpg", ".png"]}`
answers `{"filePaths": [...]}`. It sweeps a set of suffixes and, with `--path`, tries passing a folder under
several parameter names to find out whether it accepts one. Listing paths changes nothing.

None of these commands will touch `saveToFile`, `setFileCRC`, `deleteFiles` or `keyboardInput`.
