from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QWidget

from photometry_app.core.models import (
    CatalogStar,
    FieldCatalog,
    FileScanResult,
    LightCurvePoint,
    LightCurveSeries,
    ObjectScanSummary,
    ObservationMetadata,
    PhotometryMeasurement,
    ProcessingReport,
    ScanReport,
    WcsStatus,
)
from photometry_app.ui.dialogs import ScanResultsSummaryDialog
from photometry_app.ui.main_window import MainWindow


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCREENSHOT_DIR = PROJECT_ROOT / "docs" / "screenshots"


@contextmanager
def isolated_app_state() -> None:
    with tempfile.TemporaryDirectory() as config_dir, tempfile.TemporaryDirectory() as state_dir:
        config_path = Path(config_dir) / "settings.json"
        state_path = Path(state_dir) / "state.json"
        config_path.write_text("{}", encoding="utf-8")
        state_path.write_text("{}", encoding="utf-8")

        previous_config = os.environ.get("CITIZEN_PHOTOMETRY_CONFIG_PATH")
        previous_state = os.environ.get("CITIZEN_PHOTOMETRY_STATE_PATH")
        os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"] = str(config_path)
        os.environ["CITIZEN_PHOTOMETRY_STATE_PATH"] = str(state_path)
        try:
            yield
        finally:
            if previous_config is None:
                os.environ.pop("CITIZEN_PHOTOMETRY_CONFIG_PATH", None)
            else:
                os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"] = previous_config
            if previous_state is None:
                os.environ.pop("CITIZEN_PHOTOMETRY_STATE_PATH", None)
            else:
                os.environ["CITIZEN_PHOTOMETRY_STATE_PATH"] = previous_state


def _widget_rect(root: QWidget, widget: QWidget) -> QRect:
    top_left = widget.mapTo(root, QPoint(0, 0))
    return QRect(top_left, widget.size())


def _union_widget_rects(root: QWidget, widgets: list[QWidget]) -> QRect:
    rects = [_widget_rect(root, widget) for widget in widgets if widget is not None]
    if not rects:
        raise ValueError("At least one widget is required to build a crop rectangle.")
    union = QRect(rects[0])
    for rect in rects[1:]:
        union = union.united(rect)
    return union


def _save_crop(widget: QWidget, output_path: Path, crop_rect: QRect | None = None) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    QApplication.processEvents()
    pixmap = widget.grab()
    if crop_rect is not None:
        crop_rect = crop_rect.intersected(pixmap.rect())
        pixmap = pixmap.copy(crop_rect)
    pixmap.save(str(output_path))


def _build_scan_summaries() -> list[ObjectScanSummary]:
    return [
        ObjectScanSummary(
            object_name="R Vir",
            files=[
                FileScanResult(
                    path=Path("rvir_01.fits"),
                    object_folder="R Vir",
                    metadata=ObservationMetadata(None, None, None, None, None, "R Vir"),
                    wcs_status=WcsStatus.SOLVED,
                ),
                FileScanResult(
                    path=Path("rvir_02.fits"),
                    object_folder="R Vir",
                    metadata=ObservationMetadata(None, None, None, None, None, "R Vir"),
                    wcs_status=WcsStatus.UNSOLVED,
                    reasons=["Missing WCS header"],
                ),
                FileScanResult(
                    path=Path("rvir_03.fits"),
                    object_folder="R Vir",
                    metadata=ObservationMetadata(None, None, None, None, None, "R Vir"),
                    wcs_status=WcsStatus.INVALID,
                    reasons=["Corrupt FITS header"],
                ),
            ],
        ),
        ObjectScanSummary(
            object_name="DemoOrion",
            files=[
                FileScanResult(
                    path=Path("demo_01.fits"),
                    object_folder="DemoOrion",
                    metadata=ObservationMetadata(None, None, None, None, None, "DemoOrion"),
                    wcs_status=WcsStatus.SOLVED,
                ),
                FileScanResult(
                    path=Path("demo_02.fits"),
                    object_folder="DemoOrion",
                    metadata=ObservationMetadata(None, None, None, None, None, "DemoOrion"),
                    wcs_status=WcsStatus.SOLVED,
                ),
            ],
        ),
    ]


def generate_loaded_results_dialog() -> Path:
    dialog = ScanResultsSummaryDialog(_build_scan_summaries(), selected_object_name="R Vir")
    dialog.resize(1280, 780)
    dialog.show()
    QApplication.processEvents()

    output_path = SCREENSHOT_DIR / "loaded_results_dialog.png"
    _save_crop(dialog, output_path)
    dialog.close()
    return output_path


def generate_workspace_strip() -> Path:
    window = MainWindow()
    window.resize(1600, 900)
    report = ScanReport(root_path=PROJECT_ROOT / "Files", object_summaries=_build_scan_summaries())
    window._root_path_input.setText(str(PROJECT_ROOT / "Files"))
    window._current_report = report
    window._populate_object_table(report.object_summaries)
    window._select_scanned_object_by_name("R Vir")
    window._set_generate_button_attention(True)
    window.show()
    QApplication.processEvents()

    output_path = SCREENSHOT_DIR / "workspace_strip.png"
    window._workspace_actions_group.grab().save(str(output_path))
    window.close()
    return output_path


def _build_processing_report() -> ProcessingReport:
    start_time = datetime(2026, 4, 1, 3, 0, 0)
    target_entry = CatalogStar(
        catalog="vsx",
        source_id="vsx-rvir",
        name="R Vir",
        ra_deg=190.123,
        dec_deg=12.345,
        magnitude=11.7,
        is_variable=True,
        metadata={"literature_period_days": 0.507, "type": "EA"},
    )
    check_entry = CatalogStar(
        catalog="gaia",
        source_id="gaia-001",
        name="Gaia DR3 1234567890",
        ra_deg=190.101,
        dec_deg=12.321,
        magnitude=11.2,
        is_variable=False,
    )

    measurements = [
        PhotometryMeasurement(
            source_id="vsx-rvir",
            source_name="R Vir",
            catalog="vsx",
            object_name="R Vir",
            file_path=Path(f"rvir_{index:02d}.fits"),
            observation_time=start_time + timedelta(minutes=index * 8),
            filter_name="V",
            ra_deg=190.123,
            dec_deg=12.345,
            x=220.0 + index,
            y=180.0 + index,
            flux=14000.0 - index * 250.0,
            flux_error=180.0,
            instrumental_magnitude=-4.1,
            differential_magnitude=11.72 + index * 0.03,
            differential_magnitude_error=0.03,
            is_variable=True,
            is_reference=False,
            snr=34.0 - index,
        )
        for index in range(4)
    ]
    measurements.extend(
        PhotometryMeasurement(
            source_id="gaia-001",
            source_name="Check Star",
            catalog="gaia",
            object_name="R Vir",
            file_path=Path(f"rvir_{index:02d}.fits"),
            observation_time=start_time + timedelta(minutes=index * 8),
            filter_name="V",
            ra_deg=190.101,
            dec_deg=12.321,
            x=150.0 + index,
            y=240.0 + index,
            flux=18000.0,
            flux_error=160.0,
            instrumental_magnitude=-4.6,
            differential_magnitude=11.20,
            differential_magnitude_error=0.02,
            is_variable=False,
            is_reference=True,
            is_check=True,
            snr=41.0,
        )
        for index in range(4)
    )

    points = [
        LightCurvePoint(
            observation_time=measurement.observation_time,
            file_path=measurement.file_path,
            differential_magnitude=measurement.differential_magnitude,
            instrumental_magnitude=measurement.instrumental_magnitude,
            flux=measurement.flux,
            flux_error=measurement.flux_error,
            differential_magnitude_error=measurement.differential_magnitude_error,
        )
        for measurement in measurements
        if measurement.source_id == "vsx-rvir"
    ]

    return ProcessingReport(
        object_name="R Vir",
        files_processed=4,
        solved_files=4,
        field_catalog=FieldCatalog(
            center_ra_deg=190.12,
            center_dec_deg=12.34,
            radius_deg=0.75,
            gaia_stars=[check_entry],
            variable_stars=[target_entry],
        ),
        reference_stars=[check_entry],
        measurements=measurements,
        light_curves=[
            LightCurveSeries(
                object_name="R Vir",
                source_id="vsx-rvir",
                source_name="R Vir",
                filter_name="V",
                points=points,
            )
        ],
    )


def generate_source_results_actions() -> Path:
    window = MainWindow()
    window.resize(3600, 1200)
    report = _build_processing_report()
    window._current_processing_report = report
    window._populate_source_table(report)
    window._results_tabs.setCurrentWidget(window._source_table)
    window._source_table.selectRow(0)
    window._update_source_period_button_state()
    window._differential_main_splitter.setSizes([2900, 500])
    window.show()
    QApplication.processEvents()

    crop_rect = _union_widget_rects(
        window,
        [
            window._source_name_filter,
            window._source_category_filter_combo,
            window._pull_period_button,
            window._calculate_period_button,
            window._clear_source_selection_button,
            window._find_better_fit_button,
            window._increase_snr_button,
            window._results_tabs,
        ],
    )
    crop_rect.adjust(-70, -6, 20, 16)

    output_path = SCREENSHOT_DIR / "source_results_actions.png"
    _save_crop(window, output_path, crop_rect)
    window.close()
    return output_path


def main() -> None:
    app = QApplication.instance() or QApplication([])
    with isolated_app_state():
        outputs = [
            generate_loaded_results_dialog(),
            generate_workspace_strip(),
            generate_source_results_actions(),
        ]
    for output in outputs:
        print(output.relative_to(PROJECT_ROOT))
    app.quit()


if __name__ == "__main__":
    main()