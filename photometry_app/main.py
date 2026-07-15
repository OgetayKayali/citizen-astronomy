from __future__ import annotations



import argparse
import json
import multiprocessing
import os

import sys

import traceback

from pathlib import Path



from photometry_app.app_metadata import APP_DISPLAY_NAME, APP_USER_MODEL_ID, APP_VERSION, APP_WINDOW_TITLE_NAME, application_icon_path





def _startup_log_path() -> Path:

    local_app_data = os.getenv("LOCALAPPDATA")

    if local_app_data:

        return Path(local_app_data) / "CitizenAstronomy" / "startup-error.log"

    return Path.home() / ".citizen-astronomy" / "startup-error.log"





def _write_startup_crash_log(exc: BaseException) -> Path:

    log_path = _startup_log_path()

    log_path.parent.mkdir(parents=True, exist_ok=True)

    log_path.write_text(traceback.format_exc(), encoding="utf-8")

    return log_path


def _set_windows_app_user_model_id() -> None:

    if os.name != "nt":

        return

    try:

        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)

    except Exception:

        pass


def _configure_qt_application_attributes() -> None:

    from PySide6.QtCore import QCoreApplication, Qt

    if QCoreApplication.instance() is not None:

        return

    # Sky View uses QOpenGLWidget and the observer-location map uses
    # QWebEngineView; Qt requires shared GL contexts before QApplication exists.
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)


def _configure_qt_application_style(app) -> None:

    from PySide6.QtWidgets import QStyleFactory

    # Windows 11's native Qt style ignores per-widget disabled-button QSS and
    # renders default light-gray scrollbars. Fusion keeps styling consistent.
    if "Fusion" in QStyleFactory.keys():

        app.setStyle("Fusion")


def _parse_cli_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:

    parser = argparse.ArgumentParser(add_help=False)

    parser.add_argument("--qt-image-format-smoke", action="store_true")

    parser.add_argument(

        "--qt-image-format-smoke-output",

        default="_tmp_qt_image_format_smoke_result_shipping.json",

    )

    parser.add_argument("--packaged-format-smoke", action="store_true")

    parser.add_argument(

        "--packaged-format-smoke-output",

        default="_tmp_packaged_format_smoke_result.json",

    )

    parser.add_argument(

        "--packaged-format-smoke-fixtures",

        default="packaging/fixtures",

    )

    parser.add_argument("--about-dialog-smoke", action="store_true")

    args, remaining = parser.parse_known_args(argv[1:])

    return args, [argv[0], *remaining]





def main() -> int:

    # PyInstaller multiprocessing workers re-enter this executable. Divert them
    # before argument parsing or Qt startup so they do not open app windows.
    multiprocessing.freeze_support()

    try:

        cli_args, qt_argv = _parse_cli_args(sys.argv)

        if cli_args.qt_image_format_smoke:

            from photometry_app.core.qt_image_format_smoke import run_qt_image_format_smoke



            result = run_qt_image_format_smoke(output_path=cli_args.qt_image_format_smoke_output)

            print(json.dumps(result, indent=2))

            return 0 if bool(result.get("success")) else 1

        if cli_args.packaged_format_smoke:

            from photometry_app.core.packaged_format_smoke import run_packaged_format_smoke



            result = run_packaged_format_smoke(

                fixtures_dir=cli_args.packaged_format_smoke_fixtures,

                output_path=cli_args.packaged_format_smoke_output,

            )

            print(json.dumps(result, indent=2))

            return 0 if bool(result.get("success")) else 1

        if cli_args.about_dialog_smoke:

            _configure_qt_application_attributes()

            from photometry_app.core.packaged_format_smoke import run_about_dialog_smoke



            result = run_about_dialog_smoke()

            print(json.dumps(result, indent=2))

            return 0 if bool(result.get("success")) else 1

        _configure_qt_application_attributes()

        from PySide6.QtGui import QIcon

        from PySide6.QtWidgets import QApplication, QMessageBox

        from photometry_app.core.discovery import cleanup_stale_discovery_temp_cache



        from photometry_app.ui.main_window import MainWindow



        cleanup_stale_discovery_temp_cache()

        _set_windows_app_user_model_id()



        app = QApplication.instance() or QApplication(qt_argv)

        _configure_qt_application_style(app)

        app.setApplicationName(APP_WINDOW_TITLE_NAME)

        app.setApplicationDisplayName(APP_DISPLAY_NAME)

        app.setApplicationVersion(APP_VERSION)

        icon_path = application_icon_path()

        if icon_path is not None:

            app.setWindowIcon(QIcon(str(icon_path)))

        window = MainWindow()

        window.showMaximized()

        return app.exec()

    except Exception as exc:

        log_path = _write_startup_crash_log(exc)

        print(traceback.format_exc(), file=sys.stderr)

        try:

            _configure_qt_application_attributes()

            from PySide6.QtWidgets import QApplication, QMessageBox



            app = QApplication.instance() or QApplication(sys.argv)

            QMessageBox.critical(

                None,

                "Startup failed",

                f"{APP_DISPLAY_NAME} could not start.\n\n"

                f"{exc}\n\n"

                "A crash log was written to:\n"

                f"{log_path}",

            )

            app.processEvents()

        except Exception:

            pass

        return 1





if __name__ == "__main__":

    raise SystemExit(main())

