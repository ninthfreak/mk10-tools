# The SD card is a root shell

`/etc/init.d/qt_app1` in Waveshare's own firmware image (`MK10_V1.0.img`, Allwinner `IMAGEWTY`) contains:

```sh
SRC="/mnt/SDCARD/lunch.sh"
DEST="/data/lunch.sh"
if [ -f "$SRC" ]; then
    cp "$SRC" "$DEST"
    chmod +x "$DEST"
    "$DEST"
fi
```

and, a few lines above it:

```sh
OVERLAY_DIR="/mnt/SDCARD/overlayDir"
if [ -n "$(ls -A "$OVERLAY_DIR" 2>/dev/null)" ]; then
    cp -rf "$OVERLAY_DIR"/* / 2>/dev/null
    rm -rf "$OVERLAY_DIR"/* "$OVERLAY_DIR"/.[^.]* 2>/dev/null
fi
```

So the card gives two things, both by the vendor's design:

- **`lunch.sh`** — an arbitrary script, run as root at every boot, before the UI starts.
- **`overlayDir/`** — anything in it is copied over the root filesystem, then deleted from the card.

Both are undone by taking the card out. Nothing has to be written to the device over the serial protocol,
so none of this risks the device the way overwriting `/data/appLunch.sh` would.

## Using it

1. Copy `lunch.sh` to the top level of the MK10's SD card. **Keep LF line endings** — a CRLF shebang
   makes `#!/bin/sh` fail silently and nothing will run. `.gitattributes` pins it to LF here.
2. Put the card in, power-cycle the MK10.
3. Read `mk10dump/` off the card.

If `mk10dump/when.txt` doesn't appear, the hook isn't in your firmware (ours was read out of a 2022 image)
and nothing happened — this cannot half-work.

## What the firmware image told us to look for

- Themes live in **`/data/theme/<width>x<height>/`** — the factory image ships `480x480`, `800x480`,
  `1024x600` and so on, all empty. For this device that means `/data/theme/896x304/`. The older note in
  `research-notes.md` about `/data/theme/MK10/<folder>/<name>.Theme` predates reading the image; the string
  `.Theme` does not appear in the firmware at all.
- `/data` is on the **writable overlay** (`/overlay`, from the UDISK partition), so what we put there
  persists and deleting it undoes it.
- `adbd` is installed and `setusbconfig` supports `adb`, `mtp` and `rndis` — but the ACM serial gadget we
  talk to is built by hand in `/data/serial_adb.sh`, so switching modes probably takes the serial protocol
  away with it. `rndis` plus the on-board `nc` is the less invasive route to a shell if we ever want one.

## Why the report is built in RAM

The first run left every file after the first one empty. That is what a **full filesystem** looks like from
a shell script: the redirection creates the file, the write fails for want of space, and the error message
has nowhere to go either, so you get a 0-byte file and no complaint. `when.txt` was small and written first,
so it fit; then `cp -r /data/image` filled the card and everything behind it was lost.

So the script now builds one report in `/tmp`, **keeps a copy at `/data/mk10dump-report.txt` on the device's
own storage**, and touches the card exactly once at the end with a single small file. If the card is too
full even for that, the copy on the device survives and the next run brings it out as
`report-previous.txt`.

The directory copying stays off unless you create an empty file called `copy-everything` beside `lunch.sh`
on the card, and it refuses to start with less than 64 MB free.

If `report.txt` ends with `===== report complete =====`, nothing was lost. `df`, `/proc/partitions` and the
card's own contents are near the top, so a space problem is visible immediately.

## Trying a theme without touching the card: `lunch-theme-test.sh`

Upload it as the card's `lunch.sh` and it gives a **one-boot theme switch that undoes itself**:

```
python mk10probe.py upload mk10test.Theme --dest /data/theme/MK10/mk10test/mk10test.Theme
python mk10probe.py upload ../sdcard-tools/lunch-theme-test.sh --dest /mnt/SDCARD/lunch.sh
python mk10probe.py upload ../sdcard-tools/theme-test-once --dest /mnt/SDCARD/theme-test-once
```

- **Next boot:** `config.json` is saved as `config.json.before-test`, its `defaultTheme` is pointed at the path
  inside `theme-test-once`, and the arm file is deleted.
- **The boot after:** `config.json` is copied back from the saved copy — byte-identical, checked.

So any second power cycle undoes a test, including one that leaves the screen broken, and undoing it needs
neither the card nor a working serial link. It refuses to switch if the theme file isn't on the device yet,
and leaves `config.json` alone if it can't find `defaultTheme` in it. Each step is logged to
`/mnt/SDCARD/theme-test.log`.

`upload` refuses a script with CRLF line endings: `#!/bin/sh\r` would silently never run.

Once `lunch.sh` is on the card it stays there, so later tests need only the theme and an arm file.
`theme-test-once-round2` arms `/data/theme/MK10/mk10test2/mk10test2.Theme`; upload it as
`/mnt/SDCARD/theme-test-once`.

## Rules for anything you put in here

`qt_app1` waits for this script to finish before the device starts its UI. So:

- No loops, no `sleep` in the foreground, nothing interactive.
- Background anything slow: `( ... ) &`.
- `adbd` exists on the device but `qt_app1` stops and disables it *after* running `lunch.sh`, so enabling
  it has to be deferred past that point.
