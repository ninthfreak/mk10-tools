"""A pretend MK10 on a pseudo-terminal (Linux/macOS), for tests. Mirrors the replies seen from firmware V2.32."""

import json
import os
import threading
import time
import tty

from mk10manager.device.protocol import (CMD_JSON, CMD_SHOW_JPG, V1_FIND_DEVICE, FrameParser, build_frame,
                                         build_frame_v1, qmap_encode)


def jpeg_size(data: bytes):
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        seglen = int.from_bytes(data[i + 2:i + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2):
            h = int.from_bytes(data[i + 5:i + 7], "big")
            w = int.from_bytes(data[i + 7:i + 9], "big")
            return w, h
        i += 2 + seglen
    return None


class FakeMK10(threading.Thread):
    def __init__(self, mode="v2"):
        super().__init__(daemon=True)
        self.master, slave = os.openpty()
        tty.setraw(slave)
        os.set_blocking(self.master, False)
        self.port = os.ttyname(slave)
        self._slave = slave
        self.mode = mode            # "v2", "old" (V1 only), "silent"
        self.parser = FrameParser()
        self.frames: list[bytes] = []
        self.calls: list[dict] = []
        self.errors_sent = 0
        self.stop_flag = threading.Event()
        self.lock = threading.Lock()
        self.event_seq = 0xFFFFFFFE

    def _write(self, data: bytes):
        with self.lock:
            view = memoryview(data)
            while view:
                try:
                    n = os.write(self.master, view)
                    view = view[n:]
                except BlockingIOError:
                    time.sleep(0.001)

    def press(self, row, col, pressed=True):
        body = {"method": "keyStateChanged", "parameters": {"row": row, "col": col, "pressed": pressed}}
        self._write(build_frame(self.event_seq, CMD_JSON, json.dumps(body).encode()))
        self.event_seq -= 1

    def tap(self, row, col, hold=0.05):
        self.press(row, col, True)
        time.sleep(hold)
        self.press(row, col, False)

    def handle(self, pkt):
        if pkt.proto == "v1":
            if self.mode == "old" and pkt.cmd == V1_FIND_DEVICE:
                self._write(build_frame_v1(V1_FIND_DEVICE, qmap_encode({"version": "V2.29"}), 2))
            return
        if self.mode != "v2":
            return
        if pkt.cmd == CMD_JSON:
            req = json.loads(pkt.payload)
            self.calls.append(req)
            m = req.get("method")
            if m == "getInfo":
                rects = [{"row": r, "col": c, "x": c * 128, "y": r * 128, "width": 128, "height": 128,
                          "isKey": True} for r in range(2) for c in range(5)]
                rects.append({"x": 656, "y": 0, "width": 240, "height": 296, "isKey": False})
                self._write(build_frame(pkt.seq, CMD_JSON, json.dumps({
                    "ack_method": m, "success": True, "result": {
                        "deviceModel": "MK10", "deviceVersion": "V2.32", "deviceWidth": 896, "deviceHeight": 304,
                        "devicePanel": {"rectCols": 5, "rectRows": 2, "rects": rects}}}).encode()))
            else:
                self._write(build_frame(pkt.seq, CMD_JSON, json.dumps(
                    {"ack_method": m, "success": True, "result": {}}).encode()))
        elif pkt.cmd == CMD_SHOW_JPG:
            if jpeg_size(pkt.payload) != (896, 304):
                self.errors_sent += 1
                self._write(build_frame(0xFFFFFFFF, CMD_JSON, json.dumps({
                    "success": False, "errorString": "Incorrect resolution"}).encode()))
            else:
                self.frames.append(pkt.payload)
                self._write(build_frame(pkt.seq, CMD_JSON, json.dumps({"success": True}).encode()))

    def run(self):
        while not self.stop_flag.is_set():
            try:
                data = os.read(self.master, 65536)
            except BlockingIOError:
                time.sleep(0.005)
                continue
            except OSError:
                return
            for pkt in self.parser.feed(data):
                self.handle(pkt)

    def unplug(self):
        self.stop_flag.set()
        time.sleep(0.05)
        for fd in (self.master, self._slave):
            try:
                os.close(fd)
            except OSError:
                pass

    def backlight(self):
        for c in reversed(self.calls):
            if c.get("method") == "setBacklight":
                return c["parameters"].get("level")
        return None
