//! Periodic OTA: compare version.txt to FIRMWARE_VERSION, then esp_ota the .bin.
use anyhow::{anyhow, Result};
use embedded_svc::http::client::Client as HttpClient;
use embedded_svc::http::Method;
use embedded_svc::io::Read;
use esp_idf_svc::hal::reset;
use esp_idf_svc::http::client::{Configuration as HttpConfig, EspHttpConnection};
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
    info!(
        "ota: current={} latest={}",
        config::FIRMWARE_VERSION,
        latest
    );
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
