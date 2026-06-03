// ota.h — OTA firmware update: version check + pull-from-URL at boot.
#pragma once
#include <Arduino.h>

// Call once in setup() after WiFi is connected.
// Downloads and applies a new firmware from GitHub Releases if a newer version
// is available. Reboots the board on success. Returns false if no update is
// available or if the update failed (caller continues normal operation).
bool otaCheckAndApply();
