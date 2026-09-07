// SPDX-License-Identifier: GPL-3.0-or-later

#include <stdatomic.h>

typedef struct SDL_Window SDL_Window;

static atomic_uint swap_count;

void SDL_GL_SwapWindow(SDL_Window *window)
{
    (void)window;
    atomic_fetch_add_explicit(&swap_count, 1U, memory_order_relaxed);
}

unsigned int fake_swap_count(void)
{
    return atomic_load_explicit(&swap_count, memory_order_relaxed);
}
