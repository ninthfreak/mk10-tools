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

## Why everything is in one file now

The first version wrote a dozen separate files. On the real device only the first, `when.txt`, had anything
in it — the rest existed but were empty. Writes were still in cache when the card stopped being written to,
and only the earliest one had been flushed.

So v2 writes a single `report.txt`, calls `sync` after every section, and leaves the slow directory copying
switched off unless you create an empty file called `copy-everything` beside `lunch.sh` on the card. A
provisioned `/data` can take long enough to copy that pulling the card loses everything after it.

If `report.txt` ends with `===== report complete =====`, nothing was lost.

## Rules for anything you put in here

`qt_app1` waits for this script to finish before the device starts its UI. So:

- No loops, no `sleep` in the foreground, nothing interactive.
- Background anything slow: `( ... ) &`.
- `adbd` exists on the device but `qt_app1` stops and disables it *after* running `lunch.sh`, so enabling
  it has to be deferred past that point.
