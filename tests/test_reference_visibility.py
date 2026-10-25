from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QCheckBox

from photometry_app.core.matching import OverviewLightCurveLayer, build_overview_light_curve_layers
from photometry_app.core.models import LightCurveSeries, PhotometryMeasurement
from photometry_app.core.plotting import LightCurveFitConfig, build_light_curve_plot_payload
from photometry_app.ui.main_window import MainWindow
from photometry_app.ui.light_curve_widget import LightCurvePlotWidget


def measurements():
    rows = []
    for band in ('R', 'B'):
        for i in range(4):
            for source, value in [('target', 12 + i * .01), (f'comp-{band}', 11), (f'check-{band}', 13)]:
                rows.append(PhotometryMeasurement(
                    source_id=source, source_name=source, catalog='manual', object_name='Field',
                    file_path=Path(f'{band}-{i}.fits'), observation_time=datetime(2026, 10, 1) + timedelta(hours=i),
                    filter_name=band, ra_deg=10, dec_deg=20, x=100, y=100,
                    flux=1000, flux_error=10, instrumental_magnitude=value,
                    differential_magnitude=value, calibrated_magnitude=value, calibrated_magnitude_error=.01,
                    is_variable=source == 'target', is_reference=source != 'target', is_check=source.startswith('check'),
                    comparison_source_ids=[f'comp-{band}'] if source == 'target' else [],
                ))
    return rows


@pytest.mark.parametrize('axis', ['jd', 'datetime', 'phase'])
def test_reference_layers_keep_target_fit_and_phase_origin(axis):
    layers, _ = build_overview_light_curve_layers([r for r in measurements() if r.filter_name == 'R'], 'target')
    target = next(layer.series for layer in layers if layer.role == 'target')
    references = [layer for layer in layers if layer.role != 'target']
    # A mismatched band must never enter this plot, even if it shares file labels.
    references.append(OverviewLightCurveLayer(role='comparison', legend_label='Wrong band',
                      series=replace(references[0].series, filter_name='B')))
    kwargs = dict(y_axis_mode='calibrated_magnitude', x_axis_mode=axis, phase_period_hours=2.5,
                  phase_anchor_mode='first_observation', fit_config=LightCurveFitConfig(polynomial_degree=1))
    plain = build_light_curve_plot_payload(target, 'Empty', **kwargs)
    combined = build_light_curve_plot_payload(target, 'Empty', reference_layers=references, **kwargs)
    assert {layer.role for layer in combined.layers} == {'target', 'comparison', 'check'}
    assert {layer.filter_name for layer in combined.layers} == {'R'}
    assert combined.layers[0].points == plain.points
    np.testing.assert_array_equal(combined.fit_y_values, plain.fit_y_values)
    for layer in combined.layers[1:]:
        assert [p.x for p in layer.points] == [p.x for p in plain.points]
    assert combined.x_axis_mode == axis


def test_band_specific_references_and_toggle():
    app = QApplication.instance() or QApplication([])
    references = QCheckBox()
    checks = QCheckBox()
    references.setChecked(True)
    checks.setChecked(True)
    harness = SimpleNamespace(_show_references_checkbox=references, _show_checks_checkbox=checks,
                              _overview_measurements=measurements, _overview_max_comparison_stars=lambda: 8)
    series = LightCurveSeries(object_name='Field', source_id='target', source_name='Target', filter_name='R', points=[])
    layers = MainWindow._reference_layers_for_series(harness, series)
    assert {layer.series.source_id for layer in layers} == {'comp-R', 'check-R'}
    references.setChecked(False)
    assert {layer.series.source_id for layer in MainWindow._reference_layers_for_series(harness, series)} == {'check-R'}
    checks.setChecked(False)
    assert MainWindow._reference_layers_for_series(harness, series) == []
    references.setChecked(True)
    assert {layer.series.source_id for layer in MainWindow._reference_layers_for_series(harness, series)} == {'comp-R'}


def test_overview_toggle_hides_reference_and_check_layers_separately():
    app = QApplication.instance() or QApplication([])
    references = QCheckBox()
    checks = QCheckBox()
    harness = SimpleNamespace(
        _show_references_checkbox=references, _show_checks_checkbox=checks,
        _current_light_curve_y_axis_mode=lambda: 'calibrated_magnitude',
        _series_selector=SimpleNamespace(currentData=lambda: None), _selected_source_id=lambda: 'target',
        _selected_source_target=lambda: ('Target', 'manual'), _overview_measurements=measurements,
        _overview_max_comparison_stars=lambda: 8, _overview_preferred_comparison_source_ids=lambda _: [],
        _current_light_curve_x_axis_mode=lambda: 'jd', _light_curve_widget=Mock(),
        _show_standard_magnitude_status_note=Mock(), statusBar=lambda: Mock(),
    )
    MainWindow._plot_overview_series(harness)
    assert {layer.role for layer in harness._light_curve_widget.plot_overview.call_args.args[0]} == {'target'}
    references.setChecked(True)
    MainWindow._plot_overview_series(harness)
    assert {layer.role for layer in harness._light_curve_widget.plot_overview.call_args.args[0]} == {'target', 'comparison'}
    checks.setChecked(True)
    MainWindow._plot_overview_series(harness)
    assert {layer.role for layer in harness._light_curve_widget.plot_overview.call_args.args[0]} == {'target', 'comparison', 'check'}


def test_widget_renders_references_and_can_return_to_target_only():
    app = QApplication.instance() or QApplication([])
    layers, _ = build_overview_light_curve_layers([r for r in measurements() if r.filter_name == 'R'], 'target')
    target = layers[0].series
    widget = LightCurvePlotWidget()
    widget.plot_series(target, 'Empty', y_axis_mode='calibrated_magnitude', reference_layers=layers[1:],
                       x_axis_mode='phase', phase_period_hours=2.5, recent_period_error_bars_only=True)
    assert len(widget._payload.layers) == 3
    assert widget._series is target
    widget.plot_series(target, 'Empty', y_axis_mode='calibrated_magnitude')
    assert not widget._payload.layers
    assert len(widget._payload.points) == 4
    widget.close()
