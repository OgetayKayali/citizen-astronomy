from __future__ import annotations

from pathlib import Path

import numpy as np
from astropy.io import fits
from PIL import Image
from xisf import XISF

ROOT = Path(__file__).resolve().parent
FIXTURES_DIR = ROOT / "fixtures"


def _write_png(path: Path) -> None:
    image = Image.new("RGB", (4, 4), color=(32, 64, 128))
    image.save(path, format="PNG")


def _write_webp(path: Path) -> None:
    image = Image.new("RGB", (4, 4), color=(16, 32, 48))
    image.save(path, format="WEBP")


def _write_fits(path: Path) -> None:
    data = np.ones((8, 8), dtype=np.float32)
    header = fits.Header()
    header["OBJECT"] = "SMOKE"
    fits.PrimaryHDU(data=data, header=header).writeto(path, overwrite=True)


def _write_xisf(path: Path) -> None:
    data = np.ones((8, 8, 1), dtype=np.float32)
    XISF.write(
        str(path),
        data,
        image_metadata={
            "FITSKeywords": {
                "OBJECT": [{"value": "SMOKE", "comment": "Packaged smoke fixture"}],
            }
        },
    )


def main() -> int:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    _write_png(FIXTURES_DIR / "smoke_tiny.png")
    _write_webp(FIXTURES_DIR / "smoke_tiny.webp")
    _write_fits(FIXTURES_DIR / "smoke_tiny.fits")
    _write_xisf(FIXTURES_DIR / "smoke_tiny.xisf")
    print(f"Wrote smoke fixtures to {FIXTURES_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
