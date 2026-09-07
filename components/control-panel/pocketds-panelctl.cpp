// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <cctype>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <dirent.h>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fcntl.h>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <optional>
#include <poll.h>
#include <set>
#include <sstream>
#include <string>
#include <sys/stat.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

namespace fs = std::filesystem;

static std::string json_escape(const std::string &input);

static std::string read_text(const fs::path &path) {
    std::ifstream stream(path);
    if (!stream) return {};
    std::ostringstream out;
    out << stream.rdbuf();
    std::string value = out.str();
    while (!value.empty() && (value.back() == '\n' || value.back() == '\r')) value.pop_back();
    return value;
}

static long long read_number(const fs::path &path, long long fallback = 0) {
    try { return std::stoll(read_text(path)); } catch (...) { return fallback; }
}

static std::optional<long long> read_optional_number(const fs::path &path) {
    const std::string text = read_text(path);
    if (text.empty()) return std::nullopt;
    try {
        size_t consumed = 0;
        const long long value = std::stoll(text, &consumed);
        if (consumed != text.size()) return std::nullopt;
        return value;
    } catch (...) {
        return std::nullopt;
    }
}

static fs::path runtime_file(const std::string &name) {
    const char *runtime = std::getenv("XDG_RUNTIME_DIR");
    const fs::path base = runtime && *runtime
        ? fs::path(runtime)
        : fs::path("/run/user") / std::to_string(getuid());
    return base / name;
}

static fs::path steam_fps_root() {
    const char *override = std::getenv("POCKETDS_STEAM_FPS_ROOT");
    if (override && *override) return fs::path(override).lexically_normal();
    const char *home = std::getenv("HOME");
    if (!home || !*home) return {};
    return (fs::path(home) / ".cache/pocketds-linux-kit/steam-fps")
        .lexically_normal();
}

static fs::path config_file(const std::string &name) {
    const char *configured = std::getenv("XDG_CONFIG_HOME");
    if (configured && *configured)
        return fs::path(configured) / "pocketds-linux-kit" / name;
    const char *home = std::getenv("HOME");
    if (!home || !*home) return {};
    return fs::path(home) / ".config/pocketds-linux-kit" / name;
}

static std::string user_executable_status(const fs::path &path) {
    struct stat metadata {};
    if (lstat(path.c_str(), &metadata) != 0)
        return errno == ENOENT ? "unavailable" : "invalid";
    if (!S_ISREG(metadata.st_mode) || metadata.st_uid != getuid()
        || metadata.st_nlink != 1 || metadata.st_size <= 0
        || (metadata.st_mode & 0022) != 0 || access(path.c_str(), X_OK) != 0)
        return "invalid";
    return "ok";
}

static std::string brightness_write_status() {
    const char *home = std::getenv("HOME");
    if (!home || !*home) return "unavailable";
    return user_executable_status(
        fs::path(home) / ".local/libexec/pocketds/pocketds-brightness"
    );
}

struct CacheFile {
    std::string status = "unavailable";
    std::string text;
    long long modified_ms = 0;
};

static CacheFile read_cache_file(const fs::path &path, uid_t owner,
                                mode_t forbidden_mode, size_t maximum) {
    CacheFile result;
    // Opening a stale FIFO must not block the entire status request.
    int flags = O_RDONLY | O_CLOEXEC | O_NONBLOCK;
#ifdef O_NOFOLLOW
    flags |= O_NOFOLLOW;
#endif
    const int descriptor = open(path.c_str(), flags);
    if (descriptor < 0) {
        if (errno != ENOENT) result.status = "invalid";
        return result;
    }
    result.status = "invalid";
    struct stat metadata {};
    if (fstat(descriptor, &metadata) != 0 || !S_ISREG(metadata.st_mode)
        || metadata.st_uid != owner || (metadata.st_mode & forbidden_mode) != 0
        || metadata.st_nlink != 1 || metadata.st_size <= 0
        || static_cast<unsigned long long>(metadata.st_size) > maximum) {
        close(descriptor);
        return result;
    }
    std::string value;
    value.reserve(static_cast<size_t>(metadata.st_size));
    char buffer[4096];
    while (value.size() <= maximum) {
        const ssize_t count = read(descriptor, buffer, sizeof(buffer));
        if (count == 0) break;
        if (count < 0) {
            if (errno == EINTR) continue;
            value.clear();
            break;
        }
        value.append(buffer, static_cast<size_t>(count));
    }
    struct stat after {};
    const bool unchanged = fstat(descriptor, &after) == 0
        && after.st_size == metadata.st_size
        && after.st_mtim.tv_sec == metadata.st_mtim.tv_sec
        && after.st_mtim.tv_nsec == metadata.st_mtim.tv_nsec;
    close(descriptor);
    if (!unchanged || value.size() != static_cast<size_t>(metadata.st_size)
        || value.size() > maximum) return result;
    result.status = "ok";
    result.text = std::move(value);
    result.modified_ms = static_cast<long long>(metadata.st_mtim.tv_sec) * 1000
        + metadata.st_mtim.tv_nsec / 1000000;
    return result;
}

static std::string read_private_runtime_text(const fs::path &path) {
    auto file = read_cache_file(path, getuid(), 0077, 65536);
    while (!file.text.empty() && (file.text.back() == '\n' || file.text.back() == '\r'))
        file.text.pop_back();
    return file.text;
}

struct JsonScalar {
    enum Kind { Null, Boolean, Number, String } kind = Null;
    std::string text;
};

// The private quota cache is deliberately a flat scalar object. Parse that
// whole grammar, including UTF-8/escapes and duplicate keys, before projecting
// its schema into status. It can never inject a sibling status field or turn
// a malformed optional cache into malformed JSON for all the other controls.
class QuotaJsonParser {
    const std::string &input;
    size_t position = 0;

    void whitespace() {
        while (position < input.size()
               && std::string(" \t\r\n").find(input[position]) != std::string::npos)
            ++position;
    }
    bool take(char c) {
        if (position >= input.size() || input[position] != c) return false;
        ++position;
        return true;
    }
    bool hex4(unsigned &value) {
        value = 0;
        for (int i = 0; i < 4; ++i) {
            if (position >= input.size()) return false;
            const char c = input[position++];
            const int digit = c >= '0' && c <= '9' ? c - '0'
                : c >= 'a' && c <= 'f' ? c - 'a' + 10
                : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
            if (digit < 0) return false;
            value = value * 16 + static_cast<unsigned>(digit);
        }
        return true;
    }
    static void utf8(std::string &value, unsigned code) {
        if (code < 0x80) value += static_cast<char>(code);
        else {
            if (code >= 0x10000) value += static_cast<char>(0xf0 | (code >> 18));
            else if (code >= 0x800) value += static_cast<char>(0xe0 | (code >> 12));
            else value += static_cast<char>(0xc0 | (code >> 6));
            if (code >= 0x10000) value += static_cast<char>(0x80 | ((code >> 12) & 0x3f));
            if (code >= 0x800) value += static_cast<char>(0x80 | ((code >> 6) & 0x3f));
            value += static_cast<char>(0x80 | (code & 0x3f));
        }
    }
    bool string(std::string &value) {
        if (!take('"')) return false;
        while (position < input.size()) {
            unsigned c = static_cast<unsigned char>(input[position++]);
            if (c == '"') return true;
            if (c < 0x20) return false;
            if (c == '\\') {
                if (position >= input.size()) return false;
                c = static_cast<unsigned char>(input[position++]);
                if (c == 'u') {
                    unsigned code = 0;
                    if (!hex4(code)) return false;
                    if (code >= 0xd800 && code <= 0xdbff) {
                        unsigned low = 0;
                        if (!take('\\') || !take('u') || !hex4(low)
                            || low < 0xdc00 || low > 0xdfff) return false;
                        code = 0x10000 + (code - 0xd800) * 0x400 + low - 0xdc00;
                    } else if (code >= 0xdc00 && code <= 0xdfff) return false;
                    utf8(value, code);
                } else {
                    switch (c) {
                        case '"': case '\\': case '/': value += static_cast<char>(c); break;
                        case 'b': value += '\b'; break;
                        case 'f': value += '\f'; break;
                        case 'n': value += '\n'; break;
                        case 'r': value += '\r'; break;
                        case 't': value += '\t'; break;
                        default: return false;
                    }
                }
            } else if (c < 0x80) value += static_cast<char>(c);
            else {
                const int remaining = c >= 0xc2 && c <= 0xdf ? 1
                    : c >= 0xe0 && c <= 0xef ? 2 : c >= 0xf0 && c <= 0xf4 ? 3 : -1;
                if (remaining < 0) return false;
                unsigned code = c & ((1u << (6 - remaining)) - 1);
                for (int i = 0; i < remaining; ++i) {
                    if (position >= input.size()) return false;
                    const unsigned next = static_cast<unsigned char>(input[position++]);
                    if ((next & 0xc0) != 0x80) return false;
                    code = (code << 6) | (next & 0x3f);
                }
                if (code < (remaining == 1 ? 0x80u : remaining == 2 ? 0x800u : 0x10000u)
                    || code > 0x10ffff || (code >= 0xd800 && code <= 0xdfff)) return false;
                utf8(value, code);
            }
            if (value.size() > 512) return false;
        }
        return false;
    }
    bool digit() const {
        return position < input.size() && input[position] >= '0' && input[position] <= '9';
    }
    bool scalar(JsonScalar &value) {
        if (position >= input.size()) return false;
        if (input[position] == '"') {
            value.kind = JsonScalar::String;
            return string(value.text);
        }
        for (const char *literal : {"null", "true", "false"}) {
            const size_t length = std::strlen(literal);
            if (input.compare(position, length, literal) == 0) {
                position += length;
                value.kind = literal[0] == 'n' ? JsonScalar::Null : JsonScalar::Boolean;
                value.text = literal;
                return true;
            }
        }
        const size_t start = position;
        take('-');
        if (!take('0')) {
            if (!digit()) return false;
            while (digit()) ++position;
        }
        if (take('.')) {
            if (!digit()) return false;
            while (digit()) ++position;
        }
        if (take('e') || take('E')) {
            if (!take('+')) take('-');
            if (!digit()) return false;
            while (digit()) ++position;
        }
        value.kind = JsonScalar::Number;
        value.text = input.substr(start, position - start);
        if (value.text.size() > 32) return false;
        char *end = nullptr;
        errno = 0;
        const double number = std::strtod(value.text.c_str(), &end);
        return errno == 0 && end == value.text.c_str() + value.text.size() && std::isfinite(number);
    }

public:
    explicit QuotaJsonParser(const std::string &text) : input(text) {}
    bool parse(std::map<std::string, JsonScalar> &values) {
        if (input.size() > 65536) return false;
        whitespace();
        if (!take('{')) return false;
        whitespace();
        if (!take('}')) {
            for (;;) {
                std::string key;
                JsonScalar value;
                if (values.size() >= 32 || !string(key) || key.size() > 64) return false;
                whitespace();
                if (!take(':')) return false;
                whitespace();
                if (!scalar(value) || !values.emplace(key, value).second) return false;
                whitespace();
                if (take('}')) break;
                if (!take(',')) return false;
                whitespace();
            }
        }
        whitespace();
        return position == input.size();
    }
};

static std::string quota_json(const std::string &text) {
    std::map<std::string, JsonScalar> fields;
    if (!QuotaJsonParser(text).parse(fields)) return "null";
    const std::set<std::string> keys = {
        "available", "fresh", "source", "synced_at", "source_timestamp",
        "limit_id", "limit_name", "plan_type", "primary_used_percent", "primary_reset",
        "primary_window_minutes", "secondary_used_percent", "secondary_reset", "secondary_window_minutes"
    };
    if (fields.size() != keys.size()) return "null";
    for (const auto &[key, value] : fields) {
        if (!keys.count(key)) return "null";
        if (value.kind == JsonScalar::String && (value.text.size() > 512
            || std::any_of(value.text.begin(), value.text.end(), [](unsigned char c) {
                return c < 0x20 || c == 0x7f;
            }))) return "null";
    }
    auto numeric = [&](const std::string &key, double low, double high, bool nullable, bool integer) {
        const auto &value = fields.at(key);
        if (value.kind == JsonScalar::Null) return nullable;
        if (value.kind != JsonScalar::Number) return false;
        const double number = std::strtod(value.text.c_str(), nullptr);
        return number >= low && number <= high && (!integer || std::floor(number) == number);
    };
    if (fields.at("available").kind != JsonScalar::Boolean || fields.at("fresh").kind != JsonScalar::Boolean
        || !numeric("synced_at", 1, 9e15, false, true)
        || !numeric("source_timestamp", 1, 9e15, true, true)) return "null";
    for (const char *key : {"source", "limit_id", "limit_name", "plan_type"})
        if (fields.at(key).kind != JsonScalar::String) return "null";
    for (const std::string prefix : {"primary", "secondary"}) {
        if (!numeric(prefix + "_used_percent", 0, 100, true, false)
            || !numeric(prefix + "_reset", 1, 9e15, true, true)
            || !numeric(prefix + "_window_minutes", 1, 525600, true, true)) return "null";
    }
    const bool available = fields.at("available").text == "true";
    const bool fresh = fields.at("fresh").text == "true";
    const bool has_window = fields.at("primary_used_percent").kind != JsonScalar::Null
        || fields.at("secondary_used_percent").kind != JsonScalar::Null;
    if (available != has_window || (!available && fresh)
        || fields.at("source").text != (available ? "app-server" : "unavailable")) return "null";
    std::string result = "{";
    for (const auto &[key, value] : fields) {
        if (result.size() > 1) result += ',';
        result += "\"" + key + "\":";
        result += value.kind == JsonScalar::String ? "\"" + json_escape(value.text) + "\"" : value.text;
    }
    return result + "}";
}

static std::optional<std::string> json_number_token(
    const std::string &json, const std::string &key
) {
    const std::string needle = "\"" + key + "\"";
    auto position = json.find(needle);
    if (position == std::string::npos) return std::nullopt;
    position = json.find(':', position + needle.size());
    if (position == std::string::npos) return std::nullopt;
    ++position;
    while (position < json.size() && std::isspace(static_cast<unsigned char>(json[position])))
        ++position;
    if (json.compare(position, 4, "null") == 0) return std::nullopt;
    const char *start = json.c_str() + position;
    char *end = nullptr;
    errno = 0;
    std::strtod(start, &end);
    if (errno != 0 || end == start) return std::nullopt;
    return json.substr(position, static_cast<size_t>(end - start));
}

static std::optional<std::string> json_string_token(
    const std::string &json, const std::string &key
) {
    const std::string needle = "\"" + key + "\"";
    auto position = json.find(needle);
    if (position == std::string::npos) return std::nullopt;
    position = json.find(':', position + needle.size());
    if (position == std::string::npos) return std::nullopt;
    ++position;
    while (position < json.size() && std::isspace(static_cast<unsigned char>(json[position])))
        ++position;
    if (json.compare(position, 4, "null") == 0 || position >= json.size()
        || json[position] != '"')
        return std::nullopt;
    std::string value;
    for (++position; position < json.size(); ++position) {
        const char c = json[position];
        if (c == '"') return value;
        if (c != '\\') {
            if (static_cast<unsigned char>(c) < 0x20) return std::nullopt;
            value += c;
            continue;
        }
        if (++position >= json.size()) return std::nullopt;
        switch (json[position]) {
            case '"': value += '"'; break;
            case '\\': value += '\\'; break;
            case '/': value += '/'; break;
            case 'b': value += '\b'; break;
            case 'f': value += '\f'; break;
            case 'n': value += '\n'; break;
            case 'r': value += '\r'; break;
            case 't': value += '\t'; break;
            default: return std::nullopt;
        }
    }
    return std::nullopt;
}

static std::optional<bool> json_bool_token(
    const std::string &json, const std::string &key
) {
    const std::string needle = "\"" + key + "\"";
    auto position = json.find(needle);
    if (position == std::string::npos) return std::nullopt;
    position = json.find(':', position + needle.size());
    if (position == std::string::npos) return std::nullopt;
    ++position;
    while (position < json.size() && std::isspace(static_cast<unsigned char>(json[position])))
        ++position;
    if (json.compare(position, 4, "true") == 0) return true;
    if (json.compare(position, 5, "false") == 0) return false;
    return std::nullopt;
}

struct GpuTelemetry {
    std::string status = "unavailable";
    std::optional<std::string> percent;
    std::optional<std::string> frequency_hz;
    std::optional<std::string> temperature_c;
    std::optional<std::string> clients;
    std::optional<long long> age_ms;
};

static GpuTelemetry gpu_telemetry(const std::string &json) {
    GpuTelemetry result;
    if (json.empty()) return result;
    const auto timestamp = json_number_token(json, "sample_unix_ms");
    if (!timestamp) return result;
    long long sample_ms = 0;
    try { sample_ms = std::stoll(*timestamp); } catch (...) { return result; }
    const auto now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()
    ).count();
    const long long age = now_ms - sample_ms;
    result.age_ms = std::min(600000LL, std::max(0LL, age));
    if (age < -5000 || age > 7000) {
        result.status = "stale";
        return result;
    }
    if (json.find("\"source\":\"msm-drm-fdinfo\"") == std::string::npos)
        return result;
    result.status = "ok";
    result.percent = json_number_token(json, "gpu_percent");
    result.frequency_hz = json_number_token(json, "gpu_freq_hz");
    result.temperature_c = json_number_token(json, "gpu_temp_c");
    result.clients = json_number_token(json, "gpu_clients");
    return result;
}

struct DisplayTelemetry {
    std::string status = "unavailable";
    std::optional<std::string> session;
    std::optional<std::string> backend;
    std::optional<std::string> compositor;
    std::optional<std::string> renderer;
    std::optional<std::string> gl_vendor;
    std::optional<std::string> gl_platform;
    std::optional<bool> dsi1_enabled;
    std::optional<std::string> dsi1_physical_width_px;
    std::optional<std::string> dsi1_physical_height_px;
    std::optional<std::string> dsi1_physical_width_mm;
    std::optional<std::string> dsi1_physical_height_mm;
    std::optional<std::string> dsi1_logical_x;
    std::optional<std::string> dsi1_logical_y;
    std::optional<std::string> dsi1_logical_width;
    std::optional<std::string> dsi1_logical_height;
    std::optional<std::string> dsi1_scale;
    std::optional<std::string> dsi1_refresh_hz;
    std::optional<bool> dsi2_enabled;
    std::optional<std::string> dsi2_physical_width_px;
    std::optional<std::string> dsi2_physical_height_px;
    std::optional<std::string> dsi2_physical_width_mm;
    std::optional<std::string> dsi2_physical_height_mm;
    std::optional<std::string> dsi2_logical_x;
    std::optional<std::string> dsi2_logical_y;
    std::optional<std::string> dsi2_logical_width;
    std::optional<std::string> dsi2_logical_height;
    std::optional<std::string> dsi2_scale;
    std::optional<std::string> dsi2_refresh_hz;
    std::optional<long long> age_ms;
};

static DisplayTelemetry display_telemetry(const std::string &json) {
    DisplayTelemetry result;
    if (json.empty()) return result;
    const auto source = json_string_token(json, "display_source");
    const auto status = json_string_token(json, "display_status");
    const auto timestamp = json_number_token(json, "display_sample_unix_ms");
    if (!source || *source != "kwin-supportInformation" || !status || !timestamp)
        return result;
    if (*status != "ok" && *status != "partial") return result;

    long long sample_ms = 0;
    try { sample_ms = std::stoll(*timestamp); } catch (...) { return result; }
    const auto now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()
    ).count();
    const long long age = now_ms - sample_ms;
    result.age_ms = std::max(0LL, age);
    if (age < -5000 || age > 125000) {
        result.status = "stale";
        return result;
    }

    result.status = *status;
    result.session = json_string_token(json, "display_session");
    result.backend = json_string_token(json, "display_backend");
    result.compositor = json_string_token(json, "display_compositor");
    result.renderer = json_string_token(json, "display_renderer");
    result.gl_vendor = json_string_token(json, "display_gl_vendor");
    result.gl_platform = json_string_token(json, "display_gl_platform");
    result.dsi1_enabled = json_bool_token(json, "display_dsi1_enabled");
    result.dsi1_physical_width_px = json_number_token(json, "display_dsi1_physical_width_px");
    result.dsi1_physical_height_px = json_number_token(json, "display_dsi1_physical_height_px");
    result.dsi1_physical_width_mm = json_number_token(json, "display_dsi1_physical_width_mm");
    result.dsi1_physical_height_mm = json_number_token(json, "display_dsi1_physical_height_mm");
    result.dsi1_logical_x = json_number_token(json, "display_dsi1_logical_x");
    result.dsi1_logical_y = json_number_token(json, "display_dsi1_logical_y");
    result.dsi1_logical_width = json_number_token(json, "display_dsi1_logical_width");
    result.dsi1_logical_height = json_number_token(json, "display_dsi1_logical_height");
    result.dsi1_scale = json_number_token(json, "display_dsi1_scale");
    result.dsi1_refresh_hz = json_number_token(json, "display_dsi1_refresh_hz");
    result.dsi2_enabled = json_bool_token(json, "display_dsi2_enabled");
    result.dsi2_physical_width_px = json_number_token(json, "display_dsi2_physical_width_px");
    result.dsi2_physical_height_px = json_number_token(json, "display_dsi2_physical_height_px");
    result.dsi2_physical_width_mm = json_number_token(json, "display_dsi2_physical_width_mm");
    result.dsi2_physical_height_mm = json_number_token(json, "display_dsi2_physical_height_mm");
    result.dsi2_logical_x = json_number_token(json, "display_dsi2_logical_x");
    result.dsi2_logical_y = json_number_token(json, "display_dsi2_logical_y");
    result.dsi2_logical_width = json_number_token(json, "display_dsi2_logical_width");
    result.dsi2_logical_height = json_number_token(json, "display_dsi2_logical_height");
    result.dsi2_scale = json_number_token(json, "display_dsi2_scale");
    result.dsi2_refresh_hz = json_number_token(json, "display_dsi2_refresh_hz");
    return result;
}

static std::string json_number_or_null(const std::optional<std::string> &value) {
    return value ? *value : "null";
}

static std::string number_token(double value, int precision = 3) {
    std::ostringstream out;
    out << std::fixed << std::setprecision(precision) << value;
    std::string result = out.str();
    while (result.size() > 1 && result.back() == '0') result.pop_back();
    if (!result.empty() && result.back() == '.') result.pop_back();
    return result;
}

struct AppFpsTelemetry {
    std::string status = "unavailable";
    std::string source = "mangohud-retroarch";
    std::optional<std::string> fps;
    std::optional<long long> age_ms;
};

static std::optional<double> strict_decimal_fps(std::string value) {
    if (!value.empty() && value.back() == '\n') value.pop_back();
    if (!value.empty() && value.back() == '\r') value.pop_back();
    if (value.empty()) return std::nullopt;

    size_t position = 0;
    while (position < value.size()
           && std::isdigit(static_cast<unsigned char>(value[position])))
        ++position;
    if (position == 0) return std::nullopt;
    if (position < value.size() && value[position] == '.') {
        const size_t fraction = ++position;
        while (position < value.size()
               && std::isdigit(static_cast<unsigned char>(value[position])))
            ++position;
        if (position == fraction) return std::nullopt;
    }
    if (position != value.size()) return std::nullopt;

    char *parsed_end = nullptr;
    errno = 0;
    const double fps = std::strtod(value.c_str(), &parsed_end);
    if (errno != 0 || parsed_end != value.c_str() + value.size()
        || !std::isfinite(fps) || fps < 0.0 || fps > 1000.0)
        return std::nullopt;
    return fps;
}

static bool private_small_regular_file(const struct stat &metadata) {
    return S_ISREG(metadata.st_mode) && metadata.st_uid == getuid()
        && (metadata.st_mode & 0077) == 0 && metadata.st_nlink == 1
        && metadata.st_size > 0 && metadata.st_size <= 64;
}

static std::optional<int> strict_game_fps_limit(const std::string &text) {
    if (text.empty()) return std::nullopt;
    char *end = nullptr;
    errno = 0;
    const long parsed = std::strtol(text.c_str(), &end, 10);
    if (errno || !end || *end) return std::nullopt;
    switch (parsed) {
        case 0: case 24: case 30: case 40: case 60: case 120:
            return static_cast<int>(parsed);
        default:
            return std::nullopt;
    }
}

struct GameFpsLimitSetting {
    int fps = 0;
    std::string status = "unavailable";
};

static GameFpsLimitSetting game_fps_limit_setting() {
    GameFpsLimitSetting result;
    const fs::path configured = config_file("game-fps-limit");
    if (configured.empty()) return result;

    struct stat metadata {};
    if (lstat(configured.c_str(), &metadata) != 0) {
        if (errno == ENOENT) result.status = "default";
    } else {
        const auto value = strict_game_fps_limit(
            read_private_runtime_text(configured)
        );
        if (S_ISREG(metadata.st_mode) && !S_ISLNK(metadata.st_mode)
            && metadata.st_uid == getuid() && metadata.st_nlink == 1
            && (metadata.st_mode & 0077) == 0 && value) {
            result.fps = *value;
            result.status = "ok";
        } else {
            result.status = "invalid";
        }
    }
    return result;
}

static AppFpsTelemetry private_rendered_fps_telemetry(
    const fs::path &path, const std::string &source
) {
    AppFpsTelemetry result;
    result.source = source;
    int flags = O_RDONLY | O_CLOEXEC | O_NONBLOCK;
#ifdef O_NOFOLLOW
    flags |= O_NOFOLLOW;
#endif
    const int descriptor = open(path.c_str(), flags);
    if (descriptor < 0) {
        if (errno != ENOENT && errno != ENOTDIR) result.status = "invalid";
        return result;
    }

    struct stat before {};
    if (fstat(descriptor, &before) != 0 || !private_small_regular_file(before)) {
        close(descriptor);
        result.status = "invalid";
        return result;
    }

    const auto now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()
    ).count();
    const long long file_ms = static_cast<long long>(before.st_mtim.tv_sec) * 1000
        + before.st_mtim.tv_nsec / 1000000;
    const long long age = now_ms - file_ms;
    result.age_ms = std::min(600000LL, std::max(0LL, age));
    if (age < -5000) {
        close(descriptor);
        result.status = "invalid";
        return result;
    }
    if (age > 3500) {
        close(descriptor);
        result.status = "stale";
        return result;
    }

    std::string value(static_cast<size_t>(before.st_size), '\0');
    size_t received = 0;
    while (received < value.size()) {
        const ssize_t count = read(
            descriptor, value.data() + received, value.size() - received
        );
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) break;
        received += static_cast<size_t>(count);
    }
    struct stat after {};
    const bool unchanged = fstat(descriptor, &after) == 0
        && private_small_regular_file(after)
        && after.st_size == before.st_size
        && after.st_mtim.tv_sec == before.st_mtim.tv_sec
        && after.st_mtim.tv_nsec == before.st_mtim.tv_nsec;
    close(descriptor);
    if (received != value.size() || !unchanged) {
        result.status = "invalid";
        return result;
    }

    const auto fps = strict_decimal_fps(value);
    if (!fps) {
        result.status = "invalid";
        return result;
    }
    result.status = "ok";
    result.fps = number_token(*fps, 1);
    return result;
}

static AppFpsTelemetry gamescope_presented_fps_telemetry() {
    return private_rendered_fps_telemetry(
        runtime_file("pocketds-gamescope-fps"), "gamescope-presented"
    );
}

static AppFpsTelemetry moonlight_rendered_fps_telemetry() {
    return private_rendered_fps_telemetry(
        runtime_file(
            "app/com.moonlight_stream.Moonlight/pocketds-rendered-fps"
        ),
        "moonlight-rendered"
    );
}

static bool name_has_suffix(const std::string &name, const std::string &suffix) {
    return name.size() >= suffix.size()
        && name.compare(name.size() - suffix.size(), suffix.size(), suffix) == 0;
}

static AppFpsTelemetry mangohud_fps_telemetry(
    const std::string &record_name,
    const std::string &directory_prefix,
    const fs::path &allowed_directory_parent,
    const std::string &source,
    const std::string &required_log_prefix
) {
    AppFpsTelemetry result;
    result.source = source;
    const fs::path record = runtime_file(record_name);
    const std::string active_directory = read_private_runtime_text(record);
    if (active_directory.empty()) return result;

    const fs::path directory(active_directory);
    const std::string leaf = directory.filename().string();
    const fs::path expected_parent = allowed_directory_parent.empty()
        ? record.parent_path() : allowed_directory_parent.lexically_normal();
    if (directory != directory.lexically_normal()
        || expected_parent != expected_parent.lexically_normal()
        || !directory.is_absolute() || directory.parent_path() != expected_parent
        || leaf.rfind(directory_prefix, 0) != 0) {
        result.status = "invalid";
        return result;
    }

    int parent_fd = open(expected_parent.c_str(), O_RDONLY | O_CLOEXEC | O_DIRECTORY
#ifdef O_NOFOLLOW
                         | O_NOFOLLOW
#endif
    );
    if (parent_fd < 0) {
        result.status = "stale";
        return result;
    }
    struct stat parent_metadata {};
    if (fstat(parent_fd, &parent_metadata) != 0
        || !S_ISDIR(parent_metadata.st_mode)
        || parent_metadata.st_uid != getuid()
        || (parent_metadata.st_mode & 0077) != 0) {
        close(parent_fd);
        result.status = "invalid";
        return result;
    }
    int directory_fd = openat(parent_fd, leaf.c_str(),
                              O_RDONLY | O_CLOEXEC | O_DIRECTORY
#ifdef O_NOFOLLOW
                              | O_NOFOLLOW
#endif
    );
    close(parent_fd);
    if (directory_fd < 0) {
        result.status = "stale";
        return result;
    }
    struct stat directory_metadata {};
    if (fstat(directory_fd, &directory_metadata) != 0
        || !S_ISDIR(directory_metadata.st_mode)
        || directory_metadata.st_uid != getuid()
        || (directory_metadata.st_mode & 0077) != 0) {
        close(directory_fd);
        result.status = "invalid";
        return result;
    }

    DIR *stream = fdopendir(dup(directory_fd));
    if (!stream) {
        close(directory_fd);
        result.status = "invalid";
        return result;
    }
    int selected_fd = -1;
    struct stat selected_metadata {};
    while (dirent *entry = readdir(stream)) {
        const std::string name(entry->d_name);
        if ((!required_log_prefix.empty()
             && name.rfind(required_log_prefix, 0) != 0)
            || !name_has_suffix(name, ".csv")
            || name_has_suffix(name, "_summary.csv"))
            continue;
        int candidate = openat(directory_fd, name.c_str(), O_RDONLY | O_CLOEXEC
#ifdef O_NOFOLLOW
                               | O_NOFOLLOW
#endif
        );
        if (candidate < 0) continue;
        struct stat metadata {};
        if (fstat(candidate, &metadata) != 0 || !S_ISREG(metadata.st_mode)
            || metadata.st_uid != getuid() || metadata.st_nlink != 1
            || metadata.st_size < 0 || metadata.st_size > 64 * 1024 * 1024) {
            close(candidate);
            continue;
        }
        const bool newer = selected_fd < 0
            || metadata.st_mtim.tv_sec > selected_metadata.st_mtim.tv_sec
            || (metadata.st_mtim.tv_sec == selected_metadata.st_mtim.tv_sec
                && metadata.st_mtim.tv_nsec > selected_metadata.st_mtim.tv_nsec);
        if (newer) {
            if (selected_fd >= 0) close(selected_fd);
            selected_fd = candidate;
            selected_metadata = metadata;
        } else {
            close(candidate);
        }
    }
    closedir(stream);
    close(directory_fd);
    if (selected_fd < 0) {
        result.status = "starting";
        return result;
    }

    const auto now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()
    ).count();
    const long long file_ms = static_cast<long long>(selected_metadata.st_mtim.tv_sec) * 1000
        + selected_metadata.st_mtim.tv_nsec / 1000000;
    const long long age = now_ms - file_ms;
    result.age_ms = std::max(0LL, age);
    if (age < -5000) {
        close(selected_fd);
        result.status = "invalid";
        return result;
    }
    if (age > 3500) {
        close(selected_fd);
        result.status = "stale";
        return result;
    }

    constexpr size_t tail_limit = 8192;
    const size_t wanted = static_cast<size_t>(std::min<off_t>(
        selected_metadata.st_size, static_cast<off_t>(tail_limit)
    ));
    const off_t offset = selected_metadata.st_size - static_cast<off_t>(wanted);
    std::string tail(wanted, '\0');
    size_t received = 0;
    while (received < wanted) {
        const ssize_t count = pread(selected_fd, tail.data() + received,
                                    wanted - received, offset + received);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) break;
        received += static_cast<size_t>(count);
    }
    close(selected_fd);
    tail.resize(received);

    size_t begin = 0;
    if (offset > 0) {
        const size_t first_newline = tail.find('\n');
        if (first_newline == std::string::npos) {
            result.status = "starting";
            return result;
        }
        begin = first_newline + 1;
    }
    const size_t complete_end = tail.rfind('\n');
    if (complete_end == std::string::npos || complete_end < begin) {
        result.status = "starting";
        return result;
    }

    std::vector<double> recent;
    size_t position = begin;
    while (position < complete_end) {
        const size_t newline = tail.find('\n', position);
        const size_t end = newline == std::string::npos ? complete_end : newline;
        const std::string line = tail.substr(position, end - position);
        char *parsed_end = nullptr;
        errno = 0;
        const double value = std::strtod(line.c_str(), &parsed_end);
        if (errno == 0 && parsed_end != line.c_str() && *parsed_end == ','
            && std::isfinite(value) && value >= 0.0 && value <= 1000.0) {
            recent.push_back(value);
            if (recent.size() > 9) recent.erase(recent.begin());
        }
        if (newline == std::string::npos) break;
        position = newline + 1;
    }
    if (recent.empty()) {
        result.status = "starting";
        return result;
    }
    std::sort(recent.begin(), recent.end());
    double median = recent[recent.size() / 2];
    if (recent.size() % 2 == 0)
        median = (recent[recent.size() / 2 - 1] + median) / 2.0;
    result.status = "ok";
    result.fps = number_token(median, 1);
    return result;
}

static AppFpsTelemetry app_fps_telemetry() {
    AppFpsTelemetry gamescope = gamescope_presented_fps_telemetry();
    if (gamescope.status == "ok" || gamescope.status == "starting")
        return gamescope;

    AppFpsTelemetry moonlight = moonlight_rendered_fps_telemetry();
    if (moonlight.status == "ok") return moonlight;

    AppFpsTelemetry steam = mangohud_fps_telemetry(
        "pocketds-steam-fps.active",
        "session.",
        steam_fps_root(),
        "mangohud-steam",
        ""
    );

    AppFpsTelemetry retroarch = mangohud_fps_telemetry(
        "pocketds-top-fps.active",
        "pocketds-top-fps.",
        {},
        "mangohud-retroarch",
        "retroarch_"
    );
    if (steam.status == "ok") return steam;
    if (retroarch.status == "ok") return retroarch;
    if (steam.status == "starting") return steam;
    if (retroarch.status == "starting") return retroarch;
    if (gamescope.status != "unavailable") return gamescope;
    if (steam.status != "unavailable") return steam;
    if (retroarch.status != "unavailable") return retroarch;
    if (moonlight.status != "unavailable") return moonlight;
    return retroarch;
}

static fs::path power_supply_root() {
    const char *override = std::getenv("POCKETDS_POWER_SUPPLY_ROOT");
    return override && *override ? fs::path(override) : fs::path("/sys/class/power_supply");
}

struct BatteryTelemetry {
    std::string state = "unavailable";
    std::optional<std::string> percent;
    std::optional<std::string> temperature_c;
    std::optional<std::string> voltage_v;
    std::optional<std::string> current_a;
    std::optional<std::string> power_w;
    std::string power_source = "unavailable";
    std::optional<bool> external_power;
    std::optional<std::string> cycles;
    std::optional<std::string> health;
};

struct SystemPowerTelemetry {
    std::optional<std::string> watts;
    std::string source = "unavailable";
};

static std::optional<std::string> voltage_current_watts(
    const fs::path &supply, bool require_positive_current
) {
    const auto voltage = read_optional_number(supply / "voltage_now");
    const auto current = read_optional_number(supply / "current_now");
    if (!voltage || *voltage < 1000000 || *voltage > 30000000 || !current
        || *current < -20000000 || *current > 20000000
        || (require_positive_current && *current <= 0))
        return std::nullopt;
    const double watts = std::abs(static_cast<double>(*voltage) * *current) / 1.0e12;
    if (watts < 0.01 || watts > 200.0) return std::nullopt;
    return number_token(watts, 2);
}

static bool is_usb_supply_type(const std::string &type) {
    return type == "USB" || type == "USB_DCP" || type == "USB_CDP"
        || type == "USB_ACA" || type == "USB_C" || type == "USB_PD"
        || type == "USB_PD_DRP" || type == "USB_FLOAT";
}

static BatteryTelemetry battery_telemetry() {
    BatteryTelemetry result;
    const fs::path root = power_supply_root();
    const fs::path battery = root / "battery";
    if (!fs::is_directory(battery)) return result;
    const auto present = read_optional_number(battery / "present");
    if (present && *present == 0) return result;

    const std::string status = read_text(battery / "status");
    if (status == "Charging") result.state = "charging";
    else if (status == "Discharging") result.state = "discharging";
    else if (status == "Full") result.state = "full";
    else if (status == "Not charging") result.state = "not-charging";
    else result.state = "unknown";

    if (const auto capacity = read_optional_number(battery / "capacity");
        capacity && *capacity >= 0 && *capacity <= 100)
        result.percent = std::to_string(*capacity);
    if (const auto temperature = read_optional_number(battery / "temp");
        temperature && *temperature >= -200 && *temperature <= 1000)
        result.temperature_c = number_token(*temperature / 10.0, 1);

    const auto voltage = read_optional_number(battery / "voltage_now");
    const auto current = read_optional_number(battery / "current_now");
    const bool valid_voltage = voltage && *voltage >= 1000000 && *voltage <= 30000000;
    const bool valid_current = current && *current >= -20000000 && *current <= 20000000;
    if (valid_voltage) result.voltage_v = number_token(*voltage / 1000000.0, 3);
    if (valid_current) result.current_a = number_token(*current / 1000000.0, 3);
    if (valid_voltage && valid_current) {
        const double watts = std::abs(static_cast<double>(*voltage) * *current) / 1.0e12;
        if (watts <= 200.0) {
            result.power_w = number_token(watts, 2);
            result.power_source = "voltage-current";
        }
    }

    if (const auto cycles = read_optional_number(battery / "cycle_count");
        cycles && *cycles >= 0)
        result.cycles = std::to_string(*cycles);
    const std::string health = read_text(battery / "health");
    if (!health.empty()) result.health = health;

    bool saw_external = false;
    bool online = false;
    try {
        for (const auto &entry : fs::directory_iterator(root)) {
            if (!entry.is_directory() || entry.path().filename() == "battery") continue;
            if (read_text(entry.path() / "type") == "Battery") continue;
            const auto value = read_optional_number(entry.path() / "online");
            if (!value) continue;
            saw_external = true;
            online = online || *value != 0;
        }
    } catch (const fs::filesystem_error &) {}
    if (saw_external) result.external_power = online;
    return result;
}

static SystemPowerTelemetry system_power_telemetry(const BatteryTelemetry &battery) {
    SystemPowerTelemetry result;
    const fs::path root = power_supply_root();
    std::vector<fs::path> supplies;
    try {
        for (const auto &entry : fs::directory_iterator(root)) {
            if (entry.is_directory()) supplies.push_back(entry.path());
        }
    } catch (const fs::filesystem_error &) {}
    std::sort(supplies.begin(), supplies.end());

    std::optional<double> strongest_usb_watts;
    for (const auto &supply : supplies) {
        const std::string type = read_text(supply / "type");
        if (!is_usb_supply_type(type)) continue;
        const auto online = read_optional_number(supply / "online");
        if (!online || *online != 1) continue;
        const auto watts = voltage_current_watts(supply, true);
        if (!watts) continue;
        try {
            const double value = std::stod(*watts);
            if (!strongest_usb_watts || value > *strongest_usb_watts)
                strongest_usb_watts = value;
        } catch (...) {}
    }
    if (strongest_usb_watts) {
        result.watts = number_token(*strongest_usb_watts, 2);
        result.source = "usb-input";
        return result;
    }

    if (battery.state == "discharging") {
        const auto watts = voltage_current_watts(root / "battery", false);
        if (watts) {
            result.watts = watts;
            result.source = "battery-discharge";
        }
    }
    return result;
}

static std::string json_bool_or_null(const std::optional<bool> &value) {
    if (!value) return "null";
    return *value ? "true" : "false";
}

static std::string json_string_or_null(const std::optional<std::string> &value) {
    return value ? "\"" + json_escape(*value) + "\"" : "null";
}

struct RecoveryTelemetry {
    std::string status = "none";
    std::optional<long long> age_ms;
    std::optional<std::string> request_id;
    std::optional<std::string> error;
    std::string request_status = "none";
    std::optional<long long> request_age_ms;
};

static RecoveryTelemetry recovery_telemetry() {
    RecoveryTelemetry result;
    const auto now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()
    ).count();

    const fs::path request_path = runtime_file("pocketds-plasma-recovery.request");
    struct stat request_metadata {};
    if (lstat(request_path.c_str(), &request_metadata) == 0) {
        if (!S_ISREG(request_metadata.st_mode) || request_metadata.st_uid != getuid()
            || (request_metadata.st_mode & 0077) != 0 || request_metadata.st_nlink != 1) {
            result.request_status = "invalid";
        } else {
#if defined(__APPLE__)
            const long long modified_ms = static_cast<long long>(request_metadata.st_mtimespec.tv_sec) * 1000
                + request_metadata.st_mtimespec.tv_nsec / 1000000;
#else
            const long long modified_ms = static_cast<long long>(request_metadata.st_mtim.tv_sec) * 1000
                + request_metadata.st_mtim.tv_nsec / 1000000;
#endif
            const long long age = now_ms - modified_ms;
            result.request_age_ms = std::max(0LL, age);
            if (age < -5000) result.request_status = "invalid";
            else if (age >= 90000) result.request_status = "stale";
            else result.request_status = "pending";
        }
    }

    const std::string json = read_private_runtime_text(
        runtime_file("pocketds-plasma-recovery-result.json")
    );
    if (json.empty()) return result;
    const auto timestamp = json_number_token(json, "worker_started_unix_ms");
    const auto okay = json_bool_token(json, "ok");
    if (!timestamp || !okay) {
        result.status = "invalid";
        return result;
    }
    long long started_ms = 0;
    try { started_ms = std::stoll(*timestamp); } catch (...) {
        result.status = "invalid";
        return result;
    }
    const long long age = now_ms - started_ms;
    result.age_ms = std::max(0LL, age);
    if (age < -5000) {
        result.status = "invalid";
        return result;
    }
    if (age > 120000) {
        result.status = "stale";
        return result;
    }
    result.status = *okay ? "ok" : "failed";
    result.request_id = json_string_token(json, "request_id");
    result.error = json_string_token(json, "error");
    if (result.error && result.error->size() > 240) result.error->resize(240);
    return result;
}

static std::string json_escape(const std::string &input) {
    std::string out;
    for (unsigned char c : input) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c >= 0x20) out += static_cast<char>(c);
        }
    }
    return out;
}

static int run(const std::vector<std::string> &args) {
    std::vector<char *> argv;
    argv.reserve(args.size() + 1);
    for (const auto &arg : args) argv.push_back(const_cast<char *>(arg.c_str()));
    argv.push_back(nullptr);
    const pid_t pid = fork();
    if (pid < 0) return 1;
    if (pid == 0) {
        execvp(argv[0], argv.data());
        _exit(127);
    }
    int status = 0;
    while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {}
    return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}

static std::map<std::string, std::string> parse_key_values(const std::string &text) {
    std::map<std::string, std::string> values;
    std::istringstream stream(text);
    std::string line;
    while (std::getline(stream, line)) {
        const auto pos = line.find('=');
        if (pos != std::string::npos) values[line.substr(0, pos)] = line.substr(pos + 1);
    }
    return values;
}

struct FanTelemetry {
    std::string status = "unavailable";
    std::optional<std::string> percent;
    std::optional<std::string> temperature_c;
    std::string profile = "unavailable";
    std::optional<long long> age_ms;
};

static FanTelemetry fan_telemetry(const fs::path &path = "/run/pocketds-fancontrol/state",
                                  uid_t owner = 0) {
    FanTelemetry result;
    const auto file = read_cache_file(path, owner, 0022, 4096);
    result.status = file.status;
    if (file.status != "ok") return result;
    const long long now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    const long long age = now_ms - file.modified_ms;
    result.age_ms = std::min(600000LL, std::max(0LL, age));
    // Fan control publishes every 1.5 seconds. Four missed polls are not a
    // fresh temperature just because the panel's status helper is still alive.
    if (age < 0 || age > 7000) {
        result.status = "stale";
        return result;
    }
    result.status = "invalid";
    std::map<std::string, std::string> fields;
    std::istringstream stream(file.text);
    std::string line;
    while (std::getline(stream, line)) {
        const auto split = line.find('=');
        if (split == std::string::npos || split == 0
            || !fields.emplace(line.substr(0, split), line.substr(split + 1)).second)
            return result;
    }
    auto integer = [&](const char *key, int low, int high) -> std::optional<int> {
        const auto entry = fields.find(key);
        if (entry == fields.end() || entry->second.empty() || entry->second.size() > 10)
            return std::nullopt;
        const auto &value = entry->second;
        const auto digits = value.begin() + (value[0] == '-' ? 1 : 0);
        if (digits == value.end() || !std::all_of(digits, value.end(), [](unsigned char c) {
            return c >= '0' && c <= '9';
        })) return std::nullopt;
        try {
            const long parsed = std::stol(value);
            if (parsed >= low && parsed <= high) return static_cast<int>(parsed);
        } catch (...) {}
        return std::nullopt;
    };
    const auto pwm = integer("pwm", 0, 255);
    const auto temperature = integer("temp_c", -50, 150);
    const auto profile = fields.find("profile");
    if (!pwm || !temperature || profile == fields.end()
        || !std::set<std::string>{"quiet", "moderate", "aggressive", "custom", "auto", "off"}.count(profile->second))
        return result;
    result.status = "ok";
    result.percent = std::to_string(static_cast<int>(std::lround(*pwm * 100.0 / 255.0)));
    result.temperature_c = std::to_string(*temperature);
    result.profile = profile->second;
    return result;
}

static std::pair<unsigned long long, unsigned long long> cpu_times() {
    std::ifstream stream("/proc/stat");
    std::string label;
    unsigned long long user = 0, nice = 0, system = 0, idle = 0, iowait = 0;
    unsigned long long irq = 0, softirq = 0, steal = 0;
    stream >> label >> user >> nice >> system >> idle >> iowait >> irq >> softirq >> steal;
    return {user + nice + system + idle + iowait + irq + softirq + steal, idle + iowait};
}

static std::pair<unsigned long long, unsigned long long> network_bytes() {
    std::ifstream stream("/proc/net/dev");
    std::string line;
    unsigned long long rx_sum = 0, tx_sum = 0;
    while (std::getline(stream, line)) {
        const auto colon = line.find(':');
        if (colon == std::string::npos) continue;
        std::string iface = line.substr(0, colon);
        iface.erase(std::remove_if(iface.begin(), iface.end(), ::isspace), iface.end());
        if (iface == "lo") continue;
        std::istringstream fields(line.substr(colon + 1));
        unsigned long long rx = 0, tx = 0, skip = 0;
        fields >> rx;
        for (int i = 0; i < 7; ++i) fields >> skip;
        fields >> tx;
        if (fields) { rx_sum += rx; tx_sum += tx; }
    }
    return {rx_sum, tx_sum};
}

static double cpu_ghz() {
    long long max_khz = 0;
    const fs::path base("/sys/devices/system/cpu/cpufreq");
    if (fs::exists(base)) {
        for (const auto &entry : fs::directory_iterator(base)) {
            if (entry.path().filename().string().rfind("policy", 0) == 0)
                max_khz = std::max(max_khz, read_number(entry.path() / "scaling_cur_freq"));
        }
    }
    return max_khz / 1000000.0;
}

static int brightness_percent(const fs::path &path) {
    const long long value = read_number(path / "brightness");
    const long long maximum = read_number(path / "max_brightness", 1);
    return static_cast<int>(std::lround(100.0 * value / std::max(1LL, maximum)));
}

static std::optional<bool> any_display_powered() {
    const auto top = read_optional_number(
        "/sys/class/backlight/ae94000.dsi.0/bl_power"
    );
    const auto bottom = read_optional_number(
        "/sys/class/backlight/sy7758-backlight/bl_power"
    );
    if (!top || !bottom) return std::nullopt;
    return *top == 0 || *bottom == 0;
}

static std::pair<int, bool> volume_status() {
    // Physical volume keys bypass panelctl, so this cache must expire before
    // the next two-second Panel refresh. A five-second cache made correct KDE
    // volume changes look disconnected from the slider.
    constexpr long long volume_cache_max_age_ms = 750;
    const fs::path cache = runtime_file("pocketds-panel-volume.state");
    const auto cached = parse_key_values(read_private_runtime_text(cache));
    const auto now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()
    ).count();
    if (cached.count("sample_ms") && cached.count("volume") && cached.count("muted")) {
        try {
            const long long age = now_ms - std::stoll(cached.at("sample_ms"));
            const int volume = std::stoi(cached.at("volume"));
            const int muted = std::stoi(cached.at("muted"));
            if (age >= 0 && age <= volume_cache_max_age_ms
                && volume >= 0 && volume <= 150
                && (muted == 0 || muted == 1))
                return {volume, muted == 1};
        } catch (...) {}
    }

    FILE *pipe = popen("wpctl get-volume @DEFAULT_AUDIO_SINK@ 2>/dev/null", "r");
    if (!pipe) return {0, false};
    char buffer[256]{};
    std::string text;
    while (fgets(buffer, sizeof(buffer), pipe)) text += buffer;
    pclose(pipe);
    double volume = 0;
    const auto colon = text.find(':');
    if (colon != std::string::npos) {
        try { volume = std::stod(text.substr(colon + 1)); } catch (...) {}
    }
    const int percent = static_cast<int>(std::lround(volume * 100));
    const bool muted = text.find("MUTED") != std::string::npos;
    const fs::path temporary = cache.string() + "." + std::to_string(getpid()) + ".tmp";
    {
        std::ofstream stream(temporary);
        if (stream)
            stream << "sample_ms=" << now_ms << '\n'
                   << "volume=" << percent << '\n'
                   << "muted=" << (muted ? 1 : 0) << '\n';
    }
    chmod(temporary.c_str(), 0600);
    std::error_code error;
    fs::rename(temporary, cache, error);
    if (error) fs::remove(temporary, error);
    return {percent, muted};
}

static void invalidate_volume_cache() {
    std::error_code error;
    fs::remove(runtime_file("pocketds-panel-volume.state"), error);
}

static std::string power_profile() {
    const std::string profile = read_text("/etc/tuned/active_profile");
    if (profile == "pocketds-powersave") return "powersave";
    if (profile == "pocketds-balanced") return "balanced";
    if (profile == "pocketds-performance") return "performance";
    return "unknown";
}

struct LidAutoPoweroffSetting {
    int minutes = 0;
    std::string status = "unavailable";
};

static bool valid_lid_auto_poweroff_minutes(const std::string &text) {
    return text == "0" || text == "60" || text == "120" ||
        text == "240" || text == "480";
}

static LidAutoPoweroffSetting lid_auto_poweroff_setting() {
    LidAutoPoweroffSetting result;
    const fs::path path = config_file("light-standby-auto-poweroff-minutes");
    if (path.empty()) return result;

    struct stat metadata {};
    if (lstat(path.c_str(), &metadata) != 0) {
        result.status = errno == ENOENT ? "default" : "invalid";
        return result;
    }
    result.status = "invalid";
    const std::string text = read_private_runtime_text(path);
    if (!valid_lid_auto_poweroff_minutes(text)) return result;
    result.minutes = std::stoi(text);
    result.status = "ok";
    return result;
}

static std::string input_mode() {
    const fs::path cache = runtime_file("pocketds-panel-input-mode.state");
    const auto cached = parse_key_values(read_private_runtime_text(cache));
    const auto now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()
    ).count();
    if (cached.count("sample_ms") && cached.count("mode")) {
        try {
            const long long age = now_ms - std::stoll(cached.at("sample_ms"));
            const std::string &mode = cached.at("mode");
            if (age >= 0 && age <= 5000
                && (mode == "joymouse" || mode == "gamepad" || mode == "other"
                    || mode == "unavailable"))
                return mode;
        } catch (...) {}
    }

    FILE *pipe = popen(
        "/usr/bin/busctl --timeout=1s get-property org.shadowblip.InputPlumber "
        "/org/shadowblip/InputPlumber/CompositeDevice0 "
        "org.shadowblip.Input.CompositeDevice ProfilePath 2>/dev/null",
        "r"
    );
    std::string mode = "unavailable";
    char buffer[512]{};
    std::string text;
    int result = -1;
    if (pipe) {
        while (fgets(buffer, sizeof(buffer), pipe)) text += buffer;
        result = pclose(pipe);
    }
    if (result == 0) {
        mode = "other";
        if (text.find("/pocketds-joymouse.yaml") != std::string::npos) mode = "joymouse";
        else if (text.find("/pocketds-gamepad.yaml") != std::string::npos) mode = "gamepad";
    }
    const fs::path temporary = cache.string() + "." + std::to_string(getpid()) + ".tmp";
    {
        std::ofstream stream(temporary);
        if (stream)
            stream << "sample_ms=" << now_ms << '\n'
                   << "mode=" << mode << '\n';
    }
    chmod(temporary.c_str(), 0600);
    std::error_code error;
    fs::rename(temporary, cache, error);
    if (error) fs::remove(temporary, error);
    return mode;
}

static int status() {
    const auto [total, idle] = cpu_times();
    const auto [rx, tx] = network_bytes();
    const auto fan = fan_telemetry();
    const auto [volume, muted] = volume_status();
    const std::string quota = quota_json(read_private_runtime_text(runtime_file("pocketds-codex-quota.json")));
    const std::string telemetry_cache = read_text(runtime_file("pocketds-gpu-status.json"));
    const auto gpu = gpu_telemetry(telemetry_cache);
    const auto display = display_telemetry(telemetry_cache);
    const auto app_fps = app_fps_telemetry();
    const bool game_runtime_active = app_fps.source == "gamescope-presented"
        && app_fps.status == "ok";
    const auto battery = battery_telemetry();
    const auto system_power = system_power_telemetry(battery);
    const auto recovery = recovery_telemetry();
    const auto lid_auto_poweroff = lid_auto_poweroff_setting();
    const auto game_fps_limit = game_fps_limit_setting();
    const auto display_powered = any_display_powered();

    std::cout << "{"
              << "\"cpu_total\":" << total << ','
              << "\"cpu_idle\":" << idle << ','
              << "\"cpu_ghz\":" << cpu_ghz() << ','
              << "\"cpu_cores\":" << sysconf(_SC_NPROCESSORS_ONLN) << ','
              << "\"rx_bytes\":" << rx << ','
              << "\"tx_bytes\":" << tx << ','
              << "\"temp_c\":" << json_number_or_null(fan.temperature_c) << ','
              << "\"fan_percent\":" << json_number_or_null(fan.percent) << ','
              << "\"fan_profile\":\"" << json_escape(fan.profile) << "\","
              << "\"fan_status\":\"" << fan.status << "\","
              << "\"fan_sample_age_ms\":"
              << (fan.age_ms ? std::to_string(*fan.age_ms) : "null") << ','
              << "\"top_brightness\":" << brightness_percent("/sys/class/backlight/ae94000.dsi.0") << ','
              << "\"bottom_brightness\":" << brightness_percent("/sys/class/backlight/sy7758-backlight") << ','
              << "\"brightness_write_status\":\""
              << brightness_write_status() << "\","
              << "\"display_powered\":" << json_bool_or_null(display_powered) << ','
              << "\"volume\":" << volume << ','
              << "\"muted\":" << (muted ? "true" : "false") << ','
              << "\"power_profile\":\"" << power_profile() << "\","
              << "\"lid_auto_poweroff_minutes\":" << lid_auto_poweroff.minutes << ','
              << "\"lid_auto_poweroff_status\":\""
              << lid_auto_poweroff.status << "\","
              << "\"game_fps_limit\":" << game_fps_limit.fps << ','
              << "\"game_fps_limit_status\":\""
              << game_fps_limit.status << "\","
              << "\"game_fps_runtime_active\":"
              << (game_runtime_active ? "true" : "false") << ','
              << "\"input_mode\":\"" << input_mode() << "\","
              << "\"gpu_status\":\"" << gpu.status << "\","
              << "\"gpu_source\":\"msm-drm-fdinfo\","
              << "\"gpu_percent\":" << json_number_or_null(gpu.percent) << ','
              << "\"gpu_freq_hz\":" << json_number_or_null(gpu.frequency_hz) << ','
              << "\"gpu_temp_c\":" << json_number_or_null(gpu.temperature_c) << ','
              << "\"gpu_clients\":" << json_number_or_null(gpu.clients) << ','
              << "\"gpu_sample_age_ms\":"
              << (gpu.age_ms ? std::to_string(*gpu.age_ms) : "null") << ','
              << "\"display_status\":\"" << display.status << "\","
              << "\"display_source\":\"kwin-supportInformation\","
              << "\"display_semantics\":\"physical-output-refresh-not-app-fps\","
              << "\"display_physical_mode_semantics\":\"logical-size-times-scale-rounded\","
              << "\"display_sample_age_ms\":"
              << (display.age_ms ? std::to_string(*display.age_ms) : "null") << ','
              << "\"display_session\":" << json_string_or_null(display.session) << ','
              << "\"display_backend\":" << json_string_or_null(display.backend) << ','
              << "\"display_compositor\":" << json_string_or_null(display.compositor) << ','
              << "\"display_renderer\":" << json_string_or_null(display.renderer) << ','
              << "\"display_gl_vendor\":" << json_string_or_null(display.gl_vendor) << ','
              << "\"display_gl_platform\":" << json_string_or_null(display.gl_platform) << ','
              << "\"display_app_fps\":" << json_number_or_null(app_fps.fps) << ','
              << "\"display_app_fps_status\":\"" << app_fps.status << "\","
              << "\"display_app_fps_source\":\"" << app_fps.source << "\","
              << "\"display_app_fps_age_ms\":"
              << (app_fps.age_ms ? std::to_string(*app_fps.age_ms) : "null") << ','
              << "\"display_dsi1_enabled\":" << json_bool_or_null(display.dsi1_enabled) << ','
              << "\"display_dsi1_physical_width_px\":" << json_number_or_null(display.dsi1_physical_width_px) << ','
              << "\"display_dsi1_physical_height_px\":" << json_number_or_null(display.dsi1_physical_height_px) << ','
              << "\"display_dsi1_physical_width_mm\":" << json_number_or_null(display.dsi1_physical_width_mm) << ','
              << "\"display_dsi1_physical_height_mm\":" << json_number_or_null(display.dsi1_physical_height_mm) << ','
              << "\"display_dsi1_logical_x\":" << json_number_or_null(display.dsi1_logical_x) << ','
              << "\"display_dsi1_logical_y\":" << json_number_or_null(display.dsi1_logical_y) << ','
              << "\"display_dsi1_logical_width\":" << json_number_or_null(display.dsi1_logical_width) << ','
              << "\"display_dsi1_logical_height\":" << json_number_or_null(display.dsi1_logical_height) << ','
              << "\"display_dsi1_scale\":" << json_number_or_null(display.dsi1_scale) << ','
              << "\"display_dsi1_refresh_hz\":" << json_number_or_null(display.dsi1_refresh_hz) << ','
              << "\"display_dsi2_enabled\":" << json_bool_or_null(display.dsi2_enabled) << ','
              << "\"display_dsi2_physical_width_px\":" << json_number_or_null(display.dsi2_physical_width_px) << ','
              << "\"display_dsi2_physical_height_px\":" << json_number_or_null(display.dsi2_physical_height_px) << ','
              << "\"display_dsi2_physical_width_mm\":" << json_number_or_null(display.dsi2_physical_width_mm) << ','
              << "\"display_dsi2_physical_height_mm\":" << json_number_or_null(display.dsi2_physical_height_mm) << ','
              << "\"display_dsi2_logical_x\":" << json_number_or_null(display.dsi2_logical_x) << ','
              << "\"display_dsi2_logical_y\":" << json_number_or_null(display.dsi2_logical_y) << ','
              << "\"display_dsi2_logical_width\":" << json_number_or_null(display.dsi2_logical_width) << ','
              << "\"display_dsi2_logical_height\":" << json_number_or_null(display.dsi2_logical_height) << ','
              << "\"display_dsi2_scale\":" << json_number_or_null(display.dsi2_scale) << ','
              << "\"display_dsi2_refresh_hz\":" << json_number_or_null(display.dsi2_refresh_hz) << ','
              << "\"battery_percent\":" << json_number_or_null(battery.percent) << ','
              << "\"battery_state\":\"" << battery.state << "\","
              << "\"battery_external_power\":" << json_bool_or_null(battery.external_power) << ','
              << "\"battery_temp_c\":" << json_number_or_null(battery.temperature_c) << ','
              << "\"battery_voltage_v\":" << json_number_or_null(battery.voltage_v) << ','
              << "\"battery_current_a\":" << json_number_or_null(battery.current_a) << ','
              << "\"battery_power_w\":" << json_number_or_null(battery.power_w) << ','
              << "\"battery_power_source\":\"" << battery.power_source << "\","
              << "\"battery_health\":" << json_string_or_null(battery.health) << ','
              << "\"battery_cycles\":" << json_number_or_null(battery.cycles) << ','
              << "\"battery_time_to_empty_s\":null,"
              << "\"battery_time_to_full_s\":null,"
              << "\"system_power_w\":" << json_number_or_null(system_power.watts) << ','
              << "\"system_power_source\":\"" << system_power.source << "\","
              << "\"recovery_status\":\"" << recovery.status << "\","
              << "\"recovery_age_ms\":"
              << (recovery.age_ms ? std::to_string(*recovery.age_ms) : "null") << ','
              << "\"recovery_request_id\":" << json_string_or_null(recovery.request_id) << ','
              << "\"recovery_error\":" << json_string_or_null(recovery.error) << ','
              << "\"recovery_request_status\":\"" << recovery.request_status << "\","
              << "\"recovery_request_age_ms\":"
              << (recovery.request_age_ms ? std::to_string(*recovery.request_age_ms) : "null") << ','
              << "\"quota\":" << quota
              << "}\n";
    return 0;
}

static bool valid_percent(const char *text, int &value) {
    char *end = nullptr;
    const long parsed = std::strtol(text, &end, 10);
    if (!end || *end != '\0' || parsed < 0 || parsed > 100) return false;
    value = static_cast<int>(parsed);
    return true;
}

static int controller_test_request(const std::string &operation, const std::string &token) {
    if ((operation != "begin" && operation != "poll" && operation != "end")
        || token.size() != 32 || !std::all_of(token.begin(), token.end(), [](char c) {
            return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
        })) return 2;
    auto fail = [&](const std::string &message) {
        std::cout << "{\"token\":\"" << token
                  << "\",\"status\":\"error\",\"buttons\":{},\"axes\":{},\"error\":\""
                  << json_escape(message) << "\"}\n";
        return 1;
    };
    const char *socket_path = "/run/pocketds-controller-test/control.sock";
#ifdef POCKETDS_CONTROLLER_TEST_TESTING
    // Only fixture binaries accept a substitute socket. Installed builds cannot.
    if (const char *configured = std::getenv("POCKETDS_CONTROLLER_TEST_SOCKET"))
        socket_path = configured;
#endif
    struct stat metadata {};
    if (lstat(socket_path, &metadata) != 0)
        return fail("手柄测试服务未就绪");
    if (!S_ISSOCK(metadata.st_mode) || (metadata.st_mode & 0007) != 0)
        return fail("手柄测试服务连接不可用");
#ifndef POCKETDS_CONTROLLER_TEST_TESTING
    if (metadata.st_uid != 0) return fail("手柄测试服务身份异常");
#endif
    sockaddr_un address {};
    address.sun_family = AF_UNIX;
    if (std::strlen(socket_path) >= sizeof(address.sun_path)) return fail("手柄测试服务连接不可用");
    std::strcpy(address.sun_path, socket_path);
    const int descriptor = socket(AF_UNIX, SOCK_STREAM, 0);
    if (descriptor < 0) return fail("无法连接手柄测试服务");
    struct Connection {
        int fd;
        ~Connection() { close(fd); }
    } connection {descriptor};
    if (fcntl(descriptor, F_SETFD, FD_CLOEXEC) != 0)
        return fail("无法连接手柄测试服务");
#ifdef SO_NOSIGPIPE
    const int no_sigpipe = 1;
    setsockopt(descriptor, SOL_SOCKET, SO_NOSIGPIPE, &no_sigpipe, sizeof(no_sigpipe));
#endif
    const int flags = fcntl(descriptor, F_GETFL, 0);
    if (flags < 0 || fcntl(descriptor, F_SETFL, flags | O_NONBLOCK) != 0)
        return fail("无法连接手柄测试服务");
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(2500);
    auto ready = [&](short events) {
        for (;;) {
            const auto left = std::chrono::duration_cast<std::chrono::milliseconds>(
                deadline - std::chrono::steady_clock::now()).count();
            if (left <= 0) return false;
            pollfd pending {descriptor, events, 0};
            const int result = poll(&pending, 1, static_cast<int>(left));
            if (result < 0 && errno == EINTR) continue;
            return result > 0 && (pending.revents & (events | POLLHUP)) != 0;
        }
    };
    if (connect(descriptor, reinterpret_cast<sockaddr *>(&address), sizeof(address)) != 0) {
        if ((errno != EINPROGRESS && errno != EAGAIN) || !ready(POLLOUT))
            return fail("手柄测试服务连接超时");
        int error = 0;
        socklen_t size = sizeof(error);
        if (getsockopt(descriptor, SOL_SOCKET, SO_ERROR, &error, &size) != 0 || error != 0)
            return fail("无法连接手柄测试服务");
    }
#if defined(__linux__) && !defined(POCKETDS_CONTROLLER_TEST_TESTING)
    ucred peer {};
    socklen_t peer_size = sizeof(peer);
    if (getsockopt(descriptor, SOL_SOCKET, SO_PEERCRED, &peer, &peer_size) != 0 || peer.uid != 0)
        return fail("手柄测试服务身份异常");
#endif
    const std::string request = "{\"op\":\"" + operation + "\",\"token\":\"" + token + "\"}\n";
    size_t sent = 0;
    while (sent < request.size()) {
        if (!ready(POLLOUT)) return fail("手柄测试服务响应超时");
        const ssize_t count = send(descriptor, request.data() + sent, request.size() - sent,
#ifdef MSG_NOSIGNAL
            MSG_NOSIGNAL
#else
            0
#endif
        );
        if (count < 0 && (errno == EINTR || errno == EAGAIN)) continue;
        if (count <= 0) return fail("手柄测试服务连接中断");
        sent += static_cast<size_t>(count);
    }
    std::string response;
    while (response.size() <= 16384) {
        if (!ready(POLLIN)) return fail("手柄测试服务响应超时");
        char buffer[4096];
        const ssize_t count = recv(descriptor, buffer, sizeof(buffer), 0);
        if (count < 0 && (errno == EINTR || errno == EAGAIN)) continue;
        if (count <= 0) return fail("手柄测试服务连接中断");
        response.append(buffer, static_cast<size_t>(count));
        const auto newline = response.find('\n');
        if (newline == std::string::npos) continue;
        response.resize(newline);
        if (response.size() > 16384 || response.empty() || response.front() != '{'
            || response.back() != '}' || json_string_token(response, "token") != token)
            return fail("手柄测试状态无效");
        const auto state = json_string_token(response, "status");
        if (!state || (*state != "starting" && *state != "active" && *state != "draining"
            && *state != "idle" && *state != "busy" && *state != "error"))
            return fail("手柄测试状态无效");
        std::cout << response << '\n';
        return 0;
    }
    return fail("手柄测试状态过长");
}

int main(int argc, char **argv) {
    if (argc == 3 && std::strcmp(argv[1], "lid-mode") == 0) {
        const std::string value = argv[2];
        if (value != "status" && value != "connected" && value != "sleep") return 2;
        const char *home = std::getenv("HOME");
        if (!home || !*home) return 1;
        const fs::path helper =
            fs::path(home) / ".local/libexec/pocketds/pocketds-lid-mode";
        if (user_executable_status(helper) != "ok") {
            std::cerr << "lid mode helper is unavailable\n";
            return 1;
        }
        return value == "status" ? run({helper.string(), "status"})
                                 : run({helper.string(), "set", value});
    }
    if (argc == 4 && std::strcmp(argv[1], "controller-test") == 0)
        return controller_test_request(argv[2], argv[3]);
    if (argc == 2 && std::strcmp(argv[1], "status") == 0) return status();
    if (argc == 3 && std::strcmp(argv[1], "fan-profile") == 0) {
        const std::string value = argv[2];
        if (value != "quiet" && value != "moderate" && value != "aggressive") return 2;
        return run({"sudo", "-n", "/usr/libexec/pocketds-fancontrol-set-profile", value});
    }
    if (argc == 3 && std::strcmp(argv[1], "fan-manual") == 0) {
        int value = 0;
        if (!valid_percent(argv[2], value) || value < 20) return 2;
        return run({"sudo", "-n", "/usr/local/libexec/pocketds-panel-root", "fan-manual", std::to_string(value)});
    }
    if (argc == 3 && std::strcmp(argv[1], "power") == 0) {
        const std::string value = argv[2];
        if (value != "powersave" && value != "balanced" && value != "performance") return 2;
        return run({"sudo", "-n", "/usr/local/libexec/pocketds-panel-root", "power", value});
    }
    if (argc == 3 && std::strcmp(argv[1], "lid-auto-poweroff") == 0) {
        const std::string value = argv[2];
        if (!valid_lid_auto_poweroff_minutes(value)) return 2;
        const char *home = std::getenv("HOME");
        if (!home || !*home) return 1;
        const fs::path helper =
            fs::path(home) / ".local/libexec/pocketds/pocketds-light-standby";
        if (!fs::exists(helper)) {
            std::cerr << "light standby helper is not installed\n";
            return 1;
        }
        return run({helper.string(), "configure-auto-poweroff", value});
    }
    if (argc == 3 && std::strcmp(argv[1], "game-limit") == 0) {
        const std::string value = argv[2];
        if (value != "off" && value != "0" && value != "24"
            && value != "30" && value != "40" && value != "60"
            && value != "120")
            return 2;
        const char *home = std::getenv("HOME");
        if (!home || !*home) return 1;
        const fs::path helper =
            fs::path(home) / ".local/bin/pocketds-game-limit";
        if (!fs::exists(helper) || fs::is_symlink(helper)) {
            std::cerr << "game limiter is not installed\n";
            return 1;
        }
        return run({helper.string(), value});
    }
    if (argc == 4 && std::strcmp(argv[1], "brightness") == 0) {
        int value = 0;
        if (!valid_percent(argv[3], value)) return 2;
        const std::string screen = argv[2];
        if (screen != "top" && screen != "bottom") return 2;
        const char *home = std::getenv("HOME");
        if (!home || !*home) return 1;
        const fs::path helper = fs::path(home) / ".local/libexec/pocketds/pocketds-brightness";
        if (!fs::exists(helper)) {
            std::cerr << "brightness state helper is not installed\n";
            return 1;
        }
        return run({helper.string(), "set", screen, std::to_string(value)});
    }
    if (argc == 3 && std::strcmp(argv[1], "volume") == 0) {
        int value = 0;
        if (!valid_percent(argv[2], value)) return 2;
        const int result = run({"wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", std::to_string(value) + "%"});
        if (result == 0) invalidate_volume_cache();
        return result;
    }
    if (argc == 2 && std::strcmp(argv[1], "mute") == 0) {
        const int result = run({"wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"});
        if (result == 0) invalidate_volume_cache();
        return result;
    }
    auto hide_overlay = [](const char *unit) {
        // The other overlay may be inactive. Hiding it is best-effort; the
        // requested overlay's start/show result remains authoritative.
        run({"systemctl", "--user", "kill", "--kill-whom=main",
             "--signal=USR2", unit});
    };
    auto show_overlay = [](const char *unit) {
        // SIGHUP is an idempotent show request. Reload remains a toggle for
        // hardware/backward compatibility, but navigation must never guess at
        // the overlay's current state.
        return run({"systemctl", "--user", "kill", "--kill-whom=main",
                    "--signal=HUP", unit});
    };
    if (argc == 2 && std::strcmp(argv[1], "keyboard") == 0) {
        hide_overlay("pocketds-touchpad.service");
        const int started = run({"systemctl", "--user", "start", "pocketds-keyboard.service"});
        if (started != 0) return started;
        return show_overlay("pocketds-keyboard.service");
    }
    if (argc == 2 && std::strcmp(argv[1], "touchpad") == 0) {
        hide_overlay("pocketds-keyboard.service");
        const int started = run({"systemctl", "--user", "start", "pocketds-touchpad.service"});
        if (started != 0) return started;
        return show_overlay("pocketds-touchpad.service");
    }
    if (argc == 2 && std::strcmp(argv[1], "input-toggle") == 0) {
        return run({"/usr/bin/pocketds-toggle-joymouse"});
    }
    if (argc == 3 && std::strcmp(argv[1], "desktop-recover") == 0) {
        if (std::strcmp(argv[2], "CONFIRM") != 0) return 2;
        return run({
            "/usr/local/bin/pocketds-plasma-recovery",
            "--recover",
            "--confirm-unit", "plasma-plasmashell.service",
            "--confirm-action", "BOUNDED-PLASMASHELL-RECOVERY",
        });
    }
    if (argc == 3 && std::strcmp(argv[1], "desktop-recovery-clear-stale") == 0) {
        if (std::strcmp(argv[2], "CONFIRM") != 0) return 2;
        return run({
            "/usr/local/bin/pocketds-plasma-recovery",
            "--clear-stale-request",
            "--confirm-unit", "plasma-plasmashell.service",
            "--confirm-action", "BOUNDED-PLASMASHELL-RECOVERY",
        });
    }
    if (argc == 3 && std::strcmp(argv[1], "boot-android") == 0) {
        if (std::strcmp(argv[2], "CONFIRM") != 0) return 2;
        return run({
            "sudo", "-n", "/usr/local/libexec/pocketds-panel-root",
            "boot-android", "CONFIRM",
        });
    }
    if (argc == 2 && std::strcmp(argv[1], "window-fullscreen") == 0) {
        return run({
            "/usr/bin/busctl", "--user", "call",
            "org.kde.kglobalaccel", "/component/kwin",
            "org.kde.kglobalaccel.Component", "invokeShortcut",
            "s", "Window Fullscreen",
        });
    }

    std::cerr << "usage: pocketds-panelctl status|fan-profile PROFILE|fan-manual PERCENT|power PROFILE|lid-auto-poweroff MINUTES|game-limit FPS|brightness SCREEN PERCENT|volume PERCENT|mute|keyboard|touchpad|input-toggle|controller-test begin|poll|end TOKEN|desktop-recover CONFIRM|desktop-recovery-clear-stale CONFIRM|boot-android CONFIRM|window-fullscreen\n";
    return 2;
}
