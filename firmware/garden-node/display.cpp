#include "display.h"
#include "config.h"
#include "display_render.h"
#include "Arduino_LED_Matrix.h"

using namespace gdisplay;

static ArduinoLEDMatrix matrix;
static DisplayHealth _health = DISP_CONNECTING;

// latest readings (copied in displaySetReadings)
static float _soil[2] = {0, 0};
static bool  _soilValid[2] = {false, false};
static float _tempC = 0, _humid = 0;
static bool  _dhtOk = false;

static unsigned long _cycleStart = 0;   // slideshow time origin
static int  _plantFrame = 0;
static unsigned long _lastPlantMs = 0;
static int  _renderedSlide = -1;
static DisplayHealth _renderedHealth = (DisplayHealth)-1;

static void show(const Frame f) {
    uint8_t buf[8][12];
    for (int y = 0; y < 8; y++) for (int x = 0; x < 12; x++) buf[y][x] = f[y][x];
    matrix.renderBitmap(buf, 8, 12);
}

void displayBegin() {
    matrix.begin();
    _health = DISP_CONNECTING;
    _renderedHealth = (DisplayHealth)-1;
    _cycleStart = millis();
}

void displaySetHealth(DisplayHealth s) {
    if (s == _health) return;
    _health = s;
    _cycleStart = millis();      // restart slideshow on (re)entering HEALTHY
    _renderedSlide = -1;
    _renderedHealth = (DisplayHealth)-1;
}

void displaySetReadings(const SoilReading* soil, size_t count,
                        float tempC, float humidity, bool dhtOk) {
    for (int i = 0; i < 2; i++) {
        if ((size_t)i < count) { _soil[i] = soil[i].percent; _soilValid[i] = true; }
        else                   { _soilValid[i] = false; }
    }
    _tempC = tempC; _humid = humidity; _dhtOk = dhtOk;
}

void displayTick() {
    Frame f;
    // Non-HEALTHY: static glyph, render once on change.
    if (_health != DISP_HEALTHY) {
        if (_renderedHealth != _health) {
            if (_health == DISP_ERROR) render_x(f); else render_seed(f);
            show(f);
            _renderedHealth = _health;
        }
        return;
    }

    // HEALTHY: 5-slide slideshow (0=plant, 1=bed1, 2=bed2, 3=temp, 4=humid).
    const int N = 5;
    unsigned long now = millis();
    int slide = select_slide(now - _cycleStart, SLIDE_MS, N);

    if (slide == 0) {
        // plant animates within its slide
        if (_renderedSlide != 0 || now - _lastPlantMs >= 700) {
            _lastPlantMs = now;
            render_plant(f, _plantFrame);
            _plantFrame = (_plantFrame + 1) % PLANT_FRAMES;
            show(f);
            _renderedSlide = 0;
        }
        return;
    }
    if (slide == _renderedSlide) return;   // static value slide already shown
    switch (slide) {
        case 1: render_value_slide(f, IC_SOIL1, _soil[0], 0, 100, _soilValid[0]); break;
        case 2: render_value_slide(f, IC_SOIL2, _soil[1], 0, 100, _soilValid[1]); break;
        case 3: render_value_slide(f, IC_TEMP,  _tempC, TEMP_MIN_C, TEMP_MAX_C, _dhtOk); break;
        case 4: render_value_slide(f, IC_HUMID, _humid, 0, 100, _dhtOk); break;
    }
    show(f);
    _renderedSlide = slide;
}
