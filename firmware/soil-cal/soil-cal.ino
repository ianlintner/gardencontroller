// Soil sensor calibration helper — prints A0 ~2x/sec so you can read endpoints.
//
//   1. Flash this:  arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi firmware/soil-cal \
//                   && arduino-cli upload  -p <port> --fqbn arduino:renesas_uno:unor4wifi firmware/soil-cal
//   2. Hold the probe in DRY air (or dry soil)      -> note the steady raw  = SOIL_RAW_DRY
//   3. Submerge the probe in WATER to its max line   -> note the steady raw  = SOIL_RAW_WET
//   4. Put those two numbers in firmware/garden-node/sensors.cpp, then reflash garden-node.
//
// Capacitive probes read LOWER raw when wetter; resistive ones may be the opposite —
// whichever way, just record the dry and wet readings.

#define SOIL_PIN A0

void setup() {
  Serial.begin(115200);
  analogReadResolution(14);   // 0..16383, same as the main firmware
}

void loop() {
  // Median of 5 to match the main firmware's smoothing.
  int v[5];
  for (int i = 0; i < 5; i++) { v[i] = analogRead(SOIL_PIN); delay(5); }
  for (int i = 0; i < 4; i++)
    for (int j = i + 1; j < 5; j++)
      if (v[j] < v[i]) { int t = v[i]; v[i] = v[j]; v[j] = t; }
  int raw = v[2];
  Serial.print("A0 raw="); Serial.print(raw);
  Serial.print("   ~"); Serial.print(raw * 5.0 / 16383.0, 2); Serial.println(" V");
  delay(500);
}
