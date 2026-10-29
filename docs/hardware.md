# MK10 hardware notes

Waveshare MK10 "Multi-Functional AI Voice Control Panel": 10 LCD keys plus a 2.01" secondary screen.

## What the firmware image says (MK10_V1.0.img, read October 2026)

| | |
|---|---|
| SoC | Allwinner **T113 / R528** (same die family), dual Cortex-A7. The device tree is `compatible = allwinner,t113 / allwinner,r528` and its root `model` is literally **`MK10`** |
| C library | **glibc 2.23**. Cross-built binaries must be statically linked to run |
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
- **Wi-Fi:** none on the board. The USB-A port is the T113's own host port (`usbc1`, host-only) for Waveshare's
  optional RTL8188GU Wi-Fi stick: `qt_app1` runs `usb_modeswitch` on `0bda:1a2b`, its driver-CD mode.
- **Audio:** a built-in mic and speaker. Waveshare's docs list both, the holes are on the case, the XiaoZhi
  voice assistant records from `hw:audiocodec`, and `config.json` has `deviceVolume`.
  - `/etc/asound.conf` shares them: playback through `dmix` (48 kHz, 4×1024 frames, ~85 ms), capture through
    `dsnoop` (16 kHz, 3 channels; `CaptureMic` is a 2-channel view of it).
  - Waveshare's app has a **USB-speaker mode**: `usbTYpe` in `config.json` makes it run `setusbconfig uac1`
    and pipe the gadget's sound card into the speaker. That mode replaces the serial port.
    See [call-control.md](call-control.md).
- **Bluetooth:** the kernel has the core (`CONFIG_BT=y`) but **no USB Bluetooth driver** (`CONFIG_BT_HCIBTUSB`
  is off), so a USB Bluetooth adapter won't work. [call-control.md](call-control.md) has the options.

## USB IDs (from a real MK10 on Windows)

| ID | What | Notes |
|---|---|---|
| `1D6B:0104` | Serial port (CDC-ACM gadget) | Pictures and key events. Generic Linux gadget ID. `KeyboardDevice` builds it with `/data/setusbconfig serial`, which also mounts configfs: `adbd`, which would otherwise, is disabled on the running device |
| `4250:426F` | Keyboard chip (QMK) | Interfaces: MI_00 keyboard, MI_01 vendor HID, MI_02 mouse/system/consumer, MI_03 vendor HID |
| `05E3:0610` | Internal hub | |

## The keyboard chip types keystrokes

Pressing a key also makes the QMK chip send its own keystrokes, whatever any PC software does.

- **Linux:** the udev rules in `manager/packaging/` and `opendeck-plugin/packaging/` set `LIBINPUT_IGNORE_DEVICE`
  on 4250:426F.
- **Windows:** disable the MK10's keyboard (4250:426F) in Device Manager. This needs admin.
- **Maybe:** remap the keys to nothing with VIA. Untested; medium confidence it works.

## SD card — it is the whole computer

**Corrected October 2026.** The card is not storage for the device. It **is** the device's system disk:
every partition in the firmware image is on it, and `root=/dev/mmcblk0p5` on the boot line is a partition
of this card. Windows shows only the first one, which is why we spent months thinking the card held
"data only".

Disk Management on a 3.75 GB card, against `sys_partition.fex`:

| On the card | Partition | Size | What |
|---|---|---|---|
| 20 MB unallocated | — | 20 MB | raw boot0 / u-boot / toc, written to sectors with no partition |
| 32 MB volume | `boot-resource` `p1` | 32 MB | FAT — **the only one Windows mounts**, and what the device calls `/mnt/SDCARD` |
| ~6 MB | `boot` `p4` | 6.2 MB | kernel (Android bootimg) |
| 1.00 GB RAW | `rootfs` `p5` | 1.00 GB | ext4, the root filesystem |
| 2.69 GB RAW | `UDISK` `p6` | 2.69 GB | ext4, the writable overlay — **everything ScreenKey installed is here**: the running firmware, the theme, fonts, images |

Partition 5's `/data` is the factory base and its content folders are all empty; its `KeyboardDevice` reports
V2.29, the published image has V2.31, and the device reports V2.32. The running software is in the overlay,
shadowing the empty originals. Read p6, not p5.

`env` and `env-redund` (`p2`, `p3`, 252 KB each) are too small for Disk Management to draw. Every size
matches `sys_partition.fex` exactly, so this is settled rather than inferred.

Three consequences:

- **Copying the card is a complete firmware backup.** The note below used to say the opposite. With an image
  of the card, the device cannot be bricked by anything we do to its software.
- **The whole filesystem can be read and written from any PC with a card reader**, no serial protocol, no
  `lunch.sh`, no upload path. The theme, `/data`, the running `KeyboardDevice` — all of it is just files on
  partition 6.
- **`/mnt/SDCARD` is only the 32 MB FAT partition**, and it already holds the boot resources. That is why the
  first dump filled it. Don't write anything substantial there.

### What is on the 32 MB partition

`config.json`, `mk10.bin` (240×296 boot splash, RGB565 little-endian), `log.txt`, `mk20-plus.bin`, and an
empty `overlayDir`.

`config.json` is a short pointer file, **not** the theme itself:

```json
{"defaultTheme": "/data/theme/MK10/<theme folder>/<theme>.Theme",
 "deviceBl": 30, "deviceVolume": 7, "enableXIAOZHIAI": true}
```

- **Themes live under `/data/theme/MK10/<folder>/<name>.Theme`** — on the card's UDISK partition, not on any
  internal flash. One folder per theme, and `config.json` only says which to load. The stock entry is
  Waveshare's own dated default theme. (The 2022 firmware image uses `/data/theme/<width>x<height>/` with no
  `.Theme` extension; this device's own `config.json` is the authority for the firmware it runs, and it says
  the `MK10/` form. The convention changed between builds.)
- `deviceBl` and `deviceVolume` are the same values FIND_DEVICE reports back.
- `enableXIAOZHIAI` switches on XiaoZhi, the voice assistant this device is sold around. Nothing here uses it.
- **Open question:** whether `defaultTheme` will accept a path on the SD card. If it does, a theme can be
  installed by writing to the card alone, with no file transfer to the device at all.

- Copying the SD card **is** a complete firmware backup — the operating system is on it. (This line used to
  say the reverse, from before the partition table was read.)
- `magic.bin` holds a key and IDs that look like device identity or activation data. **Never publish it.**

## Firmware versions seen

- **V2.29:** speaks only the old (V1) format, and sends key events as V1 cmd 13.
- **V2.32:** speaks both formats. This is what ScreenKey v1.1 installs, and what all the tools here need.

## Official Windows software

ScreenKey (Waveshare). It reads PC stats through LibreHardwareMonitor, which uses the WinRing0 driver.
Windows security tools often flag that driver.
