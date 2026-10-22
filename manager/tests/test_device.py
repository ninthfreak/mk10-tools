import json
import time

import pytest

from conftest import wait_until
from fake_mk10 import FakeMK10, jpeg_size
from mk10manager.device.layout import DeviceLayout
from mk10manager.device.manager import Connection, DeviceManager, NoAnswer, OldFirmware
from mk10manager.device.protocol import CMD_JSON, FrameParser, build_frame, build_frame_v1, crc32, key_event


def test_frame_matches_reference():
    # Same bytes the Rust plugin's unit test checks (ends 0673419e)
    f = build_frame(1, CMD_JSON, b'{"method":"getInfo","parameters":{}}')
    assert f[:4] == b"\xA1\xA5\x5A\x5E"
    p = FrameParser()
    pkts = p.feed(f[:7]) + p.feed(f[7:])
    assert len(pkts) == 1 and pkts[0].seq == 1 and pkts[0].json()["method"] == "getInfo"


def test_parser_skips_junk_and_bad_crc():
    good = build_frame(5, CMD_JSON, b'{"method":"keyStateChanged","parameters":{"row":1,"col":3,"pressed":true}}')
    bad = bytearray(good)
    bad[-1] ^= 0xFF
    p = FrameParser()
    pkts = p.feed(b"junk" + bytes(bad) + b"\x00\x01" + good)
    assert len(pkts) == 1 and key_event(pkts[0]) == (1, 3, True)
    assert p.bad_frames == 1


def test_layout_from_info_and_hits():
    info = {"result": {"deviceModel": "MK10", "deviceVersion": "V2.32", "deviceWidth": 896, "deviceHeight": 304,
                       "devicePanel": {"rectCols": 5, "rectRows": 2, "rects": [
                           {"row": r, "col": c, "x": c * 128, "y": r * 128, "width": 128, "height": 128,
                            "isKey": True} for r in range(2) for c in range(5)] + [
                           {"x": 656, "y": 0, "width": 240, "height": 296, "isKey": False}]}}}
    lay = DeviceLayout.from_info(info)
    assert (lay.rows, lay.cols, lay.key_count, len(lay.screens)) == (2, 5, 10, 1)
    assert lay.position(1, 2) == 7
    assert lay.hit(3 * 128 + 5, 128 + 5) == ("key", 8)
    assert lay.hit(700, 200) == ("screen", 0)
    assert lay.version == "V2.32"


def test_connection_handshake_and_old_firmware():
    dev = FakeMK10("v2")
    dev.start()
    c = Connection(dev.port)
    lay = c.handshake()
    assert lay.key_count == 10 and lay.model == "MK10"
    c.close()
    dev.unplug()

    old = FakeMK10("old")
    old.start()
    c = Connection(old.port)
    with pytest.raises(OldFirmware) as e:
        c.handshake(timeout=0.5)
    assert "V2.29" in str(e.value)
    c.close()
    old.unplug()

    silent = FakeMK10("silent")
    silent.start()
    c = Connection(silent.port)
    with pytest.raises(NoAnswer):
        c.handshake(timeout=0.3)
    c.close()
    silent.unplug()


def test_manager_connect_keys_frames_and_unplug(qapp, monkeypatch):
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QImage, QColor
    dev = FakeMK10("v2")
    dev.start()
    monkeypatch.setenv("MK10_PORT", dev.port)
    m = DeviceManager()
    got = {"connected": None, "keys": [], "disconnected": None}
    m.connected.connect(lambda lay: got.__setitem__("connected", lay))
    m.key_event.connect(lambda r, c, p: got["keys"].append((r, c, p)))
    m.disconnected.connect(lambda why: got.__setitem__("disconnected", why))
    m.start()
    assert wait_until(qapp, lambda: got["connected"] is not None), m.status

    img = QImage(896, 304, QImage.Format_RGB32)
    img.fill(QColor(0, 90, 200))
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "JPG", 90)
    m.show(bytes(buf.data()))
    m.send("setBacklight", {"level": 42})
    assert wait_until(qapp, lambda: dev.frames and dev.backlight() == 42)
    assert jpeg_size(dev.frames[-1]) == (896, 304)

    dev.tap(1, 4)
    assert wait_until(qapp, lambda: len(got["keys"]) == 2)
    assert got["keys"] == [(1, 4, True), (1, 4, False)]

    dev.unplug()
    assert wait_until(qapp, lambda: got["disconnected"] is not None, 5)
    m.stop()
