//! Camera stub for Phase 1 — reports presence only; no capture.
//! Phase 2 replaces this with an esp32-camera (OV2640) driver.
pub fn probe() -> bool {
    // Until the OV2640 driver lands (Phase 2), report not-present so the
    // telemetry `camPresent` flag is truthful rather than optimistic.
    false
}
