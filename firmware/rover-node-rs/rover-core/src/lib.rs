//! rover-core — pure, host-testable logic shared by the ESP32 rover firmware:
//! the NDJSON telemetry frame schema, OTA version comparison, and the
//! newest-wins TCP broadcaster. No ESP / hardware dependencies.

use std::collections::BTreeMap;

use serde::Serialize;

#[derive(Serialize)]
pub struct Sensors {
    #[serde(rename = "camPresent")]
    pub cam_present: bool,
    #[serde(rename = "camFps")]
    pub cam_fps: u32,
}

#[derive(Serialize)]
pub struct Net {
    pub wifi: String,
    pub rssi: i32,
    pub ip: String,
    pub push: String,
}

#[derive(Serialize)]
pub struct Board {
    pub up_s: u64,
    pub heap_b: i64,
    pub psram_b: i64,
}

#[derive(Serialize)]
pub struct Frame {
    pub t: u64,
    pub fw: String,
    pub dev: String,
    pub pins: BTreeMap<String, i64>,
    pub sensors: Sensors,
    pub net: Net,
    pub board: Board,
}

impl Frame {
    pub fn new(t_ms: u64, fw: &str, dev: &str) -> Self {
        Frame {
            t: t_ms,
            fw: fw.to_string(),
            dev: dev.to_string(),
            pins: BTreeMap::new(),
            sensors: Sensors { cam_present: false, cam_fps: 0 },
            net: Net { wifi: "off".into(), rssi: 0, ip: "0.0.0.0".into(), push: "n/a".into() },
            board: Board { up_s: 0, heap_b: 0, psram_b: 0 },
        }
    }

    pub fn with_camera(mut self, present: bool, fps: u32) -> Self {
        self.sensors = Sensors { cam_present: present, cam_fps: fps };
        self
    }

    pub fn with_net(mut self, wifi: &str, rssi: i32, ip: &str, push: &str) -> Self {
        self.net = Net { wifi: wifi.into(), rssi, ip: ip.into(), push: push.into() };
        self
    }

    pub fn with_board(mut self, up_s: u64, heap_b: i64, psram_b: i64) -> Self {
        self.board = Board { up_s, heap_b, psram_b };
        self
    }

    /// One line of NDJSON, no trailing newline (caller adds the delimiter).
    pub fn to_ndjson(&self) -> String {
        serde_json::to_string(self).expect("Frame serializes")
    }
}

/// Parse a strict "MAJOR.MINOR.PATCH" string into a tuple. None if malformed.
fn parse_semver(s: &str) -> Option<(u32, u32, u32)> {
    let mut it = s.trim().split('.');
    let a = it.next()?.parse().ok()?;
    let b = it.next()?.parse().ok()?;
    let c = it.next()?.parse().ok()?;
    if it.next().is_some() {
        return None; // more than 3 components
    }
    Some((a, b, c))
}

/// True iff `latest` is a strictly newer semver than `current`.
/// Any malformed input returns false (fail safe: don't OTA on garbage).
pub fn version_is_newer(latest: &str, current: &str) -> bool {
    match (parse_semver(latest), parse_semver(current)) {
        (Some(l), Some(c)) => l > c,
        _ => false,
    }
}

#[cfg(test)]
mod frame_tests {
    use super::*;

    fn sample() -> Frame {
        Frame::new(12345, "r1.0.0", "rover-node-1")
            .with_camera(true, 0)
            .with_net("up", -58, "192.168.1.50", "ok")
            .with_board(1234, 210000, 3_800_000)
    }

    #[test]
    fn ndjson_has_required_keys_and_no_newline() {
        let line = sample().to_ndjson();
        assert!(!line.contains('\n'), "frame line must not contain a newline");
        let v: serde_json::Value = serde_json::from_str(&line).unwrap();
        for k in ["t", "fw", "dev", "pins", "sensors", "net", "board"] {
            assert!(v.get(k).is_some(), "missing key {k}");
        }
        assert_eq!(v["pins"], serde_json::json!({}), "pins is an empty object");
        assert_eq!(v["sensors"]["camPresent"], serde_json::json!(true));
        assert_eq!(v["board"]["psram_b"], serde_json::json!(3_800_000));
        assert_eq!(v["dev"], serde_json::json!("rover-node-1"));
    }
}

#[cfg(test)]
mod version_tests {
    use super::version_is_newer;

    #[test]
    fn compares_semver() {
        assert!(version_is_newer("1.0.1", "1.0.0"));
        assert!(version_is_newer("1.1.0", "1.0.9"));
        assert!(version_is_newer("2.0.0", "1.9.9"));
        assert!(!version_is_newer("1.0.0", "1.0.0"));
        assert!(!version_is_newer("1.0.0", "1.0.1"));
    }

    #[test]
    fn malformed_is_not_newer() {
        assert!(!version_is_newer("garbage", "1.0.0"));
        assert!(!version_is_newer("1.0", "1.0.0"));
        assert!(!version_is_newer("", "1.0.0"));
    }
}
