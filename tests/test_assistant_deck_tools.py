from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from astropy.io import fits
import numpy as np

from photometry_app.assistant.deck_tools import deck_tools
from photometry_app.assistant.file_guard import GuardedFileWriter
from photometry_app.assistant.messages import ToolCall
from photometry_app.assistant.tools import ApprovalDecision, ToolContext, ToolRegistry, execute_tool_call
from photometry_app.core.models import AppMode
from photometry_app.core.observation_deck import (
    ObservationDeckLibrary,
    merge_observation_deck_libraries,
    scan_observation_deck,
    set_observation_deck_exclusions,
)


def _write_light(path: Path, date_obs: str, seed: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = fits.Header()
    header["IMAGETYP"] = "Light"
    header["EXPTIME"] = 300.0
    header["FILTER"] = "L"
    header["DATE-OBS"] = date_obs
    data = np.random.default_rng(seed).random((8, 8)).astype(np.float32)
    fits.PrimaryHDU(data=data, header=header).writeto(path)
    return path


class FakeDeckBridge:
    def __init__(self, library: ObservationDeckLibrary | None) -> None:
        self.library = library
        self.mode = AppMode.SKY_EXPLORER
        self.busy_polls = 0
        self.added: list[Path] = []
        self.exclusion_calls: list[tuple[tuple[Path, ...], bool]] = []

    def current_mode(self, cancel_event: threading.Event) -> AppMode:
        return self.mode

    def mode_switch_notes(self, mode: AppMode, cancel_event: threading.Event) -> tuple[str, ...]:
        return ("Anything loaded in Sky Explorer is cleared when you leave it.",)

    def deck_library(self, cancel_event: threading.Event) -> ObservationDeckLibrary | None:
        return self.library

    def deck_busy(self, cancel_event: threading.Event) -> bool:
        if self.busy_polls:
            self.busy_polls -= 1
            return True
        return False

    def open_deck(self, cancel_event: threading.Event) -> None:
        self.mode = AppMode.ASTRO_TOOLS
        self.busy_polls = 1

    def add_deck_folder(self, folder: Path, cancel_event: threading.Event) -> None:
        assert self.mode == AppMode.ASTRO_TOOLS and not self.busy_polls
        self.added.append(folder)
        self.library = merge_observation_deck_libraries(self.library, scan_observation_deck(folder))
        self.busy_polls = 2

    def set_deck_exclusions(self, paths: tuple[Path, ...], excluded: bool, cancel_event: threading.Event) -> int:
        self.exclusion_calls.append((paths, excluded))
        assert self.library is not None
        self.library, changed = set_observation_deck_exclusions(self.library, paths, excluded=excluded)
        return changed


class DeckToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = (Path(self._temp.name) / "Astro").resolve()
        self.a = _write_light(self.root / "M13" / "Lights" / "a.fits", "2024-06-01T01:00:00", 1)
        self.b = _write_light(self.root / "M13" / "Lights" / "b.fits", "2024-06-01T01:05:00", 2)
        self.copy = self.root / "M13" / "Lights" / "Backup" / "a.fits"
        self.copy.parent.mkdir(parents=True)
        shutil.copyfile(self.a, self.copy)
        self.bridge = FakeDeckBridge(scan_observation_deck(self.root))
        self.sleeps: list[float] = []
        self.registry = ToolRegistry(deck_tools(self.bridge, sleep=self.sleeps.append))
        self.context = ToolContext(writer=GuardedFileWriter())
        self.plans: list[object] = []
        self.progress: list[str] = []
        self.context.progress = self.progress.append

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _run(self, name: str, arguments: dict[str, object], approve: bool | None = True) -> tuple[str, dict[str, object]]:
        def approver(tool, plan):
            self.plans.append(plan)
            return ApprovalDecision(approved=bool(approve))

        result = execute_tool_call(self.registry, self.context, ToolCall("c1", name, arguments), approver if approve is not None else None)
        return result.status, json.loads(result.content)

    def _snapshot(self) -> dict[str, bytes]:
        return {str(path): path.read_bytes() for path in sorted(self.root.rglob("*")) if path.is_file()}

    def test_deck_summary_waits_for_loading_and_protects_the_masters(self) -> None:
        self.bridge.busy_polls = 3

        status, payload = self._run("get_observation_deck", {}, approve=None)

        self.assertEqual(status, "ok")
        self.assertEqual(payload["images"], 3)
        self.assertEqual(payload["master_folders"], [str(self.root)])
        self.assertEqual(payload["targets"][0]["name"], "M13")
        self.assertEqual(len(self.sleeps), 3)
        self.assertIn("Waiting for the Observation Deck library to load", self.progress)
        self.assertTrue(self.context.writer.is_protected(self.a))

    def test_duplicates_in_the_deck_suggest_the_shallowest_file_to_keep(self) -> None:
        before = self._snapshot()

        status, payload = self._run("find_duplicate_images", {}, approve=None)

        self.assertEqual(status, "ok")
        self.assertEqual((payload["exact_copy_groups"], payload["copies"]), (1, 1))
        group = payload["groups"][0]
        self.assertEqual(group["keep"], str(self.a))
        self.assertEqual(group["copies"], [str(self.copy)])
        self.assertEqual(self._snapshot(), before)

    def test_duplicates_in_a_folder_outside_the_deck(self) -> None:
        other = (Path(self._temp.name) / "Other").resolve()
        first = _write_light(other / "x.fits", "2024-07-01T01:00:00", 5)
        shutil.copyfile(first, other / "x (1).fits")

        status, payload = self._run("find_duplicate_images", {"folder": str(other)}, approve=None)

        self.assertEqual(status, "ok")
        self.assertEqual(payload["checked"], str(other))
        self.assertEqual(payload["copies"], 1)
        self.assertTrue(self.context.writer.is_protected(first))

    def test_exclusion_needs_approval_and_changes_only_the_deck(self) -> None:
        before = self._snapshot()

        status, _payload = self._run("exclude_from_observation_deck", {"paths": [str(self.copy)]}, approve=False)
        self.assertEqual(status, "declined")
        self.assertEqual(self.bridge.exclusion_calls, [])

        status, payload = self._run(
            "exclude_from_observation_deck",
            {"paths": [str(self.copy), "D:/Elsewhere/z.fits"], "reason": "It is a byte-for-byte copy of a.fits."},
        )

        self.assertEqual(status, "ok")
        self.assertEqual(payload["changed"], 1)
        self.assertEqual(payload["deck"]["excluded_images"], 1)
        self.assertEqual(payload["deck"]["counted_subframes"], 2)
        plan = self.plans[-1]
        self.assertEqual(plan.title, "Leave 1 image out of Observation Deck statistics")
        self.assertIn("byte-for-byte copy", plan.summary)
        self.assertIn(str(self.copy), plan.details)
        self.assertTrue(any("not in the deck" in line for line in plan.details))
        self.assertEqual(self._snapshot(), before)

    def test_duplicate_report_marks_copies_already_excluded_and_include_restores_them(self) -> None:
        self._run("exclude_from_observation_deck", {"paths": [str(self.copy)]})

        _status, payload = self._run("find_duplicate_images", {}, approve=None)
        self.assertEqual(payload["groups"][0]["copies_already_excluded"], [str(self.copy)])

        status, payload = self._run("exclude_from_observation_deck", {"paths": [str(self.copy)]})
        self.assertEqual(status, "error")
        self.assertIn("already excluded", payload["message"])

        status, payload = self._run("include_in_observation_deck", {"paths": [str(self.copy)]})
        self.assertEqual((status, payload["changed"]), ("ok", 1))
        self.assertEqual(self.bridge.exclusion_calls[-1][1], False)

    def test_unknown_paths_are_refused_before_asking(self) -> None:
        status, payload = self._run("exclude_from_observation_deck", {"paths": ["D:/Elsewhere/z.fits"]})

        self.assertEqual(status, "error")
        self.assertIn("None of those paths", payload["message"])
        self.assertEqual(self.plans, [])

    def test_adding_a_folder_asks_first_then_waits_for_the_scan(self) -> None:
        more = (Path(self._temp.name) / "More").resolve()
        _write_light(more / "M92" / "Lights" / "c.fits", "2024-07-01T01:00:00", 7)

        status, payload = self._run("add_folder_to_observation_deck", {"folder": str(more)})

        self.assertEqual(status, "ok")
        self.assertEqual(self.bridge.added, [more])
        self.assertEqual(payload["images_in_folder"], 1)
        self.assertEqual(payload["deck"]["images"], 4)
        plan = self.plans[0]
        self.assertEqual(plan.title, "Add More to the Observation Deck")
        self.assertEqual(plan.details[0], "CAst switches to Observation Deck.")
        self.assertTrue(any("not changed" in line for line in plan.details))
        self.assertEqual(len(self.sleeps), 3)

    def test_folders_already_in_the_deck_are_not_added_twice(self) -> None:
        status, payload = self._run("add_folder_to_observation_deck", {"folder": str(self.root / "M13")})

        self.assertEqual(status, "error")
        self.assertIn("already in the Observation Deck", payload["message"])
        self.assertEqual(self.bridge.added, [])

    def test_an_empty_deck_says_so(self) -> None:
        self.bridge.library = None

        _status, payload = self._run("get_observation_deck", {}, approve=None)
        self.assertIn("empty", payload["note"])
        status, payload = self._run("find_duplicate_images", {}, approve=None)
        self.assertEqual(status, "error")
        self.assertIn("Give a folder", payload["message"])


if __name__ == "__main__":
    unittest.main()
