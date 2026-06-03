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
