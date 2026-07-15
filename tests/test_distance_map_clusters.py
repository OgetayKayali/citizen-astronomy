from __future__ import annotations

import importlib.util
import unittest

from photometry_app.core.distance_map import DistanceMapStar
from photometry_app.core.distance_map_clusters import DistanceMapClusterSettings, find_distance_map_cluster


def _cluster_star(
    index: int,
    *,
    pm_ra: float,
    pm_dec: float,
    parallax_mas: float,
    magnitude: float = 12.0,
    bp_rp: float = 0.8,
) -> DistanceMapStar:
    distance_pc = 1000.0 / parallax_mas
    return DistanceMapStar(
        source_id=str(index),
        name=f"Cluster {index}",
        ra_deg=0.0,
        dec_deg=0.0,
        magnitude=magnitude,
        bp_rp=bp_rp,
        parallax_mas=parallax_mas,
        parallax_error_mas=0.1,
        pm_ra_mas_per_year=pm_ra,
        pm_dec_mas_per_year=pm_dec,
        distance_pc=distance_pc,
        x_pc=float(index),
        y_pc=float(index),
        z_pc=0.0,
    )


class DistanceMapClusterTest(unittest.TestCase):
    def test_cluster_settings_default_preset_normalizes(self) -> None:
        settings = DistanceMapClusterSettings().normalized()
        self.assertEqual(settings.preset, "default")
        self.assertTrue(settings.mark_on_image)

    def test_find_distance_map_cluster_prefers_dense_cluster_with_parallax(self) -> None:
        stars = [
            _cluster_star(
                index,
                pm_ra=10.0 + (index * 0.05),
                pm_dec=-4.5 + (index * 0.04),
                parallax_mas=2.0 + (index * 0.03),
            )
            for index in range(6)
        ]
        stars.extend(
            [
                _cluster_star(index=100, pm_ra=-14.0, pm_dec=6.5, parallax_mas=8.0, magnitude=13.0, bp_rp=1.1),
                _cluster_star(index=101, pm_ra=24.0, pm_dec=15.0, parallax_mas=0.4, magnitude=13.5, bp_rp=1.3),
            ]
        )

        result = find_distance_map_cluster(stars)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.used_parallax)
        self.assertEqual(result.member_count, 6)
        self.assertEqual(result.member_indices, [0, 1, 2, 3, 4, 5])

    def test_find_distance_map_cluster_supports_sklearn_dbscan(self) -> None:
        if importlib.util.find_spec("sklearn") is None:
            self.skipTest("sklearn is not installed")
        stars = [
            _cluster_star(
                index,
                pm_ra=10.0 + (index * 0.05),
                pm_dec=-4.5 + (index * 0.04),
                parallax_mas=2.0 + (index * 0.03),
            )
            for index in range(6)
        ]
        stars.append(_cluster_star(index=100, pm_ra=-14.0, pm_dec=6.5, parallax_mas=8.0, magnitude=13.0, bp_rp=1.1))

        result = find_distance_map_cluster(stars, method="sklearn")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.clustering_method, "sklearn")


if __name__ == "__main__":
    unittest.main()
