from __future__ import annotations

from pathlib import Path

from photometry_app.core.pipeline import PhotometryPipeline, science_export_metadata_from_settings
from photometry_app.core.settings import AppSettings


def main() -> int:
    workspace_root = Path(__file__).resolve().parents[1]
    object_name = "DemoOrion"
    pipeline = PhotometryPipeline()
    settings = AppSettings.from_root(workspace_root)
    report = pipeline.process_object(workspace_root, object_name)

    export_dir = workspace_root / "Exports" / f"{object_name}_smoke"
    exported = pipeline.export_results(
        report,
        export_dir,
        plot_theme=settings.theme,
        custom_theme_colors=settings.custom_theme_colors,
        science_metadata=science_export_metadata_from_settings(settings),
    )

    print(f"object={report.object_name}")
    print(f"files_processed={report.files_processed}")
    print(f"solved_files={report.solved_files}")
    print(f"measurements={len(report.measurements)}")
    print(f"light_curves={len(report.light_curves)}")
    print(f"first_curve={report.light_curves[0].source_name if report.light_curves else 'NONE'}")
    print(f"export_measurements={exported['measurements_csv']}")
    print(f"export_summary={exported['summary_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())