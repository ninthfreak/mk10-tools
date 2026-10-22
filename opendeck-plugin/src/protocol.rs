//! MK10 serial protocol (reverse-engineered from the device firmware V2.32).
//!
//! Newer ("V2") frame, little-endian:
//!   A1 A5 5A 5E | u32 seq | u32 cmd | u32 len | u32 crc32(len bytes) | payload | u32 crc32(payload)
//!   cmd 100 = full-screen JPEG (must match the canvas size exactly), cmd 101 = JSON.
//! Older ("V1") frame, only used here to recognise old firmware:
//!   "AA551234 FIXEDCMDHEAD " | u32 type | u32 cmd | u32 len | u32 crc32(payload) | payload

pub const MAGIC: [u8; 4] = [0xA1, 0xA5, 0x5A, 0x5E];
pub const MAGIC_V1: &[u8] = b"AA551234 FIXEDCMDHEAD ";
pub const CMD_SHOW_JPG: u32 = 100;
pub const CMD_JSON: u32 = 101;
pub const V1_FIND_DEVICE: u32 = 0;
const HEADER: usize = 20;
const HEADER_V1: usize = 22 + 16;
const MAX_PAYLOAD: usize = 16 * 1024 * 1024;

pub fn crc32(data: &[u8]) -> u32 {
    crc32fast::hash(data)
}

pub fn frame(seq: u32, cmd: u32, payload: &[u8]) -> Vec<u8> {
    let n = payload.len() as u32;
    let mut v = Vec::with_capacity(payload.len() + 24);
    v.extend_from_slice(&MAGIC);
    v.extend_from_slice(&seq.to_le_bytes());
    v.extend_from_slice(&cmd.to_le_bytes());
    v.extend_from_slice(&n.to_le_bytes());
    v.extend_from_slice(&crc32(&n.to_le_bytes()).to_le_bytes());
    v.extend_from_slice(payload);
    v.extend_from_slice(&crc32(payload).to_le_bytes());
    v
}

pub fn frame_v1(cmd: u32, payload: &[u8]) -> Vec<u8> {
    let mut v = Vec::with_capacity(payload.len() + HEADER_V1);
    v.extend_from_slice(MAGIC_V1);
    v.extend_from_slice(&0u32.to_le_bytes());
    v.extend_from_slice(&cmd.to_le_bytes());
    v.extend_from_slice(&(payload.len() as u32).to_le_bytes());
    v.extend_from_slice(&crc32(payload).to_le_bytes());
    v.extend_from_slice(payload);
    v
}

#[derive(Debug, Clone, PartialEq)]
pub struct Packet {
    pub v1: bool,
    pub seq: u32,
    pub cmd: u32,
    pub payload: Vec<u8>,
}

fn find(hay: &[u8], needle: &[u8]) -> Option<usize> {
    if hay.len() < needle.len() {
        return None;
    }
    hay.windows(needle.len()).position(|w| w == needle)
}

fn u32le(b: &[u8], at: usize) -> u32 {
    u32::from_le_bytes([b[at], b[at + 1], b[at + 2], b[at + 3]])
}

/// Feed raw bytes in any chunking; get complete packets back.
#[derive(Default)]
pub struct Parser {
    buf: Vec<u8>,
}

impl Parser {
    pub fn feed(&mut self, data: &[u8]) -> Vec<Packet> {
        self.buf.extend_from_slice(data);
        let mut out = Vec::new();
        loop {
            let i = match (find(&self.buf, &MAGIC), find(&self.buf, MAGIC_V1)) {
                (Some(a), Some(b)) => a.min(b),
                (Some(a), None) | (None, Some(a)) => a,
                (None, None) => {
                    // keep only a tail that could be the start of a marker split across reads
                    let max = (MAGIC_V1.len() - 1).min(self.buf.len());
                    let mut keep = 0;
                    for k in (1..=max).rev() {
                        let tail = &self.buf[self.buf.len() - k..];
                        if MAGIC.starts_with(tail) || MAGIC_V1.starts_with(tail) {
                            keep = k;
                            break;
                        }
                    }
                    let drop = self.buf.len() - keep;
                    self.buf.drain(..drop);
                    return out;
                }
            };
            if i > 0 {
                self.buf.drain(..i);
                continue;
            }
            if self.buf.starts_with(&MAGIC) {
                if self.buf.len() < HEADER {
                    return out;
                }
                let (seq, cmd, n, ncrc) = (
                    u32le(&self.buf, 4),
                    u32le(&self.buf, 8),
                    u32le(&self.buf, 12) as usize,
                    u32le(&self.buf, 16),
                );
                if ncrc != crc32(&(n as u32).to_le_bytes()) || n > MAX_PAYLOAD {
                    self.buf.drain(..1);
                    continue;
                }
                let total = HEADER + n + 4;
                if self.buf.len() < total {
                    return out;
                }
                let payload = self.buf[HEADER..HEADER + n].to_vec();
                let pcrc = u32le(&self.buf, HEADER + n);
                self.buf.drain(..total);
                if pcrc == crc32(&payload) {
                    out.push(Packet { v1: false, seq, cmd, payload });
                }
            } else {
                if self.buf.len() < HEADER_V1 {
                    return out;
                }
                let (ptype, cmd, n, pcrc) = (
                    u32le(&self.buf, 22),
                    u32le(&self.buf, 26),
                    u32le(&self.buf, 30) as usize,
                    u32le(&self.buf, 34),
                );
                if n > MAX_PAYLOAD || ptype > 3 {
                    self.buf.drain(..1);
                    continue;
                }
                let total = HEADER_V1 + n;
                if self.buf.len() < total {
                    return out;
                }
                let payload = self.buf[HEADER_V1..total].to_vec();
                if pcrc != crc32(&payload) {
                    self.buf.drain(..1);
                    continue;
                }
                self.buf.drain(..total);
                out.push(Packet { v1: true, seq: ptype, cmd, payload });
            }
        }
    }
}

/// Qt5 QDataStream QMap<QString,QString> -> pairs (used to read the old-firmware version).
pub fn qmap_decode(b: &[u8]) -> Option<Vec<(String, String)>> {
    fn be(b: &[u8], p: usize) -> Option<u32> {
        b.get(p..p + 4).map(|s| u32::from_be_bytes([s[0], s[1], s[2], s[3]]))
    }
    fn qstr(b: &[u8], p: &mut usize) -> Option<String> {
        let n = be(b, *p)?;
        *p += 4;
        if n == 0xFFFF_FFFF {
            return Some(String::new());
        }
        let n = n as usize;
        if n % 2 != 0 {
            return None;
        }
        let raw = b.get(*p..*p + n)?;
        *p += n;
        let units: Vec<u16> = raw.chunks(2).map(|c| u16::from_be_bytes([c[0], c[1]])).collect();
        String::from_utf16(&units).ok()
    }
    let count = be(b, 0)? as usize;
    if count > 1000 {
        return None;
    }
    let mut p = 4;
    let mut out = Vec::new();
    for _ in 0..count {
        let k = qstr(b, &mut p)?;
        let v = qstr(b, &mut p)?;
        out.push((k, v));
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn crc_matches_standard() {
        assert_eq!(crc32(b"123456789"), 0xCBF4_3926);
    }

    #[test]
    fn frame_round_trip_any_chunking() {
        let a = frame(7, CMD_JSON, br#"{"method":"getInfo","parameters":{}}"#);
        let b = frame_v1(8, &[0, 0, 0, 0]);
        let mut stream = b"AT\r\n".to_vec();
        stream.extend_from_slice(&a);
        stream.extend_from_slice(&b);
        stream.extend_from_slice(b"junk");
        let mut p = Parser::default();
        let mut got = Vec::new();
        for byte in &stream {
            got.extend(p.feed(&[*byte]));
        }
        assert_eq!(got.len(), 2);
        assert!(!got[0].v1 && got[0].seq == 7 && got[0].cmd == CMD_JSON);
        assert!(got[1].v1 && got[1].cmd == 8);
    }

    #[test]
    fn matches_python_reference_frame() {
        // Same bytes the Python probe sends for getInfo seq=1 (verified against a real MK10).
        let f = frame(1, CMD_JSON, br#"{"method":"getInfo","parameters":{},"requestId":1}"#);
        let hex: String = f.iter().map(|b| format!("{b:02x}")).collect();
        assert!(hex.starts_with("a1a55a5e01000000650000003200000036ef667b"));
        assert!(hex.ends_with("0673419e"));
    }

    #[test]
    fn corrupt_frame_is_dropped() {
        let mut a = frame(1, CMD_JSON, b"{}");
        let last = a.len() - 1;
        a[last] ^= 0xFF;
        a.extend_from_slice(&frame(2, CMD_JSON, b"{}"));
        let got = Parser::default().feed(&a);
        assert_eq!(got.len(), 1);
        assert_eq!(got[0].seq, 2);
    }

    #[test]
    fn qmap_decodes_real_version_reply() {
        // payload of a real GET_VERSION reply from an MK10 on V2.29
        let hex = "000000010000000e00760065007200730069006f006e0000000a00560032002e00320039";
        let b: Vec<u8> = (0..hex.len()).step_by(2).map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap()).collect();
        assert_eq!(qmap_decode(&b), Some(vec![("version".into(), "V2.29".into())]));
    }
}
