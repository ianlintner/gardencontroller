// net.h — WiFi, OAuth2 client-credentials token cache, and telemetry upload.
#pragma once
#include <Arduino.h>
#include "sensors.h"

void netBegin();                   // bring up WiFi
bool netEnsureWifi();              // (re)connect WiFi; returns connected?
bool netPublish(const Reading& r); // auth (cached) + POST readings; returns ok?
int  netRssi();
