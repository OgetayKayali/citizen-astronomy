from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from photometry_app.core.comet_photometry import build_comet_stack_pair, save_comet_session
from photometry_app.ui.comet_photometry_dialog import CometPhotometryDialog
from test_comet_photometry import make_sequence


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def make_dialog(tmp_path):
    frames, _ = make_sequence(tmp_path)
    detection = SimpleNamespace(name="C/2026 Test", designation="Test", object_type="Comet",
        predicted_ra_deg=frames[0].ra_deg, predicted_dec_deg=frames[0].dec_deg,
        ra_rate_arcsec_per_hour=0, dec_rate_arcsec_per_hour=0)
    result = SimpleNamespace(detections=[detection], filter_name="V", solved_field=None,
                             prediction_time=datetime(2026, 1, 1, tzinfo=UTC))
    metadata = {str(f.path.resolve()): SimpleNamespace(date_obs=f.midpoint, exposure_seconds=f.exposure, filter_name="V")
                for f in frames}
    dialog = CometPhotometryDialog(frame_paths=[f.path for f in frames], frame_metadata=metadata,
        detection_result=result, detection_index=0, reference_path=frames[0].path)
    pair = build_comet_stack_pair(frames, reference_path=frames[0].path)
    return dialog, pair


def test_guided_entry_and_instrumental_measurement(app, tmp_path):
    dialog, pair = make_dialog(tmp_path)
    assert not dialog.prepare_button.isEnabled()
    assert not dialog.export_button.isEnabled()
    assert not dialog.advanced.isVisible()
    dialog.calibrated.setChecked(True)
    assert dialog.prepare_button.isEnabled()
    dialog._prepared(pair, [], "No catalog stars")
    assert dialog.rows
    assert dialog.tabs.currentIndex() == 1
    assert "Instrumental" in dialog.result_label.text()
    assert dialog.export_button.isEnabled()
    assert dialog.rows[0]["magnitude_system"] == "Instrumental"
    dialog.close()


def test_unprepared_tabs_are_clickable_and_explain_next_action(app, tmp_path):
    dialog, pair = make_dialog(tmp_path)
    dialog.show()
    app.processEvents()
    assert dialog.aperture_controls.isHidden()
    assert "Prepare Photometry first" in dialog.aperture_guidance.text()
    for index, (page, action) in enumerate(dialog._result_pages, start=1):
        QTest.mouseClick(dialog.tabs.tabBar(), Qt.MouseButton.LeftButton,
                        pos=dialog.tabs.tabBar().tabRect(index).center())
        assert dialog.tabs.currentIndex() == index
        assert page.currentIndex() == 0
        action.click()
        assert dialog.tabs.currentIndex() == 0
        assert "Confirm" in dialog.status.text()
    dialog.step_buttons[1].click()
    assert dialog.tabs.currentIndex() == 1
    assert not dialog.export_button.isEnabled()
    dialog._prepared(pair, [], "")
    assert all(page.currentIndex() == 1 for page, _ in dialog._result_pages)
    assert not dialog.aperture_controls.isHidden()
    assert "inner circle" in dialog.comet_guidance.text()
    dialog.close()


def test_center_and_sky_pickers_receive_real_image_clicks(app, tmp_path):
    dialog, pair = make_dialog(tmp_path)
    dialog.show()
    dialog._prepared(pair, [], "")
    app.processEvents()
    view = dialog.comet_view
    initial = dialog._center
    point = view.image_to_widget(83, 78).toPoint()
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=point)
    assert dialog._center == initial  # Normal navigation never moves the aperture.
    dialog.tabs.setCurrentIndex(0)
    dialog.pick_center.click()
    app.processEvents()
    assert dialog.tabs.currentIndex() == 1
    point = view.image_to_widget(83, 78).toPoint()
    expected = view.widget_to_image(point.x(), point.y())
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=point)
    assert dialog._center == pytest.approx((expected.x(), expected.y()))
    assert not dialog._picking_center
    dialog._measure()
    assert next(row for row in dialog.rows if row["selected_aperture"])["center_x"] == pytest.approx(expected.x())
    dialog._pick_sky()
    point = view.image_to_widget(115, 80).toPoint()
    expected = view.widget_to_image(point.x(), point.y())
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=point)
    assert dialog._sky_center == pytest.approx((expected.x(), expected.y()))
    assert not dialog._picking_sky
    dialog._measure()
    assert dialog.rows
    # Dragging with the center picker armed still pans rather than placing it.
    dialog.pick_center.click()
    initial = dialog._center
    point = view.image_to_widget(*initial).toPoint()
    QTest.mousePress(view, Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseMove(view, point + QPoint(35, 20))
    QTest.mouseRelease(view, Qt.MouseButton.LeftButton, pos=point + QPoint(35, 20))
    assert dialog._center == initial
    dialog.pick_center.click()
    assert not dialog._picking_center
    dialog.reset_center.click()
    assert dialog._center == pytest.approx((pair.center_x, pair.center_y))
    dialog.close()


def test_centering_units_and_invalid_annulus_clear_stale_exports(app, tmp_path):
    dialog, pair = make_dialog(tmp_path)
    dialog._prepared(pair, [], "")
    dialog._center_clicked(81, 79, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    dialog._measure()
    assert dialog.rows[0]["center_x"] == 81
    dialog._reset_center()
    dialog._measure()
    assert dialog.rows[0]["center_x"] == pytest.approx(80)
    dialog.units.setCurrentIndex(1)
    dialog._measure()
    assert dialog.radius.value() == pytest.approx(12 * pair.pixel_scale, abs=0.1)
    dialog.auto_sky.setChecked(False)
    dialog.sky_inner.setValue(5)
    dialog._measure()
    assert not dialog.rows
    assert not dialog.export_button.isEnabled()
    assert dialog.measurements_table.rowCount() == 0
    dialog.close()


def test_session_reopen_reproduces_measurements(app, tmp_path):
    dialog, pair = make_dialog(tmp_path)
    dialog._prepared(pair, [], "")
    dialog.radius.setValue(15)
    dialog._measure()
    expected = dialog.rows
    path = tmp_path / "test.castcomet"
    save_comet_session(path, pair, dialog._session_settings(), [])
    dialog.radius.setValue(8)
    dialog._measure()
    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(path), "")):
        dialog._open_session()
    assert dialog.radius.value() == 15
    assert dialog.rows == expected
    dialog.close()


def test_tail_picker_filter_and_session_replay(app, tmp_path):
    import numpy as np
    dialog, pair = make_dialog(tmp_path)
    pair.comet[80, 91] += 2000
    saved_star_pixels = pair.stars.copy()
    saved_comet_pixels = pair.comet.copy()
    dialog.show()
    dialog._prepared(pair, [], "")
    app.processEvents()
    original_center = dialog._center
    dialog.comet_contrast.setCurrentText("Faint structures")
    dialog.pick_tail.click()
    view = dialog.comet_view
    point = view.image_to_widget(64, 89).toPoint()
    expected = view.widget_to_image(point.x(), point.y())
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=point)
    assert dialog._tail_endpoint == pytest.approx((expected.x(), expected.y()))
    view.imageDoubleClicked.emit(expected.x(), expected.y(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    assert dialog._center == original_center
    dialog.star_filter.setChecked(True)
    dialog.filter_statistic.setCurrentIndex(1)
    dialog._measure()
    selected = next(r for r in dialog.rows if r["selected_aperture"])
    assert selected["tail_length_arcmin"] > 0
    assert 0 < selected["tail_pa_deg"] < 90
    assert selected["unfiltered_flux_counts_per_second"] > selected["flux_counts_per_second"]
    assert selected["replaced_pixel_count"] == 1
    assert selected["radial_filter_statistic"] == "median"
    assert selected["tail_selection_contrast"] == "Faint structures"
    assert dialog.filter_preview.isEnabled()
    np.testing.assert_array_equal(pair.comet, saved_comet_pixels)
    np.testing.assert_array_equal(pair.stars, saved_star_pixels)
    saved = tmp_path / "morphology.castcomet"
    expected_rows = list(dialog.rows)
    save_comet_session(saved, pair, dialog._session_settings(), [])
    dialog.star_filter.setChecked(False)
    dialog.clear_tail.click()
    dialog._measure()
    assert dialog.rows[0]["tail_pa_deg"] is None
    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(saved), "")):
        dialog._open_session()
    assert dialog.rows == expected_rows
    dialog.close()


def test_invalid_saved_preview_preserves_active_pair_and_aperture_edits_reuse_raster(app, tmp_path):
    from dataclasses import replace
    from unittest.mock import patch
    import numpy as np
    from PySide6.QtWidgets import QMessageBox
    from photometry_app.ui import comet_photometry_dialog as module
    dialog, pair = make_dialog(tmp_path)
    with patch.object(module, "render_comet_stack_preview", wraps=module.render_comet_stack_preview) as render:
        dialog._prepared(pair, [], "")
        assert render.call_count == 2
        dialog.radius.setValue(14)
        dialog.star_filter.setChecked(True)
        dialog._measure()
        dialog._show_comet()
        assert render.call_count == 2
        original_flux = dialog.rows[0]["flux_counts_per_second"]
        dialog.comet_contrast.setCurrentText("Faint structures")
        assert render.call_count == 3
        assert dialog.rows[0]["flux_counts_per_second"] == original_flux
    expected = list(dialog.rows)
    corrupt = replace(pair, comet=np.full_like(pair.comet, np.nan))
    path = tmp_path / "empty-stack.castcomet"
    save_comet_session(path, corrupt, dialog._session_settings(), [])
    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(path), "")), \
            patch.object(QMessageBox, "warning") as warning:
        dialog._open_session()
    assert warning.called
    assert dialog.pair is pair
    assert dialog.rows == expected
    dialog.close()


def test_sky_patch_selection_and_calibration_review(app, tmp_path):
    import math
    import numpy as np
    from photometry_app.core.comet_photometry import ComparisonStar
    dialog, pair = make_dialog(tmp_path)
    yy, xx = np.indices(pair.stars.shape)
    comparisons = []
    for index, (x, y) in enumerate([(35, 40), (125, 35), (35, 125), (125, 125)]):
        if index:
            pair.stars += 60 * np.exp(-((xx-x)**2 + (yy-y)**2) / 8)
        comparisons.append(ComparisonStar(str(index), str(index), x, y,
                                          25 - 2.5 * math.log10(60 * 8 * math.pi), 0.02, 0.6))
    dialog._prepared(pair, comparisons, "Four stars")
    assert dialog.zero_point is not None
    assert dialog.zero_point.value == pytest.approx(25, abs=0.01)
    assert dialog.rows[0]["comparison_median_fwhm_px"] == pytest.approx(2.35482*2, abs=0.45)
    previous_radius = dialog.star_radius.value()
    dialog.apply_fwhm.click()
    assert dialog.star_radius.value() == math.ceil(4 * dialog.rows[0]["comparison_median_fwhm_px"])
    dialog.star_radius.setValue(previous_radius)
    assert "untransformed" in dialog.result_label.text()
    dialog.background_mode.setCurrentIndex(1)
    dialog._measure()
    assert not dialog.rows
    dialog._pick_sky()
    dialog._sky_clicked(120, 80, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    dialog._measure()
    assert dialog.rows
    assert dialog.rows[0]["background_method"] == "Separate sky patch"
    dialog.star_table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    dialog.star_table.item(1, 0).setCheckState(Qt.CheckState.Unchecked)
    dialog._measure()
    assert dialog.zero_point is None
    assert "Instrumental" in dialog.result_label.text()
    dialog.close()


def test_rectangle_sky_picker_preserves_geometry_in_session(app, tmp_path):
    dialog, pair = make_dialog(tmp_path)
    dialog._prepared(pair, [], "")
    dialog.background_mode.setCurrentIndex(2)
    dialog._measure()
    assert not dialog.rows
    dialog._pick_sky()
    dialog._sky_clicked(132., 120., Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    dialog._sky_clicked(108., 104., Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    dialog._measure()
    assert dialog.rows[0]["background_method"] == "Separate sky rectangle"
    assert dialog.rows[0]["sky_rectangle_x_min"] == 108
    assert dialog.rows[0]["sky_rectangle_y_max"] == 120
    assert dialog.comet_view._selection_overlays[0].x0 == 108
    expected = list(dialog.rows)
    path = tmp_path / "rectangle.castcomet"
    save_comet_session(path, pair, dialog._session_settings(), [])
    dialog.background_mode.setCurrentIndex(0)
    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(path), "")):
        dialog._open_session()
    assert dialog.rows == expected
    dialog.close()


def test_worker_cannot_be_destroyed_by_close(app, tmp_path):
    dialog, _ = make_dialog(tmp_path)
    class Worker:
        cancelled = False
        def requestInterruption(self):
            self.cancelled = True
    worker = Worker()
    dialog._worker = worker
    dialog.reject()
    assert worker.cancelled
    dialog._worker = None
    dialog.close()


def test_horizons_worker_matches_epochs_and_keeps_instrumental_results(app, tmp_path):
    from astropy.table import Table
    from astropy.time import Time
    from photometry_app.ui.comet_photometry_dialog import CometPreparationWorker
    frames, _ = make_sequence(tmp_path)
    worker = CometPreparationWorker(frames=frames, reference_path=frames[0].path,
        detection=SimpleNamespace(designation="C/2026 Test", name="Test"), solved_field=None,
        use_horizons=True, observer={"lat": 40, "lon": -75, "elevation": 0.25}, band="V")
    table = Table({"datetime_jd": [Time(f.midpoint).jd for f in reversed(frames)],
                   "RA": [f.ra_deg for f in reversed(frames)], "DEC": [f.dec_deg for f in reversed(frames)]})
    completed, failed = [], []
    worker.completed.connect(lambda *args: completed.append(args))
    worker.failed.connect(failed.append)
    with patch("astroquery.jplhorizons.Horizons") as query, \
            patch("photometry_app.core.standard_magnitude.query_apass_field", return_value=[]):
        query.return_value.ephemerides.return_value = table
        worker.run()
        assert not failed
        pair, stars, note = completed[0]
        assert pair.provenance["tracking"] == "JPL Horizons · observing site"
        assert pair.provenance["frames"][0]["ra_deg"] == frames[0].ra_deg
        assert "Instrumental" in note
        assert not stars
        completed.clear()
        table["datetime_jd"] += 1
        worker.run()
        assert not completed
        assert "times do not match" in failed[0]


def test_invalid_session_preserves_current_measurements(app, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    dialog, pair = make_dialog(tmp_path)
    dialog._prepared(pair, [], "")
    previous = list(dialog.rows)
    settings = dialog._session_settings()
    settings["radius"] = -1
    path = tmp_path / "invalid.castcomet"
    save_comet_session(path, pair, settings, [])
    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(path), "")), \
            patch.object(QMessageBox, "warning") as warning:
        dialog._open_session()
        warning.assert_called_once()
    assert dialog.rows == previous
    assert dialog.radius.value() == 12
    dialog.close()


def test_horizons_batches_long_sequences_retries_and_preserves_epoch_order(app, tmp_path):
    from dataclasses import replace
    from datetime import timedelta
    from astropy.table import Table
    from requests import HTTPError, Response
    from unittest.mock import MagicMock
    from photometry_app.ui.comet_photometry_dialog import CometPreparationWorker
    frames, _ = make_sequence(tmp_path)
    frames = [replace(frames[0], midpoint=frames[0].midpoint + timedelta(minutes=i)) for i in range(76)]
    worker = CometPreparationWorker(frames=frames, reference_path=frames[0].path,
        detection=SimpleNamespace(designation="3I", name="3I/ATLAS"), solved_field=None,
        use_horizons=True, observer="500", band=None)
    response = Response()
    response.status_code = 502
    failure = HTTPError("502 Server Error for url: https://example.test/long-query", response=response)
    batches, requests = [], []

    def make_query(**kwargs):
        epochs = kwargs["epochs"]
        batches.append(epochs)
        query = MagicMock()
        def ephemerides(**options):
            requests.append(options)
            if len(requests) == 1:
                raise failure
            return Table({"datetime_jd": epochs[::-1], "RA": epochs[::-1], "DEC": [20.] * len(epochs),
                          "r": [2.] * len(epochs), "delta": [1.5] * len(epochs)})
        query.ephemerides.side_effect = ephemerides
        return query

    with patch("astroquery.jplhorizons.Horizons", side_effect=make_query), \
            patch.object(worker, "_wait_to_retry") as wait:
        updated = worker._horizons_positions(frames)
    assert [len(batch) for batch in batches] == [20, 20, 20, 20, 16]
    assert batches[0] == batches[1]
    assert len(updated) == 76
    assert [f.ra_deg for f in updated] == sorted(f.ra_deg for f in updated)
    assert all(r["quantities"] == "1,19,20" and r["extra_precision"] for r in requests)
    assert worker.distances == [(2., 1.5)] * len(frames)
    assert requests[0]["cache"] and not requests[1]["cache"]
    wait.assert_called_once_with(1)


@pytest.mark.parametrize("status,attempts", [(502, 3), (400, 1)])
def test_horizons_failure_is_bounded_readable_and_never_stacks(app, tmp_path, status, attempts):
    from requests import HTTPError, Response
    from photometry_app.ui.comet_photometry_dialog import CometPreparationWorker
    frames, _ = make_sequence(tmp_path)
    worker = CometPreparationWorker(frames=frames, reference_path=frames[0].path,
        detection=SimpleNamespace(designation="3I", name="3I/ATLAS"), solved_field=None,
        use_horizons=True, observer="500", band=None)
    response = Response()
    response.status_code = status
    failed = []
    worker.failed.connect(failed.append)
    with patch("astroquery.jplhorizons.Horizons") as query, \
            patch.object(worker, "_wait_to_retry"), \
            patch("photometry_app.ui.comet_photometry_dialog.build_comet_stack_pair") as build:
        query.return_value.ephemerides.side_effect = HTTPError("https://example.test/very-long-url", response=response)
        worker.run()
    assert query.call_count == attempts
    build.assert_not_called()
    assert f"HTTP {status}" in failed[0]
    assert "Retry Prepare" in failed[0]
    assert "https://" not in failed[0]


def test_horizons_retry_wait_can_be_cancelled(app, tmp_path):
    from photometry_app.ui.comet_photometry_dialog import CometPreparationWorker
    worker = CometPreparationWorker(frames=[], reference_path=tmp_path,
        detection=None, solved_field=None, use_horizons=True, observer="500", band=None)
    with patch.object(worker, "isInterruptionRequested", return_value=True):
        with pytest.raises(InterruptedError, match="cancelled"):
            worker._wait_to_retry(2)


def test_failed_repreparation_preserves_saved_sessions_calibration_band(app, tmp_path):
    dialog, pair = make_dialog(tmp_path)
    pair.provenance["filter"] = "B"
    dialog._prepared(pair, [], "Restored B session")
    assert dialog.band == "B"
    with patch("photometry_app.ui.comet_photometry_dialog.CometPreparationWorker") as worker:
        dialog._prepare()  # The original launch selection has V frames.
        assert worker.call_args.kwargs["band"] == "V"
        dialog._failed("Preparation failed")
        dialog._worker_finished()
    dialog._measure()
    assert dialog.band == "B"
    assert dialog.rows[0]["filter"] == "B"
    assert sum(row["selected_aperture"] for row in dialog.rows) == 1
    dialog.close()


def test_main_window_photometry_accepts_asteroids_and_comets_but_rejects_mixed_groups(app):
    from photometry_app.ui.main_window import MainWindow, _ASTEROID_ALL_GROUP_KEY
    stub = SimpleNamespace(_selected_asteroid_detection=lambda: SimpleNamespace(object_type="Asteroid", name="Test"),
                            _asteroid_frame_paths=[Path("frame.fits")], _asteroid_frame_groups={})
    assert not MainWindow._comet_photometry_availability(stub)[0]
    stub._current_asteroid_detection_result = object()
    assert MainWindow._comet_photometry_availability(stub)[0]
    stub._selected_asteroid_detection = lambda: SimpleNamespace(object_type="Comet", name="Test")
    assert MainWindow._comet_photometry_availability(stub)[0]
    stub._asteroid_active_group_key = _ASTEROID_ALL_GROUP_KEY
    stub._asteroid_frame_groups = {"V": 1, "B": 2}
    assert not MainWindow._comet_photometry_availability(stub)[0]
