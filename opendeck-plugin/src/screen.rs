//! What the small screen shows: a label and a clock (OpenDeck has no slot for it).

use crate::device::{fill, Mk10, Rect};
use crate::font;

pub fn clock_text() -> String {
    chrono::Local::now().format("%H:%M").to_string()
}

pub fn draw_idle(dev: &Mk10) {
    let rects: Vec<Rect> = dev.layout.screen_rects().cloned().collect();
    let time = clock_text();
    let _ = dev.with_canvas(|c| {
        for r in &rects {
            fill(c, r, [0, 0, 0]);
            let cx = (r.x + r.w / 2) as i64;
            let top = r.y as i64;
            // Content stays away from the rounded corners.
            font::draw_text_centered(c, "OPENDECK", cx, top + 70, 3, [230, 120, 0]);
            font::draw_text_centered(c, &time, cx, top + 120, 7, [0, 150, 210]);
            font::draw_text_centered(c, "MK10", cx, top + 205, 3, [150, 150, 150]);
        }
    });
}
