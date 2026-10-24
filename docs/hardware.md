# MK10 hardware notes

Waveshare MK10 "Multi-Functional AI Voice Control Panel": 10 LCD keys plus a 2.01" secondary screen.

## What the firmware image says (MK10_V1.0.img, read October 2026)

| | |
|---|---|
| SoC | Allwinner **T113** — the build tree baked into the binary is `../Tina-t113-pro/...` |
| OS | **Tina Linux**, Allwinner's OpenWrt: procd, busybox, `/etc/init.d` |
| UI toolkit | **Qt 5.12.9**, `linuxfb` platform plugin, drawing to `/dev/fb0`. Qt Charts installed |
| Application | `/data/KeyboardDevice` (ARM 32-bit, stripped), source project `PCMonitorLinux2` |
| Root filesystem | ext4, overlay-mounted: `/overlay` from the UDISK partition over a read-only base |
| Partitions | `boot-resource`, `env`, `env-redund`, `boot`, `rootfs`; UDISK takes the rest |
| Crypto / identity | `/dev/atsha204a` — a Microchip ATSHA204A. That is what `magic.bin` talks to |
| On board | `busybox`, `adbd`, `nc`, `wget`, `curl`, `tcpdump`, `ntfs-3g`. No telnetd, dropbear or ssh |

`/data` sits on the writable overlay, so files put there persist and deleting them undoes it.

## Inside

- **Main chip:** Allwinner T113-S3 (two Cortex-A7 cores, 128 MB DDR3 in the package). It runs Linux
  (Tina/OpenWrt style, overlayfs) from internal flash, plus the Qt program `/data/KeyboardDevice`, which drives the
  screens and the USB serial link.
- **Keyboard chip:** a GD32 running QMK, VIA-enabled (`syk_keyboards_mk10_via.bin`). It reads the keys and also
  acts as a USB keyboard in its own right.
- **USB internal hub:** `05E3:0610`.
- **Wi-Fi:** the firmware configures it, so the board seems to have it *(not checked on the board itself)*.

## USB IDs (from a real MK10 on Windows)

| ID | What | Notes |
|---|---|---|
| `1D6B:0104` | Serial port (CDC-ACM gadget) | Pictures and key events. Generic Linux gadget ID |
| `4250:426F` | Keyboard chip (QMK) | Interfaces: MI_00 keyboard, MI_01 vendor HID, MI_02 mouse/system/consumer, MI_03 vendor HID |
| `05E3:0610` | Internal hub | |

## The keyboard chip types keystrokes

Pressing a key also makes the QMK chip send its own keystrokes, whatever any PC software does.

- **Linux:** the udev rules in `manager/packaging/` and `opendeck-plugin/packaging/` set `LIBINPUT_IGNORE_DEVICE`
  on 4250:426F.
- **Windows:** disable the MK10's keyboard (4250:426F) in Device Manager. This needs admin.
- **Maybe:** remap the keys to nothing with VIA. Untested; medium confidence it works.

## SD card

The SD card holds **data only**: `config.json`, `mk10.bin` (240×296 boot splash, RGB565 little-endian),
`log.txt`, `mk20-plus.bin`, and an empty `overlayDir`.

`config.json` is a short pointer file, **not** the theme itself:

```json
{"defaultTheme": "/data/theme/MK10/<theme folder>/<theme>.Theme",
 "deviceBl": 30, "deviceVolume": 7, "enableXIAOZHIAI": true}
```

- **Themes live on internal flash** under `/data/theme/MK10/<folder>/<name>.Theme`, one folder per theme, and
  the card only says which one to load. The stock entry is Waveshare's own dated default theme.
- `deviceBl` and `deviceVolume` are the same values FIND_DEVICE reports back.
- `enableXIAOZHIAI` switches on XiaoZhi, the voice assistant this device is sold around. Nothing here uses it.
- **Open question:** whether `defaultTheme` will accept a path on the SD card. If it does, a theme can be
  installed by writing to the card alone, with no file transfer to the device at all.

- The operating system is on internal flash, so copying the SD card is **not** a firmware backup.
- `magic.bin` holds a key and IDs that look like device identity or activation data. **Never publish it.**

## Firmware versions seen

- **V2.29:** speaks only the old (V1) format, and sends key events as V1 cmd 13.
- **V2.32:** speaks both formats. This is what ScreenKey v1.1 installs, and what all the tools here need.

## Official Windows software

ScreenKey (Waveshare). It reads PC stats through LibreHardwareMonitor, which uses the WinRing0 driver.
Windows security tools often flag that driver.
