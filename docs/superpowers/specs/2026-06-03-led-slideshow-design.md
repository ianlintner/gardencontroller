# LED Matrix Slideshow — Design

**Date:** 2026-06-03
**Status:** Approved design, pending implementation plan

## Context

The garden node (Arduino UNO R4 WiFi) has a built-in 12×8 monochrome LED matrix.
Today it shows a health animation: a growing **plant** when the last push succeeded,
an **X** on failure, a **seed** while connecting (in `net.cpp`). The board now reads
multiple values — soil `bed1` (A0), soil `bed2` (A1), and air temp + humidity (DHT22
on D7). We want the matrix to **rotate through those values as a slideshow** so the
current state is glanceable without a screen or the dashboard.

## Decisions (from brainstorming)

- **Rendering:** bar graph (fill proportional to value) — calm and glanceable on 12×8.
- **Slide identity:** a small **icon + bar** per slide so similar-looking bars are distinguishable.
- **Slides (5 s each, loop):** 🌱 plant → soil bed1 → soil bed2 → temp → humidity.
- **Error/health overrides the slideshow:** ERROR → X, CONNECTING → seed, HEALTHY → slideshow.
- **Refactor:** move the matrix code out of `net.cpp` into a dedicated `display` module
  (matrix rendering is a separate responsibility from networking).

## Architecture

New module `firmware/garden-node/display.{h,cpp}` owns the matrix and the
slideshow. The plant/X/seed frames move here from `net.cpp`.

Interface:
- `void displayBegin();` — `matrix.begin()`, initial state CONNECTING.
- `void displayTick();` — called every `loop()`; non-blocking, advances the
  current animation/slide via `millis()`.
- `void displaySetHealth(DisplayHealth s);` — `DISP_CONNECTING | DISP_HEALTHY | DISP_ERROR`.
  `net.cpp` calls this (replacing today's `netSetHealth`).
- `void displaySetReadings(const SoilReading* soil, size_t count, float tempC,
   float humidity, bool dhtOk);` — `loop()` calls this after each `sensorsRead()`.

`net.cpp` keeps the WiFi/HTTP logic and calls `displaySetHealth(...)` on publish
results (success → HEALTHY, WiFi/auth/publish failure → ERROR). `net.h`'s
`netSetHealth`/`netDisplayTick` are removed in favor of the display module;
`garden-node.ino` calls `displayTick()` every loop and `displaySetReadings()`
after sampling.

## Slides & rendering

12 columns × 8 rows. Each **value slide**: icon in the left 4 columns, a bar in the
right ~8 columns (rows 0–7), filled bottom-up proportional to the value.

| Slide | Icon (left 4 cols) | Bar value |
|---|---|---|
| plant | — (full-matrix growing-plant animation, existing frames) | n/a |
| soil bed1 | water-drop glyph + small "1" | soil % (0–100) |
| soil bed2 | water-drop glyph + small "2" | soil % (0–100) |
| temp | thermometer glyph | tempC mapped over `TEMP_MIN_C`..`TEMP_MAX_C` (0–40) |
| humidity | droplet glyph | humidity % (0–100) |

- **Bar:** the right region's rows light from the bottom up; level = `round(value/range * rows)`.
- **Missing/stale data:** if a slide's value is unavailable (e.g. bed2 probe absent,
  or `dhtOk == false` for temp/humidity), show the icon with an **empty bar** (or a
  single dim "—" row) — never a misleading full bar. The plant slide always shows.
- Icons and bar frames are designed as ASCII art (like the existing plant frames)
  for readability/tweakability.

## States

- `DISP_CONNECTING` → seed frame (static).
- `DISP_ERROR` → X frame (static) — **overrides** the slideshow.
- `DISP_HEALTHY` → run the slideshow (advance one slide every `SLIDE_MS`).
State changes reset the slide index/timer so transitions are clean.

## Config (`config.h`)

- `SLIDE_MS` = 5000 (ms per slide; 5 slides → 25 s cycle).
- `TEMP_MIN_C` = 0, `TEMP_MAX_C` = 40 (temp bar range).

## Testing

- A small host script renders each frame (the 4 icons, plant frames, and bars at
  0/25/50/75/100%) to ASCII to eyeball legibility (same approach used to validate
  the plant animation and the X).
- `arduino-cli compile` for `arduino:renesas_uno:unor4wifi`.
- Flash and confirm on hardware: plant → bed1 → bed2 → temp → humidity cycling at
  5 s; X on induced failure (e.g. WiFi off); empty bar for a stale/missing slide.

## Out of scope

- OTA firmware updates (separate spec/effort).
- Any change to telemetry, the formula, or the cloud side — display-only.
