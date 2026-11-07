#include "timing.hpp"

#include <gtest/gtest.h>

#include <cerrno>
#include <stdexcept>

using edge_lab::measure;

TEST(Measurement, ExactReleaseHasZeroLatencyAndFirstJitter) {
    const auto sample = measure(0, 1000, 1000, 1100, std::nullopt, 1000);
    EXPECT_DOUBLE_EQ(sample.latency_us, 0.0);
    EXPECT_DOUBLE_EQ(sample.jitter_us, 0.0);
    EXPECT_DOUBLE_EQ(sample.execution_us, 0.1);
    EXPECT_FALSE(sample.deadline_missed);
}

TEST(Measurement, LateWakeAndOverrunMissCompletionDeadline) {
    const auto sample = measure(1, 2000, 2500, 3200, 1000, 1000);
    EXPECT_DOUBLE_EQ(sample.latency_us, 0.5);
    EXPECT_DOUBLE_EQ(sample.jitter_us, 0.5);
    EXPECT_DOUBLE_EQ(sample.execution_us, 0.7);
    EXPECT_TRUE(sample.deadline_missed);
}

TEST(Measurement, ExactDeadlineIsNotMissed) {
    EXPECT_FALSE(measure(0, 1000, 1500, 2000, std::nullopt, 1000).deadline_missed);
    EXPECT_TRUE(measure(0, 1000, 1500, 2001, std::nullopt, 1000).deadline_missed);
}

TEST(Measurement, CatchingUpProducesNegativeJitter) {
    EXPECT_DOUBLE_EQ(measure(2, 3000, 3000, 3100, 2500, 1000).jitter_us, -0.5);
}

TEST(Measurement, RejectsImpossibleClockOrder) {
    EXPECT_THROW(measure(0, 2000, 1000, 3000, std::nullopt, 1000), std::invalid_argument);
    EXPECT_THROW(measure(0, 1000, 2000, 1000, std::nullopt, 1000), std::invalid_argument);
    EXPECT_THROW(measure(0, 1000, 2000, 3000, 4000, 1000), std::invalid_argument);
    EXPECT_THROW(measure(0, 1000, 2000, 3000, std::nullopt, 0), std::invalid_argument);
}

TEST(Config, Defaults) {
    const auto config = edge_lab::parse_args({});
    EXPECT_EQ(config.period_ms, 5);
    EXPECT_EQ(config.cycles, 20);
    EXPECT_EQ(config.work_ms, 0);
    EXPECT_EQ(config.stress_threads, 0);
}

TEST(Config, AllOptions) {
    const auto config = edge_lab::parse_args({"--period-ms", "10", "--cycles", "12", "--work-ms", "2",
                                             "--stress-threads", "3"});
    EXPECT_EQ(config.period_ms, 10);
    EXPECT_EQ(config.cycles, 12);
    EXPECT_EQ(config.work_ms, 2);
    EXPECT_EQ(config.stress_threads, 3);
}

class InvalidConfig : public testing::TestWithParam<std::vector<std::string>> {};
TEST_P(InvalidConfig, RejectsInvalidArguments) {
    EXPECT_THROW(edge_lab::parse_args(GetParam()), std::invalid_argument);
}
INSTANTIATE_TEST_SUITE_P(Invalid, InvalidConfig, testing::Values(
    std::vector<std::string>{"--unknown", "1"},
    std::vector<std::string>{"--cycles"},
    std::vector<std::string>{"--cycles", "2", "--cycles", "3"},
    std::vector<std::string>{"--cycles", "1"},
    std::vector<std::string>{"--cycles", "10001"},
    std::vector<std::string>{"--period-ms", "0"},
    std::vector<std::string>{"--period-ms", "1001"},
    std::vector<std::string>{"--period-ms", "2x"},
    std::vector<std::string>{"--period-ms", "999999999999999999999"},
    std::vector<std::string>{"--period-ms", ""},
    std::vector<std::string>{"--work-ms", "-1"},
    std::vector<std::string>{"--work-ms", "1001"},
    std::vector<std::string>{"--stress-threads", "5"}));

TEST(Scheduling, SuccessIsExplicit) {
    const auto decision = edge_lab::scheduling_decision(0, 0);
    EXPECT_EQ(decision.policy, "fifo");
    EXPECT_NE(decision.message.find("SCHED_FIFO enabled"), std::string::npos);
}

TEST(Scheduling, PermissionFailureNamesFallback) {
    const auto decision = edge_lab::scheduling_decision(-1, EPERM);
    EXPECT_EQ(decision.policy, "normal");
    EXPECT_NE(decision.message.find("SCHED_FIFO unavailable"), std::string::npos);
    EXPECT_NE(decision.message.find("SCHED_OTHER"), std::string::npos);
}

TEST(Json, SampleContainsUnitsAndBoolean) {
    const auto json = edge_lab::sample_json(measure(3, 2000, 2500, 3100, 1000, 1000));
    EXPECT_NE(json.find("\"cycle\":3"), std::string::npos);
    EXPECT_NE(json.find("\"latency_us\":0.500"), std::string::npos);
    EXPECT_NE(json.find("\"deadline_missed\":true"), std::string::npos);
}

TEST(Json, ResultCountsActualMisses) {
    const std::vector<edge_lab::Sample> samples{
        measure(0, 1000, 1000, 1500, std::nullopt, 1000),
        measure(1, 2000, 2500, 3200, 1000, 1000)};
    const auto json = edge_lab::result_json({}, samples, "normal");
    EXPECT_NE(json.find("\"cycles\":2"), std::string::npos);
    EXPECT_NE(json.find("\"missed_deadlines\":1"), std::string::npos);
    EXPECT_NE(json.find("\"scheduler\":\"normal\""), std::string::npos);
}

TEST(Clock, MonotonicAndCpuWorkAdvancesTime) {
    const auto before = edge_lab::monotonic_ns();
    edge_lab::burn_cpu(1);
    EXPECT_GE(edge_lab::monotonic_ns() - before, 1000000);
    edge_lab::burn_cpu(0);
}
