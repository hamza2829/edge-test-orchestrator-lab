#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace edge_lab {

struct Config {
    int period_ms = 5;
    int cycles = 20;
    int work_ms = 0;
    int stress_threads = 0;
};

struct Sample {
    std::size_t cycle;
    std::int64_t scheduled_ns;
    std::int64_t started_ns;
    double latency_us;
    double jitter_us;
    double execution_us;
    bool deadline_missed;
};

struct Scheduling {
    std::string policy;
    std::string message;
};

Config parse_args(const std::vector<std::string>& args);
Sample measure(std::size_t cycle, std::int64_t scheduled_ns, std::int64_t started_ns,
               std::int64_t finished_ns, std::optional<std::int64_t> previous_start_ns,
               std::int64_t period_ns);
Scheduling scheduling_decision(int result, int error_number);
std::string sample_json(const Sample& sample);
std::string result_json(const Config& config, const std::vector<Sample>& samples,
                        const std::string& policy);
std::int64_t monotonic_ns();
void burn_cpu(int duration_ms);

}  // namespace edge_lab
