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

int main() {
    test_bar_level();
    test_render_value_slide();
    printf("all render tests passed\n");
    return 0;
}
