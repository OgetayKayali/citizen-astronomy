from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


PAPER_DIR = Path(__file__).resolve().parent
MAIN_TEX = PAPER_DIR / "citizen_photometry_paper.tex"
BUILD_DIR = PAPER_DIR / "build"
COMMON_TEX_BIN_DIRS = [
    Path.home() / "AppData" / "Local" / "Programs" / "MiKTeX" / "miktex" / "bin" / "x64",
    Path.home() / "AppData" / "Local" / "Programs" / "MiKTeX" / "miktex" / "bin",
    Path("C:/Program Files/MiKTeX/miktex/bin/x64"),
    Path("C:/Program Files/MiKTeX/miktex/bin"),
]


def _find_tool(name: str) -> str | None:
    resolved = shutil.which(name)
    if resolved is not None:
        return resolved

    executable_name = f"{name}.exe" if sys.platform.startswith("win") else name
    for bin_dir in COMMON_TEX_BIN_DIRS:
        candidate = bin_dir / executable_name
        if candidate.exists():
            return str(candidate)
    return None


def _run(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(command, cwd=cwd, env=env)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def _document_class_name(main_tex: Path) -> str:
    content = main_tex.read_text(encoding="utf-8")
    match = re.search(r"\\documentclass(?:\[[^\]]*\])?\{([^}]+)\}", content)
    if match is None:
        raise RuntimeError(f"Could not determine the LaTeX document class from {main_tex.name}.")
    return match.group(1).strip()


def _ensure_aastex_available(main_tex: Path) -> None:
    kpsewhich = _find_tool("kpsewhich")
    if kpsewhich is None:
        raise RuntimeError(
            "A LaTeX toolchain was found, but kpsewhich is missing, so the script cannot verify that "
            "the requested AAS class is installed. Install a full TeX distribution that includes the "
            "required AAS class."
        )

    class_name = _document_class_name(main_tex)
    class_file = f"{class_name}.cls"

    result = subprocess.run(
        [kpsewhich, class_file],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(
            f"The TeX toolchain is installed, but {class_file} was not found. Install the matching AAS TeX package "
            "before building this manuscript."
        )


def _open_pdf(pdf_path: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(str(pdf_path))
        return

    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, str(pdf_path)])


def _build_with_latexmk(main_tex: Path, output_dir: Path) -> Path:
    latexmk = _find_tool("latexmk")
    if latexmk is None:
        raise RuntimeError("latexmk was requested but is not available.")

    _run(
        [
            latexmk,
            "-pdf",
            "-interaction=nonstopmode",
            f"-outdir={output_dir}",
            str(main_tex.name),
        ],
        cwd=PAPER_DIR,
    )
    return output_dir / f"{main_tex.stem}.pdf"


def _build_with_pdflatex(main_tex: Path, output_dir: Path) -> Path:
    pdflatex = _find_tool("pdflatex")
    bibtex = _find_tool("bibtex")
    if not pdflatex or not bibtex:
        raise RuntimeError("pdflatex and bibtex are both required for the fallback LaTeX build.")

    common_args = [
        pdflatex,
        "-interaction=nonstopmode",
        f"-output-directory={output_dir}",
        str(main_tex.name),
    ]
    bibtex_env = os.environ.copy()
    path_separator = ";" if sys.platform.startswith("win") else ":"
    existing_bibinputs = bibtex_env.get("BIBINPUTS", "")
    bibtex_env["BIBINPUTS"] = f"{PAPER_DIR}{path_separator}{existing_bibinputs}"
    _run(common_args, cwd=PAPER_DIR)
    _run([bibtex, main_tex.stem], cwd=output_dir, env=bibtex_env)
    _run(common_args, cwd=PAPER_DIR)
    _run(common_args, cwd=PAPER_DIR)
    return output_dir / f"{main_tex.stem}.pdf"


def build_pdf(main_tex: Path, clean: bool) -> Path:
    if clean and BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    if not main_tex.exists():
        raise RuntimeError(f"Manuscript source not found: {main_tex}")

    if _find_tool("pdflatex"):
        _ensure_aastex_available(main_tex)
        return _build_with_pdflatex(main_tex, BUILD_DIR)
    if _find_tool("latexmk"):
        _ensure_aastex_available(main_tex)
        return _build_with_latexmk(main_tex, BUILD_DIR)

    raise RuntimeError(
        "No LaTeX build tool was found in PATH. Install latexmk or a LaTeX distribution "
        "that provides pdflatex and bibtex, then rerun this script."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the Citizen Photometry paper PDF.")
    parser.add_argument(
        "--source",
        default=MAIN_TEX.name,
        help="Manuscript .tex filename inside docs/paper to compile.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove the build directory before compiling.",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the generated PDF after a successful build.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_path = (PAPER_DIR / str(args.source)).resolve()
    try:
        source_path.relative_to(PAPER_DIR.resolve())
    except ValueError:
        print("The manuscript source must be inside docs/paper.", file=sys.stderr)
        return 1
    try:
        pdf_path = build_pdf(source_path, clean=args.clean)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.open:
        _open_pdf(pdf_path)

    print(pdf_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())