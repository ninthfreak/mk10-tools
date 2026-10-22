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
  devices in use, a picture, or a plain colour.
- **Drag** one key onto another to swap them. **Drop a picture** from your files onto a key.
  **Right-click** a key to copy, paste, clear or test it.
- **Pages** are at the top. Each page has its own keys and its own small screen.
  Right-click a page to rename, duplicate, delete it, or make it the default.
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
open a website or file, run a command, send a web request (Home Assistant and similar), change page,
set a variable, show a message on the small screen, change brightness, wait.

### Text placeholders

Use these in labels and on the small screen:
`{time}` `{seconds}` `{date}` `{day}` `{month}` `{year}` `{app}` `{title}` `{page}` `{var:name}`
and any strftime format, like `{time:%H:%M:%S}` or `{date:%d/%m}`.

For sound: `{out_device}` `{in_device}` (the speakers and microphone in use), `{volume}` `{mic_volume}`,
`{level}` `{mic_level}` (how loud they are right now, 0-100), `{mute}` `{mic_mute}` (the word "muted", or
nothing), and `{mute:Muted|Live}` for your own two words.

A Text face can also run a command every few seconds and show its output with `{output}`.

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
- **Media keys on Linux** use `playerctl`, plus `wpctl` or `pactl` for volume.
- **Reading and changing the sound** uses Windows Core Audio on Windows, and `pactl` on Linux (install
  `pulseaudio-utils`; it works with PipeWire too). The Device and app tab says which one it found.
- **Live level meters** measure how loud the speakers and microphone are.
  - Windows asks the audio engine, so nothing is recorded.
  - Linux has to open a recording stream (`parec`, also in `pulseaudio-utils`). It only does this while a
    page on screen actually shows a live meter, and stops the moment you switch away. The *Live level
    meters* switch on the Device and app tab turns it off completely.
  - While a live meter is on screen the whole display picture is redrawn and sent to the MK10 several
    times a second. *Updates a second* on the Device and app tab sets how often (6 by default).
- **Your settings, pictures and log** are in the settings folder. The Device and app tab has a button that opens it.
  - Windows: `%APPDATA%\MK10 Manager`
  - Linux: `~/.config/mk10-manager`
- **Second hand.** A clock with a second hand sends the whole display picture once a second.

## Testing

`python -m pytest tests` runs the tests. They use a pretend MK10 (Linux or macOS only).

Icons: [Tabler Icons](https://tabler.io/icons), MIT licence (`mk10manager/resources/TABLER-ICONS-LICENSE.txt`).
