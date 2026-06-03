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
