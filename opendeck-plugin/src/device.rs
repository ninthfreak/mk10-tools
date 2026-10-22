//! One connected MK10: serial link, handshake, the 896x304 canvas, and the key-event reader.

use crate::protocol::{self, Packet, Parser, CMD_JSON, CMD_SHOW_JPG};
use anyhow::{anyhow, Result};
use image::{imageops, DynamicImage, Rgb, RgbImage};
use serialport::SerialPort;
use std::io::{ErrorKind, Read, Write};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

pub const USB_VID: u16 = 0x1D6B; // Linux "multifunction composite gadget": generic, so we confirm by handshake
pub const USB_PID: u16 = 0x0104;

#[derive(Debug, Clone)]
pub struct Rect {
    pub x: u32,
    pub y: u32,
    pub w: u32,
    pub h: u32,
    pub row: Option<u32>,
    pub col: Option<u32>,
    pub is_key: bool,
}

#[derive(Debug, Clone)]
pub struct Layout {
    pub width: u32,
    pub height: u32,
    pub rows: u32,
    pub cols: u32,
    pub rects: Vec<Rect>,
    pub model: String,
    pub version: String,
}

impl Layout {
    /// What a real MK10 (V2.32) reported; used if the reply can't be parsed.
    pub fn mk10_default() -> Self {
        let mut rects = Vec::new();
        for r in 0..2 {
            for c in 0..5 {
                rects.push(Rect { x: c * 128, y: r * 128, w: 128, h: 128, row: Some(r), col: Some(c), is_key: true });
            }
        }
        rects.push(Rect { x: 656, y: 0, w: 240, h: 296, row: None, col: None, is_key: false });
        Layout { width: 896, height: 304, rows: 2, cols: 5, rects, model: "MK10".into(), version: "?".into() }
    }

    fn from_get_info(v: &serde_json::Value) -> Self {
        let mut l = Layout::mk10_default();
        let res = &v["result"];
        if let Some(s) = res["deviceModel"].as_str() {
            l.model = s.into();
        }
        if let Some(s) = res["deviceVersion"].as_str() {
            l.version = s.into();
        }
        let (w, h) = (res["deviceWidth"].as_u64(), res["deviceHeight"].as_u64());
        let panel = &res["devicePanel"];
        if let (Some(w), Some(h), Some(rects)) = (w, h, panel["rects"].as_array()) {
            let parsed: Vec<Rect> = rects
                .iter()
                .filter_map(|r| {
                    Some(Rect {
                        x: r["x"].as_u64()? as u32,
                        y: r["y"].as_u64()? as u32,
                        w: r["width"].as_u64()? as u32,
                        h: r["height"].as_u64()? as u32,
                        row: r["row"].as_u64().map(|x| x as u32),
                        col: r["col"].as_u64().map(|x| x as u32),
                        is_key: r["isKey"].as_bool().unwrap_or(false),
                    })
                })
                .filter(|r| r.x + r.w <= w as u32 && r.y + r.h <= h as u32)
                .collect();
            if parsed.iter().any(|r| r.is_key) {
                l.width = w as u32;
                l.height = h as u32;
                l.rows = panel["rectRows"].as_u64().unwrap_or(2) as u32;
                l.cols = panel["rectCols"].as_u64().unwrap_or(5) as u32;
                l.rects = parsed;
            }
        }
        l
    }

    pub fn key_rect(&self, position: u8) -> Option<&Rect> {
        let (row, col) = (position as u32 / self.cols, position as u32 % self.cols);
        self.rects.iter().find(|r| r.is_key && r.row == Some(row) && r.col == Some(col))
    }

    pub fn screen_rects(&self) -> impl Iterator<Item = &Rect> {
        self.rects.iter().filter(|r| !r.is_key)
    }
}

#[derive(Debug)]
pub enum HandshakeError {
    /// The device answered only in the old format: firmware too old for this plugin.
    OldFirmware(String),
    NoAnswer,
    Io(anyhow::Error),
}

impl std::fmt::Display for HandshakeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            HandshakeError::OldFirmware(v) => write!(f, "firmware {v} is too old (needs V2.32 or newer)"),
            HandshakeError::NoAnswer => write!(f, "no answer to the handshake"),
            HandshakeError::Io(e) => write!(f, "{e}"),
        }
    }
}

pub enum DeviceEvent {
    Key { row: u32, col: u32, pressed: bool },
    Disconnected(String),
}

pub struct Mk10 {
    pub id: String,
    #[allow(dead_code)]
    pub port_name: String,
    pub layout: Layout,
    writer: Mutex<Box<dyn SerialPort>>,
    canvas: Mutex<RgbImage>,
    seq: AtomicU32,
    stop: Arc<AtomicBool>,
}

fn open_port(port_name: &str) -> Result<Box<dyn SerialPort>> {
    let mut port = serialport::new(port_name, 115_200).timeout(Duration::from_millis(100)).open()?;
    let _ = port.write_data_terminal_ready(true);
    let _ = port.write_request_to_send(true);
    Ok(port)
}

/// Read until `pred` matches a packet or the deadline passes.
fn read_until(port: &mut Box<dyn SerialPort>, parser: &mut Parser, timeout: Duration, pred: impl Fn(&Packet) -> bool) -> Result<Option<Packet>> {
    let deadline = Instant::now() + timeout;
    let mut buf = [0u8; 4096];
    while Instant::now() < deadline {
        match port.read(&mut buf) {
            Ok(0) => {}
            Ok(n) => {
                for p in parser.feed(&buf[..n]) {
                    if pred(&p) {
                        return Ok(Some(p));
                    }
                }
            }
            Err(e) if e.kind() == ErrorKind::TimedOut => {}
            Err(e) => return Err(e.into()),
        }
    }
    Ok(None)
}

impl Mk10 {
    /// Open the port, do the handshake, and return the device plus a second handle for the reader thread.
    pub fn connect(port_name: &str, id: String) -> std::result::Result<(Mk10, Box<dyn SerialPort>), HandshakeError> {
        let mut port = open_port(port_name).map_err(HandshakeError::Io)?;
        let mut parser = Parser::default();
        let _ = port.clear(serialport::ClearBuffer::Input);

        let req = br#"{"method":"getInfo","parameters":{},"requestId":1}"#;
        port.write_all(&protocol::frame(1, CMD_JSON, req)).map_err(|e| HandshakeError::Io(e.into()))?;
        let _ = port.flush();
        let reply = read_until(&mut port, &mut parser, Duration::from_secs(3), |p| !p.v1 && p.cmd == CMD_JSON && p.seq == 1)
            .map_err(HandshakeError::Io)?;

        let layout = match reply {
            Some(p) => {
                let v: serde_json::Value = serde_json::from_slice(&p.payload).unwrap_or(serde_json::Value::Null);
                Layout::from_get_info(&v)
            }
            None => {
                // Old firmware only speaks the old format. Find out its version so we can say so clearly.
                port.write_all(&protocol::frame_v1(protocol::V1_FIND_DEVICE, &[0, 0, 0, 0]))
                    .map_err(|e| HandshakeError::Io(e.into()))?;
                let _ = port.flush();
                let old = read_until(&mut port, &mut parser, Duration::from_secs(2), |p| p.v1 && p.cmd == protocol::V1_FIND_DEVICE)
                    .map_err(HandshakeError::Io)?;
                return Err(match old {
                    Some(p) => {
                        let version = protocol::qmap_decode(&p.payload)
                            .and_then(|m| m.into_iter().find(|(k, _)| k == "version").map(|(_, v)| v))
                            .unwrap_or_else(|| "unknown".into());
                        HandshakeError::OldFirmware(version)
                    }
                    None => HandshakeError::NoAnswer,
                });
            }
        };

        let reader = port.try_clone().map_err(|e| HandshakeError::Io(e.into()))?;
        let canvas = RgbImage::new(layout.width, layout.height);
        Ok((
            Mk10 {
                id,
                port_name: port_name.to_string(),
                layout,
                writer: Mutex::new(port),
                canvas: Mutex::new(canvas),
                seq: AtomicU32::new(2),
                stop: Arc::new(AtomicBool::new(false)),
            },
            reader,
        ))
    }

    fn next_seq(&self) -> u32 {
        self.seq.fetch_add(1, Ordering::Relaxed)
    }

    fn write_frame(&self, cmd: u32, payload: &[u8]) -> Result<()> {
        let f = protocol::frame(self.next_seq(), cmd, payload);
        let mut w = self.writer.lock().map_err(|_| anyhow!("writer lock poisoned"))?;
        w.write_all(&f)?;
        w.flush()?;
        Ok(())
    }

    pub fn send_json(&self, method: &str, params: serde_json::Value) -> Result<()> {
        let body = serde_json::json!({ "method": method, "parameters": params });
        self.write_frame(CMD_JSON, body.to_string().as_bytes())
    }

    pub fn set_brightness(&self, level: u8) -> Result<()> {
        self.send_json("setBacklight", serde_json::json!({ "level": level.min(100) }))
    }

    /// Paint an image into one key's area (scaled to fit exactly).
    pub fn set_key_image(&self, position: u8, img: &DynamicImage) -> Result<()> {
        let r = self.layout.key_rect(position).ok_or_else(|| anyhow!("no key at position {position}"))?.clone();
        let scaled = img.resize_exact(r.w, r.h, imageops::FilterType::Triangle).to_rgb8();
        let mut c = self.canvas.lock().map_err(|_| anyhow!("canvas lock poisoned"))?;
        imageops::replace(&mut *c, &scaled, r.x as i64, r.y as i64);
        Ok(())
    }

    pub fn fill_key(&self, position: u8, color: [u8; 3]) -> Result<()> {
        let r = self.layout.key_rect(position).ok_or_else(|| anyhow!("no key at position {position}"))?.clone();
        let mut c = self.canvas.lock().map_err(|_| anyhow!("canvas lock poisoned"))?;
        fill(&mut c, &r, color);
        Ok(())
    }

    pub fn clear_keys(&self) -> Result<()> {
        let mut c = self.canvas.lock().map_err(|_| anyhow!("canvas lock poisoned"))?;
        for r in self.layout.rects.iter().filter(|r| r.is_key) {
            fill(&mut c, r, [0, 0, 0]);
        }
        Ok(())
    }

    /// Draw directly on the canvas (used by the self-test and the small screen).
    pub fn with_canvas<T>(&self, f: impl FnOnce(&mut RgbImage) -> T) -> Result<T> {
        let mut c = self.canvas.lock().map_err(|_| anyhow!("canvas lock poisoned"))?;
        Ok(f(&mut c))
    }

    pub fn render_jpeg(&self) -> Result<Vec<u8>> {
        let snapshot = self.canvas.lock().map_err(|_| anyhow!("canvas lock poisoned"))?.clone();
        let mut out = Vec::with_capacity(64 * 1024);
        let mut enc = image::codecs::jpeg::JpegEncoder::new_with_quality(&mut out, 90);
        enc.encode_image(&snapshot)?;
        Ok(out)
    }

    /// Encode the whole canvas and send it as one full-screen JPEG.
    pub fn push_frame(&self) -> Result<()> {
        let jpg = self.render_jpeg()?;
        self.write_frame(CMD_SHOW_JPG, &jpg)
    }

    pub fn stop(&self) {
        self.stop.store(true, Ordering::Relaxed);
    }

    /// Reader thread: turns device packets into events. Ends on unplug or stop().
    pub fn spawn_reader(&self, mut port: Box<dyn SerialPort>, tx: tokio::sync::mpsc::UnboundedSender<DeviceEvent>) {
        let stop = self.stop.clone();
        let id = self.id.clone();
        std::thread::Builder::new()
            .name(format!("mk10-reader-{id}"))
            .spawn(move || {
                let mut parser = Parser::default();
                let mut buf = [0u8; 4096];
                loop {
                    if stop.load(Ordering::Relaxed) || tx.is_closed() {
                        return;
                    }
                    match port.read(&mut buf) {
                        Ok(0) => {}
                        Ok(n) => {
                            for p in parser.feed(&buf[..n]) {
                                handle_packet(&p, &tx);
                            }
                        }
                        Err(e) if e.kind() == ErrorKind::TimedOut => {}
                        Err(e) => {
                            let _ = tx.send(DeviceEvent::Disconnected(e.to_string()));
                            return;
                        }
                    }
                }
            })
            .expect("spawn reader thread");
    }
}

fn handle_packet(p: &Packet, tx: &tokio::sync::mpsc::UnboundedSender<DeviceEvent>) {
    if p.v1 || p.cmd != CMD_JSON {
        return;
    }
    let Ok(v) = serde_json::from_slice::<serde_json::Value>(&p.payload) else { return };
    if v["method"] == "keyStateChanged" {
        let prm = &v["parameters"];
        if let (Some(row), Some(col)) = (prm["row"].as_u64(), prm["col"].as_u64()) {
            let pressed = prm["pressed"].as_bool().unwrap_or(false);
            let _ = tx.send(DeviceEvent::Key { row: row as u32, col: col as u32, pressed });
        }
    } else if v["success"] == false {
        log::warn!("device reported an error: {}", v["errorString"]);
    }
}

pub fn fill(img: &mut RgbImage, r: &Rect, color: [u8; 3]) {
    for y in r.y..(r.y + r.h).min(img.height()) {
        for x in r.x..(r.x + r.w).min(img.width()) {
            img.put_pixel(x, y, Rgb(color));
        }
    }
}

/// USB serial ports that look like an MK10 (or MK10_PORT for testing).
pub fn find_ports() -> Vec<(String, String)> {
    if let Ok(p) = std::env::var("MK10_PORT") {
        return vec![(p.clone(), p)];
    }
    let Ok(ports) = serialport::available_ports() else { return Vec::new() };
    ports
        .into_iter()
        .filter_map(|p| match p.port_type {
            serialport::SerialPortType::UsbPort(u) if u.vid == USB_VID && u.pid == USB_PID => {
                let serial = u.serial_number.clone().unwrap_or_else(|| p.port_name.clone());
                Some((p.port_name, serial))
            }
            _ => None,
        })
        .collect()
}
