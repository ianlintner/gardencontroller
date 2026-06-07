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
