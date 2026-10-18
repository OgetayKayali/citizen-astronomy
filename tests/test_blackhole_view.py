from __future__ import annotations

import math
import re
import sys
import unittest
from pathlib import Path

import numpy as np

from photometry_app.core.blackhole_stars import MARGIN, build_star_textures, cube_cells, equatorial_vectors
from photometry_app.core.milky_way_assets import milky_way_u_from_ra_deg, milky_way_v_from_dec_deg
from photometry_app.ui.blackhole_view import (
    camera_horizon,
    camera_minimum,
    cast_sky_uv,
    kerr_isco,
    library_path,
    load_library,
    resolve_cast_milky_way_path,
)

ROOT = Path(__file__).resolve().parents[1]
SHADERS = ROOT / "native" / "blackhole" / "vendor" / "apps" / "blackhole" / "shaders.hpp"


def lab_panorama_galactic(direction: tuple[float, float, float], sky_rotation_deg: float) -> tuple[float, float]:
    """Galactic (l, b) the PhysicsEngine lab showed for a ray, from its panorama mapping."""

    x, y, z = direction
    angle = math.radians(sky_rotation_deg)
    c, s = math.cos(angle), math.sin(angle)
    x, z = c * x + s * z, -s * x + c * z
    y, z = 0.906307 * y + 0.422618 * z, -0.422618 * y + 0.906307 * z
    u = math.atan2(z, x) / math.tau + 0.5
    v = 0.5 - math.asin(max(-1.0, min(1.0, y))) / math.pi
    return (0.5 - u) * 360.0, (0.5 - v) * 180.0


class BlackHoleSkyMappingTest(unittest.TestCase):
    def test_sky_lands_where_the_lab_panorama_put_it(self) -> None:
        from astropy import units as u
        from astropy.coordinates import SkyCoord

        directions = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.3, -0.5, 0.81), (-0.7, 0.2, -0.68), (0.05, 0.9, -0.43)]
        for raw in directions:
            norm = math.sqrt(sum(c * c for c in raw))
            direction = tuple(c / norm for c in raw)
            for rotation in (-65.0, 0.0, 120.0):
                longitude, latitude = lab_panorama_galactic(direction, rotation)
                icrs = SkyCoord(l=longitude * u.deg, b=latitude * u.deg, frame="galactic").icrs
                expected_u = milky_way_u_from_ra_deg(icrs.ra.deg)
                expected_v = milky_way_v_from_dec_deg(icrs.dec.deg)
                got_u, got_v = cast_sky_uv(direction, rotation)
                self.assertAlmostEqual(min(abs(got_u - expected_u), 1 - abs(got_u - expected_u)), 0.0, places=4)
                self.assertAlmostEqual(got_v, expected_v, places=4)

    def test_panorama_center_is_the_galactic_center(self) -> None:
        # This ray reaches the lab panorama's center pixel at the default -65 degree rotation.
        angle = math.radians(-65.0)
        direction = (math.cos(angle), 0.0, math.sin(angle))
        self.assertEqual(tuple(round(c, 6) for c in lab_panorama_galactic(direction, -65.0)), (0.0, 0.0))
        got_u, got_v = cast_sky_uv(direction, -65.0)
        self.assertAlmostEqual(got_u, milky_way_u_from_ra_deg(266.405), places=3)
        self.assertAlmostEqual(got_v, milky_way_v_from_dec_deg(-28.936), places=3)

    def test_shader_uses_the_same_rotation_as_python(self) -> None:
        source = SHADERS.read_text(encoding="utf-8")
        for value in (
            "-.0548755604", ".4941094279", "-.8676661490",
            "-.8734370902", "-.4448296300", "-.1980763734",
            "-.4838350155", ".7469822445", ".4559837762",
        ):
            self.assertIn(value, source)
        self.assertIn("skyWidth*fov", source)
        self.assertNotRegex(source, re.compile(r"log2\(6000\.0"))

    def test_kerr_orbit_and_horizon(self) -> None:
        self.assertAlmostEqual(kerr_isco(0.0), 6.0)
        self.assertAlmostEqual(kerr_isco(0.6), 3.8290694188, places=6)
        self.assertAlmostEqual(camera_horizon(0.0), 2.0)
        self.assertAlmostEqual(camera_horizon(0.6), 1.8)
        self.assertAlmostEqual(camera_minimum(0.6), 1.82)
        self.assertLess(kerr_isco(0.9), kerr_isco(0.0))

    def test_cast_milky_way_file_is_the_one_sky_atlas_can_load(self) -> None:
        path = resolve_cast_milky_way_path()
        self.assertIsNotNone(path)
        assert path is not None
        self.assertTrue(path.is_file())
        self.assertIn("milkyway", path.name.casefold())


def shader_cube_cell(d: tuple[float, float, float], grid: int) -> tuple[int, int, int]:
    """Texel (x, y) and face the shader's cubeFace/catalogStars look up for a direction."""

    x, y, z = d
    ax, ay, az = abs(x), abs(y), abs(z)
    if ax >= ay and ax >= az:
        p, face = (y / ax, z / ax), 1 if x > 0 else 2
    elif ay >= az:
        p, face = (x / ay, z / ay), 3 if y > 0 else 4
    else:
        p, face = (x / az, y / az), 5 if z > 0 else 6
    q = [min(max(int(math.floor((math.atan(c) * 4 / math.pi * 0.5 + 0.5) * grid)), 0), grid - 1) for c in p]
    return (face - 1) * grid + q[0], q[1], face


class BlackHoleCatalogStarsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rng = np.random.default_rng(7)

    def test_shader_margin_matches_python(self) -> None:
        source = SHADERS.read_text(encoding="utf-8")
        self.assertIn(f"const float starMargin={str(MARGIN).lstrip('0')};", source)

    def test_cube_cells_match_the_shader_lookup(self) -> None:
        grid = 24
        vectors = self.rng.normal(size=(2000, 3))
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        for vector, cell in zip(vectors, cube_cells(vectors, grid)):
            column, row, face = shader_cube_cell(tuple(vector), grid)
            face_index, rest = divmod(int(cell), grid * grid)
            j, i = divmod(rest, grid)
            self.assertEqual((face_index + 1, face_index * grid + i, j), (face, column, row))

    def test_every_nearby_direction_lists_the_star_brightest_first(self) -> None:
        count = 400
        ra = self.rng.uniform(0.0, 360.0, count)
        dec = np.degrees(np.arcsin(self.rng.uniform(-1.0, 1.0, count)))
        magnitude = self.rng.uniform(-1.0, 9.5, count)
        rgb = self.rng.integers(0, 256, (count, 3))
        grid = 96
        table, stars, _ = build_star_textures(ra, dec, magnitude, rgb, grid=grid)
        vectors = equatorial_vectors(ra, dec)
        for index, vector in enumerate(vectors):
            for _ in range(40):
                offset = self.rng.normal(size=3)
                offset -= offset.dot(vector) * vector
                offset *= self.rng.uniform(0.0, MARGIN) / np.linalg.norm(offset)
                column, row, _ = shader_cube_cell(tuple(vector + offset), grid)
                first, entries = (int(v) for v in table[row, column])
                listed = stars[first:first + entries]
                self.assertTrue(np.all(np.diff(listed[:, 3]) >= 0))
                self.assertTrue(
                    np.any(np.all(np.isclose(listed[:, :3], vector, atol=1e-6), axis=1)),
                    f"star {index} missing from a cell within the margin",
                )


@unittest.skipUnless(sys.platform == "win32", "The native black hole engine is built for Windows.")
class BlackHoleNativeEngineTest(unittest.TestCase):
    def test_library_is_built_and_matches_python_formulas(self) -> None:
        self.assertTrue(library_path().is_file(), "Run scripts/build_blackhole_native.ps1")
        lib = load_library()
        self.assertIsNotNone(lib)
        assert lib is not None
        for spin in (0.0, 0.3, 0.6, 0.9):
            self.assertAlmostEqual(lib.bh_kerr_isco(spin), kerr_isco(spin), places=9)
            self.assertAlmostEqual(lib.bh_kerr_horizon(spin), camera_horizon(spin), places=6)
        # 1e8 solar masses: GM/c^3 is about 492.5 s.
        self.assertAlmostEqual(lib.bh_gravitational_seconds(1e8), 492.5, delta=0.5)


if __name__ == "__main__":
    unittest.main()
