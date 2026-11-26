import time
from unittest.mock import patch
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog
from photometry_app.ui.asteroid_photometry_dialog import AsteroidPhotometryDialog
from photometry_app.core.comet_photometry import save_comet_session
from test_comet_photometry_dialog import make_dialog


def test_asteroid_workflow_measures_exposures_and_restores_saved_results(tmp_path):
    app = QApplication.instance() or QApplication([])
    source, pair = make_dialog(tmp_path)
    source.detection.object_type = "Asteroid"
    dialog = AsteroidPhotometryDialog(frame_paths=source.paths, frame_metadata=source.metadata,
        detection_result=source.detection_result, detection_index=0, reference_path=source.reference_path)
    source.close()
    # Clear-band workflow is valid without matching standard-band calibration.
    pair.provenance["filter"] = "L"
    dialog._prepared(pair, [], "No references")
    deadline = time.monotonic() + 10
    while dialog._worker is not None and time.monotonic() < deadline:
        QTest.qWait(10)
        app.processEvents()
    assert dialog._worker is None
    assert len(dialog.rows) == 3
    assert dialog.tabs.tabText(1) == "Asteroid"
    assert dialog.tabs.tabText(3) == "Light Curve"
    assert dialog.export_button.isEnabled()
    assert "exposures measured" in dialog.result_label.text()
    path = tmp_path / "asteroid.castphotometry"
    expected = dialog.rows
    save_comet_session(path, pair, dialog._session_settings(), [])
    for frame in pair.provenance["frames"]:
        from pathlib import Path
        Path(frame["path"]).unlink()
    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(path), "")):
        dialog._open_session()
    assert dialog.rows == expected
    assert "Missing original" in dialog.status.text()
    assert dialog._worker is None
    dialog.close()
