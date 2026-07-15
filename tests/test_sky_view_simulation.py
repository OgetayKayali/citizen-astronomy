from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from photometry_app.ui.sky_view_simulation import SkyViewSimulationClock


class SkyViewSimulationClockTest(unittest.TestCase):

    def test_simulation_time_advances_continuously_between_seconds(self) -> None:

        monotonic_seconds = [10.0]
        start_time = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
        clock = SkyViewSimulationClock(start_time, monotonic_source=lambda: monotonic_seconds[0])

        monotonic_seconds[0] = 10.125

        self.assertEqual(clock.simulation_time(), start_time + timedelta(milliseconds=125))

    def test_pause_freezes_time_and_play_resumes_from_frozen_time(self) -> None:

        monotonic_seconds = [10.0]
        start_time = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
        clock = SkyViewSimulationClock(start_time, monotonic_source=lambda: monotonic_seconds[0])
        monotonic_seconds[0] = 11.0
        clock.pause()
        paused_time = clock.simulation_time()

        monotonic_seconds[0] = 20.0

        self.assertEqual(clock.simulation_time(), paused_time)
        clock.play()
        monotonic_seconds[0] = 20.25
        self.assertEqual(clock.simulation_time(), paused_time + timedelta(milliseconds=250))

    def test_time_rate_reanchors_current_time_and_scales_elapsed_time(self) -> None:

        monotonic_seconds = [10.0]
        start_time = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
        clock = SkyViewSimulationClock(start_time, monotonic_source=lambda: monotonic_seconds[0])
        monotonic_seconds[0] = 10.5
        clock.set_time_rate(20.0)
        monotonic_seconds[0] = 10.75

        self.assertEqual(clock.simulation_time(), start_time + timedelta(seconds=5.5))

    def test_scrub_time_preserves_play_state(self) -> None:

        monotonic_seconds = [10.0]
        start_time = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
        clock = SkyViewSimulationClock(start_time, playing=False, monotonic_source=lambda: monotonic_seconds[0])

        self.assertEqual(clock.scrub_time(timedelta(hours=1)), start_time + timedelta(hours=1))
        self.assertFalse(clock.playing)


if __name__ == "__main__":

    unittest.main()
