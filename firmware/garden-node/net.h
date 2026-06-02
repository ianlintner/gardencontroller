// net.h — WiFi, OAuth2 client-credentials token cache, and telemetry upload.
#pragma once
#include <Arduino.h>
#include "sensors.h"

void netBegin();                 // bring up WiFi + LED matrix
bool netEnsureWifi();            // (re)connect WiFi; returns connected?
bool netPublish(const Reading& r); // auth (cached) + POST readings; returns ok?
int  netRssi();

// LED matrix (monochrome red): healthy => slow growing-plant animation,
// unhealthy => static X. The matrix can't show colour, so state is shown by shape.
void netSetHealth(bool ok);      // set the displayed state (true after a good push)
void netDisplayTick();           // call every loop(): advances the animation, non-blocking
