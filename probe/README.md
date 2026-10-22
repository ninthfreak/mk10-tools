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
python mk10probe.py selftest             # check the packet code, no device needed
python mk10probe.py demo                 # run against a simulated MK10 (Linux/macOS)
```

Add `--port COM6` (or `/dev/ttyACM0`) to pick the port, and `--log FILE` to record everything sent and received.

`update` accepts either the `KeyboardDevice` program or the ScreenKey installer `.exe`, and extracts the program
from the installer. It checks the file, the device's version and free space, and runs the `send-test` rehearsal
before sending.
