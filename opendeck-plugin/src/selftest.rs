//! `--test`: check the plugin's own device code against a real MK10, without OpenDeck.

use crate::device::{self, fill, DeviceEvent, Mk10, Rect};
use crate::{font, screen};
use anyhow::{anyhow, bail, Result};
use std::time::{Duration, Instant};

const BLUE: [u8; 3] = [0, 90, 200];
const ORANGE: [u8; 3] = [230, 120, 0];
const WHITE: [u8; 3] = [255, 255, 255];

fn draw_key(dev: &Mk10, pos: u8, pressed: bool) {
    let Some(r) = dev.layout.key_rect(pos).cloned() else { return };
    let _ = dev.with_canvas(|c| {
        let bg = if pressed { WHITE } else if pos % 2 == 0 { BLUE } else { ORANGE };
        fill(c, &r, bg);
        fill(c, &Rect { x: r.x + 6, y: r.y + 6, w: 16, h: 16, row: None, col: None, is_key: true }, if pressed { BLUE } else { WHITE });
        let label = (pos + 1).to_string();
        let fg = if pressed { [0, 0, 0] } else { WHITE };
        font::draw_text_centered(c, &label, (r.x + r.w / 2) as i64, (r.y + r.h / 2) as i64 - 21, 6, fg);
    });
}

pub fn run(port_arg: Option<String>) -> Result<()> {
    let port = match port_arg {
        Some(p) => p,
        None => {
            let found = device::find_ports();
            match found.first() {
                Some((p, _)) => p.clone(),
                None => {
                    println!("No MK10 found (looking for USB ID 1d6b:0104). Serial ports present:");
                    for p in serialport::available_ports().unwrap_or_default() {
                        println!("  {}  {:?}", p.port_name, p.port_type);
                    }
                    bail!("pass the port explicitly, e.g. --test COM6");
                }
            }
        }
    };
    println!("Connecting to {port} ...");
    let (dev, reader) = Mk10::connect(&port, "selftest".into()).map_err(|e| anyhow!("{e}"))?;
    let l = &dev.layout;
    println!("Connected: model {}, firmware {}, canvas {}x{}, {}x{} keys", l.model, l.version, l.width, l.height, l.rows, l.cols);

    let keys = (l.rows * l.cols) as u8;
    for pos in 0..keys {
        draw_key(&dev, pos, false);
    }
    screen::draw_idle(&dev);
    let t = Instant::now();
    dev.push_frame()?;
    println!("Sent a test picture ({} ms). Keys should show 1-10 on blue/orange, the small screen a clock.", t.elapsed().as_millis());
    println!("Press keys for 20 seconds: each key turns white while held.");

    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
    dev.spawn_reader(reader, tx);
    let end = Instant::now() + Duration::from_secs(20);
    let mut presses = 0;
    while Instant::now() < end {
        match rx.try_recv() {
            Ok(DeviceEvent::Key { row, col, pressed }) => {
                if row >= l.rows || col >= l.cols {
                    continue;
                }
                let pos = (row * l.cols + col) as u8;
                println!("  key {} (row {row}, col {col}) {}", pos + 1, if pressed { "DOWN" } else { "UP" });
                if pressed {
                    presses += 1;
                }
                draw_key(&dev, pos, pressed);
                let t = Instant::now();
                dev.push_frame()?;
                log::debug!("frame sent in {} ms", t.elapsed().as_millis());
            }
            Ok(DeviceEvent::Disconnected(why)) => bail!("device disconnected: {why}"),
            Err(tokio::sync::mpsc::error::TryRecvError::Empty) => std::thread::sleep(Duration::from_millis(10)),
            Err(e) => bail!("reader stopped: {e}"),
        }
    }
    dev.stop();
    println!("Done: {presses} key presses seen. If the keys and clock looked right, the plugin can drive this MK10.");
    Ok(())
}
