from datetime import datetime
from pathlib import Path

from photometry_app.core.matching import OverviewLightCurveLayer
from photometry_app.core.models import LightCurvePoint, LightCurveSeries
from photometry_app.core.plotting import LightCurvePlotPayload, LightCurveRenderPoint, LightCurveSeriesLayer
from photometry_app.ui.light_curve_widget import light_curve_hide_key, light_curve_points_in_rect, marked_file_path
from photometry_app.ui.main_window import _strip_hidden_light_curve_layers


def _point(x, y, name):
    source = LightCurvePoint(
        observation_time=datetime(2026, 10, 1, 1, 0),
        file_path=Path(name),
        differential_magnitude=y,
        instrumental_magnitude=y,
        flux=1000,
        flux_error=10,
    )
    return LightCurveRenderPoint(x=x, y=y, y_error=None, fit_weight=None, source_point=source)


def test_rectangle_selects_points_inside_including_reference_layers():
    target = LightCurveSeries("Field", "target", "Target", "L", points=[])
    inside = _point(1, 10, "a.fits")
    outside = _point(5, 10, "b.fits")
    reference = _point(1.2, 10.2, "c.fits")
    payload = LightCurvePlotPayload(
        title="Target",
        y_axis_label="mag",
        x_axis_label="jd",
        x_axis_mode="jd",
        invert_y=True,
        points=(inside, outside),
        layers=(
            LightCurveSeriesLayer("Target [L]", "target", "#e74c3c", points=(inside, outside), source_id="target", filter_name="L"),
            LightCurveSeriesLayer("Comp [L]", "comparison", "#f1c40f", points=(reference,), source_id="comp", filter_name="L"),
        ),
    )
    selected = light_curve_points_in_rect(payload, target, 0.5, 9.5, 2.0, 11.0)
    assert {item["source_id"] for item in selected} == {"target", "comp"}
    assert all(item["key"] == light_curve_hide_key(item["source_id"], "L", item["file_path"], item["observation_time"]) for item in selected)


def test_hidden_points_leave_the_plotted_layer_only():
    source = LightCurvePoint(
        observation_time=datetime(2026, 10, 1, 1, 0),
        file_path=Path("hide.fits"),
        differential_magnitude=10,
        instrumental_magnitude=10,
        flux=1000,
        flux_error=10,
    )
    kept = LightCurvePoint(
        observation_time=datetime(2026, 10, 1, 2, 0),
        file_path=Path("keep.fits"),
        differential_magnitude=11,
        instrumental_magnitude=11,
        flux=1000,
        flux_error=10,
    )
    series = LightCurveSeries("Field", "target", "Target", "L", points=[source, kept])
    layer = OverviewLightCurveLayer(role="target", legend_label="Target [L]", series=series)
    hidden = {light_curve_hide_key("target", "L", source.file_path, source.observation_time)}
    filtered = _strip_hidden_light_curve_layers([layer], hidden)
    assert [point.file_path.name for point in filtered[0].series.points] == ["keep.fits"]
    assert [point.file_path.name for point in series.points] == ["hide.fits", "keep.fits"]


def test_mark_files_prefixes_the_name_and_does_not_delete(tmp_path):
    original = tmp_path / "frame.fits"
    original.write_bytes(b"fits")
    renamed = marked_file_path(original)
    assert renamed == tmp_path / "(marked) frame.fits"
    assert renamed.is_file()
    assert not original.exists()
    assert marked_file_path(renamed) is None
    assert renamed.is_file()
