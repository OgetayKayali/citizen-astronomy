"""Source audit: assistant code may only write files through photometry_app/assistant/file_guard.py."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD_MODULE = REPO_ROOT / "photometry_app" / "assistant" / "file_guard.py"
# Core modules the assistant runs over the user's whole archive; they must stay read-only too.
READ_ONLY_CORE_MODULES = (REPO_ROOT / "photometry_app" / "core" / "duplicate_images.py",)

_FORBIDDEN_MODULES = {"shutil", "tempfile", "send2trash"}
_FORBIDDEN_OS_CALLS = {
    "remove",
    "unlink",
    "rmdir",
    "removedirs",
    "rename",
    "renames",
    "replace",
    "mkdir",
    "makedirs",
    "truncate",
    "open",
    "link",
    "symlink",
    "chmod",
    "utime",
}
_FORBIDDEN_METHODS = {
    "unlink",
    "rmdir",
    "rename",
    "touch",
    "mkdir",
    "write_text",
    "write_bytes",
    "symlink_to",
    "hardlink_to",
    "chmod",
    "truncate",
    "savefig",
    "to_csv",
    "writeto",
}
_WRITE_MODE_CHARACTERS = set("wax+")


def audited_files() -> list[Path]:
    files = sorted((REPO_ROOT / "photometry_app" / "assistant").rglob("*.py"))
    files += sorted((REPO_ROOT / "photometry_app" / "ui").glob("assistant_*.py"))
    files += READ_ONLY_CORE_MODULES
    return [path for path in files if path != GUARD_MODULE]


def find_filesystem_writes(source: str, filename: str) -> list[str]:
    tree = ast.parse(source, filename=filename)
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _FORBIDDEN_MODULES:
                    findings.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").split(".")[0]
            if module in _FORBIDDEN_MODULES:
                findings.append(f"line {node.lineno}: from {node.module} import ...")
            if module == "os":
                for alias in node.names:
                    if alias.name in _FORBIDDEN_OS_CALLS:
                        findings.append(f"line {node.lineno}: from os import {alias.name}")
        elif isinstance(node, ast.Call):
            function = node.func
            if isinstance(function, ast.Attribute):
                owner = function.value
                if isinstance(owner, ast.Name) and owner.id == "os" and function.attr in _FORBIDDEN_OS_CALLS:
                    findings.append(f"line {node.lineno}: os.{function.attr}()")
                elif function.attr in _FORBIDDEN_METHODS:
                    findings.append(f"line {node.lineno}: .{function.attr}()")
                elif function.attr == "open" and _opens_for_writing(node, mode_position=0):
                    findings.append(f"line {node.lineno}: .open() for writing")
            elif isinstance(function, ast.Name) and function.id == "open" and _opens_for_writing(node, mode_position=1):
                findings.append(f"line {node.lineno}: open() for writing")
    return findings


def _opens_for_writing(node: ast.Call, *, mode_position: int) -> bool:
    mode: ast.expr | None = None
    if len(node.args) > mode_position:
        mode = node.args[mode_position]
    for keyword in node.keywords:
        if keyword.arg == "mode":
            mode = keyword.value
    if mode is None:
        return False
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return bool(_WRITE_MODE_CHARACTERS & set(mode.value))
    return True


class AssistantSafetyAuditTest(unittest.TestCase):
    def test_assistant_code_writes_files_only_through_the_guard(self) -> None:
        files = audited_files()
        self.assertTrue(any(path.name == "tools.py" for path in files))
        problems: list[str] = []
        for path in files:
            for finding in find_filesystem_writes(path.read_text(encoding="utf-8"), str(path)):
                problems.append(f"{path.relative_to(REPO_ROOT)} {finding}")
        self.assertEqual(problems, [], "Route file writes through GuardedFileWriter:\n" + "\n".join(problems))

    def test_audit_catches_each_kind_of_write(self) -> None:
        samples = {
            "import shutil": 1,
            "import os\nos.remove(p)": 1,
            "from os import rename": 1,
            "p.unlink()": 1,
            "p.write_text('x')": 1,
            "open(p, 'w')": 1,
            "open(p, mode='ab')": 1,
            "p.open('r+')": 1,
            "open(p, mode)": 1,
            "figure.savefig(p)": 1,
            "open(p)": 0,
            "open(p, 'rb')": 0,
            "text.replace('a', 'b')": 0,
            "copy.deepcopy(x)": 0,
        }
        for source, expected in samples.items():
            with self.subTest(source=source):
                self.assertEqual(len(find_filesystem_writes(source, "<sample>")), expected)


if __name__ == "__main__":
    unittest.main()
