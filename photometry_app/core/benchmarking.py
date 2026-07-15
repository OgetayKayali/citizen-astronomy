from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import gc
import math
import os
from pathlib import Path
import platform
import threading
from time import perf_counter, time
from typing import Any


BENCHMARK_ENV_VAR = "CITIZEN_PHOTOMETRY_BENCHMARK"


def _env_flag_enabled(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() not in {"", "0", "false", "no", "off"}


BENCHMARK_ENABLED = _env_flag_enabled(os.getenv(BENCHMARK_ENV_VAR))


@dataclass(slots=True)
class BenchmarkSectionSample:
    scenario: str
    path: str
    name: str
    parent_path: str
    depth: int
    frame_number: int | None
    frame_phase: str
    frame_index: int | None
    timestamp_seconds: float
    seconds: float
    interaction_state: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "path": self.path,
            "name": self.name,
            "parent_path": self.parent_path,
            "depth": self.depth,
            "frame_number": self.frame_number,
            "frame_phase": self.frame_phase,
            "frame_index": self.frame_index,
            "timestamp_seconds": self.timestamp_seconds,
            "seconds": self.seconds,
            "milliseconds": self.seconds * 1000.0,
            "interaction_state": self.interaction_state,
            "metadata": _json_safe(self.metadata),
        }


@dataclass(slots=True)
class BenchmarkFrameRecord:
    scenario: str
    frame_number: int
    frame_phase: str
    frame_index: int | None
    timestamp_seconds: float
    total_seconds: float
    interaction_state: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "frame_number": self.frame_number,
            "frame_phase": self.frame_phase,
            "frame_index": self.frame_index,
            "timestamp_seconds": self.timestamp_seconds,
            "total_seconds": self.total_seconds,
            "total_milliseconds": self.total_seconds * 1000.0,
            "interaction_state": self.interaction_state,
            "metadata": _json_safe(self.metadata),
        }


@dataclass(slots=True)
class BenchmarkEvent:
    scenario: str
    name: str
    timestamp_seconds: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "name": self.name,
            "timestamp_seconds": self.timestamp_seconds,
            "metadata": _json_safe(self.metadata),
        }


@dataclass(slots=True)
class _ActiveSection:
    scenario: str
    name: str
    path: str
    parent_path: str
    depth: int
    frame_number: int | None
    frame_phase: str
    frame_index: int | None
    interaction_state: str
    start_perf_seconds: float
    timestamp_seconds: float
    metadata: dict[str, Any]


class BenchmarkRecorder:
    def __init__(self, *, enabled: bool = True, slow_frame_threshold_seconds: float = 0.050) -> None:
        self.enabled = bool(enabled)
        self.slow_frame_threshold_seconds = float(slow_frame_threshold_seconds)
        self._lock = threading.RLock()
        self._thread_local = threading.local()
        self._scenario = "manual"
        self._scenario_metadata: dict[str, dict[str, Any]] = {}
        self._frame_number = 0
        self._section_samples: list[BenchmarkSectionSample] = []
        self._frame_records: list[BenchmarkFrameRecord] = []
        self._events: list[BenchmarkEvent] = []
        self._created_wall_time = time()
        self._created_perf_counter = perf_counter()

    def reset(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._scenario = "manual"
            self._scenario_metadata.clear()
            self._frame_number = 0
            self._section_samples.clear()
            self._frame_records.clear()
            self._events.clear()
            self._created_wall_time = time()
            self._created_perf_counter = perf_counter()
        self._thread_local.stack = []
        self._thread_local.current_frame_number = None
        self._thread_local.current_frame_phase = "manual"
        self._thread_local.current_frame_index = None
        self._thread_local.current_interaction_state = "unknown"
        self._thread_local.current_frame_start = None
        self._thread_local.current_frame_metadata = {}

    def set_scenario(self, name: str, metadata: dict[str, Any] | None = None) -> None:
        if not self.enabled:
            return
        scenario = str(name or "manual")
        with self._lock:
            self._scenario = scenario
            if metadata:
                self._scenario_metadata[scenario] = _json_safe_dict(metadata)
        self.mark_event("scenario.start", metadata={"scenario": scenario, **(metadata or {})})

    def current_scenario(self) -> str:
        if not self.enabled:
            return "disabled"
        with self._lock:
            return self._scenario

    def begin_frame(
        self,
        *,
        phase: str = "measured",
        frame_index: int | None = None,
        interaction_state: str = "unknown",
        metadata: dict[str, Any] | None = None,
    ) -> int | None:
        if not self.enabled:
            return None
        with self._lock:
            self._frame_number += 1
            frame_number = self._frame_number
        self._thread_local.current_frame_number = frame_number
        self._thread_local.current_frame_phase = str(phase or "measured")
        self._thread_local.current_frame_index = frame_index
        self._thread_local.current_interaction_state = str(interaction_state or "unknown")
        self._thread_local.current_frame_start = perf_counter()
        self._thread_local.current_frame_metadata = _json_safe_dict(metadata or {})
        self._thread_local.current_frame_gc_count_before = tuple(int(value) for value in gc.get_count())
        self._thread_local.current_frame_gc_stats_before = tuple(
            int(item.get("collections", 0)) for item in gc.get_stats()
        )
        return frame_number

    def finish_frame(
        self,
        *,
        total_seconds: float | None = None,
        interaction_state: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled:
            return
        frame_number = getattr(self._thread_local, "current_frame_number", None)
        if frame_number is None:
            return
        frame_start = getattr(self._thread_local, "current_frame_start", None)
        if total_seconds is None:
            total_seconds = 0.0 if frame_start is None else perf_counter() - float(frame_start)
        merged_metadata = dict(getattr(self._thread_local, "current_frame_metadata", {}) or {})
        if metadata:
            merged_metadata.update(metadata)
        gc_count_before = tuple(getattr(self._thread_local, "current_frame_gc_count_before", ()) or ())
        gc_stats_before = tuple(getattr(self._thread_local, "current_frame_gc_stats_before", ()) or ())
        gc_count_after = tuple(int(value) for value in gc.get_count())
        gc_stats_after = tuple(int(item.get("collections", 0)) for item in gc.get_stats())
        if gc_count_before:
            merged_metadata.setdefault("gc_count_before", gc_count_before)
            merged_metadata.setdefault("gc_count_after", gc_count_after)
        if gc_stats_before:
            gc_collections_delta = tuple(
                max(0, int(after) - int(before))
                for before, after in zip(gc_stats_before, gc_stats_after)
            )
            merged_metadata.setdefault("gc_collections_delta", gc_collections_delta)
            merged_metadata.setdefault("gc_ran", any(delta > 0 for delta in gc_collections_delta))
        if float(total_seconds) >= self.slow_frame_threshold_seconds:
            try:
                merged_metadata.setdefault("python_tracked_objects_after", len(gc.get_objects()))
            except Exception:
                merged_metadata.setdefault("python_tracked_objects_after", "unavailable")
        record = BenchmarkFrameRecord(
            scenario=self.current_scenario(),
            frame_number=int(frame_number),
            frame_phase=str(getattr(self._thread_local, "current_frame_phase", "measured")),
            frame_index=getattr(self._thread_local, "current_frame_index", None),
            timestamp_seconds=time(),
            total_seconds=float(total_seconds),
            interaction_state=str(interaction_state or getattr(self._thread_local, "current_interaction_state", "unknown")),
            metadata=_json_safe_dict(merged_metadata),
        )
        with self._lock:
            self._frame_records.append(record)
        self._thread_local.current_frame_number = None
        self._thread_local.current_frame_start = None
        self._thread_local.current_frame_phase = "manual"
        self._thread_local.current_frame_index = None
        self._thread_local.current_interaction_state = "unknown"
        self._thread_local.current_frame_gc_count_before = ()
        self._thread_local.current_frame_gc_stats_before = ()

    def start_section(self, name: str, metadata: dict[str, Any] | None = None) -> _ActiveSection | None:
        if not self.enabled:
            return None
        stack = self._section_stack()
        parent_path = stack[-1].path if stack else ""
        section_name = str(name or "unnamed")
        section_path = f"{parent_path}/{section_name}" if parent_path else section_name
        active = _ActiveSection(
            scenario=self.current_scenario(),
            name=section_name,
            path=section_path,
            parent_path=parent_path,
            depth=len(stack),
            frame_number=getattr(self._thread_local, "current_frame_number", None),
            frame_phase=str(getattr(self._thread_local, "current_frame_phase", "manual")),
            frame_index=getattr(self._thread_local, "current_frame_index", None),
            interaction_state=str(getattr(self._thread_local, "current_interaction_state", "unknown")),
            start_perf_seconds=perf_counter(),
            timestamp_seconds=time(),
            metadata=_json_safe_dict(metadata or {}),
        )
        stack.append(active)
        return active

    def stop_section(self, active: _ActiveSection | None, metadata: dict[str, Any] | None = None) -> None:
        if not self.enabled or active is None:
            return
        elapsed = perf_counter() - active.start_perf_seconds
        stack = self._section_stack()
        if stack and stack[-1] is active:
            stack.pop()
        elif active in stack:
            stack.remove(active)
        merged_metadata = dict(active.metadata)
        if metadata:
            merged_metadata.update(metadata)
        sample = BenchmarkSectionSample(
            scenario=active.scenario,
            path=active.path,
            name=active.name,
            parent_path=active.parent_path,
            depth=active.depth,
            frame_number=active.frame_number,
            frame_phase=active.frame_phase,
            frame_index=active.frame_index,
            timestamp_seconds=active.timestamp_seconds,
            seconds=float(elapsed),
            interaction_state=active.interaction_state,
            metadata=_json_safe_dict(merged_metadata),
        )
        with self._lock:
            self._section_samples.append(sample)

    def mark_event(self, name: str, metadata: dict[str, Any] | None = None) -> None:
        if not self.enabled:
            return
        event = BenchmarkEvent(
            scenario=self.current_scenario(),
            name=str(name or "event"),
            timestamp_seconds=time(),
            metadata=_json_safe_dict(metadata or {}),
        )
        with self._lock:
            self._events.append(event)

    def section_samples(self) -> tuple[BenchmarkSectionSample, ...]:
        with self._lock:
            return tuple(self._section_samples)

    def frame_records(self) -> tuple[BenchmarkFrameRecord, ...]:
        with self._lock:
            return tuple(self._frame_records)

    def events(self) -> tuple[BenchmarkEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def section_summaries(self) -> list[dict[str, Any]]:
        samples_by_key: dict[tuple[str, str, str], list[BenchmarkSectionSample]] = {}
        for sample in self.section_samples():
            samples_by_key.setdefault((sample.scenario, sample.frame_phase, sample.path), []).append(sample)
        summaries: list[dict[str, Any]] = []
        for (scenario, frame_phase, path), samples in sorted(samples_by_key.items(), key=lambda item: (item[0][0], item[0][1], item[0][2])):
            seconds = [sample.seconds for sample in samples]
            first_sample = samples[0]
            stats = _summarize_seconds(seconds)
            summaries.append(
                {
                    "scenario": scenario,
                    "frame_phase": frame_phase,
                    "path": path,
                    "name": first_sample.name,
                    "parent_path": first_sample.parent_path,
                    "depth": first_sample.depth,
                    **stats,
                    "last_metadata": _json_safe(samples[-1].metadata),
                }
            )
        return summaries

    def frame_summaries(self) -> list[dict[str, Any]]:
        frames_by_scenario: dict[str, list[BenchmarkFrameRecord]] = {}
        for frame in self.frame_records():
            if frame.frame_phase != "measured":
                continue
            frames_by_scenario.setdefault(frame.scenario, []).append(frame)
        summaries: list[dict[str, Any]] = []
        for scenario, frames in sorted(frames_by_scenario.items()):
            seconds = [frame.total_seconds for frame in frames]
            summaries.append({"scenario": scenario, **_summarize_seconds(seconds)})
        return summaries

    def slow_frames(self, *, limit: int = 25) -> list[dict[str, Any]]:
        frames = sorted(
            (frame for frame in self.frame_records() if frame.total_seconds >= self.slow_frame_threshold_seconds),
            key=lambda frame: frame.total_seconds,
            reverse=True,
        )
        result: list[dict[str, Any]] = []
        samples_by_frame: dict[tuple[str, int], list[BenchmarkSectionSample]] = {}
        for sample in self.section_samples():
            if sample.frame_number is None:
                continue
            samples_by_frame.setdefault((sample.scenario, int(sample.frame_number)), []).append(sample)
        events = self.events()
        for frame in frames[: max(0, int(limit))]:
            frame_sections = sorted(
                samples_by_frame.get((frame.scenario, frame.frame_number), ()),
                key=lambda sample: sample.seconds,
                reverse=True,
            )[:20]
            frame_dict = frame.to_dict()
            frame_dict["above_threshold"] = frame.total_seconds >= self.slow_frame_threshold_seconds
            frame_dict["top_sections"] = [sample.to_dict() for sample in frame_sections]
            frame_dict["recent_events"] = [
                event.to_dict()
                for event in events
                if event.scenario == frame.scenario and 0.0 <= frame.timestamp_seconds - event.timestamp_seconds <= 1.0
            ][-20:]
            result.append(frame_dict)
        return result

    def to_dict(self, *, include_samples: bool = True) -> dict[str, Any]:
        elapsed_seconds = perf_counter() - self._created_perf_counter
        payload = {
            "enabled": self.enabled,
            "env_var": BENCHMARK_ENV_VAR,
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "elapsed_seconds": elapsed_seconds,
            "environment": collect_environment_metadata(),
            "scenario_metadata": _json_safe(self._scenario_metadata),
            "section_summaries": self.section_summaries(),
            "frame_summaries": self.frame_summaries(),
            "slow_frames": self.slow_frames(),
            "frame_records": [frame.to_dict() for frame in self.frame_records()],
            "events": [event.to_dict() for event in self.events()],
            "notes": [
                "GPU-facing operations are CPU-side proxy timings unless a section explicitly states otherwise.",
                "OpenGL texture upload and draw sections do not include GPU timer-query measurements or forced glFinish synchronization.",
            ],
        }
        if include_samples:
            payload["section_samples"] = [sample.to_dict() for sample in self.section_samples()]
        return payload

    def _section_stack(self) -> list[_ActiveSection]:
        stack = getattr(self._thread_local, "stack", None)
        if stack is None:
            stack = []
            self._thread_local.stack = stack
        return stack


class _NoOpBenchmarkRecorder:
    enabled = False
    slow_frame_threshold_seconds = 1.0 / 30.0

    def reset(self) -> None:
        return None

    def set_scenario(self, name: str, metadata: dict[str, Any] | None = None) -> None:
        del name, metadata
        return None

    def current_scenario(self) -> str:
        return "disabled"

    def begin_frame(self, **kwargs: Any) -> None:
        del kwargs
        return None

    def finish_frame(self, **kwargs: Any) -> None:
        del kwargs
        return None

    def start_section(self, name: str, metadata: dict[str, Any] | None = None) -> None:
        del name, metadata
        return None

    def stop_section(self, active: object, metadata: dict[str, Any] | None = None) -> None:
        del active, metadata
        return None

    def mark_event(self, name: str, metadata: dict[str, Any] | None = None) -> None:
        del name, metadata
        return None

    def section_samples(self) -> tuple[BenchmarkSectionSample, ...]:
        return ()

    def frame_records(self) -> tuple[BenchmarkFrameRecord, ...]:
        return ()

    def events(self) -> tuple[BenchmarkEvent, ...]:
        return ()

    def section_summaries(self) -> list[dict[str, Any]]:
        return []

    def frame_summaries(self) -> list[dict[str, Any]]:
        return []

    def slow_frames(self, *, limit: int = 25) -> list[dict[str, Any]]:
        del limit
        return []

    def to_dict(self, *, include_samples: bool = True) -> dict[str, Any]:
        del include_samples
        return {"enabled": False, "env_var": BENCHMARK_ENV_VAR, "environment": collect_environment_metadata()}


_GLOBAL_RECORDER: BenchmarkRecorder | _NoOpBenchmarkRecorder
_GLOBAL_RECORDER = BenchmarkRecorder(enabled=True) if BENCHMARK_ENABLED else _NoOpBenchmarkRecorder()


def get_benchmark_recorder() -> BenchmarkRecorder | _NoOpBenchmarkRecorder:
    return _GLOBAL_RECORDER


def reset_global_benchmark_recorder(*, enabled: bool | None = None) -> BenchmarkRecorder | _NoOpBenchmarkRecorder:
    global _GLOBAL_RECORDER
    resolved_enabled = BENCHMARK_ENABLED if enabled is None else bool(enabled)
    _GLOBAL_RECORDER = BenchmarkRecorder(enabled=True) if resolved_enabled else _NoOpBenchmarkRecorder()
    return _GLOBAL_RECORDER


def collect_environment_metadata() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "processor": platform.processor(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "process_id": os.getpid(),
        "working_directory": str(Path.cwd()),
        "benchmark_env_value": os.getenv(BENCHMARK_ENV_VAR),
    }


def _summarize_seconds(seconds: list[float]) -> dict[str, Any]:
    if not seconds:
        return {
            "count": 0,
            "total_seconds": 0.0,
            "avg_seconds": 0.0,
            "median_seconds": 0.0,
            "min_seconds": 0.0,
            "max_seconds": 0.0,
            "p95_seconds": 0.0,
            "last_seconds": 0.0,
        }
    sorted_seconds = sorted(float(value) for value in seconds)
    total_seconds = sum(sorted_seconds)
    count = len(sorted_seconds)
    return {
        "count": count,
        "total_seconds": total_seconds,
        "total_milliseconds": total_seconds * 1000.0,
        "avg_seconds": total_seconds / float(count),
        "avg_milliseconds": (total_seconds / float(count)) * 1000.0,
        "median_seconds": _percentile(sorted_seconds, 0.5),
        "median_milliseconds": _percentile(sorted_seconds, 0.5) * 1000.0,
        "min_seconds": sorted_seconds[0],
        "min_milliseconds": sorted_seconds[0] * 1000.0,
        "max_seconds": sorted_seconds[-1],
        "max_milliseconds": sorted_seconds[-1] * 1000.0,
        "p95_seconds": _percentile(sorted_seconds, 0.95),
        "p95_milliseconds": _percentile(sorted_seconds, 0.95) * 1000.0,
        "last_seconds": float(seconds[-1]),
        "last_milliseconds": float(seconds[-1]) * 1000.0,
    }


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    clamped_fraction = max(0.0, min(1.0, float(fraction)))
    position = (len(sorted_values) - 1) * clamped_fraction
    lower_index = int(math.floor(position))
    upper_index = int(math.ceil(position))
    if lower_index == upper_index:
        return float(sorted_values[lower_index])
    lower_value = float(sorted_values[lower_index])
    upper_value = float(sorted_values[upper_index])
    return lower_value + (upper_value - lower_value) * (position - float(lower_index))


def _json_safe_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _json_safe(item) for key, item in value.items()}


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except Exception:
            return str(value)
    return str(value)