// SPDX-License-Identifier: GPL-3.0-or-later

#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

typedef struct SDL_Window SDL_Window;

void SDL_GL_SwapWindow(SDL_Window *window);
unsigned int fake_swap_count(void);

static void sleep_microseconds(long microseconds)
{
    struct timespec delay = {
        .tv_sec = microseconds / 1000000L,
        .tv_nsec = (microseconds % 1000000L) * 1000L,
    };

    while (nanosleep(&delay, &delay) != 0 && errno == EINTR) {
    }
}

int main(int argc, char **argv)
{
    char *end = NULL;
    long calls;
    long delay;

    if (argc != 3) {
        return 2;
    }
    calls = strtol(argv[1], &end, 10);
    if (*argv[1] == '\0' || *end != '\0' || calls < 0 || calls > 100000) {
        return 2;
    }
    delay = strtol(argv[2], &end, 10);
    if (*argv[2] == '\0' || *end != '\0' || delay < 0 || delay > 10000000) {
        return 2;
    }
    for (long index = 0; index < calls; index++) {
        SDL_GL_SwapWindow(NULL);
        if (delay > 0) {
            sleep_microseconds(delay);
        }
    }
    printf("%u\n", fake_swap_count());
    return fake_swap_count() == (unsigned int)calls ? 0 : 1;
}
