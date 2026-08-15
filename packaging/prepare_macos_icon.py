#!/usr/bin/env python3
"""Create packaging/macos/CitizenAstronomy.icns from assets/citizen_astronomy.ico when possible."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICO_PATH = ROOT / "assets" / "citizen_astronomy.ico"
PNG_FALLBACK = ROOT / "assets" / "citizen_astronomy.png"
OUT_DIR = ROOT / "packaging" / "macos"
ICNS_PATH = OUT_DIR / "CitizenAstronomy.icns"
PNG_ASSET_PATH = OUT_DIR / "CitizenAstronomy.png"


def _export_png(source: Path, destination: Path) -> None:
    from PIL import Image

    with Image.open(source) as image:
        image = image.convert("RGBA")
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination, format="PNG")


def _build_icns_with_iconutil(png_path: Path, icns_path: Path) -> bool:
    if sys.platform != "darwin":
        return False
    if shutil.which("sips") is None or shutil.which("iconutil") is None:
        return False

    sizes = (16, 32, 64, 128, 256, 512, 1024)
    with tempfile.TemporaryDirectory(prefix="cast-iconset-") as temp_dir:
        iconset = Path(temp_dir) / "CitizenAstronomy.iconset"
        iconset.mkdir(parents=True, exist_ok=True)
        for size in sizes:
            ordinary = iconset / f"icon_{size}x{size}.png"
            retina = iconset / f"icon_{size}x{size}@2x.png"
            subprocess.run(
                ["sips", "-z", str(size), str(size), str(png_path), "--out", str(ordinary)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            retina_size = size * 2
            if retina_size <= 1024:
                subprocess.run(
                    ["sips", "-z", str(retina_size), str(retina_size), str(png_path), "--out", str(retina)],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                shutil.copy2(ordinary, retina)
        icns_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns_path)], check=True)
    return icns_path.is_file()


def main() -> int:
    source = ICO_PATH if ICO_PATH.is_file() else PNG_FALLBACK
    if not source.is_file():
        print(f"No icon source found at {ICO_PATH} or {PNG_FALLBACK}; skipping.", file=sys.stderr)
        return 0

    try:
        _export_png(source, PNG_ASSET_PATH)
        # Also keep a PNG under assets for runtime icon fallbacks on macOS.
        runtime_png = ROOT / "assets" / "citizen_astronomy.png"
        if not runtime_png.is_file():
            shutil.copy2(PNG_ASSET_PATH, runtime_png)
    except Exception as exc:
        print(f"Could not export PNG icon: {exc}", file=sys.stderr)
        return 0

    try:
        if _build_icns_with_iconutil(PNG_ASSET_PATH, ICNS_PATH):
            print(f"Wrote {ICNS_PATH}")
        else:
            print("icns generation skipped (requires macOS sips/iconutil). PNG prepared.")
    except Exception as exc:
        print(f"icns generation failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
