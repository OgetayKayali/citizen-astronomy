from __future__ import annotations

from photometry_app.core.plotting import FitPeriodInferenceResult, LightCurveFitConfig, infer_fit_period_result


def merge_period_results(
    primary_result: FitPeriodInferenceResult | None,
    duration_result: FitPeriodInferenceResult | None,
) -> FitPeriodInferenceResult | None:
    if primary_result is None and duration_result is None:
        return None
    assert primary_result is not None or duration_result is not None
    return FitPeriodInferenceResult(
        period_hours=(primary_result.period_hours if primary_result is not None else duration_result.period_hours),
        periodic_harmonics=(primary_result.periodic_harmonics if primary_result is not None else duration_result.periodic_harmonics),
        method=(primary_result.method if primary_result is not None else duration_result.method),
        eclipse_duration_hours=(
            duration_result.eclipse_duration_hours
            if duration_result is not None and duration_result.eclipse_duration_hours is not None
            else (primary_result.eclipse_duration_hours if primary_result is not None else None)
        ),
    )


def calculate_period_for_series(
    series: object,
    fit_config: LightCurveFitConfig | None,
    y_axis_mode: str,
    period_method: str,
    period_convention: str,
    *,
    include_bls_duration: bool = True,
) -> FitPeriodInferenceResult | None:
    primary_result = infer_fit_period_result(
        series,
        fit_config=fit_config,
        y_axis_mode=y_axis_mode,
        method=period_method,
        period_convention=period_convention,
    )
    if not include_bls_duration or period_method == "bls":
        return primary_result
    duration_result = infer_fit_period_result(
        series,
        fit_config=fit_config,
        y_axis_mode=y_axis_mode,
        method="bls",
        period_convention=period_convention,
    )
    return merge_period_results(primary_result, duration_result)


def calculate_period_task(
    series_key: tuple[str, str],
    series: object,
    fit_config: LightCurveFitConfig | None,
    y_axis_mode: str,
    period_method: str,
    period_convention: str,
    include_bls_duration: bool = True,
) -> tuple[tuple[str, str], str, str, FitPeriodInferenceResult | None]:
    result = calculate_period_for_series(
        series,
        fit_config=fit_config,
        y_axis_mode=y_axis_mode,
        period_method=period_method,
        period_convention=period_convention,
        include_bls_duration=include_bls_duration,
    )
    return series_key, str(series.source_name), str(series.filter_name), result
