"""Offline tests for the call-control test: python -m pytest -q sdcard-tools/hidtest

Linux or macOS with a C compiler. Builds mk10hid for this machine and checks:
  - the HID report descriptor parses and every report is whole bytes;
  - the relay, driven through the probe's own HID functions over a socket pair standing in for /dev/hidg0;
  - usbconfig.sh and lunch-tests.sh against fake configfs and SD-card trees (MK10HID_ROOT, and sed for lunch).
"""

import os
import select
import shutil
import socket
import stat
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "probe"))
import mk10probe  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("cc"), reason="needs a C compiler")


@pytest.fixture(scope="module")
def relay_bin(tmp_path_factory):
    out = tmp_path_factory.mktemp("bin") / "mk10hid"
    subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(out), os.path.join(HERE, "mk10hid.c")],
                   check=True)
    return str(out)


# ---- the descriptor ------------------------------------------------------------------------------------------

def parse_descriptor(desc):
    """Bits per (main item, report id), the usage pages of each top-level collection, collection depth."""
    bits, pages, depth, i = {}, [], 0, 0
    size = count = report_id = page = 0
    while i < len(desc):
        prefix = desc[i]
        n = (0, 1, 2, 4)[prefix & 3]
        value = int.from_bytes(desc[i + 1:i + 1 + n], "little")
        tag = prefix & 0xFC
        i += 1 + n
        if tag == 0x04:
            page = value
        elif tag == 0x74:
            size = value
        elif tag == 0x94:
            count = value
        elif tag == 0x84:
            report_id = value
        elif tag in (0x80, 0x90):
            key = ("in" if tag == 0x80 else "out", report_id)
            bits[key] = bits.get(key, 0) + size * count
        elif tag == 0xA0:
            if depth == 0:
                pages.append(page)
            depth += 1
        elif tag == 0xC0:
            depth -= 1
    return bits, pages, depth


def test_descriptor_is_whole_bytes_and_two_collections(relay_bin):
    desc = subprocess.run([relay_bin, "descriptor"], capture_output=True, check=True).stdout
    bits, pages, depth = parse_descriptor(desc)
    assert depth == 0
    assert pages == [0x0B, 0xFF00]
    assert bits == {("in", 1): 8, ("out", 1): 8,
                    ("in", 2): 8 * mk10probe.HID_VENDOR_LEN, ("out", 2): 8 * mk10probe.HID_VENDOR_LEN}
    assert 1 + mk10probe.HID_VENDOR_LEN <= 64          # usbconfig.sh sets report_length 64


# ---- the relay, through the probe's functions ----------------------------------------------------------------

class SocketLink:
    """The probe's HidLink, but over the host end of a socket pair."""

    def __init__(self, sock):
        self.sock = sock

    def send(self, report_id, payload):
        self.sock.send(bytes([report_id]) + bytes(payload) + bytes(mk10probe.HID_VENDOR_LEN - len(payload)))

    def receive(self, timeout_ms):
        ready, _, _ = select.select([self.sock], [], [], timeout_ms / 1000)
        return self.sock.recv(1024) if ready else b""


@pytest.fixture
def relay(relay_bin, tmp_path):
    host, device = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    log = tmp_path / "log.txt"
    log.write_text("1.0 lunch: armed for this boot: hid audio\n")
    with open(log, "a") as err:
        proc = subprocess.Popen([relay_bin, "relay", "--fd", str(device.fileno()), "--log", str(log)],
                                pass_fds=[device.fileno()], stderr=err)
    device.close()
    yield SocketLink(host), log
    host.close()
    proc.wait(timeout=5)


def test_ping(relay):
    link, _ = relay
    ping = mk10probe.hid_ping(link)
    assert ping["version"] == 1 and ping["lights"] == 0 and ping["out"] == 1


def test_lights_from_a_meeting_app_come_back_to_the_host(relay):
    link, log = relay
    link.sock.send(bytes([mk10probe.HID_REPORT_TEL, 0x03]))       # off-hook + mute, as Teams would
    forwarded = mk10probe.hid_vendor_reply(link, "O")
    assert forwarded[1] == 2 and forwarded[2:4] == bytes([1, 3])
    assert mk10probe.hid_ping(link)["lights"] == 3
    assert "lights now 0x03" in log.read_text()


def test_telephony_press_goes_out_as_report_1(relay):
    link, _ = relay
    seen = []
    assert mk10probe.hid_telephony(link, mk10probe.HID_HOOK | mk10probe.HID_MUTE, seen.append)
    assert bytes([mk10probe.HID_REPORT_TEL, 0x03]) in seen
    assert mk10probe.hid_ping(link)["telephony"] == 3


def test_log_comes_back_whole(relay):
    link, log = relay
    with open(log, "a") as fh:
        fh.write("".join("line %03d of a longer log, to need several reports\n" % i for i in range(40)))
    text = mk10probe.hid_fetch_log(link)
    assert text == log.read_text()


def test_unknown_command_is_answered(relay):
    link, _ = relay
    link.send(mk10probe.HID_REPORT_VENDOR, b"Z")
    assert mk10probe.hid_vendor_reply(link, "?")[1] == ord("Z")


# ---- usbconfig.sh against a fake configfs ---------------------------------------------------------------------

def fake_tool(path, body="exit 0"):
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def fake_mount(tools, calls):
    """Records each call; mounting configfs makes usb_gadget appear, as the real one does."""
    fake_tool(tools / "mount", 'echo "$*" >> "%s"\n'
              '[ "$1 $2" = "-t configfs" ] && mkdir -p "$4/usb_gadget"\nexit 0' % calls)


def mount_lines(calls):
    return calls.read_text().splitlines() if calls.exists() else []


@pytest.fixture
def fake_root(tmp_path, relay_bin):
    root = tmp_path / "root"
    g = root / "sys/kernel/config/usb_gadget/g1"
    for d in (g / "configs/c.1/strings/0x409", g / "strings/0x409", g / "functions",
              root / "sys/class/udc/4100000.udc-controller", root / "tmp/mk10hid", root / "dev"):
        d.mkdir(parents=True)
    (g / "UDC").write_text("")
    hid = root / "tmp/mk10hid"
    shutil.copy(relay_bin, hid / "mk10hid")
    for name in ("usbconfig.sh", "mk10loop.sh"):
        shutil.copy(os.path.join(HERE, name), hid / name)
        (hid / name).chmod(0o755)                 # as lunch.sh leaves them
    fake_tool(hid / "setusbconfig.orig", 'echo "$*" >> "%s"' % (root / "orig-calls"))
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("arecord", "aplay"):
        fake_tool(tools / name, "sleep 30")
    fake_mount(tools, root / "mount-calls")
    env = dict(os.environ, MK10HID_ROOT=str(root), PATH="%s:%s" % (tools, os.environ["PATH"]))
    yield root, g, env
    subprocess.run(["pkill", "-f", str(root)], check=False)


def run_usbconfig(root, env, *args):
    return subprocess.run(["sh", str(root / "tmp/mk10hid/usbconfig.sh"), *args], env=env, timeout=10,
                          capture_output=True, text=True)


def test_usbconfig_builds_serial_hid_and_audio(fake_root):
    root, g, env = fake_root
    (root / "tmp/mk10hid/mode").write_text("hid audio\n")
    assert run_usbconfig(root, env, "serial").returncode == 0
    links = sorted(p.name for p in (g / "configs/c.1").iterdir() if p.is_symlink())
    assert links == ["acm.usb0", "hid.usb0", "uac1.usb0"]
    assert (g / "UDC").read_text().strip() == "4100000.udc-controller"
    assert (g / "idVendor").read_text().strip() == "0x1d6b" and (g / "idProduct").read_text().strip() == "0x0104"
    assert (g / "bDeviceClass").read_text().strip() == "0xEF"
    assert (g / "strings/0x409/product").read_text().strip() == "MK10 call-control test"
    assert (g / "strings/0x409/serialnumber").read_text().strip() == "mk10hid-hid-audio"
    desc = (g / "functions/hid.usb0/report_desc").read_bytes()
    assert parse_descriptor(desc)[1] == [0x0B, 0xFF00]
    assert (g / "functions/hid.usb0/report_length").read_text().strip() == "64"
    assert (g / "functions/uac1.usb0/p_srate").read_text().strip() == "16000"
    log = (root / "tmp/mk10hid/log.txt").read_text()
    assert "bound to 4100000.udc-controller with: acm.usb0 hid.usb0 uac1.usb0" in log
    time.sleep(0.5)
    helpers = subprocess.run(["pgrep", "-af", "mk10loop.sh"], capture_output=True, text=True).stdout
    for helper in ("relay", "speaker", "mic"):
        assert "mk10loop.sh %s" % helper in helpers


def test_usbconfig_mounts_configfs_itself(fake_root):
    """A stock MK10 at boot: adbd is disabled, so nothing has mounted configfs (this is what round 1 hit)."""
    root, g, env = fake_root
    shutil.rmtree(root / "sys/kernel/config/usb_gadget")
    (root / "tmp/mk10hid/mode").write_text("hid audio\n")
    run_usbconfig(root, env, "serial")
    assert mount_lines(root / "mount-calls") == ["-t configfs none %s" % (root / "sys/kernel/config")]
    assert not (root / "orig-calls").exists()
    assert (g / "strings/0x409/manufacturer").read_text().strip() == "Allwinner Technology Inc."
    assert (g / "configs/c.1/MaxPower").read_text().strip() == "500"
    assert sorted(p.name for p in (g / "configs/c.1").iterdir() if p.is_symlink()) == [
        "acm.usb0", "hid.usb0", "uac1.usb0"]
    assert (g / "strings/0x409/serialnumber").read_text().strip() == "mk10hid-hid-audio"


def test_usbconfig_hid_only(fake_root):
    root, g, env = fake_root
    (root / "tmp/mk10hid/mode").write_text("hid\n")
    run_usbconfig(root, env, "serial")
    assert sorted(p.name for p in (g / "configs/c.1").iterdir() if p.is_symlink()) == ["acm.usb0", "hid.usb0"]


def test_usbconfig_hands_over_when_nothing_binds(fake_root):
    root, g, env = fake_root
    (root / "tmp/mk10hid/mode").write_text("hid audio\n")
    (g / "UDC").unlink()
    (g / "UDC").mkdir()                           # writing to it fails, as a refused bind would
    run_usbconfig(root, env, "serial")
    assert (root / "orig-calls").read_text() == "serial\n"
    assert not [p for p in (g / "configs/c.1").iterdir() if p.is_symlink()]
    assert (g / "strings/0x409/serialnumber").read_text().strip() == "mk10hid-handed-over"
    assert "even the serial port alone would not bind" in (root / "tmp/mk10hid/log.txt").read_text()


def test_usbconfig_passes_other_modes_through(fake_root):
    root, _, env = fake_root
    run_usbconfig(root, env, "uac1")
    assert (root / "orig-calls").read_text() == "uac1\n"


# ---- lunch-tests.sh against a fake card -----------------------------------------------------------------------

@pytest.fixture
def fake_card(tmp_path, relay_bin):
    card, data, tmp = tmp_path / "card", tmp_path / "data", tmp_path / "tmp"
    for d in (card / "hidtest", data, tmp, tmp_path / "sys/kernel/config"):
        d.mkdir(parents=True)
    shutil.copy(relay_bin, card / "hidtest/mk10hid")
    for name in ("usbconfig.sh", "mk10loop.sh"):
        (card / "hidtest" / name).write_bytes(open(os.path.join(HERE, name), "rb").read().replace(b"\n", b"\r\n"))
    (data / "setusbconfig").write_text("#!/bin/sh\n# Waveshare's\n")
    (card / "config.json").write_text('{"deviceBl":30}')
    tools = tmp_path / "tools"
    tools.mkdir()
    fake_mount(tools, tmp_path / "mount-calls")
    script = open(os.path.join(HERE, os.pardir, "lunch-tests.sh")).read()
    script = (script.replace("/mnt/SDCARD", str(card)).replace("/data/setusbconfig", str(data / "setusbconfig"))
              .replace("/tmp/mk10hid", str(tmp / "mk10hid")).replace("/sys/kernel/config", str(tmp_path / "sys/kernel/config")))
    (tmp_path / "lunch.sh").write_text(script)
    env = dict(os.environ, PATH="%s:%s" % (tools, os.environ["PATH"]))
    return tmp_path, card, tmp / "mk10hid", env


def crumb(base):
    path = base / "sys/kernel/config/usb_gadget/g1/strings/0x409/serialnumber"
    return path.read_text().strip() if path.exists() else None


def binds(base):
    return [line for line in mount_lines(base / "mount-calls") if line.startswith("-o bind")]


def run_lunch(base, env):
    subprocess.run(["sh", str(base / "lunch.sh")], env=env, check=True, timeout=10)


def test_lunch_arms_one_boot_with_a_bind_mount(fake_card):
    base, card, hid, env = fake_card
    (card / "hid-test-once").write_text("hid audio\r\n")
    run_lunch(base, env)
    assert not (card / "hid-test-once").exists()
    assert (hid / "mode").read_text() == "hid audio\n"
    assert b"\r" not in (hid / "usbconfig.sh").read_bytes()
    assert os.access(hid / "usbconfig.sh", os.X_OK) and os.access(hid / "mk10hid", os.X_OK)
    assert (hid / "setusbconfig.orig").read_text() == "#!/bin/sh\n# Waveshare's\n"
    assert "-o bind %s %s" % (hid / "usbconfig.sh", base / "data/setusbconfig") in mount_lines(base / "mount-calls")
    assert crumb(base) == "mk10hid-armed"
    assert "armed for this boot: hid audio" in (card / "hid-test.log").read_text()
    assert "armed for this boot" in (hid / "log.txt").read_text()


def test_lunch_does_nothing_without_an_arm_file(fake_card):
    base, card, hid, env = fake_card
    run_lunch(base, env)
    assert not (base / "mount-calls").exists() and not hid.exists() and crumb(base) is None


@pytest.mark.parametrize("mode", ["hid; reboot", "", "HID"])
def test_lunch_refuses_odd_arm_files(fake_card, mode):
    base, card, _, env = fake_card
    (card / "hid-test-once").write_text(mode + "\n")
    run_lunch(base, env)
    assert not binds(base)
    assert "refused" in (card / "hid-test.log").read_text()
    assert crumb(base) == "mk10hid-refused-arm-file"


def test_lunch_needs_the_files(fake_card):
    base, card, _, env = fake_card
    (card / "hidtest/mk10loop.sh").unlink()
    (card / "hid-test-once").write_text("hid\n")
    run_lunch(base, env)
    assert not binds(base)
    assert "needs mk10hid, usbconfig.sh and mk10loop.sh" in (card / "hid-test.log").read_text()
    assert crumb(base) == "mk10hid-missing-files"


def test_install_plan_arms_last_and_ships_lf_scripts():
    plan = mk10probe.hidtest_plan()
    assert [p[0] for p in plan] == ["/mnt/SDCARD/lunch.sh", "/mnt/SDCARD/hidtest/mk10hid",
                                    "/mnt/SDCARD/hidtest/usbconfig.sh", "/mnt/SDCARD/hidtest/mk10loop.sh",
                                    "/mnt/SDCARD/hid-test-once"]
    assert plan[-1][1] == b"hid audio\n"
    assert mk10probe.hidtest_plan(audio=False, arm_only=True) == [
        ("/mnt/SDCARD/hid-test-once", b"hid\n", "arms the next boot: hid")]
    for dest, data, _ in plan:
        if dest.endswith(".sh"):
            assert b"\r" not in data and data.rstrip().endswith(b"exit 0") or b"\nexit 0\n" in data
    assert len(plan[0][1]) >= 4144                 # never shorter than the lunch.sh already on the card


@pytest.mark.parametrize("serial, expect", [
    ("mk10hid-armed", "never ran"),
    ("MK10HID-HANDED-OVER", "handed over"),          # Windows may show the serial number in capitals
    ("", "No breadcrumb"),
])
def test_probe_explains_the_breadcrumb(monkeypatch, capsys, serial, expect):
    monkeypatch.setattr(mk10probe, "usb_identity", lambda: [("Serial", serial)])
    mk10probe._hid_explain_absent()
    assert expect in capsys.readouterr().out
