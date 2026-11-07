#include "timing.hpp"

#include <atomic>
#include <charconv>
#include <chrono>
#include <cstring>
#include <iomanip>
#include <set>
#include <sstream>
#include <stdexcept>
#include <system_error>

namespace edge_lab {
namespace {
int parse_integer(const std::string& value, int minimum, int maximum) {
    int number = 0;
    const auto parsed = std::from_chars(value.data(), value.data() + value.size(), number);
    if (parsed.ec != std::errc{} || parsed.ptr != value.data() + value.size() ||
        number < minimum || number > maximum) {
        throw std::invalid_argument("invalid numeric option: " + value);
    }
    return number;
}
}  // namespace

Config parse_args(const std::vector<std::string>& args) {
    Config config;
    std::set<std::string> seen;
    for (std::size_t index = 0; index < args.size(); index += 2) {
        const auto& option = args[index];
        if (index + 1 >= args.size() || !seen.insert(option).second) {
            throw std::invalid_argument("missing value or duplicate option: " + option);
        }
        const auto& value = args[index + 1];
        if (option == "--period-ms") {
            config.period_ms = parse_integer(value, 1, 1000);
        } else if (option == "--cycles") {
            config.cycles = parse_integer(value, 2, 10000);
        } else if (option == "--work-ms") {
            config.work_ms = parse_integer(value, 0, 1000);
        } else if (option == "--stress-threads") {
            config.stress_threads = parse_integer(value, 0, 4);
        } else {
            throw std::invalid_argument("unknown option: " + option);
        }
    }
    return config;
}

Sample measure(std::size_t cycle, std::int64_t scheduled_ns, std::int64_t started_ns,
               std::int64_t finished_ns, std::optional<std::int64_t> previous_start_ns,
               std::int64_t period_ns) {
    if (period_ns <= 0 || started_ns < scheduled_ns || finished_ns < started_ns ||
        (previous_start_ns && *previous_start_ns > started_ns)) {
        throw std::invalid_argument("invalid monotonic measurement or period");
    }
    return {cycle, scheduled_ns, started_ns,
            static_cast<double>(started_ns - scheduled_ns) / 1000.0,
            previous_start_ns ? static_cast<double>(started_ns - *previous_start_ns - period_ns) / 1000.0 : 0.0,
            static_cast<double>(finished_ns - started_ns) / 1000.0,
            finished_ns - scheduled_ns > period_ns};
}

Scheduling scheduling_decision(int result, int error_number) {
    if (result == 0) {
        return {"fifo", "SCHED_FIFO enabled at priority 10"};
    }
    return {"normal", "SCHED_FIFO unavailable: " + std::string(std::strerror(error_number)) +
                       "; using normal SCHED_OTHER scheduling"};
}

std::string sample_json(const Sample& sample) {
    std::ostringstream out;
    out << std::fixed << std::setprecision(3)
        << "{\"event\":\"sample\",\"cycle\":" << sample.cycle
        << ",\"scheduled_ns\":" << sample.scheduled_ns << ",\"started_ns\":" << sample.started_ns
        << ",\"latency_us\":" << sample.latency_us << ",\"jitter_us\":" << sample.jitter_us
        << ",\"execution_us\":" << sample.execution_us
        << ",\"deadline_missed\":" << (sample.deadline_missed ? "true" : "false") << "}";
    return out.str();
}

std::string result_json(const Config& config, const std::vector<Sample>& samples, const std::string& policy) {
    std::size_t misses = 0;
    for (const auto& sample : samples) {
        if (sample.deadline_missed) {
            ++misses;
        }
    }
    std::ostringstream out;
    out << "{\"event\":\"result\",\"period_ms\":" << config.period_ms
        << ",\"cycles\":" << samples.size() << ",\"missed_deadlines\":" << misses
        << ",\"scheduler\":" << std::quoted(policy) << "}";
    return out.str();
}

std::int64_t monotonic_ns() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}

void burn_cpu(int duration_ms) {
    const auto end = monotonic_ns() + static_cast<std::int64_t>(duration_ms) * 1000000;
    std::uint64_t state = 1;
    static std::atomic<std::uint64_t> sink{0};
    while (monotonic_ns() < end) {
        for (int count = 0; count < 256; ++count) {
            state = state * 1664525U + 1013904223U;
        }
        sink.store(state, std::memory_order_relaxed);
    }
}
}  // namespace edge_lab
