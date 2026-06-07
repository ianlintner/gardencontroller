//! WiFi STA bring-up + status, via esp-idf-svc.
use anyhow::Result;
use esp_idf_svc::eventloop::EspSystemEventLoop;
use esp_idf_svc::hal::modem::Modem;
use esp_idf_svc::nvs::EspDefaultNvsPartition;
use esp_idf_svc::wifi::{AuthMethod, BlockingWifi, ClientConfiguration, Configuration, EspWifi};

use crate::config;

pub fn connect(
    modem: Modem<'static>,
    sysloop: EspSystemEventLoop,
    nvs: EspDefaultNvsPartition,
) -> Result<BlockingWifi<EspWifi<'static>>> {
    let mut wifi = BlockingWifi::wrap(EspWifi::new(modem, sysloop.clone(), Some(nvs))?, sysloop)?;
    wifi.set_configuration(&Configuration::Client(ClientConfiguration {
        ssid: config::WIFI_SSID
            .try_into()
            .map_err(|_| anyhow::anyhow!("ssid too long"))?,
        password: config::WIFI_PASS
            .try_into()
            .map_err(|_| anyhow::anyhow!("pass too long"))?,
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
    // RSSI via the scan API is omitted in Phase 1 — reported as 0 (schema parity).
    ("up", 0, ip)
}
