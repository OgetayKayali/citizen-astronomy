from __future__ import annotations

import argparse
from pathlib import Path

import imageio.v3 as iio
import numpy as np


def process_milky_way(
    image: np.ndarray,
    *,
    exposure: float = 1.35,
    contrast: float = 1.08,
    saturation: float = 0.72,
) -> np.ndarray:
    image = np.asarray(image)
    if image.ndim == 4 and image.shape[0] == 1:
        image = image[0]
    if image.ndim != 3 or image.shape[2] < 3:
        raise ValueError(f"Expected an RGB/RGBA image, got shape {image.shape!r}")

    source_dtype = image.dtype
    image = image[..., :3].astype(np.float32)
    if np.issubdtype(source_dtype, np.integer):
        max_value = float(np.iinfo(source_dtype).max)
        image = image / max(1.0, max_value)
    image = np.nan_to_num(image, nan=0.0, posinf=1.0, neginf=0.0)

    image *= max(0.0, float(exposure))
    image = image / (1.0 + image)
    image = np.clip((image - 0.5) * max(0.0, float(contrast)) + 0.5, 0.0, 1.0)

    gray = np.mean(image, axis=2, keepdims=True)
    image = gray + max(0.0, float(saturation)) * (image - gray)
    image = np.power(np.clip(image, 0.0, 1.0), 1.0 / 2.2)

    return np.clip(image * 255.0, 0.0, 255.0).astype(np.uint8)


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert the NASA SVS Milky Way EXR master into an app-friendly PNG.")
    parser.add_argument("input", nargs="?", default="textures/milkyway_2020_4k.exr", help="Source EXR path")
    parser.add_argument("output", nargs="?", default="textures/milkyway_2020_4k_preview.png", help="Derived PNG path")
    parser.add_argument("--exposure", type=float, default=1.35)
    parser.add_argument("--contrast", type=float, default=1.08)
    parser.add_argument("--saturation", type=float, default=0.72)
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    image = iio.imread(input_path)
    processed = process_milky_way(
        image,
        exposure=args.exposure,
        contrast=args.contrast,
        saturation=args.saturation,
    )
    iio.imwrite(output_path, processed)
    print(f"Wrote {output_path} from {input_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())