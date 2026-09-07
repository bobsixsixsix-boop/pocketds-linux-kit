// SPDX-License-Identifier: GPL-3.0-or-later

#define _GNU_SOURCE

#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <math.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#ifndef O_NOFOLLOW
#define O_NOFOLLOW 0
#endif

#ifndef NAME_MAX
#define NAME_MAX 255
#endif

#ifndef PATH_MAX
#define PATH_MAX 4096
#endif

#ifndef POCKETDS_FPS_SAMPLE_NS
#define POCKETDS_FPS_SAMPLE_NS 1000000000LL
#endif

#ifndef POCKETDS_FPS_MIN_FRAMES
#define POCKETDS_FPS_MIN_FRAMES 8U
#endif

#ifndef POCKETDS_FPS_MIN_HZ
#define POCKETDS_FPS_MIN_HZ 8.0
#endif

#ifndef POCKETDS_FPS_WARMUP_WINDOWS
#define POCKETDS_FPS_WARMUP_WINDOWS 2U
#endif

#define POCKETDS_FPS_MAX_HZ 1000.0
#define POCKETDS_FPS_ENV "POCKETDS_MOONLIGHT_FPS_FILE"

typedef struct SDL_Window SDL_Window;
typedef void (*sdl_gl_swap_window_fn)(SDL_Window *window);

static pthread_once_t real_swap_once = PTHREAD_ONCE_INIT;
static pthread_mutex_t telemetry_lock = PTHREAD_MUTEX_INITIALIZER;
static sdl_gl_swap_window_fn real_swap_window;
static int64_t window_started_ns;
static uint32_t window_frames;
static uint32_t qualified_windows;
static unsigned long temporary_sequence;

static int64_t monotonic_now_ns(void)
{
    struct timespec now;

    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) {
        return 0;
    }
    return (int64_t)now.tv_sec * 1000000000LL + now.tv_nsec;
}

static void resolve_real_swap_window(void)
{
    real_swap_window = (sdl_gl_swap_window_fn)dlsym(RTLD_NEXT,
                                                      "SDL_GL_SwapWindow");
}

static bool safe_leaf_name(const char *name)
{
    size_t length;

    if (name == NULL || name[0] == '\0' || strcmp(name, ".") == 0 ||
        strcmp(name, "..") == 0) {
        return false;
    }
    length = strlen(name);
    if (length > NAME_MAX) {
        return false;
    }
    for (size_t index = 0; index < length; index++) {
        const unsigned char character = (unsigned char)name[index];

        if ((character >= 'a' && character <= 'z') ||
            (character >= 'A' && character <= 'Z') ||
            (character >= '0' && character <= '9') || character == '.' ||
            character == '_' || character == '-') {
            continue;
        }
        return false;
    }
    return true;
}

static int open_private_parent(const char *configured_path,
                               char output_name[NAME_MAX + 1])
{
    char path[PATH_MAX];
    char *separator;
    const char *name;
    struct stat metadata;
    int directory_fd;
    size_t length;

    if (configured_path == NULL || configured_path[0] != '/') {
        return -1;
    }
    length = strlen(configured_path);
    if (length < 2 || length >= sizeof(path) ||
        configured_path[length - 1] == '/') {
        return -1;
    }
    memcpy(path, configured_path, length + 1);
    separator = strrchr(path, '/');
    if (separator == NULL || separator[1] == '\0') {
        return -1;
    }
    name = separator + 1;
    if (!safe_leaf_name(name)) {
        return -1;
    }
    memcpy(output_name, name, strlen(name) + 1);

    if (separator == path) {
        separator[1] = '\0';
    } else {
        *separator = '\0';
    }
    directory_fd = open(path, O_RDONLY | O_CLOEXEC | O_DIRECTORY | O_NOFOLLOW);
    if (directory_fd < 0) {
        return -1;
    }
    if (fstat(directory_fd, &metadata) != 0 ||
        !S_ISDIR(metadata.st_mode) || metadata.st_uid != geteuid() ||
        (metadata.st_mode & 0077) != 0) {
        close(directory_fd);
        return -1;
    }
    return directory_fd;
}

static bool write_complete(int file_fd, const char *payload, size_t length)
{
    size_t written = 0;

    while (written < length) {
        const ssize_t count = write(file_fd, payload + written,
                                    length - written);

        if (count < 0 && errno == EINTR) {
            continue;
        }
        if (count <= 0) {
            return false;
        }
        written += (size_t)count;
    }
    return true;
}

static void publish_fps(double fps)
{
    const char *configured_path = getenv(POCKETDS_FPS_ENV);
    char output_name[NAME_MAX + 1];
    char temporary_name[96];
    char payload[32];
    int directory_fd;
    int temporary_fd = -1;
    int payload_length;
    unsigned int fps_tenths;

    if (!isfinite(fps) || fps < POCKETDS_FPS_MIN_HZ ||
        fps > POCKETDS_FPS_MAX_HZ) {
        return;
    }
    directory_fd = open_private_parent(configured_path, output_name);
    if (directory_fd < 0) {
        return;
    }
    fps_tenths = (unsigned int)(fps * 10.0 + 0.5);
    payload_length = snprintf(payload, sizeof(payload), "%u.%u\n",
                              fps_tenths / 10U, fps_tenths % 10U);
    if (payload_length <= 0 || (size_t)payload_length >= sizeof(payload)) {
        close(directory_fd);
        return;
    }

    for (unsigned int attempt = 0; attempt < 4; attempt++) {
        temporary_sequence++;
        const int name_length = snprintf(
            temporary_name, sizeof(temporary_name),
            ".pocketds-moonlight-fps.%ld.%lu.tmp", (long)getpid(),
            temporary_sequence);

        if (name_length <= 0 || (size_t)name_length >= sizeof(temporary_name)) {
            break;
        }
        temporary_fd = openat(directory_fd, temporary_name,
                              O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC |
                                  O_NOFOLLOW,
                              0600);
        if (temporary_fd >= 0 || errno != EEXIST) {
            break;
        }
    }
    if (temporary_fd < 0) {
        close(directory_fd);
        return;
    }
    if (fchmod(temporary_fd, 0600) != 0 ||
        !write_complete(temporary_fd, payload, (size_t)payload_length)) {
        close(temporary_fd);
        temporary_fd = -1;
        unlinkat(directory_fd, temporary_name, 0);
        close(directory_fd);
        return;
    }
    if (close(temporary_fd) != 0) {
        temporary_fd = -1;
        unlinkat(directory_fd, temporary_name, 0);
        close(directory_fd);
        return;
    }
    temporary_fd = -1;
    if (renameat(directory_fd, temporary_name, directory_fd, output_name) !=
        0) {
        unlinkat(directory_fd, temporary_name, 0);
    }
    close(directory_fd);
}

static void observe_presented_frame(void)
{
    int64_t now_ns;
    int64_t elapsed_ns;
    double fps;

    if (pthread_mutex_trylock(&telemetry_lock) != 0) {
        return;
    }
    now_ns = monotonic_now_ns();
    if (now_ns <= 0) {
        pthread_mutex_unlock(&telemetry_lock);
        return;
    }
    if (window_started_ns == 0 || now_ns <= window_started_ns) {
        window_started_ns = now_ns;
        window_frames = 0;
        qualified_windows = 0;
        pthread_mutex_unlock(&telemetry_lock);
        return;
    }

    window_frames++;
    elapsed_ns = now_ns - window_started_ns;
    if (elapsed_ns < POCKETDS_FPS_SAMPLE_NS) {
        pthread_mutex_unlock(&telemetry_lock);
        return;
    }
    fps = (double)window_frames * 1000000000.0 / (double)elapsed_ns;
    if (window_frames >= POCKETDS_FPS_MIN_FRAMES &&
        isfinite(fps) && fps >= POCKETDS_FPS_MIN_HZ &&
        fps <= POCKETDS_FPS_MAX_HZ) {
        if (qualified_windows < POCKETDS_FPS_WARMUP_WINDOWS) {
            qualified_windows++;
        }
        if (qualified_windows >= POCKETDS_FPS_WARMUP_WINDOWS) {
            publish_fps(fps);
        }
    } else {
        qualified_windows = 0;
    }
    window_started_ns = now_ns;
    window_frames = 0;
    pthread_mutex_unlock(&telemetry_lock);
}

__attribute__((visibility("default"))) void
SDL_GL_SwapWindow(SDL_Window *window)
{
    pthread_once(&real_swap_once, resolve_real_swap_window);
    if (real_swap_window == NULL) {
        return;
    }

    real_swap_window(window);
    observe_presented_frame();
}
