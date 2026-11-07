#include "timing.hpp"

#include <cerrno>
#include <chrono>
#include <exception>
#include <iomanip>
#include <iostream>
#include <sched.h>
#include <stop_token>
#include <thread>

int main(int argc, char* argv[]) {
    try {
        const std::vector<std::string> args(argv + 1, argv + argc);
        const auto config = edge_lab::parse_args(args);
        sched_param parameter{};
        parameter.sched_priority = 10;
        const int scheduling_result = sched_setscheduler(0, SCHED_FIFO, &parameter);
        const auto scheduling = edge_lab::scheduling_decision(scheduling_result, errno);
        std::cout << "{\"event\":\"scheduler\",\"policy\":" << std::quoted(scheduling.policy)
                  << ",\"message\":" << std::quoted(scheduling.message) << "}" << std::endl;

        std::vector<std::jthread> stress;
        for (int index = 0; index < config.stress_threads; ++index) {
            stress.emplace_back([](std::stop_token stop) {
                while (!stop.stop_requested()) {
                    edge_lab::burn_cpu(1);
                }
            });
        }
        const std::int64_t period_ns = static_cast<std::int64_t>(config.period_ms) * 1000000;
        const auto origin = edge_lab::monotonic_ns();
        std::optional<std::int64_t> previous;
        std::vector<edge_lab::Sample> samples;
        samples.reserve(static_cast<std::size_t>(config.cycles));
        for (int cycle = 0; cycle < config.cycles; ++cycle) {
            const auto scheduled = origin + (static_cast<std::int64_t>(cycle) + 1) * period_ns;
            const std::chrono::steady_clock::time_point wake{std::chrono::nanoseconds{scheduled}};
            std::this_thread::sleep_until(wake);
            const auto started = edge_lab::monotonic_ns();
            edge_lab::burn_cpu(config.work_ms);
            const auto finished = edge_lab::monotonic_ns();
            auto sample = edge_lab::measure(static_cast<std::size_t>(cycle), scheduled, started,
                                           finished, previous, period_ns);
            previous = started;
            samples.push_back(sample);
            std::cout << edge_lab::sample_json(sample) << std::endl;
        }
        for (auto& thread : stress) {
            thread.request_stop();
        }
        stress.clear();
        std::cout << edge_lab::result_json(config, samples, scheduling.policy) << std::endl;
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Target error: " << error.what() << '\n';
        return 2;
    }
}
