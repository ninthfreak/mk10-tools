//! OpenDeck side: device discovery, registration, images in, key presses out.

use crate::device::{self, DeviceEvent, HandshakeError, Mk10};
use crate::screen;
use openaction::{
    ActionEventHandler, EventHandlerResult, GlobalEventHandler, OutboundEventManager, SetBrightnessEvent,
    SetImageEvent, SystemDidWakeUpEvent, OUTBOUND_EVENT_MANAGER,
};
use std::collections::{HashMap, HashSet};
use std::sync::{Arc, LazyLock};
use std::time::{Duration, Instant};
use tokio::sync::{Notify, RwLock};
use tokio_util::sync::CancellationToken;

/// Must match "DeviceNamespace" in manifest.json (2 characters, unique among plugins).
pub const NAMESPACE: &str = "wk";

struct Entry {
    dev: Arc<Mk10>,
    dirty: Arc<Notify>,
    token: CancellationToken,
}

static DEVICES: LazyLock<RwLock<HashMap<String, Arc<Entry>>>> = LazyLock::new(|| RwLock::new(HashMap::new()));
/// Ports that are connected or currently connecting.
static BUSY: LazyLock<std::sync::Mutex<HashSet<String>>> = LazyLock::new(|| std::sync::Mutex::new(HashSet::new()));
/// Ports with firmware too old to use; forgotten when the device is unplugged.
static REJECTED: LazyLock<std::sync::Mutex<HashMap<String, String>>> =
    LazyLock::new(|| std::sync::Mutex::new(HashMap::new()));

pub struct Global;

impl GlobalEventHandler for Global {
    async fn plugin_ready(&self, _outbound: &mut OutboundEventManager) -> EventHandlerResult {
        log::info!("opendeck-mk10 {} ready; looking for MK10 devices", env!("CARGO_PKG_VERSION"));
        tokio::spawn(watcher());
        Ok(())
    }

    async fn set_image(&self, event: SetImageEvent, _outbound: &mut OutboundEventManager) -> EventHandlerResult {
        if event.controller.as_deref() == Some("Encoder") {
            return Ok(());
        }
        let entry = DEVICES.read().await.get(&event.device).cloned();
        let Some(entry) = entry else { return Ok(()) };
        let result = match (event.position, event.image) {
            (Some(pos), Some(url)) => match decode_data_url(&url) {
                Ok(img) => entry.dev.set_key_image(pos, &img),
                Err(e) => {
                    log::warn!("couldn't decode image for key {pos}: {e}");
                    Ok(())
                }
            },
            (Some(pos), None) => entry.dev.fill_key(pos, [0, 0, 0]),
            (None, None) => entry.dev.clear_keys(),
            _ => Ok(()),
        };
        if let Err(e) = result {
            log::warn!("set_image: {e}");
        }
        entry.dirty.notify_one();
        Ok(())
    }

    async fn set_brightness(&self, event: SetBrightnessEvent, _outbound: &mut OutboundEventManager) -> EventHandlerResult {
        let entry = DEVICES.read().await.get(&event.device).cloned();
        if let Some(entry) = entry {
            let dev = entry.dev.clone();
            let level = event.brightness;
            tokio::task::spawn_blocking(move || {
                if let Err(e) = dev.set_brightness(level) {
                    log::warn!("set brightness: {e}");
                }
            });
        }
        Ok(())
    }

    async fn system_did_wake_up(&self, _event: SystemDidWakeUpEvent, outbound: &mut OutboundEventManager) -> EventHandlerResult {
        let entries: Vec<(String, Arc<Entry>)> =
            DEVICES.read().await.iter().map(|(k, v)| (k.clone(), v.clone())).collect();
        for (id, entry) in entries {
            entry.dirty.notify_one();
            let _ = outbound.rerender_images(id).await;
        }
        Ok(())
    }
}

pub struct Actions;
impl ActionEventHandler for Actions {}

fn decode_data_url(url: &str) -> anyhow::Result<image::DynamicImage> {
    let parsed = data_url::DataUrl::process(url).map_err(|e| anyhow::anyhow!("bad data URL: {e:?}"))?;
    let (bytes, _) = parsed.decode_to_vec().map_err(|e| anyhow::anyhow!("bad data URL body: {e:?}"))?;
    Ok(image::load_from_memory(&bytes)?)
}

fn sanitize(s: &str) -> String {
    let t: String = s.chars().filter(|c| c.is_ascii_alphanumeric()).collect();
    if t.is_empty() { "mk10".into() } else { t }
}

async fn watcher() {
    loop {
        let found = tokio::task::spawn_blocking(device::find_ports).await.unwrap_or_default();
        let present: HashSet<String> = found.iter().map(|(p, _)| p.clone()).collect();
        REJECTED.lock().unwrap().retain(|p, _| present.contains(p));
        for (port, serial) in found {
            if REJECTED.lock().unwrap().contains_key(&port) {
                continue;
            }
            if !BUSY.lock().unwrap().insert(port.clone()) {
                continue;
            }
            tokio::spawn(connect_and_run(port, serial));
        }
        tokio::time::sleep(Duration::from_secs(2)).await;
    }
}

async fn connect_and_run(port: String, serial: String) {
    let id = format!("{NAMESPACE}-{}", sanitize(&serial));
    let (p2, id2) = (port.clone(), id.clone());
    let (dev, reader) = match tokio::task::spawn_blocking(move || Mk10::connect(&p2, id2)).await {
        Ok(Ok(x)) => x,
        Ok(Err(HandshakeError::OldFirmware(v))) => {
            log::error!("MK10 on {port}: firmware {v} is too old for this plugin (needs V2.32+). Update it, then replug.");
            REJECTED.lock().unwrap().insert(port.clone(), v);
            BUSY.lock().unwrap().remove(&port);
            return;
        }
        Ok(Err(e)) => {
            log::warn!("MK10 on {port}: {e}; will retry");
            tokio::time::sleep(Duration::from_secs(5)).await;
            BUSY.lock().unwrap().remove(&port);
            return;
        }
        Err(e) => {
            log::error!("MK10 on {port}: connect task failed: {e}");
            BUSY.lock().unwrap().remove(&port);
            return;
        }
    };
    let dev = Arc::new(dev);
    let (rows, cols) = (dev.layout.rows, dev.layout.cols);
    log::info!(
        "MK10 {id} connected on {port}: model {}, firmware {}, canvas {}x{}, {}x{} keys",
        dev.layout.model, dev.layout.version, dev.layout.width, dev.layout.height, rows, cols
    );

    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
    dev.spawn_reader(reader, tx);
    let entry = Arc::new(Entry { dev: dev.clone(), dirty: Arc::new(Notify::new()), token: CancellationToken::new() });
    let _ = dev.clear_keys();
    screen::draw_idle(&dev);
    DEVICES.write().await.insert(id.clone(), entry.clone());
    if let Some(o) = OUTBOUND_EVENT_MANAGER.lock().await.as_mut() {
        let _ = o.register_device(id.clone(), "Waveshare MK10".into(), rows as u8, cols as u8, 0, 0).await;
        let _ = o.rerender_images(id.clone()).await;
    }
    entry.dirty.notify_one();
    let flush = tokio::spawn(flush_task(entry.clone()));
    let clock = tokio::spawn(clock_task(entry.clone()));

    while let Some(ev) = rx.recv().await {
        match ev {
            DeviceEvent::Key { row, col, pressed } => {
                if row >= rows || col >= cols {
                    continue;
                }
                let pos = (row * cols + col) as u8;
                if let Some(o) = OUTBOUND_EVENT_MANAGER.lock().await.as_mut() {
                    let _ = if pressed { o.key_down(id.clone(), pos).await } else { o.key_up(id.clone(), pos).await };
                }
                if !pressed {
                    // In case the device's own theme redrew anything on the press, re-send our picture.
                    let d = entry.dirty.clone();
                    tokio::spawn(async move {
                        tokio::time::sleep(Duration::from_millis(150)).await;
                        d.notify_one();
                    });
                }
            }
            DeviceEvent::Disconnected(why) => {
                log::info!("MK10 {id} on {port} disconnected: {why}");
                break;
            }
        }
    }

    entry.token.cancel();
    dev.stop();
    let _ = flush.await;
    let _ = clock.await;
    DEVICES.write().await.remove(&id);
    if let Some(o) = OUTBOUND_EVENT_MANAGER.lock().await.as_mut() {
        let _ = o.deregister_device(id.clone()).await;
    }
    BUSY.lock().unwrap().remove(&port);
}

/// Send the canvas after changes settle (30 ms quiet, at most 100 ms after the first change).
async fn flush_task(e: Arc<Entry>) {
    loop {
        tokio::select! {
            _ = e.dirty.notified() => {}
            _ = e.token.cancelled() => return,
        }
        let first = Instant::now();
        loop {
            let remaining = Duration::from_millis(100).saturating_sub(first.elapsed());
            if remaining.is_zero() {
                break;
            }
            tokio::select! {
                _ = e.dirty.notified() => continue,
                _ = tokio::time::sleep(Duration::from_millis(30).min(remaining)) => break,
                _ = e.token.cancelled() => return,
            }
        }
        let dev = e.dev.clone();
        match tokio::task::spawn_blocking(move || dev.push_frame()).await {
            Ok(Ok(())) => {}
            Ok(Err(err)) => log::warn!("sending picture failed: {err}"),
            Err(err) => log::warn!("sending picture task failed: {err}"),
        }
    }
}

/// Keep the small screen's clock current.
async fn clock_task(e: Arc<Entry>) {
    let mut last = String::new();
    loop {
        let now = screen::clock_text();
        if now != last {
            screen::draw_idle(&e.dev);
            e.dirty.notify_one();
            last = now;
        }
        tokio::select! {
            _ = tokio::time::sleep(Duration::from_secs(5)) => {}
            _ = e.token.cancelled() => return,
        }
    }
}
