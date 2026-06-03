// display.h — owns the 12x8 LED matrix: health states + sensor-value slideshow.
#pragma once
#include <Arduino.h>
#include "sensors.h"

enum DisplayHealth { DISP_CONNECTING, DISP_HEALTHY, DISP_ERROR };

void displayBegin();
void displayTick();                       // call every loop(); non-blocking
void displaySetHealth(DisplayHealth s);   // CONNECTING/HEALTHY/ERROR
void displaySetReadings(const SoilReading* soil, size_t count,
                        float tempC, float humidity, bool dhtOk);
