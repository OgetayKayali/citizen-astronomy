from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest

from photometry_app.assistant.file_guard import FileGuardError, GuardedFileWriter, validate_file_name


class GuardedFileWriterTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name).resolve()
        self.source = self.root / "Lights" / "M13"
        self.source.mkdir(parents=True)
        self.source_frame = self.source / "frame_001.fits"
        self.source_frame.write_bytes(b"ORIGINAL")
        self.output = self.root / "Exports"
        self.writer = GuardedFileWriter(protected_roots=[self.root / "Lights"])

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_writing_requires_an_approved_folder(self) -> None:
        with self.assertRaisesRegex(FileGuardError, "not inside an output folder you approved"):
            self.writer.write_text(self.output, "notes.txt", "hello")
        self.assertFalse(self.output.exists())

    def test_approved_folder_accepts_new_files_and_approval_ends_with_the_action(self) -> None:
        with self.writer.approved(self.output) as folder:
            path = self.writer.write_text(folder, "summary.txt", "7 diagrams")
        self.assertEqual(path, self.output / "summary.txt")
        self.assertEqual(path.read_text(encoding="utf-8"), "7 diagrams")
        self.assertEqual(self.writer.created_files, (path,))
        self.assertEqual(self.writer.approved_folders, ())
        with self.assertRaises(FileGuardError):
            self.writer.write_text(self.output, "later.txt", "x")

    def test_missing_parents_of_the_approved_folder_are_created(self) -> None:
        nested = self.root / "New" / "Deep" / "Exports"
        with self.writer.approved(nested):
            path = self.writer.write_text(nested / "M13", "hr.csv", "a,b")
        self.assertTrue(path.is_file())
        self.assertEqual(path.parent, nested / "M13")

    def test_existing_files_are_never_overwritten(self) -> None:
        self.output.mkdir()
        existing = self.output / "hr_M13.png"
        existing.write_bytes(b"KEEP ME")
        with self.writer.approved(self.output):
            second = self.writer.write_bytes(self.output, "hr_M13.png", b"new")
            third = self.writer.write_bytes(self.output, "hr_M13.png", b"newer")
        self.assertEqual(existing.read_bytes(), b"KEEP ME")
        self.assertEqual(second.name, "hr_M13 (2).png")
        self.assertEqual(third.name, "hr_M13 (3).png")
        self.assertEqual(third.read_bytes(), b"newer")

    def test_names_without_extension_are_numbered_too(self) -> None:
        self.output.mkdir()
        (self.output / "README").write_text("keep", encoding="utf-8")
        with self.writer.approved(self.output):
            path = self.writer.write_text(self.output, "README", "new")
        self.assertEqual(path.name, "README (2)")

    def test_source_folders_cannot_be_output_folders(self) -> None:
        for folder in (self.root / "Lights", self.source, self.source / "sub"):
            with self.subTest(folder=folder):
                with self.assertRaisesRegex(FileGuardError, "CAst is reading from"):
                    with self.writer.approved(folder):
                        pass

    def test_source_folder_inside_the_output_folder_stays_protected(self) -> None:
        with self.writer.approved(self.root) as folder:
            allowed = self.writer.write_text(folder, "index.txt", "ok")
            with self.assertRaisesRegex(FileGuardError, "CAst is reading from"):
                self.writer.write_text(self.source, "frame_001.fits", "overwrite attempt")
            with self.assertRaisesRegex(FileGuardError, "CAst is reading from"):
                self.writer.ensure_folder(self.source / "aligned")
        self.assertTrue(allowed.is_file())
        self.assertEqual(self.source_frame.read_bytes(), b"ORIGINAL")
        self.assertEqual(sorted(path.name for path in self.source.iterdir()), ["frame_001.fits"])

    def test_file_names_cannot_escape_the_folder(self) -> None:
        bad_names = ["../escape.txt", "..\\escape.txt", "sub/file.txt", "C:\\file.txt", "a:stream", "", " ", "..", "con.txt", "NUL", "trailing."]
        for name in bad_names:
            with self.subTest(name=name):
                with self.assertRaises(FileGuardError):
                    validate_file_name(name)
        with self.writer.approved(self.output):
            with self.assertRaises(FileGuardError):
                self.writer.write_text(self.output, "../escape.txt", "x")
        self.assertFalse((self.root / "escape.txt").exists())

    def test_parent_traversal_in_the_folder_is_resolved_before_checking(self) -> None:
        with self.writer.approved(self.output):
            with self.assertRaisesRegex(FileGuardError, "not inside an output folder"):
                self.writer.write_text(self.output / ".." / "Lights", "sneaky.txt", "x")

    def test_drive_root_and_file_paths_are_rejected_as_output_folders(self) -> None:
        with self.assertRaisesRegex(FileGuardError, "Choose a specific folder"):
            self.writer.check_output_folder(Path(self.root.anchor))
        with self.assertRaisesRegex(FileGuardError, "not a folder"):
            self.writer.check_output_folder(self.source_frame)

    def test_failed_write_removes_only_its_own_partial_file(self) -> None:
        self.output.mkdir()
        neighbour = self.output / "other.txt"
        neighbour.write_text("untouched", encoding="utf-8")
        with self.writer.approved(self.output):
            with self.assertRaises(RuntimeError):
                with self.writer.create_file(self.output, "export.gif") as (path, handle):
                    handle.write(b"partial")
                    partial_path = path
                    raise RuntimeError("encoder crashed")
        self.assertFalse(partial_path.exists())
        self.assertEqual(neighbour.read_text(encoding="utf-8"), "untouched")
        self.assertEqual(self.writer.created_files, ())

    @unittest.skipUnless(os.name == "nt", "Windows paths are case-insensitive")
    def test_protection_ignores_letter_case_on_windows(self) -> None:
        upper = Path(str(self.source).upper())
        self.assertTrue(self.writer.is_protected(upper))
        with self.assertRaises(FileGuardError):
            self.writer.check_output_folder(upper)

    def test_links_into_source_folders_are_followed_and_refused(self) -> None:
        self.output.mkdir()
        link = self.output / "link_to_source"
        if not _make_directory_link(link, self.source):
            self.skipTest("Could not create a directory link on this system.")
        with self.writer.approved(self.output):
            with self.assertRaisesRegex(FileGuardError, "Refused to write"):
                self.writer.write_text(link, "frame_001.fits", "overwrite attempt")
            with self.assertRaisesRegex(FileGuardError, "Refused to write"):
                self.writer.write_text(link, "new_file.txt", "x")
        self.assertEqual(self.source_frame.read_bytes(), b"ORIGINAL")
        self.assertEqual(sorted(path.name for path in self.source.iterdir()), ["frame_001.fits"])


def _make_directory_link(link: Path, target: Path) -> bool:
    if sys.platform == "win32":
        try:
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
            return True
        except (ImportError, OSError):
            return False
    try:
        link.symlink_to(target, target_is_directory=True)
        return True
    except OSError:
        return False


if __name__ == "__main__":
    unittest.main()
