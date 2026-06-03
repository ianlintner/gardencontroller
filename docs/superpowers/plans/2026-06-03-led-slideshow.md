# LED Matrix Slideshow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rotate the UNO R4 WiFi's 12×8 LED matrix through a slideshow — plant → soil bed1 → soil bed2 → temp → humidity, 5s each — with icon+bar rendering, and X/seed overrides for error/connecting states.

**Architecture:** Pure, host-testable rendering logic (`display_render.h`: bar math, icon/bar composition, slide selection — no Arduino deps) is unit-tested with `c++`. A thin Arduino module (`display.cpp`) owns the matrix + millis-based slideshow state machine and calls the pure renderers. The matrix code moves out of `net.cpp` (which keeps only networking + calls `displaySetHealth`).

**Tech Stack:** Arduino C++ (UNO R4 WiFi, `Arduino_LED_Matrix`), pure C++17 header for logic, host tests via `c++ -std=c++17` + `assert`, `arduino-cli` for compile.

---

## File structure

- `firmware/garden-node/display_render.h` — **pure** (no Arduino): frame type, `bar_level`, icon glyphs, `draw_icon`, `draw_bar`, `render_value_slide`, plant/X/seed frame data, `select_slide`.
- `firmware/garden-node/display.h` — module interface (`DisplayHealth`, `displayBegin/Tick/SetHealth/SetReadings`).
- `firmware/garden-node/display.cpp` — matrix object + slideshow state machine; includes `display_render.h` + `Arduino_LED_Matrix.h`.
- `firmware/garden-node/test/test_render.cpp` — host unit tests for `display_render.h`.
- Modify `net.cpp`/`net.h` — remove matrix/health/frame code; call `displaySetHealth`.
- Modify `garden-node.ino` — `displayBegin()`, `displayTick()`, `displaySetReadings()`.
- Modify `config.h` — `SLIDE_MS`, `TEMP_MIN_C`, `TEMP_MAX_C`.

Frame convention: `uint8_t frame[8][12]` (8 rows × 12 cols), `1`=lit. Icon occupies cols 0–3, bar occupies cols 5–11 (col 4 is a gap), bar fills bottom-up.

---

## Task 1: `display_render.h` scaffold + `bar_level` (pure, host-tested)

**Files:**
- Create: `firmware/garden-node/display_render.h`
- Create: `firmware/garden-node/test/test_render.cpp`

- [ ] **Step 1: Write the failing test**

`firmware/garden-node/test/test_render.cpp`:
```cpp
#include "../display_render.h"
#include <cassert>
#include <cstdio>
using namespace gdisplay;

static void test_bar_level() {
    assert(bar_level(0, 0, 100) == 0);
    assert(bar_level(100, 0, 100) == 8);
    assert(bar_level(50, 0, 100) == 4);
    assert(bar_level(-10, 0, 100) == 0);     // clamp low
    assert(bar_level(999, 0, 100) == 8);      // clamp high
    assert(bar_level(20, 0, 40) == 4);        // temp range
    assert(bar_level(5, 5, 5) == 0);          // degenerate range -> 0
}

int main() {
    test_bar_level();
    printf("all render tests passed\n");
    return 0;
}
```

- [ ] **Step 2: Run it — fails to compile (header missing)**

Run: `cd firmware/garden-node && c++ -std=c++17 -o /tmp/trender test/test_render.cpp 2>&1 | head`
Expected: FAIL — `display_render.h` not found.

- [ ] **Step 3: Create the header with `bar_level`**

`firmware/garden-node/display_render.h`:
```cpp
// display_render.h — pure, host-testable LED matrix rendering (no Arduino deps).
#pragma once
#include <cstdint>
#include <cstddef>

namespace gdisplay {
constexpr int ROWS = 8;
constexpr int COLS = 12;
typedef uint8_t Frame[ROWS][COLS];

// Number of bottom-up rows (0..ROWS) to light for `value` within [lo, hi].
inline int bar_level(float value, float lo, float hi) {
    if (hi <= lo) return 0;
    float t = (value - lo) / (hi - lo);
    if (t < 0) t = 0;
    if (t > 1) t = 1;
    int lvl = (int)(t * ROWS + 0.5f);
    if (lvl < 0) lvl = 0;
    if (lvl > ROWS) lvl = ROWS;
    return lvl;
}
}  // namespace gdisplay
```

- [ ] **Step 4: Run it — passes**

Run: `cd firmware/garden-node && c++ -std=c++17 -o /tmp/trender test/test_render.cpp && /tmp/trender`
Expected: `all render tests passed`

- [ ] **Step 5: Commit**

```bash
git add firmware/garden-node/display_render.h firmware/garden-node/test/test_render.cpp
git commit -m "feat(display): pure bar_level with host test harness"
```

---

## Task 2: Icon glyphs + `draw_icon`/`draw_bar`/`render_value_slide`

**Files:**
- Modify: `firmware/garden-node/display_render.h`
- Modify: `firmware/garden-node/test/test_render.cpp`

- [ ] **Step 1: Write the failing tests**

Add to `test/test_render.cpp` (before `main`, and call from `main`):
```cpp
static int count_lit(const Frame f) {
    int n = 0;
    for (int y = 0; y < ROWS; y++) for (int x = 0; x < COLS; x++) n += f[y][x] ? 1 : 0;
    return n;
}
static int bar_lit_rows(const Frame f) {
    // count rows in the bar region (cols 5..11) that have any lit pixel
    int rows = 0;
    for (int y = 0; y < ROWS; y++) {
        for (int x = 5; x < COLS; x++) if (f[y][x]) { rows++; break; }
    }
    return rows;
}
static void test_render_value_slide() {
    Frame f;
    render_value_slide(f, IC_SOIL1, 100, 0, 100, true);   // full bar
    assert(bar_lit_rows(f) == 8);
    int icon_pixels = 0;
    for (int y = 0; y < ROWS; y++) for (int x = 0; x < 4; x++) icon_pixels += f[y][x] ? 1 : 0;
    assert(icon_pixels > 0);                               // icon drawn in cols 0..3

    render_value_slide(f, IC_HUMID, 50, 0, 100, true);     // half bar
    assert(bar_lit_rows(f) == 4);

    render_value_slide(f, IC_TEMP, 0, 0, 100, false);      // invalid -> "no data" dash, not full
    assert(bar_lit_rows(f) <= 1);
}
```
And in `main`, add `test_render_value_slide();`.

- [ ] **Step 2: Run it — fails**

Run: `cd firmware/garden-node && c++ -std=c++17 -o /tmp/trender test/test_render.cpp 2>&1 | head`
Expected: FAIL — `IC_SOIL1`/`render_value_slide` undefined.

- [ ] **Step 3: Implement glyphs + drawing**

Add to `display_render.h` inside the namespace:
```cpp
enum Icon { IC_SOIL1 = 0, IC_SOIL2, IC_TEMP, IC_HUMID, ICON_COUNT };

// 4-wide x 8-tall glyphs, '#' = lit. Plain ASCII so they're easy to tweak.
// soil = digit (1/2), temp = thermometer, humid = droplet.
static const char* const ICON_ART[ICON_COUNT][ROWS] = {
    { " #  ", "##  ", " #  ", " #  ", " #  ", " #  ", "### ", "    " },  // IC_SOIL1 "1"
    { "##  ", "  # ", "  # ", " #  ", "#   ", "#   ", "### ", "    " },  // IC_SOIL2 "2"
    { " #  ", " #  ", " #  ", " #  ", " #  ", "### ", "### ", " #  " },  // IC_TEMP thermometer
    { " #  ", " #  ", "##  ", "### ", "### ", "### ", " #  ", "    " },  // IC_HUMID droplet
};

inline void clear(Frame f) {
    for (int y = 0; y < ROWS; y++) for (int x = 0; x < COLS; x++) f[y][x] = 0;
}

inline void draw_icon(Frame f, Icon ic) {
    for (int y = 0; y < ROWS; y++) {
        const char* row = ICON_ART[ic][y];
        for (int x = 0; x < 4 && row[x]; x++) f[y][x] = (row[x] != ' ') ? 1 : 0;
    }
}

// Bar in cols 5..11, bottom-up. valid=false -> a single "no data" dash row.
inline void draw_bar(Frame f, int lvl, bool valid) {
    const int x0 = 5, x1 = COLS - 1;
    if (!valid) {
        for (int x = x0; x <= x1; x++) f[ROWS / 2][x] = 1;   // mid dash
        return;
    }
    for (int r = 0; r < lvl; r++) {
        int y = ROWS - 1 - r;
        for (int x = x0; x <= x1; x++) f[y][x] = 1;
    }
}

inline void render_value_slide(Frame f, Icon ic, float value, float lo, float hi, bool valid) {
    clear(f);
    draw_icon(f, ic);
    draw_bar(f, valid ? bar_level(value, lo, hi) : 0, valid);
}
```

- [ ] **Step 4: Run it — passes**

Run: `cd firmware/garden-node && c++ -std=c++17 -o /tmp/trender test/test_render.cpp && /tmp/trender`
Expected: `all render tests passed`

- [ ] **Step 5: Commit**

```bash
git add firmware/garden-node/display_render.h firmware/garden-node/test/test_render.cpp
git commit -m "feat(display): icon glyphs + icon/bar value-slide rendering"
```

---

## Task 3: Health/plant frames + `select_slide`

**Files:**
- Modify: `firmware/garden-node/display_render.h`
- Modify: `firmware/garden-node/test/test_render.cpp`

- [ ] **Step 1: Write the failing tests**

Add to `test/test_render.cpp` and call from `main`:
```cpp
static void test_select_slide() {
    // 5 slides, 5000ms each
    assert(select_slide(0, 5000, 5) == 0);
    assert(select_slide(4999, 5000, 5) == 0);
    assert(select_slide(5000, 5000, 5) == 1);
    assert(select_slide(25000, 5000, 5) == 0);   // wraps
    assert(select_slide(1234, 0, 5) == 0);        // guard: slide_ms 0
    assert(select_slide(1234, 5000, 0) == 0);     // guard: n 0
}
static void test_static_frames_exist() {
    Frame f;
    render_plant(f, 0); assert(count_lit(f) >= 1);
    render_x(f);        assert(count_lit(f) >= 6);
    render_seed(f);     assert(count_lit(f) >= 1);
    int frames = PLANT_FRAMES; assert(frames >= 2);
}
```
Add `test_select_slide(); test_static_frames_exist();` to `main`.

- [ ] **Step 2: Run it — fails**

Run: `cd firmware/garden-node && c++ -std=c++17 -o /tmp/trender test/test_render.cpp 2>&1 | head`
Expected: FAIL — `select_slide`/`render_plant`/`render_x`/`render_seed`/`PLANT_FRAMES` undefined.

- [ ] **Step 3: Implement (move the existing plant/X/seed art here)**

Add to `display_render.h` inside the namespace. The plant frames are the same ASCII art currently in `net.cpp` (7 frames), the X is the current X glyph, seed is plant frame 0. Each art row is 12 chars:
```cpp
static const char* const PLANT_ART[][ROWS] = {
  { "            ","            ","            ","            ","            ","            ","            ","     ##     " },
  { "            ","            ","            ","            ","            ","            ","     ##     ","     ##     " },
  { "            ","            ","            ","            ","            ","     ##     ","    ####    ","     ##     " },
  { "            ","            ","            ","            ","     ##     ","    ####    ","     ##     ","     ##     " },
  { "            ","            ","            ","     ##     ","   # ## #   ","    ####    ","     ##     ","     ##     " },
  { "            ","            ","     ##     ","   # ## #   ","    ####    ","   # ## #   ","     ##     ","     ##     " },
  { "    ####    ","   #    #   ","    ####    ","   # ## #   ","    ####    ","     ##     ","     ##     ","     ##     " },
};
constexpr int PLANT_FRAMES = (int)(sizeof(PLANT_ART) / sizeof(PLANT_ART[0]));

static const char* const X_ART[ROWS] = {
  "  #      #  ","   #    #   ","    #  #    ","     ##     ","     ##     ","    #  #    ","   #    #   ","  #      #  ",
};

inline void render_art12(Frame f, const char* const art[ROWS]) {
    clear(f);
    for (int y = 0; y < ROWS; y++)
        for (int x = 0; x < COLS && art[y][x]; x++) f[y][x] = (art[y][x] != ' ') ? 1 : 0;
}
inline void render_plant(Frame f, int frame) {
    if (frame < 0) frame = 0;
    render_art12(f, PLANT_ART[frame % PLANT_FRAMES]);
}
inline void render_x(Frame f)    { render_art12(f, X_ART); }
inline void render_seed(Frame f) { render_plant(f, 0); }

inline int select_slide(unsigned long elapsed_ms, unsigned long slide_ms, int n) {
    if (slide_ms == 0 || n <= 0) return 0;
    return (int)((elapsed_ms / slide_ms) % (unsigned long)n);
}
```

- [ ] **Step 4: Run it — passes**

Run: `cd firmware/garden-node && c++ -std=c++17 -o /tmp/trender test/test_render.cpp && /tmp/trender`
Expected: `all render tests passed`

- [ ] **Step 5: Commit**

```bash
git add firmware/garden-node/display_render.h firmware/garden-node/test/test_render.cpp
git commit -m "feat(display): plant/X/seed frames + slide selection"
```

---

## Task 4: `display.h` / `display.cpp` — matrix + slideshow state machine

**Files:**
- Create: `firmware/garden-node/display.h`
- Create: `firmware/garden-node/display.cpp`

- [ ] **Step 1: Create the interface**

`firmware/garden-node/display.h`:
```cpp
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
```

- [ ] **Step 2: Create the implementation**

`firmware/garden-node/display.cpp`:
```cpp
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
```

- [ ] **Step 3: Add config knobs**

In `config.h`, after `PLANT_FRAME_MS`:
```cpp
// LED slideshow
#define SLIDE_MS    5000UL   // ms per slide (5 slides -> 25s cycle)
#define TEMP_MIN_C  0.0f     // temp bar range
#define TEMP_MAX_C  40.0f
```

- [ ] **Step 4: Verify it compiles (after Task 5 wires it in)**

`display.cpp` references `SLIDE_MS`/`TEMP_MIN_C`/`TEMP_MAX_C` (this step) and is compiled as part of the sketch once `garden-node.ino` is wired in Task 5. No standalone compile here.

- [ ] **Step 5: Commit**

```bash
git add firmware/garden-node/display.h firmware/garden-node/display.cpp firmware/garden-node/config.h
git commit -m "feat(display): matrix slideshow state machine + config"
```

---

## Task 5: Wire display module in; remove matrix code from net.cpp

**Files:**
- Modify: `firmware/garden-node/net.h`
- Modify: `firmware/garden-node/net.cpp`
- Modify: `firmware/garden-node/garden-node.ino`

- [ ] **Step 1: Trim `net.h`**

Remove the display declarations (`netSetHealth`, `netDisplayTick`) from `net.h`. The remaining public API is `netBegin()`, `netEnsureWifi()`, `netPublish()`, `netRssi()`.

- [ ] **Step 2: Update `net.cpp`**

- Add `#include "display.h"` near the top.
- Delete the matrix-related code that moved to the display module: the `#include "Arduino_LED_Matrix.h"`, `static ArduinoLEDMatrix matrix;`, the PLANT/X_GLYPH art, `enum DispState`/state vars, `renderAscii`, `setDisp`, `netSetHealth`, `netDisplayTick`, and the LED frames block.
- Replace every call that was `netSetHealth(false)` with `displaySetHealth(DISP_ERROR)`, and `netSetHealth(ok)`/success with `displaySetHealth(ok ? DISP_HEALTHY : DISP_ERROR)`.
- In `netBegin()`: replace the old `setDisp(...)/netDisplayTick()` body with `displayBegin();`.
- Keep `base64Encode`, WiFi, token, and publish logic unchanged otherwise.

The publish-result line becomes:
```cpp
  bool ok = code >= 200 && code < 300;
  displaySetHealth(ok ? DISP_HEALTHY : DISP_ERROR);
  return ok;
```
and the WiFi/token failure paths call `displaySetHealth(DISP_ERROR);`.

- [ ] **Step 3: Update `garden-node.ino`**

- Add `#include "display.h"` **unconditionally** (NOT under `#if ENABLE_UPLOAD`) — the display runs even in Phase 0.
- In `setup()`: call `displayBegin();` (after `sensorsBegin();`). If `ENABLE_UPLOAD`, `netBegin()` no longer touches the matrix.
- In `loop()`: call `displayTick();` every iteration (replacing the old `netDisplayTick()`), and after `Reading r = sensorsRead();` add:
```cpp
  displaySetReadings(r.soil, SOIL_PROBE_COUNT, r.tempC, r.humidity, r.dhtOk);
```
- For Phase 0 (uploads off), `displaySetHealth` is never called, so the display stays in CONNECTING (seed) — acceptable. (Optional: call `displaySetHealth(DISP_HEALTHY)` once after the first successful read so the slideshow runs on the bench; include this line in `loop()` guarded by `#if !ENABLE_UPLOAD` so bench testing shows the slideshow.)

Add after the sample read in `loop()`:
```cpp
#if !ENABLE_UPLOAD
  displaySetHealth(DISP_HEALTHY);   // bench: show the slideshow without networking
#endif
```

- [ ] **Step 4: Compile the whole sketch**

Run: `cd firmware/garden-node && cp arduino_secrets.h.example arduino_secrets.h 2>/dev/null; arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi .`
Expected: compiles; prints flash/RAM usage. (Remove the throwaway `arduino_secrets.h` after if it was created: `git status` should show it untracked/ignored.)

- [ ] **Step 5: Commit**

```bash
git add firmware/garden-node/net.h firmware/garden-node/net.cpp firmware/garden-node/garden-node.ino
git commit -m "refactor(firmware): move matrix to display module; wire slideshow into loop"
```

---

## Task 6: ASCII preview + final verification

**Files:**
- Create: `firmware/garden-node/test/preview.cpp`

- [ ] **Step 1: Add a preview that prints frames as ASCII**

`firmware/garden-node/test/preview.cpp`:
```cpp
#include "../display_render.h"
#include <cstdio>
using namespace gdisplay;
static void dump(const char* label, const Frame f) {
    printf("== %s ==\n", label);
    for (int y = 0; y < ROWS; y++) {
        for (int x = 0; x < COLS; x++) putchar(f[y][x] ? '#' : '.');
        putchar('\n');
    }
}
int main() {
    Frame f;
    render_plant(f, PLANT_FRAMES - 1); dump("plant(bloom)", f);
    render_x(f); dump("X", f);
    render_value_slide(f, IC_SOIL1, 42, 0, 100, true);  dump("bed1 42%", f);
    render_value_slide(f, IC_SOIL2, 0, 0, 100, true);   dump("bed2 0%", f);
    render_value_slide(f, IC_TEMP, 24, 0, 40, true);    dump("temp 24C", f);
    render_value_slide(f, IC_HUMID, 55, 0, 100, true);  dump("humid 55%", f);
    render_value_slide(f, IC_TEMP, 0, 0, 40, false);    dump("temp no-data", f);
    return 0;
}
```

- [ ] **Step 2: Build + eyeball the frames**

Run: `cd firmware/garden-node && c++ -std=c++17 -o /tmp/preview test/preview.cpp && /tmp/preview`
Expected: each glyph + bar looks legible (icon in left cols, bar rising from bottom in right cols; "no-data" shows a mid dash, not a full bar). Tweak `ICON_ART` if a glyph reads poorly.

- [ ] **Step 3: Full host test + sketch compile**

Run: `cd firmware/garden-node && c++ -std=c++17 -o /tmp/trender test/test_render.cpp && /tmp/trender && arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi .`
Expected: tests pass; sketch compiles.

- [ ] **Step 4: Commit + push**

```bash
git add firmware/garden-node/test/preview.cpp
git commit -m "test(display): ASCII frame preview"
git push origin main
```

- [ ] **Step 5: Flash + hardware check**

Run: `./scripts/flash.sh` (with `ENABLE_UPLOAD 1`). Confirm on the matrix: plant → bed1 → bed2 → temp → humidity cycling ~5s each; X when WiFi/publish fails; a stale/missing slide shows the mid dash, not a full bar.

---

## Notes
- `display_render.h` stays Arduino-free (only `<cstdint>`/`<cstddef>`) so the host tests compile with `c++`. Do not add `Arduino.h` includes to it.
- Glyphs are plain ASCII art — tweak `ICON_ART` freely; the preview script shows the result instantly.
- The plant animation frames are the same data currently in `net.cpp`; Task 3 is a move, not a redesign.
