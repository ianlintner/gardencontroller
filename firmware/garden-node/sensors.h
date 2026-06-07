// sensors.h — read + calibrate the garden sensors.
#pragma once
#include <Arduino.h>
#include "config.h"

struct SoilReading {
  const char* probe;
  int   raw;       // raw ADC value
  float percent;   // calibrated 0..100
};

struct Reading {
  float tempC;
  float humidity;
  bool  dhtOk;

  SoilReading soil[SOIL_PROBE_COUNT];

  int   rainRaw;
  float rainPercent;   // 0..100 intensity
  bool  rainDetected;  // thresholded boolean
};

void sensorsBegin();
Reading sensorsRead();
void sensorsPrint(const Reading& r);  // Phase 0 Serial dump

// Lightweight DHT read for the live telemetry stream (no soil/rain work).
// Returns true on a successful read; writes tempC/hum on success.
bool sensorsReadDht(float& tempC, float& hum);

// ─── Contribution points (see docs/calibration.md) ──────────────────────────
float soilMoisturePercent(int raw);   // TODO(you) #1: map raw ADC → 0..100
bool  classifyRainDetected(int raw);  // TODO(you) #2: threshold for "raining"
float rainIntensityPercent(int raw);  // TODO(you) #2: map raw → 0..100
