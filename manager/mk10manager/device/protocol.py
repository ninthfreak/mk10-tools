"""MK10 wire protocol (firmware V2.32+), worked out from the firmware.

New format (V2), little-endian:
    A1 A5 5A 5E | u32 seq | u32 cmd | u32 len | u32 crc32(len as 4 bytes) | payload | u32 crc32(payload)
    cmd 100 = show a full-canvas JPEG, cmd 101 = JSON {"method", "parameters"}.
Old format (V1), only used here to recognise older firmware:
    "AA551234 FIXEDCMDHEAD " | u32 type | u32 cmd | u32 len | u32 crc32(payload) | payload (Qt QMap)
"""

import json
import struct
import zlib
from dataclasses import dataclass

MAGIC = b"\xA1\xA5\x5A\x5E"
MAGIC_V1 = b"AA551234 FIXEDCMDHEAD "
HEADER_LEN = 20
HEADER_LEN_V1 = len(MAGIC_V1) + 16
CMD_SHOW_JPG = 100
CMD_JSON = 101
V1_FIND_DEVICE = 0
MAX_PAYLOAD = 16 * 1024 * 1024
ERROR_SEQ = 0xFFFFFFFF


@dataclass
class Packet:
    proto: str          # "v1" or "v2"
    seq: int | None
    ptype: int | None
    cmd: int
    payload: bytes

    def json(self):
        if self.proto != "v2" or self.cmd != CMD_JSON:
            return None
        try:
            d = json.loads(self.payload)
        except ValueError:
            return None
        return d if isinstance(d, dict) else None


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def build_frame(seq: int, cmd: int, payload: bytes) -> bytes:
    n = len(payload)
    return (MAGIC + struct.pack("<III", seq & 0xFFFFFFFF, cmd, n)
            + struct.pack("<I", crc32(struct.pack("<I", n)))
            + payload + struct.pack("<I", crc32(payload)))


def build_json(seq: int, method: str, parameters: dict | None = None) -> bytes:
    body = {"method": method, "parameters": parameters or {}, "requestId": seq}
    return build_frame(seq, CMD_JSON, json.dumps(body, separators=(",", ":")).encode())


def build_frame_v1(cmd: int, payload: bytes, ptype: int = 0) -> bytes:
    return MAGIC_V1 + struct.pack("<IIII", ptype, cmd, len(payload), crc32(payload)) + payload


def _qstr(s: str) -> bytes:
    b = s.encode("utf-16-be")
    return struct.pack(">I", len(b)) + b


def qmap_encode(d: dict) -> bytes:
    """Qt5 QDataStream QMap<QString,QString>."""
    out = struct.pack(">i", len(d))
    for k in sorted(d, reverse=True):
        out += _qstr(k) + _qstr(d[k])
    return out


def qmap_decode(b: bytes):
    try:
        (count,) = struct.unpack_from(">I", b, 0)
        if count > 10000:
            return None
        pos, out = 4, {}
        for _ in range(count):
            pair = []
            for _ in range(2):
                (n,) = struct.unpack_from(">I", b, pos)
                pos += 4
                if n == 0xFFFFFFFF:
                    pair.append("")
                    continue
                if n % 2 or pos + n > len(b):
                    return None
                pair.append(b[pos:pos + n].decode("utf-16-be"))
                pos += n
            out[pair[0]] = pair[1]
        return out if pos == len(b) else None
    except (struct.error, UnicodeDecodeError):
        return None


def key_event(pkt: Packet):
    """(row, col, pressed) for a V2 key packet, else None."""
    d = pkt.json()
    if d and d.get("method") == "keyStateChanged":
        p = d.get("parameters") or {}
        try:
            return int(p.get("row")), int(p.get("col")), bool(p.get("pressed"))
        except (TypeError, ValueError):
            return None
    return None


class FrameParser:
    """Feed raw bytes in any chunk size; returns complete packets in either format."""

    KEEP = max(len(MAGIC), len(MAGIC_V1)) - 1

    def __init__(self):
        self.buf = bytearray()
        self.bad_frames = 0

    def feed(self, data: bytes) -> list[Packet]:
        self.buf += data
        out = []
        while True:
            hits = [i for i in (self.buf.find(MAGIC), self.buf.find(MAGIC_V1)) if i >= 0]
            if not hits:
                keep = 0
                for k in range(min(len(self.buf), self.KEEP), 0, -1):
                    tail = bytes(self.buf[-k:])
                    if MAGIC.startswith(tail) or MAGIC_V1.startswith(tail):
                        keep = k
                        break
                del self.buf[:len(self.buf) - keep]
                return out
            i = min(hits)
            if i > 0:
                del self.buf[:i]
                continue
            if self.buf.startswith(MAGIC):
                if len(self.buf) < HEADER_LEN:
                    return out
                seq, cmd, n, ncrc = struct.unpack_from("<IIII", self.buf, 4)
                if ncrc != crc32(struct.pack("<I", n)) or n > MAX_PAYLOAD:
                    self.bad_frames += 1
                    del self.buf[:1]
                    continue
                total = HEADER_LEN + n + 4
                if len(self.buf) < total:
                    return out
                payload = bytes(self.buf[HEADER_LEN:HEADER_LEN + n])
                (pcrc,) = struct.unpack_from("<I", self.buf, HEADER_LEN + n)
                del self.buf[:total]
                if pcrc != crc32(payload):
                    self.bad_frames += 1
                    continue
                out.append(Packet("v2", seq, None, cmd, payload))
            else:
                if len(self.buf) < HEADER_LEN_V1:
                    return out
                ptype, cmd, n, pcrc = struct.unpack_from("<IIII", self.buf, len(MAGIC_V1))
                if n > MAX_PAYLOAD or ptype > 3:
                    self.bad_frames += 1
                    del self.buf[:1]
                    continue
                total = HEADER_LEN_V1 + n
                if len(self.buf) < total:
                    return out
                payload = bytes(self.buf[HEADER_LEN_V1:total])
                if pcrc != crc32(payload):
                    self.bad_frames += 1
                    del self.buf[:1]
                    continue
                del self.buf[:total]
                out.append(Packet("v1", None, ptype, cmd, payload))
