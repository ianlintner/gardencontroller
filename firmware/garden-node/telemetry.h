// telemetry.h — emit a structured NDJSON telemetry frame over Serial (~1 Hz).
#pragma once
#include <Arduino.h>

void telemetryBegin();                      // call in setup() (sets ADC resolution)
void telemetrySetPush(const char* status);  // "ok"/"fail"/"n/a" from the publish path
void telemetryTick();                       // call every loop(); non-blocking
