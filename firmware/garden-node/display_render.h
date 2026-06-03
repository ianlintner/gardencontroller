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

enum Icon { IC_SOIL1 = 0, IC_SOIL2, IC_TEMP, IC_HUMID, ICON_COUNT };

// 4-wide x 8-tall glyphs, '#' = lit. Each row is exactly 4 chars.
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

// Bar in cols 5..11, bottom-up. valid=false -> a single "no data" dash at mid-row.
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

}  // namespace gdisplay
