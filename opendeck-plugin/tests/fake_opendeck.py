"""End-to-end test: fake OpenDeck (websocket server) + simulated MK10 (pty) + the real plugin binary."""
import asyncio, base64, io, json, os, subprocess, sys, tty, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "probe"))
import mk10probe as m
from PIL import Image
import websockets

BIN = sys.argv[1]
results = []


def check(name, cond, detail=""):
    results.append(cond)
    print("  %s  %s%s" % ("PASS" if cond else "FAIL", name, ("  (%s)" % (detail,)) if detail else ""))


class RecordingMK10(m.SimulatedMK10):
    def __init__(self, fd):
        super().__init__(fd)
        self.frames, self.backlight = [], None

    def handle(self, pkt):
        if pkt.proto == "v2" and pkt.cmd == m.CMD_SHOW_JPG:
            self.frames.append(pkt.payload)
        if pkt.proto == "v2" and pkt.cmd == m.CMD_JSON:
            d = json.loads(pkt.payload)
            if d.get("method") == "setBacklight":
                self.backlight = d["parameters"]["level"]
        super().handle(pkt)


def png_url(color):
    buf = io.BytesIO()
    Image.new("RGB", (144, 144), color).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def jpeg_url(color):
    buf = io.BytesIO()
    Image.new("RGB", (72, 72), color).save(buf, "JPEG", quality=95)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


COLORS = [(200, 30, 30), (30, 200, 30), (30, 30, 200), (200, 200, 30), (200, 30, 200),
          (30, 200, 200), (240, 240, 240), (120, 60, 0), (0, 120, 60), (60, 0, 120)]


def pixel(frame, x, y):
    return Image.open(io.BytesIO(frame)).convert("RGB").getpixel((x, y))


def close(a, b, tol=28):
    return all(abs(p - q) <= tol for p, q in zip(a, b))


async def main():
    master, slave = os.openpty()
    tty.setraw(slave)
    os.set_blocking(master, False)
    sim = RecordingMK10(master)
    sim.start()

    inbox = asyncio.Queue()
    conn = {}

    async def handler(ws):
        conn["ws"] = ws
        async for msg in ws:
            await inbox.put(json.loads(msg))

    async def expect(pred, timeout=8.0, what=""):
        end = time.time() + timeout
        while time.time() < end:
            try:
                ev = await asyncio.wait_for(inbox.get(), timeout=max(0.05, end - time.time()))
            except asyncio.TimeoutError:
                break
            if pred(ev):
                return ev
        return None

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        env = dict(os.environ, MK10_PORT=os.ttyname(slave))
        proc = subprocess.Popen([BIN, "-port", str(port), "-pluginUUID", "local.opendeck-mk10",
                                 "-registerEvent", "registerPlugin", "-info", "{}"],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        print("Plugin started (pid %d)" % proc.pid)

        reg = await expect(lambda e: e.get("event") == "registerPlugin")
        check("plugin registers with OpenDeck", reg is not None and reg.get("uuid") == "local.opendeck-mk10")
        dev = await expect(lambda e: e.get("event") == "registerDevice")
        p = (dev or {}).get("payload", {})
        check("registers the MK10 as a 2x5 device", p.get("rows") == 2 and p.get("columns") == 5 and p.get("encoders") == 0, p)
        check("device id uses the plugin namespace", str(p.get("id", "")).startswith("wk-"), p.get("id"))
        rr = await expect(lambda e: e.get("event") == "rerenderImages")
        check("asks OpenDeck to send key images", rr is not None)
        dev_id = p.get("id")
        ws = conn["ws"]

        # Key images: mix of PNG and JPEG data URLs, as OpenDeck/plugins may send either
        for pos, col in enumerate(COLORS):
            url = png_url(col) if pos % 2 == 0 else jpeg_url(col)
            await ws.send(json.dumps({"event": "setImage", "device": dev_id, "controller": "Keypad",
                                      "position": pos, "image": url}))
        await asyncio.sleep(1.0)
        check("sends pictures to the device", len(sim.frames) >= 1, "%d frames" % len(sim.frames))
        last = sim.frames[-1] if sim.frames else None
        if last:
            check("pictures are 896x304 JPEGs", m.jpeg_size(last) == (896, 304), m.jpeg_size(last))
            ok = True
            for pos, col in enumerate(COLORS):
                r, c = divmod(pos, 5)
                px = pixel(last, c * 128 + 64, r * 128 + 64)
                if not close(px, col):
                    ok = False
                    print("     key %d expected %s got %s" % (pos, col, px))
            check("each key shows its own image in the right place", ok)
            small = [pixel(last, x, y) for x in range(656, 896, 4) for y in range(110, 180, 4)]
            check("small screen shows the clock (not blank)", any(sum(p) > 200 for p in small))
            gap = pixel(last, 648, 100)
            check("gap between keys and small screen stays dark", sum(gap) < 60, gap)

        # Keys pressed on the device -> keyDown/keyUp at row*5+col
        presses = []
        end = time.time() + 6
        while time.time() < end and len(presses) < 6:
            ev = await expect(lambda e: e.get("event") in ("keyDown", "keyUp"), timeout=end - time.time())
            if ev:
                presses.append((ev["event"], ev["payload"]["position"], ev["payload"]["device"]))
        expected = [("keyDown", 0), ("keyUp", 0), ("keyDown", 4), ("keyUp", 4), ("keyDown", 7), ("keyUp", 7)]
        check("key presses arrive with the right positions", [(a, b) for a, b, _ in presses] == expected, presses)
        check("key presses carry the device id", all(d == dev_id for _, _, d in presses))

        # Clear one key
        n = len(sim.frames)
        await ws.send(json.dumps({"event": "setImage", "device": dev_id, "controller": "Keypad", "position": 3}))
        await asyncio.sleep(0.8)
        check("clearing a key sends a new picture", len(sim.frames) > n)
        if sim.frames:
            check("cleared key is black", sum(pixel(sim.frames[-1], 3 * 128 + 64, 64)) < 40)

        # Brightness
        await ws.send(json.dumps({"event": "setBrightness", "device": dev_id, "brightness": 42}))
        await asyncio.sleep(0.8)
        check("brightness reaches the device", sim.backlight == 42, sim.backlight)

        # Unknown device id is ignored without crashing
        await ws.send(json.dumps({"event": "setImage", "device": "nope", "position": 1, "image": png_url((1, 2, 3))}))
        await asyncio.sleep(0.3)
        check("plugin still running after an unknown device id", proc.poll() is None)

        # Unplug: close the device side -> plugin deregisters
        sim.stop.set()
        time.sleep(0.2)
        os.close(master)
        dereg = await expect(lambda e: e.get("event") == "deregisterDevice", timeout=8)
        check("unplugging deregisters the device", dereg is not None and dereg.get("payload") == dev_id, dereg)
        check("plugin keeps running after unplug", proc.poll() is None)

        await ws.close()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        check("plugin exits when OpenDeck closes the connection", proc.returncode is not None, proc.returncode)
        out = proc.stdout.read()
        print("---- plugin output ----")
        print("\n".join(out.strip().splitlines()[-12:]))
    print("\n%d/%d checks passed" % (sum(results), len(results)))
    return all(results)


sys.exit(0 if asyncio.run(main()) else 1)
