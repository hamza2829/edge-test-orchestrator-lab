"""Bounded input contracts and timing consistency checks."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    StrictBool,
    model_validator,
)

Name = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Scenario(Model):
    name: Name
    period_ms: int = Field(default=5, ge=1, le=1000)
    cycles: int = Field(default=20, ge=2, le=10000)
    work_ms: int = Field(default=0, ge=0, le=1000)
    stress_threads: int = Field(default=0, ge=0, le=4)
    cpus: FiniteFloat = Field(default=0.75, gt=0, le=8)
    kill_after_ms: int | None = Field(default=None, ge=1, le=5000)
    timeout_seconds: int = Field(default=30, ge=2, le=120)


class Scenarios(Model):
    scenarios: list[Scenario] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_names(self):
        if len({s.name for s in self.scenarios}) != len(self.scenarios):
            raise ValueError("scenario names must be unique")
        return self


class Sample(Model):
    event: Literal["sample"] = "sample"
    cycle: int = Field(ge=0, le=9999)
    scheduled_ns: int = Field(ge=0)
    started_ns: int = Field(ge=0)
    latency_us: FiniteFloat = Field(ge=0)
    jitter_us: FiniteFloat
    execution_us: FiniteFloat = Field(ge=0)
    deadline_missed: StrictBool

    @model_validator(mode="after")
    def consistent_latency(self):
        expected = (self.started_ns - self.scheduled_ns) / 1000
        if expected < 0 or abs(expected - self.latency_us) > 0.002:
            raise ValueError("latency_us must match the monotonic timestamps")
        return self


class Measurement(Model):
    run_id: Name
    scenario: Name
    period_ms: int = Field(ge=1, le=1000)
    expected_cycles: int = Field(ge=2, le=10000)
    cpus: FiniteFloat = Field(gt=0, le=8)
    stress_threads: int = Field(ge=0, le=4)
    work_ms: int = Field(ge=0, le=1000)
    status: Literal["complete", "killed", "timed_out", "failed"]
    exit_code: int
    scheduler: Literal["fifo", "normal", "unknown"]
    samples: list[Sample] = Field(max_length=10000)

    @model_validator(mode="after")
    def consistent_cycles(self):
        if len(self.samples) > self.expected_cycles:
            raise ValueError("more observed cycles than configured")
        if self.status == "complete" and (len(self.samples) != self.expected_cycles or self.exit_code != 0):
            raise ValueError("a complete run needs every cycle and exit code zero")
        previous = None
        for index, sample in enumerate(self.samples):
            if sample.cycle != index:
                raise ValueError("cycles must be contiguous and start at zero")
            expected_jitter = 0 if previous is None else (
                sample.started_ns - previous.started_ns) / 1000 - self.period_ms * 1000
            if abs(expected_jitter - sample.jitter_us) > 0.002:
                raise ValueError("jitter_us must match the observed start interval")
            if previous and sample.scheduled_ns - previous.scheduled_ns != self.period_ms * 1000000:
                raise ValueError("scheduled releases must follow the configured period")
            if previous and sample.started_ns < previous.started_ns:
                raise ValueError("monotonic start times must not go backwards")
            previous = sample
        return self


class Batch(Model):
    measurements: list[Measurement] = Field(min_length=1, max_length=100)
