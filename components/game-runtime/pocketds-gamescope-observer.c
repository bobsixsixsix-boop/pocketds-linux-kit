#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <math.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>
#include <wayland-client.h>
#include <xcb/xcb.h>

#include "gamescope-control-client-protocol.h"

#define CONTROL_VERSION 6u
#define SAMPLE_INTERVAL_NS 50000000ull
#define FOCUS_INTERVAL_NS 100000000ull
#define LIMIT_INTERVAL_NS 200000000ull
#define STATE_INTERVAL_NS 200000000ull

struct observer {
    struct wl_display *display;
    struct gamescope_control *control;
    xcb_connection_t *xcb;
    xcb_window_t xcb_root;
    xcb_atom_t focused_app_atom;
    xcb_atom_t focused_window_atom;
    uint32_t control_version;
    uint32_t display_flags;
    uint32_t limit_flags;
    uint32_t current_app_id;
    uint32_t requested_app_id;
    uint32_t current_limit;
    uint64_t next_focus_ns;
    uint64_t next_limit_ns;
    uint64_t next_request_ns;
    uint64_t last_state_ns;
    uint64_t last_sample_ns;
    uint64_t last_frametime_ns;
    double smoothed_frametime_ns;
    unsigned sample_count;
    unsigned warmup_remaining;
    bool have_display_info;
    bool have_perf_query;
    bool request_outstanding;
    char state_path[4096];
    char limit_path[4096];
};

static volatile sig_atomic_t stopping;

static uint64_t monotonic_ns(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
        return 0;
    return (uint64_t)now.tv_sec * 1000000000ull + (uint64_t)now.tv_nsec;
}

static void handle_signal(int signum)
{
    (void)signum;
    stopping = 1;
}

static bool allowed_limit(uint32_t value)
{
    return value == 0 || value == 24 || value == 30 || value == 40
        || value == 60 || value == 120;
}

static bool parse_parent_pid(const char *text, pid_t *value)
{
    char *end = NULL;
    errno = 0;
    long parsed = text && *text ? strtol(text, &end, 10) : 0;
    if (errno || !end || *end || parsed <= 1 || parsed > INT_MAX)
        return false;
    *value = (pid_t)parsed;
    return true;
}

static bool private_runtime_dir(const char *path)
{
    struct stat metadata;
    return path && path[0] == '/' && lstat(path, &metadata) == 0
        && S_ISDIR(metadata.st_mode) && !S_ISLNK(metadata.st_mode)
        && metadata.st_uid == getuid() && (metadata.st_mode & 0022) == 0;
}

static bool build_runtime_path(char *target, size_t size,
                               const char *runtime_dir, const char *leaf)
{
    int count = snprintf(target, size, "%s/%s", runtime_dir, leaf);
    return count > 0 && (size_t)count < size;
}

static int write_fps_state(struct observer *state, uint64_t now_ns)
{
    char temporary[4096];
    int count = snprintf(temporary, sizeof(temporary), "%s.tmp.%ld",
                         state->state_path, (long)getpid());
    if (count <= 0 || (size_t)count >= sizeof(temporary))
        return -1;

    unlink(temporary);
    int descriptor = open(temporary,
                          O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC
#ifdef O_NOFOLLOW
                          | O_NOFOLLOW
#endif
                          , 0600);
    if (descriptor < 0)
        return -1;

    const double fps = state->smoothed_frametime_ns > 0.0
        ? 1000000000.0 / state->smoothed_frametime_ns : 0.0;
    char value[64];
    count = snprintf(value, sizeof(value), "%.1f\n", fps);
    ssize_t written = count > 0 ? write(descriptor, value, (size_t)count) : -1;
    int saved_errno = errno;
    if (close(descriptor) != 0 && written == count)
        written = -1;
    errno = saved_errno;
    if (written != count || rename(temporary, state->state_path) != 0) {
        unlink(temporary);
        return -1;
    }
    state->last_state_ns = now_ns;
    return 0;
}

static void feature_support(void *data, struct gamescope_control *control,
                            uint32_t feature, uint32_t version,
                            uint32_t flags)
{
    struct observer *state = data;
    (void)control;
    (void)flags;
    if (feature == GAMESCOPE_CONTROL_FEATURE_PERF_QUERY && version >= 1)
        state->have_perf_query = true;
}

static void active_display_info(void *data, struct gamescope_control *control,
                                const char *connector_name,
                                const char *display_make,
                                const char *display_model,
                                uint32_t display_flags,
                                struct wl_array *valid_refresh_rates)
{
    struct observer *state = data;
    (void)control;
    (void)connector_name;
    (void)display_make;
    (void)display_model;
    (void)valid_refresh_rates;
    state->display_flags = display_flags;
    state->limit_flags =
        display_flags & GAMESCOPE_CONTROL_DISPLAY_FLAG_INTERNAL_DISPLAY
        ? GAMESCOPE_CONTROL_TARGET_REFRESH_CYCLE_FLAG_INTERNAL_DISPLAY : 0;
    state->have_display_info = true;
}

static void screenshot_taken(void *data, struct gamescope_control *control,
                             const char *path)
{
    (void)data;
    (void)control;
    (void)path;
}

static void app_performance_stats(void *data,
                                  struct gamescope_control *control,
                                  uint32_t app_id,
                                  uint32_t frametime_ns_lo,
                                  uint32_t frametime_ns_hi)
{
    struct observer *state = data;
    uint64_t frametime_ns =
        ((uint64_t)frametime_ns_hi << 32) | frametime_ns_lo;
    uint64_t now_ns = monotonic_ns();
    (void)control;

    if (!state->request_outstanding || app_id != state->requested_app_id)
        return;
    state->request_outstanding = false;
    if (app_id == 0 || app_id != state->current_app_id
        || frametime_ns < 500000ull || frametime_ns > 5000000000ull)
        return;
    state->next_request_ns = now_ns + SAMPLE_INTERVAL_NS;
    if (state->warmup_remaining) {
        state->warmup_remaining--;
        return;
    }

    if (state->sample_count == 0)
        state->smoothed_frametime_ns = (double)frametime_ns;
    else
        state->smoothed_frametime_ns =
            state->smoothed_frametime_ns * 0.75 + (double)frametime_ns * 0.25;
    state->sample_count++;
    state->last_frametime_ns = frametime_ns;
    state->last_sample_ns = now_ns;
    if (state->last_state_ns == 0
        || now_ns - state->last_state_ns >= STATE_INTERVAL_NS)
        (void)write_fps_state(state, now_ns);
}

static const struct gamescope_control_listener control_listener = {
    .feature_support = feature_support,
    .active_display_info = active_display_info,
    .screenshot_taken = screenshot_taken,
    .app_performance_stats = app_performance_stats,
};

static void registry_global(void *data, struct wl_registry *registry,
                            uint32_t name, const char *interface,
                            uint32_t version)
{
    struct observer *state = data;
    if (strcmp(interface, gamescope_control_interface.name) != 0)
        return;
    state->control_version = version < CONTROL_VERSION ? version : CONTROL_VERSION;
    state->control = wl_registry_bind(registry, name,
                                      &gamescope_control_interface,
                                      state->control_version);
    gamescope_control_add_listener(state->control, &control_listener, state);
}

static void registry_global_remove(void *data, struct wl_registry *registry,
                                   uint32_t name)
{
    (void)data;
    (void)registry;
    (void)name;
}

static const struct wl_registry_listener registry_listener = {
    .global = registry_global,
    .global_remove = registry_global_remove,
};

static xcb_atom_t intern_atom(xcb_connection_t *connection, const char *name)
{
    xcb_intern_atom_cookie_t cookie =
        xcb_intern_atom(connection, 0, (uint16_t)strlen(name), name);
    xcb_intern_atom_reply_t *reply =
        xcb_intern_atom_reply(connection, cookie, NULL);
    if (!reply)
        return XCB_ATOM_NONE;
    xcb_atom_t atom = reply->atom;
    free(reply);
    return atom;
}

static uint32_t root_u32_property(struct observer *state, xcb_atom_t atom)
{
    if (atom == XCB_ATOM_NONE)
        return 0;
    xcb_get_property_cookie_t cookie = xcb_get_property(
        state->xcb, 0, state->xcb_root, atom, XCB_GET_PROPERTY_TYPE_ANY, 0, 1);
    xcb_get_property_reply_t *reply =
        xcb_get_property_reply(state->xcb, cookie, NULL);
    if (!reply)
        return 0;
    uint32_t result = 0;
    if (reply->format == 32 && xcb_get_property_value_length(reply) >= 4)
        memcpy(&result, xcb_get_property_value(reply), sizeof(result));
    free(reply);
    return result;
}

static uint32_t focused_app_id(struct observer *state)
{
    uint32_t app_id = root_u32_property(state, state->focused_app_atom);
    return app_id ? app_id
                  : root_u32_property(state, state->focused_window_atom);
}

static bool read_limit_file(struct observer *state, uint32_t *value)
{
    int descriptor = open(state->limit_path, O_RDONLY | O_CLOEXEC
#ifdef O_NOFOLLOW
                          | O_NOFOLLOW
#endif
    );
    if (descriptor < 0)
        return false;
    struct stat metadata;
    char text[24];
    ssize_t count = fstat(descriptor, &metadata) == 0
        && S_ISREG(metadata.st_mode) && metadata.st_uid == getuid()
        && metadata.st_nlink == 1 && (metadata.st_mode & 0077) == 0
        && metadata.st_size > 0 && metadata.st_size < (off_t)sizeof(text)
        ? read(descriptor, text, sizeof(text) - 1) : -1;
    close(descriptor);
    if (count <= 0)
        return false;
    text[count] = '\0';
    char *end = NULL;
    errno = 0;
    unsigned long parsed = strtoul(text, &end, 10);
    while (end && (*end == ' ' || *end == '\t' || *end == '\r' || *end == '\n'))
        ++end;
    if (errno || !end || *end || parsed > UINT32_MAX
        || !allowed_limit((uint32_t)parsed))
        return false;
    *value = (uint32_t)parsed;
    return true;
}

static void apply_limit(struct observer *state, uint32_t limit)
{
    if (!state->have_display_info || limit == state->current_limit)
        return;
    gamescope_control_set_app_target_refresh_cycle(
        state->control, limit, state->limit_flags);
    state->current_limit = limit;
    state->sample_count = 0;
    state->warmup_remaining = 3;
    state->smoothed_frametime_ns = 0.0;
    state->last_state_ns = 0;
}

static int setup_xcb(struct observer *state)
{
    int screen_number = 0;
    state->xcb = xcb_connect(NULL, &screen_number);
    if (!state->xcb || xcb_connection_has_error(state->xcb))
        return -1;
    const xcb_setup_t *setup = xcb_get_setup(state->xcb);
    xcb_screen_iterator_t iterator = xcb_setup_roots_iterator(setup);
    for (int index = 0; index < screen_number && iterator.rem; ++index)
        xcb_screen_next(&iterator);
    if (!iterator.rem)
        return -1;
    state->xcb_root = iterator.data->root;
    state->focused_app_atom = intern_atom(state->xcb, "GAMESCOPE_FOCUSED_APP_GFX");
    state->focused_window_atom = intern_atom(
        state->xcb, "GAMESCOPE_FOCUSED_WINDOW");
    return state->focused_window_atom == XCB_ATOM_NONE ? -1 : 0;
}

static void remove_own_state(const char *path)
{
    struct stat metadata;
    if (lstat(path, &metadata) == 0 && S_ISREG(metadata.st_mode)
        && !S_ISLNK(metadata.st_mode) && metadata.st_uid == getuid()
        && metadata.st_nlink == 1)
        unlink(path);
}

int main(void)
{
    struct observer state = {0};
    const char *runtime_dir = getenv("XDG_RUNTIME_DIR");
    const char *gamescope_display = getenv("GAMESCOPE_WAYLAND_DISPLAY");
    const char *parent_text = getenv("POCKETDS_GAMESCOPE_PARENT_PID");
    struct wl_registry *registry = NULL;
    struct sigaction action = {.sa_handler = handle_signal};
    pid_t parent_pid = 0;
    int status = 1;

    if (!parse_parent_pid(parent_text, &parent_pid)
        || !private_runtime_dir(runtime_dir)
        || !gamescope_display || !*gamescope_display
        || !build_runtime_path(state.state_path, sizeof(state.state_path),
                               runtime_dir, "pocketds-gamescope-fps")
        || !build_runtime_path(state.limit_path, sizeof(state.limit_path),
                               runtime_dir, "pocketds-gamescope-limit")) {
        fprintf(stderr, "unsafe or incomplete Gamescope runtime environment\n");
        return 2;
    }

    sigemptyset(&action.sa_mask);
    sigaction(SIGINT, &action, NULL);
    sigaction(SIGTERM, &action, NULL);
    sigaction(SIGHUP, &action, NULL);
    if (prctl(PR_SET_PDEATHSIG, SIGTERM) != 0) {
        fprintf(stderr, "cannot bind observer lifetime to its parent\n");
        return 2;
    }
    if (getppid() != parent_pid)
        return 0;

    state.display = wl_display_connect(gamescope_display);
    if (!state.display || setup_xcb(&state) != 0) {
        fprintf(stderr, "cannot connect to Gamescope control displays\n");
        goto cleanup;
    }

    registry = wl_display_get_registry(state.display);
    wl_registry_add_listener(registry, &registry_listener, &state);
    if (wl_display_roundtrip(state.display) < 0 || !state.control
        || state.control_version < CONTROL_VERSION
        || wl_display_roundtrip(state.display) < 0
        || !state.have_perf_query || !state.have_display_info) {
        fprintf(stderr, "Gamescope performance protocol v6 is unavailable\n");
        goto cleanup;
    }

    state.current_limit = UINT32_MAX;
    int wayland_fd = wl_display_get_fd(state.display);
    while (!stopping) {
        uint64_t now_ns = monotonic_ns();
        if (getppid() != parent_pid) {
            status = 0;
            break;
        }

        if (now_ns >= state.next_focus_ns) {
            uint32_t app_id = focused_app_id(&state);
            if (app_id != state.current_app_id) {
                state.current_app_id = app_id;
                state.request_outstanding = false;
                state.sample_count = 0;
                state.warmup_remaining = 3;
                state.smoothed_frametime_ns = 0.0;
                state.last_state_ns = 0;
                state.next_request_ns = 0;
            }
            state.next_focus_ns = now_ns + FOCUS_INTERVAL_NS;
        }

        if (now_ns >= state.next_limit_ns) {
            uint32_t limit = 0;
            if (read_limit_file(&state, &limit))
                apply_limit(&state, limit);
            state.next_limit_ns = now_ns + LIMIT_INTERVAL_NS;
        }

        if (state.current_app_id && !state.request_outstanding
            && now_ns >= state.next_request_ns) {
            gamescope_control_request_app_performance_stats(
                state.control, state.current_app_id);
            state.requested_app_id = state.current_app_id;
            state.request_outstanding = true;
        }

        if (wl_display_dispatch_pending(state.display) < 0)
            break;
        if (wl_display_flush(state.display) < 0 && errno != EAGAIN)
            break;
        struct pollfd descriptor = {
            .fd = wayland_fd,
            .events = POLLIN,
        };
        int ready = poll(&descriptor, 1, 50);
        if (ready < 0 && errno == EINTR)
            continue;
        if (ready < 0)
            break;
        if (ready > 0 && (descriptor.revents & (POLLERR | POLLHUP | POLLNVAL)))
            break;
        if (ready > 0 && (descriptor.revents & POLLIN)
            && wl_display_dispatch(state.display) < 0)
            break;
    }
    status = 0;

cleanup:
    if (state.control && state.have_display_info
        && state.current_limit != 0 && state.current_limit != UINT32_MAX) {
        gamescope_control_set_app_target_refresh_cycle(
            state.control, 0, state.limit_flags);
        wl_display_flush(state.display);
    }
    remove_own_state(state.state_path);
    if (state.control)
        gamescope_control_destroy(state.control);
    if (registry)
        wl_registry_destroy(registry);
    if (state.display)
        wl_display_disconnect(state.display);
    if (state.xcb)
        xcb_disconnect(state.xcb);
    return status;
}
