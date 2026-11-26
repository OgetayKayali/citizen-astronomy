"""Compare the published 3I/ATLAS benchmark with Caltech/IPAC APT in batch mode.

Requires the published APT.jar and Java 13+. The original FITS are read-only.
"""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

from astropy.io import fits
from astropy.wcs import WCS
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from photometry_app.core.comet_photometry import measure_comet_aperture
from verify_comet_3i_atlas import circle_overlap

HALF = 48
SCALE = 1e6  # Reversible display/output scaling, independent of magnitude zero point.


def build_stamps(dataset, fixture, refs):
    first = fixture["frames"][0]
    wcs = WCS(fits.getheader(dataset / first["file"])).celestial

    def positions(rows):
        return np.column_stack(wcs.world_to_pixel_values(
            [s["ra_deg"] for s in rows], [s["dec_deg"] for s in rows]
        ))

    motion = positions(fixture["frames"])
    centers = np.vstack((motion[0], positions(refs)))
    offsets = np.arange(-HALF, HALF + 1)
    xx, yy = np.meshgrid(offsets, offsets)
    # Two output grids. The first preserves the original pixel phase; the
    # second puts the same celestial position on a whole pixel for APT.
    stamps = np.zeros((2, len(centers), *xx.shape))
    for i, f in enumerate(fixture["frames"]):
        path = dataset / f["file"]
        with path.open("rb") as src:
            if hashlib.file_digest(src, "sha256").hexdigest() != f["sha256"]:
                raise ValueError(f"Input hash differs: {path}")
        with fits.open(path, memmap=True) as hdus:
            raw = hdus[0].data
            exposure = float(hdus[0].header["EXPTIME"])
            for grid, origins in enumerate((np.rint(centers), centers)):
                for j, (x, y) in enumerate(origins):
                    if j == 0:
                        x, y = np.array([x, y]) + (motion[i] - motion[0])
                    ix, iy = math.floor(x), math.floor(y)
                    fx, fy = x - ix, y - iy
                    cols, rows = xx + ix, yy + iy
                    value = (
                        (1-fx)*(1-fy)*raw[rows, cols] + fx*(1-fy)*raw[rows, cols+1]
                        + (1-fx)*fy*raw[rows+1, cols] + fx*fy*raw[rows+1, cols+1]
                    )
                    stamps[grid, j] += value / exposure
    stamps /= len(fixture["frames"])
    if not np.isfinite(stamps).all():
        raise ValueError("Incomplete stamp")
    return stamps, centers - np.rint(centers)


def common_sky(image, inner, outer):
    offsets = np.arange(-HALF, HALF + 1)
    xx, yy = np.meshgrid(offsets, offsets)
    overlap = np.vectorize(circle_overlap, otypes=[float])
    eligible = overlap(xx, yy, outer) - overlap(xx, yy, inner) > 0.99
    kept = eligible.copy()
    for _ in range(5):
        sky = image[kept]
        new = kept & (abs(image - np.median(sky)) <= 3*np.std(sky))
        if np.array_equal(kept, new):
            break
        kept = new
    # Mask just the annulus and its edges. APT still computes its own median
    # and source integral. No source-aperture samples are replaced.
    distance = np.hypot(xx, yy)
    reject = (distance >= inner-1) & (distance <= outer+1) & ~kept
    return reject, float(np.median(image[kept])), int(kept.sum())


def apt_run(args, dest, image, r, inner, outer, sky, keep_count):
    dest.mkdir(parents=True, exist_ok=True)
    name = dest/"stamp.fits"
    fits.PrimaryHDU(image*SCALE).writeto(name, overwrite=True)
    source = dest/"positions.txt"
    source.write_text(f"{HALF+1} {HALF+1}\n", encoding="ascii")
    pref = dest/"APT.pref"
    pref.write_text("\n".join([
        f"ellipseMajorRadius={r}", f"ellipseMinorRadius={r}", f"radiusSkyAnnulusInner={inner}",
        f"radiusSkyAnnulusOuter={outer}", "sourceListCoordsFlag=0", "useCentroidFlag=0",
        "noSkyFlag=0", "interpolateFlag=0", "scientificNotationFlag=1",
        "lowerSrcDataLimit=-1e30", "upperSrcDataLimit=1e30",
        "lowerSkyDataLimit=-1e30", "upperSkyDataLimit=1e30",
    ])+"\n", encoding="ascii")
    output = dest/"APT.tbl"
    if output.exists():
        output.unlink()  # APT appends. This directory holds only benchmark output.
    proc = subprocess.run([args.java, "-Duser.language=en", "-Duser.region=US",
        f"-Duser.home={args.work}", "-Xmx512m", "-jar", str(args.jar), "-i", str(name), "-s", str(source),
        "-p", str(pref), "-o", str(output), "-b", str(dest/"unused_model_f.fits"), "-v", "1"],
        cwd=dest, env={**os.environ, "APT_HOME": str(args.jar.parent), "APT_ARCH": "WIN" if os.name=="nt" else "LINUX"},
        capture_output=True, text=True, errors="replace", timeout=60)
    (dest/"apt.log").write_text(proc.stdout+proc.stderr, encoding="utf-8")
    if proc.returncode or not output.exists():
        raise RuntimeError(f"APT did not create a measurement. See {dest/'apt.log'}")
    return parse_apt(output, r, inner, outer, sky, keep_count)


def parse_apt(path, r, inner, outer, sky, keep_count):
    lines = [line.split() for line in path.read_text().splitlines() if line.strip()]
    header = next(line for line in lines if line[0] == "Number")
    result, = [dict(zip(header, line)) for line in lines if line[0].isdigit()]
    expected = dict(ApertureX=HALF+1, ApertureY=HALF+1, CentroidFlag=0,
                    ApertureMajorRadius=r, ApertureMinorRadius=r, SkyRadiusInner=inner,
                    SkyRadiusOuter=outer, SourceModel=0, ApertureCorr=1, ApertureNumRejected=0, Flags=0)
    for key, value in expected.items():
        if float(result[key]) != value:
            raise ValueError(f"{path}: expected {key}={value}, got {result[key]}")
    if result["SkyModel"] != "B":
        raise ValueError(f"{path}: expected sky model B, got {result['SkyModel']}")
    bkg = float(result["SkyMedian"])/SCALE
    if keep_count is not None:
        if int(result["SkyNumPixels"]) != keep_count:
            raise ValueError(f"{path}: expected {keep_count} sky samples, got {result['SkyNumPixels']}")
        np.testing.assert_allclose(bkg, sky, rtol=1e-5, atol=0)
    return {
        "flux": float(result["SourceIntensity"]) / SCALE,
        "sky": bkg,
        "sky_pixels": int(result["SkyNumPixels"]),
        "area_px2": float(result["ApertureNumPixels"]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--jar", type=Path, required=True)
    parser.add_argument("--java", default="java")
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    args.work, args.jar = args.work.resolve(), args.jar.resolve()
    args.work.mkdir(parents=True, exist_ok=True)
    fixture = json.loads((ROOT/"tests/data/comet_3i_atlas.json").read_text())
    with (ROOT / "tests/data/comet_3i_atlas_apass_v.csv").open() as f:
        refs = list(csv.DictReader(f))
    for ref in refs:
        for key in ("ra_deg", "dec_deg", "magnitude", "catalog_error"):
            ref[key] = float(ref[key])
    stamps, phases = build_stamps(args.dataset, fixture, refs)
    rows = []
    for j, label in enumerate(["3I/ATLAS", *[s["source_id"] for s in refs]]):
        radii, inner, outer = ((6,12,18),24,36) if j == 0 else ((8,),12,20)
        raw, centered = stamps[:,j]
        reject, sky, n = common_sky(centered, inner, outer)
        for r in radii:
            options = dict(radius=r, sky_inner=inner, sky_outer=outer)
            orig = measure_comet_aperture(raw, x=HALF+phases[j,0], y=HALF+phases[j,1], **options)
            same = measure_comet_aperture(centered, x=HALF, y=HALF, **options)
            if orig.flags or same.flags:
                raise AssertionError((label, orig.flags, same.flags))
            if same.sky_pixels != n:
                raise AssertionError(f"{label}: CAst retained {same.sky_pixels} sky samples, expected {n}")
            np.testing.assert_allclose(same.background, sky, rtol=1e-12, atol=0)
            if j == 0:
                np.testing.assert_allclose(orig.flux,fixture["reference_flux"][str(r)],rtol=1e-10)
            row = dict(
                source=label, radius_px=r, sky_inner_px=inner, sky_outer_px=outer,
                cast_original=orig.flux, cast_common_grid=same.flux, cast_sky=same.background,
                cast_sky_pixels=same.sky_pixels, mag_error=orig.magnitude_error,
            )
            for mode, img in (("native",centered), ("matched",np.where(reject,np.nan,centered))):
                dest = args.work/f"source{j}_r{r}_{mode}"
                measurement = apt_run(args, dest, img, r, inner, outer, sky, n if mode == "matched" else None)
                row[f"apt_{mode}"] = measurement["flux"]
                for field in ("sky", "sky_pixels", "area_px2"):
                    row[f"apt_{mode}_{field}"] = measurement[field]
            rows.append(row)
    summarize(args.work, rows, refs, fixture)


def summarize(work, rows, refs, fixture):
    columns = ("cast_original", "cast_common_grid", "apt_native", "apt_matched")
    stars = {s["source_id"]: s for s in refs}
    star_rows = [row for row in rows if row["source"] in stars]
    variances = np.array([max(0.0001, stars[row["source"]]["catalog_error"]**2 + row["mag_error"]**2)
                          for row in star_rows])
    for key in columns:
        zero_points = np.array([stars[row["source"]]["magnitude"] + 2.5*math.log10(row[key])
                                for row in star_rows])
        deviations = abs(zero_points - np.median(zero_points))
        if not np.all(deviations <= np.maximum(0.15, 3*np.sqrt(variances + (1.4826*np.median(deviations))**2))):
            raise ValueError(f"{key}: comparison-star selection differs from the pinned five-star ensemble")
        zero_point = np.average(zero_points, weights=1/variances)
        for row in rows:
            row[f"{key}_zp"] = zero_point
            row[f"{key}_mag"] = zero_point - 2.5*math.log10(row[key])
    for row in rows:
        row["apt_minus_original_mag"] = row["apt_matched_mag"] - row["cast_original_mag"]
        row["apt_minus_common_grid_mag"] = row["apt_matched_mag"] - row["cast_common_grid_mag"]
        print(row["source"], row["radius_px"], *(f"{row[f'{key}_mag']:.8f}" for key in columns))
    primary, = [row for row in rows if row["source"] == "3I/ATLAS" and row["radius_px"] == 12]
    np.testing.assert_allclose(primary["cast_original_mag"], fixture["reference_l_v_mag"], atol=1e-10, rtol=0)
    with (work/"comparison.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
