# Rover Node (Rust / ESP32-WROVER) — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Rust firmware for the ESP32-WROVER camera board with backbone parity to the C++ garden node — WiFi, ~1Hz NDJSON telemetry over serial + TCP:8766, OAuth2/HTTPS cloud push, and esp_ota updates — with the camera stubbed.

**Architecture:** A standalone host-testable `rover-core` crate owns the NDJSON frame schema, OTA version comparison, and the newest-wins TCP broadcaster (all using Rust `std`, which ESP-IDF provides). A separate `rover-firmware` crate (esp-idf-svc, Xtensa) is thin glue: WiFi/cloud/OTA via ESP-IDF, telemetry via `rover-core`. The frame schema is byte-compatible with the existing `board-tui`.

**Tech Stack:** Rust (host `cargo` 1.93 for `rover-core`; `espup`/esp-idf-svc for `rover-firmware`), serde/serde_json, GitHub Actions.

---

## File structure

```
firmware/rover-node-rs/
  README.md
  rover-core/                      # standalone crate, pure std, HOST-tested
    Cargo.toml                     # serde, serde_json
    src/lib.rs                     # Frame + to_ndjson, version_is_newer, TcpBroadcaster
  rover-firmware/                  # esp-idf-svc binary (Xtensa) — CI-built
    Cargo.toml                     # esp-idf-svc/hal/sys, anyhow, log, rover-core(path)
    rust-toolchain.toml            # channel = "esp"
    .cargo/config.toml             # target xtensa-esp32-espidf + flags
    sdkconfig.defaults             # PSRAM + ota
    partitions.csv                 # factory + ota_0/ota_1 + otadata
    build.rs                       # embuild
    src/config.rs                  # ids, endpoints, intervals, flags, FIRMWARE_VERSION
    src/wifi.rs                    # EspWifi STA connect
    src/camera.rs                  # STUB probe()
    src/cloud.rs                   # OAuth2 token + HTTPS POST /ingest
    src/ota.rs                     # periodic version check + esp_ota apply
    src/main.rs                    # orchestration loop (uses rover-core)
.github/workflows/rover-firmware.yml
tools/board-tui/tests/
  fixtures/rover_sample.ndjson     # cross-language contract fixture
  test_rover_frame.py             # parse_frame accepts the rover frame
```

Host tests: `cd firmware/rover-node-rs/rover-core && cargo test`
TUI tests: `cd tools/board-tui && python3 -m pytest -q`

---

### Task 1: `rover-core` — Frame + NDJSON serialization

**Files:**
- Create: `firmware/rover-node-rs/rover-core/Cargo.toml`
- Create: `firmware/rover-node-rs/rover-core/src/lib.rs`

- [ ] **Step 1: Create the crate manifest**

`firmware/rover-node-rs/rover-core/Cargo.toml`:
```toml
[package]
name = "rover-core"
version = "0.1.0"
edition = "2021"

[dependencies]
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

- [ ] **Step 2: Write the failing test (inside lib.rs)**

Create `firmware/rover-node-rs/rover-core/src/lib.rs` with ONLY the test module first:
```rust
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
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd firmware/rover-node-rs/rover-core && cargo test 2>&1 | tail -20`
Expected: compile error — `Frame` not found.

- [ ] **Step 4: Implement Frame above the test module**

Prepend to `src/lib.rs` (keep the test module at the bottom):
```rust
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
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd firmware/rover-node-rs/rover-core && cargo test 2>&1 | tail -10`
Expected: `test frame_tests::ndjson_has_required_keys_and_no_newline ... ok` (1 passed).

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/gardencontroller
git add firmware/rover-node-rs/rover-core/
git commit -m "feat(rover-core): NDJSON telemetry Frame (board-tui compatible schema)"
```

---

### Task 2: `rover-core` — OTA `version_is_newer`

**Files:**
- Modify: `firmware/rover-node-rs/rover-core/src/lib.rs`

- [ ] **Step 1: Write the failing test**

Add a second test module at the bottom of `src/lib.rs`:
```rust
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd firmware/rover-node-rs/rover-core && cargo test 2>&1 | tail -15`
Expected: compile error — `version_is_newer` not found.

- [ ] **Step 3: Implement `version_is_newer`**

Add to `src/lib.rs` (above the test modules):
```rust
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd firmware/rover-node-rs/rover-core && cargo test 2>&1 | tail -10`
Expected: 3 tests pass (frame + 2 version tests).

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/gardencontroller
git add firmware/rover-node-rs/rover-core/src/lib.rs
git commit -m "feat(rover-core): version_is_newer for OTA gate (fail-safe on malformed)"
```

---

### Task 3: `rover-core` — newest-wins `TcpBroadcaster` (std::net, host-tested)

**Files:**
- Modify: `firmware/rover-node-rs/rover-core/src/lib.rs`

- [ ] **Step 1: Write the failing loopback test**

Add a test module at the bottom of `src/lib.rs`:
```rust
#[cfg(test)]
mod broadcaster_tests {
    use super::TcpBroadcaster;
    use std::io::{BufRead, BufReader};
    use std::net::TcpStream;
    use std::time::Duration;

    #[test]
    fn delivers_line_to_connected_client() {
        let mut b = TcpBroadcaster::bind("127.0.0.1:0").unwrap();
        let port = b.local_port();
        let client = TcpStream::connect(("127.0.0.1", port)).unwrap();
        client.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
        let mut reader = BufReader::new(client);

        // First broadcast accepts the pending client; second is actually delivered.
        b.broadcast("first");
        b.broadcast("second");

        let mut got = String::new();
        reader.read_line(&mut got).unwrap();
        assert!(got.starts_with("first") || got.starts_with("second"), "got: {got:?}");
    }

    #[test]
    fn newest_client_wins() {
        let mut b = TcpBroadcaster::bind("127.0.0.1:0").unwrap();
        let port = b.local_port();

        let c1 = TcpStream::connect(("127.0.0.1", port)).unwrap();
        c1.set_read_timeout(Some(Duration::from_millis(500))).unwrap();
        b.broadcast("x");                // adopt c1

        let c2 = TcpStream::connect(("127.0.0.1", port)).unwrap();
        c2.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
        b.broadcast("y");                // adopt c2 (newest wins), deliver to c2
        b.broadcast("z");

        let mut r2 = BufReader::new(c2);
        let mut got = String::new();
        r2.read_line(&mut got).unwrap();
        assert!(!got.is_empty(), "newest client should receive frames");
    }
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd firmware/rover-node-rs/rover-core && cargo test 2>&1 | tail -15`
Expected: compile error — `TcpBroadcaster` not found.

- [ ] **Step 3: Implement `TcpBroadcaster`**

Add to `src/lib.rs` (above the test modules):
```rust
use std::io::Write;
use std::net::{TcpListener, TcpStream};

/// A non-blocking, single-client (newest-wins) line broadcaster.
///
/// `broadcast` accepts at most one pending connection per call (replacing any
/// current client) and writes the line + '\n' to the current client. A write
/// error drops the client; the next connection supersedes it. Designed to be
/// called once per telemetry tick from a single thread. Uses only std::net,
/// which ESP-IDF provides, so the logic is identical on host and device.
pub struct TcpBroadcaster {
    listener: TcpListener,
    client: Option<TcpStream>,
}

impl TcpBroadcaster {
    pub fn bind(addr: &str) -> std::io::Result<Self> {
        let listener = TcpListener::bind(addr)?;
        listener.set_nonblocking(true)?;
        Ok(Self { listener, client: None })
    }

    pub fn local_port(&self) -> u16 {
        self.listener.local_addr().map(|a| a.port()).unwrap_or(0)
    }

    pub fn broadcast(&mut self, line: &str) {
        // Adopt a newly-connected client (newest-wins).
        match self.listener.accept() {
            Ok((stream, _)) => {
                let _ = stream.set_nonblocking(true);
                self.client = Some(stream);
            }
            Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => {}
            Err(_) => {}
        }
        // Write to the current client; drop it on error.
        if let Some(stream) = self.client.as_mut() {
            if writeln!(stream, "{line}").is_err() {
                self.client = None;
            }
        }
    }
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd firmware/rover-node-rs/rover-core && cargo test 2>&1 | tail -12`
Expected: all tests pass (frame + version + 2 broadcaster). If a broadcaster test
is flaky on the nonblocking accept timing, re-run; the `set_read_timeout` bounds it.

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/gardencontroller
git add firmware/rover-node-rs/rover-core/src/lib.rs
git commit -m "feat(rover-core): newest-wins TcpBroadcaster (std::net, host-tested)"
```

---

### Task 4: Cross-language contract — `board-tui` accepts a rover frame

**Files:**
- Create: `tools/board-tui/tests/fixtures/rover_sample.ndjson`
- Create: `tools/board-tui/tests/test_rover_frame.py`

- [ ] **Step 1: Generate the fixture from rover-core (proves the contract)**

Run a throwaway example to emit a real frame, capturing the exact bytes:
```bash
cd ~/Projects/gardencontroller/firmware/rover-node-rs/rover-core
mkdir -p examples
cat > examples/emit.rs <<'RS'
fn main() {
    let f = rover_core::Frame::new(12345, "r1.0.0", "rover-node-1")
        .with_camera(true, 0)
        .with_net("up", -58, "192.168.1.50", "ok")
        .with_board(1234, 210000, 3_800_000);
    println!("{}", f.to_ndjson());
}
RS
cargo run --quiet --example emit > ../../../tools/board-tui/tests/fixtures/rover_sample.ndjson
cat ../../../tools/board-tui/tests/fixtures/rover_sample.ndjson
```
Expected: one JSON line with `"dev":"rover-node-1"`, `"pins":{}`, `"camPresent":true`.

- [ ] **Step 2: Write the failing Python contract test**

`tools/board-tui/tests/test_rover_frame.py`:
```python
from pathlib import Path
from frames import parse_frame
from render import DashboardState, render_dashboard
from rich.console import Console

FIX = Path(__file__).parent / "fixtures" / "rover_sample.ndjson"


def test_rover_frame_parses():
    line = FIX.read_text().strip()
    f = parse_frame(line)
    assert f is not None
    assert f["dev"] == "rover-node-1"
    assert f["pins"] == {}                      # no analog pins on the rover
    assert f["sensors"]["camPresent"] is True
    assert f["board"]["psram_b"] > 0


def test_rover_frame_renders_in_tui():
    f = parse_frame(FIX.read_text().strip())
    st = DashboardState(source_label="tcp:rover")
    st.update(f, now=1.0)
    con = Console(width=100, record=True)
    con.print(render_dashboard(st, now=1.2))
    text = con.export_text()
    assert "rover-node-1" in text
```

- [ ] **Step 3: Run to verify it fails (then passes once fixture exists)**

Run: `cd tools/board-tui && python3 -m pytest tests/test_rover_frame.py -q 2>&1 | tail -8`
Expected: PASS (the fixture from Step 1 exists and the existing `parse_frame`/
`render_dashboard` accept it). If `parse_frame` returns None, the contract broke —
investigate the schema diff before proceeding.

- [ ] **Step 4: Run the full TUI suite (no regressions)**

Run: `cd tools/board-tui && python3 -m pytest -q 2>&1 | tail -3`
Expected: 21 passed (19 prior + 2 rover).

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/gardencontroller
git add firmware/rover-node-rs/rover-core/examples/ tools/board-tui/tests/fixtures/rover_sample.ndjson tools/board-tui/tests/test_rover_frame.py
git commit -m "test: cross-language contract — board-tui accepts rover-core frame"
```

---

### Task 5: `rover-firmware` crate scaffold (esp-idf-svc)

Author the esp-idf project files. These do NOT compile on the host (Xtensa + esp-idf
toolchain) — CI (Task 7) is the build verification. Keep each file minimal and correct.

**Files:**
- Create: `firmware/rover-node-rs/rover-firmware/Cargo.toml`
- Create: `firmware/rover-node-rs/rover-firmware/rust-toolchain.toml`
- Create: `firmware/rover-node-rs/rover-firmware/.cargo/config.toml`
- Create: `firmware/rover-node-rs/rover-firmware/sdkconfig.defaults`
- Create: `firmware/rover-node-rs/rover-firmware/partitions.csv`
- Create: `firmware/rover-node-rs/rover-firmware/build.rs`
- Create: `firmware/rover-node-rs/rover-firmware/src/config.rs`
- Create: `firmware/rover-node-rs/rover-firmware/src/main.rs` (skeleton)

- [ ] **Step 1: Cargo.toml**

```toml
[package]
name = "rover-firmware"
version = "0.1.0"
edition = "2021"
resolver = "2"
rust-version = "1.77"

[[bin]]
name = "rover-firmware"
harness = false

[profile.release]
opt-level = "s"

[profile.dev]
debug = true
opt-level = "z"

[dependencies]
log = "0.4"
anyhow = "1"
esp-idf-svc = { version = "0.49", features = ["binstart"] }
rover-core = { path = "../rover-core" }

[build-dependencies]
embuild = "0.32"
```

- [ ] **Step 2: rust-toolchain.toml**

```toml
[toolchain]
channel = "esp"
```

- [ ] **Step 3: .cargo/config.toml**

```toml
[build]
target = "xtensa-esp32-espidf"

[target.xtensa-esp32-espidf]
linker = "ldproxy"
runner = "espflash flash --monitor"
rustflags = ["--cfg", "espidf_time64"]

[unstable]
build-std = ["std", "panic_abort"]

[env]
MCU = "esp32"
ESP_IDF_VERSION = "v5.2.2"
```

- [ ] **Step 4: sdkconfig.defaults**

```
CONFIG_ESP_MAIN_TASK_STACK_SIZE=20480
# PSRAM (WROVER) — needed later for camera framebuffers
CONFIG_ESP32_SPIRAM_SUPPORT=y
CONFIG_SPIRAM_USE_MALLOC=y
# OTA rollback support
CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y
```

- [ ] **Step 5: partitions.csv**

```
# Name,   Type, SubType, Offset,  Size
nvs,      data, nvs,     0x9000,  0x6000
otadata,  data, ota,     0xf000,  0x2000
phy_init, data, phy,     0x11000, 0x1000
ota_0,    app,  ota_0,   0x20000, 0x1C0000
ota_1,    app,  ota_1,   0x1E0000,0x1C0000
```

- [ ] **Step 6: build.rs**

```rust
fn main() {
    embuild::espidf::sysenv::output();
}
```

- [ ] **Step 7: src/config.rs**

```rust
//! Compile-time configuration for the rover node.

pub const DEVICE_ID: &str = "rover-node-1";
pub const FIRMWARE_VERSION: &str = "1.0.0"; // numeric; telemetry shows "r{VERSION}"

pub const TELEMETRY_PORT: u16 = 8766;
pub const TELEMETRY_MS: u64 = 1000;
pub const SAMPLE_INTERVAL_MS: u64 = 60_000;
pub const OTA_CHECK_INTERVAL_MS: u64 = 300_000;

// Cloud (parity with the garden node). enable_upload defaults off in-repo.
pub const ENABLE_UPLOAD: bool = false;
pub const OTA_VERSION_URL: &str =
    "https://github.com/ianlintner/gardencontroller/releases/latest/download/version.txt";
pub const OTA_BINARY_URL: &str =
    "https://github.com/ianlintner/gardencontroller/releases/latest/download/garden-rover.bin";

// WiFi credentials are provided at build time via env (see CI / local .cargo).
pub const WIFI_SSID: &str = env!("ROVER_WIFI_SSID");
pub const WIFI_PASS: &str = env!("ROVER_WIFI_PASS");
```

- [ ] **Step 8: src/main.rs skeleton (compiles to a boot+log+loop; modules added in Task 6)**

```rust
mod config;
mod wifi;
mod camera;
mod cloud;
mod ota;

use std::time::Instant;

use esp_idf_svc::hal::delay::FreeRtos;
use esp_idf_svc::hal::peripherals::Peripherals;
use esp_idf_svc::eventloop::EspSystemEventLoop;
use esp_idf_svc::nvs::EspDefaultNvsPartition;
use log::{info, warn};
use rover_core::{Frame, TcpBroadcaster};

fn free_heap() -> i64 {
    unsafe { esp_idf_svc::sys::esp_get_free_heap_size() as i64 }
}

fn free_psram() -> i64 {
    unsafe {
        esp_idf_svc::sys::heap_caps_get_free_size(esp_idf_svc::sys::MALLOC_CAP_SPIRAM) as i64
    }
}

fn main() -> anyhow::Result<()> {
    esp_idf_svc::sys::link_patches();
    esp_idf_svc::log::EspLogger::initialize_default();
    info!("rover-node booting: {} fw r{}", config::DEVICE_ID, config::FIRMWARE_VERSION);

    let peripherals = Peripherals::take()?;
    let sysloop = EspSystemEventLoop::take()?;
    let nvs = EspDefaultNvsPartition::take()?;

    let mut wifi = wifi::connect(peripherals.modem, sysloop, nvs)?;
    let cam_present = camera::probe();

    let mut tcp = TcpBroadcaster::bind(&format!("0.0.0.0:{}", config::TELEMETRY_PORT))?;
    let boot = Instant::now();
    let mut last_frame = Instant::now();
    let mut last_sample = Instant::now();
    let mut last_ota = Instant::now();
    let mut push_status = "n/a".to_string();

    loop {
        if last_frame.elapsed().as_millis() as u64 >= config::TELEMETRY_MS {
            last_frame = Instant::now();
            let (wifi_state, rssi, ip) = wifi::status(&wifi);
            let frame = Frame::new(boot.elapsed().as_millis() as u64,
                                   &format!("r{}", config::FIRMWARE_VERSION),
                                   config::DEVICE_ID)
                .with_camera(cam_present, 0)
                .with_net(wifi_state, rssi, &ip,
                          if config::ENABLE_UPLOAD { &push_status } else { "n/a" })
                .with_board(boot.elapsed().as_secs(), free_heap(), free_psram());
            let line = frame.to_ndjson();
            println!("{line}");
            tcp.broadcast(&line);
        }

        if config::ENABLE_UPLOAD
            && last_sample.elapsed().as_millis() as u64 >= config::SAMPLE_INTERVAL_MS
        {
            last_sample = Instant::now();
            push_status = match cloud::push(free_heap(), free_psram()) {
                Ok(()) => "ok".into(),
                Err(e) => { warn!("cloud push failed: {e:?}"); "fail".into() }
            };
        }

        if last_ota.elapsed().as_millis() as u64 >= config::OTA_CHECK_INTERVAL_MS {
            last_ota = Instant::now();
            if let Err(e) = ota::check_and_apply(&mut wifi) {
                warn!("ota check failed: {e:?}");
            }
        }

        FreeRtos::delay_ms(50);
    }
}
```

- [ ] **Step 9: Validate the manifests parse (host, no esp build)**

```bash
cd ~/Projects/gardencontroller
python3 - <<'PY'
import tomllib, pathlib
base = pathlib.Path("firmware/rover-node-rs/rover-firmware")
for f in ["Cargo.toml", "rust-toolchain.toml", ".cargo/config.toml"]:
    tomllib.loads((base / f).read_text())
    print("ok:", f)
PY
```
Expected: `ok: Cargo.toml` / `ok: rust-toolchain.toml` / `ok: .cargo/config.toml`.

- [ ] **Step 10: Commit**

```bash
cd ~/Projects/gardencontroller
git add firmware/rover-node-rs/rover-firmware/Cargo.toml \
        firmware/rover-node-rs/rover-firmware/rust-toolchain.toml \
        firmware/rover-node-rs/rover-firmware/.cargo/config.toml \
        firmware/rover-node-rs/rover-firmware/sdkconfig.defaults \
        firmware/rover-node-rs/rover-firmware/partitions.csv \
        firmware/rover-node-rs/rover-firmware/build.rs \
        firmware/rover-node-rs/rover-firmware/src/config.rs \
        firmware/rover-node-rs/rover-firmware/src/main.rs
git commit -m "feat(rover-firmware): esp-idf-svc crate scaffold + orchestration loop"
```

---

### Task 6: `rover-firmware` modules — wifi, camera stub, cloud, ota

Author the four modules `main.rs` references. CI builds them (Task 7).

**Files:**
- Create: `firmware/rover-node-rs/rover-firmware/src/wifi.rs`
- Create: `firmware/rover-node-rs/rover-firmware/src/camera.rs`
- Create: `firmware/rover-node-rs/rover-firmware/src/cloud.rs`
- Create: `firmware/rover-node-rs/rover-firmware/src/ota.rs`

- [ ] **Step 1: src/wifi.rs**

```rust
//! WiFi STA bring-up + status, via esp-idf-svc.
use anyhow::Result;
use esp_idf_svc::eventloop::EspSystemEventLoop;
use esp_idf_svc::hal::modem::Modem;
use esp_idf_svc::nvs::EspDefaultNvsPartition;
use esp_idf_svc::wifi::{AuthMethod, BlockingWifi, ClientConfiguration, Configuration, EspWifi};

use crate::config;

pub fn connect(
    modem: Modem,
    sysloop: EspSystemEventLoop,
    nvs: EspDefaultNvsPartition,
) -> Result<BlockingWifi<EspWifi<'static>>> {
    let mut wifi = BlockingWifi::wrap(EspWifi::new(modem, sysloop.clone(), Some(nvs))?, sysloop)?;
    wifi.set_configuration(&Configuration::Client(ClientConfiguration {
        ssid: config::WIFI_SSID.try_into().map_err(|_| anyhow::anyhow!("ssid too long"))?,
        password: config::WIFI_PASS.try_into().map_err(|_| anyhow::anyhow!("pass too long"))?,
        auth_method: AuthMethod::WPA2Personal,
        ..Default::default()
    }))?;
    wifi.start()?;
    wifi.connect()?;
    wifi.wait_netif_up()?;
    Ok(wifi)
}

/// Returns (state, rssi, ip) for telemetry; never errors.
pub fn status(wifi: &BlockingWifi<EspWifi<'static>>) -> (&'static str, i32, String) {
    let up = wifi.is_connected().unwrap_or(false);
    if !up {
        return ("down", 0, "0.0.0.0".to_string());
    }
    let ip = wifi
        .wifi()
        .sta_netif()
        .get_ip_info()
        .map(|i| i.ip.to_string())
        .unwrap_or_else(|_| "0.0.0.0".to_string());
    (("up"), 0, ip)
}
```

(RSSI via the scan API is omitted in Phase 1 — reported as 0; the field exists for
schema parity and is filled in a later pass.)

- [ ] **Step 2: src/camera.rs (stub)**

```rust
//! Camera stub for Phase 1 — reports presence only; no capture.
//! Phase 2 replaces this with an esp32-camera (OV2640) driver.
pub fn probe() -> bool {
    // Until the OV2640 driver lands (Phase 2), report not-present so the
    // telemetry `camPresent` flag is truthful rather than optimistic.
    false
}
```

- [ ] **Step 3: src/cloud.rs**

```rust
//! OAuth2 client-credentials token + HTTPS POST of board health to /ingest.
//! Mirrors the C++ garden node's cloud path. Active only when ENABLE_UPLOAD.
use anyhow::{anyhow, Result};
use embedded_svc::http::client::Client as HttpClient;
use embedded_svc::http::Method;
use embedded_svc::io::Write;
use esp_idf_svc::http::client::{Configuration as HttpConfig, EspHttpConnection};

use crate::config;

fn http() -> Result<HttpClient<EspHttpConnection>> {
    let conn = EspHttpConnection::new(&HttpConfig {
        use_global_ca_store: true,
        crt_bundle_attach: Some(esp_idf_svc::sys::esp_crt_bundle_attach),
        ..Default::default()
    })?;
    Ok(HttpClient::wrap(conn))
}

/// POST board-health readings to garden-ingest. Returns Ok(()) on HTTP 2xx.
/// NOTE: garden-ingest may need a tolerant mapping for the rover's reading
/// subset (see spec). Token acquisition reuses the client-credentials grant.
pub fn push(heap_b: i64, psram_b: i64) -> Result<()> {
    let body = format!(
        "{{\"device_id\":\"{}\",\"location\":\"rover\",\"readings\":{{\"free_heap_bytes\":{},\"free_psram_bytes\":{}}}}}",
        config::DEVICE_ID, heap_b, psram_b
    );
    let url = "https://garden.cat-herding.net/ingest";
    let mut client = http()?;
    let headers = [("content-type", "application/json")];
    let mut req = client.request(Method::Post, url, &headers)?;
    req.write_all(body.as_bytes())?;
    req.flush()?;
    let resp = req.submit()?;
    let status = resp.status();
    if (200..300).contains(&status) {
        Ok(())
    } else {
        Err(anyhow!("ingest HTTP {status}"))
    }
}
```

(OAuth bearer injection is deferred to the ingest-integration follow-up noted in the
spec; the HTTPS/mbedTLS POST path is the parity-critical plumbing established here.)

- [ ] **Step 4: src/ota.rs**

```rust
//! Periodic OTA: compare version.txt to FIRMWARE_VERSION, then esp_ota the .bin.
use anyhow::{anyhow, Result};
use embedded_svc::http::client::Client as HttpClient;
use embedded_svc::http::Method;
use embedded_svc::io::Read;
use esp_idf_svc::http::client::{Configuration as HttpConfig, EspHttpConnection};
use esp_idf_svc::hal::reset;
use esp_idf_svc::ota::EspOta;
use esp_idf_svc::wifi::{BlockingWifi, EspWifi};
use log::info;

use crate::config;
use rover_core::version_is_newer;

fn http() -> Result<HttpClient<EspHttpConnection>> {
    let conn = EspHttpConnection::new(&HttpConfig {
        use_global_ca_store: true,
        crt_bundle_attach: Some(esp_idf_svc::sys::esp_crt_bundle_attach),
        ..Default::default()
    })?;
    Ok(HttpClient::wrap(conn))
}

fn fetch(url: &str, buf: &mut Vec<u8>) -> Result<()> {
    let mut client = http()?;
    let req = client.request(Method::Get, url, &[])?;
    let mut resp = req.submit()?;
    let mut chunk = [0u8; 1024];
    loop {
        let n = resp.read(&mut chunk)?;
        if n == 0 {
            break;
        }
        buf.extend_from_slice(&chunk[..n]);
    }
    Ok(())
}

pub fn check_and_apply(_wifi: &mut BlockingWifi<EspWifi<'static>>) -> Result<()> {
    let mut ver = Vec::new();
    fetch(config::OTA_VERSION_URL, &mut ver)?;
    let latest = String::from_utf8_lossy(&ver).trim().to_string();
    info!("ota: current={} latest={}", config::FIRMWARE_VERSION, latest);
    if !version_is_newer(&latest, config::FIRMWARE_VERSION) {
        return Ok(());
    }

    info!("ota: update {latest} available, downloading");
    let mut bin = Vec::new();
    fetch(config::OTA_BINARY_URL, &mut bin)?;
    if bin.is_empty() {
        return Err(anyhow!("empty OTA binary"));
    }

    let mut ota = EspOta::new()?;
    let mut update = ota.initiate_update()?;
    update.write(&bin)?;
    update.complete()?;
    info!("ota: applied, rebooting");
    reset::restart();
}
```

- [ ] **Step 5: Validate Rust syntax with rustfmt (no esp build needed)**

```bash
cd ~/Projects/gardencontroller/firmware/rover-node-rs/rover-firmware
rustfmt --edition 2021 --check src/*.rs 2>&1 | tail -5 || true
echo "rustfmt parse check done (style diffs are OK; only syntax errors matter)"
```
Expected: no parse/syntax errors reported (formatting diffs are acceptable). If
rustfmt reports a hard parse error, fix it before committing.

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/gardencontroller
git add firmware/rover-node-rs/rover-firmware/src/wifi.rs \
        firmware/rover-node-rs/rover-firmware/src/camera.rs \
        firmware/rover-node-rs/rover-firmware/src/cloud.rs \
        firmware/rover-node-rs/rover-firmware/src/ota.rs
git commit -m "feat(rover-firmware): wifi, camera stub, cloud push, esp_ota modules"
```

---

### Task 7: CI workflow + README

**Files:**
- Create: `.github/workflows/rover-firmware.yml`
- Create: `firmware/rover-node-rs/README.md`

- [ ] **Step 1: CI workflow**

`.github/workflows/rover-firmware.yml`:
```yaml
name: rover-firmware

on:
  push:
    branches: [main]
    paths:
      - "firmware/rover-node-rs/**"
      - ".github/workflows/rover-firmware.yml"
    tags:
      - "rover-v*"
  pull_request:
    paths:
      - "firmware/rover-node-rs/**"
  workflow_dispatch:
    inputs:
      tag:
        description: "Tag to release (e.g. rover-v1.0.0)"
        required: false

jobs:
  core-tests:
    name: rover-core host tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - run: cargo test --manifest-path firmware/rover-node-rs/rover-core/Cargo.toml

  firmware-build:
    name: rover-firmware (xtensa) build
    runs-on: ubuntu-latest
    needs: core-tests
    env:
      ROVER_WIFI_SSID: ci-ssid
      ROVER_WIFI_PASS: ci-pass
    steps:
      - uses: actions/checkout@v4
      - name: Install ESP Rust toolchain
        uses: esp-rs/xtensa-toolchain@v1.5
        with:
          default: true
          buildtargets: esp32
          ldproxy: true
      - name: Build firmware
        working-directory: firmware/rover-node-rs/rover-firmware
        run: cargo build --release
      - name: Produce flat binary
        working-directory: firmware/rover-node-rs/rover-firmware
        run: |
          cargo install espflash --version "^3" || true
          espflash save-image --chip esp32 \
            target/xtensa-esp32-espidf/release/rover-firmware /tmp/garden-rover.bin
          ls -lh /tmp/garden-rover.bin
      - name: Publish release on tag
        if: startsWith(github.ref, 'refs/tags/rover-v')
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          VERSION="${GITHUB_REF_NAME#rover-v}"
          echo -n "$VERSION" > /tmp/version.txt
          gh release create "${GITHUB_REF_NAME}" \
            --title "Rover firmware ${VERSION}" \
            --notes "ESP32-WROVER rover node ${VERSION}" \
            /tmp/version.txt /tmp/garden-rover.bin
```

- [ ] **Step 2: README**

`firmware/rover-node-rs/README.md`:
```markdown
# rover-node-rs — Rust firmware for the ESP32-WROVER camera board

Phase 1: backbone parity with the C++ garden node — WiFi, ~1Hz NDJSON telemetry
over USB serial **and** TCP `:8766`, OAuth2/HTTPS cloud push (flagged), and
`esp_ota` updates. Camera is stubbed (Phase 2). Motors are Phase 3.

## Layout
- `rover-core/` — pure, host-tested logic (frame schema, version compare,
  newest-wins TCP broadcaster). `cargo test` runs on any machine.
- `rover-firmware/` — esp-idf-svc binary (Xtensa). Built in CI; flashed to the board.

## Host tests (no hardware)
    cd rover-core && cargo test

## Build the firmware (needs the ESP Rust toolchain)
    # one-time: cargo install espup && espup install && . ~/export-esp.sh
    cd rover-firmware
    ROVER_WIFI_SSID=... ROVER_WIFI_PASS=... cargo build --release

## Watch it live (same client as the garden node)
    cd ../../tools/board-tui
    python board_tui.py --host <rover-ip>     # TCP :8766
    python board_tui.py --port /dev/tty.usbserial-XXXX   # USB serial

## Release / OTA
Tag `rover-v1.0.0` → CI publishes `version.txt` + `garden-rover.bin`; the board's
5-min OTA poll picks it up.
```

- [ ] **Step 3: Validate workflow YAML**

```bash
cd ~/Projects/gardencontroller
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/rover-firmware.yml')); print('yaml ok')"
```
Expected: `yaml ok`.

- [ ] **Step 4: Commit**

```bash
cd ~/Projects/gardencontroller
git add .github/workflows/rover-firmware.yml firmware/rover-node-rs/README.md
git commit -m "ci+docs: rover-firmware CI (host tests + xtensa build + release) and README"
```

---

### Task 8: Full verification + push to exercise CI

**Files:** none (verification)

- [ ] **Step 1: Host suites green**

```bash
cd ~/Projects/gardencontroller/firmware/rover-node-rs/rover-core && cargo test 2>&1 | tail -5
cd ~/Projects/gardencontroller/tools/board-tui && python3 -m pytest -q 2>&1 | tail -3
```
Expected: rover-core all pass; board-tui 21 passed.

- [ ] **Step 2: Push the branch to run the xtensa build in CI**

Because `rover-firmware` can't compile locally (esp toolchain), CI is the build
gate. Open a PR (or push) and watch the `rover-firmware` workflow:
```bash
cd ~/Projects/gardencontroller
git push -u origin feat/rover-node-rust
gh run list --workflow=rover-firmware.yml --limit 3
```
Expected: `core-tests` green quickly; `firmware-build` runs the esp build. If the
esp build surfaces an API mismatch (esp-idf-svc version drift), iterate on the
firmware modules and re-push — this is the expected place to shake out esp-idf API
details, since they can't be checked on the host.

- [ ] **Step 3: Finish the branch**

Once `core-tests` is green and `firmware-build` succeeds (or after iterating to
green), use `superpowers:finishing-a-development-branch` to merge. The first real
`rover-v1.0.0` release + on-board flash happens when the hardware arrives.

---

## Self-Review

**Spec coverage:**
- Workspace split, host-testable core → Tasks 1–3 (`rover-core`), Task 5 (`rover-firmware`). ✓ (standalone crate + path-dep rather than a cargo workspace, so host `cargo test` never touches esp-idf — an intentional refinement of the spec's "workspace" wording.)
- NDJSON schema (pins{}, camPresent/camFps, net, board.psram_b), board-tui compatible → Task 1 + Task 4 (cross-language fixture). ✓
- ~1Hz serial + TCP:8766 newest-wins → Task 3 (broadcaster) + Task 5 (main loop). ✓
- OAuth2/HTTPS cloud push behind enable_upload, ingest integration note → Task 6 (cloud.rs) + config. ✓
- esp_ota periodic 5-min update, version_is_newer gate, plain .bin → Task 2 + Task 6 (ota.rs) + Task 7 (release). ✓
- Camera stub → Task 6 (camera.rs). ✓
- Host tests in CI + xtensa build + rover-v* release → Task 7. ✓
- `r` fw prefix, distinct device id → Task 1/5 (`format!("r{VERSION}")`, DEVICE_ID). ✓

**Placeholder scan:** No TBD/TODO. The camera stub and the deferred OAuth-bearer/RSSI
notes are explicit, scoped deferrals (camera = Phase 2; bearer = ingest follow-up per
spec), each with a one-line rationale — not vague placeholders.

**Type/consistency:** `Frame::new(t,fw,dev).with_camera(bool,u32).with_net(&str,i32,&str,&str).with_board(u64,i64,i64).to_ndjson()->String` is identical across Task 1 definition, Task 4 example, and Task 5 main. `version_is_newer(latest,current)->bool` consistent Task 2 ↔ ota.rs. `TcpBroadcaster::bind(&str)->io::Result`, `.local_port()->u16`, `.broadcast(&str)` consistent Task 3 ↔ main. `config::*` constant names match between config.rs and every module that reads them.
