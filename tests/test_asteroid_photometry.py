from dataclasses import replace
import math
import numpy as np
import pytest
from astropy.io import fits
from photometry_app.core.comet_photometry import ComparisonStar, build_comet_stack_pair
from photometry_app.core.asteroid_photometry import measure_asteroid_sequence
from test_comet_photometry import make_sequence


def sequence(tmp_path):
    frames, wcs = make_sequence(tmp_path)
    yy, xx = np.indices((160, 160))
    stars = [ComparisonStar(str(i), str(i), x, y, 25 - 2.5 * math.log10(60 * 8 * math.pi), .02)
             for i, (x, y) in enumerate([(35, 40), (125, 35), (35, 125), (125, 125)])]
    for i, frame in enumerate(frames):
        rate = 40 + 100 * [1, .8, 1.4][i] * np.exp(-((xx-80-2*i)**2 + (yy-80)**2)/18)
        for star in stars:
            rate += 60 * np.exp(-((xx-star.x)**2 + (yy-star.y)**2)/8)
        fits.PrimaryHDU(rate * frame.exposure * [1, .6, 1.2][i], wcs.to_header()).writeto(frame.path, overwrite=True)
    pair = build_comet_stack_pair(frames, reference_path=frames[0].path)
    return pair, stars


def measure(pair, stars, band):
    return measure_asteroid_sequence(pair, stars, radius=12, sky_inner=20, sky_outer=30,
        star_radius=8, center=(80, 80), band=band, target="Test asteroid")


@pytest.mark.parametrize("band", [None, "V"])
def test_exposure_light_curve_preserves_rotation_and_removes_transparency(tmp_path, band):
    pair, stars = sequence(tmp_path)
    rows = measure(pair, stars, band)
    assert len(rows) == 3
    mags = [r["magnitude"] for r in rows]
    assert all(m is not None for m in mags)
    assert mags[1]-mags[0] == pytest.approx(-2.5 * math.log10(.8), abs=.01)
    assert mags[2]-mags[0] == pytest.approx(-2.5 * math.log10(1.4), abs=.01)
    assert [r["center_x"] for r in rows] == pytest.approx([80, 82, 84], abs=.001)
    assert all(r["comparison_count"] >= 3 for r in rows)
    assert all(("APASS V" if band else "Differential") in r["magnitude_system"] for r in rows)


def test_bad_frames_remain_flagged_and_cancellation_stops_work(tmp_path):
    pair, stars = sequence(tmp_path)
    with open(pair.provenance["frames"][1]["path"], "ab") as f:
        f.write(b"changed")
    rows = measure(pair, stars, None)
    assert rows[1]["magnitude"] is None
    assert "changed" in rows[1]["quality"]
    assert "Differential" in rows[0]["magnitude_system"]
    with pytest.raises(InterruptedError):
        measure_asteroid_sequence(pair, stars, radius=12, sky_inner=20, sky_outer=30,
            star_radius=8, center=(80,80), band=None, target="Test", cancel=lambda: True)


def test_no_references_is_explicitly_instrumental(tmp_path):
    pair, _ = sequence(tmp_path)
    rows = measure(pair, [], None)
    assert all(r["magnitude_system"] == "Instrumental" and r["magnitude"] is not None for r in rows)
    assert all("Instrumental only" in r["quality"] for r in rows)


def test_csv_can_export_when_the_first_exposure_failed(tmp_path):
    from pathlib import Path
    from photometry_app.core.comet_photometry import export_comet_measurements
    import csv
    pair, stars = sequence(tmp_path)
    Path(pair.provenance["frames"][0]["path"]).unlink()
    rows = measure(pair, stars, None)
    output = tmp_path / "asteroid.csv"
    export_comet_measurements(output, rows)
    with output.open(encoding="utf-8-sig", newline="") as f:
        exported = list(csv.DictReader(f))
    assert len(exported) == 3
    assert exported[0]["magnitude"] == ""
    assert exported[1]["magnitude"]


def test_saturated_target_is_not_reported_as_a_valid_magnitude(tmp_path):
    pair, stars = sequence(tmp_path)
    source = pair.provenance["frames"][0]
    with fits.open(source["path"], mode="update") as hdus:
        hdus[0].header["SATURATE"] = 130 * source["exposure"]
    from pathlib import Path
    stat = Path(source["path"]).stat()
    source["size"], source["mtime_ns"] = stat.st_size, stat.st_mtime_ns
    rows = measure(pair, stars, "V")
    assert rows[0]["magnitude"] is None
    assert "saturated" in rows[0]["quality"]
    assert all(r["magnitude"] is not None for r in rows[1:])
