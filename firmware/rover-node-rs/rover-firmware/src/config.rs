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
