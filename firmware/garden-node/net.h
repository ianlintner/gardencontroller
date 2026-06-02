// net.h — WiFi, OAuth2 client-credentials token cache, and telemetry upload.
#pragma once
#include <Arduino.h>
#include "sensors.h"

enum NetStatus { NET_BOOT, NET_WIFI_DOWN, NET_AUTH_FAIL, NET_OK, NET_PUSHING };

void netBegin();                 // bring up WiFi + LED matrix
bool netEnsureWifi();            // (re)connect WiFi; returns connected?
bool netPublish(const Reading& r); // auth (cached) + POST readings; returns ok?
void netShowStatus(NetStatus s); // drive the 12x8 LED matrix
int  netRssi();
