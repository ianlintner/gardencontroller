mod camera;
mod cloud;
mod config;
mod ota;
mod wifi;

use std::time::Instant;

use esp_idf_svc::eventloop::EspSystemEventLoop;
use esp_idf_svc::hal::delay::FreeRtos;
use esp_idf_svc::hal::peripherals::Peripherals;
use esp_idf_svc::nvs::EspDefaultNvsPartition;
use log::{info, warn};
use rover_core::{Frame, TcpBroadcaster};

fn free_heap() -> i64 {
    unsafe { esp_idf_svc::sys::esp_get_free_heap_size() as i64 }
}

fn free_psram() -> i64 {
    unsafe { esp_idf_svc::sys::heap_caps_get_free_size(esp_idf_svc::sys::MALLOC_CAP_SPIRAM) as i64 }
}

fn main() -> anyhow::Result<()> {
    esp_idf_svc::sys::link_patches();
    esp_idf_svc::log::EspLogger::initialize_default();
    info!(
        "rover-node booting: {} fw r{}",
        config::DEVICE_ID,
        config::FIRMWARE_VERSION
    );

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
            let frame = Frame::new(
                boot.elapsed().as_millis() as u64,
                &format!("r{}", config::FIRMWARE_VERSION),
                config::DEVICE_ID,
            )
            .with_camera(cam_present, 0)
            .with_net(
                wifi_state,
                rssi,
                &ip,
                if config::ENABLE_UPLOAD {
                    &push_status
                } else {
                    "n/a"
                },
            )
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
                Err(e) => {
                    warn!("cloud push failed: {e:?}");
                    "fail".into()
                }
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
