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
