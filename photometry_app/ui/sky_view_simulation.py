from __future__ import annotations

from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Callable


class SkyViewSimulationClock:
    """Continuous simulation time anchored to a monotonic real-time clock."""

    def __init__(
        self,
        simulation_time_utc: datetime | None = None,
        *,
        time_rate: float = 1.0,
        playing: bool = True,
        monotonic_source: Callable[[], float] = monotonic,
    ) -> None:

        self._monotonic_source = monotonic_source
        self._base_simulation_time_utc = self._normalize_time(simulation_time_utc or datetime.now(UTC))
        self._base_monotonic_seconds = float(self._monotonic_source())
        self._time_rate = float(time_rate)
        self._playing = bool(playing)

    @property
    def playing(self) -> bool:

        return self._playing

    @property
    def time_rate(self) -> float:

        return self._time_rate

    def simulation_time(self) -> datetime:

        if not self._playing:

            return self._base_simulation_time_utc

        elapsed_seconds = max(0.0, float(self._monotonic_source()) - self._base_monotonic_seconds)

        return self._base_simulation_time_utc + timedelta(seconds=elapsed_seconds * self._time_rate)

    def set_time(self, simulation_time_utc: datetime) -> datetime:

        self._base_simulation_time_utc = self._normalize_time(simulation_time_utc)
        self._base_monotonic_seconds = float(self._monotonic_source())

        return self._base_simulation_time_utc

    def scrub_time(self, delta: timedelta) -> datetime:

        return self.set_time(self.simulation_time() + delta)

    def play(self) -> None:

        if self._playing:

            return

        self._base_monotonic_seconds = float(self._monotonic_source())
        self._playing = True

    def pause(self) -> None:

        if not self._playing:

            return

        self._base_simulation_time_utc = self.simulation_time()
        self._base_monotonic_seconds = float(self._monotonic_source())
        self._playing = False

    def set_time_rate(self, time_rate: float) -> None:

        current_time = self.simulation_time()
        self._base_simulation_time_utc = current_time
        self._base_monotonic_seconds = float(self._monotonic_source())
        self._time_rate = float(time_rate)

    @staticmethod
    def _normalize_time(value: datetime) -> datetime:

        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
